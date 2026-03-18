"""
db.py — SQLite manager for Alchemica Community Server
All writes go through a threading.Lock so Flask + admin bot can share safely.
"""

import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path("data/combos.db")
_lock = threading.Lock()


# ── Connection ────────────────────────────────────────────────────────────────

def _conn():
    DB_PATH.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")   # safe for concurrent reads
    c.execute("PRAGMA synchronous=NORMAL")
    return c


# ── Schema ────────────────────────────────────────────────────────────────────

def init_db():
    with _lock:
        c = _conn()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS combos (
                key         TEXT PRIMARY KEY,
                item_a      TEXT NOT NULL,
                item_b      TEXT NOT NULL,
                result      TEXT NOT NULL,
                emoji       TEXT NOT NULL,
                rarity      TEXT NOT NULL,
                confidence  INTEGER DEFAULT 1,
                verified    INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS blocklist (
                ip_hash     TEXT PRIMARY KEY,
                reason      TEXT,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                message     TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                expires_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS request_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          REAL NOT NULL,
                endpoint    TEXT NOT NULL,
                ip_hash     TEXT,
                success     INTEGER DEFAULT 1
            );

            -- ── Friend leaderboard ─────────────────────────────────────────
            -- One row per player (upserted on each stat push).
            -- username is whatever the player set in their local account.
            -- avatar_color is their chosen hex colour for the leaderboard UI.
            CREATE TABLE IF NOT EXISTS leaderboard (
                username        TEXT PRIMARY KEY,
                avatar_color    TEXT DEFAULT '#4a9eff',
                total_discoveries INTEGER DEFAULT 0,
                total_combos    INTEGER DEFAULT 0,
                best_speedrun_ms INTEGER DEFAULT 0,   -- 0 = never ran
                best_speedrun_world TEXT DEFAULT '',
                rarest_item     TEXT DEFAULT '',
                rarest_emoji    TEXT DEFAULT '',
                rarest_rarity   TEXT DEFAULT '',
                daily_streak    INTEGER DEFAULT 0,
                weekly_streak   INTEGER DEFAULT 0,
                updated_at      TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_request_log_ts ON request_log(ts);
            CREATE INDEX IF NOT EXISTS idx_combos_key    ON combos(key);
            CREATE INDEX IF NOT EXISTS idx_lb_discoveries ON leaderboard(total_discoveries DESC);
        """)
        c.commit()
        c.close()


# ── Combos ────────────────────────────────────────────────────────────────────

def lookup_combo(key: str):
    with _lock:
        c = _conn()
        row = c.execute("SELECT * FROM combos WHERE key = ?", (key,)).fetchone()
        c.close()
        return dict(row) if row else None


def submit_combo(key, item_a, item_b, result, emoji, rarity, ip_hash) -> bool:
    """
    Insert or increment confidence.
    Returns True if this was a brand-new combo, False if it already existed.
    """
    now = datetime.utcnow().isoformat()
    with _lock:
        c = _conn()
        existing = c.execute(
            "SELECT confidence FROM combos WHERE key = ?", (key,)
        ).fetchone()

        if existing:
            new_conf = existing["confidence"] + 1
            verified = 1 if new_conf >= 3 else 0
            c.execute(
                "UPDATE combos SET confidence=?, verified=?, updated_at=? WHERE key=?",
                (new_conf, verified, now, key),
            )
            c.commit()
            c.close()
            return False

        c.execute(
            """INSERT INTO combos
               (key,item_a,item_b,result,emoji,rarity,confidence,verified,created_at,updated_at)
               VALUES (?,?,?,?,?,?,1,0,?,?)""",
            (key, item_a, item_b, result, emoji, rarity, now, now),
        )
        c.commit()
        c.close()
        return True


def get_total_combos() -> int:
    with _lock:
        c = _conn()
        n = c.execute("SELECT COUNT(*) FROM combos").fetchone()[0]
        c.close()
        return n


def get_verified_combos() -> int:
    with _lock:
        c = _conn()
        n = c.execute("SELECT COUNT(*) FROM combos WHERE verified=1").fetchone()[0]
        c.close()
        return n


def get_top_results(limit=10):
    with _lock:
        c = _conn()
        rows = c.execute(
            "SELECT result, COUNT(*) as cnt FROM combos GROUP BY result ORDER BY cnt DESC LIMIT ?",
            (limit,),
        ).fetchall()
        c.close()
        return [dict(r) for r in rows]


# ── Blocklist ─────────────────────────────────────────────────────────────────

def is_blocked(ip_hash: str) -> bool:
    with _lock:
        c = _conn()
        row = c.execute(
            "SELECT 1 FROM blocklist WHERE ip_hash=?", (ip_hash,)
        ).fetchone()
        c.close()
        return row is not None


def block_ip(ip_hash: str, reason: str = ""):
    now = datetime.utcnow().isoformat()
    with _lock:
        c = _conn()
        c.execute(
            "INSERT OR REPLACE INTO blocklist(ip_hash,reason,created_at) VALUES(?,?,?)",
            (ip_hash, reason, now),
        )
        c.commit()
        c.close()


def unblock_ip(ip_hash: str):
    with _lock:
        c = _conn()
        c.execute("DELETE FROM blocklist WHERE ip_hash=?", (ip_hash,))
        c.commit()
        c.close()


def list_blocked():
    with _lock:
        c = _conn()
        rows = c.execute("SELECT * FROM blocklist ORDER BY created_at DESC").fetchall()
        c.close()
        return [dict(r) for r in rows]


# ── Notifications ─────────────────────────────────────────────────────────────

def add_notification(message: str, expires_hours: int = 48) -> int:
    now = datetime.utcnow()
    expires = (now + timedelta(hours=expires_hours)).isoformat()
    with _lock:
        c = _conn()
        cur = c.execute(
            "INSERT INTO notifications(message,created_at,expires_at) VALUES(?,?,?)",
            (message, now.isoformat(), expires),
        )
        row_id = cur.lastrowid
        c.commit()
        c.close()
        return row_id


def get_notifications(since_id: int = 0):
    now = datetime.utcnow().isoformat()
    with _lock:
        c = _conn()
        rows = c.execute(
            """SELECT id, message, created_at FROM notifications
               WHERE id > ? AND (expires_at IS NULL OR expires_at > ?)
               ORDER BY id ASC""",
            (since_id, now),
        ).fetchall()
        c.close()
        return [dict(r) for r in rows]


# ── Request log ───────────────────────────────────────────────────────────────

def log_request(endpoint: str, ip_hash: str, success: bool = True):
    with _lock:
        c = _conn()
        c.execute(
            "INSERT INTO request_log(ts,endpoint,ip_hash,success) VALUES(?,?,?,?)",
            (time.time(), endpoint, ip_hash, 1 if success else 0),
        )
        # Prune: keep only the last 20 000 rows
        c.execute(
            "DELETE FROM request_log WHERE id NOT IN "
            "(SELECT id FROM request_log ORDER BY id DESC LIMIT 20000)"
        )
        c.commit()
        c.close()


def get_rpm() -> int:
    cutoff = time.time() - 60
    with _lock:
        c = _conn()
        n = c.execute(
            "SELECT COUNT(*) FROM request_log WHERE ts > ?", (cutoff,)
        ).fetchone()[0]
        c.close()
        return n


def get_recent_requests(limit: int = 50):
    with _lock:
        c = _conn()
        rows = c.execute(
            "SELECT ts, endpoint, ip_hash, success FROM request_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        c.close()
        return [dict(r) for r in rows]


# ── Leaderboard ───────────────────────────────────────────────────────────────

RARITY_RANK = {
    "transcendent": 6, "mythic": 5, "legendary": 4,
    "rare": 3, "uncommon": 2, "common": 1,
}

def upsert_leaderboard(username: str, stats: dict) -> None:
    """
    Insert or update a player's leaderboard row.
    Only updates a field if the incoming value is strictly better than stored.
    """
    now = datetime.utcnow().isoformat()
    with _lock:
        c = _conn()
        existing = c.execute(
            "SELECT * FROM leaderboard WHERE username=?", (username,)
        ).fetchone()

        if not existing:
            c.execute("""
                INSERT INTO leaderboard
                  (username, avatar_color, total_discoveries, total_combos,
                   best_speedrun_ms, best_speedrun_world,
                   rarest_item, rarest_emoji, rarest_rarity,
                   daily_streak, weekly_streak, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                username,
                stats.get("avatar_color", "#4a9eff"),
                stats.get("total_discoveries", 0),
                stats.get("total_combos", 0),
                stats.get("best_speedrun_ms", 0),
                stats.get("best_speedrun_world", ""),
                stats.get("rarest_item", ""),
                stats.get("rarest_emoji", ""),
                stats.get("rarest_rarity", ""),
                stats.get("daily_streak", 0),
                stats.get("weekly_streak", 0),
                now,
            ))
        else:
            ex = dict(existing)
            # Best speedrun: lower ms is better (0 means never ran, so skip)
            incoming_ms = stats.get("best_speedrun_ms", 0)
            best_ms = ex["best_speedrun_ms"]
            if incoming_ms > 0 and (best_ms == 0 or incoming_ms < best_ms):
                best_ms = incoming_ms
                best_world = stats.get("best_speedrun_world", ex["best_speedrun_world"])
            else:
                best_world = ex["best_speedrun_world"]

            # Rarest item: higher rank wins
            in_rank   = RARITY_RANK.get(stats.get("rarest_rarity", ""), 0)
            ex_rank   = RARITY_RANK.get(ex["rarest_rarity"], 0)
            if in_rank > ex_rank:
                rarest_item   = stats.get("rarest_item",  ex["rarest_item"])
                rarest_emoji  = stats.get("rarest_emoji", ex["rarest_emoji"])
                rarest_rarity = stats.get("rarest_rarity", ex["rarest_rarity"])
            else:
                rarest_item, rarest_emoji, rarest_rarity = ex["rarest_item"], ex["rarest_emoji"], ex["rarest_rarity"]

            c.execute("""
                UPDATE leaderboard SET
                  avatar_color       = ?,
                  total_discoveries  = MAX(total_discoveries, ?),
                  total_combos       = MAX(total_combos, ?),
                  best_speedrun_ms   = ?,
                  best_speedrun_world= ?,
                  rarest_item        = ?,
                  rarest_emoji       = ?,
                  rarest_rarity      = ?,
                  daily_streak       = MAX(daily_streak, ?),
                  weekly_streak      = MAX(weekly_streak, ?),
                  updated_at         = ?
                WHERE username = ?
            """, (
                stats.get("avatar_color", ex["avatar_color"]),
                stats.get("total_discoveries", 0),
                stats.get("total_combos", 0),
                best_ms, best_world,
                rarest_item, rarest_emoji, rarest_rarity,
                stats.get("daily_streak", 0),
                stats.get("weekly_streak", 0),
                now,
                username,
            ))

        c.commit()
        c.close()


def get_leaderboard() -> list:
    with _lock:
        c = _conn()
        rows = c.execute("""
            SELECT username, avatar_color,
                   total_discoveries, total_combos,
                   best_speedrun_ms, best_speedrun_world,
                   rarest_item, rarest_emoji, rarest_rarity,
                   daily_streak, weekly_streak, updated_at
            FROM leaderboard
            ORDER BY total_discoveries DESC
        """).fetchall()
        c.close()
        return [dict(r) for r in rows]
