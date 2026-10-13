"""Pre-market brief and end-of-day summary."""

from __future__ import annotations

from datetime import timedelta

from . import DISCLAIMER
from .market_hours import ET
from .models import utcnow
from .outcomes import outcome_stats
from .storage import Database


def _top_lines(db: Database, n: int = 8) -> list[str]:
    lines = []
    for r in db.latest_scores()[:n]:
        pct = f"{r['pct_change']:+.1f}%" if r["pct_change"] is not None else "n/a"
        lines.append(f"  {r['ticker']:<6} {r['score']:>4.0f}  {pct:>7}  {r['direction']}")
    return lines


def premarket_brief(db: Database) -> str:
    now = utcnow()
    events = [e for e in db.recent_events(16, now) if e.tickers or e.themes]
    events.sort(key=lambda e: e.strength(), reverse=True)
    lines = [f"☀️ Pre-market brief · {now.astimezone(ET):%a %b %d}", "", "Top Move Scores:"]
    lines += _top_lines(db) or ["  (no scores yet)"]
    lines += ["", "Biggest overnight stories:"]
    for e in events[:6]:
        tag = ", ".join(e.tickers[:4] or e.themes[:2])
        lines.append(f"  • [{tag}] {e.title} ({e.catalyst.replace('_', ' ')}, {e.source_count} src)")
    if not events:
        lines.append("  (none)")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def close_summary(db: Database) -> str:
    now = utcnow()
    start = now.astimezone(ET).replace(hour=0, minute=0, second=0, microsecond=0)
    today = db.alerts_since(start)
    lines = [f"🌙 Close summary · {now.astimezone(ET):%a %b %d}", "", f"Alerts today: {len(today)}"]
    for a in today[-12:]:
        lines.append(f"  {a['tier']:<6} {a['ticker']:<6} score {a['score']:.0f}  {a['direction']}")
    lines += ["", "Top Move Scores at close:"] + (_top_lines(db) or ["  (no scores yet)"])
    stats = outcome_stats(db.alerts_since(now - timedelta(days=30)))
    if stats["alerts_with_1d"]:
        hit = stats["direction_hit_rate"]
        lines += ["", f"Last 30 days: {stats['alerts_with_1d']} alerts graded, "
                      f"avg next-day move {stats['avg_abs_move_1d']:.1f}%"
                      + (f", direction right {hit:.0%} of the time" if hit is not None else "")]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)
