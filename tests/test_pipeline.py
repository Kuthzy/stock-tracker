import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from tracker.adapters.base import NewsAdapter, PriceAdapter
from tracker.adapters.rss_news import parse_feed
from tracker.adapters.yfinance_prices import snapshot_from_daily
from tracker.alerts.manager import AlertManager
from tracker.alerts.telegram_commands import TelegramCommands
from tracker.engine import Engine
from tracker.market_hours import ET, MarketCalendar, in_window, parse_hhmm
from tracker.models import NewsItem, ScoreResult, utcnow


class Capture:
    name = "capture"

    def __init__(self):
        self.sent = []

    def configured(self):
        return True

    def send(self, text):
        self.sent.append(text)
        return True


class StaticNews(NewsAdapter):
    def __init__(self, items):
        self.items = items

    def fetch(self, tickers, themes):
        return list(self.items)


class NoPrices(PriceAdapter):
    def snapshots(self, tickers):
        return {}

    def history(self, ticker, period="6mo"):
        return pd.DataFrame()


def make_engine(cfg, db, items, channel=None):
    am = AlertManager(cfg["alerts"], db, [channel or Capture()])
    return Engine(cfg, db, NoPrices(), [StaticNews(items)], am, MarketCalendar())


def test_engine_groups_outlets_and_skips_seen(cfg, db):
    now = utcnow()
    items = [
        NewsItem("Houthi missile strike hits crude tanker in Red Sea", "u1", "Wire A", now),
        NewsItem("Red Sea: Houthi missile strike hits crude tanker", "u2", "Wire B", now),
        NewsItem("Local bakery opens new store", "u3", "Wire C", now),
    ]
    eng = make_engine(cfg, db, items)
    eng.ingest_news(now)
    events = db.recent_events(24, now)
    assert len(events) == 1 and events[0].source_count == 2
    assert eng.ingest_news(now) == 0  # everything already seen


def test_rehashed_story_gets_low_novelty(cfg, db):
    now = utcnow()
    eng = make_engine(cfg, db, [NewsItem("Houthi missile strike hits crude tanker in Red Sea", "u1", "A",
                                         now - timedelta(hours=20))])
    eng.ingest_news(now - timedelta(hours=20))
    eng.news = [StaticNews([NewsItem("Houthi missile strike hits crude tanker in Red Sea again", "u9", "B", now)])]
    eng.ingest_news(now)
    newest = db.recent_events(48, now)[-1]
    assert newest.novelty < 0.5


def _result(score, ticker="BWET"):
    return ScoreResult(ticker=ticker, score=score, components={"news": score}, direction="up", reasons=[])


def test_alert_cooldown_tier_climb_and_quiet_hours(cfg, db):
    settings = dict(cfg["alerts"], quiet_hours_et=None)
    am = AlertManager(settings, db, [Capture()])
    t0 = datetime(2026, 10, 13, 14, 0, tzinfo=ET)
    assert len(am.process([_result(62)], t0)) == 1
    assert am.process([_result(63)], t0 + timedelta(minutes=5)) == []          # cooldown
    assert len(am.process([_result(80)], t0 + timedelta(minutes=10))) == 1      # climbed to HOT
    am.s = dict(settings, quiet_hours_et=["13:00", "23:00"])
    assert am.process([_result(70, "FRO")], t0) == []                          # WATCH muted at night
    assert len(am.process([_result(95, "DHT")], t0)) == 1                      # URGENT still goes


def test_daily_cap(cfg, db):
    am = AlertManager(dict(cfg["alerts"], quiet_hours_et=None, max_per_day=2), db, [Capture()])
    t0 = datetime(2026, 10, 13, 14, 0, tzinfo=ET)
    sent = am.process([_result(70, t) for t in ("A", "B", "C")], t0)
    assert len(sent) == 2


def _bars(n=60, last_date=None):
    idx = pd.bdate_range(end=last_date or "2026-10-12", periods=n)
    close = np.linspace(20, 24, n)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close,
                         "Volume": np.full(n, 1000.0)}, index=idx)


def test_snapshot_before_open_uses_last_close():
    cal = MarketCalendar()
    snap = snapshot_from_daily("X", _bars(last_date="2026-10-12"), cal,
                               now=datetime(2026, 10, 13, 8, 0, tzinfo=ET))
    assert snap.price == snap.prev_close and snap.volume == 0


def test_snapshot_intraday_rvol():
    cal = MarketCalendar()
    df = _bars(last_date="2026-10-13")
    df.iloc[-1, df.columns.get_loc("Volume")] = 1000.0
    df.iloc[-1, df.columns.get_loc("Close")] = 26.0
    snap = snapshot_from_daily("X", df, cal, now=datetime(2026, 10, 13, 12, 45, tzinfo=ET))
    assert 0.49 < snap.session_fraction < 0.51
    assert 1.9 < snap.rvol < 2.1
    assert snap.pct_change > 5


def test_market_sessions_and_windows():
    cal = MarketCalendar(["2026-11-26"])
    assert cal.session(datetime(2026, 10, 13, 10, 0, tzinfo=ET)) == "regular"
    assert cal.session(datetime(2026, 10, 13, 5, 0, tzinfo=ET)) == "pre"
    assert cal.session(datetime(2026, 10, 17, 10, 0, tzinfo=ET)) == "closed"   # Saturday
    assert cal.session(datetime(2026, 11, 26, 10, 0, tzinfo=ET)) == "closed"   # Thanksgiving
    assert in_window(parse_hhmm("23:30"), ["22:00", "07:00"])
    assert not in_window(parse_hhmm("12:00"), ["22:00", "07:00"])


def test_rss_parsing_strips_outlet_suffix():
    body = f"""<?xml version="1.0"?><rss><channel><item>
      <title>Tanker rates soar - Example News</title><link>https://x/1</link>
      <pubDate>{utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate>
      <source url="https://x">Example News</source><description>&lt;b&gt;Rates&lt;/b&gt; up</description>
    </item></channel></rss>""".encode()
    items = parse_feed(body, "Google News")
    assert items[0].title == "Tanker rates soar" and items[0].source == "Example News"
    assert items[0].summary == "Rates up"


def test_telegram_commands(db):
    tc = TelegramCommands("t", "1", db, lambda: ["BWET"])
    assert "Added NVDA" in tc.handle("/watch nvda")
    assert db.overrides() == {"NVDA": "add"}
    assert "Usage" in tc.handle("/watch")
    assert "Muted" in tc.handle("/mute 2h") and db.kv_get("mute_until")
    assert "No score" in tc.handle("/score XYZ")


def test_claude_classifier_parses_structured_output(cfg):
    from tracker.signals.sentiment import ClaudeClassifier

    class Block:
        type = "text"
        text = json.dumps({"items": [{"index": 0, "catalyst": "geopolitical_supply", "strength": 0.9,
                                      "sentiment": 0.7, "tickers": ["FRO", "ZZZZ"], "reason": "Rates spike"}]})

    class Resp:
        stop_reason = "end_turn"
        content = [Block()]

    class FakeMessages:
        def create(self, **kw):
            assert kw["output_config"]["format"]["type"] == "json_schema"
            return Resp()

    clf = ClaudeClassifier.__new__(ClaudeClassifier)
    clf.client = type("C", (), {"beta": type("B", (), {"messages": FakeMessages()})()})()
    clf.model, clf.batch_size = "claude-opus-5-5", 25
    it = NewsItem("Tanker hit", "u", "s", utcnow())
    clf.classify([it], ["FRO", "BWET"])
    assert it.catalyst == "geopolitical_supply" and it.tickers == ["FRO"] and it.reason == "Rates spike"


def test_export_excel(cfg, db, tmp_path):
    from openpyxl import load_workbook

    from tracker.export import export_excel

    am = AlertManager(dict(cfg["alerts"], quiet_hours_et=None), db, [Capture()])
    db.save_scores([_result(70)])
    am.process([_result(70)], utcnow())
    out = export_excel(db, tmp_path / "r.xlsx")
    wb = load_workbook(out)
    assert wb.sheetnames == ["Summary", "Scores", "Alerts", "News"]
    assert wb["Alerts"].max_row == 2
