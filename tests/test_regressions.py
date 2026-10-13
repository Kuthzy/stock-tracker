"""Tests for issues found in the independent code review."""

from datetime import datetime, timedelta, timezone

import pandas as pd

from tests.test_pipeline import Capture, NoPrices, StaticNews, _result, make_engine
from tracker.alerts.manager import AlertManager
from tracker.config import Config
from tracker.market_hours import ET, MarketCalendar
from tracker.models import NewsItem, PriceSnapshot, utcnow
from tracker.outcomes import update_outcomes
from tracker.signals import catalysts
from tracker.signals.mapping import TickerMapper


def item(title, **kw):
    return NewsItem(title=title, url=kw.pop("url", title), source=kw.pop("source", "s"),
                    published=kw.pop("published", utcnow()), **kw)


def test_lookalike_headlines_about_different_companies_stay_separate(cfg, db):
    now = utcnow()
    eng = make_engine(cfg, db, [
        item("Frontline plc to report third quarter 2026 results", source="A", published=now),
        item("DHT Holdings to report third quarter 2026 results", source="B", published=now),
    ])
    eng.ingest_news(now)
    events = db.recent_events(24, now)
    assert sorted(tuple(e.tickers) for e in events) == [("DHT",), ("FRO",)]


def test_sec_filings_are_never_merged(cfg, db):
    now = utcnow()
    eng = make_engine(cfg, db, [
        item("FRO filed 4: 4 - Statement of changes", tickers=["FRO"], form_type="4", published=now),
        item("FRO filed 424B5: 424B5 - Prospectus", tickers=["FRO"], form_type="424B5", published=now),
    ])
    eng.ingest_news(now)
    events = db.recent_events(24, now)
    assert len(events) == 2 and all(e.novelty == 1.0 for e in events)
    assert {e.catalyst for e in events} == {"insider_filing", "offering"}


def test_old_story_retold_by_several_outlets_is_one_event(cfg, db):
    now = utcnow()
    old = now - timedelta(hours=20)
    eng = make_engine(cfg, db, [item("Houthi missile strike hits crude tanker in Red Sea", published=old)])
    eng.ingest_news(old)
    for i, src in enumerate(["B", "C", "D"]):
        eng.news = [StaticNews([item("Houthi missile strike hits crude tanker in Red Sea", url=f"u{i}",
                                     source=src, published=now)])]
        eng.ingest_news(now)
    retold = [e for e in db.recent_events(48, now) if e.novelty < 1]
    assert len(retold) == 1 and retold[0].source_count == 3


def test_evening_alerts_do_not_count_toward_next_day(cfg, db):
    am = AlertManager(dict(cfg["alerts"], quiet_hours_et=None, max_per_ticker_per_day=1), db, [Capture()])
    evening = datetime(2026, 10, 12, 21, 0, tzinfo=ET)        # 01:00 UTC on the 13th
    morning = datetime(2026, 10, 13, 9, 40, tzinfo=ET)
    assert len(am.process([_result(70, "FRO")], evening)) == 1
    assert len(am.process([_result(70, "FRO")], morning)) == 1


def test_offering_regex_false_positives():
    def cat(t):
        return catalysts.classify(item(t)).catalyst
    assert cat("Stocks rise at the market open as oil jumps") != "offering"
    assert cat("Acme secures non-dilutive financing") != "offering"
    assert cat("Acme launches $100 million at-the-market offering program") == "offering"


def test_sec_form_types():
    def cat(form):
        return catalysts.classify(item("x", form_type=form)).catalyst
    assert cat("4") == "insider_filing" and cat("4/A") == "insider_filing"
    assert cat("40-F") != "insider_filing" and cat("485BPOS") != "insider_filing"
    assert cat("SCHEDULE 13D") == "activist_stake"


def test_mapping_false_positives(cfg):
    m = TickerMapper(cfg.base_universe() + ["RTX", "NOC", "CVX"], {"CVX": ["Chevron"]}, {})
    assert "RTX" not in m.tickers_in("Nvidia RTX 5090 review")
    assert "NOC" not in m.tickers_in("Libya's NOC says output restored")
    assert "CVX" not in m.tickers_in("a chevron pattern on the chart")
    assert "CVX" in m.tickers_in("Chevron raises dividend")


def test_ticker_feed_accepts_short_company_name(cfg):
    m = TickerMapper(cfg.base_universe(), cfg["aliases"], cfg.themes)
    it = m.apply(item("Frontline shares jump on rate spike", hint_tickers=["FRO"]))
    assert "FRO" in it.tickers
    assert "FRO" not in m.apply(item("Frontline workers strike in hospitals")).tickers


def test_env_comment_values_are_unset(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "# from @BotFather")
    monkeypatch.setenv("EMPTY_ONE", "   ")
    assert Config.env("TELEGRAM_BOT_TOKEN") is None
    assert Config.env("EMPTY_ONE", "d") == "d"


class Boom:
    name = "boom"

    def send(self, text):
        raise RuntimeError("plug-in bug")


class Fails:
    name = "telegram"

    def __init__(self):
        self.calls = 0

    def send(self, text):
        self.calls += 1
        return False


def test_broken_channel_does_not_stop_others(cfg, db):
    good = Capture()
    am = AlertManager(dict(cfg["alerts"], quiet_hours_et=None), db, [Boom(), good])
    assert len(am.process([_result(70)], utcnow())) == 1 and len(good.sent) == 1


def test_undelivered_alert_is_retried_then_recorded(cfg, db):
    ch = Fails()
    am = AlertManager(dict(cfg["alerts"], quiet_hours_et=None), db, [ch])
    t = utcnow()
    assert am.process([_result(70)], t) == []
    assert am.process([_result(70)], t + timedelta(minutes=2)) == []
    assert len(am.process([_result(70)], t + timedelta(minutes=4))) == 1   # recorded after 3 tries
    assert ch.calls == 3


class FlakyPrices(NoPrices):
    def __init__(self):
        self.ok = True

    def snapshots(self, tickers):
        if not self.ok:
            raise ConnectionError("down")
        return {"BWET": PriceSnapshot("BWET", 11, 10, 10, 11, 10, 100, 100, 12, 9, 1, 12, 8, 1.0)}


def test_failed_price_fetch_clears_old_snapshots(cfg, db):
    eng = make_engine(cfg, db, [])
    eng.prices = FlakyPrices()
    eng.refresh_prices()
    assert "BWET" in eng.snapshots
    eng.prices.ok = False
    assert eng.refresh_prices() == 0 and eng.snapshots == {}


def test_naive_published_time_from_plugin_is_accepted(cfg, db):
    eng = make_engine(cfg, db, [NewsItem("Frontline plc fleet update", "u", "s", datetime(2026, 10, 13, 12, 0))])
    assert eng.ingest_news(datetime(2026, 10, 13, 13, 0, tzinfo=timezone.utc)) == 1


def test_latest_scores_only_current_cycle(cfg, db):
    t0 = utcnow()
    old, new = _result(50, "OLD"), _result(40, "NEW")
    old.asof, new.asof = t0 - timedelta(hours=1), t0
    db.save_scores([old])
    db.save_scores([new])
    assert [r["ticker"] for r in db.latest_scores()] == ["NEW"]


class HistPrices(NoPrices):
    def history(self, ticker, period="6mo"):
        idx = pd.to_datetime(["2026-10-13", "2026-10-14"])
        return pd.DataFrame({"Close": [10.0, 11.0]}, index=idx)


def test_premarket_alert_plus_one_day_is_same_day_close(cfg, db):
    am = AlertManager(dict(cfg["alerts"], quiet_hours_et=None), db, [Capture()])
    r = _result(70)
    r.snapshot = PriceSnapshot("BWET", 9, 9, 9, 9, 9, 0, 1, 10, 8, 1, 10, 8, 0.0)
    am.process([r], datetime(2026, 10, 13, 8, 0, tzinfo=ET))
    update_outcomes(db, HistPrices(), MarketCalendar(), {}, now=datetime(2026, 10, 15, 12, 0, tzinfo=ET))
    assert db.all_alerts()[0]["price_1d"] == 10.0


def test_daily_job_retries_when_delivery_fails(cfg, db, monkeypatch):
    from tracker.service import Service

    cfg.data["market_scan"]["enabled"] = False
    ch = Fails()
    eng = make_engine(cfg, db, [], channel=ch)
    svc = Service(cfg, eng)
    monkeypatch.setattr("tracker.service.now_et", lambda: datetime(2026, 10, 13, 9, 0, tzinfo=ET))
    svc.daily_jobs()
    assert db.kv_get("job_brief") != "2026-10-13"      # not marked done, will retry
