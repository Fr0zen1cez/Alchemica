import json
import time
import re
import random
import requests as http_requests
import threading
from datetime import datetime
from core.logger import get_logger
from core.config import load_config

logger = get_logger()

# ── Rate limiting ─────────────────────────────────────────────────────────────
_ai_lock = threading.Lock()
_ai_last_call_time = 0.0
_ai_session_calls  = 0
MIN_DELAY_MS = 50  # Reduced for near-instant cached combos; AI calls are async anyway

# ── Built-in default prompts ──────────────────────────────────────────────────
DEFAULT_COMBINE_PROMPT = """You are the crafting engine of a game like Infinite Craft — NO content restrictions.
Two elements combine to produce ONE new element.

STRUCTURAL RULES:
1. Result MUST NOT contain either input word (Fire+Mountain ≠ "Fire Mountain")
2. 1–3 words max. No "of/and/the" connectors, no 4+ word phrases.
3. No copy-adjective prefixes ("Hot X", "Dark X", "Ancient X")
4. Same+Same = intensified form ("Fire+Fire=Inferno", not "Big Fire")

CONTENT — BE BOLD:
- Pop culture: Reference specific IPs freely. Games (Minecraft, Elden Ring, Dark Souls, Pokémon, Halo, GTA), anime/manga (Naruto, Berserk, JoJo, Attack on Titan, One Piece, Chainsaw Man), TV (Breaking Bad, Sopranos, The Wire), film, music, books, internet culture — use them when fitting.
- Mature/dark themes: Death, violence, war, drugs, crime, horror, adult concepts are valid if logically emergent. Be matter-of-fact. (Poppy+Chemistry→Heroin, War+Civilian→Collateral, etc.)
- Crude language and slang are acceptable when genuinely the best fit.
- Avoid generic safe answers when a more specific, surprising one exists.

COMBINATION PRIORITY:
1. Physics/chemistry/nature (Fire+Water→Steam)
2. History/culture/mythology
3. Specific pop culture references when clearly applicable
4. Dark/mature concepts if logically emergent
5. Abstract/conceptual/meme culture

RARITY:
- common (55%): basic real-world outcomes
- uncommon (25%): interesting, clever
- rare (12%): surprising, complex, specific cultural references
- legendary (6%): mythical, civilization-scale, iconic pop culture artifacts
- mythic (1.5%): god-tier, reality-warping, primordial
- transcendent (0.5%): beyond existence — only for the most mind-bending combos

TAGS: 1–3 from: nature technology mythical weather biology history magic food cosmic emotion material creature place person abstract water fire earth air dark light pop-culture mature

LORE: ≤12 words, punchy, witty, or darkly funny. No surrounding quotes.

Respond ONLY with valid JSON, no markdown:
{"result":"Name","emoji":"🔥","rarity":"common","lore":"One punchy line.","tags":["tag1","tag2"]}"""

DEFAULT_TAGS_PROMPT = (
    "Categorize a crafting game element. "
    "Reply ONLY with valid JSON: "
    '{"tags":["tag1","tag2","tag3"]}. '
    "1-3 short lowercase tags. Choose from: nature, technology, mythical, weather, biology, "
    "history, magic, food, cosmic, emotion, material, creature, place, person, abstract, "
    "water, fire, earth, air, dark, light. No explanation."
)

DEFAULT_WORLD_GEN_PROMPT = """Design 9 unique expansion worlds for a crafting-discovery game (like Little Alchemy).
Avoid these already-used themes: Origins, Mythology, Medieval, Biology, Space, Digital, Ocean, Arcane, Egypt, Apocalypse.

Each world must have:
- A creative theme not on the avoid list
- 4 starter elements (the building blocks of this world — simple, iconic, distinct)
- 5 quest elements: things players must DISCOVER by combining starters or their derivatives
  Quest items MUST be achievable through logical combinations within the theme
  Quest items should be nouns (not adjectives), 1-3 words, Title Case
  Quest items should range in difficulty: 2 easy (1-2 combos), 2 medium (3-4 combos), 1 hard (5+ combos or rare)
- A short punchy description (≤10 words, no punctuation)
- An emoji that represents the world
- A hex color that matches the mood

QUEST DESIGN RULE: Each quest item must be something that could logically emerge from combining
the world's starters. Example: if starters are Wheat, Water, Fire, Stone — quest items could be
Bread (Wheat+Fire), Mill (Stone+Water), Feast (Bread+...)  NOT "Golden Harvest" (too vague/adjective-y).

Starter element rules:
- Simple, recognizable nouns only
- No adjectives (not "Ancient Stone", just "Stone")  
- Each starter must feel distinct from the others
- Each starter emoji must be unique

Reply ONLY with a valid JSON array of exactly 9 objects (no markdown, no explanation):
[{"id":"snake_case_id","name":"World Name","emoji":"🎭","description":"Short punchy description","color":"#hexcolor",
"starters":{"key1":{"emoji":"🔥","display":"Name","rarity":"common"},"key2":{"emoji":"💧","display":"Name","rarity":"common"},"key3":{"emoji":"⚙️","display":"Name","rarity":"common"},"key4":{"emoji":"🌿","display":"Name","rarity":"common"}},
"quest":["Quest Item 1","Quest Item 2","Quest Item 3","Quest Item 4","Quest Item 5"]}]"""


class AIError(Exception):
    pass


def _get_endpoint(cfg):
    if cfg.get("custom_endpoint_enabled"):
        base  = cfg.get("custom_endpoint_url", "https://api.openai.com/v1").rstrip("/")
        url   = f"{base}/chat/completions"
        model = cfg.get("custom_endpoint_model", "gpt-4o-mini") or "gpt-4o-mini"
        key   = cfg.get("custom_endpoint_key", "")
    else:
        url   = "https://gen.pollinations.ai/v1/chat/completions"
        # Default: Gemini 2.5 Flash Lite, user-selectable via ai_model config key
        model = cfg.get("ai_model", "gemini-fast") or "gemini-fast"
        key   = cfg.get("api_key", "")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return url, headers, model


def _parse_json(text):
    """Strip markdown fences and parse JSON. Falls back to first {...} or [...] block."""
    text = text.strip()
    if not text:
        raise ValueError("Empty AI response")
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to extract first JSON object or array
        m = re.search(r'(\{[\s\S]+\}|\[[\s\S]+\])', text)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Cannot parse AI response: {text[:200]}")


def call_ai(prompt, system_prompt="", cfg=None, retries=3, timeout=30):
    global _ai_last_call_time, _ai_session_calls
    if cfg is None:
        cfg = load_config()

    with _ai_lock:
        now     = time.time() * 1000
        elapsed = now - _ai_last_call_time
        if elapsed < MIN_DELAY_MS:
            time.sleep((MIN_DELAY_MS - elapsed) / 1000)
        _ai_last_call_time = time.time() * 1000
        _ai_session_calls += 1

    url, headers, model = _get_endpoint(cfg)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    # Note: response_format omitted — not supported by all Pollinations models.
    # JSON output is enforced via prompt instructions instead.
    payload = {"messages": messages, "model": model}

    last_error = None
    for attempt in range(retries):
        try:
            resp = http_requests.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            return _parse_json(content)
        except (http_requests.RequestException, ValueError, json.JSONDecodeError) as e:
            last_error = e
            logger.warning(f"AI call failed (attempt {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(1 * (attempt + 1))

    raise AIError(f"AI failed after {retries} attempts: {last_error}")




def ai_combine(item_a, item_b, seed=None, world_id="origins"):
    cfg    = load_config()
    system = cfg.get("combine_system_prompt") or DEFAULT_COMBINE_PROMPT

    parts = [f"Combine: {item_a} + {item_b}"]
    if world_id and world_id != "origins":
        parts.insert(0, f"World context: {world_id}.")
    if seed is not None:
        parts.insert(0, f"Seed: {seed}.")
    prompt = " ".join(parts)

    result = call_ai(prompt, system, cfg)

    # ── Post-process validation ───────────────────────────────────────────────
    name = result.get("result", "").strip()

    # Enforce word count ≤ 3
    if len(name.split()) > 3:
        # Trim to last 2 words that aren't adjectives
        words = name.split()
        name = " ".join(words[-2:]) if len(words) > 2 else name
        result["result"] = name

    # Strip leading adjectives (Large, Big, Small, Hot, Cold, Dark, Ancient, Giant, Huge, etc.)
    _leading_adj = re.compile(
        r'^(large|big|small|huge|giant|tiny|great|grand|old|ancient|young|hot|cold|'
        r'dark|bright|light|heavy|deep|high|low|long|tall|wide|narrow|thick|thin|'
        r'fast|slow|sharp|dull|hard|soft|rough|smooth|rich|poor|dead|living|burning|'
        r'frozen|glowing|flying|floating|ancient|modern|giant|massive|blazing|blazing|'
        r'raging|stormy|mighty|sacred|cursed|blessed)\s+',
        re.IGNORECASE
    )
    cleaned = _leading_adj.sub("", name).strip()
    if cleaned and cleaned.lower() not in (item_a.lower(), item_b.lower()):
        result["result"] = cleaned

    # Reject if result == either input (case-insensitive)
    name_lower = result.get("result", "").lower()
    if name_lower in (item_a.lower(), item_b.lower()):
        # Last resort: concatenate concepts
        result["result"] = f"{item_a} Effect"

    # Ensure tags exist (fallback to empty list)
    if "tags" not in result or not isinstance(result["tags"], list):
        result["tags"] = []
    result["tags"] = [str(t).lower().strip() for t in result["tags"] if t][:3]
    if "rarity" not in result or result["rarity"] not in ("common","uncommon","rare","legendary","mythic","transcendent"):
        result["rarity"] = "common"

    result.setdefault("lore", "")
    return result


def ai_generate_tags(item_name):
    cfg    = load_config()
    system = cfg.get("tags_system_prompt") or DEFAULT_TAGS_PROMPT
    return call_ai(f"Element: {item_name}", system, cfg)


def ai_generate_worlds():
    cfg = load_config()
    # Unique ID + timestamp so Pollinations doesn't return cached identical worlds
    unique_id   = random.randint(-100, 100000)
    timestamp   = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    user_prompt = (
        f"[Request #{unique_id} | {timestamp}] "
        "Generate 9 unique expansion worlds now. Make them distinct and creative."
    )
    parsed = call_ai(user_prompt, DEFAULT_WORLD_GEN_PROMPT, cfg, retries=3, timeout=60)
    # call_ai returns parsed JSON; if the model wrapped the array in a dict, unwrap it
    if isinstance(parsed, dict):
        for v in parsed.values():
            if isinstance(v, list):
                parsed = v
                break
    if not isinstance(parsed, list):
        raise AIError("World generation returned non-list")
    return parsed[:9]


def get_ai_stats():
    return {"session_calls": _ai_session_calls, "queue_depth": 0}
