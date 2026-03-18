"""
Alchemica — Example Plugin
================================
Drop this file (or your own .py) into the plugins/ folder.
Enable it in Settings → Advanced → Plugins.

A plugin can:
  1. Add hardcoded combinations (skips AI, instant result)
  2. Add extra starting items to every world
  3. React to any combination via on_combination()
"""

PLUGIN_NAME    = "Example Plugin"
PLUGIN_VERSION = "1.0"


def custom_combinations():
    """
    Return a dict of hardcoded combinations.
    Keys MUST be alphabetically sorted: 'earth+fire', not 'fire+earth'.
    """
    return {
        "earth+fire": {
            "result":  "lava",
            "emoji":   "🌋",
            "rarity":  "uncommon",
        },
        "earth+water": {
            "result":  "mud",
            "emoji":   "🟫",
            "rarity":  "common",
        },
        "fire+water": {
            "result":  "steam",
            "emoji":   "♨️",
            "rarity":  "common",
        },
        "air+fire": {
            "result":  "smoke",
            "emoji":   "🌫️",
            "rarity":  "common",
        },
    }


# Optional: extra items added to every world's starting inventory
EXTRA_STARTING_ITEMS = [
    # {"key": "void", "emoji": "⬛", "display": "Void", "rarity": "rare"},
]


def on_combination(item_a, item_b, result):
    """Called every time a combination resolves (cache, plugin, or AI)."""
    pass  # e.g. log to a file, send a webhook, etc.
