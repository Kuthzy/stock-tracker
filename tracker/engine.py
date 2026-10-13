"""One tracking cycle: collect news -> map and classify -> group stories -> prices -> score -> alert."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .adapters.base import NewsAdapter, PriceAdapter
from .alerts.manager import AlertManager
from .config import Config
from .market_hours import ET, MarketCalendar, now_et
from .models import NewsEvent, NewsItem, PriceSnapshot, ScoreResult, utcnow
from .signals import catalysts, sentiment
from .signals.dedup import SAME_STORY, similarity, tokens
from .signals.mapping import TickerMapper
from .signals.scoring import build_signals, score_ticker
from .storage import Database

log = logging.getLogger(__name__)

REHASH_AFTER_HOURS = 12   # a "new" story matching one older than this is old news retold
REHASH_NOVELTY = 0.35
SEC_GROUP_HOURS = 24      # same filing type for the same ticker within this window is one story


def _as_list(value) -> list[str]:
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)


class Engine:
    def __init__(self, cfg: Config, db: Database, prices: PriceAdapter, news: list[NewsAdapter],
                 alerts: AlertManager, calendar: MarketCalendar, classifier=None):
        self.cfg, self.db, self.prices, self.news = cfg, db, prices, news
        self.alerts, self.calendar, self.classifier = alerts, calendar, classifier
        self.snapshots: dict[str, PriceSnapshot] = {}
        self.context: dict[str, PriceSnapshot] = {}
        self.last_results: list[ScoreResult] = []
        self.signals = build_signals(cfg["scoring"], cfg)

    # --- universe -----------------------------------------------------------
    def universe(self) -> list[str]:
        tickers = dict.fromkeys(self.cfg.base_universe())
        if self.db.kv_get("scan_date") == now_et().date().isoformat():
            for t in (self.db.kv_get("scan_tickers") or "").split(","):
                if t:
                    tickers[t] = None
        for t, action in self.db.overrides().items():
            if action == "add":
                tickers[t] = None
            else:
                tickers.pop(t, None)
        return list(tickers)

    # --- news ---------------------------------------------------------------
    def collect(self, universe: list[str]) -> list[NewsItem]:
        items: dict[str, NewsItem] = {}
        for adapter in self.news:
            try:
                for it in adapter.fetch(universe, self.cfg.themes) or []:
                    if not isinstance(it, NewsItem) or not isinstance(it.published, datetime) or not it.title:
                        continue
                    # Plug-ins may leave these as None or a single string.
                    it.summary = it.summary or ""
                    it.tickers = _as_list(it.tickers)
                    it.hint_tickers = _as_list(it.hint_tickers)
                    # Plug-ins may hand back naive or non-UTC times; store everything as UTC.
                    if it.published.tzinfo is None:
                        it.published = it.published.replace(tzinfo=timezone.utc)
                    it.published = it.published.astimezone(timezone.utc)
                    items.setdefault(it.id, it)
            except Exception:
                log.exception("news source %s failed; skipping it this cycle", getattr(adapter, "name", adapter))
        fresh = self.db.unseen(list(items))
        return [items[i] for i in items if i in fresh]

    def analyze(self, items: list[NewsItem], universe: list[str]) -> list[NewsItem]:
        mapper = TickerMapper(universe, self.cfg["aliases"], self.cfg.themes)
        relevant = []
        for it in items:
            try:
                mapper.apply(it)
                if not it.tickers and not it.themes:
                    continue
                catalysts.classify(it)
                sentiment.score_sentiment(it)
            except Exception:
                log.exception("could not analyze %r; skipping it", it.title[:80])
                continue
            relevant.append(it)
        if self.classifier and relevant:
            try:
                self.classifier.classify(relevant, universe)
            except Exception:
                log.exception("AI classification failed; using rule-based results")
        return relevant

    def group(self, items: list[NewsItem], now) -> list[NewsEvent]:
        """Merge items into stories. Returns events that are new or got new sources."""
        known = self.db.recent_events(48, now)
        pool = [(tokens(e.title), e) for e in known]
        touched: dict[str, NewsEvent] = {}
        def age_h(ev: NewsEvent) -> float:
            return (now - ev.first_seen).total_seconds() / 3600

        for it in sorted(items, key=lambda x: x.published):
            toks = tokens(it.title)
            if it.form_type:
                # A burst of the same filing type (five Form 4s on one day) counts as one story.
                same = next((ev for _, ev in pool
                             if " filed " in ev.title and ev.catalyst == it.catalyst
                             and set(ev.tickers) == set(it.tickers) and age_h(ev) <= SEC_GROUP_HOURS), None)
                if same:
                    same.source_count += 1
                    touched[same.event_id] = same
                    continue
            best, best_key = None, (False, 0.0)
            # SEC filings each have their own URL and are never merged by title.
            for etoks, ev in ([] if it.form_type else pool):
                # Look-alike headlines about different companies are different stories.
                if it.tickers and ev.tickers and not set(it.tickers) & set(ev.tickers):
                    continue
                sim = similarity(toks, etoks)
                # Prefer a recent matching story over an old one, so outlets re-reporting
                # old news pile onto one "retold" event instead of each making a new one.
                key = (sim >= SAME_STORY and age_h(ev) <= REHASH_AFTER_HOURS, sim)
                if key > best_key:
                    best, best_key = ev, key
            best_sim = best_key[1]
            if best and best_sim >= SAME_STORY:
                if age_h(best) <= REHASH_AFTER_HOURS:
                    if it.source not in best.sources:
                        best.sources.append(it.source)
                        best.source_count += 1
                    best.tickers = sorted(set(best.tickers) | set(it.tickers))
                    best.themes = sorted(set(best.themes) | set(it.themes))
                    if it.catalyst_strength > best.catalyst_strength:
                        best.catalyst, best.catalyst_strength = it.catalyst, it.catalyst_strength
                    touched[best.event_id] = best
                    continue
                novelty = REHASH_NOVELTY
            else:
                novelty = 1.0
            ev = NewsEvent(
                event_id=it.id, title=it.title, url=it.url, first_seen=min(now, it.published),
                tickers=it.tickers, themes=it.themes, catalyst=it.catalyst,
                catalyst_strength=it.catalyst_strength, sentiment=it.sentiment, novelty=novelty,
                source_count=1, sources=[it.source], reason=it.reason,
            )
            pool.append((toks, ev))
            touched[ev.event_id] = ev
        for ev in touched.values():
            self.db.save_event(ev)
        return list(touched.values())

    def ingest_news(self, now=None) -> int:
        now = now or utcnow()
        universe = self.universe()
        items = self.collect(universe)
        try:
            relevant = self.analyze(items, universe)
            events = self.group(relevant, now)
        finally:
            # Mark items seen even if processing failed, so one bad item can't fail every cycle.
            self.db.mark_seen([it.id for it in items])
        log.info("news: %d new items, %d relevant, %d stories updated", len(items), len(relevant), len(events))
        return len(items)

    # --- prices and scoring -------------------------------------------------
    def refresh_prices(self) -> int:
        """Replace (not merge) snapshots, so a failed fetch never re-scores yesterday's move."""
        universe = self.universe()
        try:
            snaps = self.prices.snapshots(universe + self.cfg.context_symbols()) or {}
        except Exception:
            log.exception("price source failed; scoring on news only this cycle")
            snaps = {}
        ctx = set(self.cfg.context_symbols())
        self.snapshots = {t: s for t, s in snaps.items() if t not in ctx or t in universe}
        self.context = {t: s for t, s in snaps.items() if t in ctx}
        return len(snaps)

    def score_all(self, now=None) -> list[ScoreResult]:
        now = now or utcnow()
        sc = self.cfg["scoring"]
        themes = self.cfg.themes
        events = self.db.recent_events(sc["news_lookback_hours"], now)
        universe = self.universe()
        first = {t: score_ticker(t, events, self.snapshots.get(t), themes, sc, now, self.context,
                                 signals=self.signals).score
                 for t in universe}
        results = [score_ticker(t, events, self.snapshots.get(t), themes, sc, now, self.context, first,
                                signals=self.signals)
                   for t in universe]
        self.db.save_scores(results)
        self.last_results = results
        return results

    def cycle(self, with_prices: bool = True, now=None) -> dict:
        now = now or utcnow()
        try:
            n_news = self.ingest_news(now)
        except Exception:
            log.exception("news stage failed; continuing with prices and scoring")
            n_news = 0
        n_prices = self.refresh_prices() if with_prices else 0
        results = self.score_all(now)
        sent = self.alerts.process(results, now)
        top = sorted(results, key=lambda r: r.score, reverse=True)[:5]
        log.info("cycle done: top %s", ", ".join(f"{r.ticker}={r.score:.0f}" for r in top))
        return {"news": n_news, "prices": n_prices, "alerts": len(sent), "top": top,
                "session": self.calendar.session(now.astimezone(ET))}
