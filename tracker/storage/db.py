"""SQLite storage. Swap the connection for Postgres later if you outgrow it."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..models import NewsEvent, ScoreResult, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_items (id TEXT PRIMARY KEY, seen_at TEXT);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY, title TEXT, url TEXT, first_seen TEXT, last_seen TEXT,
    tickers TEXT, themes TEXT, catalyst TEXT, strength REAL, sentiment REAL, novelty REAL,
    source_count INTEGER, sources TEXT, reason TEXT);
CREATE INDEX IF NOT EXISTS ix_events_seen ON events(first_seen);
CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, asof TEXT, score REAL, components TEXT,
    direction TEXT, reasons TEXT, price REAL, pct_change REAL, rvol REAL, event_id TEXT);
CREATE INDEX IF NOT EXISTS ix_scores_ticker ON scores(ticker, asof);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, asof TEXT, tier TEXT, score REAL,
    price REAL, direction TEXT, headline TEXT, url TEXT, message TEXT,
    price_1h REAL, price_1d REAL, price_5d REAL);
CREATE INDEX IF NOT EXISTS ix_alerts_ticker ON alerts(ticker, asof);
CREATE TABLE IF NOT EXISTS watch_overrides (ticker TEXT PRIMARY KEY, action TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


def _ts(dt: datetime) -> str:
    """Store every time as UTC so string comparisons in SQL compare real instants."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


class Database:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def _exec(self, sql: str, params=()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def _all(self, sql: str, params=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    # --- news ---------------------------------------------------------------
    def unseen(self, ids: list[str]) -> set[str]:
        if not ids:
            return set()
        seen = set()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows = self._all(f"SELECT id FROM seen_items WHERE id IN ({','.join('?' * len(chunk))})", chunk)
            seen |= {r["id"] for r in rows}
        return set(ids) - seen

    def mark_seen(self, ids: list[str]) -> None:
        now = _ts(utcnow())
        with self.lock:
            self.conn.executemany("INSERT OR IGNORE INTO seen_items VALUES (?, ?)", [(i, now) for i in ids])
            self.conn.commit()

    def save_event(self, ev: NewsEvent) -> None:
        self._exec(
            "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ev.event_id, ev.title, ev.url, _ts(ev.first_seen), _ts(utcnow()), json.dumps(ev.tickers),
             json.dumps(ev.themes), ev.catalyst, ev.catalyst_strength, ev.sentiment, ev.novelty,
             ev.source_count, json.dumps(ev.sources), ev.reason))

    def recent_events(self, hours: float, now: datetime | None = None) -> list[NewsEvent]:
        since = _ts((now or utcnow()) - timedelta(hours=hours))
        rows = self._all("SELECT * FROM events WHERE first_seen >= ? ORDER BY first_seen", (since,))
        return [NewsEvent(
            event_id=r["event_id"], title=r["title"], url=r["url"], first_seen=_dt(r["first_seen"]),
            tickers=json.loads(r["tickers"]), themes=json.loads(r["themes"]), catalyst=r["catalyst"],
            catalyst_strength=r["strength"], sentiment=r["sentiment"], novelty=r["novelty"],
            source_count=r["source_count"], sources=json.loads(r["sources"]), reason=r["reason"] or "",
        ) for r in rows]

    # --- scores -------------------------------------------------------------
    def save_scores(self, results: list[ScoreResult]) -> None:
        rows = []
        for r in results:
            s = r.snapshot
            rows.append((r.ticker, _ts(r.asof), r.score, json.dumps(r.components), r.direction,
                         json.dumps(r.reasons), s.price if s else None, s.pct_change if s else None,
                         s.rvol if s else None, r.top_event.event_id if r.top_event else None))
        with self.lock:
            self.conn.executemany(
                "INSERT INTO scores (ticker, asof, score, components, direction, reasons, price, "
                "pct_change, rvol, event_id) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
            self.conn.commit()

    def latest_scores(self) -> list[sqlite3.Row]:
        """Scores from the most recent cycle only (every ticker in a cycle shares one timestamp),
        so tickers you removed or yesterday's scan picks don't linger."""
        return self._all("SELECT * FROM scores WHERE asof = (SELECT MAX(asof) FROM scores) "
                         "ORDER BY score DESC")

    def latest_score(self, ticker: str) -> sqlite3.Row | None:
        rows = self._all("SELECT * FROM scores WHERE ticker = ? ORDER BY id DESC LIMIT 1", (ticker,))
        return rows[0] if rows else None

    def event(self, event_id: str) -> sqlite3.Row | None:
        rows = self._all("SELECT * FROM events WHERE event_id = ?", (event_id,))
        return rows[0] if rows else None

    # --- alerts -------------------------------------------------------------
    def save_alert(self, r: ScoreResult, tier: str, message: str, at: datetime | None = None) -> int:
        ev = r.top_event
        cur = self._exec(
            "INSERT INTO alerts (ticker, asof, tier, score, price, direction, headline, url, message) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (r.ticker, _ts(at or r.asof), tier, r.score, r.snapshot.price if r.snapshot else None, r.direction,
             ev.title if ev else None, ev.url if ev else None, message))
        return cur.lastrowid

    def last_alert(self, ticker: str) -> sqlite3.Row | None:
        rows = self._all("SELECT * FROM alerts WHERE ticker = ? ORDER BY id DESC LIMIT 1", (ticker,))
        return rows[0] if rows else None

    def alerts_since(self, since: datetime, ticker: str | None = None) -> list[sqlite3.Row]:
        if ticker:
            return self._all("SELECT * FROM alerts WHERE asof >= ? AND ticker = ? ORDER BY id",
                             (_ts(since), ticker))
        return self._all("SELECT * FROM alerts WHERE asof >= ? ORDER BY id", (_ts(since),))

    def pending_outcomes(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM alerts WHERE price IS NOT NULL AND "
                         "(price_1h IS NULL OR price_1d IS NULL OR price_5d IS NULL) "
                         "AND asof >= ?", (_ts(utcnow() - timedelta(days=12)),))

    def set_outcome(self, alert_id: int, column: str, price: float) -> None:
        assert column in ("price_1h", "price_1d", "price_5d")
        self._exec(f"UPDATE alerts SET {column} = ? WHERE id = ?", (price, alert_id))

    def all_alerts(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM alerts ORDER BY id")

    # --- watchlist edits and small state ------------------------------------
    def set_override(self, ticker: str, action: str) -> None:
        self._exec("INSERT OR REPLACE INTO watch_overrides VALUES (?,?,?)", (ticker, action, _ts(utcnow())))

    def overrides(self) -> dict[str, str]:
        return {r["ticker"]: r["action"] for r in self._all("SELECT * FROM watch_overrides")}

    def kv_get(self, key: str, default: str | None = None) -> str | None:
        rows = self._all("SELECT value FROM kv WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    def kv_set(self, key: str, value: str) -> None:
        self._exec("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, value))

    def prune(self, keep_days: int = 30) -> None:
        cutoff = _ts(utcnow() - timedelta(days=keep_days))
        with self.lock:
            self.conn.execute("DELETE FROM seen_items WHERE seen_at < ?", (cutoff,))
            self.conn.execute("DELETE FROM scores WHERE asof < ?", (cutoff,))
            self.conn.execute("DELETE FROM events WHERE first_seen < ?", (cutoff,))
            self.conn.commit()
