"""Records what happened after each alert (+1 hour, +1 trading day, +5 trading days)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd

from .adapters.base import PriceAdapter
from .market_hours import ET, OPEN, MarketCalendar
from .models import PriceSnapshot, utcnow
from .storage import Database

log = logging.getLogger(__name__)


def update_outcomes(db: Database, prices: PriceAdapter, calendar: MarketCalendar,
                    snapshots: dict[str, PriceSnapshot], now: datetime | None = None) -> int:
    now = now or utcnow()
    pending = db.pending_outcomes()
    histories: dict[str, pd.DataFrame] = {}
    filled = 0
    for a in pending:
        t0 = datetime.fromisoformat(a["asof"])
        # +1h is only meaningful shortly after the hour; overnight it would just be the next morning.
        if (a["price_1h"] is None and timedelta(hours=1) <= now - t0 <= timedelta(hours=3)
                and a["ticker"] in snapshots):
            db.set_outcome(a["id"], "price_1h", snapshots[a["ticker"]].price)
            filled += 1
        need = [(col, n) for col, n in (("price_1d", 1), ("price_5d", 5)) if a[col] is None]
        if not need:
            continue
        if a["ticker"] not in histories:
            try:
                histories[a["ticker"]] = prices.history(a["ticker"], "1mo")
            except Exception:
                log.warning("no price history for %s; will fill its outcomes later", a["ticker"])
                histories[a["ticker"]] = pd.DataFrame()
        hist = histories[a["ticker"]]
        if hist is None or hist.empty or "Close" not in hist:
            continue
        closes = {pd.Timestamp(i).date(): float(c) for i, c in hist["Close"].dropna().items()}
        local = t0.astimezone(ET)
        start = local.date()
        if calendar.is_trading_day(start) and local.time() < OPEN:
            start -= timedelta(days=1)  # pre-market alert: "+1 day" is today's close
        days = calendar.next_trading_days(start, 5)
        for col, n in need:
            target = days[n - 1]
            if target in closes and now.astimezone(ET).date() > target:
                db.set_outcome(a["id"], col, closes[target])
                filled += 1
    if filled:
        log.info("outcomes: filled %d values", filled)
    return filled


def outcome_stats(rows) -> dict:
    """Share of alerts whose next-day move went the alerted direction, plus average moves."""
    graded, hits, moves = 0, 0, []
    for a in rows:
        if a["price_1d"] is None or not a["price"]:
            continue
        move = (a["price_1d"] / a["price"] - 1) * 100
        moves.append(abs(move))
        if a["direction"] in ("up", "down"):
            graded += 1
            hits += (move > 0) == (a["direction"] == "up")
    return {
        "alerts_with_1d": len(moves),
        "direction_hit_rate": (hits / graded) if graded else None,
        "avg_abs_move_1d": (sum(moves) / len(moves)) if moves else None,
    }
