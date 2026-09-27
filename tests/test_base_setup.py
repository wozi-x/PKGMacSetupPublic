"""Exercise the actual Base shell with fake providers and a disposable HOME."""
import json
import os
import re
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
import json, os, pathlib, plistlib, re, sys
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
    if args[1] == "%Sf":
        print("hidden" if state.get("library_hidden") else "-")
    else:
        print("501" if args[1] == "%u" else oct(os.stat(args[2]).st_mode & 0o777)[2:])
elif kind == "chflags":
    assert args[0] == "nohidden"
    assert pathlib.Path(args[1]).resolve() == root / "home with spaces/Library"
    state["library_hidden"] = False
    state_file.write_text(json.dumps(state))
elif kind == "sudo":
    assert not os.environ.get("AWS_ACCESS_KEY_ID")
    if args[0] == "-n":
        args = args[1:]
    assert args[0] == "/usr/sbin/systemsetup"
    if state.get("systemsetup_failure"):
        sys.exit(1)
    if args[1:] == ["-getrestartfreeze"]:
        print("Restart After Freeze: " + ("On" if state.get("restartfreeze") else "Off"))
    else:
        assert args[1:] == ["-setrestartfreeze", "on"]
        state["restartfreeze"] = True
        state_file.write_text(json.dumps(state))
elif kind == "PlistBuddy":
    assert args[0] == "-c"
    assert pathlib.Path(args[2]).resolve() == root / "home with spaces/Library/Preferences/com.apple.finder.plist"
    command = args[1].split()
    path = command[1].strip(":").split(":")
    tree = state.setdefault("finder_views", {})
    for component in path[:-1]:
        if component not in tree:
            sys.exit(1)
        tree = tree[component]
    key = path[-1]
    if command[0] == "Print":
        if key not in tree:
            sys.exit(1)
        value = tree[key]
        print(f"{value:.6f}" if isinstance(value, int) else value)
    else:
        if state.get("finder_view_failure"):
            sys.exit(1)
        if command[0] == "Set":
            if key not in tree:
                sys.exit(1)
            value = command[2]
        else:
            assert command[0] == "Add"
            if key in tree:
                sys.exit(1)
            value = {} if command[2] == "dict" else command[3]
        tree[key] = int(value) if key == "iconSize" else value
        state_file.write_text(json.dumps(state))
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
        if brewfile.name == "AppStore.Brewfile" and (root / "mas-fail").exists():
            print("App Store sign-in is required", file=sys.stderr)
            sys.exit(4)
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
elif kind == "git":
    assert args[:6] == ["clone", "--depth", "1", "--branch", "master", "--"]
    assert args[6] == "https://github.com/ohmyzsh/ohmyzsh.git"
    assert not os.environ.get("AWS_ACCESS_KEY_ID")
    if (root / "git-fail").exists():
        sys.exit(1)
    destination = pathlib.Path(args[7])
    destination.mkdir()
    (destination / "oh-my-zsh.sh").write_text("function omz() { :; }\n")
elif kind == "mas":
    assert args == ["list"]
    if (root / "mas-list-fail").exists():
        sys.exit(4)
    for app_id, name in state.get("mas", {}).items():
        print(app_id.rjust(10) + "  " + name + " (1.0)")
elif kind == "defaults":
    current_host = args[0] == "-currentHost"
    if current_host:
        args = args[1:]
    if args[0] == "export":
        if args[1] != "com.apple.symbolichotkeys":
            domain = args[1]
            if domain in state.get("export_failures", []):
                sys.exit(1)
            values = {key[len(domain) + 1:]: value for key, value in state["preferences"].items()
                      if key.startswith(domain + " ")}
            values.update(state.get("domains", {}).get(domain, {}))
            print(plistlib.dumps(values).decode())
            sys.exit(0)
        hotkeys = state.get("hotkeys", {})
        if state.get("hotkey_reads_remaining", 0):
            hotkeys = state.get("previous_hotkeys", {})
            state["hotkey_reads_remaining"] -= 1
            state_file.write_text(json.dumps(state))
        if state.get("hotkey_numeric_enabled"):
            for shortcut in hotkeys.values():
                if isinstance(shortcut.get("enabled"), bool):
                    shortcut["enabled"] = int(shortcut["enabled"])
        print(plistlib.dumps({"AppleSymbolicHotKeys": hotkeys}).decode())
        sys.exit(0)
    if args[1] == "com.apple.HIToolbox":
        key = ("host:" if current_host else "") + args[2]
        sources = state.setdefault("input_sources", {})
        if args[0] == "read":
            if key not in sources or (not current_host and state.get("input_requires_byhost")):
                sys.exit(1)
            print("(\n" + ",\n".join(sources[key]) + "\n)")
        else:
            assert args[0] == "write" and args[3] == "-array-add"
            if state.get("input_failure") == "write":
                sys.exit(1)
            if state.get("input_failure") != "persist":
                sources.setdefault(key, []).append(args[4])
                state_file.write_text(json.dumps(state))
        sys.exit(0)
    if args[0] == "write" and args[3] in ("-array", "-dict-add") and args[1] != "com.apple.symbolichotkeys":
        failure = state.get("preference_failures", {}).get(args[1] + " " + args[2])
        if failure == "write":
            sys.exit(255)
        if failure != "persist":
            values = state.setdefault("domains", {}).setdefault(args[1], {})
            if args[3] == "-array":
                assert len(args) == 4
                values[args[2]] = []
            else:
                assert len(args) == 6
                values.setdefault(args[2], {})[args[4]] = plistlib.loads(("<plist>" + args[5] + "</plist>").encode())
            state_file.write_text(json.dumps(state))
        sys.exit(0)
    if args[0] == "write" and args[3] == "-dict-add":
        if state.get("hotkey_failure") == "write":
            sys.exit(255)
        if state.get("hotkey_failure") != "persist":
            state["previous_hotkeys"] = json.loads(json.dumps(state.get("hotkeys", {})))
            state["hotkey_reads_remaining"] = state.get("hotkey_stale_reads", 0)
            state.setdefault("hotkeys", {})[args[4]] = plistlib.loads(("<plist>" + args[5] + "</plist>").encode())
            state_file.write_text(json.dumps(state))
        sys.exit(0)
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
elif kind == "swift":
    assert len(args) == 3
    assert pathlib.Path(args[0]) == root / "set-desktop-wallpaper.swift"
    assert pathlib.Path(args[0]).is_file()
    assert args[1] == "/System/Library/Desktop Pictures/Solid Colors/Stone.png"
    assert args[2] in ("--check", "--apply")
    assert not os.environ.get("AWS_ACCESS_KEY_ID")
    if state.get("wallpaper_failure"):
        print("wallpaper: desktop image verification failed", file=sys.stderr)
        sys.exit(1)
    if state.get("wallpaper_skip"):
        print("wallpaper: skipped (" + state["wallpaper_skip"] + ")")
    elif state.get("wallpaper") == args[1]:
        print("wallpaper: unchanged")
    elif args[2] == "--check":
        print("wallpaper: would-change (2 display(s))")
    else:
        state["wallpaper"] = args[1]
        state_file.write_text(json.dumps(state))
        print("wallpaper: changed (2 display(s))")
else:
    raise AssertionError((kind, args))
'''
        )
        self.provider.chmod(0o755)
        for name in ("brew", "git", "mas", "defaults", "uname", "id", "stat", "curl", "xcode-select", "swift", "sudo", "chflags", "PlistBuddy"):
            (self.bin / name).symlink_to(self.provider)
        self.script = self.root / "setup.sh"
        source = (ROOT / "setup.sh").read_text()
        for name in ("git", "defaults", "uname", "id", "stat", "curl", "xcode-select", "swift", "sudo"):
            source = source.replace(f"/usr/bin/{name}", str(self.bin / name))
        source = source.replace("/bin/chflags", str(self.bin / "chflags"))
        source = source.replace("/usr/libexec/PlistBuddy", str(self.bin / "PlistBuddy"))
        source = source.replace("/opt/homebrew/bin/brew", str(self.bin / "brew"))
        source = source.replace("/usr/local/bin/brew", str(self.bin / "brew"))
        source = source.replace('"/Applications/', f'"{self.apps}/')
        self.script.write_text(source)
        self.script.chmod(0o755)
        for name in ("Brewfile", "preferences.conf", "migrate-ohmyzsh.sh", "set-desktop-wallpaper.swift"):
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
        bundles = [c[1] for c in self.calls() if c[0] == "brew" and c[1][:2] == ["bundle", "install"]]
        self.assertTrue(any(a.endswith("/Prerequisites.Brewfile") for a in bundles[0]))
        installed = json.loads(self.state.read_text())
        self.assertIn("mas", installed["formulae"])
        self.assertEqual(installed["wallpaper"], "/System/Library/Desktop Pictures/Solid Colors/Stone.png")
        self.assertEqual(installed["mas"], {"937984704": "Amphetamine"})
        state = self.state.read_bytes()
        dots = {str(p.relative_to(self.home)): p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
        before = len(self.calls())
        self.run_base()
        self.assertEqual(self.state.read_bytes(), state)
        self.assertEqual({str(p.relative_to(self.home)): p.read_bytes() for p in self.home.rglob("*") if p.is_file()}, dots)
        second_calls = self.calls()[before:]
        self.assertFalse(any(c[0] == "defaults" and c[1][0] == "write" for c in second_calls))
        before = len(self.calls())
        self.run_base("--check")
        self.assertFalse(any(c[0] == "brew" and c[1][0] != "list" for c in self.calls()[before:]))
        self.assertEqual(self.state.read_bytes(), state)
        self.assertEqual({str(p.relative_to(self.home)): p.read_bytes() for p in self.home.rglob("*") if p.is_file()}, dots)
        self.assertEqual(len([c for c in self.calls() if c[0] == "git"]), 1)

    def test_ohmyzsh_check_and_clone_failure_do_not_create_installation(self):
        args = self.selection()
        output = self.run_base(*args, "--check", code=1)
        self.assertIn("Oh My Zsh is not installed", output)
        self.assertFalse(any(c[0] == "git" for c in self.calls()))
        (self.root / "git-fail").touch()
        self.run_base(*args, code=2)
        self.assertFalse((self.home / ".oh-my-zsh").exists())
        self.assertFalse((self.home / ".zshrc").exists())

    def test_existing_ohmyzsh_is_preserved_without_fetch(self):
        target = self.home / ".oh-my-zsh"
        target.mkdir()
        (target / "oh-my-zsh.sh").write_text("function omz() { :; } # existing\n")
        self.run_base(*self.selection())
        self.assertEqual((target / "oh-my-zsh.sh").read_text(), "function omz() { :; } # existing\n")
        self.assertFalse(any(c[0] == "git" for c in self.calls()))

    def test_unsafe_or_incomplete_ohmyzsh_fails_before_package_mutation(self):
        target = self.home / ".oh-my-zsh"
        target.symlink_to(self.root)
        self.run_base(code=2)
        self.assertFalse(any(c[0] in ("brew", "git", "defaults") for c in self.calls()))
        target.unlink()
        target.mkdir()
        self.run_base(code=2)
        self.assertTrue(target.is_dir())
        self.assertFalse(any(c[0] in ("brew", "git", "defaults") for c in self.calls()))

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

    def test_wallpaper_check_apply_and_rerun(self):
        # Satisfy everything else first so --check's exit code proves wallpaper drift.
        self.run_base(*self.selection())
        args = self.selection(prefs="wallpaper=true\n")
        original = self.state.read_bytes()
        output = self.run_base(*args, "--check", code=1)
        self.assertIn("Pending: Desktop wallpaper: Stone", output)
        self.assertEqual(self.state.read_bytes(), original)
        self.assertIn("Desktop wallpaper: Stone", self.run_base(*args))
        applied = self.state.read_bytes()
        self.assertNotIn("Desktop wallpaper: Stone", self.run_base(*args))
        self.run_base(*args, "--check")
        self.assertEqual(self.state.read_bytes(), applied)
        self.assertEqual([c[1][-1] for c in self.calls() if c[0] == "swift"],
                         ["--check", "--apply", "--apply", "--check"])

    def test_disabled_or_omitted_wallpaper_preserves_existing_image(self):
        state = json.loads(self.state.read_text())
        state["wallpaper"] = "/custom/image.png"
        self.state.write_text(json.dumps(state))
        for prefs in ("", "wallpaper=false\n"):
            args = self.selection(prefs=prefs)
            self.run_base(*args)
            self.run_base(*args, "--check")
        self.assertFalse(any(c[0] == "swift" for c in self.calls()))
        self.assertEqual(json.loads(self.state.read_text())["wallpaper"], "/custom/image.png")

    def test_wallpaper_session_skips_are_reported_without_changes(self):
        args = self.selection(prefs="wallpaper=true\n")
        for reason in ("run as the logged-in console user", "no accessible graphical display"):
            state = json.loads(self.state.read_text())
            state["wallpaper_skip"] = reason
            self.state.write_text(json.dumps(state))
            for extra in ((), ("--check",)):
                self.assertIn("wallpaper: skipped (" + reason + ")", self.run_base(*args, *extra))
                self.assertNotIn("wallpaper", json.loads(self.state.read_text()))

    def test_wallpaper_failure_is_retained_after_other_setup_finishes(self):
        state = json.loads(self.state.read_text())
        state["wallpaper_failure"] = True
        self.state.write_text(json.dumps(state))
        args = self.selection(prefs="wallpaper=true\nfinder=true\n")
        output = self.run_base(*args, code=3)
        self.assertIn("desktop image verification failed", output)
        self.assertIn("Base needs attention", output)
        self.assertIn("Desktop wallpaper failed; review the error above", output)
        self.assertTrue((self.home / ".zshrc").is_file())
        self.assertEqual(json.loads(self.state.read_text())["preferences"]["com.apple.finder ShowPathbar"], "1")
        original = self.state.read_bytes()
        self.assertIn("Desktop wallpaper could not be applied or verified", self.run_base(*args, "--check", code=1))
        self.assertEqual(self.state.read_bytes(), original)

    def test_keyboard_shortcuts_preserve_other_keys_and_detect_drift(self):
        args = self.selection(prefs="keyboard=true\n")
        state = json.loads(self.state.read_text())
        state["hotkeys"] = {"99": {"enabled": True, "custom": "preserve"}}
        self.state.write_text(json.dumps(state))
        self.run_base(*args)
        state = json.loads(self.state.read_text())
        self.assertEqual(state["hotkeys"]["99"], {"enabled": True, "custom": "preserve"})
        self.assertFalse(state["hotkeys"]["64"]["enabled"])
        self.assertEqual(state["hotkeys"]["61"], {
            "enabled": True, "value": {"type": "standard", "parameters": [32, 49, 1048576]},
        })
        state["hotkeys"]["61"]["value"]["parameters"] = [32, 49, 262144]
        self.state.write_text(json.dumps(state))
        before = self.state.read_bytes()
        self.assertIn("Keyboard shortcut: 61", self.run_base(*args, "--check", code=1))
        self.assertEqual(self.state.read_bytes(), before)
        self.run_base(*args)
        self.run_base(*args, "--check")

    def test_keyboard_shortcut_failure_is_pending_after_shell_finishes(self):
        for failure in ("write", "persist"):
            with self.subTest(failure=failure):
                state = json.loads(self.state.read_text())
                state["hotkey_failure"] = failure
                self.state.write_text(json.dumps(state))
                output = self.run_base(code=3)
                self.assertIn("com.apple.symbolichotkeys", output)
                self.assertTrue((self.home / ".zshrc").exists())

    def test_keyboard_shortcuts_accept_numeric_booleans_and_delayed_readback(self):
        args = self.selection(prefs="keyboard=true\n")
        state = json.loads(self.state.read_text())
        state["hotkey_numeric_enabled"] = True
        state["hotkey_stale_reads"] = 2
        self.state.write_text(json.dumps(state))
        self.run_base(*args)
        before = len(self.calls())
        self.run_base(*args)
        self.run_base(*args, "--check")
        self.assertFalse(any(c[0] == "defaults" and c[1][0] == "write" for c in self.calls()[before:]))
        # A real change to the enabled state must still be detected.
        state = json.loads(self.state.read_text())
        state["hotkeys"]["64"]["enabled"] = 1
        self.state.write_text(json.dumps(state))
        self.assertIn("Keyboard shortcut: 64", self.run_base(*args, "--check", code=1))

    def test_keyboard_false_does_not_change_shortcuts(self):
        args = self.selection(prefs="keyboard=false\n")
        self.run_base(*args)
        self.run_base(*args, "--check")
        self.assertFalse(any(c[0] == "defaults" for c in self.calls()))

    def test_unsafe_keyboard_preferences_fail_before_changes(self):
        prefs = self.home / "Library/Preferences"
        prefs.mkdir(parents=True)
        target = prefs / "com.apple.symbolichotkeys.plist"
        target.write_text("malformed plist")
        self.run_base(code=2)
        self.assertEqual(target.read_text(), "malformed plist")
        target.unlink()
        target.symlink_to(self.state)
        self.run_base(code=2)
        self.assertTrue(target.is_symlink())
        self.assertFalse(any(c[0] in ("brew", "mas", "defaults") for c in self.calls()))

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
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                output = self.run_base(code=3)
                self.assertIn(f"{key}: {reason}", output)
                self.assertIn("→ Shell configuration", output)
                self.assertIn("Base needs attention", output)
                self.assertNotIn("✓ Base setup complete", output)
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
        output = self.run_base(*args, code=3)
        self.assertIn("  - com.apple.finder ShowPathbar: defaults write failed with exit 255", output)
        self.assertIn("  - com.apple.finder ShowStatusBar: value did not persist", output)
        self.assertTrue((self.home / ".zshrc").read_text().startswith("# existing bytes\n"))
        self.assertEqual((self.home / ".config/macsetup/base/zshrc").read_text(), "# client fragment\n")
        original = (self.home / ".zshrc").read_bytes()
        self.run_base(*args, code=3)
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

    def test_explicit_app_store_deferral_completes_without_installing(self):
        source = self.script.read_text()
        source = re.sub(r"^account_ready\(\) \{.*?^\}", 'account_ready() { [[ "$1" != "App Store" ]]; }', source, flags=re.M | re.S)
        self.script.write_text(source)
        output = self.run_base()
        self.assertIn("Deferred by request: App Store apps", output)
        self.assertNotIn("Base needs attention", output)
        self.assertFalse(json.loads(self.state.read_text()).get("mas"))
        self.assertFalse(any(c[0] == "brew" and any(a.endswith("/AppStore.Brewfile") for a in c[1]) for c in self.calls()))
        self.run_base("--check", code=1)

    def test_1password_readiness_only_follows_new_installation(self):
        source = self.script.read_text()
        source = re.sub(r"^account_ready\(\) \{.*?^\}",
                        'account_ready() { printf "READY:%s\\\\n" "$1"; }', source, flags=re.M | re.S)
        self.script.write_text(source)
        args = self.selection('cask "1password"\n')
        self.assertIn('READY:1Password', self.run_base(*args))
        self.assertNotIn('READY:', self.run_base(*args))
        self.assertNotIn('READY:', self.run_base(*args, '--check'))
        # Simulate the shared launcher having installed the app in this run.
        self.script.write_text(source.replace(
            '${PKGMACSETUP_1PASSWORD_JUST_INSTALLED:-false}', 'true'))
        self.assertIn('READY:1Password', self.run_base(*args))
        self.assertNotIn('READY:', self.run_base(*self.selection()))

    def test_external_apps_and_existing_dotfiles_are_preserved(self):
        for app in ("Raycast.app", "Zed.app", "1Password.app"):
            (self.apps / app).mkdir()
        (self.home / ".zshrc").write_text("# existing private setup\n")
        output = self.run_base()
        self.assertIn("Preserved existing dotfile: .zshrc", output)
        self.assertTrue((self.home / ".zshrc").read_text().startswith("# existing private setup\n"))
        backups = list(self.home.glob(".zshrc.pre-base-ohmyzsh.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "# existing private setup\n")
        self.assertEqual(json.loads(self.state.read_text())["casks"], [])
        self.run_base("--check")

    def test_existing_default_dotfiles_are_preserved_without_incomplete_status(self):
        self.run_base()
        (self.home / ".tmux.conf").write_text("set -g prefix C-z\n")
        before = (self.home / ".tmux.conf").read_bytes()
        for args in ((), ("--check",)):
            output = self.run_base(*args)
            self.assertIn("Preserved existing dotfile: .tmux.conf", output)
            self.assertIn("✓ Base", output)
            self.assertEqual((self.home / ".tmux.conf").read_bytes(), before)

    def test_app_store_failure_finishes_common_setup_then_recovers(self):
        (self.root / "mas-fail").touch()
        output = self.run_base(code=3)
        self.assertIn("App Store apps remain pending", output)
        self.assertNotIn("✓ Base setup complete", output)
        state = json.loads(self.state.read_text())
        self.assertEqual(state["preferences"]["com.apple.dock show-recents"], "0")
        self.assertEqual((self.home / ".tmux.conf").read_bytes(), (ROOT / "dotfiles/.tmux.conf").read_bytes())
        calls = self.calls()
        app_store = next(i for i, c in enumerate(calls) if c[0] == "brew" and any("AppStore.Brewfile" in a for a in c[1]))
        self.assertTrue(all(i < app_store for i, c in enumerate(calls) if c[0] == "defaults"))
        self.run_base("--check", code=1)
        (self.root / "mas-fail").unlink()
        self.run_base()
        before = len(self.calls())
        self.run_base()
        self.assertFalse(any("AppStore.Brewfile" in a for c in self.calls()[before:] for a in c[1]))
        self.run_base("--check")

    def test_app_store_inventory_failure_does_not_block_common_setup(self):
        (self.root / "mas-list-fail").touch()
        output = self.run_base(code=3)
        self.assertIn("App Store inventory could not be verified", output)
        self.assertTrue((self.home / ".zshrc").exists())
        self.run_base("--check", code=1)

    def test_app_store_requires_mas_declaration_before_mutation(self):
        args = self.selection('mas "Amphetamine", id: 937984704\n')
        self.run_base(*args, code=2)
        self.assertFalse(any(c[0] in ("brew", "mas", "defaults") for c in self.calls()))

    def test_app_store_padded_ids_match_exactly(self):
        self.run_base()
        state = json.loads(self.state.read_text())
        state["mas"]["1937984704"] = "Different app with suffix matching Amphetamine"
        self.state.write_text(json.dumps(state))
        self.run_base("--check")
        del state["mas"]["937984704"]
        self.state.write_text(json.dumps(state))
        self.assertIn("App Store app: 937984704", self.run_base("--check", code=1))
        self.run_base()
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
