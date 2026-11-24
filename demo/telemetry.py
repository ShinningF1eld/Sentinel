import re
from datetime import datetime, timezone


SCHEMA_VERSION = 1
SOURCE_PATHS = {"api": "demo/app.py", "cleanup": "demo/incident.py"}
TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)


def parse_timestamp(value):
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        raise ValueError("expected a timezone-aware ISO 8601 timestamp")
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except (ValueError, OverflowError) as error:
        raise ValueError("invalid UTC timestamp") from error
