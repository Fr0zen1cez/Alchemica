"""
telegram_sync.py — Telegram community channel integration.

Responsibilities:
  • Post new combo discoveries as log messages
  • Upload SQLite DB as a document (backup) every BACKUP_INTERVAL seconds
  • Pin the latest backup so restore_from_telegram() can find it on cold start
  • Restore DB from the pinned backup on startup if no local DB exists

Requires the bot to be an ADMIN of the channel with:
  • Post messages ✓
  • Pin messages  ✓
  • Send files    ✓
"""

import io
import logging
import os
import threading
import time
from pathlib import Path

import requests

logger = logging.getLogger("TelegramSync")

BOT_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID   = os.environ.get("TELEGRAM_CHANNEL_ID", "")
BACKUP_INTERVAL = 600   # seconds between backups (10 min)

_dirty        = False
_last_backup  = 0.0
_lock         = threading.Lock()


# ── Low-level API helper ──────────────────────────────────────────────────────

def _api(method: str, json_data=None, files=None, data=None):
    if not BOT_TOKEN:
        return {}
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        if files:
            r = requests.post(url, data=data, files=files, timeout=30)
        else:
            r = requests.post(url, json=json_data, timeout=15)
        return r.json()
    except Exception as e:
        logger.error(f"Telegram API '{method}' error: {e}")
        return {}


# ── Public helpers ────────────────────────────────────────────────────────────

def mark_dirty():
    """Call after every new combo is written to DB."""
    global _dirty
    with _lock:
        _dirty = True


def post_new_combo(item_a: str, item_b: str,
                   result: str, emoji: str, rarity: str):
    """Send a discovery notification to the community channel."""
    stars = {"common": "⚪", "uncommon": "🟢", "rare": "🟣", "legendary": "🟡"}
    tier = stars.get(rarity, "⚪")
    text = (
        f"{tier} *New discovery!*\n"
        f"`{item_a}` \\+ `{item_b}` \\= {emoji} `{result}` \\({rarity}\\)"
    )
    _api("sendMessage", json_data={
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_notification": True,  # silent — don't ping members
    })


def backup_db():
    """Upload the current SQLite file to Telegram and pin the message."""
    from db import DB_PATH
    if not DB_PATH.exists():
        return

    with open(DB_PATH, "rb") as f:
        raw = f.read()

    caption = f"💾 DB backup {time.strftime('%Y-%m-%d %H:%M UTC')}"
    result = _api(
        "sendDocument",
        data={"chat_id": CHANNEL_ID, "caption": caption},
        files={"document": ("combos.db", io.BytesIO(raw), "application/octet-stream")},
    )

    msg_id = (result.get("result") or {}).get("message_id")
    if msg_id:
        # Pin silently so it's always findable on cold-start restore
        _api("pinChatMessage", json_data={
            "chat_id": CHANNEL_ID,
            "message_id": msg_id,
            "disable_notification": True,
        })
        logger.info(f"DB backup uploaded and pinned (msg_id={msg_id})")
    else:
        logger.warning("Backup uploaded but could not pin message")


def restore_from_telegram() -> bool:
    """
    On cold start: check for a pinned message in the channel.
    If it contains a document (our DB backup), download and restore it.
    Returns True if restored, False if starting fresh.
    """
    from db import DB_PATH, init_db

    if not BOT_TOKEN or not CHANNEL_ID:
        logger.warning("Telegram not configured — skipping restore, init fresh DB")
        init_db()
        return False

    logger.info("Checking Telegram for latest DB backup...")
    try:
        chat = _api("getChat", json_data={"chat_id": CHANNEL_ID})
        pinned = (chat.get("result") or {}).get("pinned_message")

        if not pinned or "document" not in pinned:
            logger.info("No pinned backup found — starting with fresh DB")
            init_db()
            return False

        file_id = pinned["document"]["file_id"]
        caption = pinned.get("caption", "")
        logger.info(f"Found backup: {caption}")

        # Get download URL
        file_info = _api("getFile", json_data={"file_id": file_id})
        file_path = (file_info.get("result") or {}).get("file_path")
        if not file_path:
            raise ValueError("Could not get file_path from Telegram")

        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()

        DB_PATH.parent.mkdir(exist_ok=True)
        with open(DB_PATH, "wb") as f:
            f.write(resp.content)

        logger.info(f"DB restored from Telegram ({len(resp.content):,} bytes)")
        # Run init_db anyway to apply any new schema migrations
        init_db()
        return True

    except Exception as e:
        logger.error(f"Telegram restore failed: {e} — starting fresh")
        init_db()
        return False


# ── Background backup loop ────────────────────────────────────────────────────

def _backup_loop():
    global _dirty, _last_backup
    while True:
        time.sleep(30)  # check every 30 s
        with _lock:
            should = _dirty and (time.time() - _last_backup > BACKUP_INTERVAL)

        if should:
            try:
                backup_db()
                with _lock:
                    _dirty = False
                    _last_backup = time.time()
            except Exception as e:
                logger.error(f"Backup loop error: {e}")


def start():
    """Start the background backup thread."""
    t = threading.Thread(target=_backup_loop, daemon=True, name="TelegramBackup")
    t.start()
    logger.info("Telegram backup loop started")
