import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from pod_pi.cli import Instance, LABEL, REPO, validate


class CLITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pod pi test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.bin = self.root / "bin"
        self.config = self.root / "config"
        self.base = [str(REPO / "bin/pod-pi"), "--data-dir", str(self.data),
                     "--bin-dir", str(self.bin), "--config-dir", str(self.config)]

    def cli(self, *args, ok=True):
        result = subprocess.run([*self.base, *args], text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def test_isolated_instances_and_launcher_with_spaces(self):
        self.cli("create", "alpha")
        secret = self.data / "alpha/home/private"
        secret.write_text("never copy this")
        self.cli("create", "beta")
        self.assertFalse((self.data / "beta/home/private").exists())
        result = subprocess.run([self.bin / "alpha-pi", "path"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.data / "alpha"))
        self.assertIn("alpha-pi", self.cli("list").stdout)
        self.assertEqual((self.data / "alpha").stat().st_mode & 0o777, 0o700)
        self.cli("create", "alpha", ok=False)
        self.assertEqual(secret.read_text(), "never copy this")

    def test_refuse_existing_and_dangling_commands(self):
        self.bin.mkdir()
        (self.bin / "alpha-pi").symlink_to(self.root / "missing")
        self.cli("create", "alpha", ok=False)
        self.assertFalse((self.data / "alpha").exists())

    def test_refuse_command_elsewhere_on_path(self):
        extra = self.root / "other-bin"
        extra.mkdir()
        cmd = extra / "alpha-pi"
        cmd.write_text("#!/bin/sh\nexit 0\n")
        cmd.chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(extra) + os.pathsep + os.environ["PATH"]}):
            self.cli("create", "alpha", ok=False)

    def test_reject_invalid_names(self):
        for name in ("../escape", "a b", "A", "pod", "a;true", "-x"):
            self.cli("create", "--", name, ok=False)
        self.assertFalse(self.data.exists())

    def test_defaults_and_explicit_empty_extensions(self):
        self.config.mkdir()
        (self.config / "defaults.json").write_text(json.dumps({"settings": {
            "defaultProvider": "test", "packages": ["npm:demo@1.0.0"]}}))
        extensions = self.root / "extensions.json"
        extensions.write_text("[]")
        self.cli("create", "alpha", "--profile", "nvidia", "--extensions", str(extensions))
        settings = json.loads((self.data / "alpha/home/.pi/agent/settings.json").read_text())
        self.assertEqual(settings["packages"], [])
        self.assertEqual(settings["defaultProvider"], "test")
        self.assertEqual(settings["theme"], "dark")
        config = json.loads((self.data / "alpha/instance.json").read_text())
        self.assertIn("nvidia.com/gpu=all", config["create_args"])

    def test_bad_profile_leaves_no_instance(self):
        profile = self.root / "bad.json"
        profile.write_text('{"create_args": "--privileged"}')
        self.cli("create", "alpha", "--profile", str(profile), ok=False)
        self.assertFalse((self.data / "alpha").exists())

    def test_image_sharing_and_customization(self):
        self.cli("create", "alpha")
        self.cli("create", "beta")
        a, b = (Instance(self.data / name) for name in ("alpha", "beta"))
        self.assertEqual(a.image(), b.image())
        (b.root / "container/extra").write_text("custom dependency")
        self.assertNotEqual(a.image(), b.image())

    def test_foreign_container_is_rejected(self):
        self.cli("create", "alpha")
        instance = Instance(self.data / "alpha")
        info = [{"Config": {"Labels": {LABEL: "/other/instance"}}, "State": {"Status": "running"}}]
        with patch("pod_pi.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0)), \
                patch("pod_pi.cli.output", return_value=json.dumps(info)):
            with self.assertRaisesRegex(ValueError, "another installation"):
                instance.state()

    def test_recreate_requires_stopped_container(self):
        self.cli("create", "alpha")
        instance = Instance(self.data / "alpha")
        with patch.object(instance, "state", return_value="running"), patch.object(instance, "pod") as pod:
            with self.assertRaisesRegex(ValueError, "stop first"):
                instance.action("recreate")
            pod.assert_not_called()

    def test_running_start_does_not_restart(self):
        self.cli("create", "alpha")
        instance = Instance(self.data / "alpha")
        instance.unit = self.root / "absent.service"
        with patch.object(instance, "state", return_value="running"), patch.object(instance, "pod") as pod:
            instance.start()
            pod.assert_not_called()

    def test_attach_requires_terminal_before_start(self):
        self.cli("create", "alpha")
        result = self.cli("run", "alpha", ok=False)
        self.assertIn("interactive terminal", result.stderr)

    def test_install_is_idempotent_and_collision_safe(self):
        self.cli("install")
        self.cli("install")
        target = self.bin / "pod-pi"
        target.unlink()
        target.write_text("keep me")
        self.cli("install", ok=False)
        self.assertEqual(target.read_text(), "keep me")

    def test_service_enable_uses_podman_49_compatible_commands(self):
        self.cli("create", "alpha")
        instance = Instance(self.data / "alpha")
        instance.unit = self.root / "units/alpha-pi.service"
        with patch.object(instance, "start"), patch("pod_pi.cli.run") as run:
            instance.service(True)
            self.assertEqual(run.call_args_list[-1].args[0],
                             ["systemctl", "--user", "enable", "--now", "alpha-pi.service"])
            self.assertFalse(any("update" in call.args[0] for call in run.call_args_list))
            self.assertIn("start --attach alpha-pi", instance.unit.read_text())
            instance.service(False)
            self.assertTrue(instance.unit.exists())
            self.assertEqual(run.call_args_list[-1].args[0],
                             ["systemctl", "--user", "disable", "alpha-pi.service"])

    def test_foreign_service_is_rejected_before_start(self):
        self.cli("create", "alpha")
        instance = Instance(self.data / "alpha")
        instance.unit = self.root / "alpha-pi.service"
        instance.unit.write_text("# another application\n")
        with patch.object(instance, "ready") as ready:
            with self.assertRaisesRegex(ValueError, "unrelated service"):
                instance.action("start")
            ready.assert_not_called()


class PackageSyncTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("sync_packages", REPO / "container/sync-packages.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.agent = self.home / ".pi/agent"
        self.agent.mkdir(parents=True)
        self.settings = self.agent / "settings.json"
        self.packages = [{"source": "npm:demo@1.0.0", "extensions": ["tools.ts"]}]
        self.settings.write_text(json.dumps({"theme": "light", "packages": self.packages}))

    def test_install_once_preserve_filters_and_handle_removal(self):
        def install(*args, **kwargs):
            self.settings.write_text(json.dumps({"theme": "light", "packages": ["npm:demo@1.0.0"]}))
        with patch.object(self.module.Path, "home", return_value=self.home), \
                patch.object(self.module.subprocess, "run", side_effect=install) as run:
            self.module.main()
            self.module.main()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(json.loads(self.settings.read_text())["packages"], self.packages)
            self.settings.write_text('{"theme":"light","packages":[]}')
            self.module.main()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(json.loads(self.settings.read_text())["packages"], [])

    def test_failed_install_retries_and_restores_list(self):
        with patch.object(self.module.Path, "home", return_value=self.home), \
                patch.object(self.module.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "pi")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.module.main()
        self.assertFalse((self.agent / ".pod-pi-packages").exists())
        self.assertEqual(json.loads(self.settings.read_text())["packages"], self.packages)


if __name__ == "__main__":
    unittest.main()
