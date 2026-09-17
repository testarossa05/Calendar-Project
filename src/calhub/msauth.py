"""Microsoft Graph authentication.

Uses the device code flow with a *delegated* permission, ``Calendars.Read``,
which reads only the signed-in user's own calendar. That distinction matters:
application permissions read the whole tenant and always require an administrator,
while a delegated permission can often be granted by the user themselves.

Publishing a calendar and registering an application are governed by different
policies -- Exchange's SharingPolicy versus Entra ID's "users can register
applications" -- so this path is worth trying even when publishing is blocked.

The first sign-in is interactive (``calhub ms-auth``). After that the refresh
token in the cache renews access silently, including from CI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

GRAPH_SCOPES = ["Calendars.Read"]
AUTHORITY_BASE = "https://login.microsoftonline.com"


class MsAuthError(Exception):
    pass


def _load_cache(cache_value: Optional[str]):
    """Build a token cache from a path or from inline JSON (e.g. a CI secret)."""
    import msal

    cache = msal.SerializableTokenCache()
    if not cache_value:
        return cache, None

    text = cache_value.strip()
    if text.startswith("{"):
        cache.deserialize(text)
        return cache, None

    path = Path(text).expanduser()
    if path.exists():
        cache.deserialize(path.read_text(encoding="utf-8"))
    return cache, path


def _save_cache(cache, path: Optional[Path]) -> None:
    if path is None or not cache.has_state_changed:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cache.serialize(), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _app(client_id: str, tenant_id: str, cache):
    import msal

    return msal.PublicClientApplication(
        client_id, authority=f"{AUTHORITY_BASE}/{tenant_id}", token_cache=cache
    )


def acquire_token(options: dict[str, Any]) -> str:
    """Return a Graph access token, refreshing silently from the cache.

    Never prompts. A run from CI must fail loudly rather than block on a prompt
    nobody is watching.
    """
    client_id = options.get("client_id") or os.environ.get("MS_CLIENT_ID")
    tenant_id = options.get("tenant_id") or os.environ.get("MS_TENANT_ID") or "organizations"
    cache_value = options.get("token_cache") or os.environ.get("MS_TOKEN_CACHE")

    if not client_id:
        raise MsAuthError(
            "missing 'client_id'. Register an application in Entra ID "
            "(Azure portal -> App registrations -> New registration), choose "
            "'Accounts in this organizational directory only', enable "
            "'Allow public client flows', and add the delegated Microsoft Graph "
            "permission Calendars.Read."
        )
    if not cache_value:
        raise MsAuthError(
            "no token cache. Run 'python -m calhub ms-auth' once to sign in, then "
            "point 'token_cache' at the file it writes (or paste its contents into "
            "the MS_TOKEN_CACHE secret)."
        )

    cache, cache_path = _load_cache(cache_value)
    app = _app(client_id, tenant_id, cache)

    accounts = app.get_accounts()
    if not accounts:
        raise MsAuthError(
            "the token cache holds no account. Run 'python -m calhub ms-auth' again."
        )

    result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])
    _save_cache(cache, cache_path)

    if not result or "access_token" not in result:
        detail = (result or {}).get("error_description") or "no details returned"
        raise MsAuthError(
            "silent token refresh failed, so the cached sign-in has expired or been "
            f"revoked. Run 'python -m calhub ms-auth' again. Details: {detail}"
        )
    return result["access_token"]


def device_code_login(
    client_id: str,
    tenant_id: str,
    cache_path: str,
    prompt: Optional[Callable[[str], None]] = None,
) -> str:
    """Run the interactive device code flow once and persist the token cache."""
    import msal

    cache = msal.SerializableTokenCache()
    path = Path(cache_path).expanduser()
    if path.exists():
        cache.deserialize(path.read_text(encoding="utf-8"))

    app = _app(client_id, tenant_id, cache)
    flow = app.initiate_device_flow(scopes=GRAPH_SCOPES)
    if "user_code" not in flow:
        raise MsAuthError(
            "could not start the device code flow. The most common cause is that the "
            "app registration does not have 'Allow public client flows' enabled "
            "(Authentication -> Advanced settings). Raw response: "
            f"{json.dumps(flow)[:400]}"
        )

    (prompt or print)(flow["message"])
    result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        error = result.get("error", "")
        description = result.get("error_description", "")
        hint = ""
        if "AADSTS65001" in description or error == "consent_required":
            hint = (
                "\n\nThe tenant requires administrator consent for Calendars.Read. "
                "Ask IT to grant it, or use the local EventKit route documented in "
                "docs/SETUP-ko.md, which needs no tenant change at all."
            )
        elif "AADSTS50105" in description or "AADSTS53003" in description:
            hint = (
                "\n\nA Conditional Access policy blocked the sign-in. This usually "
                "means the device must be managed/compliant. The local EventKit route "
                "avoids this entirely."
            )
        raise MsAuthError(f"sign-in failed: {error}: {description}{hint}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cache.serialize(), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return str(path)
