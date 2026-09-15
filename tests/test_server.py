import unittest

from lib_arcade.config import AdapterConfig
from lib_arcade.server import (
    _evaluate_idle_shutdown,
    _handler_accepts_body,
    _merge_actions,
    _safe_stats,
)


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
        update_check_seconds=1800.0,
        idle_shutdown_enabled=False,
        idle_shutdown_minutes=30.0,
    )
    defaults.update(overrides)
    return AdapterConfig(**defaults)


class MergeActionsTests(unittest.TestCase):
    def test_defaults_to_start_stop_update(self):
        config = make_config()
        actions, handlers = _merge_actions(config, None)
        self.assertEqual(actions, ["start", "stop", "update"])
        self.assertEqual(set(handlers), {"start", "stop", "update"})

    def test_extra_actions_are_appended_after_start_stop_update(self):
        config = make_config()
        extra = {"restart_round": lambda: (True, "restarted")}
        actions, handlers = _merge_actions(config, extra)
        self.assertEqual(actions, ["start", "stop", "update", "restart_round"])
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
        self.assertEqual(actions, ["start", "stop", "update", "kick_bots", "restart_round"])

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
                "update",
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
            [
                "start",
                "stop",
                "update",
                "restart_round",
                {"name": "apply_preset", "label": "Apply preset"},
            ],
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


class EvaluateIdleShutdownTests(unittest.TestCase):
    def test_positive_count_resets_timer(self):
        last_active, should_stop = _evaluate_idle_shutdown(
            last_active=100.0,
            now=2000.0,
            became_active=False,
            player_count=3,
            idle_shutdown_minutes=30.0,
        )
        self.assertEqual(last_active, 2000.0)
        self.assertFalse(should_stop)

    def test_zero_count_under_threshold_does_not_stop(self):
        # 10 minutes elapsed, threshold is 30 -- not idle long enough yet.
        last_active, should_stop = _evaluate_idle_shutdown(
            last_active=1000.0,
            now=1000.0 + 10 * 60,
            became_active=False,
            player_count=0,
            idle_shutdown_minutes=30.0,
        )
        self.assertEqual(last_active, 1000.0)
        self.assertFalse(should_stop)

    def test_zero_count_over_threshold_stops(self):
        # 31 minutes elapsed, threshold is 30 -- idle long enough.
        last_active, should_stop = _evaluate_idle_shutdown(
            last_active=1000.0,
            now=1000.0 + 31 * 60,
            became_active=False,
            player_count=0,
            idle_shutdown_minutes=30.0,
        )
        self.assertEqual(last_active, 1000.0 + 31 * 60)
        self.assertTrue(should_stop)

    def test_none_count_never_stops_regardless_of_elapsed_time(self):
        # A huge elapsed time would trigger a stop for count==0, but None
        # means "inconclusive" and must never be treated as zero players.
        last_active, should_stop = _evaluate_idle_shutdown(
            last_active=0.0,
            now=1_000_000.0,
            became_active=False,
            player_count=None,
            idle_shutdown_minutes=30.0,
        )
        self.assertEqual(last_active, 1_000_000.0)
        self.assertFalse(should_stop)

    def test_status_transition_into_running_resets_timer(self):
        # Even with a stale last_active and a zero count that would
        # otherwise be well over threshold, a fresh boot resets the clock.
        last_active, should_stop = _evaluate_idle_shutdown(
            last_active=0.0,
            now=1_000_000.0,
            became_active=True,
            player_count=0,
            idle_shutdown_minutes=30.0,
        )
        self.assertEqual(last_active, 1_000_000.0)
        self.assertFalse(should_stop)


if __name__ == "__main__":
    unittest.main()
