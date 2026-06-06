#!/usr/bin/env python3
"""Run a local Longbridge OAuth flow and store tokens outside the repo."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import stat
import sys
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

AUTH_BASE = "https://openapi.longbridge.cn/oauth2"
REDIRECT_URI = "http://127.0.0.1:60355/longbridge/oauth/callback"
TOKEN_PATH = Path.home() / ".longbridge" / "reveal-oauth.json"


class CallbackState:
    code: str | None = None
    error: str | None = None
    state: str | None = None


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _post_form(url: str, values: dict[str, str]) -> dict:
    body = urllib.parse.urlencode(values).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _store_token(payload: dict) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["saved_at"] = int(time.time())
    payload["api_base"] = "https://openapi.longbridge.cn"
    payload["redirect_uri"] = REDIRECT_URI
    TOKEN_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    TOKEN_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _make_handler(expected_state: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if parsed.path != "/longbridge/oauth/callback":
                self.send_response(404)
                self.end_headers()
                return
            CallbackState.code = (params.get("code") or [None])[0]
            CallbackState.error = (params.get("error") or [None])[0]
            CallbackState.state = (params.get("state") or [None])[0]
            ok = CallbackState.code and CallbackState.state == expected_state
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            if ok:
                self.wfile.write(b"Longbridge authorization received. You can return to Codex.")
            else:
                self.wfile.write(b"Longbridge authorization failed or state mismatch.")

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--scope", default="4 6 10 11")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_urlsafe(24)

    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": args.client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": args.scope,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        quote_via=urllib.parse.quote,
    )
    auth_url = f"{AUTH_BASE}/authorize?{query}"
    print("Open this URL and approve Longbridge authorization:")
    print(auth_url)
    print(f"Waiting up to {args.timeout} seconds for callback on {REDIRECT_URI}...")
    sys.stdout.flush()

    with HTTPServer(("127.0.0.1", 60355), _make_handler(state)) as server:
        server.timeout = 1
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline and not CallbackState.code and not CallbackState.error:
            server.handle_request()

    if CallbackState.error:
        print(f"Authorization error: {CallbackState.error}", file=sys.stderr)
        return 1
    if not CallbackState.code:
        print("Timed out waiting for authorization callback.", file=sys.stderr)
        return 1
    if CallbackState.state != state:
        print("State mismatch; refusing to exchange token.", file=sys.stderr)
        return 1

    token = _post_form(
        f"{AUTH_BASE}/token",
        {
            "grant_type": "authorization_code",
            "client_id": args.client_id,
            "redirect_uri": REDIRECT_URI,
            "code": CallbackState.code,
            "code_verifier": verifier,
        },
    )
    token["client_id"] = args.client_id
    _store_token(token)
    print(f"Token saved to {TOKEN_PATH}")
    print("Saved fields:", ", ".join(sorted(token.keys())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
