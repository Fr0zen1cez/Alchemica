import importlib.util
from pathlib import Path
from core.logger import get_logger
from core.config import load_config, get_base_dir

logger = get_logger()

BASE_DIR = get_base_dir()
PLUGINS_DIR = BASE_DIR / "plugins"
PLUGINS_DIR.mkdir(exist_ok=True)

loaded_plugins = {}

def discover_plugins():
    global loaded_plugins
    loaded_plugins = {}
    cfg = load_config()
    
    for f in PLUGINS_DIR.glob("*.py"):
        name = f.stem
        try:
            spec = importlib.util.spec_from_file_location(name, f)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            pname = getattr(mod, "PLUGIN_NAME", name)
            pver = getattr(mod, "PLUGIN_VERSION", "0.0")
            
            loaded_plugins[name] = {
                "module": mod,
                "name": pname,
                "version": pver,
                "file": str(f),
                "enabled": cfg.get("plugins", {}).get(name, False),
            }
            logger.debug(f"Discovered plugin: {name} (Enabled: {loaded_plugins[name]['enabled']})")
        except Exception as e:
            logger.error(f"Plugin Error loading {name}: {e}")

def get_plugin_combos():
    combos = {}
    conflicts = []
    
    for pid, pinfo in loaded_plugins.items():
        if not pinfo["enabled"]:
            continue
            
        mod = pinfo["module"]
        fn = getattr(mod, "custom_combinations", None)
        if fn:
            try:
                pc = fn()
                for k, v in pc.items():
                    # Ensure alphabetical key
                    parts = sorted([p.lower().strip() for p in k.split("+")])
                    nk = "+".join(parts)
                    
                    if nk in combos and combos[nk] != v:
                        conflicts.append({"key": nk, "plugins": [combos[nk]["_source"], pid]})
                        logger.warning(f"Plugin conflict for combo {nk} between {combos[nk]['_source']} and {pid}")
                    
                    v["_source"] = pid
                    combos[nk] = v
            except Exception as e:
                logger.error(f"Error getting combos from plugin {pid}: {e}")
                
    return combos, conflicts

def get_plugin_extra_items():
    items = []
    for pid, pinfo in loaded_plugins.items():
        if not pinfo["enabled"]:
            continue
        mod = pinfo["module"]
        extra = getattr(mod, "EXTRA_STARTING_ITEMS", [])
        items.extend(extra)
    return items

def notify_plugins_combination(a, b, result):
    for pid, pinfo in loaded_plugins.items():
        if not pinfo["enabled"]:
            continue
        mod = pinfo["module"]
        fn = getattr(mod, "on_combination", None)
        if fn:
            try:
                fn(a, b, result)
            except Exception as e:
                logger.error(f"Error notifying plugin {pid} of combination: {e}")

def get_loaded_plugins():
    return loaded_plugins