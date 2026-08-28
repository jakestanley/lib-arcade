import os
import unittest
from unittest.mock import patch

from lib_arcade.config import AdapterConfig


class ConfigFromEnvTests(unittest.TestCase):
    def test_uses_defaults_when_env_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            config = AdapterConfig.from_env(
                default_server_id="arcade-palworld",
                default_server_name="Palworld",
                default_server_description="Palworld dedicated server (arcade-palworld)",
                default_adapter_port=8300,
                default_compose_project="arcade-palworld",
                default_compose_service="palworld",
                default_stop_timeout_seconds=30,
                default_forward_protocol="udp",
            )
        self.assertEqual(config.server_id, "arcade-palworld")
        self.assertEqual(config.adapter_port, 8300)
        self.assertEqual(config.stop_timeout_seconds, 30)
        self.assertEqual(config.forward_protocol, "udp")
        self.assertTrue(config.upnp_enabled)
        self.assertEqual(config.forward_port, 0)

    def test_env_overrides_defaults(self):
        env = {
            "ARCADE_SERVER_ID": "custom-id",
            "ARCADE_ADAPTER_PORT": "9999",
            "ARCADE_UPNP_ENABLED": "false",
            "SERVER_PORT": "25565",
            "ARCADE_FORWARD_PROTOCOL": "tcp",
        }
        with patch.dict(os.environ, env, clear=True):
            config = AdapterConfig.from_env(
                default_server_id="arcade-minecraft",
                default_server_name="Minecraft",
                default_server_description="Minecraft server (arcade-minecraft)",
                default_adapter_port=8301,
                default_compose_project="arcade-minecraft",
                default_compose_service="minecraft",
                default_stop_timeout_seconds=70,
                default_forward_protocol="tcp",
            )
        self.assertEqual(config.server_id, "custom-id")
        self.assertEqual(config.adapter_port, 9999)
        self.assertFalse(config.upnp_enabled)
        self.assertEqual(config.forward_port, 25565)
        self.assertEqual(config.forward_protocol, "tcp")


if __name__ == "__main__":
    unittest.main()
