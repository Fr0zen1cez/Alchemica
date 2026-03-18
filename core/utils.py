"""
Alchemica — Shared utility helpers
"""


def combo_key(a: str, b: str) -> str:
    """Return a canonical, order-independent key for a pair of element names.

    Always lower-cased, stripped, and alphabetically sorted so that
    (Fire, Water) and (Water, Fire) map to the same key.
    """
    return "+".join(sorted([a.lower().strip(), b.lower().strip()]))
