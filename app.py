"""
Alchemica — An element-combination crafting game served via Flask.
"""

import json, time, hashlib, re, shutil, multiprocessing, base64, zlib, random, sys, threading
from datetime import datetime, date
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory

from core import (
    get_logger,
    load_config, save_config, get_base_dir, get_resource_dir,
    load_save, write_save, backup_save, default_save, get_save_path, STARTER_ITEMS,
    WORLDS, WORLDS_BY_ID, _make_world_data, sync_active_world, flush_active_world,
    ai_combine, ai_generate_tags, get_ai_stats, AIError,
    discover_plugins, get_plugin_combos, get_plugin_extra_items, notify_plugins_combination, get_loaded_plugins
)
from core.config import DEFAULT_CONFIG
from core.ai import DEFAULT_COMBINE_PROMPT, DEFAULT_TAGS_PROMPT, ai_generate_worlds
import core.shared_db as shared_db

logger = get_logger()

RESOURCE_DIR = get_resource_dir()
app = Flask(
    __name__,
    template_folder=str(RESOURCE_DIR / "templates"),
    static_folder=str(RESOURCE_DIR / "assets"),
)

# ── Global in-memory combination cache (key → result_name) ───────────────────
# Survives across API calls within a server session — saves AI calls on
# combinations already computed by any save slot, making repeats near-instant.
_GLOBAL_COMBO_CACHE: dict = {}
_GLOBAL_COMBO_LOCK = threading.Lock()

def _gcache_get(key):
    with _GLOBAL_COMBO_LOCK:
        return _GLOBAL_COMBO_CACHE.get(key)

def _gcache_set(key, result_name):
    with _GLOBAL_COMBO_LOCK:
        _GLOBAL_COMBO_CACHE[key] = result_name
BASE_DIR = get_base_dir()
SAVES_DIR = BASE_DIR / "saves"
BACKUPS_DIR = BASE_DIR / "backups"
PLUGINS_DIR = BASE_DIR / "plugins"
ASSETS_DIR = BASE_DIR / "assets"
CONFIG_PATH = BASE_DIR / "config.json"

ASSETS_DIR.mkdir(exist_ok=True)

# Set to True by desktop_app.py so /api/server-info can report is_desktop_app correctly
_DESKTOP_APP = False

# ── Challenge/Weekly pool (250 items) ─────────────────────────────────────────
CHALLENGE_POOL = [
    "Steam","Mud","Lava","Rain","Cloud","Ice","Snow","Dust","Sand","Wind",
    "Storm","Thunder","Lightning","Ocean","River","Volcano","Earthquake","Tsunami","Hurricane","Tornado",
    "Glacier","Swamp","Desert","Forest","Jungle","Mountain","Valley","Cave","Crystal","Diamond",
    "Gold","Silver","Iron","Bronze","Coal","Oil","Plastic","Glass","Cement","Brick",
    "Wood","Paper","Ash","Smoke","Fog","Rainbow","Aurora","Comet","Meteor","Asteroid",
    "Planet","Star","Galaxy","Universe","Sun","Moon","Eclipse","Tide","Gravity","Magnet",
    "Electricity","Battery","Engine","Rocket","Satellite","Robot","Computer","Internet","Phone","Camera",
    "Clock","Compass","Telescope","Microscope","Bacteria","Virus","DNA","Cell","Brain","Heart",
    "Blood","Bone","Muscle","Tree","Flower","Seed","Grass","Moss","Fungus","Coral",
    "Seaweed","Plankton","Fish","Shark","Whale","Dolphin","Octopus","Crab","Jellyfish","Eagle",
    "Owl","Penguin","Dragon","Phoenix","Unicorn","Vampire","Zombie","Ghost","Witch","Wizard",
    "Knight","Samurai","Ninja","Pirate","Astronaut","Doctor","Chef","Artist","Musician","Bread",
    "Cheese","Wine","Beer","Coffee","Tea","Soup","Pizza","Chocolate","Candy","Honey",
    "Salt","Pepper","Spice","Potion","Sword","Shield","Bow","Arrow","Bomb","Cannon",
    "Gunpowder","Dynamite","Nuke","Laser","Portal","Wormhole","Time Machine","Clone","Cyborg","Alien",
    "UFO","Matrix","Firewall","Hacker","Cryptocurrency","Black Hole","Supernova","Nebula","Quasar","Pulsar",
    "Antimatter","Dark Matter","Dimension","Paradox","Simulation","Myth","Legend","Prophecy","Curse","Blessing",
    "Spell","Rune","Scroll","Tome","Artifact","Relic","Fossil","Amber","Pearl","Ruby",
    "Emerald","Sapphire","Opal","Obsidian","Quartz","Marble","Granite","Lava Rock","Meteorite","Stardust",
    "Ether","Void","Chaos","Order","Balance","Harmony","Discord","Illusion","Dream","Nightmare",
    "Memory","Echo","Shadow","Mirror","Prism","Lens","Spectrum","Frequency","Vibration","Resonance",
    "Silence","Explosion","Implosion","Vortex","Singularity","Entropy","Evolution","Mutation","Hybrid","Chimera",
    "Golem","Titan","Kraken","Leviathan","Behemoth","Sphinx","Minotaur","Centaur","Mermaid","Siren",
    "Banshee","Wraith","Lich","Paladin","Warlock","Druid","Shaman","Monk","Jester","Alchemist",
]

# ── Holiday Worlds (10-day windows, day 5 = the holiday itself) ───────────────
HOLIDAY_WINDOWS = [
    {
        "id": "halloween", "name": "All Hallows' Eve", "emoji": "🎃",
        "description": "The veil thins. Ancient spirits stir from the darkness.",
        "color": "#c05a00",
        "theme_key": "halloween-night",
        "starters": {
            "pumpkin":   {"emoji": "🎃", "display": "Pumpkin",   "rarity": "common"},
            "cobweb":    {"emoji": "🕸️",  "display": "Cobweb",    "rarity": "common"},
            "bone":      {"emoji": "🦴",  "display": "Bone",      "rarity": "common"},
            "moonlight": {"emoji": "🌙",  "display": "Moonlight", "rarity": "common"},
        },
        "quest": ["ghost", "vampire", "witch", "haunted house", "black cat"],
        "start": (10, 27), "end": (11, 5),   # Oct 31 = day 5
    },
    {
        "id": "christmas", "name": "Winter Wonderland", "emoji": "🎄",
        "description": "A magical season of gifts, warmth, and wonder.",
        "color": "#1a6e32",
        "theme_key": "winter-wonderland",
        "starters": {
            "snow":       {"emoji": "❄️",  "display": "Snow",       "rarity": "common"},
            "gift":       {"emoji": "🎁",  "display": "Gift",       "rarity": "common"},
            "candy cane": {"emoji": "🍬",  "display": "Candy Cane", "rarity": "common"},
            "tinsel":     {"emoji": "✨",  "display": "Tinsel",     "rarity": "common"},
        },
        "quest": ["christmas tree", "santa claus", "reindeer", "snowman", "christmas miracle"],
        "start": (12, 21), "end": (12, 30),  # Dec 25 = day 5
    },
    {
        "id": "new_year", "name": "New Year's Eve", "emoji": "🎆",
        "description": "Fireworks, hope, and a fresh beginning.",
        "color": "#5a0d9e",
        "theme_key": "new-year-blaze",
        "starters": {
            "firework":  {"emoji": "🎆",  "display": "Firework",  "rarity": "common"},
            "champagne": {"emoji": "🍾",  "display": "Champagne", "rarity": "common"},
            "countdown": {"emoji": "⏳",  "display": "Countdown", "rarity": "common"},
            "confetti":  {"emoji": "🎊",  "display": "Confetti",  "rarity": "common"},
        },
        "quest": ["new year", "resolution", "celebration", "midnight", "fresh start"],
        "start": (12, 28), "end": (1, 6),   # Jan 1 = day 5 (wraps year)
    },
    {
        "id": "valentines", "name": "Heart's Domain", "emoji": "💕",
        "description": "Love is the rarest element of all.",
        "color": "#c41c7e",
        "theme_key": "valentines-rose",
        "starters": {
            "rose":      {"emoji": "🌹",  "display": "Rose",      "rarity": "common"},
            "heart":     {"emoji": "❤️",  "display": "Heart",     "rarity": "common"},
            "chocolate": {"emoji": "🍫",  "display": "Chocolate", "rarity": "common"},
            "arrow":     {"emoji": "💘",  "display": "Arrow",     "rarity": "common"},
        },
        "quest": ["love", "cupid", "wedding", "soulmate", "eternal love"],
        "start": (2, 10), "end": (2, 19),   # Feb 14 = day 5
    },
    {
        "id": "st_patricks", "name": "Emerald Isle", "emoji": "🍀",
        "description": "Luck, gold, and a rainbow's promise.",
        "color": "#1a7a40",
        "theme_key": "emerald-isle-holiday",
        "starters": {
            "shamrock": {"emoji": "☘️",  "display": "Shamrock", "rarity": "common"},
            "gold":     {"emoji": "🪙",  "display": "Gold",     "rarity": "common"},
            "rainbow":  {"emoji": "🌈",  "display": "Rainbow",  "rarity": "common"},
            "mead":     {"emoji": "🍺",  "display": "Mead",     "rarity": "common"},
        },
        "quest": ["leprechaun", "pot of gold", "four leaf clover", "irish stew", "luck"],
        "start": (3, 13), "end": (3, 22),   # Mar 17 = day 5
    },
    {
        "id": "independence", "name": "Liberty's Forge", "emoji": "🗽",
        "description": "Freedom, fire, and the pursuit of discovery.",
        "color": "#1a2e7a",
        "theme_key": "liberty-forge",
        "starters": {
            "firework": {"emoji": "🎆",  "display": "Firework", "rarity": "common"},
            "eagle":    {"emoji": "🦅",  "display": "Eagle",    "rarity": "common"},
            "flag":     {"emoji": "🚩",  "display": "Flag",     "rarity": "common"},
            "liberty":  {"emoji": "🗽",  "display": "Liberty",  "rarity": "common"},
        },
        "quest": ["revolution", "freedom", "constitution", "independence", "democracy"],
        "start": (6, 30), "end": (7, 9),    # Jul 4 = day 5
    },
    {
        "id": "diwali", "name": "Festival of Lights", "emoji": "🪔",
        "description": "Light triumphs over darkness.",
        "color": "#ff8c00",
        "theme_key": "diwali-glow",
        "starters": {
            "diya":      {"emoji": "🪔",  "display": "Diya",      "rarity": "common"},
            "rangoli":   {"emoji": "🎨",  "display": "Rangoli",   "rarity": "common"},
            "marigold":  {"emoji": "🌼",  "display": "Marigold",  "rarity": "common"},
            "sparkler":  {"emoji": "🎇",  "display": "Sparkler",  "rarity": "common"},
        },
        "quest": ["lakshmi", "sweets", "lantern", "light", "prosperity"],
        "start": (10, 20), "end": (11, 15),
    },
    {
        "id": "easter", "name": "Spring Awakening", "emoji": "🐣",
        "description": "Life returns in a burst of color.",
        "color": "#ffb6c1",
        "theme_key": "easter-bloom",
        "starters": {
            "egg":        {"emoji": "🥚",  "display": "Egg",        "rarity": "common"},
            "rabbit":     {"emoji": "🐇",  "display": "Rabbit",     "rarity": "common"},
            "spring":     {"emoji": "🌸",  "display": "Spring",     "rarity": "common"},
            "chocolate":  {"emoji": "🍫",  "display": "Chocolate",  "rarity": "common"},
        },
        "quest": ["easter bunny", "chocolate egg", "rebirth", "basket", "lily"],
        "start": (3, 20), "end": (4, 25),
    },
    {
        "id": "ramadan", "name": "Crescent Moon", "emoji": "🌙",
        "description": "A time of reflection, dawn to dusk.",
        "color": "#2e0854",
        "theme_key": "ramadan-crescent",
        "starters": {
            "crescent": {"emoji": "🌙",  "display": "Crescent", "rarity": "common"},
            "date":     {"emoji": "🌴",  "display": "Date",     "rarity": "common"},
            "lantern":  {"emoji": "🏮",  "display": "Lantern",  "rarity": "common"},
            "prayer":   {"emoji": "🕌",  "display": "Prayer",   "rarity": "common"},
        },
        "quest": ["fast", "feast", "charity", "twilight", "eid"],
        "start": (3, 1), "end": (4, 10),
    },
    {
        "id": "hanukkah", "name": "Festival of Dedication", "emoji": "🕎",
        "description": "Eight days of miracles and light.",
        "color": "#000080",
        "theme_key": "hanukkah-blue",
        "starters": {
            "menorah":  {"emoji": "🕎",  "display": "Menorah",  "rarity": "common"},
            "dreidel":  {"emoji": "🎲",  "display": "Dreidel",  "rarity": "common"},
            "oil":      {"emoji": "🛢️",  "display": "Oil",      "rarity": "common"},
            "gelt":     {"emoji": "🪙",  "display": "Gelt",     "rarity": "common"},
        },
        "quest": ["miracle", "latke", "light", "eight days", "faith"],
        "start": (11, 25), "end": (12, 10),
    },
    {
        "id": "lunar_new_year", "name": "Lunar Festival", "emoji": "🐉",
        "description": "Awaken the dragon for a prosperous year.",
        "color": "#d32f2f",
        "theme_key": "lunar-red",
        "starters": {
            "dragon":       {"emoji": "🐉",  "display": "Dragon",       "rarity": "common"},
            "red lantern":  {"emoji": "🏮",  "display": "Red Lantern",  "rarity": "common"},
            "envelope":     {"emoji": "🧧",  "display": "Envelope",     "rarity": "common"},
            "firecracker":  {"emoji": "🧨",  "display": "Firecracker",  "rarity": "common"},
        },
        "quest": ["luck", "spring festival", "zodiac", "prosperity", "jade"],
        "start": (1, 20), "end": (2, 20),
    },
    {
        "id": "day_of_dead", "name": "Ancestral Spirit", "emoji": "💀",
        "description": "Remember the ones who came before.",
        "color": "#8e24aa",
        "theme_key": "ofrenda-purple",
        "starters": {
            "skull":        {"emoji": "💀",  "display": "Skull",        "rarity": "common"},
            "marigold":     {"emoji": "🌼",  "display": "Marigold",     "rarity": "common"},
            "bread":        {"emoji": "🍞",  "display": "Bread",        "rarity": "common"},
            "candle":       {"emoji": "🕯️",  "display": "Candle",       "rarity": "common"},
        },
        "quest": ["ofrenda", "ancestor", "spirit", "memory", "skeleton"],
        "start": (10, 25), "end": (11, 5),
    },
    {
        "id": "midsummer", "name": "Solstice Sun", "emoji": "☀️",
        "description": "The sun never truly sets on the longest day.",
        "color": "#ffeb3b",
        "theme_key": "solstice-gold",
        "starters": {
            "sun":          {"emoji": "☀️",  "display": "Sun",          "rarity": "common"},
            "flower crown": {"emoji": "🌺",  "display": "Flower Crown", "rarity": "common"},
            "bonfire":      {"emoji": "🔥",  "display": "Bonfire",      "rarity": "common"},
            "dance":        {"emoji": "💃",  "display": "Dance",        "rarity": "common"},
        },
        "quest": ["longest day", "fae", "magic", "solstice", "twilight"],
        "start": (6, 20), "end": (6, 25),
    },
    {
        "id": "nowruz", "name": "Persian Spring", "emoji": "🌺",
        "description": "A new day, a new year.",
        "color": "#43a047",
        "theme_key": "nowruz-green",
        "starters": {
            "sprout":   {"emoji": "🌱",  "display": "Sprout",   "rarity": "common"},
            "mirror":   {"emoji": "🪞",  "display": "Mirror",   "rarity": "common"},
            "goldfish": {"emoji": "🐟",  "display": "Goldfish", "rarity": "common"},
            "apple":    {"emoji": "🍎",  "display": "Apple",    "rarity": "common"},
        },
        "quest": ["rebirth", "equinox", "fire jump", "spring", "haft-sin"],
        "start": (3, 15), "end": (3, 25),
    },
    {
        "id": "holi", "name": "Festival of Colors", "emoji": "🎨",
        "description": "Paint the world in joy.",
        "color": "#ec407a",
        "theme_key": "holi-colors",
        "starters": {
            "color":         {"emoji": "🎨",  "display": "Color",         "rarity": "common"},
            "water balloon": {"emoji": "🎈",  "display": "Water Balloon", "rarity": "common"},
            "joy":           {"emoji": "😊",  "display": "Joy",           "rarity": "common"},
            "spring":        {"emoji": "🌸",  "display": "Spring",        "rarity": "common"},
        },
        "quest": ["rainbow", "triumph", "bonfire", "playful", "vivid"],
        "start": (2, 25), "end": (3, 15),
    },
    {
        "id": "vesak", "name": "Lotus Enlightenment", "emoji": "🪷",
        "description": "Find peace in the blooming lotus.",
        "color": "#ffeb3b",
        "theme_key": "vesak-lotus",
        "starters": {
            "lotus":      {"emoji": "🪷",  "display": "Lotus",      "rarity": "common"},
            "peace":      {"emoji": "🕊️",  "display": "Peace",      "rarity": "common"},
            "candle":     {"emoji": "🕯️",  "display": "Candle",     "rarity": "common"},
            "bodhi tree": {"emoji": "🌳",  "display": "Bodhi Tree", "rarity": "common"},
        },
        "quest": ["awakening", "nirvana", "compassion", "wisdom", "harmony"],
        "start": (4, 25), "end": (5, 25),
    },
    {
        "id": "songkran", "name": "Water Festival", "emoji": "💦",
        "description": "Wash away the old, welcome the new.",
        "color": "#03a9f4",
        "theme_key": "songkran-water",
        "starters": {
            "water":    {"emoji": "💦",  "display": "Water",    "rarity": "common"},
            "splash":   {"emoji": "🌊",  "display": "Splash",   "rarity": "common"},
            "blessing": {"emoji": "🙏",  "display": "Blessing", "rarity": "common"},
            "elephant": {"emoji": "🐘",  "display": "Elephant", "rarity": "common"},
        },
        "quest": ["purification", "respect", "new year", "street party", "wash"],
        "start": (4, 10), "end": (4, 20),
    },
    {
        "id": "onam", "name": "Harvest Festival", "emoji": "🌸",
        "description": "Welcome the king, celebrate the bounty.",
        "color": "#fbc02d",
        "theme_key": "onam-harvest",
        "starters": {
            "flower rangoli": {"emoji": "🌸",  "display": "Flower Rangoli", "rarity": "common"},
            "boat race":      {"emoji": "🛶",  "display": "Boat Race",      "rarity": "common"},
            "harvest":        {"emoji": "🌾",  "display": "Harvest",        "rarity": "common"},
            "umbrella":       {"emoji": "🌂",  "display": "Umbrella",       "rarity": "common"},
        },
        "quest": ["king mahabali", "feast", "prosperity", "kerala", "golden era"],
        "start": (8, 20), "end": (9, 10),
    },
    {
        "id": "chuseok", "name": "Autumn Eve", "emoji": "🎑",
        "description": "Beneath the glow of the harvest moon.",
        "color": "#fb8c00",
        "theme_key": "chuseok-autumn",
        "starters": {
            "full moon":  {"emoji": "🎑",  "display": "Full Moon",  "rarity": "common"},
            "persimmon":  {"emoji": "🍅",  "display": "Persimmon",  "rarity": "common"},
            "rice cake":  {"emoji": "🍘",  "display": "Rice Cake",  "rarity": "common"},
            "hanbok":     {"emoji": "👘",  "display": "Hanbok",     "rarity": "common"},
        },
        "quest": ["harvest", "ancestor", "bounty", "autumn", "connection"],
        "start": (9, 15), "end": (10, 5),
    },
    {
        "id": "bonfire_night", "name": "Guy Fawkes Night", "emoji": "🔥",
        "description": "Remember, remember the fifth of November.",
        "color": "#e65100",
        "theme_key": "bonfire-flame",
        "starters": {
            "bonfire":  {"emoji": "🔥",  "display": "Bonfire",  "rarity": "common"},
            "sparkler": {"emoji": "🎇",  "display": "Sparkler", "rarity": "common"},
            "guy":      {"emoji": "🧍",  "display": "Guy",      "rarity": "common"},
            "plot":     {"emoji": "📜",  "display": "Plot",     "rarity": "common"},
        },
        "quest": ["gunpowder", "treason", "november", "explosion", "warmth"],
        "start": (11, 1), "end": (11, 10),
    },
    {
        "id": "carnival", "name": "Grand Parade", "emoji": "🎭",
        "description": "Dance to the rhythm of the streets.",
        "color": "#ba68c8",
        "theme_key": "carnival-parade",
        "starters": {
            "mask":     {"emoji": "🎭",  "display": "Mask",     "rarity": "common"},
            "feathers": {"emoji": "🪶",  "display": "Feathers", "rarity": "common"},
            "samba":    {"emoji": "💃",  "display": "Samba",    "rarity": "common"},
            "float":    {"emoji": "🚗",  "display": "Float",    "rarity": "common"},
        },
        "quest": ["parade", "music", "celebration", "costume", "rhythm"],
        "start": (2, 10), "end": (3, 5),
    },
    {
        "id": "samhain", "name": "Celtic Harvest", "emoji": "☽",
        "description": "When the dark half of the year begins.",
        "color": "#5d4037",
        "theme_key": "samhain-dark",
        "starters": {
            "twilight": {"emoji": "☽",  "display": "Twilight", "rarity": "common"},
            "bonfire":  {"emoji": "🔥",  "display": "Bonfire",  "rarity": "common"},
            "harvest":  {"emoji": "🌾",  "display": "Harvest",  "rarity": "common"},
            "spirit":   {"emoji": "👻",  "display": "Spirit",   "rarity": "common"},
        },
        "quest": ["ancestor", "dark half", "divination", "pagan", "veil"],
        "start": (10, 25), "end": (11, 5),
    },
]

# ── Secret Void World (unlocked by discovering void/nothing/etc.) ─────────────
VOID_WORLD = {
    "id": "void",
    "name": "The Void",
    "emoji": "🌑",
    "description": "Where existence unravels. Nothing is something here.",
    "color": "#111118",
    "starters": {
        "silence":   {"emoji": "🔇", "display": "Silence",   "rarity": "uncommon"},
        "emptiness": {"emoji": "⬜", "display": "Emptiness", "rarity": "uncommon"},
        "absence":   {"emoji": "👁️", "display": "Absence",   "rarity": "uncommon"},
        "zero":      {"emoji": "0️⃣", "display": "Zero",      "rarity": "uncommon"},
    },
    "quest": ["nothing", "void", "oblivion", "antimatter", "singularity"],
}

# Words that trigger Void World unlock
VOID_TRIGGERS = {"void", "nothing", "emptiness", "nothingness", "null", "vacuum",
                 "the void", "oblivion", "non-existence", "nonexistence"}

# Pre-built set of all holiday world IDs — avoids rebuilding this set in every request
HOLIDAY_IDS = {hw["id"] for hw in HOLIDAY_WINDOWS}


def _in_holiday_window(today_m, today_d, start_m, start_d, end_m, end_d):
    """Return True if (today_m, today_d) falls within (start, end), handling year wrap."""
    today = (today_m, today_d)
    start = (start_m, start_d)
    end   = (end_m,   end_d)
    if start <= end:
        return start <= today <= end
    # Year-wrap case (e.g. Dec 28 – Jan 6)
    return today >= start or today <= end


def get_active_holiday():
    """Return the holiday world dict if today is within its 10-day window, else None."""
    t = date.today()
    for hw in HOLIDAY_WINDOWS:
        sm, sd = hw["start"]
        em, ed = hw["end"]
        if _in_holiday_window(t.month, t.day, sm, sd, em, ed):
            world = {k: v for k, v in hw.items() if k not in ("start", "end")}
            return world
    return None


def combo_key(a, b):
    return "+".join(sorted([a.lower().strip(), b.lower().strip()]))

discover_plugins()

# ── Weekly challenges helper ───────────────────────────────────────────────────
def get_week_key():
    d = date.today()
    return d.isocalendar()[:2]  # (year, week)

def get_weekly_challenges():
    year, week = get_week_key()
    seed_str = f"{year}-{week}"
    h = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)
    indices = []
    used = set()
    i = 0
    while len(indices) < 5:
        idx = (h + i * 37) % len(CHALLENGE_POOL)
        if idx not in used:
            used.add(idx)
            indices.append(idx)
        i += 1
    return {
        "week": seed_str,
        "challenges": [{"id": i, "target": CHALLENGE_POOL[idx]} for i, idx in enumerate(indices)],
    }

# ── Recipe path helper ────────────────────────────────────────────────────────
def build_recipe_tree(item_key, combinations, items, visited=None, depth=0):
    """Build a deduplicated recipe tree. shared ingredients appear once."""
    if visited is None:
        visited = set()
    if depth > 12 or item_key in visited:
        return None
    visited = visited | {item_key}

    item_data = items.get(item_key, {})
    node = {
        "key":     item_key,
        "display": item_data.get("display", item_key.title()),
        "emoji":   item_data.get("emoji", "✨"),
        "rarity":  item_data.get("rarity", "common"),
        "parents": None,
    }

    for combo_k, result in combinations.items():
        if result == item_key:
            parts = combo_k.split("+")
            if len(parts) == 2:
                a, b = parts
                node["parents"] = {
                    "a": build_recipe_tree(a, combinations, items, visited, depth + 1),
                    "b": build_recipe_tree(b, combinations, items, visited, depth + 1),
                }
                break
    return node


def flatten_recipe_steps(tree):
    """Convert a recipe tree into an ordered list of unique steps (bottom-up)."""
    steps = []
    seen = set()

    def walk(node):
        if not node or not node.get("parents"):
            return
        pa = node["parents"]["a"]
        pb = node["parents"]["b"]
        walk(pa)
        walk(pb)
        key = node["key"]
        if key not in seen:
            seen.add(key)
            steps.append({
                "result":  node["display"],
                "emoji":   node["emoji"],
                "rarity":  node["rarity"],
                "input_a": pa["display"] if pa else "?",
                "emoji_a": pa["emoji"]   if pa else "✨",
                "input_b": pb["display"] if pb else "?",
                "emoji_b": pb["emoji"]   if pb else "✨",
                "is_base_a": pa is None or pa.get("parents") is None,
                "is_base_b": pb is None or pb.get("parents") is None,
            })

    walk(tree)
    return steps

# ── Effective worlds list (default or AI-generated) ───────────────────────────
def get_effective_worlds(save=None):
    """Return the world list for the current save.
    Order: [Origins, ...9 standard/AI worlds, holiday world (if active), void world (if unlocked)]
    Holiday worlds are never replaced by AI world generation.
    """
    cfg = load_config()

    if save is not None:
        ai_worlds = save.get("monthly_worlds_data")
        base = [WORLDS[0]] + ai_worlds[:9] if ai_worlds else list(WORLDS)
    else:
        base = list(WORLDS)

    # Holiday worlds — injected after main worlds, never overwritten by AI gen
    # They are now all accessible at all times
    if cfg.get("holiday_worlds_enabled", True):
        for hw in HOLIDAY_WINDOWS:
            world = {k: v for k, v in hw.items() if k not in ("start", "end")}
            base.append(world)

    # Void World — secret, unlocked per-save
    if save is not None and save.get("void_world_unlocked"):
        base = base + [VOID_WORLD]

    return base

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify(load_config())

@app.route("/api/config", methods=["POST"])
def api_set_config():
    cfg = load_config()
    data = request.json
    for k, v in data.items():
        if k in DEFAULT_CONFIG:
            cfg[k] = v
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/mirror-folder/validate", methods=["POST"])
def api_mirror_folder_validate():
    """Check a folder path is a real writable directory (for Drive folder sync)."""
    path = (request.json or {}).get("path", "").strip()
    if not path:
        return jsonify({"ok": False, "error": "No path provided"})
    import pathlib, os
    p = pathlib.Path(path)
    if not p.exists():
        return jsonify({"ok": False, "error": "Folder does not exist"})
    if not p.is_dir():
        return jsonify({"ok": False, "error": "Path is not a folder"})
    if not os.access(p, os.W_OK):
        return jsonify({"ok": False, "error": "Folder is not writable"})
    return jsonify({"ok": True, "resolved": str(p.resolve())})

@app.route("/api/save", methods=["GET"])
def api_get_save():
    cfg = load_config()
    slot = request.args.get("slot", cfg["active_slot"], type=int)
    data = load_save(slot)
    sync_active_world(data)   # ensure top-level items mirror the active world
    return jsonify(data)

@app.route("/api/save", methods=["POST"])
def api_set_save():
    cfg = load_config()
    slot = request.args.get("slot", cfg["active_slot"], type=int)
    data = request.json
    # If frontend sends updated top-level items/combos, flush them to world_data
    flush_active_world(data)
    write_save(slot, data)
    return jsonify({"ok": True})

@app.route("/api/slots", methods=["GET"])
def api_get_slots():
    slots = []
    for i in range(1, 6):
        p = get_save_path(i)
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    d = json.load(f)
                slots.append({
                    "slot": i,
                    "name": d.get("name", f"Slot {i}"),
                    "item_count": len(d.get("items", {})),
                    "last_modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
                })
            except:
                slots.append({"slot": i, "name": f"Slot {i}", "item_count": 0, "last_modified": None})
        else:
            slots.append({"slot": i, "name": f"Slot {i}", "item_count": 0, "last_modified": None})
    return jsonify(slots)

@app.route("/api/slot/rename", methods=["POST"])
def api_rename_slot():
    data = request.json
    slot = data.get("slot", 1)
    name = data.get("name", f"Slot {slot}")
    s = load_save(slot)
    s["name"] = name
    write_save(slot, s)
    return jsonify({"ok": True})

@app.route("/api/slot/duplicate", methods=["POST"])
def api_duplicate_slot():
    data = request.json
    src = data.get("source", 1)
    dest = data.get("dest")
    if not dest:
        for i in range(1, 6):
            if not get_save_path(i).exists():
                dest = i
                break
    if not dest:
        return jsonify({"error": "No empty slot available"}), 400
    s = load_save(src)
    s["name"] = f"{s['name']} (Copy)"
    write_save(dest, s, backup=False)
    return jsonify({"ok": True, "dest": dest})

@app.route("/api/slot/delete", methods=["POST"])
def api_delete_slot():
    data = request.json
    slot = data.get("slot", 1)
    cfg = load_config()
    if slot == cfg["active_slot"]:
        return jsonify({"error": "Cannot delete active slot"}), 400
    p = get_save_path(slot)
    if p.exists():
        backup_save(slot)
        p.unlink()
    return jsonify({"ok": True})

@app.route("/api/slot/switch", methods=["POST"])
def api_switch_slot():
    data = request.json
    slot = data.get("slot", 1)
    cfg = load_config()
    cfg["active_slot"] = slot
    save_config(cfg)
    load_save(slot)
    return jsonify({"ok": True})

# ── Worlds API ────────────────────────────────────────────────────────────────
@app.route("/api/worlds", methods=["GET"])
def api_get_worlds():
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    effective = get_effective_worlds(save)

    unlocked = set(save.get("worlds_unlocked", ["origins"]))
    unlocked = unlocked | HOLIDAY_IDS
    
    # Get currently active holiday (within date window)
    active_holiday = get_active_holiday()
    active_holiday_id = active_holiday["id"] if active_holiday else None
    all_unlocked = save.get("all_worlds_unlocked", False)
    active = save.get("active_world", "origins")

    result = []
    for i, w in enumerate(effective):
        wd = save.get("world_data", {}).get(w["id"], {})
        quest = w.get("quest", [])
        quest_progress = wd.get("quest_progress", [])
        quest_completed = wd.get("quest_completed", False)
        
        # Holiday worlds are only switchable if currently in their date window
        is_holiday = w["id"] in HOLIDAY_IDS
        is_active_holiday = is_holiday and w["id"] == active_holiday_id
        
        result.append({
            "id": w["id"],
            "name": w["name"],
            "emoji": w["emoji"],
            "description": w["description"],
            "color": w["color"],
            "starters": list(w["starters"].keys()),
            "quest": quest,
            "quest_progress": quest_progress,
            "quest_completed": quest_completed,
            "unlocked": all_unlocked or w["id"] in unlocked or (w["id"] == "void" and bool(save.get("void_world_unlocked"))),
            "active": w["id"] == active,
            "seasonal": is_holiday,  # Mark as seasonal world
            "currently_active": is_active_holiday,  # Only active during date window
            "order": i,
        })
    return jsonify(result)

@app.route("/api/worlds/switch", methods=["POST"])
def api_switch_world():
    data = request.json
    world_id = data.get("world_id", "origins")
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)

    unlocked = set(save.get("worlds_unlocked", ["origins"]))
    unlocked = unlocked | HOLIDAY_IDS
    all_unlocked = save.get("all_worlds_unlocked", False)
    void_ok = world_id == "void" and bool(save.get("void_world_unlocked"))

    if not all_unlocked and not void_ok and world_id not in unlocked:
        return jsonify({"error": "World not unlocked"}), 403

    # Check if holiday world is currently active (within date window)
    if world_id in HOLIDAY_IDS:
        # Find the holiday world config
        hw = next((h for h in HOLIDAY_WINDOWS if h["id"] == world_id), None)
        if hw:
            t = date.today()
            sm, sd = hw["start"]
            em, ed = hw["end"]
            if not _in_holiday_window(t.month, t.day, sm, sd, em, ed):
                return jsonify({"error": "Holiday world not currently active"}), 403

    effective = get_effective_worlds(save)
    world_ids = [w["id"] for w in effective]
    if world_id not in world_ids:
        return jsonify({"error": "Unknown world"}), 404

    # Flush current world data back before switching
    flush_active_world(save)

    # Switch
    old_world = save.get("active_world", "origins")
    save["active_world"] = world_id

    # Init world data if first visit
    if "world_data" not in save:
        save["world_data"] = {}
    if world_id not in save["world_data"]:
        # Find world config in effective worlds
        wconf = next((w for w in effective if w["id"] == world_id), None)
        if wconf:
            wd = _make_world_data(world_id)
            # Override starters with the correct world's starters
            items = {}
            for k, v in wconf["starters"].items():
                items[k] = {
                    "emoji": v["emoji"],
                    "display": v["display"],
                    "rarity": v["rarity"],
                    "is_first_discovery": False,
                    "pinned": False,
                    "tags": [],
                    "discovered_at": datetime.utcnow().isoformat(),
                    "notes": "",
                    "collection_ids": [],
                    "trophy": {"speedrun_best": None, "challenge_best": None},
                    "lore": "",
                }
            wd["items"] = items
            save["world_data"][world_id] = wd

    # Sync the new world's data to top-level
    sync_active_world(save)

    # Handle sync mode
    if cfg.get("worlds_sync_enabled"):
        # Merge discoveries from all worlds into current world
        all_items = save.get("items", {})
        for wid, wdata in save.get("world_data", {}).items():
            if wid != world_id:
                for k, v in wdata.get("items", {}).items():
                    if k not in all_items:
                        all_items[k] = v
        save["items"] = all_items
        save["world_data"][world_id]["items"] = all_items

    write_save(slot, save)
    return jsonify({"ok": True, "world_id": world_id})

@app.route("/api/worlds/generate", methods=["POST"])
def api_generate_worlds():
    """Generate 9 AI worlds for this save slot."""
    try:
        worlds = ai_generate_worlds()
        cfg = load_config()
        slot = cfg["active_slot"]
        save = load_save(slot)
        save["monthly_worlds_data"] = worlds
        write_save(slot, save)
        return jsonify({"ok": True, "worlds": worlds})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/worlds/new-save-options", methods=["POST"])
def api_new_save_world_options():
    """Set all_worlds_unlocked on a fresh save."""
    data = request.json
    all_unlocked = data.get("all_worlds_unlocked", False)
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    save["all_worlds_unlocked"] = all_unlocked
    if all_unlocked:
        save["worlds_unlocked"] = [w["id"] for w in get_effective_worlds(save)]
    write_save(slot, save)
    return jsonify({"ok": True})

# ── Combine ───────────────────────────────────────────────────────────────────
@app.route("/api/combine", methods=["POST"])
def api_combine():
    data   = request.json
    item_a = data.get("item_a", "").lower().strip()
    item_b = data.get("item_b", "").lower().strip()
    force  = data.get("force", False)

    if not item_a or not item_b:
        return jsonify({"error": "Two items required"}), 400

    # Load save first — save cache is in-memory, fast
    cfg          = load_config()
    slot         = cfg["active_slot"]
    save         = load_save(slot)
    key          = combo_key(item_a, item_b)
    active_world = save.get("active_world", "origins")

    # Validate items actually exist in this save
    if item_a not in save.get("items", {}):
        return jsonify({"error": f"Item '{item_a}' not found in save"}), 400
    if item_b not in save.get("items", {}):
        return jsonify({"error": f"Item '{item_b}' not found in save"}), 400

    # ── Fast cache hit: return immediately, no AI call needed ─────────────────
    if key in save["combinations"] and not force:
        result_name = save["combinations"][key]
        item_data   = save["items"].get(result_name, {})
        # Also populate global cache so other saves benefit
        _gcache_set(key, result_name)
        return jsonify({
            "result":    result_name,
            "display":   item_data.get("display", result_name.title()),
            "emoji":     item_data.get("emoji", "✨"),
            "rarity":    item_data.get("rarity", "common"),
            "is_new":    False, "cached": True, "source": "cache",
            "lore":      item_data.get("lore", ""),
            "tags":      item_data.get("tags", []),
            "item_data": item_data,
            "combo_key": key,
        })

    # Check plugin combos first
    plugin_combos, _ = get_plugin_combos()
    if key in plugin_combos and not force:
        result_data = plugin_combos[key]
        result_name = result_data["result"].lower().strip()
        result_emoji = result_data.get("emoji", "✨")
        result_rarity = result_data.get("rarity", "common")
        is_new = result_name not in save["items"]

        if result_name in save["items"]:
            result_emoji = save["items"][result_name]["emoji"]
            result_rarity = save["items"][result_name]["rarity"]

        save["combinations"][key] = result_name
        if result_name not in save["items"]:
            save["items"][result_name] = _new_item(result_name, result_emoji, result_rarity, "")

        _append_log(save, item_a, item_b, result_name, result_emoji, "plugin")
        notify_plugins_combination(item_a, item_b, result_name)
        flush_active_world(save)
        write_save(slot, save)

        full_item = save["items"].get(result_name, {})
        return jsonify({
            "result": result_name, "display": result_name.title(),
            "emoji": result_emoji, "rarity": result_rarity,
            "is_new": is_new, "cached": False, "source": "plugin", "lore": "",
            "tags": full_item.get("tags", []),
            "item_data": full_item, "combo_key": key,
        })

    # ── Shared DB lookup (before AI) ─────────────────────────────────────────
    disp_a = save["items"].get(item_a, {}).get("display", item_a.title())
    disp_b = save["items"].get(item_b, {}).get("display", item_b.title())
    shared_hit = shared_db.lookup(disp_a, disp_b) if not force else None
    is_first_global = False

    # AI call
    try:
        try:
            if shared_hit:
                ai_result = shared_hit
                is_first_global = False
            else:
                ai_result = ai_combine(disp_a, disp_b, save.get("seed"), active_world)
                is_first_global = True   # we had to call AI → we're first
        except AIError as e:
            return jsonify({"error": str(e)}), 503

        result_name   = ai_result.get("result", "unknown").lower().strip()
        result_emoji  = ai_result.get("emoji", "✨")
        result_rarity = ai_result.get("rarity", "common")
        result_lore   = ai_result.get("lore", "")
        result_tags   = ai_result.get("tags", [])

        if result_rarity not in ("common", "uncommon", "rare", "legendary", "mythic", "transcendent"):
            result_rarity = "common"

        if result_name in save["items"]:
            result_emoji  = save["items"][result_name]["emoji"]
            result_rarity = save["items"][result_name]["rarity"]

        is_new = result_name not in save["items"]
        save["combinations"][key] = result_name

        if is_new:
            item_data = _new_item(result_name, result_emoji, result_rarity, result_lore)
            if result_tags:
                item_data["tags"] = result_tags
            save["items"][result_name] = item_data
        else:
            # update lore and tags if not set
            if not save["items"][result_name].get("lore"):
                save["items"][result_name]["lore"] = result_lore
            if not save["items"][result_name].get("tags") and result_tags:
                save["items"][result_name]["tags"] = result_tags

        _append_log(save, item_a, item_b, result_name, result_emoji, "ai")
        notify_plugins_combination(item_a, item_b, result_name)

        # ── Quest check ───────────────────────────────────────────────────────
        quest_unlocked = None
        if is_new:
            quest_unlocked = _check_world_quest(save, result_name, active_world)

        # ── Sync mode ─────────────────────────────────────────────────────────
        if cfg.get("worlds_sync_enabled") and is_new:
            for wid, wdata in save.get("world_data", {}).items():
                if wid != active_world and result_name not in wdata.get("items", {}):
                    wdata.setdefault("items", {})[result_name] = save["items"][result_name]

        # ── Void World unlock (check before write) ────────────────────────────
        void_unlocked = False
        if is_new and result_name in VOID_TRIGGERS and not save.get("void_world_unlocked"):
            save["void_world_unlocked"] = True
            void_unlocked = True

        flush_active_world(save)
        write_save(slot, save)

        # Submit new AI results to shared DB
        if is_first_global and not shared_hit:
            try:
                shared_db.submit(disp_a, disp_b, {
                    "result": result_name, "emoji": result_emoji,
                    "rarity": result_rarity, "lore": result_lore,
                })
            except Exception:
                pass

        # Include the full item object so the client can update save locally
        full_item = save["items"].get(result_name, {})

        resp = {
            "result":       result_name,
            "display":      result_name.title(),
            "emoji":        result_emoji,
            "rarity":       result_rarity,
            "is_new":       is_new,
            "cached":       False,
            "source":       "shared_db" if shared_hit else "ai",
            "lore":         result_lore,
            "tags":         result_tags,
            "first_global": is_first_global and is_new,
            # Client uses these to patch save locally (avoids a round-trip GET /save)
            "item_data":    full_item,
            "combo_key":    key,
        }
        if quest_unlocked:
            if isinstance(quest_unlocked, dict):
                if quest_unlocked.get("world_unlocked"):
                    resp["quest_unlocked"] = quest_unlocked["world_unlocked"]
                if quest_unlocked.get("holiday_theme_unlocked"):
                    resp["holiday_theme_unlocked"] = quest_unlocked["holiday_theme_unlocked"]
                if quest_unlocked.get("holiday_completionist_unlocked"):
                    resp["holiday_completionist_unlocked"] = True
            else:
                resp["quest_unlocked"] = quest_unlocked
        if void_unlocked:
            resp["void_world_unlocked"] = True

        return jsonify(resp)

    except Exception as e:
        import traceback
        logger.error(f"Error in /api/combine: {e}\n{traceback.format_exc()}")
        # Provide a more helpful error message
        error_msg = str(e)
        if not error_msg or error_msg == "":
            error_msg = "An unexpected error occurred during combination"
        return jsonify({"error": error_msg}), 500


def _new_item(name, emoji, rarity, lore):
    return {
        "emoji": emoji,
        "display": name.title(),
        "rarity": rarity,
        "is_first_discovery": True,
        "pinned": False,
        "tags": [],
        "discovered_at": datetime.utcnow().isoformat(),
        "notes": "",
        "collection_ids": [],
        "trophy": {"speedrun_best": None, "challenge_best": None},
        "lore": lore,
    }


MAX_DISCOVERY_LOG = 500

def _append_log(save, item_a, item_b, result_name, result_emoji, source):
    entry = {
        "item_a": item_a,
        "item_b": item_b,
        "result": result_name,
        "emoji_a": save["items"].get(item_a, {}).get("emoji", "?"),
        "emoji_b": save["items"].get(item_b, {}).get("emoji", "?"),
        "emoji_result": result_emoji,
        "timestamp": datetime.utcnow().isoformat(),
        "source": source,
    }
    save["discovery_log"].insert(0, entry)
    # Cap to avoid save file bloat
    if len(save["discovery_log"]) > MAX_DISCOVERY_LOG:
        save["discovery_log"] = save["discovery_log"][:MAX_DISCOVERY_LOG]


def _check_world_quest(save, result_name, active_world):
    """Check if result_name completes a quest item. Returns dict with unlocked info if newly unlocked, else None."""
    effective = get_effective_worlds(save)
    world_order = [w["id"] for w in effective]

    current_idx = world_order.index(active_world) if active_world in world_order else 0
    current_world_conf = effective[current_idx]
    quest = current_world_conf.get("quest", [])

    if not quest:
        return None

    wd = save.setdefault("world_data", {}).setdefault(active_world, {})
    wd.setdefault("quest_progress", [])

    if wd.get("quest_completed"):
        return None

    if result_name in quest and result_name not in wd["quest_progress"]:
        wd["quest_progress"].append(result_name)

    result = {}
    if len(wd["quest_progress"]) >= len(quest):
        wd["quest_completed"] = True
        # Unlock next world
        if current_idx + 1 < len(effective):
            next_world = effective[current_idx + 1]["id"]
            unlocked = save.setdefault("worlds_unlocked", ["origins"])
            if next_world not in unlocked:
                unlocked.append(next_world)
            result["world_unlocked"] = next_world
        
        # Check if this is a holiday world - unlock its theme
        if active_world in HOLIDAY_IDS:
            theme_unlocked = _unlock_holiday_theme(save, active_world)
            if theme_unlocked:
                result["holiday_theme_unlocked"] = theme_unlocked
            # Check for holiday completionist badge
            badge_unlocked = _check_holiday_completionist(save)
            if badge_unlocked:
                result["holiday_completionist_unlocked"] = True
        
        return result if result else None

    return None


def _unlock_holiday_theme(save, world_id):
    """Unlock the holiday theme for the given world. Returns theme_key if newly unlocked, else None."""
    # Find the holiday world config
    hw = next((h for h in HOLIDAY_WINDOWS if h["id"] == world_id), None)
    if not hw or "theme_key" not in hw:
        return None
    
    theme_key = hw["theme_key"]
    
    # Load account and add theme to theme_unlocks
    acc = load_account()
    if not acc:
        return None
    
    theme_unlocks = acc.get("theme_unlocks", [])
    if theme_key in theme_unlocks:
        return None  # Already unlocked
    
    theme_unlocks.append(theme_key)
    acc["theme_unlocks"] = theme_unlocks
    save_account(acc)
    
    return theme_key


def _check_holiday_completionist(save):
    """Check if all holiday worlds have completed quests. Grant badge if so. Returns True if newly granted."""
    # Get all holiday world IDs
    # Check all holiday worlds in world_data
    world_data = save.get("world_data", {})
    
    for hw_id in HOLIDAY_IDS:
        wd = world_data.get(hw_id, {})
        if not wd.get("quest_completed", False):
            return False  # Not all holidays completed
    
    # All holidays completed - grant badge
    acc = load_account()
    if not acc:
        return False
    
    badges = acc.get("badges", {})
    if "holiday_completionist" in badges:
        return False  # Already have badge
    
    badges["holiday_completionist"] = datetime.utcnow().isoformat()
    acc["badges"] = badges
    save_account(acc)
    
    return True

# ── Recipe Path ───────────────────────────────────────────────────────────────
@app.route("/api/recipe-path", methods=["GET"])
def api_recipe_path():
    item_key = request.args.get("item", "").lower().strip()
    cfg  = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    if item_key not in save["items"]:
        return jsonify({"error": "Item not found"}), 404
    tree  = build_recipe_tree(item_key, save["combinations"], save["items"])
    steps = flatten_recipe_steps(tree)
    root  = save["items"].get(item_key, {})
    return jsonify({
        "key":     item_key,
        "display": root.get("display", item_key.title()),
        "emoji":   root.get("emoji", "✨"),
        "rarity":  root.get("rarity", "common"),
        "lore":    root.get("lore", ""),
        "steps":   steps,
    })

# ── Weekly Challenges ─────────────────────────────────────────────────────────
@app.route("/api/weekly-challenges", methods=["GET"])
def api_weekly_challenges():
    wc = get_weekly_challenges()
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    scores = save.get("weekly_challenge_scores", {}).get(wc["week"], {})
    for ch in wc["challenges"]:
        ch["best_combos"] = scores.get(str(ch["id"]), {}).get("combos", None)
        ch["best_time_ms"] = scores.get(str(ch["id"]), {}).get("time_ms", None)
    return jsonify(wc)

@app.route("/api/weekly-challenges/save", methods=["POST"])
def api_save_weekly_challenge():
    data = request.json
    week = data.get("week")
    challenge_id = str(data.get("challenge_id"))
    combos = data.get("combos")
    time_ms = data.get("time_ms")
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    scores = save.setdefault("weekly_challenge_scores", {})
    week_scores = scores.setdefault(week, {})
    existing = week_scores.get(challenge_id, {})
    # Keep best score (fewest combos)
    if existing.get("combos") is None or combos < existing["combos"]:
        week_scores[challenge_id] = {"combos": combos, "time_ms": time_ms}
    write_save(slot, save)
    return jsonify({"ok": True})

# ── Tags ──────────────────────────────────────────────────────────────────────
@app.route("/api/tags/generate", methods=["POST"])
def api_generate_tags():
    data = request.json
    item_name = data.get("item", "").lower().strip()
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    if item_name not in save["items"]:
        return jsonify({"error": "Item not found"}), 404
    try:
        result = ai_generate_tags(save["items"][item_name]["display"])
        tags = result.get("tags", [])
        save["items"][item_name]["tags"] = tags
        flush_active_world(save)
        write_save(slot, save)
        return jsonify({"tags": tags})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/item/update", methods=["POST"])
def api_update_item():
    data = request.json
    item_name = data.get("item", "").lower().strip()
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    if item_name not in save["items"]:
        return jsonify({"error": "Item not found"}), 404
    for field in ["pinned", "notes", "tags", "collection_ids", "emoji", "display", "rarity", "lore"]:
        if field in data:
            save["items"][item_name][field] = data[field]
    flush_active_world(save)
    write_save(slot, save)
    return jsonify({"ok": True})

@app.route("/api/item/create", methods=["POST"])
def api_create_item():
    data = request.json
    name = data.get("name", "").lower().strip()
    emoji = data.get("emoji", "✨")
    rarity = data.get("rarity", "common")
    if not name:
        return jsonify({"error": "Name required"}), 400
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    if name in save["items"]:
        return jsonify({"error": "Item already exists"}), 400
    save["items"][name] = _new_item(name, emoji, rarity, "")
    save["items"][name]["is_first_discovery"] = False
    save["items"][name]["tags"] = ["dev made item"]
    flush_active_world(save)
    write_save(slot, save)
    return jsonify({"ok": True})

@app.route("/api/item/delete", methods=["POST"])
def api_delete_item():
    data = request.json
    item_name = data.get("item", "").lower().strip()
    permanent = data.get("permanent", False)
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    # Don't delete world starters for current world
    active = save.get("active_world", "origins")
    effective = get_effective_worlds(save)
    current_world = next((w for w in effective if w["id"] == active), None)
    starters = set(current_world["starters"].keys()) if current_world else set(STARTER_ITEMS.keys())
    if item_name in starters:
        return jsonify({"error": "Cannot delete starter items"}), 400
    if item_name not in save["items"]:
        return jsonify({"error": "Item not found"}), 404
    if permanent:
        del save["items"][item_name]
        save.get("trash", {}).pop(item_name, None)
        to_remove = [k for k in save["combinations"] if item_name in k.split("+")]
        for k in to_remove:
            del save["combinations"][k]
    else:
        save.setdefault("trash", {})[item_name] = save["items"].pop(item_name)
    flush_active_world(save)
    write_save(slot, save)
    return jsonify({"ok": True})

@app.route("/api/item/restore", methods=["POST"])
def api_restore_item():
    data = request.json
    item_name = data.get("item", "").lower().strip()
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    if "trash" not in save or item_name not in save["trash"]:
        return jsonify({"error": "Item not in trash"}), 404
    save["items"][item_name] = save["trash"].pop(item_name)
    flush_active_world(save)
    write_save(slot, save)
    return jsonify({"ok": True})

@app.route("/api/trash/empty", methods=["POST"])
def api_empty_trash():
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    trashed = list(save.get("trash", {}).keys())
    save["trash"] = {}
    for item_name in trashed:
        to_remove = [k for k in save["combinations"] if item_name in k.split("+")]
        for k in to_remove:
            del save["combinations"][k]
    flush_active_world(save)
    write_save(slot, save)
    return jsonify({"ok": True})

@app.route("/api/reset", methods=["POST"])
def api_reset():
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    backup_save(slot)
    name = save.get("name", f"Slot {slot}")
    seed = save.get("seed")
    active_world = save.get("active_world", "origins")
    new_save = default_save()
    new_save["name"] = name
    new_save["seed"] = seed
    new_save["active_world"] = active_world
    new_save["achievements"] = save.get("achievements", {})
    new_save["weekly_challenge_scores"] = save.get("weekly_challenge_scores", {})
    # Reset only the active world's data
    new_save["world_data"][active_world] = _make_world_data(active_world)
    sync_active_world(new_save)
    write_save(slot, new_save, backup=False)
    return jsonify({"ok": True})

@app.route("/api/hard-reset", methods=["POST"])
def api_hard_reset():
    # ── Saves & backups ───────────────────────────────────────────────────────
    for f in SAVES_DIR.glob("slot_*.json"):
        f.unlink()
    for f in BACKUPS_DIR.glob("slot_*.bak*.json"):
        f.unlink()

    # ── Account ───────────────────────────────────────────────────────────────
    ACCOUNT_PATH.unlink(missing_ok=True)

    # ── Custom background ─────────────────────────────────────────────────────
    for f in ASSETS_DIR.glob("custom_bg.*"):
        f.unlink()

    # ── Config → factory defaults ─────────────────────────────────────────────
    save_config(DEFAULT_CONFIG)

    # ── Log file ──────────────────────────────────────────────────────────────
    log_file = BASE_DIR / "logs" / "app.log"
    if log_file.exists():
        open(log_file, "w").write("")

    # ── In-memory caches ──────────────────────────────────────────────────────
    # Clear the save cache so stale data can't bleed back in
    import core.save as _save_mod
    _save_mod._save_cache.clear()
    _save_mod._write_counts.clear()

    # Clear the global combo cache
    with _GLOBAL_COMBO_LOCK:
        _GLOBAL_COMBO_CACHE.clear()

    # ── Fresh slot 1 ─────────────────────────────────────────────────────────
    write_save(1, default_save(), backup=False)
    return jsonify({"ok": True})

@app.route("/api/achievements/unlock", methods=["POST"])
def api_unlock_achievement():
    data = request.json
    achievement_id = data.get("id", "")
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    if achievement_id not in save.get("achievements", {}):
        save.setdefault("achievements", {})[achievement_id] = datetime.utcnow().isoformat()
        write_save(slot, save)
        return jsonify({"ok": True, "new": True})
    return jsonify({"ok": True, "new": False})


# ── Shared Community Database ─────────────────────────────────────────────────
@app.route("/api/shared-db/stats", methods=["GET"])
def api_shared_db_stats():
    return jsonify(shared_db.get_stats())

@app.route("/api/shared-db/test", methods=["POST"])
def api_shared_db_test():
    return jsonify(shared_db.test_connection())

@app.route("/api/shared-db/sync", methods=["POST"])
def api_shared_db_sync():
    force = request.json.get("force", False) if request.json else False
    return jsonify(shared_db.sync(force=force))

@app.route("/api/shared-db/save", methods=["POST"])
def api_shared_db_save():
    data = request.json or {}
    cfg = load_config()
    for field in ["shared_db_enabled","shared_db_backend","shared_db_tg_token",
                  "shared_db_tg_chat","shared_db_webhook_url"]:
        if field in data:
            cfg[field] = data[field]
    save_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/collection/add-item", methods=["POST"])
def api_collection_add_item():
    data = request.json
    cid      = data.get("collection_id", "")
    item_key = data.get("item_key", "").lower().strip()
    cfg  = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    col  = save.get("collections", {}).get(cid)
    if not col:
        return jsonify({"error": "Collection not found"}), 404
    if item_key not in save.get("items", {}):
        return jsonify({"error": "Item not found"}), 404
    keys = col.setdefault("item_keys", [])
    if item_key not in keys:
        keys.append(item_key)
    write_save(slot, save)
    return jsonify({"ok": True})

@app.route("/api/collection/remove-item", methods=["POST"])
def api_collection_remove_item():
    data = request.json
    cid      = data.get("collection_id", "")
    item_key = data.get("item_key", "").lower().strip()
    cfg  = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    col  = save.get("collections", {}).get(cid)
    if not col:
        return jsonify({"error": "Collection not found"}), 404
    col["item_keys"] = [k for k in col.get("item_keys", []) if k != item_key]
    write_save(slot, save)
    return jsonify({"ok": True})

@app.route("/api/collection/create", methods=["POST"])
def api_create_collection():
    data = request.json
    name = data.get("name", "Untitled")
    emoji = data.get("emoji", "📁")
    color = data.get("color", "#4a9eff")
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    cid = f"col_{int(time.time()*1000)}"
    save.setdefault("collections", {})[cid] = {"name": name, "emoji": emoji, "color": color, "item_keys": []}
    write_save(slot, save)
    return jsonify({"ok": True, "id": cid})

@app.route("/api/collection/update", methods=["POST"])
def api_update_collection():
    data = request.json
    cid = data.get("id", "")
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    if cid not in save.get("collections", {}):
        return jsonify({"error": "Collection not found"}), 404
    for field in ["name", "emoji", "color", "item_keys"]:
        if field in data:
            save["collections"][cid][field] = data[field]
    write_save(slot, save)
    return jsonify({"ok": True})

@app.route("/api/collection/delete", methods=["POST"])
def api_delete_collection():
    data = request.json
    cid = data.get("id", "")
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    if cid in save.get("collections", {}):
        del save["collections"][cid]
        for idata in save["items"].values():
            if cid in idata.get("collection_ids", []):
                idata["collection_ids"].remove(cid)
        write_save(slot, save)
    return jsonify({"ok": True})

@app.route("/api/daily", methods=["GET"])
def api_daily():
    today = date.today().isoformat()
    h = int(hashlib.sha256(today.encode()).hexdigest(), 16)
    all_items = list(CHALLENGE_POOL)
    # Banner combo: two items to try combining
    a_idx = h % len(all_items)
    b_idx = (h // len(all_items)) % len(all_items)
    if a_idx == b_idx:
        b_idx = (b_idx + 1) % len(all_items)
    combo = {"date": today, "item_a": all_items[a_idx], "item_b": all_items[b_idx]}
    # Daily challenge: a separate discovery target
    target_idx = (h // (len(all_items) ** 2)) % len(all_items)
    if target_idx in (a_idx, b_idx):
        target_idx = (target_idx + 7) % len(all_items)
    challenge = {"date": today, "target": all_items[target_idx]}
    return jsonify({"combo": combo, "challenge": challenge})

@app.route("/api/plugins", methods=["GET"])
def api_plugins():
    discover_plugins()
    result = {}
    for pid, pinfo in get_loaded_plugins().items():
        result[pid] = {"name": pinfo["name"], "version": pinfo["version"], "enabled": pinfo["enabled"], "file": pinfo["file"]}
    return jsonify(result)

@app.route("/api/plugin/toggle", methods=["POST"])
def api_toggle_plugin():
    data = request.json
    pid = data.get("plugin", "")
    cfg = load_config()
    cfg.setdefault("plugins", {})
    loaded_plugins = get_loaded_plugins()
    if pid in loaded_plugins:
        new_state = not loaded_plugins[pid]["enabled"]
        loaded_plugins[pid]["enabled"] = new_state
        cfg["plugins"][pid] = new_state
        save_config(cfg)
        return jsonify({"ok": True, "enabled": new_state})
    return jsonify({"error": "Plugin not found"}), 404

@app.route("/api/backups", methods=["GET"])
def api_backups():
    slot = request.args.get("slot", type=int)
    if not slot:
        cfg = load_config()
        slot = cfg["active_slot"]
    backups = []
    for f in sorted(BACKUPS_DIR.glob(f"slot_{slot}.bak*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        backups.append({"file": f.name, "timestamp": datetime.fromtimestamp(f.stat().st_mtime).isoformat(), "size": f.stat().st_size})
    return jsonify(backups)

@app.route("/api/backup/restore", methods=["POST"])
def api_restore_backup():
    data = request.json
    filename = data.get("file", "")
    p = BACKUPS_DIR / filename
    if not p.exists():
        return jsonify({"error": "Backup not found"}), 404
    match = re.match(r'slot_(\d+)\.bak', filename)
    if not match:
        return jsonify({"error": "Invalid backup filename"}), 400
    slot = int(match.group(1))
    backup_save(slot)
    shutil.copy2(p, get_save_path(slot))
    return jsonify({"ok": True})

_health_cache = {"result": None, "ts": 0}

@app.route("/api/health", methods=["GET"])
def api_health():
    global _health_cache
    now = time.time()
    if now - _health_cache["ts"] < 25 and _health_cache["result"]:
        return jsonify(_health_cache["result"])
    ai_ok = False
    try:
        import requests as hr
        resp = hr.get("https://gen.pollinations.ai/v1/models", timeout=5)
        ai_ok = resp.status_code < 500
    except:
        pass
    ai_stats = get_ai_stats()
    result = {"server": True, "ai_reachable": ai_ok, "queue_depth": ai_stats["queue_depth"], "session_calls": ai_stats["session_calls"]}
    _health_cache = {"result": result, "ts": now}
    return jsonify(result)

@app.route("/api/stats", methods=["GET"])
def api_stats():
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    ai_stats = get_ai_stats()

    all_items = {}
    all_combos = {}
    for wid, wd in save.get("world_data", {}).items():
        all_items.update(wd.get("items", {}))
        all_combos.update(wd.get("combinations", {}))
    # Fallback to top-level if world_data is empty
    if not all_items:
        all_items = save.get("items", {})
        all_combos = save.get("combinations", {})

    rarity_counts = {}
    for item in all_items.values():
        r = item.get("rarity", "common")
        rarity_counts[r] = rarity_counts.get(r, 0) + 1

    usage: dict = {}
    for ck, result in all_combos.items():
        for p in ck.split("+"):
            usage[p] = usage.get(p, 0) + 1
    top_used = sorted(usage.items(), key=lambda x: -x[1])[:10]

    log = save.get("discovery_log", [])
    ai_disc = sum(1 for e in log if e.get("source") == "ai")
    cache_disc = sum(1 for e in log if e.get("source") in ("cache", "shared_db", "plugin"))

    return jsonify({
        "total_items": len(all_items),
        "total_combos": len(all_combos),
        "rarity_counts": rarity_counts,
        "top_used": [{"item": k, "count": v, "display": all_items.get(k, {}).get("display", k.title()), "emoji": all_items.get(k, {}).get("emoji", "✨")} for k, v in top_used],
        "ai_calls": ai_disc,
        "cache_hits": cache_disc,
        "worlds_explored": len(save.get("world_data", {})),
        "session_api_calls": ai_stats["session_calls"],
        "queue_depth": ai_stats["queue_depth"],
        "discovery_log_size": len(log),
        "trash_size": len(save.get("trash", {})),
    })

@app.route("/api/inject", methods=["POST"])
def api_inject_combination():
    if not load_config().get("dev_mode"):
        return jsonify({"error": "Dev mode required"}), 403
    data = request.json
    item_a = data.get("item_a", "").lower().strip()
    item_b = data.get("item_b", "").lower().strip()
    result_name = data.get("result", "").lower().strip()
    result_emoji = data.get("emoji", "✨")
    result_rarity = data.get("rarity", "common")
    if not item_a or not item_b or not result_name:
        return jsonify({"error": "item_a, item_b, and result required"}), 400
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    key = combo_key(item_a, item_b)
    save["combinations"][key] = result_name
    if result_name not in save["items"]:
        save["items"][result_name] = _new_item(result_name, result_emoji, result_rarity, "")
        save["items"][result_name]["is_first_discovery"] = False
    flush_active_world(save)
    write_save(slot, save)
    return jsonify({"ok": True})

@app.route("/api/seed", methods=["POST"])
def api_set_seed():
    data = request.json
    seed = data.get("seed")
    clear = data.get("clear_data", False)
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    if clear and seed != save.get("seed"):
        backup_save(slot)
        save["combinations"] = {}
        active = save.get("active_world", "origins")
        effective = get_effective_worlds(save)
        current_world = next((w for w in effective if w["id"] == active), None)
        starters = set(current_world["starters"].keys()) if current_world else set(STARTER_ITEMS.keys())
        save["items"] = {k: v for k, v in save["items"].items() if k in starters}
        save["discovery_log"] = []
        flush_active_world(save)
    save["seed"] = seed
    write_save(slot, save)
    return jsonify({"ok": True})

@app.route("/api/speedrun/save", methods=["POST"])
def api_save_speedrun():
    data = request.json
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    save.setdefault("speedrun_history", []).append(data)
    goal = data.get("goal_item", "").lower().strip()
    new_time = data.get("time_ms", 0)
    if goal in save["items"]:
        current_best = save["items"][goal]["trophy"].get("speedrun_best")
        if current_best is None or new_time < current_best:
            save["items"][goal]["trophy"]["speedrun_best"] = new_time
    # Track global best speedrun across all goals for leaderboard
    if new_time > 0:
        global_best = save.get("best_speedrun_ms", 0)
        if global_best == 0 or new_time < global_best:
            save["best_speedrun_ms"]    = new_time
            save["best_speedrun_world"] = save.get("active_world", "origins")
    flush_active_world(save)
    write_save(slot, save)
    return jsonify({"ok": True})

@app.route("/api/challenge/save", methods=["POST"])
def api_save_challenge():
    data = request.json
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    save.setdefault("challenge_history", []).append(data)
    goal = data.get("goal_item", "").lower().strip()
    if goal in save["items"]:
        current_best = save["items"][goal]["trophy"].get("challenge_best")
        new_time = data.get("time_ms", 0)
        if current_best is None or new_time < current_best:
            save["items"][goal]["trophy"]["challenge_best"] = new_time
    flush_active_world(save)
    write_save(slot, save)
    return jsonify({"ok": True})

@app.route("/api/export", methods=["GET"])
def api_export():
    cfg = load_config()
    slot = request.args.get("slot", cfg["active_slot"], type=int)
    save = load_save(slot)
    return jsonify(save)

@app.route("/api/import", methods=["POST"])
def api_import_save():
    data = request.json
    mode = data.get("mode", "overwrite")
    save_data = data.get("data", {})
    cfg = load_config()
    slot = cfg["active_slot"]
    if mode == "overwrite":
        backup_save(slot)
        write_save(slot, save_data, backup=False)
    elif mode == "merge":
        current = load_save(slot)
        for k, v in save_data.get("items", {}).items():
            if k not in current["items"]:
                current["items"][k] = v
        for k, v in save_data.get("combinations", {}).items():
            if k not in current["combinations"]:
                current["combinations"][k] = v
        current["discovery_log"] = save_data.get("discovery_log", []) + current["discovery_log"]
        for k, v in save_data.get("achievements", {}).items():
            if k not in current["achievements"]:
                current["achievements"][k] = v
        flush_active_world(current)
        write_save(slot, current)
    return jsonify({"ok": True})

@app.route("/api/log/clear", methods=["POST"])
def api_clear_log():
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    save["discovery_log"] = []
    flush_active_world(save)
    write_save(slot, save)
    return jsonify({"ok": True})

@app.route("/api/plugin/create", methods=["POST"])
def api_create_plugin():
    data = request.json
    name = data.get("name", "my_plugin")
    display_name = data.get("display_name", "My Plugin")
    version = data.get("version", "1.0")
    extra_items = data.get("extra_items", [])
    combos = data.get("combos", {})
    filename = re.sub(r'[^a-z0-9_]', '_', name.lower())
    items_str = json.dumps(extra_items, indent=4)
    combos_str = json.dumps(combos, indent=8)
    content = f'''# Auto-generated plugin for Alchemica
PLUGIN_NAME = "{display_name}"
PLUGIN_VERSION = "{version}"
EXTRA_STARTING_ITEMS = {items_str}

def on_combination(item_a, item_b, result):
    pass

def custom_combinations():
    return {combos_str}
'''
    path = PLUGINS_DIR / f"{filename}.py"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    discover_plugins()
    return jsonify({"ok": True, "file": str(path)})

@app.route("/api/stress-test", methods=["POST"])
def api_stress_test():
    if not load_config().get("dev_mode"):
        return jsonify({"error": "Dev mode required"}), 403
    data = request.json
    count = min(data.get("count", 5), 50)
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    active_world = save.get("active_world", "origins")
    items = list(save["items"].keys())
    results = []
    for _ in range(count):
        a = random.choice(items)
        b = random.choice(items)
        try:
            key = combo_key(a, b)
            if key in save["combinations"]:
                results.append({"a": a, "b": b, "result": save["combinations"][key], "source": "cache"})
                continue
            ai_result = ai_combine(
                save["items"][a]["display"], save["items"][b]["display"],
                save.get("seed"), active_world
            )
            rn = ai_result.get("result", "unknown").lower().strip()
            re_ = ai_result.get("emoji", "✨")
            rr = ai_result.get("rarity", "common")
            save["combinations"][key] = rn
            if rn not in save["items"]:
                save["items"][rn] = _new_item(rn, re_, rr, ai_result.get("lore", ""))
                items.append(rn)
            results.append({"a": a, "b": b, "result": rn, "emoji": re_, "source": "ai"})
        except Exception as e:
            results.append({"a": a, "b": b, "error": str(e)})
    flush_active_world(save)
    write_save(slot, save)
    return jsonify({"results": results, "total": len(results)})

@app.route("/assets/<path:filename>")
def serve_asset(filename):
    if (ASSETS_DIR / filename).exists():
        return send_from_directory(str(ASSETS_DIR), filename)
    return send_from_directory(str(RESOURCE_DIR / "assets"), filename)

@app.route("/api/background/upload", methods=["POST"])
def api_upload_background():
    if "file" in request.files:
        f = request.files["file"]
        ext = f.filename.rsplit(".", 1)[-1] if "." in f.filename else "png"
        fname = f"custom_bg.{ext}"
        f.save(str(ASSETS_DIR / fname))
        cfg = load_config()
        cfg["custom_background"] = f"/assets/{fname}"
        save_config(cfg)
        return jsonify({"ok": True, "url": f"/assets/{fname}"})
    data = request.json
    if data and data.get("base64"):
        img_data = base64.b64decode(data["base64"].split(",")[-1])
        fname = "custom_bg.png"
        with open(ASSETS_DIR / fname, "wb") as f:
            f.write(img_data)
        cfg = load_config()
        cfg["custom_background"] = f"/assets/{fname}"
        save_config(cfg)
        return jsonify({"ok": True, "url": f"/assets/{fname}"})
    return jsonify({"error": "No file provided"}), 400

@app.route("/api/challenge-pool", methods=["GET"])
def api_challenge_pool():
    return jsonify(CHALLENGE_POOL)

@app.route("/api/prompts/defaults", methods=["GET"])
def api_prompt_defaults():
    return jsonify({"combine": DEFAULT_COMBINE_PROMPT, "tags": DEFAULT_TAGS_PROMPT})

@app.route("/api/server-info", methods=["GET"])
def api_server_info():
    """Return current server binding info plus the machine's LAN IPs."""
    import socket as _sock
    cfg = load_config()

    # Collect all non-loopback IPv4 addresses
    lan_ips = []
    try:
        hostname = _sock.gethostname()
        for info in _sock.getaddrinfo(hostname, None):
            ip = info[4][0]
            if not ip.startswith("127.") and ":" not in ip:   # skip loopback & IPv6
                if ip not in lan_ips:
                    lan_ips.append(ip)
    except Exception:
        pass
    if not lan_ips:
        try:
            s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan_ips = [s.getsockname()[0]]
            s.close()
        except Exception:
            lan_ips = ["127.0.0.1"]

    port   = cfg.get("server_port", 5000)
    is_server = cfg.get("server_mode", False)
    custom_url_on = cfg.get("server_custom_url_enabled", False)
    custom_url    = cfg.get("server_custom_url", "")

    if custom_url_on and custom_url:
        public_url = custom_url.rstrip("/")
    elif is_server and lan_ips:
        public_url = f"http://{lan_ips[0]}:{port}"
    else:
        public_url = f"http://127.0.0.1:{port}"

    return jsonify({
        "server_mode":     is_server,
        "port":            port,
        "lan_ips":         lan_ips,
        "public_url":      public_url,
        "custom_url_on":   custom_url_on,
        "custom_url":      custom_url,
        "is_desktop_app":  getattr(sys, "frozen", False) or _DESKTOP_APP,
    })

@app.route("/api/restart", methods=["POST"])
def api_restart():
    """Restart the desktop app process with updated settings."""
    import threading as _th
    def _do_restart():
        time.sleep(0.8)   # give the HTTP response time to fully flush
        try:
            import os, sys as _sys
            # Prefer subprocess + _exit over os.execv — execv is unreliable
            # inside PyInstaller one-file bundles on Windows because the exe
            # unpacks to a temp dir that may still be locked when exec runs.
            import subprocess as _sp
            _sp.Popen([_sys.executable] + _sys.argv[1:],
                      close_fds=True,
                      creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
            os._exit(0)
        except Exception:
            try:
                import os, sys as _sys
                os.execv(_sys.executable, [_sys.executable] + _sys.argv[1:])
            except Exception:
                pass
    _th.Thread(target=_do_restart, daemon=True).start()
    return jsonify({"ok": True, "restarting": True})

@app.route("/api/holiday", methods=["GET"])
def api_holiday():
    """Return the active holiday world (if any) and days remaining in its window."""
    hw = get_active_holiday()
    if not hw:
        return jsonify({"active": False})
    # Find the raw window for day calculation
    t = date.today()
    for raw in HOLIDAY_WINDOWS:
        sm, sd = raw["start"]
        em, ed = raw["end"]
        if _in_holiday_window(t.month, t.day, sm, sd, em, ed):
            start_date = date(t.year if not (sm > em and t.month <= em) else t.year - 1, sm, sd)
            end_date   = date(t.year if not (sm > em and t.month >= sm) else t.year + 1, em, ed) \
                         if sm > em else date(t.year, em, ed)
            days_left  = (end_date - t).days + 1
            break
    else:
        days_left = 0
    return jsonify({"active": True, "world": hw, "days_left": days_left})

@app.route("/api/share-code/export", methods=["GET"])
def api_share_code_export():
    """Return a shareable JSON code of the current save's discoveries."""
    cfg  = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    payload = {
        "ef_version": 1,
        "name":        save.get("name", f"Slot {slot}"),
        "items":        save.get("items", {}),
        "combinations": save.get("combinations", {}),
        "achievements": save.get("achievements", {}),
    }
    raw     = json.dumps(payload, separators=(",", ":")).encode()
    packed  = zlib.compress(raw, level=9)
    code    = base64.b64encode(packed).decode()
    return jsonify({"code": code, "item_count": len(payload["items"])})

@app.route("/api/share-code/import", methods=["POST"])
def api_share_code_import():
    """Import a share code (merge mode only — never overwrites starters or existing items)."""
    data = request.json or {}
    code = data.get("code", "").strip()
    try:
        raw     = zlib.decompress(base64.b64decode(code))
        payload = json.loads(raw)
        if payload.get("ef_version") != 1:
            return jsonify({"error": "Unknown code format"}), 400
    except Exception:
        return jsonify({"error": "Invalid or corrupted share code"}), 400

    cfg  = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    added_items = 0
    added_combos = 0
    for k, v in payload.get("items", {}).items():
        if k not in save["items"]:
            save["items"][k] = v
            added_items += 1
    for k, v in payload.get("combinations", {}).items():
        if k not in save["combinations"]:
            save["combinations"][k] = v
            added_combos += 1
    for k, v in payload.get("achievements", {}).items():
        if k not in save.get("achievements", {}):
            save.setdefault("achievements", {})[k] = v
    flush_active_world(save)
    write_save(slot, save)
    return jsonify({"ok": True, "added_items": added_items, "added_combos": added_combos,
                    "from_name": payload.get("name", "Unknown")})

# ── Friend leaderboard ────────────────────────────────────────────────────────

@app.route("/api/leaderboard", methods=["GET"])
def api_leaderboard():
    """Fetch leaderboard from community server. Empty list if disabled."""
    players = shared_db.fetch_leaderboard()
    return jsonify({"players": players, "enabled": shared_db.is_enabled()})


@app.route("/api/leaderboard/push", methods=["POST"])
def api_leaderboard_push():
    """Push this player's current stats to the community leaderboard."""
    acc = load_account()
    if not acc:
        return jsonify({"ok": False, "error": "No account — create one first"})

    cfg  = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)

    # Find rarest item (skip dev items)
    rarest_item = rarest_emoji = rarest_rarity = ""
    rarity_rank = {"transcendent":6,"mythic":5,"legendary":4,"rare":3,"uncommon":2,"common":1}
    best_rank = 0
    for key, item in save.get("items", {}).items():
        if "dev made item" in [t.lower() for t in item.get("tags", [])]:
            continue
        rank = rarity_rank.get(item.get("rarity","common"), 1)
        if rank > best_rank:
            best_rank     = rank
            rarest_item   = item.get("display", key)
            rarest_emoji  = item.get("emoji", "✨")
            rarest_rarity = item.get("rarity", "common")

    stats = {
        "username":           acc["username"],
        "avatar_color":       acc.get("avatar_color", "#4a9eff"),
        "total_discoveries":  acc.get("total_discoveries", 0),
        "total_combos":       len(save.get("combinations", {})),
        "best_speedrun_ms":   save.get("best_speedrun_ms", 0),
        "best_speedrun_world": save.get("best_speedrun_world", ""),
        "rarest_item":        rarest_item,
        "rarest_emoji":       rarest_emoji,
        "rarest_rarity":      rarest_rarity,
        "daily_streak":       acc.get("daily_streak", 0),
        "weekly_streak":      acc.get("weekly_streak", 0),
    }

    ok = shared_db.submit_leaderboard(stats)
    return jsonify({"ok": ok})


# ── Account system ────────────────────────────────────────────────────────────
ACCOUNT_PATH = BASE_DIR / "account.json"
VALID_RARITIES = ("common", "uncommon", "rare", "legendary", "mythic", "transcendent")

# XP per rarity
RARITY_XP = {"common": 1, "uncommon": 3, "rare": 8, "legendary": 20, "mythic": 50, "transcendent": 150}

def _xp_for_level(level: int) -> int:
    """XP needed to reach this level from 0."""
    total = 0
    for l in range(1, level + 1):
        if l <= 50:   total += 50 * l
        elif l <= 100: total += 100 * l
        else:          total += 200 * l
    return total

def _level_from_xp(xp: int) -> int:
    lvl = 0
    while _xp_for_level(lvl + 1) <= xp:
        lvl += 1
        if lvl >= 100: break
    return max(1, lvl)

def load_account():
    if ACCOUNT_PATH.exists():
        try:
            with open(ACCOUNT_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None

def save_account(data):
    try:
        with open(ACCOUNT_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
    except Exception as e:
        logger.error(f"Error saving account: {e}")

def default_account(username: str):
    return {
        "username": username,
        "created_at": datetime.utcnow().isoformat(),
        "xp": 0,
        "level": 1,
        "avatar_color": "#4a9eff",
        "bio": "",
        "featured_items": [],
        "badges": {},               # account-level badge ids
        "daily_streak": 0,
        "weekly_streak": 0,
        "last_daily": None,
        "last_weekly": None,
        "github_username": None,
        "github_starred": False,
        "github_star_key_used": None,
        "total_discoveries": 0,
        "theme_unlocks": [],        # earned extra themes
    }

def _recalc_account_xp(account):
    """Recompute XP from all saves (called on demand)."""
    total_xp = 0
    total_items = 0
    for slot in range(1, 6):
        p = get_save_path(slot)
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    s = json.load(f)
                for item in s.get("items", {}).values():
                    if "dev made item" in [t.lower() for t in item.get("tags", [])]:
                        continue
                    if item.get("is_first_discovery", True):
                        xp = RARITY_XP.get(item.get("rarity", "common"), 1)
                        total_xp += xp
                        total_items += 1
            except Exception:
                pass
    account["xp"] = total_xp
    account["level"] = _level_from_xp(total_xp)
    account["total_discoveries"] = total_items

# Account-level badge definitions
ACCOUNT_BADGES = {
    "grand_scholar":   {"name": "Grand Scholar",   "icon": "📚", "desc": "Discover 1,000 items across all saves"},
    "world_traveler":  {"name": "World Traveler",  "icon": "🌍", "desc": "Complete every world's quest across any save"},
    "completionist":   {"name": "Completionist",   "icon": "🏅", "desc": "Unlock every in-game achievement"},
    "marathon":        {"name": "Marathon",         "icon": "🔥", "desc": "Reach a 30-day daily challenge streak"},
    "centurion":       {"name": "Centurion",        "icon": "👑", "desc": "Reach account level 100"},
    "github_star":     {"name": "Stargazer",        "icon": "⭐", "desc": "Starred the Alchemica GitHub repo"},
    "holiday_completionist": {"name": "Holiday Master", "icon": "🌍", "desc": "Complete every holiday world's quest"},
}

@app.route("/api/account", methods=["GET"])
def api_get_account():
    acc = load_account()
    if not acc:
        return jsonify({"exists": False})
    _recalc_account_xp(acc)
    acc["exists"] = True
    acc["level"] = _level_from_xp(acc["xp"])
    xp_floor = 0 if acc["level"] == 1 else _xp_for_level(acc["level"])
    acc["xp_to_next"] = _xp_for_level(acc["level"] + 1) - xp_floor if acc["level"] < 100 else 0
    acc["xp_this_level"] = acc["xp"] - xp_floor
    return jsonify(acc)

@app.route("/api/account/create", methods=["POST"])
def api_create_account():
    data = request.json
    username = data.get("username", "").strip()
    if not username or len(username) < 2 or len(username) > 24:
        return jsonify({"error": "Username must be 2–24 characters"}), 400
    if load_account():
        return jsonify({"error": "Account already exists"}), 400
    acc = default_account(username)
    acc["avatar_color"] = data.get("avatar_color", "#4a9eff")
    _recalc_account_xp(acc)
    save_account(acc)
    return jsonify({"ok": True})

@app.route("/api/account/update", methods=["POST"])
def api_update_account():
    acc = load_account()
    if not acc:
        return jsonify({"error": "No account"}), 404
    data = request.json
    for field in ("bio", "avatar_color", "featured_items"):
        if field in data:
            acc[field] = data[field]
    save_account(acc)
    return jsonify({"ok": True})

@app.route("/api/account/github-star", methods=["POST"])
def api_check_github_star():
    acc = load_account()
    if not acc:
        return jsonify({"error": "No account"}), 404
    gh_user = (request.json or {}).get("github_username", "").strip()
    repo = "Fr0zen1cez/Alchemica"
    try:
        import urllib.request
        url = f"https://api.github.com/repos/{repo}/stargazers?per_page=100"
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "Alchemica-App"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            stars = json.loads(resp.read())
            usernames = [s.get("login", "").lower() for s in stars]
            starred = gh_user.lower() in usernames
    except Exception:
        starred = False
    if starred:
        acc["github_username"] = gh_user
        acc["github_starred"] = True
        acc["badges"]["github_star"] = datetime.utcnow().isoformat()
        acc["theme_unlocks"] = list(set(acc.get("theme_unlocks", []) + ["neon-abyss", "forest-spirit", "crimson-void", "aurora-borealis", "golden-age"]))
        save_account(acc)
        return jsonify({"ok": True, "starred": True, "themes": acc["theme_unlocks"]})
    return jsonify({"ok": True, "starred": False})

@app.route("/api/account/add-xp", methods=["POST"])
def api_add_xp():
    """Called when a new item is discovered — adds XP and checks level-up."""
    acc = load_account()
    if not acc:
        return jsonify({"ok": False})
    data = request.json or {}
    rarity = data.get("rarity", "common")
    # Skip dev-made items
    if data.get("is_dev", False):
        return jsonify({"ok": False})
    xp_gain = RARITY_XP.get(rarity, 1)
    old_level = _level_from_xp(acc.get("xp", 0))
    acc["xp"] = acc.get("xp", 0) + xp_gain
    acc["total_discoveries"] = acc.get("total_discoveries", 0) + 1
    new_level = _level_from_xp(acc["xp"])
    acc["level"] = new_level
    leveled_up = new_level > old_level
    save_account(acc)
    return jsonify({"ok": True, "xp_gain": xp_gain, "level": new_level, "leveled_up": leveled_up})

@app.route("/api/account/delete", methods=["POST"])
def api_delete_account():
    try:
        ACCOUNT_PATH.unlink(missing_ok=True)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/account/streak", methods=["POST"])
def api_update_streak():
    acc = load_account()
    if not acc:
        return jsonify({"ok": False})
    data = request.json or {}
    kind = data.get("kind", "daily")
    today = date.today().isoformat()
    week_key = f"{datetime.utcnow().isocalendar()[0]}-{datetime.utcnow().isocalendar()[1]}"

    if kind == "daily":
        last = acc.get("last_daily")
        yesterday = (date.today().toordinal() - 1)
        if last == today:
            pass  # already counted
        elif last and date.fromisoformat(last).toordinal() == yesterday:
            acc["daily_streak"] = acc.get("daily_streak", 0) + 1
        else:
            acc["daily_streak"] = 1
        acc["last_daily"] = today
        if acc["daily_streak"] >= 30:
            acc.setdefault("badges", {})["marathon"] = datetime.utcnow().isoformat()
    elif kind == "weekly":
        last_w = acc.get("last_weekly")
        if last_w != week_key:
            prev_week = f"{datetime.utcnow().isocalendar()[0]}-{datetime.utcnow().isocalendar()[1]-1}"
            if last_w == prev_week:
                acc["weekly_streak"] = acc.get("weekly_streak", 0) + 1
            else:
                acc["weekly_streak"] = 1
            acc["last_weekly"] = week_key

    save_account(acc)
    return jsonify({"ok": True, "daily_streak": acc.get("daily_streak", 0),
                    "weekly_streak": acc.get("weekly_streak", 0)})


@app.route("/api/account/holiday-themes", methods=["GET"])
def api_get_holiday_themes():
    """Return list of available holiday themes and which ones are unlocked."""
    acc = load_account()
    if not acc:
        return jsonify({"exists": False})
    
    # Get all holiday world theme keys
    holiday_themes = []
    for hw in HOLIDAY_WINDOWS:
        if "theme_key" in hw:
            holiday_themes.append({
                "id": hw["id"],
                "name": hw["name"],
                "theme_key": hw["theme_key"],
                "emoji": hw.get("emoji", ""),
                "color": hw.get("color", "#000000"),
            })
    
    # Get unlocked theme keys
    unlocked = acc.get("theme_unlocks", [])
    
    # Mark each theme as unlocked or locked
    for ht in holiday_themes:
        ht["unlocked"] = ht["theme_key"] in unlocked
    
    return jsonify({
        "exists": True,
        "holiday_themes": holiday_themes,
        "unlocked_count": sum(1 for ht in holiday_themes if ht["unlocked"]),
        "total_count": len(holiday_themes),
    })

# ── Weekly challenge themes ────────────────────────────────────────────────────
WEEKLY_THEMES = [
    {"name": "Norse Mythology",    "forbidden": "technology", "items": ["Odin","Thor","Loki","Yggdrasil"]},
    {"name": "Deep Ocean",         "forbidden": "fire",       "items": ["Coral","Current","Pressure","Abyss"]},
    {"name": "Ancient Egypt",      "forbidden": "modern",     "items": ["Sand","Sun","River","Stone"]},
    {"name": "Space Exploration",  "forbidden": "magic",      "items": ["Moon","Planet","Sun","Space"]},
    {"name": "Medieval Kingdom",   "forbidden": "digital",    "items": ["Iron","Wood","Faith","Blood"]},
    {"name": "Digital World",      "forbidden": "nature",     "items": ["Code","Data","Silicon","Electricity"]},
    {"name": "Primordial Chaos",   "forbidden": "structure",  "items": ["Fire","Water","Air","Earth"]},
    {"name": "Alchemical Lab",     "forbidden": "violence",   "items": ["Mercury","Sulfur","Salt","Philosopher's Stone"]},
    {"name": "Arcane Realm",       "forbidden": "technology", "items": ["Mana","Rune","Crystal","Shadow"]},
    {"name": "Industrial Age",     "forbidden": "magic",      "items": ["Coal","Steam","Iron","Gear"]},
    {"name": "Dreamworld",         "forbidden": "real-world", "items": ["Dream","Nightmare","Memory","Echo"]},
    {"name": "Apocalypse",         "forbidden": "nature",     "items": ["Radiation","Ruin","Survivor","Mutation"]},
    {"name": "Microscopic World",  "forbidden": "large",      "items": ["Cell","Bacteria","Virus","DNA"]},
    {"name": "Celestial Realm",    "forbidden": "earthly",    "items": ["Star","Comet","Nebula","Void"]},
    {"name": "Ancient Rome",       "forbidden": "digital",    "items": ["Marble","Gladius","Toga","Aqueduct"]},
    {"name": "Fairy Tale",         "forbidden": "science",    "items": ["Castle","Dragon","Witch","Spell"]},
    {"name": "Wild West",          "forbidden": "magic",      "items": ["Dust","Gunpowder","Gold","Cactus"]},
    {"name": "Cyberpunk City",     "forbidden": "nature",     "items": ["Neon","Implant","Data","Rain"]},
    {"name": "Arctic Tundra",      "forbidden": "fire",       "items": ["Ice","Wind","Aurora","Permafrost"]},
    {"name": "Haunted Mansion",    "forbidden": "light",      "items": ["Shadow","Cobweb","Candle","Dust"]},
    {"name": "Volcanic Island",    "forbidden": "water",      "items": ["Lava","Obsidian","Ash","Sulfur"]},
    {"name": "Bamboo Forest",      "forbidden": "technology", "items": ["Bamboo","Mist","Panda","Silence"]},
    {"name": "Sunken City",        "forbidden": "fire",       "items": ["Coral","Ruin","Pearl","Bioluminescence"]},
    {"name": "Forgotten Library",  "forbidden": "combat",     "items": ["Scroll","Dust","Knowledge","Quill"]},
    {"name": "Time Rift",          "forbidden": "linear",     "items": ["Clock","Echo","Paradox","Memory"]},
    {"name": "Pirate Seas",        "forbidden": "law",        "items": ["Wave","Rum","Cannon","Map"]},
    {"name": "Quantum Realm",      "forbidden": "classical",  "items": ["Particle","Wave","Entanglement","Void"]},
    {"name": "Dwarven Forge",      "forbidden": "nature",     "items": ["Iron","Coal","Anvil","Fire"]},
    {"name": "Cloud Kingdom",      "forbidden": "ground",     "items": ["Cloud","Wind","Lightning","Rain"]},
    {"name": "Cursed Swamp",       "forbidden": "holy",       "items": ["Mud","Fog","Thorn","Toxin"]},
    {"name": "Renaissance Italy",  "forbidden": "modern",     "items": ["Canvas","Gold","Marble","Ink"]},
    {"name": "Samurai Japan",      "forbidden": "gunpowder",  "items": ["Steel","Honor","Cherry Blossom","Shadow"]},
    {"name": "Neon Desert",        "forbidden": "water",      "items": ["Sand","Mirage","Cactus","Starlight"]},
    {"name": "Celestial Kitchen",  "forbidden": "violence",   "items": ["Stardust","Honey","Flame","Salt"]},
    {"name": "Crystal Caves",      "forbidden": "organic",    "items": ["Crystal","Quartz","Echo","Darkness"]},
    {"name": "Steampunk Empire",   "forbidden": "magic",      "items": ["Steam","Gear","Brass","Coal"]},
    {"name": "Enchanted Garden",   "forbidden": "industrial", "items": ["Seed","Moonlight","Dew","Petal"]},
    {"name": "Warpzone",           "forbidden": "slow",       "items": ["Speed","Energy","Plasma","Warp"]},
    {"name": "Frozen Hell",        "forbidden": "warm",       "items": ["Ice","Fire","Paradox","Sulfur"]},
    {"name": "Pixel World",        "forbidden": "analog",     "items": ["Bit","Sprite","Pixel","Code"]},
    {"name": "Lovecraftian Deep",  "forbidden": "sanity",     "items": ["Void","Tentacle","Madness","Abyss"]},
    {"name": "Solar Kingdom",      "forbidden": "dark",       "items": ["Sunlight","Gold","Heat","Dawn"]},
    {"name": "Runic Highlands",    "forbidden": "modern",     "items": ["Rune","Stone","Wind","Blood"]},
    {"name": "Bioluminescent Sea", "forbidden": "dry",        "items": ["Glow","Plankton","Wave","Darkness"]},
    {"name": "Clockwork City",     "forbidden": "organic",    "items": ["Gear","Spring","Copper","Tick"]},
    {"name": "Phantom Circus",     "forbidden": "mundane",    "items": ["Illusion","Mirror","Fire","Laughter"]},
    {"name": "Nuclear Winter",     "forbidden": "life",       "items": ["Radiation","Ash","Metal","Cold"]},
    {"name": "Sky Archipelago",    "forbidden": "ocean",      "items": ["Cloud","Stone","Wind","Sunlight"]},
    {"name": "Infernal Bazaar",    "forbidden": "holy",       "items": ["Coin","Flame","Pact","Shadow"]},
    {"name": "Primeval Jungle",    "forbidden": "civilization","items": ["Vine","Rain","Beast","Rot"]},
]

@app.route("/api/weekly-themes", methods=["GET"])
def api_weekly_themes():
    """Returns this week's themed challenge data."""
    year, week = get_week_key()
    seed_str = f"{year}-{week}-theme"
    h = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)
    theme = WEEKLY_THEMES[h % len(WEEKLY_THEMES)]
    # Pick a target from challenge pool seeded by week
    target_idx = (h * 7 + 13) % len(CHALLENGE_POOL)
    return jsonify({
        "week": f"{year}-{week}",
        "theme": theme["name"],
        "forbidden_tag": theme["forbidden"],
        "starters": theme["items"],
        "target": CHALLENGE_POOL[target_idx],
        "constraint_desc": f"Reach '{CHALLENGE_POOL[target_idx]}' without using any {theme['forbidden']}-tagged elements",
    })


# ── Save Export / Import (.alc) ──────────────────────────────────────────────

@app.route("/api/save/export", methods=["GET"])
def api_save_export():
    """Export current save as a downloadable .alc file (gzip-compressed JSON)."""
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    raw = json.dumps(save, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(raw, level=6)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"alchemica_slot{slot}_{ts}.alc"
    from flask import Response
    return Response(
        compressed,
        mimetype="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Alchemica-Version": "1.6",
        },
    )


@app.route("/api/save/import", methods=["POST"])
def api_save_import():
    """Import an .alc file into the current slot (after backup)."""
    cfg = load_config()
    slot = cfg["active_slot"]
    raw = request.get_data()
    if not raw:
        return jsonify({"error": "No data received"}), 400
    try:
        # Try gzip decompression first, fall back to raw JSON
        try:
            text = zlib.decompress(raw).decode("utf-8")
        except Exception:
            text = raw.decode("utf-8")
        data = json.loads(text)
    except Exception:
        return jsonify({"error": "Invalid save file format"}), 400
    # Basic validation
    if not isinstance(data, dict) or "world_data" not in data:
        # Try migration
        if "items" in data:
            from core.save import migrate_save
            data, _ = migrate_save(data)
        else:
            return jsonify({"error": "Unrecognized save format"}), 400
    backup_save(slot)
    write_save(slot, data)
    return jsonify({"ok": True, "slot": slot})


# ── Boards (named canvas layouts) ─────────────────────────────────────────────

@app.route("/api/boards", methods=["GET"])
def api_boards_get():
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    return jsonify(save.get("boards", {}))


@app.route("/api/boards/save", methods=["POST"])
def api_boards_save():
    data = request.json
    board_id = data.get("id", "")
    board_name = data.get("name", "Board")
    items = data.get("items", [])  # list of {key, x, y}
    if not board_id:
        return jsonify({"error": "board id required"}), 400
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    if "boards" not in save:
        save["boards"] = {}
    save["boards"][board_id] = {
        "name": board_name,
        "items": items,
        "saved_at": datetime.utcnow().isoformat(),
    }
    write_save(slot, save)
    return jsonify({"ok": True})


@app.route("/api/boards/delete", methods=["POST"])
def api_boards_delete():
    data = request.json
    board_id = data.get("id", "")
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    save.get("boards", {}).pop(board_id, None)
    write_save(slot, save)
    return jsonify({"ok": True})


# ── Gift System ───────────────────────────────────────────────────────────────

@app.route("/api/gift/create", methods=["POST"])
def api_gift_create():
    """Encode an item as a shareable gift code string."""
    data = request.json or {}
    item_key = data.get("item", "").lower().strip()
    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)
    item = save.get("items", {}).get(item_key)
    if not item:
        return jsonify({"error": "Item not found"}), 400
    # Dev-made items cannot be gifted
    if any(str(t).lower() == "dev made item" for t in item.get("tags", [])):
        return jsonify({"error": "Dev-made items cannot be gifted"}), 400

    # Determine sender username
    sender = ""
    if ACCOUNT_PATH.exists():
        try:
            acc = json.loads(ACCOUNT_PATH.read_text())
            sender = acc.get("username", "")
        except Exception:
            pass

    gift_payload = {
        "v": 1,
        "key": item_key,
        "display": item.get("display", item_key.title()),
        "emoji": item.get("emoji", "✨"),
        "rarity": item.get("rarity", "common"),
        "lore": item.get("lore", ""),
        "tags": [t for t in (item.get("tags") or []) if str(t).lower() != "dev made item"],
        "from": sender,
        "ts": datetime.utcnow().isoformat(),
    }
    raw = json.dumps(gift_payload, separators=(",", ":")).encode()
    code = "ALCGIFT-" + base64.b64encode(zlib.compress(raw, 6)).decode()
    return jsonify({"code": code, "display": gift_payload["display"], "emoji": gift_payload["emoji"]})


@app.route("/api/gift/claim", methods=["POST"])
def api_gift_claim():
    """Decode a gift code and add the item to the active save."""
    data = request.json or {}
    code = data.get("code", "").strip()
    if not code.startswith("ALCGIFT-"):
        return jsonify({"error": "Invalid gift code format"}), 400
    try:
        compressed = base64.b64decode(code[8:])
        raw = zlib.decompress(compressed)
        gift = json.loads(raw)
    except Exception:
        return jsonify({"error": "Corrupted or invalid gift code"}), 400
    if gift.get("v") != 1:
        return jsonify({"error": "Unsupported gift version"}), 400

    item_key = (gift.get("key") or "").lower().strip()
    if not item_key:
        return jsonify({"error": "Gift has no item data"}), 400

    cfg = load_config()
    slot = cfg["active_slot"]
    save = load_save(slot)

    already_owned = item_key in save.get("items", {})
    if already_owned:
        return jsonify({"error": "You already own this item!", "already_owned": True}), 400

    new_item = {
        "emoji": gift.get("emoji", "✨"),
        "display": gift.get("display", item_key.title()),
        "rarity": gift.get("rarity", "common"),
        "lore": gift.get("lore", ""),
        "tags": gift.get("tags") or [],
        "gifted_from": gift.get("from", ""),
        "is_first_discovery": True,
        "pinned": False,
        "notes": "",
        "collection_ids": [],
        "trophy": {"speedrun_best": None, "challenge_best": None},
        "discovered_at": datetime.utcnow().isoformat(),
    }
    save["items"][item_key] = new_item
    flush_active_world(save)
    write_save(slot, save)

    return jsonify({
        "ok": True,
        "key": item_key,
        "display": gift.get("display", item_key.title()),
        "emoji": gift.get("emoji", "✨"),
        "rarity": gift.get("rarity", "common"),
        "lore": gift.get("lore", ""),
        "from": gift.get("from", ""),
        "item_data": new_item,
    })


# ── Profile Export / Import (.alcp) ──────────────────────────────────────────

@app.route("/api/profile/export", methods=["GET"])
def api_profile_export():
    """Export account profile as a compressed .alcp file."""
    if not ACCOUNT_PATH.exists():
        return jsonify({"error": "No profile found"}), 404
    try:
        profile_data = json.loads(ACCOUNT_PATH.read_text())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    payload = {
        "format": "alchemica-profile",
        "version": "1.7",
        "exported_at": datetime.utcnow().isoformat(),
        "profile": profile_data,
    }
    compressed = zlib.compress(json.dumps(payload, separators=(",", ":")).encode(), 6)
    from flask import Response
    return Response(
        compressed,
        mimetype="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="alchemica_profile.alcp"'},
    )


@app.route("/api/profile/import", methods=["POST"])
def api_profile_import():
    """Import a .alcp profile file."""
    from core.save import SAVES_DIR
    raw = request.get_data()
    if not raw:
        return jsonify({"error": "No data received"}), 400
    try:
        # Try decompressing first, fall back to plain JSON
        try:
            text = zlib.decompress(raw).decode("utf-8")
        except Exception:
            text = raw.decode("utf-8")
        payload = json.loads(text)
    except Exception:
        return jsonify({"error": "Invalid profile file — could not parse"}), 400
    if payload.get("format") != "alchemica-profile":
        return jsonify({"error": "Not a valid .alcp profile file"}), 400
    profile_data = payload.get("profile")
    if not isinstance(profile_data, dict):
        return jsonify({"error": "Profile data is corrupt"}), 400
    ACCOUNT_PATH.write_text(json.dumps(profile_data))
    return jsonify({"ok": True, "username": profile_data.get("username", "?")})


# ── Custom Themes ─────────────────────────────────────────────────────────────

CUSTOM_THEMES_DIR = ASSETS_DIR / "custom_themes"
CUSTOM_THEMES_DIR.mkdir(exist_ok=True)

ALLOWED_VARS = {
    "--bg", "--glass-bg", "--glass-border", "--accent", "--accent-glow",
    "--text", "--text-dim", "--text-bright", "--sidebar-bg",
    "--particle-color", "--item-hover-bg",
    "--rarity-common", "--rarity-uncommon", "--rarity-rare",
    "--rarity-legendary", "--rarity-mythic", "--rarity-transcendent",
}

def _validate_theme(data: dict):
    """Basic validation for an imported theme dict. Returns (ok, error_msg)."""
    if not isinstance(data, dict):
        return False, "Theme file must be a JSON object"
    if not data.get("id") or not isinstance(data["id"], str):
        return False, "Missing or invalid 'id' field"
    if not data.get("name") or not isinstance(data["name"], str):
        return False, "Missing or invalid 'name' field"
    theme_id = data["id"].strip()
    if not theme_id or len(theme_id) > 64:
        return False, "Theme id must be 1–64 characters"
    import re
    if not re.match(r'^[a-z0-9][a-z0-9\-_]*$', theme_id):
        return False, "Theme id may only contain lowercase letters, digits, hyphens and underscores"
    variables = data.get("variables", {})
    if not isinstance(variables, dict):
        return False, "'variables' must be an object"
    bad = [k for k in variables if k not in ALLOWED_VARS]
    if bad:
        return False, f"Unknown CSS variables: {bad[:3]}"
    return True, None


@app.route("/api/theme/import", methods=["POST"])
def api_theme_import():
    import zipfile, io, re

    theme_data = None
    animation_code = None

    # ── ZIP upload ────────────────────────────────────────────────────────────
    if "file" in request.files:
        f = request.files["file"]
        filename = f.filename or ""
        raw = f.read()

        if filename.lower().endswith(".zip"):
            try:
                zf = zipfile.ZipFile(io.BytesIO(raw))
            except Exception:
                return jsonify({"error": "Could not read ZIP file"}), 400

            names = zf.namelist()
            # Accept theme.json at root or inside one folder
            json_candidates = [n for n in names if n.endswith("theme.json")]
            if not json_candidates:
                return jsonify({"error": "ZIP must contain a theme.json file"}), 400
            json_name = sorted(json_candidates, key=len)[0]  # shortest = closest to root

            try:
                theme_data = json.loads(zf.read(json_name).decode("utf-8"))
            except Exception:
                return jsonify({"error": "theme.json is not valid JSON"}), 400

            # Optional animation.js
            js_candidates = [n for n in names if n.endswith("animation.js")]
            if js_candidates:
                js_name = sorted(js_candidates, key=len)[0]
                try:
                    animation_code = zf.read(js_name).decode("utf-8").strip()
                except Exception:
                    animation_code = None

        elif filename.lower().endswith(".json"):
            try:
                theme_data = json.loads(raw.decode("utf-8"))
            except Exception:
                return jsonify({"error": "File is not valid JSON"}), 400
        else:
            return jsonify({"error": "Please upload a .zip or .json file"}), 400

    # ── JSON body fallback ────────────────────────────────────────────────────
    elif request.is_json:
        theme_data = request.json
    else:
        return jsonify({"error": "No file provided"}), 400

    # Inline animation_code field takes precedence over animation.js from zip
    if theme_data and theme_data.get("animation_code") and not animation_code:
        animation_code = theme_data["animation_code"]

    ok, err = _validate_theme(theme_data)
    if not ok:
        return jsonify({"error": err}), 400

    theme_id = theme_data["id"].strip()

    # Persist theme files
    theme_dir = CUSTOM_THEMES_DIR / theme_id
    theme_dir.mkdir(exist_ok=True)

    # Strip animation_code from the stored JSON (saved separately)
    stored = {k: v for k, v in theme_data.items() if k != "animation_code"}
    (theme_dir / "theme.json").write_text(json.dumps(stored, ensure_ascii=False, indent=2))

    if animation_code:
        (theme_dir / "animation.js").write_text(animation_code)
    else:
        # Remove old animation if re-importing without one
        anim_path = theme_dir / "animation.js"
        if anim_path.exists():
            anim_path.unlink()

    # Update config registry
    cfg = load_config()
    existing = [t for t in cfg.get("custom_themes", []) if t["id"] != theme_id]
    entry = {
        "id":            theme_id,
        "name":          theme_data.get("name", theme_id).strip(),
        "author":        theme_data.get("author", ""),
        "type":          theme_data.get("type", "community"),
        "has_animation": bool(animation_code),
    }
    cfg["custom_themes"] = existing + [entry]
    save_config(cfg)

    return jsonify({"ok": True, "theme": entry})


@app.route("/api/theme/list", methods=["GET"])
def api_theme_list():
    cfg = load_config()
    themes = cfg.get("custom_themes", [])
    # Attach animation code for each theme that has it
    result = []
    for t in themes:
        entry = dict(t)
        anim_path = CUSTOM_THEMES_DIR / t["id"] / "animation.js"
        entry["animation_code"] = anim_path.read_text() if anim_path.exists() else None
        theme_path = CUSTOM_THEMES_DIR / t["id"] / "theme.json"
        if theme_path.exists():
            try:
                td = json.loads(theme_path.read_text())
                entry["variables"] = td.get("variables", {})
            except Exception:
                entry["variables"] = {}
        result.append(entry)
    return jsonify(result)


@app.route("/api/theme/delete", methods=["POST"])
def api_theme_delete():
    import shutil as _shutil
    theme_id = (request.json or {}).get("id", "").strip()
    if not theme_id:
        return jsonify({"error": "No theme id provided"}), 400

    theme_dir = CUSTOM_THEMES_DIR / theme_id
    if theme_dir.exists():
        _shutil.rmtree(theme_dir)

    cfg = load_config()
    cfg["custom_themes"] = [t for t in cfg.get("custom_themes", []) if t["id"] != theme_id]
    # If the active theme was this one, reset to default
    if cfg.get("theme") == f"custom:{theme_id}":
        cfg["theme"] = "deep-space"
    save_config(cfg)
    return jsonify({"ok": True})


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if not get_save_path(1).exists():
        write_save(1, default_save(), backup=False)
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
    logger.info("=" * 50)
    logger.info("  Alchemica")
    logger.info("  Open http://localhost:5000 in your browser")
    logger.info("=" * 50)
    app.run(debug=False, port=5000)


