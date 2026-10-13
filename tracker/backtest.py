"""Replays daily price history through the price/volume/volatility parts of the score
to check whether higher scores really preceded bigger moves. (News history isn't
available for free, so this tests the technical half of the Move Score.)"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from .adapters.base import PriceAdapter
from .adapters.yfinance_prices import snapshot_from_daily
from .market_hours import ET, MarketCalendar
from .signals.scoring import DEFAULT_SIGNALS, price_component, volatility_component, volume_component

BUCKETS = [(0, 15), (15, 30), (30, 45), (45, 100)]


def technical_scores(df: pd.DataFrame, sc: dict, calendar: MarketCalendar) -> pd.DataFrame:
    rows = []
    df = df.dropna(subset=["Close"])
    for i in range(25, len(df) - 1):
        day = pd.Timestamp(df.index[i]).date()
        close_time = datetime(day.year, day.month, day.day, 16, 5, tzinfo=ET)
        snap = snapshot_from_daily("X", df.iloc[: i + 1], calendar, now=close_time)
        if not snap:
            continue
        weights = sc.get("signals") or DEFAULT_SIGNALS
        score = (price_component(snap, sc, weights.get("price", 25))[0]
                 + volume_component(snap, sc, weights.get("volume", 25))[0]
                 + volatility_component(snap, sc, weights.get("volatility", 10))[0])
        close = float(df["Close"].iloc[i])
        nxt = float(df["Close"].iloc[i + 1])
        fwd5 = float(df["Close"].iloc[min(i + 5, len(df) - 1)])
        rows.append({"date": day, "score": score, "next_day_pct": (nxt / close - 1) * 100,
                     "next_5d_pct": (fwd5 / close - 1) * 100})
    return pd.DataFrame(rows)


def backtest(prices: PriceAdapter, tickers: list[str], period: str, sc: dict,
             calendar: MarketCalendar) -> pd.DataFrame:
    frames = []
    for t in tickers:
        hist = prices.history(t, period)
        if hist.empty:
            print(f"{t}: no history")
            continue
        res = technical_scores(hist, sc, calendar)
        res.insert(0, "ticker", t)
        frames.append(res)
    if not frames:
        return pd.DataFrame()
    allres = pd.concat(frames)
    out = []
    for lo, hi in BUCKETS:
        b = allres[(allres.score >= lo) & (allres.score < hi)]
        if len(b):
            out.append({"score bucket": f"{lo}-{hi}", "days": len(b),
                        "avg |next-day move| %": round(b.next_day_pct.abs().mean(), 2),
                        "next day up %": round((b.next_day_pct > 0).mean() * 100, 1),
                        "avg next 5d %": round(b.next_5d_pct.mean(), 2)})
    return pd.DataFrame(out)
