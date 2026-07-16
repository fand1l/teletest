import os
import json
import pytest
from unittest.mock import patch
from src.core.channels_config import is_channel_monitored

@pytest.mark.asyncio
async def test_channels_config_lifecycle(tmp_path):
    temp_config_file = tmp_path / "channels_config.json"
    
    with patch("src.core.channels_config.CONFIG_PATH", str(temp_config_file)):
        # 1. Check a new channel. It should be added and return True.
        res1 = await is_channel_monitored(12345, "Test Channel 1", "test_channel_1")
        assert res1 is True
        
        # Verify the file was written
        assert os.path.exists(temp_config_file)
        with open(temp_config_file, "r") as f:
            data = json.load(f)
        assert data["12345"] == {
            "name": "Test Channel 1",
            "username": "test_channel_1",
            "enabled": True
        }
        
        # 2. Check it again. It should return True.
        res2 = await is_channel_monitored(12345, "Test Channel 1", "test_channel_1")
        assert res2 is True
        
        # 3. Disable the channel manually in the file.
        data["12345"]["enabled"] = False
        with open(temp_config_file, "w") as f:
            json.dump(data, f)
            
        # 4. Check again. It should return False.
        res3 = await is_channel_monitored(12345, "Test Channel 1", "test_channel_1")
        assert res3 is False
        
        # 5. Check another channel. It should be added as enabled.
        res4 = await is_channel_monitored(67890, "Test Channel 2", None)
        assert res4 is True
        
        with open(temp_config_file, "r") as f:
            data_updated = json.load(f)
        assert data_updated["67890"] == {
            "name": "Test Channel 2",
            "username": None,
            "enabled": True
        }
