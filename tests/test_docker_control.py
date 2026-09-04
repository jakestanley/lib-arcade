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
        update_check_seconds=1800.0,
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


class CheckForUpdateTests(unittest.TestCase):
    def test_false_when_no_container(self):
        config = make_config()
        with patch.object(docker_control, "find_target_container", return_value=None):
            self.assertFalse(docker_control.check_for_update(config))

    def test_true_when_pulled_image_differs(self):
        config = make_config()
        container = MagicMock()
        container.attrs = {"Config": {"Image": "joedwards32/cs2:latest"}}
        container.image.id = "sha256:old"
        pulled = MagicMock(id="sha256:new")
        with patch.object(docker_control, "find_target_container", return_value=container), \
             patch.object(docker_control, "docker_client") as mock_client:
            mock_client.images.pull.return_value = pulled
            self.assertTrue(docker_control.check_for_update(config))
            mock_client.images.pull.assert_called_once_with("joedwards32/cs2", tag="latest")

    def test_false_when_pulled_image_matches(self):
        config = make_config()
        container = MagicMock()
        container.attrs = {"Config": {"Image": "joedwards32/cs2:latest"}}
        container.image.id = "sha256:same"
        pulled = MagicMock(id="sha256:same")
        with patch.object(docker_control, "find_target_container", return_value=container), \
             patch.object(docker_control, "docker_client") as mock_client:
            mock_client.images.pull.return_value = pulled
            self.assertFalse(docker_control.check_for_update(config))

    def test_false_on_docker_error(self):
        config = make_config()
        with patch.object(
            docker_control, "find_target_container", side_effect=docker_control.DockerException("boom")
        ):
            self.assertFalse(docker_control.check_for_update(config))


class DoUpdateTests(unittest.TestCase):
    def test_fails_when_no_container(self):
        config = make_config()
        with patch.object(docker_control, "find_target_container", return_value=None):
            ok, message = docker_control.do_update(config)
            self.assertFalse(ok)
            self.assertIn("no container found", message)

    def test_already_up_to_date_does_not_recreate(self):
        config = make_config()
        container = MagicMock()
        container.attrs = {"Config": {"Image": "joedwards32/cs2:latest"}}
        container.image.id = "sha256:same"
        pulled = MagicMock(id="sha256:same")
        with patch.object(docker_control, "find_target_container", return_value=container), \
             patch.object(docker_control, "docker_client") as mock_client:
            mock_client.images.pull.return_value = pulled
            ok, message = docker_control.do_update(config)
            self.assertTrue(ok)
            self.assertEqual(message, "already up to date")
            container.remove.assert_not_called()

    def test_recreates_and_restarts_when_running(self):
        config = make_config()
        container = MagicMock()
        container.status = "running"
        container.attrs = {
            "Config": {
                "Image": "joedwards32/cs2:latest",
                "Env": ["FOO=bar"],
                "Labels": {"l": "v"},
                "Entrypoint": None,
                "Cmd": None,
            },
            "HostConfig": {"NetworkMode": "host"},
            "Name": "/arcade-cs2-cs2-1",
        }
        container.image.id = "sha256:old"
        pulled = MagicMock(id="sha256:new")
        with patch.object(docker_control, "find_target_container", return_value=container), \
             patch.object(docker_control, "docker_client") as mock_client, \
             patch.object(docker_control, "sync_port_forward") as mock_sync:
            mock_client.images.pull.return_value = pulled
            mock_client.api.create_container.return_value = {"Id": "newid"}
            ok, message = docker_control.do_update(config)
            self.assertTrue(ok)
            container.remove.assert_called_once_with(force=True)
            mock_client.api.create_container.assert_called_once_with(
                image="sha256:new",
                name="arcade-cs2-cs2-1",
                environment=["FOO=bar"],
                labels={"l": "v"},
                entrypoint=None,
                command=None,
                host_config={"NetworkMode": "host"},
            )
            mock_client.api.start.assert_called_once_with("newid")
            mock_sync.assert_called_once_with(config, should_be_open=True)
            self.assertIn("restarted", message)

    def test_recreates_but_leaves_stopped_container_stopped(self):
        config = make_config()
        container = MagicMock()
        container.status = "exited"
        container.attrs = {
            "Config": {
                "Image": "joedwards32/cs2:latest",
                "Env": [],
                "Labels": {},
                "Entrypoint": None,
                "Cmd": None,
            },
            "HostConfig": {},
            "Name": "/arcade-cs2-cs2-1",
        }
        container.image.id = "sha256:old"
        pulled = MagicMock(id="sha256:new")
        with patch.object(docker_control, "find_target_container", return_value=container), \
             patch.object(docker_control, "docker_client") as mock_client, \
             patch.object(docker_control, "sync_port_forward") as mock_sync:
            mock_client.images.pull.return_value = pulled
            mock_client.api.create_container.return_value = {"Id": "newid"}
            ok, message = docker_control.do_update(config)
            self.assertTrue(ok)
            mock_client.api.start.assert_not_called()
            mock_sync.assert_called_once_with(config, should_be_open=False)
            self.assertIn("left stopped", message)

    def test_error_returns_false_and_message(self):
        config = make_config()
        with patch.object(
            docker_control, "find_target_container", side_effect=docker_control.DockerException("boom")
        ):
            ok, message = docker_control.do_update(config)
            self.assertFalse(ok)
            self.assertEqual(message, "boom")


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
