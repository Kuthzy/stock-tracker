"""Free price data via yfinance (Yahoo Finance, roughly 15 minutes delayed)."""

from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd

from ..market_hours import ET, MarketCalendar
from ..models import PriceSnapshot, utcnow
from ..plugins import register
from .base import PriceAdapter

log = logging.getLogger(__name__)


def snapshot_from_daily(ticker: str, df: pd.DataFrame, calendar: MarketCalendar,
                        now: datetime | None = None) -> PriceSnapshot | None:
    """Build a snapshot from daily bars. The last bar is 'today' if it is dated today (ET);
    otherwise the market hasn't traded today yet and the last close is the reference."""
    df = df.dropna(subset=["Close"])
    if len(df) < 22:
        return None
    now = (now or utcnow()).astimezone(ET)
    last_date = pd.Timestamp(df.index[-1]).date()
    traded_today = last_date == now.date()
    if traded_today:
        today, hist = df.iloc[-1], df.iloc[:-1]
        price, open_, high, low, volume = (float(today["Close"]), float(today["Open"]),
                                           float(today["High"]), float(today["Low"]),
                                           float(today["Volume"]))
        fraction = calendar.session_fraction(now)
    else:
        hist = df
        last = df.iloc[-1]
        price = open_ = high = low = float(last["Close"])
        volume = 0.0
        fraction = 1.0
    prev_close = float(hist["Close"].iloc[-1])
    last20 = hist.tail(20)
    tr = pd.concat([
        hist["High"] - hist["Low"],
        (hist["High"] - hist["Close"].shift()).abs(),
        (hist["Low"] - hist["Close"].shift()).abs(),
    ], axis=1).max(axis=1)
    closes = last20["Close"]
    sma, std = float(closes.mean()), float(closes.std(ddof=0))
    return PriceSnapshot(
        ticker=ticker,
        price=price,
        prev_close=prev_close,
        open=open_,
        day_high=high,
        day_low=low,
        volume=volume,
        avg_volume_20=float(last20["Volume"].mean()),
        high_20=float(last20["High"].max()),
        low_20=float(last20["Low"].min()),
        atr_14=float(tr.tail(14).mean()),
        bb_upper=sma + 2 * std,
        bb_lower=sma - 2 * std,
        session_fraction=fraction,
    )


@register("prices", "yfinance")
class YFinancePrices(PriceAdapter):
    name = "yfinance"

    def __init__(self, calendar: MarketCalendar):
        self.calendar = calendar

    @classmethod
    def from_config(cls, cfg):
        return cls(MarketCalendar(cfg["schedule"]["holidays"]))

    def _download(self, tickers: list[str], period: str) -> dict[str, pd.DataFrame]:
        import yfinance as yf

        if not tickers:
            return {}
        try:
            raw = yf.download(tickers, period=period, interval="1d", group_by="ticker",
                              auto_adjust=False, progress=False, threads=True)
        except Exception as exc:  # yfinance raises many exception types
            log.warning("yfinance download failed: %s", exc)
            return {}
        out = {}
        if raw is None or raw.empty:
            return out
        if isinstance(raw.columns, pd.MultiIndex):
            for t in tickers:
                if t in raw.columns.get_level_values(0):
                    out[t] = raw[t].replace([np.inf, -np.inf], np.nan)
        else:
            out[tickers[0]] = raw
        return out

    def snapshots(self, tickers: list[str]) -> dict[str, PriceSnapshot]:
        frames = self._download(tickers, "3mo")
        snaps = {}
        for t, df in frames.items():
            try:
                snap = snapshot_from_daily(t, df, self.calendar)
            except Exception as exc:
                log.debug("snapshot failed for %s: %s", t, exc)
                snap = None
            if snap:
                snaps[t] = snap
        missing = set(tickers) - set(snaps)
        if missing:
            log.info("no price data for: %s", ", ".join(sorted(missing)))
        return snaps

    def history(self, ticker: str, period: str = "6mo") -> pd.DataFrame:
        return self._download([ticker], period).get(ticker, pd.DataFrame())
