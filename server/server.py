"""
server.py — Alchemica Community API
Deploy this folder to Render (free tier).

Required environment variables (set in Render dashboard):
  HMAC_SECRET               — shared secret with the game client
  TELEGRAM_BOT_TOKEN        — community channel bot token
  TELEGRAM_CHANNEL_ID       — community channel ID (e.g. -100xxxxxxxxxx)
  TELEGRAM_ADMIN_BOT_TOKEN  — separate bot token for admin/dev tools
  TELEGRAM_ADMIN_ID         — YOUR Telegram user ID (integer)

Endpoints:
  GET  /health              — liveness probe
  GET  /api/lookup          — check community DB for a combo
  POST /api/submit          — submit a new combo
  GET  /api/notifications   — poll for broadcast notifications
"""

import hashlib
import logging
import os
import sys

from flask import Flask, jsonify, request

# Allow imports from this folder regardless of working directory
sys.path.insert(0, os.path.dirname(__file__))

import admin_bot
import db
import limiter
import telegram_sync

# ── App setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("Server")

# ── Startup ───────────────────────────────────────────────────────────────────

@app.before_request
def _startup():
    """Idempotent startup — only runs real work once."""
    if getattr(app, "_started", False):
        return
    app._started = True
    telegram_sync.restore_from_telegram()
    telegram_sync.start()
    admin_bot.start()
    logger.info("Server started successfully")


# ── Auth helper ───────────────────────────────────────────────────────────────

def _verify_request() -> bool:
    """
    Validate the HMAC signature attached to every client request.
    Headers expected:
      X-Timestamp  — unix timestamp (float string)
      X-Signature  — hex HMAC-SHA256
    Body hash is SHA256 of the raw request body.
    """
    timestamp = request.headers.get("X-Timestamp", "")
    signature = request.headers.get("X-Signature", "")
    if not timestamp or not signature:
        return False
    body_hash = hashlib.sha256(request.get_data()).hexdigest()
    return limiter.verify_hmac(
        request.method,
        request.path,
        timestamp,
        body_hash,
        signature,
    )


def _client_ip() -> str:
    """Best-effort real IP behind Render's proxy."""
    return (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.remote_addr
        or "unknown"
    )


def _bad(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def _ok(data: dict):
    return jsonify({"ok": True, **data})


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return _ok({"combos": db.get_total_combos(), "rpm": db.get_rpm()})


@app.get("/api/lookup")
def lookup():
    # Rate limit (no HMAC needed for reads — they can't cause damage)
    ip = _client_ip()
    allowed, ip_hash = limiter.check_rate_limit(ip, "lookup")
    if not allowed:
        db.log_request("lookup", ip_hash, success=False)
        return _bad("Rate limit exceeded", 429)

    if db.is_blocked(ip_hash):
        return _bad("Forbidden", 403)

    a = (request.args.get("a") or "").strip().lower()
    b = (request.args.get("b") or "").strip().lower()
    if not a or not b:
        return _bad("Missing parameters a and b")

    # Canonical key: alphabetically sorted so fire+water == water+fire
    key = "|".join(sorted([a, b]))
    combo = db.lookup_combo(key)

    db.log_request("lookup", ip_hash)

    if combo:
        return _ok({
            "found":     True,
            "result":    combo["result"],
            "emoji":     combo["emoji"],
            "rarity":    combo["rarity"],
            "verified":  bool(combo["verified"]),
            "confidence": combo["confidence"],
        })
    return _ok({"found": False})


@app.post("/api/submit")
def submit():
    ip = _client_ip()

    # HMAC — reject anything not signed by the game client
    if not _verify_request():
        db.log_request("submit", limiter.hash_ip(ip), success=False)
        return _bad("Invalid or missing signature", 401)

    allowed, ip_hash = limiter.check_rate_limit(ip, "submit")
    if not allowed:
        db.log_request("submit", ip_hash, success=False)
        return _bad("Rate limit exceeded", 429)

    if db.is_blocked(ip_hash):
        return _bad("Forbidden", 403)

    body = request.get_json(silent=True) or {}
    item_a = (body.get("item_a") or "").strip().lower()
    item_b = (body.get("item_b") or "").strip().lower()
    result = (body.get("result") or "").strip()
    emoji  = (body.get("emoji")  or "").strip()
    rarity = (body.get("rarity") or "").strip().lower()

    # Server-side validation — the client cannot influence this
    valid, reason = limiter.validate_combo(item_a, item_b, result, emoji, rarity)
    if not valid:
        db.log_request("submit", ip_hash, success=False)
        # Three bad submissions → auto-block
        _maybe_autoblock(ip_hash, reason)
        return _bad(f"Validation failed: {reason}", 422)

    key = "|".join(sorted([item_a, item_b]))
    is_new = db.submit_combo(key, item_a, item_b, result, emoji, rarity, ip_hash)

    db.log_request("submit", ip_hash)

    if is_new:
        telegram_sync.mark_dirty()
        telegram_sync.post_new_combo(item_a, item_b, result, emoji, rarity)
        logger.info(f"New combo: {item_a}+{item_b}={result} from {ip_hash}")

    return _ok({"new": is_new})


@app.get("/api/notifications")
def notifications():
    ip = _client_ip()
    allowed, ip_hash = limiter.check_rate_limit(ip, "lookup")
    if not allowed:
        return _bad("Rate limit exceeded", 429)

    since = request.args.get("since", 0)
    try:
        since = int(since)
    except (ValueError, TypeError):
        since = 0

    msgs = db.get_notifications(since)
    return _ok({"notifications": msgs})


# ── Leaderboard ───────────────────────────────────────────────────────────────
# ⚠️  This server is designed for small friend groups only — not public use.
# Anyone with the webhook URL can submit stats. Share it only with friends.

@app.post("/api/leaderboard/submit")
def leaderboard_submit():
    ip = _client_ip()

    if not _verify_request():
        db.log_request("lb_submit", limiter.hash_ip(ip), success=False)
        return _bad("Invalid or missing signature", 401)

    allowed, ip_hash = limiter.check_rate_limit(ip, "submit")
    if not allowed:
        return _bad("Rate limit exceeded", 429)

    if db.is_blocked(ip_hash):
        return _bad("Forbidden", 403)

    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    if not username or len(username) > 32:
        return _bad("Invalid username")

    allowed_fields = {
        "avatar_color", "total_discoveries", "total_combos",
        "best_speedrun_ms", "best_speedrun_world",
        "rarest_item", "rarest_emoji", "rarest_rarity",
        "daily_streak", "weekly_streak",
    }
    stats = {k: v for k, v in body.items() if k in allowed_fields}

    db.upsert_leaderboard(username, stats)
    db.log_request("lb_submit", ip_hash)
    logger.info(f"Leaderboard update from {username} ({ip_hash})")
    return _ok({"updated": True})


@app.get("/api/leaderboard")
def leaderboard_get():
    ip = _client_ip()
    allowed, ip_hash = limiter.check_rate_limit(ip, "lookup")
    if not allowed:
        return _bad("Rate limit exceeded", 429)

    rows = db.get_leaderboard()
    return _ok({"players": rows})


# ── Auto-block logic ──────────────────────────────────────────────────────────

_bad_counts: dict = {}
_bad_lock = __import__("threading").Lock()

def _maybe_autoblock(ip_hash: str, reason: str):
    """Auto-block an IP after 3 invalid submissions in a session."""
    with _bad_lock:
        _bad_counts[ip_hash] = _bad_counts.get(ip_hash, 0) + 1
        if _bad_counts[ip_hash] >= 3:
            db.block_ip(ip_hash, f"auto-block: {reason}")
            logger.warning(f"Auto-blocked {ip_hash} after repeated invalid submissions")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)), debug=False)
