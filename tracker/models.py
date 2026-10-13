"""Plain data objects passed between adapters, signals, storage and alerts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_id(*parts: str) -> str:
    return hashlib.sha1("|".join(p or "" for p in parts).encode("utf-8")).hexdigest()[:16]


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published: datetime
    summary: str = ""
    hint_tickers: list[str] = field(default_factory=list)  # tickers the feed itself was about
    tickers: list[str] = field(default_factory=list)       # tickers named in the story
    themes: list[str] = field(default_factory=list)
    catalyst: str = "general"
    catalyst_strength: float = 0.2
    catalyst_bias: float = 0.0
    sentiment: float = 0.0
    novelty: float = 1.0
    reason: str = ""
    form_type: str = ""                                     # SEC filings only

    @property
    def id(self) -> str:
        return make_id(self.url or "", self.title.strip().lower())

    @property
    def text(self) -> str:
        return f"{self.title}. {self.summary}".strip()


@dataclass
class NewsEvent:
    """One real-world story, possibly reported by several outlets."""

    event_id: str
    title: str
    url: str
    first_seen: datetime
    tickers: list[str]
    themes: list[str]
    catalyst: str
    catalyst_strength: float
    sentiment: float
    novelty: float
    source_count: int = 1
    sources: list[str] = field(default_factory=list)
    reason: str = ""

    def strength(self) -> float:
        """0..1 how much this story could move a stock, before time decay."""
        source_factor = min(1.0, 0.6 + 0.1 * self.source_count)
        return self.catalyst_strength * (0.5 + 0.5 * abs(self.sentiment)) * self.novelty * source_factor


@dataclass
class PriceSnapshot:
    ticker: str
    price: float
    prev_close: float
    open: float
    day_high: float
    day_low: float
    volume: float
    avg_volume_20: float
    high_20: float          # 20-day high, excluding today
    low_20: float
    atr_14: float
    bb_upper: float
    bb_lower: float
    session_fraction: float  # share of the regular session elapsed (0..1)
    asof: datetime = field(default_factory=utcnow)

    @property
    def pct_change(self) -> float:
        return (self.price / self.prev_close - 1) * 100 if self.prev_close else 0.0

    @property
    def gap_pct(self) -> float:
        return (self.open / self.prev_close - 1) * 100 if self.prev_close else 0.0

    @property
    def rvol(self) -> float:
        expected = self.avg_volume_20 * max(self.session_fraction, 0.1)
        return self.volume / expected if expected else 0.0


@dataclass
class ScoreResult:
    ticker: str
    score: float
    components: dict[str, float]
    direction: str                 # "up", "down" or "mixed"
    reasons: list[str]
    top_event: NewsEvent | None = None
    snapshot: PriceSnapshot | None = None
    themes: list[str] = field(default_factory=list)
    news_sentiment: float = 0.0    # news tone from this ticker's point of view, -1..1
    asof: datetime = field(default_factory=utcnow)

    def tier(self, thresholds: dict) -> str | None:
        if self.score >= thresholds["urgent"]:
            return "URGENT"
        if self.score >= thresholds["hot"]:
            return "HOT"
        if self.score >= thresholds["watch"]:
            return "WATCH"
        return None


TIER_RANK = {None: 0, "WATCH": 1, "HOT": 2, "URGENT": 3}
