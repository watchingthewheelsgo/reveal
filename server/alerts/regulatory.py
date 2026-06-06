"""
Regulatory event alerts for watched tickers.

SEC filings are pulled from SEC EDGAR JSON APIs. FDA recalls are pulled from
openFDA enforcement endpoints and matched by configured company/product keywords.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from loguru import logger
from sqlalchemy import select

from config.settings import get_settings
from server.db.engine import get_session_factory
from server.db.models import RegulatoryEvent

SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_BASE_URL = "https://www.sec.gov/Archives/edgar/data"

_SEC_COMPANY_CACHE: tuple[datetime, dict[str, dict[str, str]]] | None = None
_SEC_COMPANY_CACHE_TTL = timedelta(hours=24)

_FDA_ENFORCEMENT_CATEGORIES = {"drug", "device", "food"}
_SEVERITY_ORDER = {"critical": 3, "warning": 2, "info": 1}


async def run_regulatory_alert_cycle(adapter=None) -> None:
    """Run one regulatory alert cycle and push newly-seen events."""
    settings = get_settings()
    if not settings.regulatory_alert_enabled:
        logger.debug("Regulatory alerts disabled")
        return

    from server.alerts.engine import get_active_tickers_for_alert

    tickers = await get_active_tickers_for_alert()
    if not tickers and not settings.fda_alert_keywords:
        logger.debug("No watched tickers or FDA keywords for regulatory alert cycle")
        return

    events = await check_regulatory_events(tickers)
    if not events:
        logger.debug("No regulatory events triggered")
        return

    new_events = await record_new_regulatory_events(events)
    if not new_events:
        logger.debug("No new regulatory events after dedupe")
        return

    new_events.sort(
        key=lambda e: (
            _SEVERITY_ORDER.get(e.get("severity", "info"), 0),
            e.get("event_date") or datetime.min,
        ),
        reverse=True,
    )

    if adapter:
        for event in new_events:
            await adapter.push_to_admin(format_regulatory_alert(event))

    logger.info("Regulatory alert cycle complete: {} new events", len(new_events))


async def check_regulatory_events(tickers: list[str]) -> list[dict[str, Any]]:
    """Fetch SEC/FDA events for watched tickers without writing push state."""
    settings = get_settings()
    normalized_tickers = sorted({ticker.upper() for ticker in tickers if ticker.strip()})
    events: list[dict[str, Any]] = []

    if normalized_tickers:
        events.extend(await check_sec_filing_events(normalized_tickers))

    if settings.fda_alert_enabled:
        events.extend(await check_fda_enforcement_events(normalized_tickers))

    return events


async def check_sec_filing_events(tickers: list[str]) -> list[dict[str, Any]]:
    """Check recent EDGAR submissions for watched tickers."""
    settings = get_settings()
    user_agent = settings.sec_user_agent.strip()
    if not user_agent:
        logger.debug("SEC_USER_AGENT not configured, skipping SEC filing alerts")
        return []

    company_map = await fetch_sec_company_tickers()
    if not company_map:
        return []

    cutoff = _utcnow() - timedelta(hours=settings.regulatory_alert_lookback_hours)
    watched_forms = {_normalize_form(form) for form in settings.sec_alert_forms}
    events: list[dict[str, Any]] = []

    headers = _sec_headers(user_agent)
    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        for ticker in tickers:
            company = company_map.get(ticker.upper())
            if not company:
                continue

            cik = company["cik"]
            try:
                response = await client.get(SEC_SUBMISSIONS_URL.format(cik=cik))
                if response.status_code != 200:
                    logger.warning(
                        "SEC submissions fetch failed for {}: HTTP {}",
                        ticker,
                        response.status_code,
                    )
                    continue
                payload = response.json()
            except Exception:
                logger.exception("SEC submissions fetch failed for {}", ticker)
                continue

            recent = payload.get("filings", {}).get("recent", {})
            forms = recent.get("form") or []
            accessions = recent.get("accessionNumber") or []
            filing_dates = recent.get("filingDate") or []
            report_dates = recent.get("reportDate") or []
            accepted_at = recent.get("acceptanceDateTime") or []
            primary_docs = recent.get("primaryDocument") or []
            descriptions = recent.get("primaryDocDescription") or []

            for idx, form in enumerate(forms):
                normalized_form = _normalize_form(str(form))
                if watched_forms and not _is_watched_sec_form(normalized_form, watched_forms):
                    continue

                accession = _list_get(accessions, idx)
                if not accession:
                    continue

                accepted_dt = _parse_datetime(_list_get(accepted_at, idx))
                filing_dt = _parse_datetime(_list_get(filing_dates, idx))
                event_dt = accepted_dt or filing_dt
                if event_dt is None:
                    continue
                if accepted_dt is not None:
                    if event_dt < cutoff:
                        continue
                elif event_dt.date() < cutoff.date():
                    continue

                primary_doc = _list_get(primary_docs, idx)
                url = _sec_filing_url(cik, accession, primary_doc)
                description = _list_get(descriptions, idx) or normalized_form
                report_date = _list_get(report_dates, idx)
                title = f"{ticker.upper()} {normalized_form}: {description}"
                detail = f"Filed {event_dt.date().isoformat()}" + (
                    f" | Report date {report_date}" if report_date else ""
                )

                events.append(
                    {
                        "source": "sec",
                        "event_id": f"sec:{cik}:{accession}",
                        "ticker": ticker.upper(),
                        "type": "SEC Filing",
                        "severity": _sec_severity(normalized_form),
                        "title": title,
                        "message": title,
                        "detail": detail,
                        "url": url,
                        "event_date": event_dt,
                        "raw": {
                            "cik": cik,
                            "company": company.get("title"),
                            "form": normalized_form,
                            "accession": accession,
                            "filing_date": _list_get(filing_dates, idx),
                            "report_date": report_date,
                            "primary_document": primary_doc,
                        },
                    }
                )

            await asyncio.sleep(0.12)

    return events


async def check_fda_enforcement_events(tickers: list[str]) -> list[dict[str, Any]]:
    """Check recent openFDA enforcement reports matched by company/product keywords."""
    settings = get_settings()
    categories = [
        c.lower() for c in settings.fda_alert_categories if c.lower() in _FDA_ENFORCEMENT_CATEGORIES
    ]
    if not categories:
        return []

    keyword_pairs = await _fda_keyword_pairs(tickers)
    if not keyword_pairs:
        logger.debug("No FDA keywords available, skipping FDA enforcement alerts")
        return []

    cutoff = _utcnow() - timedelta(hours=settings.regulatory_alert_lookback_hours)
    start = cutoff.strftime("%Y%m%d")
    end = _utcnow().strftime("%Y%m%d")
    accepted_classifications = {c.lower() for c in settings.fda_alert_classifications}
    events: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=20) as client:
        for category in categories:
            url = f"{settings.fda_base_url.rstrip('/')}/{category}/enforcement.json"
            try:
                response = await client.get(
                    url,
                    params={
                        "search": f"report_date:[{start} TO {end}]",
                        "sort": "report_date:desc",
                        "limit": 100,
                    },
                )
                if response.status_code == 404:
                    continue
                if response.status_code != 200:
                    logger.warning(
                        "FDA enforcement fetch failed for {}: HTTP {}",
                        category,
                        response.status_code,
                    )
                    continue
                results = response.json().get("results", [])
            except Exception:
                logger.exception("FDA enforcement fetch failed for {}", category)
                continue

            for item in results:
                classification = str(item.get("classification") or "")
                if (
                    accepted_classifications
                    and classification.lower() not in accepted_classifications
                ):
                    continue

                haystack = _fda_search_text(item)
                matched_keyword, matched_ticker = _match_keyword(keyword_pairs, haystack)
                if not matched_keyword:
                    continue

                recall_number = str(item.get("recall_number") or "")
                if not recall_number:
                    continue

                report_date = _parse_yyyymmdd(str(item.get("report_date") or ""))
                reason = _clean_text(str(item.get("reason_for_recall") or ""))
                product = _clean_text(str(item.get("product_description") or ""))
                firm = _clean_text(str(item.get("recalling_firm") or ""))
                title = f"FDA {category.title()} Recall: {firm or matched_keyword}"

                events.append(
                    {
                        "source": "fda",
                        "event_id": f"fda:{category}:{recall_number}",
                        "ticker": matched_ticker,
                        "type": "FDA Enforcement",
                        "severity": _fda_severity(classification),
                        "title": title,
                        "message": title,
                        "detail": (
                            f"{classification or 'Unclassified'} | {_trim(product or reason, 180)}"
                        ),
                        "url": f"https://open.fda.gov/apis/{category}/enforcement/",
                        "event_date": report_date,
                        "raw": {
                            "category": category,
                            "recall_number": recall_number,
                            "classification": classification,
                            "status": item.get("status"),
                            "report_date": item.get("report_date"),
                            "recalling_firm": firm,
                            "matched_keyword": matched_keyword,
                            "reason_for_recall": reason,
                            "product_description": product,
                        },
                    }
                )

    return events


async def record_new_regulatory_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist new regulatory events and return the events not seen before."""
    if not events:
        return []

    now = _utcnow()
    new_events: list[dict[str, Any]] = []
    session_factory = get_session_factory()
    async with session_factory() as session:
        for event in events:
            event_id = str(event.get("event_id") or "")
            if not event_id:
                continue
            result = await session.execute(
                select(RegulatoryEvent.id).where(RegulatoryEvent.event_id == event_id)
            )
            if result.scalar_one_or_none() is not None:
                continue

            session.add(
                RegulatoryEvent(
                    source=str(event.get("source") or ""),
                    event_id=event_id,
                    ticker=event.get("ticker"),
                    event_type=str(event.get("type") or "Regulatory Event"),
                    severity=str(event.get("severity") or "info"),
                    title=str(event.get("title") or event.get("message") or event_id),
                    detail=event.get("detail"),
                    url=event.get("url"),
                    event_date=event.get("event_date"),
                    raw_json=event.get("raw"),
                    first_seen_at=now,
                    pushed_at=now,
                )
            )
            new_events.append(event)
        await session.commit()

    return new_events


async def fetch_sec_company_tickers() -> dict[str, dict[str, str]]:
    """Return SEC ticker metadata keyed by ticker symbol."""
    global _SEC_COMPANY_CACHE

    now = _utcnow()
    if _SEC_COMPANY_CACHE and now - _SEC_COMPANY_CACHE[0] < _SEC_COMPANY_CACHE_TTL:
        return _SEC_COMPANY_CACHE[1]

    user_agent = get_settings().sec_user_agent.strip()
    if not user_agent:
        return {}

    try:
        async with httpx.AsyncClient(timeout=15, headers=_sec_headers(user_agent)) as client:
            response = await client.get(SEC_COMPANY_TICKERS_URL)
        if response.status_code != 200:
            logger.warning("SEC ticker mapping fetch failed: HTTP {}", response.status_code)
            return {}
        payload = response.json()
    except Exception:
        logger.exception("SEC ticker mapping fetch failed")
        return {}

    mapping: dict[str, dict[str, str]] = {}
    for item in payload.values():
        try:
            ticker = str(item.get("ticker") or "").upper()
            cik = str(int(item.get("cik_str"))).zfill(10)
            title = str(item.get("title") or ticker)
            if ticker:
                mapping[ticker] = {"cik": cik, "title": title}
        except Exception:
            continue

    _SEC_COMPANY_CACHE = (now, mapping)
    return mapping


async def get_regulatory_alert_status_payload() -> dict[str, Any]:
    """Return current regulatory alert configuration."""
    settings = get_settings()
    from server.alerts.engine import get_active_tickers_for_alert

    return {
        "enabled": settings.regulatory_alert_enabled,
        "interval_minutes": settings.regulatory_alert_interval_minutes,
        "lookback_hours": settings.regulatory_alert_lookback_hours,
        "sec": {
            "enabled": bool(settings.sec_user_agent),
            "forms": settings.sec_alert_forms,
        },
        "fda": {
            "enabled": settings.fda_alert_enabled,
            "categories": settings.fda_alert_categories,
            "classifications": settings.fda_alert_classifications,
            "keyword_count": len(settings.fda_alert_keywords),
        },
        "active_tickers": await get_active_tickers_for_alert(),
    }


def format_regulatory_alert(event: dict[str, Any]) -> str:
    """Format a regulatory event as a bot message."""
    severity_emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
    emoji = severity_emoji.get(event.get("severity", "info"), "🔵")
    ticker = event.get("ticker") or "WATCH"

    lines = [
        f"{emoji} *{event.get('type', '监管事件')} — {ticker}*",
        "",
        str(event.get("message") or event.get("title") or ""),
    ]
    if event.get("detail"):
        lines.append(f"_{event['detail']}_")
    if event.get("url"):
        lines.append(str(event["url"]))
    return "\n".join(lines)


def _sec_headers(user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
    }


def _normalize_form(form: str) -> str:
    return re.sub(r"\s+", " ", form.strip().upper())


def _is_watched_sec_form(form: str, watched_forms: set[str]) -> bool:
    return any(form == watched or form.startswith(f"{watched}/") for watched in watched_forms)


def _sec_severity(form: str) -> str:
    if form.startswith(("8-K", "S-1", "F-1", "424B")):
        return "critical"
    if form.startswith(("10-K", "10-Q", "SC 13D", "SC 13G", "DEF 14A")):
        return "warning"
    return "info"


def _fda_severity(classification: str) -> str:
    normalized = classification.lower()
    if normalized == "class i":
        return "critical"
    if normalized == "class ii":
        return "warning"
    return "info"


async def _fda_keyword_pairs(tickers: list[str]) -> list[tuple[str, str | None]]:
    settings = get_settings()
    pairs: list[tuple[str, str | None]] = [
        (keyword, None) for keyword in settings.fda_alert_keywords if keyword.strip()
    ]

    if tickers:
        company_map = await fetch_sec_company_tickers()
        for ticker in tickers:
            company = company_map.get(ticker.upper())
            if not company:
                continue
            for keyword in _company_keywords(company["title"]):
                pairs.append((keyword, ticker.upper()))

    seen: set[tuple[str, str | None]] = set()
    deduped: list[tuple[str, str | None]] = []
    for keyword, ticker in pairs:
        normalized = re.sub(r"\s+", " ", keyword.strip())
        if len(normalized) < 3:
            continue
        key = (normalized.lower(), ticker)
        if key not in seen:
            deduped.append((normalized, ticker))
            seen.add(key)
    return deduped


def _company_keywords(title: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", title.strip())
    keywords = [normalized]
    stripped = re.sub(
        r"\b(INC|INC\.|CORP|CORPORATION|CO|COMPANY|PLC|LTD|LIMITED|LLC|SA|N\.V\.|NV|ADR)\b\.?",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    stripped = re.sub(r"\s+", " ", stripped).strip(" ,.-")
    if stripped and stripped.lower() != normalized.lower():
        keywords.append(stripped)
    return keywords


def _fda_search_text(item: dict[str, Any]) -> str:
    fields = [
        "recalling_firm",
        "product_description",
        "reason_for_recall",
        "code_info",
        "distribution_pattern",
    ]
    return " ".join(str(item.get(field) or "") for field in fields).lower()


def _match_keyword(
    keyword_pairs: list[tuple[str, str | None]], haystack: str
) -> tuple[str | None, str | None]:
    for keyword, ticker in keyword_pairs:
        if keyword.lower() in haystack:
            return keyword, ticker
    return None, None


def _sec_filing_url(cik: str, accession: str, primary_document: str | None) -> str:
    accession_no_dashes = accession.replace("-", "")
    cik_no_leading = str(int(cik))
    if primary_document:
        return f"{SEC_ARCHIVES_BASE_URL}/{cik_no_leading}/{accession_no_dashes}/{primary_document}"
    return f"https://www.sec.gov/edgar/browse/?CIK={cik_no_leading}"


def _list_get(values: list[Any], idx: int) -> str | None:
    if idx >= len(values):
        return None
    value = values[idx]
    if value is None:
        return None
    return str(value)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            dt = datetime.fromisoformat(value[:10])
        except ValueError:
            return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _parse_yyyymmdd(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d")
    except ValueError:
        return None


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _trim(value: str, limit: int) -> str:
    cleaned = _clean_text(value)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."
