import unittest
from unittest.mock import MagicMock, patch

from lib_arcade import docker_control
from lib_arcade.config import AdapterConfig


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


class FindTargetContainerTests(unittest.TestCase):
    def test_returns_none_when_no_container_matches(self):
        config = make_config()
        with patch.object(docker_control, "docker_client") as mock_client:
            mock_client.containers.list.return_value = []
            self.assertIsNone(docker_control.find_target_container(config))
            mock_client.containers.list.assert_called_once_with(
                all=True,
                filters={
                    "label": [
                        "com.docker.compose.project=arcade-test",
                        "com.docker.compose.service=test",
                    ]
                },
            )

    def test_returns_first_matching_container(self):
        config = make_config()
        container = MagicMock()
        with patch.object(docker_control, "docker_client") as mock_client:
            mock_client.containers.list.return_value = [container]
            self.assertIs(docker_control.find_target_container(config), container)


class CurrentStatusTests(unittest.TestCase):
    def test_unknown_when_no_container(self):
        config = make_config()
        with patch.object(docker_control, "find_target_container", return_value=None):
            self.assertEqual(docker_control.current_status(config), "unknown")

    def test_running_when_container_running(self):
        config = make_config()
        container = MagicMock()
        container.status = "running"
        with patch.object(docker_control, "find_target_container", return_value=container):
            self.assertEqual(docker_control.current_status(config), "running")
            container.reload.assert_called_once()

    def test_stopped_when_container_exited(self):
        config = make_config()
        container = MagicMock()
        container.status = "exited"
        with patch.object(docker_control, "find_target_container", return_value=container):
            self.assertEqual(docker_control.current_status(config), "stopped")


class DoStartStopTests(unittest.TestCase):
    def test_start_fails_when_no_container(self):
        config = make_config()
        with patch.object(docker_control, "find_target_container", return_value=None):
            ok, message = docker_control.do_start(config)
            self.assertFalse(ok)
            self.assertIn("no container found", message)

    def test_start_calls_container_start_and_syncs_port(self):
        config = make_config(upnp_enabled=True, forward_port=8211)
        container = MagicMock()
        container.status = "running"
        with patch.object(docker_control, "find_target_container", return_value=container), \
             patch.object(docker_control, "sync_port_forward") as mock_sync:
            ok, status = docker_control.do_start(config)
            self.assertTrue(ok)
            container.start.assert_called_once()
            mock_sync.assert_called_once_with(config, should_be_open=True)

    def test_stop_uses_configured_timeout(self):
        config = make_config(stop_timeout_seconds=70)
        container = MagicMock()
        container.status = "exited"
        with patch.object(docker_control, "find_target_container", return_value=container), \
             patch.object(docker_control, "sync_port_forward"):
            ok, status = docker_control.do_stop(config)
            self.assertTrue(ok)
            container.stop.assert_called_once_with(timeout=70)


class DoExecTests(unittest.TestCase):
    def test_fails_when_no_container(self):
        config = make_config()
        with patch.object(docker_control, "find_target_container", return_value=None):
            ok, message = docker_control.do_exec(config, "echo hi")
            self.assertFalse(ok)
            self.assertIn("no container found", message)

    def test_runs_command_and_returns_ok_on_zero_exit(self):
        config = make_config()
        container = MagicMock()
        container.exec_run.return_value = (0, b"done\n")
        with patch.object(docker_control, "find_target_container", return_value=container):
            ok, status = docker_control.do_exec(config, "bash /usr/local/bin/backup")
            self.assertTrue(ok)
            self.assertEqual(status, "ok")
            container.exec_run.assert_called_once_with("bash /usr/local/bin/backup")

    def test_returns_output_as_error_on_nonzero_exit(self):
        config = make_config()
        container = MagicMock()
        container.exec_run.return_value = (1, b"boom\n")
        with patch.object(docker_control, "find_target_container", return_value=container):
            ok, message = docker_control.do_exec(config, "false")
            self.assertFalse(ok)
            self.assertEqual(message, "boom\n")


class SyncPortForwardTests(unittest.TestCase):
    def test_noop_when_upnp_disabled(self):
        config = make_config(upnp_enabled=False, forward_port=8211)
        with patch("lib_arcade.docker_control.upnp") as mock_upnp:
            docker_control.sync_port_forward(config, should_be_open=True)
            mock_upnp.ensure_mapping.assert_not_called()

    def test_noop_when_no_forward_port(self):
        config = make_config(upnp_enabled=True, forward_port=0)
        with patch("lib_arcade.docker_control.upnp") as mock_upnp:
            docker_control.sync_port_forward(config, should_be_open=True)
            mock_upnp.ensure_mapping.assert_not_called()

    def test_calls_ensure_mapping_when_enabled(self):
        config = make_config(upnp_enabled=True, forward_port=8211, forward_protocols=("udp",))
        with patch("lib_arcade.docker_control.upnp") as mock_upnp:
            docker_control.sync_port_forward(config, should_be_open=True)
            mock_upnp.ensure_mapping.assert_called_once_with(
                8211, "udp", config.server_description, True
            )

    def test_calls_ensure_mapping_for_every_protocol(self):
        config = make_config(
            upnp_enabled=True, forward_port=27015, forward_protocols=("udp", "tcp")
        )
        with patch("lib_arcade.docker_control.upnp") as mock_upnp:
            docker_control.sync_port_forward(config, should_be_open=True)
            self.assertEqual(mock_upnp.ensure_mapping.call_count, 2)
            mock_upnp.ensure_mapping.assert_any_call(
                27015, "udp", config.server_description, True
            )
            mock_upnp.ensure_mapping.assert_any_call(
                27015, "tcp", config.server_description, True
            )


if __name__ == "__main__":
    unittest.main()
