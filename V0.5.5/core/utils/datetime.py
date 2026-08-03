"""
System-wide Datetime and Timestamp Utilities for AutoCheck.
Provides consistent strftime formats, ISO parsing/formatting, and run_id generation.
"""
import datetime
from typing import Optional, Union

# Common format strings
RUN_ID_FORMAT = "%Y-%m-%d_%H%M%S"
COMPACT_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"
DISPLAY_DATE_FORMAT = "%Y-%m-%d"
DISPLAY_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def current_datetime() -> datetime.datetime:
    """Return the current local datetime object."""
    return datetime.datetime.now()


def format_run_id(dt: Optional[datetime.datetime] = None) -> str:
    """Format datetime into a standard run_id format (YYYY-MM-DD_HHMMSS)."""
    if dt is None:
        dt = current_datetime()
    return dt.strftime(RUN_ID_FORMAT)


def format_compact(dt: Optional[datetime.datetime] = None) -> str:
    """Format datetime into compact timestamp format (YYYYMMDDHHMMSS)."""
    if dt is None:
        dt = current_datetime()
    return dt.strftime(COMPACT_TIMESTAMP_FORMAT)


def format_iso(dt: Optional[datetime.datetime] = None, timespec: str = "seconds") -> str:
    """Format datetime into ISO 8601 string (e.g. 2026-07-30T08:30:00)."""
    if dt is None:
        dt = current_datetime()
    return dt.isoformat(timespec=timespec)


def parse_iso(dt_str: str) -> Optional[datetime.datetime]:
    """Parse ISO string safely into a datetime object. Returns None if invalid."""
    if not dt_str:
        return None
    try:
        return datetime.datetime.fromisoformat(dt_str)
    except (ValueError, TypeError):
        # Fallback for plain date strings like '2026-07-27'
        try:
            return datetime.datetime.strptime(dt_str[:10], DISPLAY_DATE_FORMAT)
        except (ValueError, TypeError):
            return None
