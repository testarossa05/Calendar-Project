"""Google API credential handling.

Two paths are supported:

* ``service_account_json`` -- a service account key. Best for unattended runs in
  GitHub Actions. Share the target calendar with the service account's email
  address and give it "Make changes to events".
* ``oauth_client_json`` + ``token_json`` -- a normal user OAuth flow. Needed when
  the calendar lives in a Workspace that forbids sharing with service accounts.

Either value may be an inline JSON string (so it can come straight from a GitHub
secret) or a path to a file on disk.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

SCOPES = ["https://www.googleapis.com/auth/calendar"]
READONLY_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


class AuthError(Exception):
    pass


def _load_json(value: str, label: str) -> dict[str, Any]:
    """Accept either inline JSON or a filesystem path."""
    text = (value or "").strip()
    if not text:
        raise AuthError(f"{label} is empty")
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AuthError(f"{label}: not valid JSON ({exc})") from exc
    path = Path(text).expanduser()
    if not path.exists():
        raise AuthError(f"{label}: file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AuthError(f"{label}: file is not valid JSON ({exc})") from exc


def build_credentials(options: dict[str, Any], readonly: bool = False):
    """Return google credentials from the given source/sink options."""
    scopes = READONLY_SCOPES if readonly else SCOPES

    sa_value = options.get("service_account_json") or os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_JSON"
    )
    if sa_value:
        from google.oauth2 import service_account

        info = _load_json(sa_value, "service_account_json")
        creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
        subject = options.get("impersonate_subject")
        if subject:
            # Domain-wide delegation: act as a real user in the Workspace.
            creds = creds.with_subject(subject)
        return creds

    token_value = options.get("token_json") or os.environ.get("GOOGLE_TOKEN_JSON")
    if token_value:
        from google.oauth2.credentials import Credentials

        info = _load_json(token_value, "token_json")
        creds = Credentials.from_authorized_user_info(info, scopes)
        if creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request

            creds.refresh(Request())
        return creds

    raise AuthError(
        "no Google credentials configured. Set 'service_account_json' (recommended for "
        "automated runs) or 'token_json' in the source/sink options, or export "
        "GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_TOKEN_JSON."
    )


def build_service(options: dict[str, Any], readonly: bool = False):
    from googleapiclient.discovery import build

    creds = build_credentials(options, readonly=readonly)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def run_local_oauth(client_secret_path: str, token_out: str) -> str:
    """One-off helper: run the browser consent flow and save a reusable token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
    creds = flow.run_local_server(port=0)
    out = Path(token_out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json(), encoding="utf-8")
    out.chmod(0o600)
    return str(out)


def optional_str(options: dict[str, Any], name: str) -> Optional[str]:
    value = options.get(name)
    return str(value) if value else None
