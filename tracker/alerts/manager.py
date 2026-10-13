"""Decides which scores become alerts: tiers, cooldowns, caps, quiet hours and mute."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..market_hours import ET, in_window
from ..models import TIER_RANK, ScoreResult
from ..storage import Database
from .channels import Channel
from .formatting import format_alert

log = logging.getLogger(__name__)


class AlertManager:
    def __init__(self, settings: dict, db: Database, channels: list[Channel]):
        self.s = settings
        self.db = db
        self.channels = channels

    def _start_of_day(self, now: datetime) -> datetime:
        local = now.astimezone(ET)
        return local.replace(hour=0, minute=0, second=0, microsecond=0)

    def muted(self, now: datetime) -> bool:
        until = self.db.kv_get("mute_until")
        return bool(until) and datetime.fromisoformat(until) > now

    def should_alert(self, r: ScoreResult, now: datetime) -> str | None:
        tier = r.tier(self.s)
        if not tier:
            return None
        if self.muted(now) and tier != "URGENT":
            return None
        if in_window(now.astimezone(ET).time(), self.s.get("quiet_hours_et")) and tier != "URGENT":
            return None
        day_start = self._start_of_day(now)
        if len(self.db.alerts_since(day_start)) >= self.s["max_per_day"]:
            return None
        today = self.db.alerts_since(day_start, r.ticker)
        if len(today) >= self.s["max_per_ticker_per_day"]:
            return None
        last = self.db.last_alert(r.ticker)
        if last:
            since = now - datetime.fromisoformat(last["asof"])
            if since < timedelta(minutes=self.s["cooldown_minutes"]):
                # Inside the cooldown (even across midnight) only a higher tier gets through.
                if TIER_RANK[tier] <= TIER_RANK.get(last["tier"], 0):
                    return None
            else:
                last_tier_today = max((TIER_RANK[a["tier"]] for a in today), default=0)
                if today and TIER_RANK[tier] <= last_tier_today and r.score < last["score"] + 5:
                    return None  # same tier again later in the day: only if the score rose meaningfully
        return tier

    MAX_DELIVERY_TRIES = 3

    def broadcast(self, text: str) -> bool:
        """Send to every channel. One broken channel never stops the others.
        Returns True if at least one real (non-console) channel delivered it,
        or if the console is the only channel configured."""
        real, delivered = False, False
        for ch in self.channels:
            try:
                ok = bool(ch.send(text))
            except Exception:
                log.exception("alert channel %s failed", getattr(ch, "name", ch))
                ok = False
            if getattr(ch, "name", "") != "console":
                real = True
                delivered = delivered or ok
        return delivered or not real

    def process(self, results: list[ScoreResult], now: datetime) -> list[tuple[ScoreResult, str]]:
        sent = []
        for r in sorted(results, key=lambda x: x.score, reverse=True):
            tier = self.should_alert(r, now)
            if not tier:
                continue
            text = format_alert(r, tier)
            key = f"undelivered_{r.ticker}"
            if not self.broadcast(text):
                tries = int(self.db.kv_get(key, "0") or 0) + 1
                self.db.kv_set(key, str(tries))
                if tries < self.MAX_DELIVERY_TRIES:
                    log.warning("alert for %s was not delivered; retrying next cycle", r.ticker)
                    continue
                log.error("alert for %s failed to deliver %d times; recording it anyway", r.ticker, tries)
            self.db.kv_set(key, "0")
            self.db.save_alert(r, tier, text, at=now)
            log.info("alert %s %s score=%.0f", tier, r.ticker, r.score)
            sent.append((r, tier))
        return sent
