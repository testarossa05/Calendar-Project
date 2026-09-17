"""Small shared helpers: time normalisation, hashing, text cleanup."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from dateutil import tz as dtz

UTC = timezone.utc


def get_tz(name: str):
    """Resolve an IANA timezone name, raising a clear error when it is unknown."""
    zone = dtz.gettz(name)
    if zone is None:
        raise ValueError(f"unknown timezone: {name!r}")
    return zone


def to_utc(value: datetime | date, default_tz) -> datetime:
    """Coerce a date or naive/aware datetime into an aware UTC datetime.

    A bare ``date`` (an all-day boundary) is anchored at midnight in ``default_tz``
    so that all-day events land on the correct local day rather than drifting.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min)
    else:
        raise TypeError(f"expected date or datetime, got {type(value).__name__}")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz)
    return dt.astimezone(UTC)


def is_date_only(value) -> bool:
    """True for a plain ``date`` -- i.e. an all-day boundary, not a timestamp."""
    return isinstance(value, date) and not isinstance(value, datetime)


def sha1(*parts: str) -> str:
    h = hashlib.sha1()
    for part in parts:
        h.update(part.encode("utf-8", errors="replace"))
        h.update(b"\x00")
    return h.hexdigest()


_WS = re.compile(r"\s+")
# Teams/Zoom/Webex boilerplate that changes wording between clients and would
# otherwise make identical meetings look different across two sources.
_NOISE = re.compile(
    r"(microsoft teams|teams 회의|zoom meeting|google meet|webex|"
    r"join conversation|회의 참가|온라인 회의로 참가)",
    re.IGNORECASE,
)


def normalize_title(title: str) -> str:
    """Fold a title down to a comparable key for cross-source duplicate matching."""
    text = unicodedata.normalize("NFKC", title or "").casefold()
    # Strip reply/forward and tentative markers that one client adds and another does not.
    text = re.sub(r"^\s*(re|fw|fwd|회신|전달)\s*:\s*", "", text)
    # Strip leading bracket tags such as "[Teams]" or "【사내】" that only one client prepends.
    while True:
        stripped = re.sub(r"^\s*[\[\(【][^\]\)】]{0,24}[\]\)】]\s*", "", text)
        if stripped == text:
            break
        text = stripped
    text = _NOISE.sub(" ", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return _WS.sub(" ", text).strip()


def truncate(text: Optional[str], limit: int) -> Optional[str]:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def parse_window(back_days: int, forward_days: int, now: Optional[datetime] = None):
    """Return the (start, end) UTC bounds of the sync window."""
    now = now or datetime.now(UTC)
    anchor = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return anchor - timedelta(days=back_days), anchor + timedelta(days=forward_days)
