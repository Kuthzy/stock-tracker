"""Loads config.yaml plus secrets from .env."""

from __future__ import annotations

import copy
import logging
import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger(__name__)
_WARNED: set[str] = set()

DEFAULTS: dict = {
    "watchlist": [],
    "aliases": {},
    "themes": {},
    "market_scan": {"enabled": False, "screens": ["day_gainers"], "max_tickers": 20, "run_at_et": "09:45"},
    "plugins_dir": "plugins",
    "sources": {
        "prices": "yfinance",
        "news": ["rss", "finnhub", "sec_edgar"],
        "rss": {"per_ticker_yahoo": True, "google_news_per_theme": True, "feeds": []},
    },
    "llm": {"enabled": "auto", "model": "claude-opus-5-5", "max_headlines_per_call": 25},
    "schedule": {
        "regular_interval": 120,
        "extended_interval": 300,
        "closed_interval": 900,
        "closed_price_interval": 3600,
        "premarket_brief_et": "08:30",
        "close_summary_et": "16:10",
        "holidays": [],
    },
    "scoring": {
        "signals": {"news": 30, "price": 25, "volume": 25, "volatility": 10, "theme": 10},
        "news_lookback_hours": 24,
        "news_half_life_hours": 6,
        "theme_only_weight": 0.6,
        "big_move_pct": 8,
        "big_gap_pct": 5,
        "big_rvol": 5,
    },
    "alerts": {
        "watch": 60,
        "hot": 75,
        "urgent": 90,
        "cooldown_minutes": 45,
        "max_per_ticker_per_day": 4,
        "max_per_day": 60,
        "quiet_hours_et": None,
        "channels": ["console"],
    },
    "storage": {"db_path": "data/tracker.db", "log_dir": "data/logs"},
    "health": {"port": 8080, "stale_after_minutes": 60},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    def __init__(self, data: dict):
        self.data = _merge(DEFAULTS, data)
        # The signal list is taken as written (not merged), so deleting a line switches it off.
        user_signals = ((data or {}).get("scoring") or {}).get("signals")
        if user_signals:
            self.data["scoring"]["signals"] = dict(user_signals)
        self.data["watchlist"] = [t.upper() for t in self.data["watchlist"]]
        for theme in self.data["themes"].values():
            theme["tickers"] = [t.upper() for t in theme.get("tickers", [])]
            theme.setdefault("keywords", [])
            theme.setdefault("context_symbols", [])
            theme.setdefault("shock_direction", "neutral")

    def __getitem__(self, key):
        return self.data[key]

    def get(self, key, default=None):
        return self.data.get(key, default)

    @property
    def themes(self) -> dict:
        return self.data["themes"]

    def base_universe(self) -> list[str]:
        """Watchlist plus every theme ticker, de-duplicated, order kept."""
        seen: dict[str, None] = {}
        for t in self.data["watchlist"]:
            seen[t] = None
        for theme in self.themes.values():
            for t in theme["tickers"]:
                seen[t] = None
        return list(seen)

    def context_symbols(self) -> list[str]:
        syms: dict[str, None] = {}
        for theme in self.themes.values():
            for s in theme["context_symbols"]:
                syms[s] = None
        return list(syms)

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else ROOT / p

    @staticmethod
    def env(name: str, default: str | None = None) -> str | None:
        """A setting from .env / the environment. Empty values and leftover '# comments' count as unset.
        Only '#' followed by a space (or a lone '#') is a comment, so a password like '#Pa55' still works."""
        value = os.environ.get(name)
        if isinstance(value, str):
            value = value.strip()
            if value == "#" or value.startswith("# "):
                if name not in _WARNED:
                    _WARNED.add(name)
                    log.warning("%s looks like a leftover comment in .env; treating it as not set", name)
                return default
            if value:
                return value
        return default

    def llm_enabled(self) -> bool:
        setting = self.data["llm"].get("enabled", "auto")
        if setting == "auto":
            return bool(self.env("ANTHROPIC_API_KEY"))
        if isinstance(setting, str):  # "yes"/"on" stay strings with the config loader
            return setting.strip().lower() in ("true", "yes", "on", "1")
        return bool(setting)


class _ConfigLoader(yaml.SafeLoader):
    """Like safe_load, but only true/false are booleans. Plain YAML turns ON (ON Semiconductor),
    YES or NO (tickers) into True/False, which would silently drop them from the watchlist."""


_ConfigLoader.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_ConfigLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def load_config(path: str | os.PathLike | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    data = {}
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as fh:
            data = yaml.load(fh, Loader=_ConfigLoader) or {}
    return Config(data)
