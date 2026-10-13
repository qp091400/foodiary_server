"""시간 규칙: DB에는 timezone 없는 UTC, "오늘"과 월 경계는 Asia/Seoul 기준."""

import calendar
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings


def utcnow() -> datetime:
    """DB 저장용 naive UTC 시각."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today() -> date:
    """서비스 기준 시간대(기본 Asia/Seoul)의 오늘 날짜."""
    return datetime.now(ZoneInfo(settings.timezone)).date()


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """해당 월의 첫날과 마지막 날."""
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
