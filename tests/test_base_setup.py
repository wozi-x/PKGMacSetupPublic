"""Exercise the actual Base shell with fake providers and a disposable HOME."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BaseSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="base-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home with spaces"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.apps = self.root / "Applications"
        self.apps.mkdir()
        self.config = self.root / "client"
        self.config.mkdir()
        self.log = self.root / "calls.jsonl"
        self.state = self.root / "state.json"
        self.state.write_text(json.dumps({"formulae": [], "casks": [], "preferences": {}}))
        self.provider = self.bin / "provider"
        self.provider.write_text(
            f"#!{sys.executable}\n"
            + r'''
import json, os, pathlib, re, sys
root = pathlib.Path(__file__).resolve().parent.parent
kind = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
state_file = root / "state.json"
state = json.loads(state_file.read_text())
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps([kind, args]) + "\n")
if kind == "uname":
    print("Darwin" if args == ["-s"] else "arm64")
elif kind == "id":
    print("fixture" if args == ["-un"] else "501")
elif kind == "stat":
    print("501" if args[1] == "%u" else oct(os.stat(args[2]).st_mode & 0o777)[2:])
elif kind == "brew":
    if args[:2] == ["list", "--formula"]:
        print("\n".join(state["formulae"]))
    elif args[:2] == ["list", "--cask"]:
        print("\n".join(state["casks"]))
    elif args[:2] == ["bundle", "install"]:
        assert "--no-upgrade" in args
        assert os.environ["HOMEBREW_NO_INSTALL_CLEANUP"] == "1"
        assert os.environ["HOMEBREW_NO_AUTO_UPDATE"] == "1"
        assert not os.environ.get("AWS_ACCESS_KEY_ID")
        if (root / "bundle-fail").exists():
            sys.exit(4)
        brewfile = pathlib.Path(next(a[7:] for a in args if a.startswith("--file=")))
        for line in brewfile.read_text().splitlines():
            match = re.match(r'(brew|cask) "([^"]+)"', line)
            if match:
                category = "formulae" if match[1] == "brew" else "casks"
                if category == "casks" and match[2] in os.environ.get("HOMEBREW_BUNDLE_CASK_SKIP", "").split():
                    continue
                if match[2] not in state[category]:
                    state[category].append(match[2])
            match = re.match(r'mas "([^"]+)", id: ([0-9]+)', line)
            if match:
                state.setdefault("mas", {})[match[2]] = match[1]
        state_file.write_text(json.dumps(state))
    else:
        raise AssertionError(args)
elif kind == "mas":
    assert args == ["list"]
    for app_id, name in state.get("mas", {}).items():
        print(app_id + " " + name + " (1.0)")
elif kind == "defaults":
    key = args[1] + " " + args[2]
    if args[0] == "read":
        if key in state.get("unreadable_preferences", []):
            print("Could not read preference", file=sys.stderr)
            sys.exit(1)
        if key not in state["preferences"]:
            sys.exit(1)
        print(state["preferences"][key])
    elif args[0] == "write":
        failure = state.get("preference_failures", {}).get(key)
        if failure == "write":
            print("Could not write domain " + args[1], file=sys.stderr)
            sys.exit(255)
        if failure == "persist":
            sys.exit(0)
        if failure == "read":
            state.setdefault("unreadable_preferences", []).append(key)
        value = args[4]
        if args[3] == "-bool":
            if value not in ("true", "false"):
                print("Boolean value must be true or false", file=sys.stderr)
                sys.exit(255)
            value = "1" if value == "true" else "0"
        state["preferences"][key] = value
        state_file.write_text(json.dumps(state))
    else:
        raise AssertionError(args)
elif kind == "curl":
    if (root / "curl-fail").exists():
        sys.exit(22)
    print(json.dumps({"artifacts": [{"app": ["External.app"]}]}))
elif kind == "xcode-select":
    sys.exit(1 if args == ["-p"] else 0)
else:
    raise AssertionError((kind, args))
'''
        )
        self.provider.chmod(0o755)
        for name in ("brew", "mas", "defaults", "uname", "id", "stat", "curl", "xcode-select"):
            (self.bin / name).symlink_to(self.provider)
        self.script = self.root / "setup.sh"
        source = (ROOT / "setup.sh").read_text()
        for name in ("defaults", "uname", "id", "stat", "curl", "xcode-select"):
            source = source.replace(f"/usr/bin/{name}", str(self.bin / name))
        source = source.replace("/opt/homebrew/bin/brew", str(self.bin / "brew"))
        source = source.replace("/usr/local/bin/brew", str(self.bin / "brew"))
        source = source.replace('"/Applications/', f'"{self.apps}/')
        self.script.write_text(source)
        self.script.chmod(0o755)
        for name in ("Brewfile", "preferences.conf"):
            shutil.copyfile(ROOT / name, self.root / name)
        shutil.copytree(ROOT / "dotfiles", self.root / "dotfiles")

    def run_base(self, *args, code=0):
        result = subprocess.run(
            ["/bin/bash", "-p", str(self.script), *args],
            env={"HOME": str(self.home), "PATH": "/usr/bin:/bin", "AWS_ACCESS_KEY_ID": "fixture-do-not-forward"},
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self.assertEqual(result.returncode, code, result.stdout)
        return result.stdout

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def selection(self, brewfile="", prefs=""):
        (self.config / "Brewfile").write_text(brewfile)
        (self.config / "preferences.conf").write_text(prefs)
        return ("--config-dir", str(self.config))

    def test_first_run_rerun_and_check(self):
        self.run_base()
        installed = json.loads(self.state.read_text())
        self.assertIn("mas", installed["formulae"])
        self.assertEqual(installed["mas"], {"937984704": "Amphetamine"})
        state = self.state.read_bytes()
        dots = {p.name: p.read_bytes() for p in self.home.iterdir()}
        before = len(self.calls())
        self.run_base()
        self.assertEqual(self.state.read_bytes(), state)
        self.assertEqual({p.name: p.read_bytes() for p in self.home.iterdir()}, dots)
        second_calls = self.calls()[before:]
        self.assertFalse(any(c[0] == "defaults" and c[1][0] == "write" for c in second_calls))
        before = len(self.calls())
        self.run_base("--check")
        self.assertFalse(any(c[0] == "brew" and c[1][0] != "list" for c in self.calls()[before:]))
        self.assertEqual(self.state.read_bytes(), state)
        self.assertEqual({p.name: p.read_bytes() for p in self.home.iterdir()}, dots)

    def test_boolean_writes_use_words_and_numeric_readback(self):
        args = self.selection(prefs="finder=true\nkeyboard=true\ntrackpad=true\ndock=true\n")
        self.run_base(*args)
        writes = [call[1] for call in self.calls() if call[0] == "defaults" and call[1][0] == "write"]
        booleans = [call for call in writes if call[3] == "-bool"]
        self.assertTrue(booleans)
        self.assertEqual({call[4] for call in booleans}, {"true", "false"})
        self.assertIn(["write", "com.apple.AppleMultitouchTrackpad", "Clicking", "-int", "1"], writes)
        values = json.loads(self.state.read_text())["preferences"]
        self.assertEqual(values["com.apple.finder ShowPathbar"], "1")
        self.assertEqual(values["com.apple.dock show-recents"], "0")
        before = len(self.calls())
        self.run_base(*args)
        self.run_base(*args, "--check")
        self.assertFalse(any(call[0] == "defaults" and call[1][0] == "write" for call in self.calls()[before:]))

    def test_check_reports_drift_without_mutations(self):
        original = self.state.read_bytes()
        self.run_base("--check", code=1)
        self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertFalse(any(c[0] == "defaults" and c[1][0] == "write" for c in self.calls()))

    def test_preference_failures_finish_shell_and_report_incomplete(self):
        for failure, reason in (
            ("write", "defaults write failed with exit 255"),
            ("persist", "value did not persist"),
            ("read", "verification read failed with exit 1"),
        ):
            with self.subTest(failure=failure):
                key = "com.apple.finder ShowPathbar"
                self.state.write_text(json.dumps({
                    "formulae": [], "casks": [], "preferences": {key: "0"},
                    "preference_failures": {key: failure},
                }))
                for path in self.home.iterdir():
                    path.unlink()
                output = self.run_base(code=1)
                self.assertIn(f"{key}: {reason}", output)
                self.assertIn("[3/3] Shell configuration", output)
                self.assertIn("Base setup incomplete", output)
                self.assertNotIn("[OK] Base setup completed", output)
                if failure == "write":
                    self.assertIn("Could not write domain com.apple.finder", output)
                state = json.loads(self.state.read_text())
                self.assertEqual(state["preferences"]["com.apple.dock show-recents"], "0")
                for name in (".zprofile", ".zshrc", ".tmux.conf"):
                    self.assertEqual((self.home / name).read_bytes(), (ROOT / "dotfiles" / name).read_bytes())
                self.run_base("--check", code=1)
                # Once the provider is repaired, rerunning converges normally.
                state.pop("preference_failures")
                state.pop("unreadable_preferences", None)
                self.state.write_text(json.dumps(state))
                self.run_base()
                self.run_base("--check")

    def test_multiple_preference_failures_preserve_existing_shell_bytes(self):
        args = self.selection(prefs="finder=true\n")
        (self.config / "dotfiles").mkdir()
        (self.config / "dotfiles/.zshrc").write_text("# client fragment\n")
        (self.home / ".zshrc").write_text("# existing bytes\n")
        state = json.loads(self.state.read_text())
        state["preferences"]["com.apple.finder ShowStatusBar"] = "0"
        state["preference_failures"] = {
            "com.apple.finder ShowPathbar": "write",
            "com.apple.finder ShowStatusBar": "persist",
        }
        self.state.write_text(json.dumps(state))
        output = self.run_base(*args, code=1)
        self.assertIn("  - com.apple.finder ShowPathbar: defaults write failed with exit 255", output)
        self.assertIn("  - com.apple.finder ShowStatusBar: value did not persist", output)
        self.assertTrue((self.home / ".zshrc").read_text().startswith("# existing bytes\n"))
        self.assertEqual((self.home / ".config/macsetup/base/zshrc").read_text(), "# client fragment\n")
        original = (self.home / ".zshrc").read_bytes()
        self.run_base(*args, code=1)
        self.assertEqual((self.home / ".zshrc").read_bytes(), original)

    def test_empty_and_single_kind_selections(self):
        for brewfile in ("", 'brew "git"\n', 'cask "raycast"\n'):
            with self.subTest(brewfile=brewfile):
                args = self.selection(brewfile)
                self.run_base(*args)
                self.run_base(*args, "--check")

    def test_invalid_local_data_fails_before_providers(self):
        for brewfile, prefs in (
            ('system "touch /tmp/not-a-hook"\n', ""),
            ('brew "git", restart_service: true\n', ""),
            ('brew "#{ENV}"\n', ""),
            ("", "remote_login=true\n"),
            ("", "finder=$(touch injected)\n"),
            ("", "finder=true\nfinder=false\n"),
        ):
            with self.subTest(brewfile=brewfile, prefs=prefs):
                args = self.selection(brewfile, prefs)
                before = len(self.calls())
                self.run_base(*args, code=2)
                self.assertFalse(any(c[0] in ("brew", "defaults", "curl") for c in self.calls()[before:]))
                self.assertEqual(list(self.home.iterdir()), [])

    def test_external_apps_and_existing_dotfiles_are_preserved(self):
        for app in ("Raycast.app", "Zed.app"):
            (self.apps / app).mkdir()
        (self.home / ".zshrc").write_text("# existing private setup\n")
        self.run_base()
        self.assertEqual((self.home / ".zshrc").read_text(), "# existing private setup\n")
        self.assertEqual(json.loads(self.state.read_text())["casks"], [])
        self.run_base("--check")

    def test_generic_external_app_uses_only_public_json(self):
        (self.apps / "External.app").mkdir()
        args = self.selection('cask "external"\n')
        self.run_base(*args)
        self.run_base(*args, "--check")
        self.assertEqual(json.loads(self.state.read_text())["casks"], [])
        urls = [call[1][-1] for call in self.calls() if call[0] == "curl"]
        self.assertTrue(urls)
        self.assertEqual(set(urls), {"https://formulae.brew.sh/api/cask/external.json"})

    def test_local_fragments_update_once_and_check_detects_edits(self):
        args = self.selection()
        dots = self.config / "dotfiles"
        dots.mkdir()
        fragments = {
            ".zshrc": 'echo do-not-run > "$HOME/forbidden"\n',
            ".zprofile": "# local profile\n",
            ".tmux.conf": "set -g mouse on\n",
            ".gitconfig": "[alias]\n\tst = status\n",
        }
        for name, content in fragments.items():
            (dots / name).write_text(content)
            (self.home / name).write_text("# existing bytes")
        self.run_base(*args)
        homes = {name: (self.home / name).read_bytes() for name in fragments}
        self.assertFalse((self.home / "forbidden").exists())
        self.assertTrue(all(content.startswith(b"# existing bytes") for content in homes.values()))
        self.run_base(*args)
        self.assertEqual({name: (self.home / name).read_bytes() for name in fragments}, homes)
        self.run_base(*args, "--check")
        result = subprocess.run(
            ["/usr/bin/git", "config", "--file", str(self.home / ".gitconfig"), "--includes", "--get", "alias.st"],
            text=True, capture_output=True, env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "status")
        (dots / ".zshrc").write_text("# changed local config\n")
        before = (self.home / ".config/macsetup/base/zshrc").read_bytes()
        self.run_base(*args, "--check", code=1)
        self.assertEqual((self.home / ".config/macsetup/base/zshrc").read_bytes(), before)
        self.run_base(*args)
        self.assertEqual((self.home / ".config/macsetup/base/zshrc").read_bytes(), b"# changed local config\n")
        self.assertEqual((self.home / ".zshrc").read_bytes(), homes[".zshrc"])

    def test_destination_symlink_refused_before_packages(self):
        args = self.selection()
        (self.config / "dotfiles").mkdir()
        (self.config / "dotfiles/.zshrc").write_text("# local\n")
        outside = self.root / "outside"
        outside.mkdir()
        (self.home / ".config").symlink_to(outside)
        self.run_base(*args, code=2)
        self.assertFalse(any(c[0] in ("brew", "defaults", "curl") for c in self.calls()))
        self.assertEqual(list(outside.iterdir()), [])

    def test_package_failure_stops_preferences_and_dotfiles(self):
        (self.root / "bundle-fail").touch()
        self.run_base(code=2)
        self.assertFalse(any(c[0] == "defaults" for c in self.calls()))
        self.assertEqual(list(self.home.iterdir()), [])

    def test_missing_brew_check_does_not_bootstrap(self):
        (self.bin / "brew").unlink()
        self.run_base("--check", code=1)
        self.assertFalse(any(c[0] in ("curl", "xcode-select") for c in self.calls()))
        self.assertEqual(list(self.home.iterdir()), [])

    def test_missing_clt_requests_apple_dialog_then_stops(self):
        (self.bin / "brew").unlink()
        self.run_base(code=2)
        self.assertIn(["xcode-select", ["--install"]], self.calls())
        self.assertFalse(any(c[0] in ("curl", "defaults") for c in self.calls()))


if __name__ == "__main__":
    unittest.main()
