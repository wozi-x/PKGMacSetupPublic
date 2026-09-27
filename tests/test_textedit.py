"""TextEdit belongs to the public Base preference selection."""
import json
import unittest
import test_base_setup


class TextEditTests(unittest.TestCase):
    setUp = test_base_setup.BaseSetupTests.setUp
    run_base = test_base_setup.BaseSetupTests.run_base
    calls = test_base_setup.BaseSetupTests.calls
    selection = test_base_setup.BaseSetupTests.selection

    def test_default_plain_text_and_idempotent_rerun(self):
        self.run_base()
        key = "com.apple.TextEdit RichText"
        self.assertEqual(json.loads(self.state.read_text())["preferences"][key], "0")
        before = len(self.calls())
        self.run_base()
        self.run_base("--check")
        self.assertFalse(any(c[0] == "defaults" and c[1][0] == "write"
                             for c in self.calls()[before:]))

    def test_explicit_local_opt_in_and_check_only(self):
        args = self.selection(prefs="textedit=true\n")
        output = self.run_base(*args, "--check", code=1)
        self.assertIn("com.apple.TextEdit RichText -> 0", output)
        self.assertFalse(any(c[0] == "defaults" and c[1][0] == "write" for c in self.calls()))
        self.run_base(*args)
        self.assertEqual(json.loads(self.state.read_text())["preferences"]["com.apple.TextEdit RichText"], "0")

    def test_disabled_or_omitted_preserves_existing_format(self):
        for prefs in ("textedit=false\n", "finder=true\n"):
            with self.subTest(prefs=prefs):
                state = json.loads(self.state.read_text())
                state["preferences"]["com.apple.TextEdit RichText"] = "1"
                self.state.write_text(json.dumps(state))
                before = len(self.calls())
                self.run_base(*self.selection(prefs=prefs))
                self.assertEqual(json.loads(self.state.read_text())["preferences"]["com.apple.TextEdit RichText"], "1")
                self.assertFalse(any(c[0] == "defaults" and "com.apple.TextEdit" in c[1]
                                     for c in self.calls()[before:]))

    def test_write_denial_warns_and_continues_then_can_be_repaired(self):
        state = json.loads(self.state.read_text())
        state["preference_failures"] = {"com.apple.TextEdit RichText": "write"}
        self.state.write_text(json.dumps(state))
        output = self.run_base(code=3)
        self.assertIn("Full Disk Access", output)
        self.assertIn("TextEdit > Settings > New Document > Plain text", output)
        self.assertIn("→ Shell configuration", output)
        self.assertIn("→ App Store apps", output)
        self.assertTrue((self.home / ".zshrc").is_file())
        self.assertNotIn("✓ Base setup complete", output)
        state = json.loads(self.state.read_text())
        state.pop("preference_failures")
        self.state.write_text(json.dumps(state))
        self.run_base()
        self.run_base("--check")

    def test_inaccessible_container_skips_write_and_check_stays_read_only(self):
        directory = self.home / "Library/Containers/com.apple.TextEdit/Data/Library/Preferences"
        directory.mkdir(parents=True)
        denied = self.bin / "denied-list"
        denied.write_text("#!/bin/bash\nexit 1\n")
        denied.chmod(0o755)
        self.script.write_text(self.script.read_text().replace("/bin/ls", str(denied)))
        output = self.run_base(code=3)
        self.assertIn("missing access to protected preferences", output)
        self.assertIn("Full Disk Access", output)
        self.assertFalse(any(c[0] == "defaults" and "com.apple.TextEdit" in c[1] for c in self.calls()))
        before = len(self.calls())
        output = self.run_base("--check", code=1)
        self.assertIn("permission is required", output)
        self.assertFalse(any(c[0] == "defaults" and c[1][0] == "write" for c in self.calls()[before:]))


if __name__ == "__main__":
    unittest.main()
