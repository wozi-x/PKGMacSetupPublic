"""Public prerequisite scripts under a substituted, temporary OS boundary."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="public-bootstrap-qa-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "commands"
        self.source = (ROOT / "bootstrap.sh").read_text()
        self.assertTrue(self.source.startswith("#!/bin/bash -p\n"))
        self.assertEqual(self.source.count('[[ -t 0 && -t 1 ]]'), 1)
        self.source = self.source.replace('[[ -t 0 && -t 1 ]]', '[[ "${QA_INTERACTIVE-}" == true ]]')
        self.source = self.source.replace('/usr/bin/xcode-select', str(self.bin / "xcode-select"))
        self.source = self.source.replace('/usr/bin/curl', str(self.bin / "curl"))
        self.source = self.source.replace('/opt/homebrew/bin/brew', str(self.bin / "brew"))
        self.source = self.source.replace('/usr/local/bin/brew', str(self.bin / "brew"))
        self.source = self.source.replace('/usr/bin/uname', str(self.bin / "uname"))
        self.source = self.source.replace('/private/tmp/macsetup-prerequisites.', str(self.root / "macsetup-prerequisites."))
        self.script = self.root / "bootstrap.sh"
        self.script.write_text(self.source)
        self.script.chmod(0o755)
        self.command("uname", "if [[ \"$1\" == -s ]]; then echo Darwin; else echo arm64; fi")
        self.command("xcode-select", "if [[ \"$1\" == -p ]]; then exit " + "${QA_CLT_MISSING:-0}" + "; fi")
        self.command("curl", "exit 91")
        self.command("brew", "exit 92")
        self.command("uv", "exit 93")

    def command(self, name, body):
        target = self.bin / name
        target.write_text("#!/bin/bash -p\nset -eu\nprintf '%s\\n' " + repr(name + " $*") + " >> " + repr(str(self.log)) + "\n" + body + "\n")
        target.chmod(0o755)

    def run_script(self, answer="prepare\n", **extra):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home), "QA_INTERACTIVE": "true", **extra}
        return subprocess.run([str(self.script)], input=answer, text=True, capture_output=True, env=env, timeout=15)

    def records(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_canceled_scope_does_not_touch_prerequisites_or_storage(self):
        result = self.run_script("no\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / "Library").exists())
        self.assertFalse(any(line.startswith(("brew", "uv", "curl", "xcode-select")) for line in self.records()))

    def test_noninteractive_does_not_read_input_or_open_os_dialog(self):
        result = self.run_script("", QA_INTERACTIVE="false")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Needs human", result.stderr)
        self.assertFalse(any(line.startswith(("brew", "uv", "curl", "xcode-select")) for line in self.records()))

    def test_missing_clt_requests_only_reviewed_os_dialog_then_stops(self):
        result = self.run_script(QA_CLT_MISSING="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("finish the Apple", result.stderr)
        self.assertEqual(sum(line.startswith("xcode-select") for line in self.records()), 2)
        self.assertFalse(any(line.startswith(("brew", "uv", "curl")) for line in self.records()))

    def test_existing_controller_preserved_without_uv_or_package_changes(self):
        controller = self.home / "Library/Application Support/MacSetup/controller/bin/python"
        controller.parent.mkdir(parents=True)
        controller.write_bytes(b"#!/bin/sh\nexit 0\n")
        controller.chmod(0o755)
        before = controller.stat()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(controller.stat().st_ino, before.st_ino)
        self.assertFalse(any(line.startswith(("brew", "uv", "curl")) for line in self.records()))

    def test_controller_redirect_is_preserved_not_adopted(self):
        directory = self.home / "Library/Application Support/MacSetup"
        directory.mkdir(parents=True)
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        (unrelated / "canary").write_bytes(b"preserve")
        (directory / "controller").symlink_to(unrelated, target_is_directory=True)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((directory / "controller").is_symlink())
        self.assertEqual((unrelated / "canary").read_bytes(), b"preserve")
        self.assertFalse(any(line.startswith(("brew", "uv", "curl")) for line in self.records()))

    def test_fresh_controller_stages_then_repeat_preserves_existing_bytes(self):
        runtime = self.home / "Library/Application Support/MacSetup/bootstrap-python/cpython-3.14.7/bin/python3"
        self.command("uv", "\n".join([
            'if [[ "$2 $3" == "python install" ]]; then',
            '/bin/mkdir -p ' + repr(str(runtime.parent)),
            "printf '#!/bin/sh\\nexit 0\\n' > " + repr(str(runtime)),
            '/bin/chmod +x ' + repr(str(runtime)),
            'elif [[ "$2 $3" == "python find" ]]; then',
            'printf "%s\\n" ' + repr(str(runtime)),
            'elif [[ "$2" == venv ]]; then',
            'destination="${@: -1}"; /bin/mkdir -p "$destination/bin"',
            "printf '#!/bin/sh\\nexit 0\\n' > \"$destination/bin/python\"",
            "printf '#!/bin/sh\\nexit 0\\n' > \"$destination/bin/ansible-playbook\"",
            '/bin/chmod +x "$destination/bin/python" "$destination/bin/ansible-playbook"',
            'elif [[ "$2 $3" == "pip install" ]]; then :',
            'else exit 98; fi']))
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        controller = self.home / "Library/Application Support/MacSetup/controller/bin/python"
        before = controller.read_bytes(), controller.stat().st_ino
        first_count = sum(line.startswith("uv") for line in self.records())
        self.assertEqual(first_count, 4)
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((controller.read_bytes(), controller.stat().st_ino), before)
        self.assertEqual(sum(line.startswith("uv") for line in self.records()), first_count)

    def test_offline_wrapper_no_runtime_never_invokes_bootstrap_or_provider(self):
        wrapper = (ROOT / "setup.sh").read_text()
        self.assertTrue(wrapper.startswith("#!/bin/bash -p\n"))
        for old in ("/opt/homebrew/opt/ansible/libexec/bin/python", "/usr/local/opt/ansible/libexec/bin/python"):
            wrapper = wrapper.replace(old, str(self.root / "missing-controller"))
        entry = self.root / "setup.sh"
        entry.write_text(wrapper)
        entry.chmod(0o755)
        # Any accidental bootstrap is an inert, recorded failure.
        self.script.write_text("#!/bin/bash -p\nprintf forbidden > " + repr(str(self.root / "forbidden")) + "\nexit 99\n")
        selected = self.root / "selected.yml"
        selected.write_text("schema_version: 1\nprofile: dev\n")
        result = subprocess.run([str(entry), "--config", str(selected), "--plan"],
                                env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"}, input="", capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertFalse((self.root / "forbidden").exists())
        self.assertFalse(self.log.exists())
        self.assertFalse((self.home / "Library").exists())

    def test_public_bootstrap_contains_no_private_handoff_or_auth(self):
        sources = "\n".join((ROOT / file).read_text() for file in ("bootstrap.sh", "setup.sh", "install.sh"))
        for forbidden in ("gh auth", "gh repo", "op read", "security find", "agent/plugins"):
            self.assertNotIn(forbidden, sources)
        self.assertIn("https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh", sources)


if __name__ == "__main__":
    unittest.main(verbosity=2)
