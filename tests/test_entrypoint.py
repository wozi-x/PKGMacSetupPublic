"""No live host observation: entrypoint consent and inventory boundary fixtures."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("public_setup_test", ROOT / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class EntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="public-entrypoint-qa-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.selected = self.root / "selected.yml"
        self.selected.write_text("schema_version: 1\nprofile: dev\npackages: {}\n")
        self.inventory = self.root / "inventory"
        self.inventory.write_text("fixture ansible_connection=ssh\n")
        self.host = {"ansible_connection": "ssh", "ansible_host": "fixture.invalid", "ansible_user": "fixture",
                     "ansible_python_interpreter": "/opt/fixture/bin/python3"}

    def main(self, arguments):
        with patch.object(sys, "argv", ["setup.py", "--config", str(self.selected), *arguments]), \
             patch.object(setup.subprocess, "run", side_effect=AssertionError("no processes during offline/declined scope")), \
             patch.object(setup, "observe", side_effect=AssertionError("no providers during offline/declined scope")), \
             patch("builtins.input", side_effect=AssertionError("no input during noninteractive scope")), \
             contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            status = setup.main()
        return status, out.getvalue(), err.getvalue()

    def test_plan_is_offline_even_with_remote_inventory_and_ambient_auth(self):
        with patch.dict(os.environ, {"ANSIBLE_BECOME_PASSWORD_FILE": "/forbidden", "BASH_ENV": "/forbidden"}):
            status, output, _ = self.main(["--plan", "-i", str(self.inventory)])
        self.assertEqual(status, 3)
        self.assertFalse(json.loads(output)["executable"])
        self.assertEqual(self.selected.read_text(), "schema_version: 1\nprofile: dev\npackages: {}\n")

    def test_noninteractive_apply_stops_before_inventory_auth_or_input(self):
        status, _, error = self.main(["--apply", "--non-interactive", "-i", str(self.inventory)])
        self.assertEqual(status, 2)
        self.assertIn("Needs human", error)

    def inspect(self, host, extra_host=False):
        native = {"all": {"hosts": ["fixture"] + (["other"] if extra_host else [])}, "_meta": {"hostvars": {"fixture": host}}}
        result = types.SimpleNamespace(returncode=0, stdout=json.dumps(native))
        with patch.object(setup.subprocess, "run", return_value=result):
            return setup.inspect_inventory(self.inventory)

    def test_native_identity_whitelist_rejects_command_hooks_and_multiple_hosts(self):
        for key in ("ansible_ssh_executable", "ansible_ssh_common_args", "ansible_become_method", "ansible_password", "mac_setup_checked"):
            with self.subTest(key=key), self.assertRaises(setup.EngineError):
                self.inspect({**self.host, key: "FORBIDDEN"})
        with self.assertRaises(setup.EngineError):
            self.inspect(self.host, extra_host=True)
        self.assertEqual(self.inspect(self.host)["connection"], "ssh")

    def test_local_identity_must_be_current_user_and_loopback(self):
        for host, user in (("fixture.invalid", setup.ENV["USER"]), ("localhost", "wrong-user")):
            with self.subTest(host=host, user=user), self.assertRaises(setup.EngineError):
                self.inspect({**self.host, "ansible_connection": "local", "ansible_host": host, "ansible_user": user})
        result = self.inspect({**self.host, "ansible_connection": "local", "ansible_host": "localhost", "ansible_user": setup.ENV["USER"]})
        self.assertEqual(result["connection"], "local")

    def test_adjacent_variables_change_fingerprint_and_links_fail_closed(self):
        variables = self.root / "host_vars"
        variables.mkdir()
        record = variables / "fixture.yml"
        record.write_text("ansible_user: fixture\n")
        before = setup.inventory_fingerprint(self.inventory)
        record.write_text("ansible_user: changed\n")
        self.assertNotEqual(before, setup.inventory_fingerprint(self.inventory))
        record.unlink()
        record.symlink_to(self.selected)
        with self.assertRaises(setup.EngineError):
            setup.inventory_fingerprint(self.inventory)


if __name__ == "__main__":
    unittest.main(verbosity=2)
