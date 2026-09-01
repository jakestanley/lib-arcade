import unittest

from lib_arcade.config import AdapterConfig
from lib_arcade.server import _handler_accepts_body, _merge_actions, _safe_stats


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

    def test_rich_extra_action_is_advertised_as_object(self):
        config = make_config()
        extra = {
            "apply_preset": {
                "handler": lambda body: (True, f"applied {body.get('preset')}"),
                "label": "Apply preset",
                "params": [
                    {
                        "name": "preset",
                        "type": "enum",
                        "label": "Preset",
                        "options": ["casual", "competitive"],
                        "default": "casual",
                    }
                ],
            }
        }
        actions, handlers = _merge_actions(config, extra)
        self.assertEqual(
            actions,
            [
                "start",
                "stop",
                {
                    "name": "apply_preset",
                    "label": "Apply preset",
                    "params": [
                        {
                            "name": "preset",
                            "type": "enum",
                            "label": "Preset",
                            "options": ["casual", "competitive"],
                            "default": "casual",
                        }
                    ],
                },
            ],
        )
        ok, status = handlers["apply_preset"]({"preset": "casual"})
        self.assertTrue(ok)
        self.assertEqual(status, "applied casual")

    def test_bare_and_rich_extra_actions_can_be_mixed(self):
        config = make_config()
        extra = {
            "restart_round": lambda: (True, "ok"),
            "apply_preset": {"handler": lambda body: (True, "ok"), "label": "Apply preset"},
        }
        actions, _ = _merge_actions(config, extra)
        self.assertEqual(
            actions,
            ["start", "stop", "restart_round", {"name": "apply_preset", "label": "Apply preset"}],
        )


class HandlerAcceptsBodyTests(unittest.TestCase):
    def test_zero_arg_handler_does_not_accept_body(self):
        self.assertFalse(_handler_accepts_body(lambda: (True, "ok")))

    def test_one_arg_handler_accepts_body(self):
        self.assertTrue(_handler_accepts_body(lambda body: (True, "ok")))


class SafeStatsTests(unittest.TestCase):
    def test_none_stats_fn_returns_empty_list(self):
        self.assertEqual(_safe_stats(None), [])

    def test_stats_fn_result_is_returned(self):
        stats = [{"label": "Map", "value": "de_nuke"}]
        self.assertEqual(_safe_stats(lambda: stats), stats)

    def test_stats_fn_exception_returns_empty_list(self):
        def boom():
            raise RuntimeError("rcon unreachable")

        self.assertEqual(_safe_stats(boom), [])


if __name__ == "__main__":
    unittest.main()
