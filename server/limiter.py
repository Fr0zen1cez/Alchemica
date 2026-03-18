"""
limiter.py — Rate limiting, HMAC request signing, and combo validation.
"""

import hashlib
import hmac
import os
import re
import time
import unicodedata
from collections import defaultdict
from threading import Lock

# ── HMAC ──────────────────────────────────────────────────────────────────────
# The server reads its secret from an env var.
# The client uses the same secret (baked in). This stops random internet bots
# from hitting the endpoint — anyone who decompiles the EXE can find it, but
# that's an accepted trade-off given the real protection is server-side.

HMAC_SECRET: bytes = os.environ.get("HMAC_SECRET", "alchemica-community-v1").encode()
TIMESTAMP_TOLERANCE = 45  # seconds — reject anything older

# ── Rate limits ───────────────────────────────────────────────────────────────
SUBMIT_LIMIT  = 10   # submits  per IP per minute
LOOKUP_LIMIT  = 120  # lookups  per IP per minute
GLOBAL_SUBMIT = 60   # global submits per minute (protects Telegram + SQLite)

_submit_counts  = defaultdict(list)  # ip_hash -> [timestamps]
_lookup_counts  = defaultdict(list)
_global_submits = []
_lock = Lock()


def hash_ip(ip: str) -> str:
    return hashlib.sha256(ip.encode()).hexdigest()[:20]


def _prune(ts_list, window=60):
    cutoff = time.time() - window
    return [t for t in ts_list if t > cutoff]


def check_rate_limit(ip: str, endpoint_type: str):
    """
    Returns (allowed: bool, ip_hash: str).
    endpoint_type: 'submit' | 'lookup'
    """
    ip_hash = hash_ip(ip)
    now = time.time()

    with _lock:
        if endpoint_type == "submit":
            _submit_counts[ip_hash] = _prune(_submit_counts[ip_hash])
            _global_submits[:] = _prune(_global_submits)

            if len(_submit_counts[ip_hash]) >= SUBMIT_LIMIT:
                return False, ip_hash
            if len(_global_submits) >= GLOBAL_SUBMIT:
                return False, ip_hash

            _submit_counts[ip_hash].append(now)
            _global_submits.append(now)

        else:  # lookup
            _lookup_counts[ip_hash] = _prune(_lookup_counts[ip_hash])
            if len(_lookup_counts[ip_hash]) >= LOOKUP_LIMIT:
                return False, ip_hash
            _lookup_counts[ip_hash].append(now)

    return True, ip_hash


def verify_hmac(method: str, path: str, timestamp_str: str,
                body_hash: str, signature: str) -> bool:
    """
    Verify a request signature sent by the client.
    message = "{METHOD}:{path}:{timestamp}:{sha256(body)}"
    """
    try:
        ts = float(timestamp_str)
        if abs(time.time() - ts) > TIMESTAMP_TOLERANCE:
            return False  # stale / replay attack
        message = f"{method}:{path}:{timestamp_str}:{body_hash}".encode()
        expected = hmac.new(HMAC_SECRET, message, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


# ── Combo validation ──────────────────────────────────────────────────────────

_VALID_RARITY = {"common", "uncommon", "rare", "legendary"}
_RESULT_RE    = re.compile(r"^[a-zA-Z][a-zA-Z '\-]{0,58}[a-zA-Z]$|^[a-zA-Z]{1,2}$")


def _is_single_emoji(s: str) -> bool:
    """Rough check: non-empty, short, contains at least one emoji-range char."""
    if not s or len(s) > 8:
        return False
    for ch in s:
        cat = unicodedata.category(ch)
        if cat.startswith("S") or cat.startswith("P") or ord(ch) > 0x2000:
            return True
    return False


def validate_combo(item_a, item_b, result, emoji, rarity):
    """
    Returns (valid: bool, reason: str).
    All validation happens server-side — the client can't influence it.
    """
    # ── Presence & length ────────────────────────────────────────
    if not all([item_a, item_b, result, emoji, rarity]):
        return False, "Missing required field"

    for label, val, maxlen in [
        ("item_a",  item_a,  50),
        ("item_b",  item_b,  50),
        ("result",  result,  60),
        ("emoji",   emoji,    8),
    ]:
        if len(val) > maxlen:
            return False, f"{label} too long (max {maxlen})"

    # ── Rarity ───────────────────────────────────────────────────
    if rarity not in _VALID_RARITY:
        return False, f"Invalid rarity '{rarity}'"

    # ── Result format ─────────────────────────────────────────────
    stripped = result.strip()
    if not _RESULT_RE.match(stripped):
        return False, "Result contains invalid characters"
    word_count = len(stripped.split())
    if word_count > 4:
        return False, "Result must be 1–4 words"

    # ── Result cannot equal either input ─────────────────────────
    a_low = item_a.lower().strip()
    b_low = item_b.lower().strip()
    r_low = stripped.lower()
    if r_low == a_low or r_low == b_low:
        return False, "Result cannot be the same as an input"

    # ── Emoji ─────────────────────────────────────────────────────
    if not _is_single_emoji(emoji):
        return False, "Invalid emoji"

    return True, "ok"
