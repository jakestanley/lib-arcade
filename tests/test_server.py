import unittest

from lib_arcade.config import AdapterConfig
from lib_arcade.server import _merge_actions


def make_config(**overrides) -> AdapterConfig:
    defaults = dict(
        server_id="arcade-test",
        server_name="Test",
        server_description="Test server",
        adapter_port=8300,
        arcade_base_url="https://arcade.stanley.arpa",
        heartbeat_seconds=30.0,
        adapter_base_url_override="http://adler.stanley.arpa:8300",
        homelab_ca_file="/nonexistent-ca.crt",
        compose_project="arcade-test",
        compose_service="test",
        stop_timeout_seconds=30,
        upnp_enabled=False,
        forward_port=0,
        forward_protocols=("udp",),
    )
    defaults.update(overrides)
    return AdapterConfig(**defaults)


class MergeActionsTests(unittest.TestCase):
    def test_defaults_to_start_stop_only(self):
        config = make_config()
        actions, handlers = _merge_actions(config, None)
        self.assertEqual(actions, ["start", "stop"])
        self.assertEqual(set(handlers), {"start", "stop"})

    def test_extra_actions_are_appended_after_start_stop(self):
        config = make_config()
        extra = {"restart_round": lambda: (True, "restarted")}
        actions, handlers = _merge_actions(config, extra)
        self.assertEqual(actions, ["start", "stop", "restart_round"])
        self.assertIn("restart_round", handlers)

    def test_extra_action_handler_is_called_through(self):
        config = make_config()
        extra = {"backup_now": lambda: (True, "ok")}
        _, handlers = _merge_actions(config, extra)
        ok, status = handlers["backup_now"]()
        self.assertTrue(ok)
        self.assertEqual(status, "ok")

    def test_multiple_extra_actions_preserve_declaration_order(self):
        config = make_config()
        extra = {
            "kick_bots": lambda: (True, "ok"),
            "restart_round": lambda: (True, "ok"),
        }
        actions, _ = _merge_actions(config, extra)
        self.assertEqual(actions, ["start", "stop", "kick_bots", "restart_round"])


if __name__ == "__main__":
    unittest.main()
