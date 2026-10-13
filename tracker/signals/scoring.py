"""The 0-100 Move Score. The score is the sum of several *signals* (news, price, volume...).
Each signal is a plug-in: a class with a `compute(ctx)` method returning (points, reasons).
Which signals run, and the most points each can give, come from `scoring.signals` in config.yaml.
If the maximums don't add up to 100 the total is rescaled back to 0-100, so the alert
thresholds keep their meaning. Rescaling shifts weight between signals, though: adding a signal
without lowering another one makes every existing signal count a little less, so a stock that
scores high only on the old signals can end up a few points lower."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime

from ..models import NewsEvent, PriceSnapshot, ScoreResult
from ..plugins import get as get_plugin
from ..plugins import register
from .catalysts import SHOCK_CATALYSTS

log = logging.getLogger(__name__)

DEFAULT_SIGNALS = {"news": 30, "price": 25, "volume": 25, "volatility": 10, "theme": 10}


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def ticker_themes(ticker: str, themes: dict) -> list[str]:
    return [name for name, th in themes.items() if ticker in th["tickers"]]


@dataclass
class SignalContext:
    """Everything a signal may look at for one ticker in one cycle."""

    ticker: str
    events: list[NewsEvent]                 # recent news stories (all tickers)
    snapshot: PriceSnapshot | None          # latest price data, None if unavailable
    themes: dict                            # config themes
    sc: dict                                # config `scoring` section
    now: datetime
    context: dict[str, PriceSnapshot]       # context symbols such as crude futures
    peer_scores: dict[str, float]           # first-pass scores of other tickers
    extras: dict = field(default_factory=dict)  # signals can leave notes for later ones

    @property
    def my_themes(self) -> list[str]:
        return ticker_themes(self.ticker, self.themes)


class Signal:
    """Base class for one part of the Move Score. Subclass it and decorate with
    @register("signals", "your_name"), then add `your_name: <max points>` under
    scoring.signals in config.yaml."""

    def __init__(self, max_points: float, cfg=None):
        self.max_points = float(max_points)
        self.cfg = cfg

    def compute(self, ctx: SignalContext) -> tuple[float, list[str]]:
        raise NotImplementedError


# --- built-in signal maths (plain functions so the backtest can reuse them) ---------------

def signed_sentiment(event: NewsEvent, ticker_theme_names: list[str], themes: dict) -> float:
    """Event sentiment from this ticker's point of view. Supply shocks flip by sector:
    a Red Sea attack reads 'negative' in text but tends to lift tanker rates."""
    s = event.sentiment
    if event.catalyst in SHOCK_CATALYSTS:
        for name in ticker_theme_names:
            if name in event.themes or not event.themes:
                direction = themes[name].get("shock_direction", "neutral")
                if direction == "bullish":
                    return max(abs(s), 0.5)
                if direction == "bearish":
                    return -max(abs(s), 0.5)
    return s


def news_component(ticker: str, events: list[NewsEvent], themes: dict, sc: dict, now: datetime,
                   m: float = 30):
    my_themes = ticker_themes(ticker, themes)
    half_life = sc["news_half_life_hours"]
    total, weighted_sent, weight_sum = 0.0, 0.0, 0.0
    top: tuple[float, NewsEvent] | None = None
    for ev in events:
        if ticker in ev.tickers:
            relevance = 1.0
        elif set(ev.themes) & set(my_themes):
            relevance = sc["theme_only_weight"]
        else:
            continue
        age_h = max(0.0, (now - ev.first_seen).total_seconds() / 3600)
        if age_h > sc["news_lookback_hours"]:
            continue
        contribution = ev.strength() * relevance * 0.5 ** (age_h / half_life)
        total += contribution
        sent = signed_sentiment(ev, my_themes, themes)
        weighted_sent += sent * contribution
        weight_sum += contribution
        if top is None or contribution > top[0]:
            top = (contribution, ev)
    points = m * (1 - math.exp(-1.5 * total))
    sentiment = weighted_sent / weight_sum if weight_sum else 0.0
    return points, (top[1] if top else None), sentiment


def price_component(snap: PriceSnapshot, sc: dict, m: float = 25) -> tuple[float, list[str]]:
    reasons = []
    pct, gap = snap.pct_change, snap.gap_pct
    pts = _clip01(abs(pct) / sc["big_move_pct"]) * 0.6 * m
    pts += _clip01(abs(gap) / sc["big_gap_pct"]) * 0.2 * m
    if abs(pct) >= 2:
        reasons.append(f"{pct:+.1f}% vs prior close")
    if abs(gap) >= 2:
        reasons.append(f"gapped {gap:+.1f}%")
    if snap.price > snap.high_20:
        pts += 0.2 * m
        reasons.append("broke 20-day high")
    elif snap.price < snap.low_20:
        pts += 0.2 * m
        reasons.append("broke 20-day low")
    return min(m, pts), reasons


def volume_component(snap: PriceSnapshot, sc: dict, m: float = 25) -> tuple[float, list[str]]:
    if snap.session_fraction <= 0 or snap.volume <= 0:
        return 0.0, []
    rvol = snap.rvol
    pts = _clip01((rvol - 1) / (sc["big_rvol"] - 1)) * m
    return pts, ([f"relative volume {rvol:.1f}x"] if rvol >= 1.8 else [])


def volatility_component(snap: PriceSnapshot, sc: dict, m: float = 10) -> tuple[float, list[str]]:
    reasons, pts = [], 0.0
    if snap.atr_14 > 0:
        r = (snap.day_high - snap.day_low) / snap.atr_14
        pts += _clip01((r - 1) / 1.5) * 0.6 * m
        if r >= 1.6:
            reasons.append(f"day range {r:.1f}x ATR")
    if snap.price > snap.bb_upper or snap.price < snap.bb_lower:
        pts += 0.4 * m
        reasons.append("outside Bollinger band")
    return min(m, pts), reasons


def theme_component(ticker: str, themes: dict, context: dict[str, PriceSnapshot],
                    peer_scores: dict[str, float], sc: dict, m: float = 10) -> tuple[float, list[str]]:
    reasons = []
    best_ctx, best_peer = (0.0, ""), (0.0, "")
    for name in ticker_themes(ticker, themes):
        th = themes[name]
        for sym in th.get("context_symbols", []):
            snap = context.get(sym)
            if snap and abs(snap.pct_change) > abs(best_ctx[0]):
                best_ctx = (snap.pct_change, sym)
        for peer in th["tickers"]:
            if peer != ticker and peer_scores.get(peer, 0) > best_peer[0]:
                best_peer = (peer_scores[peer], peer)
    pts = _clip01(abs(best_ctx[0]) / 4) * 0.5 * m
    if abs(best_ctx[0]) >= 1.5:
        reasons.append(f"{best_ctx[1]} {best_ctx[0]:+.1f}%")
    pts += _clip01((best_peer[0] - 40) / 40) * 0.5 * m
    if best_peer[0] >= 55:
        reasons.append(f"peer {best_peer[1]} scoring {best_peer[0]:.0f}")
    return min(m, pts), reasons


# --- built-in signals as plug-ins ----------------------------------------------------------

@register("signals", "news")
class NewsSignal(Signal):
    """Catalyst strength x sentiment x novelty x outlets, fading with age."""

    def compute(self, ctx):
        points, top, sentiment = news_component(ctx.ticker, ctx.events, ctx.themes, ctx.sc, ctx.now,
                                                self.max_points)
        ctx.extras["top_event"], ctx.extras["news_sentiment"] = top, sentiment
        if not top:
            return points, []
        plural = "s" if top.source_count != 1 else ""
        return points, [f"{top.catalyst.replace('_', ' ')} news ({top.source_count} source{plural})"]


@register("signals", "price")
class PriceSignal(Signal):
    """% move, opening gap, 20-day breakout."""

    def compute(self, ctx):
        return price_component(ctx.snapshot, ctx.sc, self.max_points) if ctx.snapshot else (0.0, [])


@register("signals", "volume")
class VolumeSignal(Signal):
    """Relative volume vs the 20-day average, adjusted for time of day."""

    def compute(self, ctx):
        return volume_component(ctx.snapshot, ctx.sc, self.max_points) if ctx.snapshot else (0.0, [])


@register("signals", "volatility")
class VolatilitySignal(Signal):
    """Day range vs ATR, Bollinger band breakout."""

    def compute(self, ctx):
        return volatility_component(ctx.snapshot, ctx.sc, self.max_points) if ctx.snapshot else (0.0, [])


@register("signals", "theme")
class ThemeSignal(Signal):
    """Context symbols (e.g. crude oil) moving, or a same-theme peer already scoring high."""

    def compute(self, ctx):
        return theme_component(ctx.ticker, ctx.themes, ctx.context, ctx.peer_scores, ctx.sc, self.max_points)


def build_signals(sc: dict, cfg=None) -> list[Signal]:
    signals = []
    for name, max_points in (sc.get("signals") or DEFAULT_SIGNALS).items():
        cls = get_plugin("signals", name)
        if cls is None:
            log.warning("scoring.signals names %r but no such signal exists", name)
            continue
        if not max_points:
            continue
        try:
            sig = cls(max_points, cfg)
        except Exception:
            log.exception("could not start signal %r; leaving it out", name)
            continue
        if not hasattr(sig, "max_points") or not hasattr(sig, "compute"):
            log.warning("signal %r doesn't look like a Signal (missing max_points/compute); leaving it out", name)
            continue
        signals.append(sig)
    return signals


def score_ticker(ticker: str, events: list[NewsEvent], snap: PriceSnapshot | None, themes: dict,
                 sc: dict, now: datetime, context: dict[str, PriceSnapshot] | None = None,
                 peer_scores: dict[str, float] | None = None,
                 signals: list[Signal] | None = None) -> ScoreResult:
    signals = signals if signals is not None else build_signals(sc)
    ctx = SignalContext(ticker, events, snap, themes, sc, now, context or {}, peer_scores or {})
    comps: dict[str, float] = {}
    reasons: list[str] = []
    for sig in signals:
        name = getattr(sig, "plugin_name", type(sig).__name__)
        try:
            points, why = sig.compute(ctx)
            points = max(0.0, min(sig.max_points, float(points)))
            why = [why] if isinstance(why, str) else [str(w) for w in (why or [])]
        except Exception:
            log.exception("signal %s failed for %s; counting it as 0", name, ticker)
            points, why = 0.0, []
        comps[name] = round(points, 1)
        reasons += why

    total_max = sum(s.max_points for s in signals) or 100.0
    score = round(min(100.0, sum(comps.values()) * 100.0 / total_max), 1)

    news_sent = ctx.extras.get("news_sentiment", 0.0)
    price_dir = 0 if not snap or abs(snap.pct_change) < 0.5 else (1 if snap.pct_change > 0 else -1)
    news_dir = 0 if abs(news_sent) < 0.15 else (1 if news_sent > 0 else -1)
    if price_dir and news_dir and price_dir != news_dir:
        direction = "mixed"
    else:
        d = price_dir or news_dir
        direction = "up" if d > 0 else "down" if d < 0 else "mixed"
    return ScoreResult(
        ticker=ticker,
        score=score,
        components=comps,
        direction=direction,
        reasons=reasons,
        top_event=ctx.extras.get("top_event"),
        snapshot=snap,
        themes=ctx.my_themes,
        news_sentiment=round(news_sent, 2),
        asof=now,
    )
