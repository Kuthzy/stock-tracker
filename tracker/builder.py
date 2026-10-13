"""Wires everything together from config.yaml, using the plug-in registry."""

from __future__ import annotations

import logging

from . import plugins
from .alerts.channels import build_channels
from .alerts.manager import AlertManager
from .config import Config
from .engine import Engine
from .market_hours import MarketCalendar
from .storage import Database

log = logging.getLogger(__name__)


def build_engine(cfg: Config, db: Database | None = None, channels=None) -> Engine:
    plugins.load_builtins()
    plugins.load_plugin_dir(cfg.path(cfg.get("plugins_dir", "plugins")))
    calendar = MarketCalendar(cfg["schedule"]["holidays"])
    db = db or Database(cfg.path(cfg["storage"]["db_path"]))
    src = cfg["sources"]

    prices = plugins.build("prices", [src["prices"]], cfg)
    if not prices:
        log.warning("price source %r unavailable, falling back to yfinance", src["prices"])
        prices = plugins.build("prices", ["yfinance"], cfg)
    news = plugins.build("news", src["news"], cfg)
    log.info("news sources active: %s", ", ".join(getattr(n, "name", "?") for n in news) or "none")

    classifier = None
    if cfg.llm_enabled():
        try:
            from .signals.sentiment import ClaudeClassifier

            classifier = ClaudeClassifier(cfg["llm"]["model"], cfg["llm"]["max_headlines_per_call"])
            log.info("AI headline classification on (%s)", cfg["llm"]["model"])
        except Exception as exc:
            log.warning("AI classification unavailable: %s", exc)
    alerts = AlertManager(cfg["alerts"], db, channels if channels is not None else build_channels(cfg))
    return Engine(cfg, db, prices[0], news, alerts, calendar, classifier)
