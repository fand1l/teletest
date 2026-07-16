import os
import json
import asyncio
import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../channels_config.json"))
config_lock = asyncio.Lock()

# In-memory cache: (path, mtime, config). The file is only re-read from disk
# when its mtime changes (e.g. manual edits while the bot is running), so the
# hot path (one check per incoming Telegram message) costs a single os.stat
# instead of a full read + JSON parse.
_cache: Optional[Tuple[str, int, Dict[str, Any]]] = None


def _read_config_from_disk() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load channels config: {e}")
        return {}


def _load_config() -> Dict[str, Any]:
    """Returns the cached config, re-reading from disk only if the file changed."""
    global _cache
    try:
        mtime = os.stat(CONFIG_PATH).st_mtime_ns
    except OSError:
        mtime = -1

    if _cache is not None:
        cached_path, cached_mtime, cached_config = _cache
        if cached_path == CONFIG_PATH and cached_mtime == mtime:
            return cached_config

    config = _read_config_from_disk()
    _cache = (CONFIG_PATH, mtime, config)
    return config


def _save_config(config: Dict[str, Any]):
    global _cache
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        try:
            mtime = os.stat(CONFIG_PATH).st_mtime_ns
        except OSError:
            mtime = -1
        _cache = (CONFIG_PATH, mtime, config)
    except Exception as e:
        logger.error(f"Failed to save channels config: {e}")


async def is_channel_monitored(channel_id: int, channel_name: str, username: Optional[str] = None) -> bool:
    """
    Checks if a channel is monitored.
    If it's not present in the config, automatically adds it with enabled=True.
    """
    async with config_lock:
        config = await asyncio.to_thread(_load_config)
        key = str(channel_id)

        if key not in config:
            config[key] = {
                "name": channel_name,
                "username": username,
                "enabled": True
            }
            await asyncio.to_thread(_save_config, config)
            logger.info(f"Added new channel to config: {channel_name} (ID: {channel_id}) with enabled=True")
            return True

        return config[key].get("enabled", True)


async def sync_all_channels(channels: list[Dict[str, Any]]):
    """
    Syncs a list of channels to the config file at startup.
    channels is a list of dicts: {'id': id, 'name': name, 'username': username}
    """
    async with config_lock:
        config = await asyncio.to_thread(_load_config)
        updated = False
        for ch in channels:
            key = str(ch['id'])
            if key not in config:
                config[key] = {
                    "name": ch['name'],
                    "username": ch.get('username'),
                    "enabled": True
                }
                updated = True
                logger.info(f"Discovered new channel on startup: {ch['name']} (ID: {ch['id']})")

        if updated:
            await asyncio.to_thread(_save_config, config)
            logger.info("Synced new channels to config successfully.")


async def get_all_monitored_channels() -> list[int]:
    """
    Returns a list of all channel IDs that have enabled=True in the config.
    """
    async with config_lock:
        config = await asyncio.to_thread(_load_config)
        return [int(k) for k, v in config.items() if v.get("enabled", True)]


async def set_channel_enabled(channel_id: int, enabled: bool) -> bool:
    """
    Enables/disables monitoring for a channel. Returns True if the channel existed.
    """
    async with config_lock:
        config = await asyncio.to_thread(_load_config)
        key = str(channel_id)
        if key not in config:
            return False
        config[key]["enabled"] = enabled
        await asyncio.to_thread(_save_config, config)
        logger.info(f"Channel {channel_id} monitoring set to enabled={enabled}")
        return True
