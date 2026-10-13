from datetime import timedelta

from tracker.models import NewsEvent, NewsItem, PriceSnapshot, utcnow
from tracker.signals import catalysts, sentiment
from tracker.signals.dedup import SAME_STORY, similarity, tokens
from tracker.signals.mapping import TickerMapper
from tracker.signals.scoring import score_ticker, signed_sentiment


def item(title, **kw):
    return NewsItem(title=title, url="u", source="s", published=utcnow(), **kw)


def test_mapping_cashtags_exchange_aliases_and_themes(cfg):
    m = TickerMapper(cfg.base_universe(), cfg["aliases"], cfg.themes)
    it = m.apply(item("Frontline (NYSE: FRO) and $DHT rally as Houthi attacks push tanker rates up"))
    assert it.tickers == ["DHT", "FRO"]
    assert "tanker_shipping" in it.themes


def test_mapping_ignores_lookalike_words(cfg):
    m = TickerMapper(cfg.base_universe() + ["CAT"], {}, {})
    assert m.tickers_in("The CEO said ALL options are open, FDA and SEC agree") == set()


def test_catalyst_rules_and_sec_forms():
    assert catalysts.classify(item("FDA approves Acme's lead drug")).catalyst == "fda_approval"
    off = catalysts.classify(item("Acme announces $50 million public offering"))
    assert off.catalyst == "offering" and off.catalyst_bias < 0
    sec = catalysts.classify(item("ACME filed 424B5", form_type="424B5"))
    assert sec.catalyst == "offering"
    assert catalysts.classify(item("Weather is nice")).catalyst == "general"


def test_sentiment_finance_words():
    up = sentiment.score_sentiment(catalysts.classify(item("Acme beats estimates, shares surge")))
    down = sentiment.score_sentiment(catalysts.classify(item("Acme misses, shares plunge after downgrade")))
    assert up.sentiment > 0.3 and down.sentiment < -0.3


def test_dedup_same_story_different_outlets():
    a = tokens("Houthi missile strike hits crude tanker in Red Sea - Reuters")
    b = tokens("Red Sea: Houthi missile strike hits crude tanker")
    c = tokens("Nvidia unveils new AI chip")
    assert similarity(a, b) >= SAME_STORY
    assert similarity(a, c) < SAME_STORY


def _event(**kw):
    base = dict(event_id="e", title="t", url="u", first_seen=utcnow(), tickers=[], themes=["tanker_shipping"],
                catalyst="geopolitical_supply", catalyst_strength=0.9, sentiment=-0.6, novelty=1.0,
                source_count=3, sources=["a", "b", "c"])
    base.update(kw)
    return NewsEvent(**base)


def _snap(**kw):
    base = dict(ticker="BWET", price=100, prev_close=100, open=100, day_high=101, day_low=99, volume=100,
                avg_volume_20=100, high_20=110, low_20=90, atr_14=2, bb_upper=108, bb_lower=92,
                session_fraction=1.0)
    base.update(kw)
    return PriceSnapshot(**base)


def test_supply_shock_reads_bullish_for_tankers_bearish_for_chips(cfg):
    ev = _event()
    assert signed_sentiment(ev, ["tanker_shipping"], cfg.themes) > 0
    ev2 = _event(themes=["semiconductors"])
    assert signed_sentiment(ev2, ["semiconductors"], cfg.themes) < 0


def test_score_components(cfg):
    sc, now = cfg["scoring"], utcnow()
    quiet = score_ticker("BWET", [], _snap(), cfg.themes, sc, now)
    assert quiet.score < 5 and quiet.tier(cfg["alerts"]) is None
    big = score_ticker("BWET", [_event()], _snap(price=110, open=106, day_high=111, day_low=104, volume=500, high_20=108),
                       cfg.themes, sc, now)
    assert big.components["price"] == sc["signals"]["price"]
    assert big.components["volume"] == sc["signals"]["volume"]
    assert big.components["news"] > 0 and big.direction == "up"
    assert big.score >= cfg["alerts"]["watch"]


def test_news_decays_with_age(cfg):
    sc, now = cfg["scoring"], utcnow()
    fresh = score_ticker("FRO", [_event()], None, cfg.themes, sc, now).components["news"]
    old = score_ticker("FRO", [_event(first_seen=now - timedelta(hours=12))], None, cfg.themes, sc, now)
    assert old.components["news"] < fresh / 2


def test_direct_mention_beats_theme_only(cfg):
    sc, now = cfg["scoring"], utcnow()
    theme_only = score_ticker("FRO", [_event()], None, cfg.themes, sc, now).components["news"]
    direct = score_ticker("FRO", [_event(tickers=["FRO"])], None, cfg.themes, sc, now).components["news"]
    assert direct > theme_only
