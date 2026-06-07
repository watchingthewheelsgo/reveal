"""Longbridge OAuth HTTP client for market data."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from config.settings import get_settings

_REFRESH_GRACE_SECONDS = 300


class LongbridgeAuthError(RuntimeError):
    """Raised when Longbridge OAuth credentials are missing or invalid."""


async def fetch_quote_anomalies(
    market: str = "US",
    count: int = 50,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Fetch quote anomalies / unusual market movements from Longbridge."""
    params: dict[str, Any] = {"market": market.upper(), "count": count}
    if symbol:
        params["symbol"] = normalize_longbridge_symbol(symbol, market)
    return await longbridge_get("/v1/quote/changes", params=params)


async def longbridge_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = get_settings()
    token = await get_access_token()
    url = f"{settings.longbridge_api_base.rstrip('/')}/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
    if response.status_code == 401:
        token = await refresh_access_token(force=True)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("code") not in {None, 0}:
        code = payload.get("code")
        message = payload.get("message")
        raise RuntimeError(f"Longbridge API error: code={code} {message}")
    data = payload.get("data") if isinstance(payload, dict) else payload
    return data if isinstance(data, dict) else {"items": data}


async def get_access_token() -> str:
    token_data = _read_token_file()
    if _should_refresh(token_data):
        token_data = await refresh_access_token()
    access_token = str(token_data.get("access_token") or "")
    if not access_token:
        raise LongbridgeAuthError("Longbridge access_token is missing")
    return access_token


async def refresh_access_token(force: bool = False) -> dict[str, Any]:
    settings = get_settings()
    token_data = _read_token_file()
    if not force and not _should_refresh(token_data):
        return token_data

    client_id = str(token_data.get("client_id") or "")
    refresh_token = str(token_data.get("refresh_token") or "")
    if not client_id or not refresh_token:
        raise LongbridgeAuthError("Longbridge client_id or refresh_token is missing")

    token_url = f"{settings.longbridge_api_base.rstrip('/')}/oauth2/token"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    response.raise_for_status()
    refreshed = response.json()
    if not isinstance(refreshed, dict) or not refreshed.get("access_token"):
        raise LongbridgeAuthError("Longbridge refresh response did not include access_token")

    merged = {**token_data, **refreshed, "client_id": client_id, "saved_at": int(time.time())}
    _write_token_file(merged)
    logger.info("Longbridge OAuth token refreshed")
    return merged


def normalize_longbridge_symbol(symbol: str, market: str = "US") -> str:
    normalized = symbol.strip().upper().lstrip("$")
    if "/" in normalized:
        parts = normalized.split("/")
        normalized = parts[-1]
    if "." in normalized:
        return normalized
    return f"{normalized}.{market.upper()}"


def longbridge_ticker_from_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if "/" in normalized:
        normalized = normalized.split("/")[-1]
    return normalized.split(".", 1)[0]


def _read_token_file() -> dict[str, Any]:
    settings = get_settings()
    path = settings.longbridge_oauth_token_path
    if not settings.longbridge_enabled:
        raise LongbridgeAuthError("LONGBRIDGE_ENABLED is false")
    if not path:
        raise LongbridgeAuthError("LONGBRIDGE_OAUTH_TOKEN_PATH is not configured")
    token_path = Path(path)
    if not token_path.exists():
        raise LongbridgeAuthError(f"Longbridge token file not found: {token_path}")
    try:
        with token_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception as exc:
        raise LongbridgeAuthError(f"Failed to read Longbridge token file: {token_path}") from exc
    if not isinstance(data, dict):
        raise LongbridgeAuthError("Longbridge token file must contain a JSON object")
    return data


def _write_token_file(data: dict[str, Any]) -> None:
    path = Path(get_settings().longbridge_oauth_token_path)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(path)


def _should_refresh(token_data: dict[str, Any]) -> bool:
    expires_in = _to_int(token_data.get("expires_in"))
    saved_at = _to_int(token_data.get("saved_at"))
    if not expires_in or not saved_at:
        return False
    return int(time.time()) >= saved_at + expires_in - _REFRESH_GRACE_SECONDS


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
