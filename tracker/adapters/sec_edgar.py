"""SEC EDGAR filings per ticker (8-K, Form 4, S-1/S-3, 424B, 13D/G)."""

from __future__ import annotations

import logging
import time
from datetime import timedelta

import feedparser

from ..models import NewsItem, utcnow
from ..plugins import register
from . import http
from .base import NewsAdapter
from .rss_news import _published

log = logging.getLogger(__name__)

URL = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={t}"
       "&type=&dateb=&owner=include&count=20&output=atom")
PREFIXES = ("8-K", "S-1", "S-3", "424B", "SC 13D", "SC 13G", "SCHEDULE 13D", "SCHEDULE 13G", "6-K")


def interesting(form: str) -> bool:
    form = form.strip().upper()
    return form.split("/")[0] == "4" or form.startswith(PREFIXES)


@register("news", "sec_edgar")
class SecEdgar(NewsAdapter):
    name = "sec"

    def __init__(self, user_agent: str):
        # SEC requires a descriptive User-Agent with contact info and max 10 requests/second.
        self.headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}

    @classmethod
    def from_config(cls, cfg):
        return cls(cfg.env("SEC_USER_AGENT", "StockTrackerBot contact@example.com"))

    def fetch(self, tickers: list[str], themes: dict) -> list[NewsItem]:
        cutoff = utcnow() - timedelta(hours=48)
        items = []
        for t in tickers:
            if "=" in t:
                continue
            body = http.get(URL.format(t=t), headers=self.headers, retries=2, timeout=12)
            time.sleep(0.15)
            if not body:
                continue
            for e in feedparser.parse(body).entries:
                form = (e.get("category") or (e.get("tags") or [{}])[0].get("term") or "").strip()
                if not interesting(form):
                    continue
                published = _published(e)
                if published < cutoff:
                    continue
                items.append(NewsItem(
                    title=f"{t} filed {form}: {e.get('title', '').strip()}",
                    url=e.get("link", ""),
                    source="SEC EDGAR",
                    published=published,
                    summary=(e.get("summary") or "")[:300],
                    hint_tickers=[t],
                    tickers=[t],
                    form_type=form,
                ))
        log.info("sec: %d recent filings", len(items))
        return items
