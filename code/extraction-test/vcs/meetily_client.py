"""Minimal stdlib client for the Meetily Pro Agent API (pattern from copilot/live_transcript.py).

Token: MEETILY_PRO_TOKEN env var (a created key; needed for `write` scope), else the
read-only loopback token Meetily writes to %APPDATA%\\pro.meetily.ai\\gateway-token.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("MEETILY_API", "http://127.0.0.1:8420")


class MeetilyError(Exception):
    def __init__(self, status: int | None, body, message: str = ""):
        self.status, self.body = status, body
        super().__init__(message or f"Meetily API {status}: {body}")

    @property
    def code(self) -> str | None:
        """Error code from the JSON body, e.g. 'recording_in_progress'."""
        if isinstance(self.body, dict):
            err = self.body.get("error", self.body)
            if isinstance(err, dict):
                return err.get("code")
            return err if isinstance(err, str) else self.body.get("code")
        return None


class MeetilyOffline(MeetilyError):
    """Meetily isn't running / the Automation API is off (connection refused or timeout)."""


def token_file() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "pro.meetily.ai" / "gateway-token"


def _loopback_token() -> str | None:
    f = token_file()
    try:
        tok = f.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return tok or None


def find_token(need_write: bool = False) -> str:
    """Least privilege: reads use the read-only loopback token (falling back to
    MEETILY_PRO_TOKEN); only writes use the MEETILY_PRO_TOKEN key."""
    env = os.environ.get("MEETILY_PRO_TOKEN", "").strip()
    if need_write:
        if env:
            return env
        raise MeetilyError(None, None, "Set MEETILY_PRO_TOKEN to a key with `write` scope "
                           "(Meetily > Settings > Integrations > Apps & scripts); the "
                           "read-only loopback token cannot PUT a summary.")
    tok = _loopback_token() or env
    if tok:
        return tok
    raise MeetilyError(None, None, f"No Meetily token: turn on Settings > Integrations > "
                       f"'Allow the CLI on this computer' (expected {token_file()}) or set "
                       f"MEETILY_PRO_TOKEN.")


def explain(e: "MeetilyError") -> str:
    """A user-facing sentence for common Meetily error codes."""
    return {
        "consumer_disabled": "This Meetily key is switched off. Turn its Allow switch on in "
                             "Meetily > Settings > Integrations > Apps & scripts.",
        "insufficient_scope": "This Meetily key lacks the needed scope (writing a summary needs "
                              "`write`).",
        "recording_in_progress": "Meetily is still recording this meeting; try after it stops.",
    }.get(e.code or "", str(e))


class MeetilyClient:
    def __init__(self, token: str | None = None, base: str = BASE, timeout: float = 10):
        self.base, self.timeout = base.rstrip("/"), timeout
        self._token = token

    @property
    def token(self) -> str:
        if self._token is None:
            self._token = find_token()
        return self._token

    def _request(self, method: str, path: str, body=None, token: str | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Authorization": f"Bearer {token or self.token}",
                     **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = raw
            raise MeetilyError(e.code, parsed) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise MeetilyOffline(None, str(e), f"Can't reach Meetily at {self.base} "
                                 f"({e}). Is Meetily Pro running with the Automation API on?") from None

    # ---- reads
    def recording(self):
        return self._request("GET", "/v1/recording")

    def list_meetings(self, limit: int = 50):
        return self._request("GET", f"/v1/meetings?limit={int(limit)}")

    def whoami(self):
        return self._request("GET", "/v1/whoami")

    def get_meeting(self, meeting_id: str) -> dict:
        return self._request("GET", f"/v1/meetings/{meeting_id}")

    def get_transcript(self, meeting_id: str) -> dict:
        return self._request("GET", f"/v1/meetings/{meeting_id}/transcript")

    def get_summary(self, meeting_id: str) -> dict:
        return self._request("GET", f"/v1/meetings/{meeting_id}/summary")

    # ---- webhooks (read scope: the loopback token is enough)
    def create_webhook(self, url: str, events: list[str], delivery_mode: str = "at-least-once"):
        return self._request("POST", "/v1/webhooks",
                             {"url": url, "events": events, "delivery_mode": delivery_mode})

    def get_webhook(self, webhook_id: str):
        return self._request("GET", f"/v1/webhooks/{webhook_id}")

    def delete_webhook(self, webhook_id: str):
        return self._request("DELETE", f"/v1/webhooks/{webhook_id}")

    def webhook_deliveries(self, webhook_id: str):
        return self._request("GET", f"/v1/webhooks/{webhook_id}/deliveries")

    def test_webhook(self, webhook_id: str):
        return self._request("POST", f"/v1/webhooks/{webhook_id}/test", {})

    # ---- write (needs a `write`-scope key in MEETILY_PRO_TOKEN)
    def write_status(self) -> dict:
        """{'ok': bool, 'reason': str, 'scopes': [...]} for the MEETILY_PRO_TOKEN key."""
        try:
            tok = find_token(need_write=True)
        except MeetilyError as e:
            return {"ok": False, "reason": "missing", "message": str(e), "scopes": []}
        try:
            w = self._request("GET", "/v1/whoami", token=tok)
        except MeetilyOffline:
            raise
        except MeetilyError as e:
            return {"ok": False, "reason": e.code or f"http_{e.status}", "message": explain(e),
                    "scopes": []}
        scopes = w.get("scopes") or []
        if "write" not in scopes:
            return {"ok": False, "reason": "insufficient_scope", "scopes": scopes,
                    "message": "This Meetily key has no `write` scope."}
        return {"ok": True, "reason": "ok", "scopes": scopes, "message": ""}

    def require_write(self):
        """Raise MeetilyError unless the write key exists, is switched on and has `write`
        scope. Called before any backup/PUT so nothing happens half-way."""
        st = self.write_status()
        if not st["ok"]:
            raise MeetilyError(None, st["reason"], st["message"])

    def rename_meeting(self, meeting_id: str, title: str):
        return self._request("PATCH", f"/v1/meetings/{meeting_id}", {"title": title},
                             token=find_token(need_write=True))

    def put_summary(self, meeting_id: str, text: str):
        return self._request("PUT", f"/v1/meetings/{meeting_id}/summary", {"text": text},
                             token=find_token(need_write=True))
