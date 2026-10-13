"""US equity market sessions (all times US/Eastern)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

PRE_OPEN = time(4, 0)
OPEN = time(9, 30)
CLOSE = time(16, 0)
POST_CLOSE = time(20, 0)


def now_et() -> datetime:
    return datetime.now(ET)


def parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


class MarketCalendar:
    def __init__(self, holidays: list | None = None):
        self.holidays = {date.fromisoformat(str(h)) for h in (holidays or [])}

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def session(self, when: datetime | None = None) -> str:
        """Returns 'pre', 'regular', 'post' or 'closed'."""
        when = (when or now_et()).astimezone(ET)
        if not self.is_trading_day(when.date()):
            return "closed"
        t = when.time()
        if PRE_OPEN <= t < OPEN:
            return "pre"
        if OPEN <= t < CLOSE:
            return "regular"
        if CLOSE <= t < POST_CLOSE:
            return "post"
        return "closed"

    def session_fraction(self, when: datetime | None = None) -> float:
        """Share of today's regular session elapsed, used to pro-rate volume."""
        when = (when or now_et()).astimezone(ET)
        if not self.is_trading_day(when.date()):
            return 1.0
        t = when.time()
        if t < OPEN:
            return 0.0
        if t >= CLOSE:
            return 1.0
        minutes = (when.hour * 60 + when.minute) - (9 * 60 + 30)
        return max(0.0, min(1.0, minutes / 390))

    def next_trading_days(self, start: date, n: int) -> list[date]:
        out, d = [], start
        while len(out) < n:
            d += timedelta(days=1)
            if self.is_trading_day(d):
                out.append(d)
        return out


def in_window(now: time, window: list[str] | None) -> bool:
    """True if `now` falls inside [start, end), handling windows that cross midnight."""
    if not window:
        return False
    start, end = parse_hhmm(window[0]), parse_hhmm(window[1])
    if start <= end:
        return start <= now < end
    return now >= start or now < end
