import json
import time
import requests
from core.logger import get_logger
from core.config import load_config, save_config
from core.utils import combo_key as _combo_key

logger = get_logger()

# ── Constants ─────────────────────────────────────────────────────────────────
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
_MSG_PREFIX  = "EF_COMBO:"      # prefix used to identify combo messages
_SYNC_INTERVAL = 300            # re-sync at most every 5 min


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tg(token, method, **kwargs):
    url = TELEGRAM_API.format(token=token, method=method)
    try:
        r = requests.post(url, json=kwargs, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"Telegram API error ({method}): {e}")
        return None


# ── Main interface ────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    cfg = load_config()
    return bool(cfg.get("shared_db_enabled") and _get_backend(cfg))


def _get_backend(cfg):
    return cfg.get("shared_db_backend", "telegram")


def test_connection() -> dict:
    """Returns {"ok": True, "detail": "..."} or {"ok": False, "error": "..."}"""
    cfg = load_config()
    backend = _get_backend(cfg)
    try:
        if backend == "telegram":
            token   = cfg.get("shared_db_tg_token", "")
            chat_id = cfg.get("shared_db_tg_chat", "")
            if not token or not chat_id:
                return {"ok": False, "error": "Bot token and chat ID are required."}
            me = _tg(token, "getMe")
            if not me or not me.get("ok"):
                return {"ok": False, "error": "Invalid bot token."}
            # Send a silent test ping to confirm bot can write to chat
            ping = _tg(token, "sendMessage",
                       chat_id=chat_id,
                       text=f"{_MSG_PREFIX}PING",
                       disable_notification=True)
            if not ping or not ping.get("ok"):
                return {"ok": False, "error": "Bot cannot post to that chat. Make sure it is a member and has permission to send messages."}
            # Delete ping immediately so it doesn't clutter the chat
            _tg(token, "deleteMessage",
                chat_id=chat_id,
                message_id=ping["result"]["message_id"])
            return {"ok": True, "detail": f"Connected as @{me['result']['username']}"}

        elif backend == "webhook":
            url = cfg.get("shared_db_webhook_url", "")
            if not url:
                return {"ok": False, "error": "Webhook URL is required."}
            r = requests.get(url.rstrip("/") + "/ping", timeout=8)
            if r.status_code not in (200, 404):   # 404 means server is up, route just doesn't exist
                return {"ok": False, "error": f"Server returned {r.status_code}"}
            return {"ok": True, "detail": f"Endpoint reachable ({r.status_code})"}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def lookup(item_a: str, item_b: str) -> dict | None:
    """Check local cache for a combo result. Returns result dict or None."""
    cfg = load_config()
    if not cfg.get("shared_db_enabled"):
        return None
    cache = cfg.get("shared_db_cache") or {}
    key = _combo_key(item_a, item_b)
    hit = cache.get(key)
    if hit:
        logger.info(f"Shared DB cache hit: {key} → {hit.get('result')}")
    return hit


def submit(item_a: str, item_b: str, result: dict, is_new_global: bool = False):
    """
    Push a new combo result to the shared backend.
    result = {"result": str, "emoji": str, "rarity": str, "lore": str}
    is_new_global: True if this wasn't in the cache before this call.
    """
    cfg = load_config()
    if not cfg.get("shared_db_enabled"):
        return
    key     = _combo_key(item_a, item_b)
    payload = {
        "key":    key,
        "a":      item_a.lower().strip(),
        "b":      item_b.lower().strip(),
        "result": result.get("result", ""),
        "emoji":  result.get("emoji", "✨"),
        "rarity": result.get("rarity", "common"),
        "lore":   result.get("lore", ""),
        "ts":     int(time.time()),
    }

    # Update local cache immediately
    cache = cfg.get("shared_db_cache") or {}
    cache[key] = payload
    cfg["shared_db_cache"] = cache
    save_config(cfg)

    backend = _get_backend(cfg)
    try:
        if backend == "telegram":
            token   = cfg.get("shared_db_tg_token", "")
            chat_id = cfg.get("shared_db_tg_chat", "")
            if not token or not chat_id:
                return
            msg = f"{_MSG_PREFIX}{json.dumps(payload, ensure_ascii=False)}"
            _tg(token, "sendMessage",
                chat_id=chat_id,
                text=msg,
                disable_notification=True)

        elif backend == "webhook":
            url = cfg.get("shared_db_webhook_url", "")
            if not url:
                return
            requests.post(url.rstrip("/") + "/submit",
                          json=payload, timeout=8)
    except Exception as e:
        logger.warning(f"Shared DB submit failed: {e}")


def sync(force: bool = False) -> dict:
    """
    Pull new combo results from backend and merge into local cache.
    Returns {"synced": N, "total": M, "new": K}
    """
    cfg = load_config()
    if not cfg.get("shared_db_enabled"):
        return {"synced": 0, "total": 0, "new": 0}

    # Rate-limit syncs
    last = cfg.get("shared_db_last_sync", 0)
    if not force and (time.time() - last) < _SYNC_INTERVAL:
        cache = cfg.get("shared_db_cache") or {}
        return {"synced": 0, "total": len(cache), "new": 0, "cached": True}

    backend = _get_backend(cfg)
    cache   = dict(cfg.get("shared_db_cache") or {})
    new_count = 0

    try:
        if backend == "telegram":
            new_count = _sync_telegram(cfg, cache)
        elif backend == "webhook":
            new_count = _sync_webhook(cfg, cache)
    except Exception as e:
        logger.error(f"Shared DB sync failed: {e}")

    cfg["shared_db_cache"]     = cache
    cfg["shared_db_last_sync"] = int(time.time())
    save_config(cfg)
    return {"synced": 1, "total": len(cache), "new": new_count}


def _sync_telegram(cfg, cache: dict) -> int:
    token   = cfg.get("shared_db_tg_token", "")
    chat_id = str(cfg.get("shared_db_tg_chat", ""))
    if not token or not chat_id:
        return 0

    # Use getUpdates with large limit to catch recent messages.
    # We store the last seen update_id to avoid re-processing.
    offset  = cfg.get("shared_db_tg_offset", 0)
    resp    = _tg(token, "getUpdates", offset=offset, limit=100, timeout=5)
    if not resp or not resp.get("ok"):
        return 0

    new_count = 0
    max_update_id = offset

    for upd in resp.get("result", []):
        uid = upd.get("update_id", 0)
        if uid > max_update_id:
            max_update_id = uid

        msg = upd.get("message") or upd.get("channel_post") or {}
        if str(msg.get("chat", {}).get("id", "")) != chat_id and \
           str(msg.get("chat", {}).get("username", "")) != chat_id.lstrip("@"):
            continue

        text = msg.get("text", "")
        if not text.startswith(_MSG_PREFIX):
            continue
        if text == f"{_MSG_PREFIX}PING":
            continue

        try:
            data = json.loads(text[len(_MSG_PREFIX):])
            key = data.get("key") or _combo_key(data.get("a",""), data.get("b",""))
            if key and key not in cache:
                cache[key] = data
                new_count += 1
            elif key and data.get("ts", 0) > cache[key].get("ts", 0):
                cache[key] = data   # newer entry wins
        except Exception:
            pass

    if max_update_id > offset:
        cfg["shared_db_tg_offset"] = max_update_id + 1

    return new_count


def _sync_webhook(cfg, cache: dict) -> int:
    url   = cfg.get("shared_db_webhook_url", "")
    since = cfg.get("shared_db_last_sync", 0)
    if not url:
        return 0
    try:
        r = requests.get(url.rstrip("/") + f"/list?since={since}", timeout=10)
        r.raise_for_status()
        entries = r.json()
        if not isinstance(entries, list):
            entries = entries.get("results", [])
        new_count = 0
        for data in entries:
            key = data.get("key") or _combo_key(data.get("a",""), data.get("b",""))
            if key and key not in cache:
                cache[key] = data
                new_count += 1
        return new_count
    except Exception as e:
        logger.warning(f"Webhook sync error: {e}")
        return 0


def get_stats() -> dict:
    cfg   = load_config()
    cache = cfg.get("shared_db_cache") or {}
    return {
        "enabled":   cfg.get("shared_db_enabled", False),
        "backend":   cfg.get("shared_db_backend", "telegram"),
        "total":     len(cache),
        "last_sync": cfg.get("shared_db_last_sync", 0),
    }


# ── Leaderboard ───────────────────────────────────────────────────────────────

import hashlib as _hashlib
import hmac    as _hmac
import time    as _time

_HMAC_SECRET = b"alchemica-community-v1"   # must match server/limiter.py default

def _signed_headers(body_bytes: bytes, path: str) -> dict:
    """Generate the HMAC headers the server expects."""
    ts        = str(_time.time())
    body_hash = _hashlib.sha256(body_bytes).hexdigest()
    message   = f"POST:{path}:{ts}:{body_hash}".encode()
    sig       = _hmac.new(_HMAC_SECRET, message, _hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Timestamp":  ts,
        "X-Signature":  sig,
    }


def submit_leaderboard(stats: dict) -> bool:
    """
    Push this player's stats to the community leaderboard.
    Only works when the webhook backend is enabled and a URL is configured.
    Returns True on success.
    """
    cfg = load_config()
    if not cfg.get("shared_db_enabled"):
        return False
    if cfg.get("shared_db_backend", "telegram") != "webhook":
        return False
    url = cfg.get("shared_db_webhook_url", "").rstrip("/")
    if not url:
        return False

    path    = "/api/leaderboard/submit"
    payload = json.dumps(stats).encode()
    try:
        r = requests.post(
            url + path,
            data=payload,
            headers=_signed_headers(payload, path),
            timeout=10,
        )
        return r.ok
    except Exception as e:
        logger.warning(f"Leaderboard submit error: {e}")
        return False


def fetch_leaderboard() -> list:
    """
    Fetch the full leaderboard from the community server.
    Returns a list of player dicts, or [] on failure / disabled.
    """
    cfg = load_config()
    if not cfg.get("shared_db_enabled"):
        return []
    if cfg.get("shared_db_backend", "telegram") != "webhook":
        return []
    url = cfg.get("shared_db_webhook_url", "").rstrip("/")
    if not url:
        return []
    try:
        r = requests.get(url + "/api/leaderboard", timeout=10)
        r.raise_for_status()
        return r.json().get("players", [])
    except Exception as e:
        logger.warning(f"Leaderboard fetch error: {e}")
        return []

