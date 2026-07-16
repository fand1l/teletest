import os
import json
import asyncio
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../channels_config.json"))
config_lock = asyncio.Lock()

def _load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load channels config: {e}")
        return {}

def _save_config(config: Dict[str, Any]):
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
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
