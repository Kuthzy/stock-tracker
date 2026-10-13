"""Offline demo with SAMPLE DATA so you can see what alerts look like without any network.
Nothing here is real market data."""

from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path

import pandas as pd

from .adapters.base import NewsAdapter, PriceAdapter
from .alerts.channels import ConsoleChannel
from .alerts.manager import AlertManager
from .config import Config
from .engine import Engine
from .market_hours import MarketCalendar
from .models import NewsItem, PriceSnapshot, utcnow
from .storage import Database


def _snap(t, price, prev, open_, hi, lo, vol, avg, atr):
    return PriceSnapshot(ticker=t, price=price, prev_close=prev, open=open_, day_high=hi, day_low=lo,
                         volume=vol, avg_volume_20=avg, high_20=prev * 1.04, low_20=prev * 0.9,
                         atr_14=atr, bb_upper=prev * 1.05, bb_lower=prev * 0.95, session_fraction=0.5)


class SamplePrices(PriceAdapter):
    name = "sample"

    def snapshots(self, tickers):
        sample = {
            "BWET": _snap("BWET", 26.40, 24.00, 25.10, 26.80, 24.90, 410_000, 120_000, 0.9),
            "FRO": _snap("FRO", 24.90, 23.50, 23.90, 25.10, 23.80, 4_100_000, 3_000_000, 0.7),
            "DHT": _snap("DHT", 12.10, 11.80, 11.85, 12.20, 11.80, 1_300_000, 2_000_000, 0.3),
            "XOM": _snap("XOM", 118.0, 117.2, 117.5, 118.4, 117.0, 7_000_000, 15_000_000, 2.0),
            "BZ=F": _snap("BZ=F", 84.9, 81.6, 82.0, 85.3, 81.9, 0, 0, 1.8),
        }
        return {t: s for t, s in sample.items() if t in tickers}

    def history(self, ticker, period="6mo"):
        return pd.DataFrame()


class SampleNews(NewsAdapter):
    name = "sample"

    def fetch(self, tickers, themes):
        now = utcnow()
        return [
            NewsItem("Houthi missile strike hits crude tanker in Red Sea, shipping lanes disrupted",
                     "https://example.com/1", "Example Wire", now - timedelta(minutes=40)),
            NewsItem("Red Sea: Houthi missile strike hits crude tanker, shipping lanes disrupted",
                     "https://example.com/2", "Example Times", now - timedelta(minutes=30)),
            NewsItem("VLCC tanker rates surge to multi-month high as owners avoid Suez",
                     "https://example.com/3", "Example Shipping", now - timedelta(minutes=20)),
            NewsItem("Frontline (NYSE: FRO) says fleet rerouting around Cape of Good Hope",
                     "https://example.com/4", "Example PR", now - timedelta(minutes=10)),
        ]


def run_demo(cfg: Config) -> None:
    from . import plugins

    print("\n*** DEMO: SAMPLE DATA ONLY, not real market data ***\n")
    plugins.load_plugin_dir(cfg.path(cfg.get("plugins_dir", "plugins")))
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "demo.db")
        cfg.data["market_scan"]["enabled"] = False
        cfg.data["alerts"]["quiet_hours_et"] = None
        alerts = AlertManager(cfg["alerts"], db, [ConsoleChannel()])
        engine = Engine(cfg, db, SamplePrices(), [SampleNews()], alerts, MarketCalendar())
        summary = engine.cycle(with_prices=True)
        print("\nTop Move Scores (sample):")
        for r in summary["top"]:
            print(f"  {r.ticker:<6} {r.score:>5.1f}  {r.direction:<6} " +
                  " ".join(f"{k}={v:.0f}" for k, v in r.components.items()))
        db.conn.close()
