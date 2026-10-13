"""Exports scores, alerts and news to an Excel workbook for review."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import DISCLAIMER
from .outcomes import outcome_stats
from .storage import Database


def _pct(after, before):
    return round((after / before - 1) * 100, 2) if after and before else None


def export_excel(db: Database, out: Path) -> Path:
    scores = pd.DataFrame([{
        "Ticker": r["ticker"], "Score": r["score"], "Direction": r["direction"], "Price": r["price"],
        "Change %": round(r["pct_change"], 2) if r["pct_change"] is not None else None,
        "Rel. volume": round(r["rvol"], 2) if r["rvol"] is not None else None,
        **{k.title(): v for k, v in json.loads(r["components"]).items()},
        "Why": "; ".join(json.loads(r["reasons"])), "As of (UTC)": r["asof"],
    } for r in db.latest_scores()])
    alerts_rows = db.all_alerts()
    alerts = pd.DataFrame([{
        "Time (UTC)": a["asof"], "Ticker": a["ticker"], "Tier": a["tier"], "Score": a["score"],
        "Direction": a["direction"], "Price": a["price"], "Headline": a["headline"], "Link": a["url"],
        "+1h %": _pct(a["price_1h"], a["price"]), "+1d %": _pct(a["price_1d"], a["price"]),
        "+5d %": _pct(a["price_5d"], a["price"]),
    } for a in alerts_rows])
    news = pd.DataFrame([{
        "First seen (UTC)": e.first_seen.isoformat(), "Headline": e.title, "Tickers": ", ".join(e.tickers),
        "Themes": ", ".join(e.themes), "Catalyst": e.catalyst, "Sentiment": round(e.sentiment, 2),
        "Sources": e.source_count, "Novelty": e.novelty, "Link": e.url,
    } for e in reversed(db.recent_events(24 * 7))])
    stats = outcome_stats(alerts_rows)
    summary = pd.DataFrame([
        ("Alerts recorded", len(alerts_rows)),
        ("Alerts with next-day result", stats["alerts_with_1d"]),
        ("Direction hit rate (next day)", f"{stats['direction_hit_rate']:.0%}" if stats["direction_hit_rate"] is not None else "n/a"),
        ("Average next-day move %", round(stats["avg_abs_move_1d"], 2) if stats["avg_abs_move_1d"] else "n/a"),
        ("Disclaimer", DISCLAIMER),
    ], columns=["Metric", "Value"])

    out.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out, engine="openpyxl") as xl:
        for name, df in (("Summary", summary), ("Scores", scores), ("Alerts", alerts), ("News", news)):
            df.to_excel(xl, sheet_name=name, index=False)
            ws = xl.sheets[name]
            ws.freeze_panes = "A2"
            for col in ws.columns:
                width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                ws.column_dimensions[col[0].column_letter].width = min(60, max(10, width + 2))
    return out
