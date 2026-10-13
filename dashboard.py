"""Optional live dashboard: pip install -r requirements-dashboard.txt, then
streamlit run dashboard.py"""

import json

import pandas as pd
import streamlit as st

from tracker import DISCLAIMER
from tracker.config import load_config
from tracker.outcomes import outcome_stats
from tracker.storage import Database

cfg = load_config()
db = Database(cfg.path(cfg["storage"]["db_path"]))

st.set_page_config(page_title="Stock Tracker", layout="wide")
st.title("Stock Tracker")
st.caption(DISCLAIMER)

scores = db.latest_scores()
alerts = db.all_alerts()
stats = outcome_stats(alerts)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Tickers scored", len(scores))
c2.metric("Alerts recorded", len(alerts))
c3.metric("Next-day direction hit rate",
          f"{stats['direction_hit_rate']:.0%}" if stats["direction_hit_rate"] is not None else "n/a")
c4.metric("Avg next-day move", f"{stats['avg_abs_move_1d']:.1f}%" if stats["avg_abs_move_1d"] else "n/a")

st.subheader("Move Scores now")
st.dataframe(pd.DataFrame([{
    "Ticker": r["ticker"], "Score": r["score"], "Direction": r["direction"], "Price": r["price"],
    "Change %": r["pct_change"], "RVOL": r["rvol"], **json.loads(r["components"]),
    "Why": "; ".join(json.loads(r["reasons"])),
} for r in scores]), use_container_width=True, hide_index=True)

left, right = st.columns(2)
with left:
    st.subheader("Recent alerts")
    st.dataframe(pd.DataFrame([{"Time": a["asof"][:16], "Ticker": a["ticker"], "Tier": a["tier"],
                                "Score": a["score"], "Headline": a["headline"]} for a in alerts[::-1][:50]]),
                 use_container_width=True, hide_index=True)
with right:
    st.subheader("Latest stories")
    st.dataframe(pd.DataFrame([{"Seen": e.first_seen.isoformat()[:16], "Tickers": ", ".join(e.tickers),
                                "Catalyst": e.catalyst, "Sources": e.source_count, "Headline": e.title}
                               for e in db.recent_events(24)[::-1][:50]]),
                 use_container_width=True, hide_index=True)

if st.button("Refresh"):
    st.rerun()
