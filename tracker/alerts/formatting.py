"""Turns a ScoreResult into a readable alert."""

from __future__ import annotations

from ..models import ScoreResult

TIER_ICON = {"WATCH": "👀", "HOT": "🔥", "URGENT": "🚨"}
DIR_ICON = {"up": "▲ up", "down": "▼ down", "mixed": "◆ mixed"}


def format_alert(r: ScoreResult, tier: str) -> str:
    lines = [f"{TIER_ICON.get(tier, '')} {tier}  {r.ticker}  score {r.score:.0f}/100  {DIR_ICON[r.direction]}"]
    s = r.snapshot
    if s:
        lines.append(f"Price ${s.price:,.2f} ({s.pct_change:+.1f}%)  RVOL {s.rvol:.1f}x")
    ev = r.top_event
    if ev:
        lines.append(f"Catalyst: {ev.catalyst.replace('_', ' ')} ({ev.source_count} source"
                     f"{'s' if ev.source_count != 1 else ''}), news tone for {r.ticker} {r.news_sentiment:+.2f}")
        lines.append(f"\"{ev.title}\"")
        if ev.reason:
            lines.append(f"AI read: {ev.reason}")
        if ev.url:
            lines.append(ev.url)
    if r.reasons:
        lines.append("Why: " + "; ".join(r.reasons))
    lines.append("Score: " + " · ".join(f"{k} {v:.0f}" for k, v in r.components.items()))
    if r.themes:
        lines.append("Themes: " + ", ".join(r.themes))
    lines.append("Not advice. Research before acting.")
    return "\n".join(lines)
