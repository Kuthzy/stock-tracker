"""Tests for issues found when double-checking the review fixes."""

from datetime import datetime, timedelta


from tests.test_pipeline import Capture, NoPrices, _result, make_engine
from tests.test_regressions import item
from tracker.alerts.manager import AlertManager
from tracker.config import Config, load_config
from tracker.market_hours import ET, MarketCalendar
from tracker.models import NewsItem, utcnow
from tracker.outcomes import update_outcomes
from tracker.signals import scoring
from tracker.signals.mapping import TickerMapper

ALIASES = {"NVDA": ["Nvidia"], "FRO": ["Frontline shares"], "CVX": ["Chevron"],
           "GD": ["General Dynamics"], "RTX": ["Raytheon"]}


def mapper():
    return TickerMapper(["NVDA", "FRO", "CVX", "GD", "RTX"], ALIASES, {})


def test_aliases_match_any_case_but_need_a_capital():
    m = mapper()
    assert m.tickers_in("NVIDIA beats estimates") == {"NVDA"}
    assert m.tickers_in("Frontline Shares rally") == {"FRO"}
    assert m.tickers_in("a chevron pattern on the chart") == set()


def test_rtx_graphics_cards_are_not_raytheon():
    m = mapper()
    for text in ["RTX 5090 review", "RTX Pro 6000 ships", "new RTX GPU lineup", "RTX Super refresh"]:
        assert "RTX" not in m.tickers_in(text), text
    assert "RTX" in m.tickers_in("RTX wins Army missile contract")


def test_generic_first_word_is_not_a_loose_alias():
    m = mapper()
    gm = m.apply(NewsItem("General Motors recalls cars", "u", "s", utcnow(), hint_tickers=["GD"]))
    assert gm.tickers == []
    nv = m.apply(NewsItem("Nvidia unveils chip", "u2", "s", utcnow(), hint_tickers=["NVDA"]))
    assert nv.tickers == ["NVDA"]


class StringReason(scoring.Signal):
    def compute(self, ctx):
        return 5, "one reason as a plain string"


class NoneReason(scoring.Signal):
    def compute(self, ctx):
        return 5, None


def test_signal_reasons_can_be_a_string_or_none():
    sigs = [StringReason(10), NoneReason(10)]
    r = scoring.score_ticker("BWET", [], None, {}, {}, utcnow(), signals=sigs)
    assert r.reasons == ["one reason as a plain string"]
    assert r.score == 50


def test_plugin_items_with_missing_fields_are_cleaned(cfg, db):
    now = utcnow()
    bad = NewsItem("Frontline plc fleet update", "u", "s", now)
    bad.summary, bad.tickers, bad.hint_tickers = None, "FRO", None
    eng = make_engine(cfg, db, [bad])
    assert eng.ingest_news(now) == 1
    assert [e.tickers for e in db.recent_events(24, now)] == [["FRO"]]


def test_one_bad_item_does_not_drop_the_others(cfg, db, monkeypatch):
    from tracker.signals import sentiment

    real = sentiment.score_sentiment

    def flaky(it):
        if "boom" in it.title:
            raise ValueError("bad item")
        return real(it)

    monkeypatch.setattr(sentiment, "score_sentiment", flaky)
    now = utcnow()
    eng = make_engine(cfg, db, [item("Frontline plc boom headline", published=now),
                                item("DHT Holdings orders new tankers", published=now)])
    eng.ingest_news(now)
    assert [e.tickers for e in db.recent_events(24, now)] == [["DHT"]]


def test_burst_of_same_filing_is_one_story(cfg, db):
    now = utcnow()
    eng = make_engine(cfg, db, [
        item(f"FRO filed 4: insider {i}", url=f"sec{i}", tickers=["FRO"], form_type="4", published=now)
        for i in range(5)
    ])
    eng.ingest_news(now)
    events = db.recent_events(24, now)
    assert len(events) == 1 and events[0].source_count == 5


def test_cooldown_holds_across_midnight(cfg, db):
    am = AlertManager(dict(cfg["alerts"], quiet_hours_et=None), db, [Capture()])
    late = datetime(2026, 10, 13, 23, 50, tzinfo=ET)
    assert len(am.process([_result(62)], late)) == 1
    assert am.process([_result(63)], late + timedelta(minutes=20)) == []        # next day, still cooling down
    assert len(am.process([_result(80)], late + timedelta(minutes=25))) == 1    # a higher tier gets through


def test_yaml_yes_no_on_stay_tickers(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("watchlist: [ON, YES, NO, BWET]\nmarket_scan:\n  enabled: true\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg["watchlist"] == ["ON", "YES", "NO", "BWET"]
    assert cfg["market_scan"]["enabled"] is True


def test_env_only_treats_hash_space_as_comment(monkeypatch):
    monkeypatch.setenv("SMTP_PASSWORD", "#Pa55word")
    monkeypatch.setenv("NTFY_TOPIC", "# put your topic here")
    assert Config.env("SMTP_PASSWORD") == "#Pa55word"
    assert Config.env("NTFY_TOPIC") is None


class BrokenHistory(NoPrices):
    def history(self, ticker, period="6mo"):
        raise ConnectionError("down")


def test_outcomes_survive_a_broken_price_source(cfg, db):
    am = AlertManager(dict(cfg["alerts"], quiet_hours_et=None), db, [Capture()])
    am.process([_result(70)], datetime(2026, 10, 13, 10, 0, tzinfo=ET))
    filled = update_outcomes(db, BrokenHistory(), MarketCalendar(), {},
                             now=datetime(2026, 10, 15, 12, 0, tzinfo=ET))
    assert filled == 0


def test_tick_runs_daily_jobs_even_if_outcomes_fail(cfg, db, monkeypatch):
    from tracker import service

    cfg.data["market_scan"]["enabled"] = False
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(key, raising=False)
    ran = []
    monkeypatch.setattr(service, "update_outcomes", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(service.Service, "daily_jobs", lambda self: ran.append(True))
    eng = make_engine(cfg, db, [])
    eng.cycle = lambda with_prices=True: {"news": 0, "prices": 0, "alerts": 0}
    service.run_tick(cfg, eng)
    assert ran == [True]
