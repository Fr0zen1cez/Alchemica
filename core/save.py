"""
save.py — SQLite-backed save system for Alchemica.

Replaces JSON file-per-slot persistence with a single SQLite database.

Layout
------
  saves/alchemica_saves.db
    slots          — one row per save slot (metadata, achievements, etc.)
    items          — one row per (slot, world_id, item_key)
    combinations   — one row per (slot, world_id, combo_key)
    discovery_log  — append-only; new rows only ever INSERTed, never rewritten
    quest_progress — tiny; replaced wholesale on each write

Public API is identical to the old JSON version — app.py needs zero changes.

Migration
---------
On first load for a slot, if a legacy JSON file (saves/slot_N.json) exists it
is imported into the DB automatically, then renamed to slot_N.json.migrated so
it won't be imported again.
"""

import json
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from core.logger import get_logger
from core.config import get_base_dir

logger = get_logger()

BASE_DIR    = get_base_dir()
SAVES_DIR   = BASE_DIR / "saves"
BACKUPS_DIR = BASE_DIR / "backups"
DB_PATH     = SAVES_DIR / "alchemica_saves.db"

SAVES_DIR.mkdir(exist_ok=True)
BACKUPS_DIR.mkdir(exist_ok=True)

# ── In-memory write-back cache ────────────────────────────────────────────────
_save_cache: dict   = {}
_write_lock         = threading.Lock()
_write_counts: dict = {}       # slot → total writes (backup throttle)
BACKUP_EVERY        = 25

# Append-only log tracking per (slot, world_id) → entries already in DB.
# New entries are prepended in memory; we only INSERT the delta.
_log_flushed: dict = {}

# ── World definitions ─────────────────────────────────────────────────────────

STARTER_ITEMS = {
    "air":   {"emoji": "💨", "display": "Air",   "rarity": "common", "tags": ["air", "nature", "weather"]},
    "water": {"emoji": "💧", "display": "Water", "rarity": "common", "tags": ["water", "nature", "material"]},
    "fire":  {"emoji": "🔥", "display": "Fire",  "rarity": "common", "tags": ["fire", "nature", "energy"]},
    "earth": {"emoji": "🌍", "display": "Earth", "rarity": "common", "tags": ["earth", "nature", "material"]},
}

WORLDS = [
    {
        "id": "origins", "name": "Origins", "emoji": "🌍",
        "description": "Where it all begins. Four elements, infinite possibilities.",
        "color": "#4a9eff",
        "starters": {
            "air":   {"emoji": "💨", "display": "Air",   "rarity": "common", "tags": ["air", "nature", "weather"]},
            "water": {"emoji": "💧", "display": "Water", "rarity": "common", "tags": ["water", "nature", "material"]},
            "fire":  {"emoji": "🔥", "display": "Fire",  "rarity": "common", "tags": ["fire", "nature", "energy"]},
            "earth": {"emoji": "🌍", "display": "Earth", "rarity": "common", "tags": ["earth", "nature", "material"]},
        },
        "quest": ["tree", "human", "mountain", "ocean", "life"],
        "permanent": True,
    },
    {
        "id": "mythology", "name": "Mythology", "emoji": "🏛️",
        "description": "Realm of gods, monsters, and epic tales.",
        "color": "#ffaa00",
        "starters": {
            "zeus":   {"emoji": "⚡", "display": "Zeus",   "rarity": "uncommon", "tags": ["mythical", "cosmic", "person"]},
            "fate":   {"emoji": "🎭", "display": "Fate",   "rarity": "uncommon", "tags": ["mythical", "abstract", "emotion"]},
            "chaos":  {"emoji": "🌀", "display": "Chaos",  "rarity": "uncommon", "tags": ["mythical", "abstract", "cosmic"]},
            "mortal": {"emoji": "👤", "display": "Mortal", "rarity": "common",   "tags": ["person", "history", "abstract"]},
        },
        "quest": ["olympus", "dragon", "titan", "hero", "prophecy"],
    },
    {
        "id": "medieval", "name": "Medieval", "emoji": "⚔️",
        "description": "Steel, faith, and the clash of kingdoms.",
        "color": "#cc6633",
        "starters": {
            "iron":  {"emoji": "⚙️",  "display": "Iron",  "rarity": "common", "tags": ["material", "technology", "earth"]},
            "faith": {"emoji": "✝️",  "display": "Faith", "rarity": "common", "tags": ["abstract", "history", "emotion"]},
            "wood":  {"emoji": "🪵",  "display": "Wood",  "rarity": "common", "tags": ["material", "nature", "earth"]},
            "blood": {"emoji": "🩸",  "display": "Blood", "rarity": "common", "tags": ["biology", "material", "dark"]},
        },
        "quest": ["kingdom", "knight", "plague", "alchemy", "crusade"],
    },
    {
        "id": "biology", "name": "Biology", "emoji": "🧬",
        "description": "The machinery of life, from cell to civilization.",
        "color": "#00cc66",
        "starters": {
            "cell":     {"emoji": "🔬", "display": "Cell",     "rarity": "common", "tags": ["biology", "technology", "material"]},
            "sunlight": {"emoji": "☀️",  "display": "Sunlight", "rarity": "common", "tags": ["light", "nature", "energy"]},
            "water":    {"emoji": "💧",  "display": "Water",    "rarity": "common", "tags": ["water", "nature", "material"]},
            "minerals": {"emoji": "⛏️",  "display": "Minerals", "rarity": "common", "tags": ["material", "earth", "nature"]},
        },
        "quest": ["dna", "evolution", "brain", "ecosystem", "consciousness"],
    },
    {
        "id": "space", "name": "Space Age", "emoji": "🚀",
        "description": "From Moon landings to the edge of the universe.",
        "color": "#9966ff",
        "starters": {
            "moon":   {"emoji": "🌙", "display": "Moon",   "rarity": "common", "tags": ["cosmic", "place", "light"]},
            "planet": {"emoji": "🌏", "display": "Planet", "rarity": "common", "tags": ["cosmic", "place", "earth"]},
            "sun":    {"emoji": "⭐", "display": "Sun",    "rarity": "common", "tags": ["cosmic", "energy", "light"]},
            "space":  {"emoji": "🌌", "display": "Space",  "rarity": "common", "tags": ["cosmic", "abstract", "dark"]},
        },
        "quest": ["rocket", "satellite", "black hole", "galaxy", "alien"],
    },
    {
        "id": "digital", "name": "Digital", "emoji": "💻",
        "description": "Binary dreams and silicon nightmares.",
        "color": "#00ffcc",
        "starters": {
            "code":        {"emoji": "💻", "display": "Code",        "rarity": "common", "tags": ["technology", "abstract", "material"]},
            "electricity": {"emoji": "⚡", "display": "Electricity", "rarity": "common", "tags": ["energy", "technology", "weather"]},
            "silicon":     {"emoji": "🪨", "display": "Silicon",     "rarity": "common", "tags": ["material", "technology", "earth"]},
            "data":        {"emoji": "📊", "display": "Data",        "rarity": "common", "tags": ["technology", "abstract", "material"]},
        },
        "quest": ["internet", "ai", "virus", "cryptocurrency", "matrix"],
    },
    {
        "id": "ocean", "name": "Ocean Depths", "emoji": "🌊",
        "description": "The abyss stares back. And it has tentacles.",
        "color": "#0066cc",
        "starters": {
            "coral":           {"emoji": "🪸", "display": "Coral",           "rarity": "common",   "tags": ["water", "nature", "creature"]},
            "current":         {"emoji": "🌊", "display": "Current",         "rarity": "common",   "tags": ["water", "weather", "energy"]},
            "pressure":        {"emoji": "🫧", "display": "Pressure",        "rarity": "common",   "tags": ["water", "abstract", "energy"]},
            "bioluminescence": {"emoji": "✨", "display": "Bioluminescence", "rarity": "uncommon", "tags": ["light", "biology", "nature"]},
        },
        "quest": ["kraken", "leviathan", "trench", "civilization", "pearl"],
    },
    {
        "id": "arcane", "name": "Arcane", "emoji": "🔮",
        "description": "Magic defies logic. That's rather the point.",
        "color": "#cc00ff",
        "starters": {
            "mana":    {"emoji": "🔮", "display": "Mana",    "rarity": "uncommon", "tags": ["magic", "energy", "abstract"]},
            "crystal": {"emoji": "💎", "display": "Crystal", "rarity": "common",   "tags": ["material", "magic", "earth"]},
            "shadow":  {"emoji": "🌑", "display": "Shadow",  "rarity": "common",   "tags": ["dark", "magic", "abstract"]},
            "rune":    {"emoji": "🔣", "display": "Rune",    "rarity": "uncommon", "tags": ["magic", "history", "abstract"]},
        },
        "quest": ["spell", "potion", "grimoire", "familiar", "lich"],
    },
    {
        "id": "egypt", "name": "Ancient Egypt", "emoji": "🏺",
        "description": "Pyramids, pharaohs, and gods with animal heads.",
        "color": "#ffcc00",
        "starters": {
            "sand":  {"emoji": "🏜️", "display": "Sand",  "rarity": "common", "tags": ["earth", "nature", "material"]},
            "sun":   {"emoji": "☀️",  "display": "Sun",   "rarity": "common", "tags": ["light", "energy", "cosmic"]},
            "river": {"emoji": "🌊", "display": "River", "rarity": "common", "tags": ["water", "nature", "place"]},
            "stone": {"emoji": "🪨", "display": "Stone", "rarity": "common", "tags": ["material", "earth", "nature"]},
        },
        "quest": ["pharaoh", "pyramid", "sphinx", "tomb", "curse"],
    },
    {
        "id": "apocalypse", "name": "Apocalypse", "emoji": "☢️",
        "description": "The end was just the beginning.",
        "color": "#ff3333",
        "starters": {
            "radiation": {"emoji": "☢️", "display": "Radiation", "rarity": "uncommon", "tags": ["energy", "dark", "technology"]},
            "ruin":      {"emoji": "🏚️", "display": "Ruin",      "rarity": "common",   "tags": ["place", "history", "dark"]},
            "survivor":  {"emoji": "👤", "display": "Survivor",  "rarity": "common",   "tags": ["person", "abstract", "history"]},
            "mutation":  {"emoji": "🧬", "display": "Mutation",  "rarity": "uncommon", "tags": ["biology", "dark", "abstract"]},
        },
        "quest": [],
    },
]

WORLDS_BY_ID = {w["id"]: w for w in WORLDS}


# ── SQLite schema ─────────────────────────────────────────────────────────────

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;

CREATE TABLE IF NOT EXISTS slots (
    slot                    INTEGER PRIMARY KEY,
    name                    TEXT    NOT NULL DEFAULT 'Slot 1',
    active_world            TEXT    NOT NULL DEFAULT 'origins',
    worlds_unlocked         TEXT    NOT NULL DEFAULT '["origins"]',
    all_worlds_unlocked     INTEGER NOT NULL DEFAULT 0,
    void_world_unlocked     INTEGER NOT NULL DEFAULT 0,
    achievements            TEXT    NOT NULL DEFAULT '{}',
    collections             TEXT    NOT NULL DEFAULT '{}',
    trash                   TEXT    NOT NULL DEFAULT '{}',
    daily_combo             TEXT    NOT NULL DEFAULT '{}',
    seed                    TEXT,
    speedrun_history        TEXT    NOT NULL DEFAULT '[]',
    challenge_history       TEXT    NOT NULL DEFAULT '[]',
    weekly_challenge_scores TEXT    NOT NULL DEFAULT '{}',
    updated_at              TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    slot               INTEGER NOT NULL,
    world_id           TEXT    NOT NULL,
    item_key           TEXT    NOT NULL,
    emoji              TEXT    NOT NULL DEFAULT '✨',
    display            TEXT    NOT NULL,
    rarity             TEXT    NOT NULL DEFAULT 'common',
    is_first_discovery INTEGER NOT NULL DEFAULT 0,
    pinned             INTEGER NOT NULL DEFAULT 0,
    tags               TEXT    NOT NULL DEFAULT '[]',
    discovered_at      TEXT    NOT NULL,
    notes              TEXT    NOT NULL DEFAULT '',
    collection_ids     TEXT    NOT NULL DEFAULT '[]',
    trophy             TEXT    NOT NULL DEFAULT '{}',
    lore               TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (slot, world_id, item_key)
);

CREATE TABLE IF NOT EXISTS combinations (
    slot      INTEGER NOT NULL,
    world_id  TEXT    NOT NULL,
    combo_key TEXT    NOT NULL,
    result    TEXT    NOT NULL,
    PRIMARY KEY (slot, world_id, combo_key)
);
CREATE INDEX IF NOT EXISTS idx_combos_lookup
    ON combinations (slot, world_id, combo_key);

CREATE TABLE IF NOT EXISTS discovery_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    slot         INTEGER NOT NULL,
    world_id     TEXT    NOT NULL,
    item_a       TEXT    NOT NULL,
    item_b       TEXT    NOT NULL,
    result       TEXT    NOT NULL,
    emoji_a      TEXT    NOT NULL DEFAULT '',
    emoji_b      TEXT    NOT NULL DEFAULT '',
    emoji_result TEXT    NOT NULL DEFAULT '',
    timestamp    TEXT    NOT NULL,
    source       TEXT    NOT NULL DEFAULT 'ai'
);
CREATE INDEX IF NOT EXISTS idx_log_slot_world
    ON discovery_log (slot, world_id, id DESC);

CREATE TABLE IF NOT EXISTS quest_progress (
    slot     INTEGER NOT NULL,
    world_id TEXT    NOT NULL,
    item_key TEXT    NOT NULL,
    PRIMARY KEY (slot, world_id, item_key)
);

CREATE TABLE IF NOT EXISTS quest_completed (
    slot     INTEGER NOT NULL,
    world_id TEXT    NOT NULL,
    PRIMARY KEY (slot, world_id)
);
"""

_db_init_done = False
_db_init_lock = threading.Lock()


def _ensure_db():
    global _db_init_done
    if _db_init_done:
        return
    with _db_init_lock:
        if _db_init_done:
            return
        c = _conn()
        c.executescript(_SCHEMA)
        c.commit()
        c.close()
        _db_init_done = True


def _conn() -> sqlite3.Connection:
    SAVES_DIR.mkdir(exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


# ── World data helpers ────────────────────────────────────────────────────────

def _make_world_data(world_id: str) -> dict:
    world = WORLDS_BY_ID.get(world_id, WORLDS[0])
    now   = datetime.utcnow().isoformat()
    items = {}
    for k, v in world["starters"].items():
        items[k] = {
            "emoji": v["emoji"], "display": v["display"],
            "rarity": v["rarity"], "is_first_discovery": False,
            "pinned": False, "tags": v.get("tags", []),
            "discovered_at": now, "notes": "", "collection_ids": [],
            "trophy": {"speedrun_best": None, "challenge_best": None},
            "lore": "",
        }
    return {
        "items": items, "combinations": {},
        "discovery_log": [], "quest_progress": [],
        "quest_completed": False,
    }


def default_save() -> dict:
    origins_data = _make_world_data("origins")
    return {
        "name": "Slot 1",
        "active_world": "origins",
        "worlds_unlocked": ["origins"],
        "all_worlds_unlocked": False,
        "void_world_unlocked": False,
        "world_data": {"origins": origins_data},
        # Top-level mirrors kept for app.py compatibility
        "items":         {k: v.copy() for k, v in origins_data["items"].items()},
        "combinations":  {},
        "discovery_log": [],
        "achievements": {},
        "collections":  {},
        "trash":        {},
        "daily_combo":  {"date": None, "item_a": None, "item_b": None, "completed": False},
        "seed":         None,
        "speedrun_history":        [],
        "challenge_history":       [],
        "weekly_challenge_scores": {},
    }


# ── Migration helpers (public, used by app.py) ────────────────────────────────

def migrate_save(data: dict):
    """Ensure a loaded dict has all expected keys (forward-compat)."""
    changed = False

    if "world_data" not in data:
        old_items  = data.get("items", {})
        old_combos = data.get("combinations", {})
        old_log    = data.get("discovery_log", [])
        for item in old_items.values():
            item.setdefault("lore", "")
        data["world_data"] = {"origins": {
            "items": old_items, "combinations": old_combos,
            "discovery_log": old_log,
            "quest_progress": [], "quest_completed": False,
        }}
        changed = True

    data.setdefault("active_world", "origins")
    data.setdefault("worlds_unlocked", ["origins"])
    data.setdefault("all_worlds_unlocked", False)
    data.setdefault("void_world_unlocked", False)
    data.setdefault("weekly_challenge_scores", {})

    active = data.get("active_world", "origins")
    if active not in data["world_data"]:
        data["world_data"][active] = _make_world_data(active)
        changed = True

    for wd in data["world_data"].values():
        for item in wd.get("items", {}).values():
            if "lore" not in item:
                item["lore"] = ""
                changed = True

    return data, changed


def sync_active_world(save: dict):
    """Mirror world_data[active_world] → top-level items/combinations/log."""
    active = save.get("active_world", "origins")
    wd = save.get("world_data", {}).get(active, {})
    save["items"]         = wd.get("items", {})
    save["combinations"]  = wd.get("combinations", {})
    save["discovery_log"] = wd.get("discovery_log", [])


def flush_active_world(save: dict):
    """Flush top-level data back into world_data[active_world]."""
    active = save.get("active_world", "origins")
    if "world_data" not in save:
        save["world_data"] = {}
    if active not in save["world_data"]:
        save["world_data"][active] = _make_world_data(active)
    save["world_data"][active]["items"]         = save.get("items", {})
    save["world_data"][active]["combinations"]  = save.get("combinations", {})
    save["world_data"][active]["discovery_log"] = save.get("discovery_log", [])


# ── DB read ───────────────────────────────────────────────────────────────────

def _load_from_db(slot: int) -> dict | None:
    """Reconstruct a full save dict from SQLite. Returns None if not found."""
    _ensure_db()
    c = _conn()
    try:
        row = c.execute("SELECT * FROM slots WHERE slot=?", (slot,)).fetchone()
        if row is None:
            return None

        data = {
            "name":                    row["name"],
            "active_world":            row["active_world"],
            "worlds_unlocked":         json.loads(row["worlds_unlocked"]),
            "all_worlds_unlocked":     bool(row["all_worlds_unlocked"]),
            "void_world_unlocked":     bool(row["void_world_unlocked"]),
            "achievements":            json.loads(row["achievements"]),
            "collections":             json.loads(row["collections"]),
            "trash":                   json.loads(row["trash"]),
            "daily_combo":             json.loads(row["daily_combo"]),
            "seed":                    row["seed"],
            "speedrun_history":        json.loads(row["speedrun_history"]),
            "challenge_history":       json.loads(row["challenge_history"]),
            "weekly_challenge_scores": json.loads(row["weekly_challenge_scores"]),
            "world_data": {},
        }

        # Items
        items_by_world: dict = {}
        for r in c.execute("SELECT * FROM items WHERE slot=?", (slot,)).fetchall():
            items_by_world.setdefault(r["world_id"], {})[r["item_key"]] = {
                "emoji":             r["emoji"],
                "display":           r["display"],
                "rarity":            r["rarity"],
                "is_first_discovery": bool(r["is_first_discovery"]),
                "pinned":            bool(r["pinned"]),
                "tags":              json.loads(r["tags"]),
                "discovered_at":     r["discovered_at"],
                "notes":             r["notes"],
                "collection_ids":    json.loads(r["collection_ids"]),
                "trophy":            json.loads(r["trophy"]),
                "lore":              r["lore"],
            }

        # Combinations
        combos_by_world: dict = {}
        for r in c.execute("SELECT * FROM combinations WHERE slot=?", (slot,)).fetchall():
            combos_by_world.setdefault(r["world_id"], {})[r["combo_key"]] = r["result"]

        # Discovery log — newest first, cap at 500
        logs_by_world: dict = {}
        for r in c.execute(
            "SELECT * FROM discovery_log WHERE slot=? ORDER BY id DESC LIMIT 500",
            (slot,)
        ).fetchall():
            logs_by_world.setdefault(r["world_id"], []).append({
                "item_a": r["item_a"], "item_b": r["item_b"],
                "result": r["result"],
                "emoji_a": r["emoji_a"], "emoji_b": r["emoji_b"],
                "emoji_result": r["emoji_result"],
                "timestamp": r["timestamp"], "source": r["source"],
            })

        # Quest progress
        qp_by_world: dict = {}
        for r in c.execute(
            "SELECT world_id, item_key FROM quest_progress WHERE slot=?", (slot,)
        ).fetchall():
            qp_by_world.setdefault(r["world_id"], []).append(r["item_key"])

        qc_worlds = {
            r["world_id"] for r in c.execute(
                "SELECT world_id FROM quest_completed WHERE slot=?", (slot,)
            ).fetchall()
        }

        # Assemble world_data
        all_wids = (
            set(items_by_world) | set(combos_by_world) |
            set(logs_by_world)  | set(qp_by_world)
        )
        for wid in all_wids:
            data["world_data"][wid] = {
                "items":          items_by_world.get(wid, {}),
                "combinations":   combos_by_world.get(wid, {}),
                "discovery_log":  logs_by_world.get(wid, []),
                "quest_progress": qp_by_world.get(wid, []),
                "quest_completed": wid in qc_worlds,
            }

        # Seed the append-only log counters
        for r in c.execute(
            "SELECT world_id, COUNT(*) as cnt FROM discovery_log WHERE slot=? GROUP BY world_id",
            (slot,)
        ).fetchall():
            _log_flushed[(slot, r["world_id"])] = r["cnt"]

        sync_active_world(data)
        return data
    finally:
        c.close()


# ── DB write ──────────────────────────────────────────────────────────────────

def _write_to_db(slot: int, data: dict):
    """Persist save dict to SQLite. Runs in a background thread."""
    _ensure_db()
    now = datetime.utcnow().isoformat()

    with _write_lock:
        c = _conn()
        try:
            # Slot metadata
            c.execute("""
                INSERT OR REPLACE INTO slots
                  (slot, name, active_world, worlds_unlocked, all_worlds_unlocked,
                   void_world_unlocked, achievements, collections, trash, daily_combo,
                   seed, speedrun_history, challenge_history, weekly_challenge_scores,
                   updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                slot,
                data.get("name", f"Slot {slot}"),
                data.get("active_world", "origins"),
                json.dumps(data.get("worlds_unlocked", ["origins"])),
                int(bool(data.get("all_worlds_unlocked", False))),
                int(bool(data.get("void_world_unlocked", False))),
                json.dumps(data.get("achievements", {})),
                json.dumps(data.get("collections", {})),
                json.dumps(data.get("trash", {})),
                json.dumps(data.get("daily_combo", {})),
                data.get("seed"),
                json.dumps(data.get("speedrun_history", [])),
                json.dumps(data.get("challenge_history", [])),
                json.dumps(data.get("weekly_challenge_scores", {})),
                now,
            ))

            for wid, wd in data.get("world_data", {}).items():
                # Items
                for key, item in wd.get("items", {}).items():
                    c.execute("""
                        INSERT OR REPLACE INTO items
                          (slot, world_id, item_key, emoji, display, rarity,
                           is_first_discovery, pinned, tags, discovered_at,
                           notes, collection_ids, trophy, lore)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        slot, wid, key,
                        item.get("emoji", "✨"),
                        item.get("display", key.title()),
                        item.get("rarity", "common"),
                        int(bool(item.get("is_first_discovery", False))),
                        int(bool(item.get("pinned", False))),
                        json.dumps(item.get("tags", [])),
                        item.get("discovered_at", now),
                        item.get("notes", ""),
                        json.dumps(item.get("collection_ids", [])),
                        json.dumps(item.get("trophy", {})),
                        item.get("lore", ""),
                    ))

                # Combinations
                for ckey, result in wd.get("combinations", {}).items():
                    c.execute("""
                        INSERT OR REPLACE INTO combinations (slot, world_id, combo_key, result)
                        VALUES (?,?,?,?)
                    """, (slot, wid, ckey, result))

                # Discovery log — append only
                log = wd.get("discovery_log", [])
                already = _log_flushed.get((slot, wid), 0)
                new_count = len(log) - already
                if new_count > 0:
                    # log is newest-first; insert oldest-first for correct IDs
                    new_entries = list(reversed(log[:new_count]))
                    c.executemany("""
                        INSERT INTO discovery_log
                          (slot, world_id, item_a, item_b, result,
                           emoji_a, emoji_b, emoji_result, timestamp, source)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, [
                        (
                            slot, wid,
                            e.get("item_a", ""), e.get("item_b", ""),
                            e.get("result", ""),
                            e.get("emoji_a", ""), e.get("emoji_b", ""),
                            e.get("emoji_result", ""),
                            e.get("timestamp", now),
                            e.get("source", "ai"),
                        )
                        for e in new_entries
                    ])
                    _log_flushed[(slot, wid)] = len(log)

                # Quest progress (tiny — replace wholesale)
                c.execute(
                    "DELETE FROM quest_progress WHERE slot=? AND world_id=?", (slot, wid)
                )
                for qi in wd.get("quest_progress", []):
                    c.execute(
                        "INSERT OR IGNORE INTO quest_progress (slot, world_id, item_key) VALUES (?,?,?)",
                        (slot, wid, qi)
                    )
                if wd.get("quest_completed"):
                    c.execute(
                        "INSERT OR IGNORE INTO quest_completed (slot, world_id) VALUES (?,?)",
                        (slot, wid)
                    )
                else:
                    c.execute(
                        "DELETE FROM quest_completed WHERE slot=? AND world_id=?",
                        (slot, wid)
                    )

            c.commit()

        except Exception as e:
            logger.error(f"SQLite write error slot {slot}: {e}")
            try:
                c.rollback()
            except Exception:
                pass
        finally:
            c.close()

    # Optional mirror-folder copy
    try:
        from core.config import load_config
        mirror = load_config().get("mirror_folder_path", "").strip()
        if mirror:
            import pathlib
            dest = pathlib.Path(mirror)
            if dest.is_dir():
                shutil.copy2(str(DB_PATH), str(dest / "alchemica_saves.db"))
    except Exception as e:
        logger.warning(f"Mirror copy failed slot {slot}: {e}")


# ── JSON migration (runs once per slot) ──────────────────────────────────────

def _migrate_json_if_needed(slot: int):
    json_path = SAVES_DIR / f"slot_{slot}.json"
    done_path = SAVES_DIR / f"slot_{slot}.json.migrated"
    if done_path.exists() or not json_path.exists():
        return
    logger.info(f"Migrating JSON slot {slot} → SQLite …")
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data, _ = migrate_save(data)
        sync_active_world(data)
        _write_to_db(slot, data)
        json_path.rename(done_path)
        logger.info(f"Slot {slot} migration done. Old file → {done_path.name}")
    except Exception as e:
        logger.error(f"JSON migration failed slot {slot}: {e}")


# ── Backup ────────────────────────────────────────────────────────────────────

def backup_save(slot: int):
    """Copy the SQLite DB to backups/. Keeps max 3 backups per slot."""
    if not DB_PATH.exists():
        return
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    try:
        existing = sorted(
            BACKUPS_DIR.glob(f"slot_{slot}.bak*.db"),
            key=lambda x: x.stat().st_mtime,
        )
        if len(existing) >= 3:
            for old in existing[:-2]:
                old.unlink(missing_ok=True)
        dest = BACKUPS_DIR / f"slot_{slot}.bak_{ts}.db"
        shutil.copy2(str(DB_PATH), str(dest))
    except Exception as e:
        logger.error(f"Backup error slot {slot}: {e}")


# ── Public API ────────────────────────────────────────────────────────────────

def get_save_path(slot: int) -> Path:
    """Returns DB path. Kept for callers that check file existence."""
    return DB_PATH


def load_save(slot: int, force_reload: bool = False) -> dict:
    if not force_reload and slot in _save_cache:
        return _save_cache[slot]

    _ensure_db()
    _migrate_json_if_needed(slot)

    data = _load_from_db(slot)
    if data is None:
        data = default_save()
        data["name"] = f"Slot {slot}"
        _write_to_db(slot, data)

    data, changed = migrate_save(data)
    if changed:
        _write_to_db(slot, data)

    sync_active_world(data)
    _save_cache[slot] = data
    return data


def write_save(slot: int, data: dict, backup: bool = True) -> None:
    """Update in-memory cache immediately; persist to SQLite in background."""
    _save_cache[slot] = data

    count = _write_counts.get(slot, 0) + 1
    _write_counts[slot] = count

    if backup and (count % BACKUP_EVERY == 0):
        backup_save(slot)

    threading.Thread(target=_write_to_db, args=(slot, data), daemon=True).start()
