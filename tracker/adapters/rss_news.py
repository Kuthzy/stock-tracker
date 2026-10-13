"""Free news from RSS: Yahoo Finance per ticker, Google News per theme, and general wires."""

from __future__ import annotations

import calendar as _cal
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlparse

import feedparser

from ..models import NewsItem, utcnow
from ..plugins import register
from . import http
from .base import NewsAdapter

log = logging.getLogger(__name__)

YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={t}&region=US&lang=en-US"
GOOGLE = "https://news.google.com/rss/search?q={q}+when:1d&hl=en-US&gl=US&ceid=US:en"
TAG_RE = re.compile(r"<[^>]+>")


def _published(entry) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime.fromtimestamp(_cal.timegm(value), tz=timezone.utc)
    return utcnow()


def parse_feed(body: bytes, default_source: str, hint_tickers: list[str] | None = None,
               max_age_hours: float = 48) -> list[NewsItem]:
    feed = feedparser.parse(body)
    cutoff = utcnow() - timedelta(hours=max_age_hours)
    items = []
    for e in feed.entries:
        title = (e.get("title") or "").strip()
        if not title:
            continue
        source = default_source
        if e.get("source") and e.source.get("title"):
            source = e.source["title"]
            title = re.sub(rf"\s+-\s+{re.escape(source)}$", "", title)
        published = _published(e)
        if published < cutoff:
            continue
        summary = TAG_RE.sub(" ", e.get("summary") or "")
        items.append(NewsItem(
            title=title,
            url=e.get("link") or "",
            source=source,
            published=published,
            summary=re.sub(r"\s+", " ", summary).strip()[:600],
            hint_tickers=list(hint_tickers or []),
        ))
    return items


@register("news", "rss")
class RSSNews(NewsAdapter):
    name = "rss"

    def __init__(self, settings: dict):
        self.settings = settings

    @classmethod
    def from_config(cls, cfg):
        return cls(cfg["sources"].get("rss", {}))

    def _jobs(self, tickers: list[str], themes: dict) -> list[tuple[str, str, list[str]]]:
        jobs = []
        if self.settings.get("per_ticker_yahoo", True):
            for t in tickers:
                if "=" in t or t.startswith("^"):
                    continue
                jobs.append((YAHOO.format(t=t), "Yahoo Finance", [t]))
        if self.settings.get("google_news_per_theme", True):
            for name, theme in themes.items():
                kws = theme.get("keywords", [])[:6]
                if kws:
                    query = " OR ".join(f'"{k}"' if " " in k else k for k in kws)
                    jobs.append((GOOGLE.format(q=quote_plus(query)), "Google News", []))
        for url in self.settings.get("feeds", []):
            jobs.append((url, urlparse(url).netloc.replace("www.", ""), []))
        return jobs

    def _one(self, job) -> list[NewsItem]:
        url, source, hints = job
        body = http.get(url, timeout=12, retries=2)
        if not body:
            return []
        try:
            return parse_feed(body, source, hints)
        except Exception as exc:
            log.debug("bad feed %s: %s", url, exc)
            return []

    def fetch(self, tickers: list[str], themes: dict) -> list[NewsItem]:
        jobs = self._jobs(tickers, themes)
        items: list[NewsItem] = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            for result in pool.map(self._one, jobs):
                items.extend(result)
        log.info("rss: %d items from %d feeds", len(items), len(jobs))
        return items
