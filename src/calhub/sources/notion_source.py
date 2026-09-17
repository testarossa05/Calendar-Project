"""Notion database source.

Reads a Notion database via the official API and turns rows with a date property
into events. Use this rather than Notion's ICS export when you want the page URL,
extra properties in the description, or filtering by status.

Setup: create an internal integration at notion.so/my-integrations, copy the
token, then share the target database with that integration.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

import requests
from dateutil import parser as dateparser

from ..models import Event, SourceRef
from ..util import get_tz, to_utc
from .base import Source, SourceError

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"


class NotionSource(Source):
    kind = "notion"

    def fetch(self, window_start: datetime, window_end: datetime) -> list[Event]:
        token = self.option("token", required=True)
        database_id = self.option("database_id", required=True)
        date_property = self.option("date_property", "Date")
        title_property = self.option("title_property")  # None -> auto-detect
        default_tz = get_tz(self.option("timezone", self.app.timezone))

        rows = self._query_all(token, database_id, date_property, window_start, window_end)

        events: list[Event] = []
        for row in rows:
            event = self._to_event(row, date_property, title_property, default_tz)
            if event is not None:
                events.append(event)
        return events

    def _query_all(
        self,
        token: str,
        database_id: str,
        date_property: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": _VERSION,
            "Content-Type": "application/json",
        }
        # Filter server-side so a large database does not have to be paged in full.
        payload: dict[str, Any] = {
            "page_size": 100,
            "filter": {
                "and": [
                    {
                        "property": date_property,
                        "date": {"on_or_after": window_start.date().isoformat()},
                    },
                    {
                        "property": date_property,
                        "date": {"on_or_before": window_end.date().isoformat()},
                    },
                ]
            },
        }
        extra_filter = self.option("filter")
        if extra_filter:
            payload["filter"]["and"].append(extra_filter)

        rows: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        timeout = int(self.option("timeout_seconds", 60))
        for _ in range(100):  # hard page cap; 100 pages * 100 rows is plenty
            if cursor:
                payload["start_cursor"] = cursor
            try:
                response = requests.post(
                    f"{_API}/databases/{database_id}/query",
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                raise SourceError(self.cfg.id, f"Notion request failed: {exc}") from exc
            if response.status_code == 404:
                raise SourceError(
                    self.cfg.id,
                    "Notion returned 404. The database id may be wrong, or the database "
                    "has not been shared with the integration (Notion page -> ... -> "
                    "Connections -> add your integration).",
                )
            if response.status_code == 400 and date_property in response.text:
                raise SourceError(
                    self.cfg.id,
                    f"Notion rejected the filter on date property {date_property!r}. "
                    "Check 'date_property' matches the column name exactly.",
                )
            if not response.ok:
                raise SourceError(
                    self.cfg.id, f"Notion API {response.status_code}: {response.text[:300]}"
                )
            body = response.json()
            rows.extend(body.get("results", []))
            if not body.get("has_more"):
                break
            cursor = body.get("next_cursor")
            if not cursor:
                break
        return rows

    def _to_event(
        self, row: dict[str, Any], date_property: str, title_property: Optional[str], default_tz
    ) -> Optional[Event]:
        props = row.get("properties") or {}
        date_prop = props.get(date_property)
        if not date_prop or date_prop.get("type") != "date":
            return None
        date_value = date_prop.get("date")
        if not date_value or not date_value.get("start"):
            return None

        start_raw = date_value["start"]
        end_raw = date_value.get("end")
        # Notion writes a bare "YYYY-MM-DD" for an all-day entry.
        all_day = len(start_raw) == 10

        start_dt = dateparser.isoparse(start_raw)
        start = to_utc(start_dt, default_tz)
        if end_raw:
            end = to_utc(dateparser.isoparse(end_raw), default_tz)
            if all_day:
                # Notion's end date is inclusive; iCalendar's is exclusive.
                end = end + timedelta(days=1)
        else:
            end = start + timedelta(days=1) if all_day else start + timedelta(hours=1)

        title = self._extract_title(props, title_property) or "(untitled)"

        return Event(
            ref=SourceRef(source_id=self.cfg.id, kind=self.kind, uid=row["id"]),
            title=title,
            start=start,
            end=end,
            all_day=all_day,
            description=self._build_description(props),
            location=self._plain(props.get(self.option("location_property", "Location"))),
            url=row.get("url"),
            status="confirmed",
            tzid=date_value.get("time_zone") or self.option("timezone", self.app.timezone),
        )

    def _extract_title(self, props: dict[str, Any], title_property: Optional[str]) -> Optional[str]:
        if title_property and title_property in props:
            return self._plain(props[title_property])
        for value in props.values():
            if isinstance(value, dict) and value.get("type") == "title":
                return self._plain(value)
        return None

    def _build_description(self, props: dict[str, Any]) -> Optional[str]:
        """Fold selected extra columns into the mirrored description."""
        wanted = self.option("description_properties") or []
        if not wanted:
            return None
        lines = []
        for name in wanted:
            text = self._plain(props.get(name))
            if text:
                lines.append(f"{name}: {text}")
        return "\n".join(lines) or None

    @staticmethod
    def _plain(prop: Any) -> Optional[str]:
        """Flatten any Notion property value to readable text."""
        if not isinstance(prop, dict):
            return None
        ptype = prop.get("type")
        value = prop.get(ptype)
        if value is None:
            return None
        if ptype in ("title", "rich_text"):
            return "".join(part.get("plain_text", "") for part in value).strip() or None
        if ptype in ("select", "status"):
            return value.get("name")
        if ptype == "multi_select":
            return ", ".join(v.get("name", "") for v in value) or None
        if ptype in ("url", "email", "phone_number"):
            return str(value)
        if ptype == "number":
            return str(value)
        if ptype == "checkbox":
            return "yes" if value else "no"
        if ptype == "people":
            return ", ".join(v.get("name", "") for v in value) or None
        if ptype == "formula":
            inner = value.get(value.get("type"))
            return str(inner) if inner is not None else None
        if ptype == "date":
            return value.get("start")
        return None
