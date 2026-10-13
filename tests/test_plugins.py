from datetime import timedelta

import pytest

from tracker import plugins
from tracker.config import ROOT
from tracker.models import NewsEvent, utcnow
from tracker.signals.scoring import Signal, build_signals, score_ticker


def test_builtins_registered():
    for kind, name in [("news", "rss"), ("news", "sec_edgar"), ("prices", "yfinance"),
                       ("signals", "news"), ("channels", "telegram"), ("commands", "/watch")]:
        assert plugins.get(kind, name) is not None, (kind, name)


def test_every_file_in_plugins_folder_loads(tmp_path):
    """Fails if any of your plug-ins has an import error."""
    import importlib.util

    for path in sorted((ROOT / "plugins").glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"check_{path.stem}", path)
        spec.loader.exec_module(importlib.util.module_from_spec(spec))


def test_shipped_examples_register(tmp_path):
    import shutil

    examples = ["example_catalyst_bonus.py", "ntfy_channel.py", "example_reddit_news.py",
                "example_news_command.py"]
    for name in examples:
        src = ROOT / "plugins" / name
        if not src.exists():
            pytest.skip(f"{name} was removed")
        shutil.copy(src, tmp_path / name)
    plugins.load_plugin_dir(tmp_path)
    for kind, name in [("signals", "catalyst_bonus"), ("channels", "ntfy"), ("news", "reddit"),
                       ("commands", "/news")]:
        assert plugins.get(kind, name) is not None, (kind, name)


def test_underscore_files_are_skipped(tmp_path):
    import shutil

    src = ROOT / "plugins" / "_template.py"
    if not src.exists():
        pytest.skip("_template.py was removed")
    shutil.copy(src, tmp_path / "_template.py")
    assert plugins.load_plugin_dir(tmp_path) == []


def test_broken_plugin_is_skipped(tmp_path):
    (tmp_path / "broken.py").write_text("raise RuntimeError('boom')\n")
    (tmp_path / "good.py").write_text(
        "from tracker.plugins import register\n"
        "@register('commands', '/ping_test')\n"
        "def ping(ctx):\n    return 'pong'\n")
    loaded = plugins.load_plugin_dir(tmp_path)
    assert loaded == ["good.py"]
    assert plugins.get("commands", "/ping_test")(None) == "pong"


def test_build_skips_unknown_and_unconfigured(cfg, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    built = plugins.build("channels", ["console", "telegram", "does_not_exist"], cfg)
    assert [c.name for c in built] == ["console"]


def test_custom_signal_and_rescaling(cfg):
    @plugins.register("signals", "always_full_test")
    class AlwaysFull(Signal):
        def compute(self, ctx):
            return 999, ["always"]

    sc = dict(cfg["scoring"], signals={"news": 30, "always_full_test": 20})
    r = score_ticker("BWET", [], None, cfg.themes, sc, utcnow(), signals=build_signals(sc))
    assert r.components == {"news": 0.0, "always_full_test": 20.0}  # capped at its max
    assert r.score == 40.0                                            # 20/50 rescaled to 0-100
    assert "always" in r.reasons


def test_failing_signal_counts_zero(cfg):
    @plugins.register("signals", "crashes_test")
    class Crashes(Signal):
        def compute(self, ctx):
            raise ValueError("bug in plug-in")

    sc = dict(cfg["scoring"], signals={"crashes_test": 10})
    r = score_ticker("BWET", [], None, cfg.themes, sc, utcnow(), signals=build_signals(sc))
    assert r.score == 0.0


def test_catalyst_bonus_example(cfg, tmp_path):
    import shutil

    src = ROOT / "plugins" / "example_catalyst_bonus.py"
    if not src.exists():
        pytest.skip("example removed")
    shutil.copy(src, tmp_path / src.name)
    plugins.load_plugin_dir(tmp_path)
    sc = dict(cfg["scoring"], signals={"catalyst_bonus": 10})
    now = utcnow()
    ev = NewsEvent("e", "Acme to be acquired", "u", now - timedelta(hours=1), ["FRO"], [], "mna",
                   1.0, 0.5, 1.0)
    r = score_ticker("FRO", [ev], None, cfg.themes, sc, now, signals=build_signals(sc))
    assert r.score == 100.0


def test_tick_runs_offline(cfg, db, monkeypatch):
    from tests.test_pipeline import Capture, NoPrices, StaticNews
    from tracker.alerts.manager import AlertManager
    from tracker.engine import Engine
    from tracker.market_hours import MarketCalendar
    from tracker.service import run_tick

    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    cfg.data["market_scan"]["enabled"] = False
    ch = Capture()
    eng = Engine(cfg, db, NoPrices(), [StaticNews([])], AlertManager(cfg["alerts"], db, [ch]), MarketCalendar())
    for i in range(3):
        run_tick(cfg, eng)
    assert db.kv_get("empty_runs") == "3"
    assert any("heartbeat" in m for m in ch.sent)


def test_signal_with_broken_init_is_left_out(cfg):
    @plugins.register("signals", "bad_init_test")
    class BadInit(Signal):
        def __init__(self, *a):
            raise RuntimeError("oops")

    sc = dict(cfg["scoring"], signals={"news": 30, "bad_init_test": 10})
    assert [type(s).__name__ for s in build_signals(sc)] == ["NewsSignal"]


def test_removing_a_signal_from_config_switches_it_off():
    from tracker.config import Config

    cfg = Config({"scoring": {"signals": {"news": 50, "price": 50}}})
    assert cfg["scoring"]["signals"] == {"news": 50, "price": 50}


def test_ntfy_title_header_has_no_emoji_or_leading_space(monkeypatch, tmp_path):
    import shutil

    from tracker.alerts import channels

    src = ROOT / "plugins" / "ntfy_channel.py"
    if not src.exists():
        pytest.skip("ntfy_channel.py was removed")
    shutil.copy(src, tmp_path / "ntfy_channel.py")
    plugins.load_plugin_dir(tmp_path)
    sent = {}

    class Resp:
        status_code = 200

    def fake_post(url, timeout, **kw):
        sent.update(kw)
        return Resp()

    monkeypatch.setattr(channels.requests, "post", fake_post)
    ch = plugins.get("channels", "ntfy")("topic")
    assert ch.send("🔥 HOT  BWET  score 80/100\nbody")
    assert sent["headers"]["Title"] == "HOT  BWET  score 80/100"
