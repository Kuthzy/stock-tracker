"""Company news from Finnhub (free tier: 60 requests/minute). Needs FINNHUB_API_KEY."""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone

from ..models import NewsItem
from ..plugins import register
from . import http
from .base import NewsAdapter

log = logging.getLogger(__name__)
URL = "https://finnhub.io/api/v1/company-news"


@register("news", "finnhub")
class FinnhubNews(NewsAdapter):
    name = "finnhub"

    def __init__(self, api_key: str, min_interval: float = 1.1):
        self.api_key = api_key
        self.min_interval = min_interval  # stay under the free-tier rate limit

    @classmethod
    def from_config(cls, cfg):
        key = cfg.env("FINNHUB_API_KEY")
        return cls(key) if key else None  # quietly off without a key

    def fetch(self, tickers: list[str], themes: dict) -> list[NewsItem]:
        today = date.today()
        params = {"from": (today - timedelta(days=2)).isoformat(), "to": today.isoformat(),
                  "token": self.api_key}
        items = []
        for t in tickers:
            if "=" in t:
                continue
            body = http.get(URL, params={**params, "symbol": t}, retries=2)
            time.sleep(self.min_interval)
            if not body:
                continue
            try:
                rows = json.loads(body)
            except ValueError:
                continue
            for r in rows[:20]:
                items.append(NewsItem(
                    title=r.get("headline", ""),
                    url=r.get("url", ""),
                    source=r.get("source") or "Finnhub",
                    published=datetime.fromtimestamp(r.get("datetime", 0), tz=timezone.utc),
                    summary=(r.get("summary") or "")[:600],
                    hint_tickers=[t],
                ))
        log.info("finnhub: %d items", len(items))
        return items
