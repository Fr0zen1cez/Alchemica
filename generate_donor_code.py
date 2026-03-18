"""
Alchemica — Donor Code Generator
=================================
Run this on YOUR machine to generate a code for a donor.
Never share this file publicly.

Usage:
    python generate_donor_code.py
    python generate_donor_code.py --username "CoolPlayer"
"""

import hmac
import hashlib
import base64
import json
import time
import sys

# ── Secret key ────────────────────────────────────────────────────────────────
# Split to make it slightly less obvious in a casual source read
_A = "alc"
_B = "hem1ca-supp0rt-k3y-2026"
_SECRET = (_A + _B).encode()

DONOR_THEMES = ["molten-core", "celestial-drift", "obsidian-rain"]
DONOR_BADGE  = "supporter"


def generate_code(username_hint: str = "") -> str:
    nonce = base64.b64encode(
        hashlib.sha256(f"{username_hint}{time.time()}".encode()).digest()[:6]
    ).decode()
    payload = {
        "r": "donor_full",
        "u": username_hint,
        "t": int(time.time()),
        "n": nonce,
    }
    payload_b64 = base64.b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).decode()
    sig = hmac.new(_SECRET, payload_b64.encode(), hashlib.sha256).hexdigest()[:16]
    return f"ALCSUPP-{payload_b64}-{sig}"


if __name__ == "__main__":
    username = ""
    if "--username" in sys.argv:
        idx = sys.argv.index("--username")
        if idx + 1 < len(sys.argv):
            username = sys.argv[idx + 1]
    else:
        username = input("Donor username (optional, press Enter to skip): ").strip()

    code = generate_code(username)
    print("\n" + "=" * 60)
    print(f"  Donor code:  {code}")
    print("=" * 60)
    print("Send this code to the donor.")
    print("They paste it in: Profile → Support → Redeem Code\n")
