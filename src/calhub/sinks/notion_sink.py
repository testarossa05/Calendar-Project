"""Notion sink -- mirrors the merged event set into a Notion database.

Each page written carries the calhub key and a content hash in two text
properties, so a later run updates rather than duplicates, and unchanged events
cost no API calls. A page in the database *without* a key was created by hand and
is never touched.

Notion deletes by archiving, which is what this does: an event that disappears
upstream is moved to the trash and can be restored, rather than destroyed.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any, Optional

import requests

from ..models import OWNER_TAG, Event, SyncPlan
from ..util import get_tz
from .base import Sink, SinkError

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"

# Notion allows roughly three requests a second, averaged. Writes are issued one
# at a time, so a fixed floor between them is enough and keeps a large first run
# from being throttled into failure.
_MIN_INTERVAL = 0.35


class NotionSink(Sink):
    kind = "notion"

    def __init__(self, cfg, app) -> None:
        super().__init__(cfg, app)
        self._database_id = str(self.option("database_id", required=True))
        self._token = str(self.option("token", required=True))
        self._existing: dict[str, dict[str, Any]] = {}
        self._last_call = 0.0

        # Property names are configurable because the database may already exist
        # with the user's own column names.
        self.p_title = str(self.option("title_property", "Name"))
        self.p_date = str(self.option("date_property", "Date"))
        self.p_source = str(self.option("source_property", "Source"))
        self.p_location = str(self.option("location_property", "Location"))
        self.p_key = str(self.option("key_property", "calhub Key"))
        self.p_hash = str(self.option("hash_property", "calhub Hash"))

    # -- HTTP ---------------------------------------------------------------

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, payload: Optional[dict] = None) -> dict:
        elapsed = time.monotonic() - self._last_call
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)

        url = f"{API}{path}"
        timeout = int(self.option("timeout_seconds", 60))
        try:
            response = requests.request(
                method, url, headers=self._headers, json=payload, timeout=timeout
            )
        except requests.RequestException as exc:
            raise SinkError(f"Notion request failed: {exc}") from exc
        finally:
            self._last_call = time.monotonic()

        if response.status_code == 429:
            # Respect the server's own backoff rather than guessing.
            wait = float(response.headers.get("Retry-After", "2"))
            time.sleep(min(wait, 30))
            return self._request(method, path, payload)

        if response.status_code == 404:
            raise SinkError(
                f"Notion returned 404 for {path}. The database id may be wrong, or the "
                "database has not been shared with the integration "
                "(open it in Notion -> ... -> Connections -> add your integration)."
            )
        if not response.ok:
            raise SinkError(f"Notion API {response.status_code}: {response.text[:400]}")
        return response.json()

    # -- planning -----------------------------------------------------------

    def _load_existing(self) -> None:
        """Index the pages this tool already owns."""
        existing: dict[str, dict[str, Any]] = {}
        cursor: Optional[str] = None
        for _ in range(200):
            payload: dict[str, Any] = {
                "page_size": 100,
                "filter": {"property": self.p_key, "rich_text": {"is_not_empty": True}},
            }
            if cursor:
                payload["start_cursor"] = cursor
            body = self._request("POST", f"/databases/{self._database_id}/query", payload)

            for page in body.get("results", []):
                key = _plain_text(page.get("properties", {}).get(self.p_key))
                if not key or not key.startswith(f"{OWNER_TAG}:"):
                    continue
                key = key[len(OWNER_TAG) + 1 :]
                if key in existing:
                    # An interrupted earlier run can leave a second copy; keep the
                    # first and let the rest be archived as orphans.
                    existing[f"__dupe__{page['id']}"] = page
                    continue
                existing[key] = page

            if not body.get("has_more"):
                break
            cursor = body.get("next_cursor")
            if not cursor:
                break
        self._existing = existing

    def plan(self, events: list[Event], window_start, window_end) -> SyncPlan:
        self._verify_schema()
        self._load_existing()

        plan = SyncPlan()
        desired: set[str] = set()
        for event in events:
            desired.add(event.key)
            page = self._existing.get(event.key)
            if page is None:
                plan.to_create.append(event)
                continue
            current_hash = _plain_text(page.get("properties", {}).get(self.p_hash))
            if current_hash == event.content_hash():
                plan.unchanged += 1
            else:
                plan.to_update.append((page["id"], event))

        for key, page in self._existing.items():
            if key.startswith("__dupe__") or key not in desired:
                title = _plain_text(page.get("properties", {}).get(self.p_title)) or "(no title)"
                plan.to_delete.append((page["id"], title))
        return plan

    def _verify_schema(self) -> None:
        """Fail early and by name when a property is missing or the wrong type.

        Notion silently ignores an unknown property on write, so without this a
        run would look successful while writing nothing usable.
        """
        body = self._request("GET", f"/databases/{self._database_id}")
        properties = body.get("properties", {})
        expected = {
            self.p_title: "title",
            self.p_date: "date",
            self.p_key: "rich_text",
            self.p_hash: "rich_text",
        }
        problems = []
        for name, wanted in expected.items():
            found = properties.get(name)
            if found is None:
                problems.append(f"missing property {name!r} (expected type {wanted})")
            elif found.get("type") != wanted:
                problems.append(
                    f"property {name!r} is type {found.get('type')!r}, expected {wanted!r}"
                )
        if problems:
            available = ", ".join(f"{k} ({v.get('type')})" for k, v in properties.items())
            raise SinkError(
                "the Notion database schema does not match:\n  - "
                + "\n  - ".join(problems)
                + f"\nProperties found: {available}\n"
                "Run 'calhub notion-setup --parent-page <id>' to create a database with "
                "the right schema, or set the *_property options to your column names."
            )

    # -- applying -----------------------------------------------------------

    def apply(self, plan: SyncPlan) -> dict[str, int]:
        created = updated = archived = failed = 0

        for event in plan.to_create:
            try:
                self._request(
                    "POST",
                    "/pages",
                    {
                        "parent": {"database_id": self._database_id},
                        "properties": self._properties(event),
                    },
                )
                created += 1
            except SinkError as exc:
                failed += 1
                _warn(f"create failed for {event.title!r}: {exc}")

        for page_id, event in plan.to_update:
            try:
                self._request("PATCH", f"/pages/{page_id}", {"properties": self._properties(event)})
                updated += 1
            except SinkError as exc:
                failed += 1
                _warn(f"update failed for {event.title!r}: {exc}")

        for page_id, title in plan.to_delete:
            try:
                # Notion has no hard delete through the API; archiving moves the
                # page to the trash, where it can still be restored.
                self._request("PATCH", f"/pages/{page_id}", {"archived": True})
                archived += 1
            except SinkError as exc:
                failed += 1
                _warn(f"archive failed for {title!r}: {exc}")

        return {
            "created": created,
            "updated": updated,
            "archived": archived,
            "failed": failed,
            "unchanged": plan.unchanged,
        }

    def _properties(self, event: Event) -> dict[str, Any]:
        zone = get_tz(str(self.option("timezone", self.app.timezone)))

        if event.all_day:
            start_value = event.start.astimezone(zone).date().isoformat()
            # Notion's all-day end is inclusive while the internal model, like
            # iCalendar, is exclusive. Writing the exclusive value would stretch
            # every all-day event by one day.
            last_day = event.end.astimezone(zone).date() - timedelta(days=1)
            end_value = last_day.isoformat() if last_day.isoformat() != start_value else None
        else:
            start_value = event.start.astimezone(zone).isoformat()
            end_value = event.end.astimezone(zone).isoformat()

        date_value: dict[str, Any] = {"start": start_value}
        if end_value:
            date_value["end"] = end_value

        properties: dict[str, Any] = {
            self.p_title: {"title": [{"text": {"content": _clip(event.title, 2000)}}]},
            self.p_date: {"date": date_value},
            self.p_key: {"rich_text": [{"text": {"content": f"{OWNER_TAG}:{event.key}"}}]},
            self.p_hash: {"rich_text": [{"text": {"content": event.content_hash()}}]},
        }
        if self.p_source:
            properties[self.p_source] = {
                "rich_text": [{"text": {"content": _clip(event.ref.source_id, 2000)}}]
            }
        if self.p_location and event.location:
            properties[self.p_location] = {
                "rich_text": [{"text": {"content": _clip(event.location, 2000)}}]
            }
        return properties


def create_database(token: str, parent_page_id: str, title: str = "Unified Calendar") -> dict:
    """Create a database with the schema this sink expects."""
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": {
            "Name": {"title": {}},
            "Date": {"date": {}},
            "Source": {"rich_text": {}},
            "Location": {"rich_text": {}},
            "calhub Key": {"rich_text": {}},
            "calhub Hash": {"rich_text": {}},
        },
    }
    response = requests.post(
        f"{API}/databases",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    if response.status_code == 404:
        raise SinkError(
            "Notion returned 404. Check the parent page id, and that the page has been "
            "shared with the integration (page -> ... -> Connections -> add it)."
        )
    if not response.ok:
        raise SinkError(f"Notion API {response.status_code}: {response.text[:400]}")
    return response.json()


def _plain_text(prop: Any) -> Optional[str]:
    if not isinstance(prop, dict):
        return None
    ptype = prop.get("type")
    parts = prop.get(ptype)
    if not isinstance(parts, list):
        return None
    text = "".join(part.get("plain_text", "") for part in parts).strip()
    return text or None


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _warn(message: str) -> None:
    import sys

    print(f"  ! {message}", file=sys.stderr)
