"""The 24/7 loop: adapts its pace to the market session, runs daily jobs, reports health."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .adapters.screener import market_scan
from .config import Config
from .engine import Engine
from .market_hours import now_et, parse_hhmm
from .models import utcnow
from .outcomes import update_outcomes
from .reports import close_summary, premarket_brief

log = logging.getLogger(__name__)

STATE: dict = {"started": utcnow().isoformat(), "last_cycle": None, "last_ok": None,
               "last_data": None, "cycles": 0, "failures": 0, "session": None}


class Health(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps(STATE, default=str).encode()
        self.send_response(200 if self.path in ("/", "/health") else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start_health_server(port: int) -> None:
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), Health)
    except OSError as exc:
        log.warning("health server not started on port %s: %s", port, exc)
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("health check at http://localhost:%s/health", port)


class Service:
    def __init__(self, cfg: Config, engine: Engine):
        self.cfg, self.engine, self.db = cfg, engine, engine.db
        self.calendar = engine.calendar
        self.stop = asyncio.Event()
        self.last_price_fetch = 0.0
        self.last_outcomes = 0.0
        self.stale_warned = False

    def interval(self, session: str) -> int:
        s = self.cfg["schedule"]
        return {"regular": s["regular_interval"], "pre": s["extended_interval"],
                "post": s["extended_interval"]}.get(session, s["closed_interval"])

    def _due(self, key: str, at_hhmm: str, trading_day_only: bool = True) -> bool:
        now = now_et()
        if trading_day_only and not self.calendar.is_trading_day(now.date()):
            return False
        return now.time() >= parse_hhmm(at_hhmm) and self.db.kv_get(key) != now.date().isoformat()

    def _done(self, key: str) -> None:
        self.db.kv_set(key, now_et().date().isoformat())

    def daily_jobs(self) -> None:
        """Each job runs once per day, and is only marked done after it succeeds,
        so a failed check retries on the next one."""
        sched, scan = self.cfg["schedule"], self.cfg["market_scan"]
        jobs = []
        if scan.get("enabled"):
            jobs.append(("job_scan", scan["run_at_et"], True, self._job_scan))
        jobs += [
            ("job_brief", sched["premarket_brief_et"], True,
             lambda: self.engine.alerts.broadcast(premarket_brief(self.db))),
            ("job_close", sched["close_summary_et"], True,
             lambda: self.engine.alerts.broadcast(close_summary(self.db))),
            ("job_prune", "03:00", False, lambda: self.db.prune() or True),
        ]
        for key, at, trading_only, job in jobs:
            if not self._due(key, at, trading_only):
                continue
            try:
                if job():
                    self._done(key)
            except Exception:
                log.exception("daily job %s failed; will retry", key)

    def _job_scan(self) -> bool:
        tickers = market_scan(self.cfg["market_scan"]["screens"], self.cfg["market_scan"]["max_tickers"])
        self.db.kv_set("scan_tickers", ",".join(tickers))
        self.db.kv_set("scan_date", now_et().date().isoformat())
        # Retry an empty scan on later checks until late morning, then give up for the day.
        return bool(tickers) or now_et().time() >= parse_hhmm("11:00")

    def run_cycle(self, session: str) -> None:
        price_due = session != "closed" or (
            time.time() - self.last_price_fetch >= self.cfg["schedule"]["closed_price_interval"])
        summary = self.engine.cycle(with_prices=price_due)
        if price_due:
            self.last_price_fetch = time.time()
        if summary["news"] or summary["prices"]:
            STATE["last_data"] = utcnow().isoformat()
        # Daily summaries come before outcome tracking, so a failure there can't hold them up.
        _safely("daily jobs", self.daily_jobs)
        if time.time() - self.last_outcomes > 900:
            _safely("outcome tracking", update_outcomes,
                    self.db, self.engine.prices, self.calendar, self.engine.snapshots)
            self.last_outcomes = time.time()

    def check_stale(self) -> None:
        limit = timedelta(minutes=self.cfg["health"]["stale_after_minutes"])
        last = datetime.fromisoformat(STATE["last_data"] or STATE["started"])
        if utcnow() - last > limit and not self.stale_warned:
            self.engine.alerts.broadcast(
                f"⚠️ Stock tracker heartbeat: no new data for over {limit.seconds // 60} minutes. "
                "Data sources may be down or blocked; the bot is still running and will keep retrying.")
            self.stale_warned = True
        elif utcnow() - last <= limit:
            self.stale_warned = False

    def acquire_lock(self) -> bool:
        """Only one copy of the bot per folder, so alerts never arrive twice from this machine."""
        path = self.cfg.path(self.cfg["storage"]["db_path"]).with_suffix(".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = open(path, "a+")
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    async def run(self) -> None:
        if not self.acquire_lock():
            log.error("another copy of the stock tracker is already running in this folder; exiting")
            return
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop.set)
            except (NotImplementedError, RuntimeError):
                pass  # Windows: Ctrl+C still raises KeyboardInterrupt
        start_health_server(self.cfg["health"]["port"])
        tasks = []
        token, chat = self.cfg.env("TELEGRAM_BOT_TOKEN"), self.cfg.env("TELEGRAM_CHAT_ID")
        if token and chat:
            from .alerts.telegram_commands import TelegramCommands

            tasks.append(asyncio.create_task(TelegramCommands(token, chat, self.db, self.engine.universe, self.engine).run()))
            log.info("Telegram commands enabled")
        last_start = self.db.kv_get("last_start_msg")
        if not last_start or utcnow() - datetime.fromisoformat(last_start) > timedelta(hours=6):
            # Rate-limited, so a crash-restart loop can't flood your phone.
            self.engine.alerts.broadcast(f"✅ Stock tracker started, watching {len(self.engine.universe())} tickers.")
            self.db.kv_set("last_start_msg", utcnow().isoformat())
        while not self.stop.is_set():
            session = self.calendar.session()
            STATE["session"] = session
            STATE["last_cycle"] = utcnow().isoformat()
            try:
                await asyncio.to_thread(self.run_cycle, session)
                STATE["cycles"] += 1
                STATE["last_ok"] = utcnow().isoformat()
                STATE["failures"] = 0
            except Exception:
                STATE["failures"] += 1
                log.exception("cycle failed (%d in a row)", STATE["failures"])
                if STATE["failures"] == 3:
                    self.engine.alerts.broadcast("⚠️ Stock tracker: 3 cycles failed in a row. Check the logs.")
            self.check_stale()
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=self.interval(session))
            except asyncio.TimeoutError:
                pass
        for t in tasks:
            t.cancel()
        log.info("stopped cleanly")


def run_tick(cfg: Config, engine: Engine) -> dict:
    """One full pass for schedulers that start the bot, let it check once, and stop it again
    (GitHub Actions, cron). State lives in the database, so cooldowns, daily summaries and
    outcome tracking carry over between runs."""
    service = Service(cfg, engine)
    token, chat = cfg.env("TELEGRAM_BOT_TOKEN"), cfg.env("TELEGRAM_CHAT_ID")
    if token and chat:
        from .alerts.telegram_commands import TelegramCommands

        _safely("Telegram commands",
                TelegramCommands(token, chat, engine.db, engine.universe, engine).poll_pending)
    session = engine.calendar.session()
    summary = engine.cycle(with_prices=True)
    _safely("heartbeat", _heartbeats, engine, summary)
    _safely("daily jobs", service.daily_jobs)
    _safely("outcome tracking", update_outcomes, engine.db, engine.prices, engine.calendar, engine.snapshots)
    log.info("tick done (%s session): %d news, %d prices, %d alerts", session,
             summary["news"], summary["prices"], summary["alerts"])
    return summary


def _safely(what: str, fn, *args) -> None:
    try:
        fn(*args)
    except Exception:
        log.exception("%s failed; the rest of the check carries on", what)


def _heartbeats(engine: Engine, summary: dict) -> None:
    """Warn once after 3 runs in a row with no data at all, and separately when prices
    are down (alerts mostly can't fire without them)."""
    empty = 0 if (summary["news"] or summary["prices"]) else int(engine.db.kv_get("empty_runs", "0") or 0) + 1
    engine.db.kv_set("empty_runs", str(empty))
    if empty == 3:
        engine.alerts.broadcast("⚠️ Stock tracker heartbeat: the last 3 checks got no data. "
                                "Data sources may be down; it will keep retrying.")
    no_prices = 0 if summary["prices"] else int(engine.db.kv_get("empty_price_runs", "0") or 0) + 1
    engine.db.kv_set("empty_price_runs", str(no_prices))
    if no_prices == 3 and empty < 3:
        engine.alerts.broadcast("⚠️ Stock tracker: no price data for the last 3 checks. News is still "
                                "being collected, but most alerts can't fire until prices return.")
