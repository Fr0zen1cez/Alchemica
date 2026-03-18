# Core module initialization
from .logger import get_logger
from .config import load_config, save_config, get_base_dir, get_resource_dir
from .save import (load_save, write_save, backup_save, default_save, get_save_path,
                   STARTER_ITEMS, WORLDS, WORLDS_BY_ID, _make_world_data,
                   migrate_save, sync_active_world, flush_active_world)
from .ai import ai_combine, ai_generate_tags, get_ai_stats, AIError
from .plugins import discover_plugins, get_plugin_combos, get_plugin_extra_items, notify_plugins_combination, get_loaded_plugins

__all__ = [
    'get_logger',
    'load_config', 'save_config', 'get_base_dir', 'get_resource_dir',
    'load_save', 'write_save', 'backup_save', 'default_save', 'get_save_path',
    'STARTER_ITEMS', 'WORLDS', 'WORLDS_BY_ID', '_make_world_data',
    'migrate_save', 'sync_active_world', 'flush_active_world',
    'ai_combine', 'ai_generate_tags', 'get_ai_stats', 'AIError',
    'discover_plugins', 'get_plugin_combos', 'get_plugin_extra_items', 'notify_plugins_combination', 'get_loaded_plugins'
]