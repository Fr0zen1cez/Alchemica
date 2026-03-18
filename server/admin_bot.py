"""
admin_bot.py — Private Telegram bot for Alchemica server administration.

SECURITY: Every single command handler checks message.from_user.id == ADMIN_ID
before doing anything. All other messages (from any user) are silently ignored.
The bot uses long-polling in its own daemon thread — it never blocks Flask.

Commands:
  /help        — list all commands
  /stats       — combo counts, RPM, uptime
  /rpm         — requests per minute (live)
  /logs [n]    — last N requests (default 20)
  /top [n]     — top N most common results
  /blocked     — list all blocked IPs
  /block  <ip_hash>  — block an IP hash
  /unblock <ip_hash> — unblock an IP hash
  /broadcast <msg>   — push a notification to all clients
  /backup      — force an immediate DB backup to Telegram
  /dbstats     — detailed DB stats
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger("AdminBot")

BOT_TOKEN = os.environ.get("TELEGRAM_ADMIN_BOT_TOKEN", "")
ADMIN_ID  = int(os.environ.get("TELEGRAM_ADMIN_ID", "0"))  # your Telegram user ID

_start_time = time.time()
_last_update_id = 0


# ── Telegram API ──────────────────────────────────────────────────────────────

def _api(method: str, **kwargs):
    if not BOT_TOKEN:
        return {}
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        r = requests.post(url, timeout=15, **kwargs)
        return r.json()
    except Exception as e:
        logger.error(f"AdminBot API error ({method}): {e}")
        return {}


def _send(chat_id: int, text: str, parse_mode="HTML"):
    _api("sendMessage", json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    })


# ── Command handlers ──────────────────────────────────────────────────────────

def _uptime_str() -> str:
    secs = int(time.time() - _start_time)
    h, m = divmod(secs // 60, 60)
    s = secs % 60
    return f"{h}h {m}m {s}s"


def cmd_help(chat_id):
    _send(chat_id, (
        "<b>Alchemica Admin Bot</b>\n\n"
        "/stats — server overview\n"
        "/rpm — requests per minute\n"
        "/logs [n] — last N requests\n"
        "/top [n] — top discovered results\n"
        "/blocked — list blocked IPs\n"
        "/block &lt;hash&gt; — block an IP\n"
        "/unblock &lt;hash&gt; — unblock an IP\n"
        "/broadcast &lt;msg&gt; — push notification to all clients\n"
        "/backup — force DB backup to Telegram now\n"
        "/dbstats — detailed DB stats"
    ))


def cmd_stats(chat_id):
    import db
    total    = db.get_total_combos()
    verified = db.get_verified_combos()
    rpm      = db.get_rpm()
    uptime   = _uptime_str()
    _send(chat_id, (
        f"<b>Server Stats</b>\n\n"
        f"⏱ Uptime:    {uptime}\n"
        f"📦 Combos:   {total:,}\n"
        f"✅ Verified:  {verified:,}\n"
        f"📡 RPM:      {rpm}"
    ))


def cmd_rpm(chat_id):
    import db
    _send(chat_id, f"📡 Requests per minute: <b>{db.get_rpm()}</b>")


def cmd_logs(chat_id, n=20):
    import db
    n = min(max(n, 1), 50)
    rows = db.get_recent_requests(n)
    if not rows:
        _send(chat_id, "No requests logged yet.")
        return
    lines = []
    for r in rows:
        ts = datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%H:%M:%S")
        ok = "✅" if r["success"] else "❌"
        lines.append(f"{ok} {ts} {r['endpoint']} {r['ip_hash'] or ''}")
    _send(chat_id, "<b>Recent requests:</b>\n<code>" + "\n".join(lines) + "</code>")


def cmd_top(chat_id, n=10):
    import db
    n = min(max(n, 1), 25)
    rows = db.get_top_results(n)
    if not rows:
        _send(chat_id, "No combos yet.")
        return
    lines = [f"{i+1}. {r['result']} ({r['cnt']})" for i, r in enumerate(rows)]
    _send(chat_id, "<b>Top results:</b>\n" + "\n".join(lines))


def cmd_blocked(chat_id):
    import db
    rows = db.list_blocked()
    if not rows:
        _send(chat_id, "No blocked IPs.")
        return
    lines = [f"• <code>{r['ip_hash']}</code> — {r['reason'] or 'no reason'}" for r in rows]
    _send(chat_id, "<b>Blocked IPs:</b>\n" + "\n".join(lines))


def cmd_block(chat_id, args: str):
    import db
    parts = args.strip().split(None, 1)
    if not parts:
        _send(chat_id, "Usage: /block &lt;ip_hash&gt; [reason]")
        return
    ip_hash = parts[0]
    reason  = parts[1] if len(parts) > 1 else "admin"
    db.block_ip(ip_hash, reason)
    _send(chat_id, f"✅ Blocked <code>{ip_hash}</code>")


def cmd_unblock(chat_id, args: str):
    import db
    ip_hash = args.strip()
    if not ip_hash:
        _send(chat_id, "Usage: /unblock &lt;ip_hash&gt;")
        return
    db.unblock_ip(ip_hash)
    _send(chat_id, f"✅ Unblocked <code>{ip_hash}</code>")


def cmd_broadcast(chat_id, message: str):
    import db
    if not message.strip():
        _send(chat_id, "Usage: /broadcast &lt;your message&gt;")
        return
    nid = db.add_notification(message.strip(), expires_hours=72)
    _send(chat_id, f"📢 Broadcast sent (notification id={nid})\nMessage: {message.strip()}")


def cmd_backup(chat_id):
    import telegram_sync
    try:
        telegram_sync.backup_db()
        _send(chat_id, "✅ DB backup uploaded to community channel.")
    except Exception as e:
        _send(chat_id, f"❌ Backup failed: {e}")


def cmd_dbstats(chat_id):
    import db
    total    = db.get_total_combos()
    verified = db.get_verified_combos()
    blocked  = len(db.list_blocked())
    recent   = db.get_recent_requests(1)
    last_ts  = (
        datetime.fromtimestamp(recent[0]["ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if recent else "never"
    )
    _send(chat_id, (
        f"<b>DB Stats</b>\n\n"
        f"Total combos:    {total:,}\n"
        f"Verified combos: {verified:,}\n"
        f"Blocked IPs:     {blocked}\n"
        f"Last request:    {last_ts}"
    ))


# ── Dispatch ──────────────────────────────────────────────────────────────────

def _dispatch(message: dict):
    """Route a Telegram message to the right handler."""
    # SECURITY: silently ignore any message not from the admin
    from_id = (message.get("from") or {}).get("id", 0)
    if from_id != ADMIN_ID:
        return

    chat_id = message["chat"]["id"]
    text    = (message.get("text") or "").strip()

    if not text.startswith("/"):
        return

    # Split "/command@botname args" into cmd + args
    raw_cmd, _, args = text[1:].partition(" ")
    cmd = raw_cmd.split("@")[0].lower()

    try:
        if cmd == "help":
            cmd_help(chat_id)
        elif cmd == "stats":
            cmd_stats(chat_id)
        elif cmd == "rpm":
            cmd_rpm(chat_id)
        elif cmd == "logs":
            n = int(args.strip()) if args.strip().isdigit() else 20
            cmd_logs(chat_id, n)
        elif cmd == "top":
            n = int(args.strip()) if args.strip().isdigit() else 10
            cmd_top(chat_id, n)
        elif cmd == "blocked":
            cmd_blocked(chat_id)
        elif cmd == "block":
            cmd_block(chat_id, args)
        elif cmd == "unblock":
            cmd_unblock(chat_id, args)
        elif cmd == "broadcast":
            cmd_broadcast(chat_id, args)
        elif cmd == "backup":
            cmd_backup(chat_id)
        elif cmd == "dbstats":
            cmd_dbstats(chat_id)
        else:
            _send(chat_id, f"Unknown command: /{cmd}\nUse /help for a list.")
    except Exception as e:
        logger.error(f"Handler error for /{cmd}: {e}")
        _send(chat_id, f"❌ Error: {e}")


# ── Polling loop ──────────────────────────────────────────────────────────────

def _poll_loop():
    global _last_update_id
    logger.info(f"Admin bot polling started (admin_id={ADMIN_ID})")

    while True:
        try:
            resp = _api("getUpdates", json={
                "offset": _last_update_id + 1,
                "timeout": 20,        # long-poll
                "allowed_updates": ["message"],
            })
            updates = resp.get("result") or []
            for update in updates:
                _last_update_id = update["update_id"]
                msg = update.get("message")
                if msg:
                    _dispatch(msg)
        except Exception as e:
            logger.error(f"Admin bot poll error: {e}")
            time.sleep(5)


def start():
    """Start admin bot in a background daemon thread."""
    if not BOT_TOKEN:
        logger.warning("TELEGRAM_ADMIN_BOT_TOKEN not set — admin bot disabled")
        return
    if not ADMIN_ID:
        logger.warning("TELEGRAM_ADMIN_ID not set — admin bot disabled")
        return

    t = threading.Thread(target=_poll_loop, daemon=True, name="AdminBot")
    t.start()
    logger.info("Admin bot thread started")
