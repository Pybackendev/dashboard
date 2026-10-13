"""Timezone helpers for OpenCart's local DATETIME values and UTC storage."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from config import settings


def shop_zone() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def source_to_utc(value: datetime | None) -> datetime | None:
    """OpenCart stores local wall time without a zone; interpret it as shop time."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=shop_zone())
    return value.astimezone(UTC)


def utc_to_source(value: datetime | None) -> datetime | None:
    """Convert a UTC watermark to the naive local value expected by MariaDB."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(shop_zone()).replace(tzinfo=None)


def iso_local(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(shop_zone()).isoformat()
