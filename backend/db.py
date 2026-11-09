"""Database initialization — extends engine.py's SQLite schema with platform tables."""
from __future__ import annotations
import sqlite3
import threading
from pathlib import Path

PLATFORM_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    email           TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    phone           TEXT,
    hashed_pw       TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'TRAVELER',
    department      TEXT,
    airline         TEXT,
    telegram_chat_id TEXT,
    email_verified  INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    expires_at  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT,
    cohort_id    TEXT,
    type         TEXT NOT NULL,
    channel      TEXT NOT NULL,
    status       TEXT NOT NULL,
    payload_json TEXT,
    sent_at      TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rfq_submissions (
    id           TEXT PRIMARY KEY,
    cohort_id    TEXT NOT NULL REFERENCES cohorts(id),
    airline      TEXT NOT NULL,
    submitted_by TEXT NOT NULL REFERENCES users(id),
    per_seat     INTEGER NOT NULL,
    seats_avail  INTEGER NOT NULL,
    deposit_pct  INTEGER NOT NULL DEFAULT 25,
    status       TEXT NOT NULL DEFAULT 'PENDING',
    submitted_at TEXT NOT NULL,
    notes        TEXT
);

CREATE TABLE IF NOT EXISTS user_notification_preferences (
    user_id        TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    email_enabled  INTEGER NOT NULL DEFAULT 1,
    telegram_enabled INTEGER NOT NULL DEFAULT 0,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quote_alerts_sent (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cohort_id   TEXT NOT NULL REFERENCES cohorts(id) ON DELETE CASCADE,
    quote_expires TEXT NOT NULL,
    sent_at     TEXT NOT NULL,
    PRIMARY KEY (user_id, cohort_id, quote_expires)
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_notif_user  ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_rfq_cohort  ON rfq_submissions(cohort_id);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def init_db(path: str) -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None
    parent = Path(path).parent
    if str(parent) != ".":
        parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=10000;")
    conn.row_factory = sqlite3.Row
    conn.executescript(PLATFORM_SCHEMA)
    conn.commit()
    _conn = conn
    return conn


def get_db() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("Database not initialized — call init_db() first.")
    return _conn


def get_lock() -> threading.Lock:
    return _lock
