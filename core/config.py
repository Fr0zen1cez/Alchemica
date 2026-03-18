import json
import sys
from pathlib import Path
from core.logger import get_logger

logger = get_logger()

def get_base_dir():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent.resolve()

def get_resource_dir():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent.parent.resolve()

BASE_DIR = get_base_dir()
RESOURCE_DIR = get_resource_dir()
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "api_key": "",
    "setup_complete": False,     # True after first-run wizard is dismissed
    "active_slot": 1,
    "theme": "deep-space",
    "bg_animation": "none",
    "cursor_trail": "none",
    "item_card_size": "normal",
    "plugins": {},
    "custom_background": None,
    "dev_mode": False,
    # AI model selection (Pollinations model alias)
    "ai_model": "gemini-fast",   # default: Google Gemini 2.5 Flash Lite
    # Custom AI endpoint (OpenAI-compatible)
    "custom_endpoint_enabled": False,
    "custom_endpoint_url": "https://api.openai.com/v1",
    "custom_endpoint_key": "",
    "custom_endpoint_model": "gpt-4o-mini",
    # Override system prompts (empty string = use built-in default)
    "combine_system_prompt": "",
    "tags_system_prompt": "",
    # Worlds system
    "worlds_sync_enabled": False,       # sync discoveries across all worlds
    "monthly_ai_worlds": False,         # replace worlds 2-10 monthly with AI-generated ones
    "monthly_worlds_generated_week": None,  # ISO week when last generated
    "monthly_worlds_data": None,        # list of 9 AI-generated world configs
    "holiday_worlds_enabled": True,     # show seasonal holiday worlds during their 10-day window
    # Server / hosting
    "server_mode": False,               # bind to 0.0.0.0 for LAN/internet access
    "server_port": 5000,                # port to listen on
    "server_custom_url_enabled": False, # show a custom public URL instead of LAN IP
    "server_custom_url": "",            # e.g. https://elementforge.example.com
    # Shared Community Database
    "shared_db_enabled":      False,
    "shared_db_backend":      "telegram",  # "telegram" | "webhook"
    "shared_db_tg_token":     "",
    "shared_db_tg_chat":      "",
    "shared_db_tg_offset":    0,
    "shared_db_webhook_url":  "",
    "shared_db_cache":        {},
    "shared_db_last_sync":    0,
    # Mirror folder — saves are copied here after every write (e.g. Google Drive folder)
    "mirror_folder_path": "",
    # Custom imported themes (user-made or supporter packs)
    # Each entry: {id, name, author, type, has_animation}
    "custom_themes": [],
}

_config_cache = None

def load_config(force_reload=False):
    global _config_cache
    if not force_reload and _config_cache is not None:
        return _config_cache

    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            _config_cache = cfg
            return cfg
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            
    _config_cache = dict(DEFAULT_CONFIG)
    save_config(_config_cache)
    return _config_cache

def save_config(cfg):
    global _config_cache
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        _config_cache = cfg
    except Exception as e:
        logger.error(f"Error saving config: {e}")
