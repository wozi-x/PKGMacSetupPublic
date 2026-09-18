"""Stateful fake-provider integration: no real package, network, or host changes."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import planner
import engine


def configuration(packages=None, **extra):
    value = {"schema_version": 1, "profile": "dev", "packages": packages or {}}
    value.update(extra)
    return planner.validate_config(value)


class FakeProviders:
    """Only documented response shapes; every unexpected command fails closed."""
    def __init__(self):
        self.formulae = {}
        self.casks = {}
        self.npm_installed = {}
        self.npm_available = {}
        self.uv_installed = {}
        self.uv_available = {}
        self.python_records = []
        self.dependents = {}
        self.calls = []
        self.urls = []
        self.executed = []
        self.fail_package = None

    def formula(self, name, present=True, version="1.0.0", candidate="2.0.0", held=False, deps=None):
        self.formulae[name] = {"present": present, "version": version, "candidate": candidate,
                               "held": held, "deps": deps or []}
        return self

    def has_file(self, path, root):
        # Synthetic layout only; never inspect files on the executing Mac.
        return path == root + "/lib/node_modules/npm/bin/npm-cli.js"

    def has_directory(self, path):
        return True  # Explicit synthetic installed-prefix evidence.

    def run(self, argv):
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] == engine.BREW:
            args = argv[1:]
            if args[:2] == ["info", "--json=v2"]:
                provider = "formulae" if "--formula" in args else "casks"
                item = getattr(self, provider)[args[-1]]
                installed = ([{"version": item["version"]}] if item["present"] else []) if provider == "formulae" else (item["version"] if item["present"] else None)
                return json.dumps({provider: [{"name": args[-1], "token": args[-1],
                                               "installed": installed, "pinned": item["held"],
                                               "versions": {"stable": item["candidate"]}, "version": item["candidate"]}]})
            if args[:1] == ["deps"]:
                item = self.formulae.get(args[-1], self.casks.get(args[-1]))
                return "\n".join(item["deps"])
            if args[:3] == ["uses", "--installed", "--recursive"]:
                return "\n".join(self.dependents.get(args[-1], []))
            if args[:1] == ["--prefix"]:
                assert args[-1] in self.formulae
                return engine.PREFIX + "/opt/" + args[-1] + "\n"
            if args == ["pin", "--help"]:
                return "brew pin --formula --cask\n"
        for name, item in self.formulae.items():
            prefix = engine.PREFIX + "/opt/" + name
            if argv == [prefix + "/bin/node", "--version"]:
                return "v" + item["version"] + "\n"
            if argv[:3] == [prefix + "/bin/node", prefix + "/lib/node_modules/npm/bin/npm-cli.js", "list"]:
                return json.dumps({"dependencies": {n: {"version": v} for n, v in self.npm_installed.items()}})
        if argv[:4] == [engine.PREFIX + "/bin/uv", "--no-config", "python", "list"]:
            return json.dumps(self.python_records)
        if argv == [engine.PREFIX + "/bin/uv", "--no-config", "tool", "list"]:
            return "\n".join(n + " v" + v for n, v in self.uv_installed.items())
        for record in self.python_records:
            if record.get("path") and argv == [record["path"], "--version"]:
                return "Python " + record["version"] + "\n"
        raise AssertionError("Unmocked provider command: " + repr(argv))

    def get_json(self, url):
        self.urls.append(url)
        if url.startswith("https://formulae.brew.sh/api/formula/"):
            name = url.rsplit("/", 1)[-1][:-5]
            item = self.formulae[name]
            return {"versions": {"stable": item["candidate"]}, "disabled": False, "dependencies": item["deps"]}
        if url.startswith("https://formulae.brew.sh/api/cask/"):
            name = url.rsplit("/", 1)[-1][:-5]
            return {"version": self.casks[name]["candidate"], "disabled": False}
        if url.startswith("https://registry.npmjs.org/"):
            name, wanted = url[len("https://registry.npmjs.org/"):].rsplit("/", 1)
            from urllib.parse import unquote
            name = unquote(name)
            selected = self.npm_available[name] if wanted == "latest" else wanted
            return {"version": selected}
        if url.startswith("https://pypi.org/pypi/"):
            parts = url[len("https://pypi.org/pypi/"):].split("/")
            selected = self.uv_available[parts[0]] if len(parts) == 2 else parts[1]
            return {"info": {"version": selected}, "urls": [{"filename": "synthetic.whl"}]}
        raise AssertionError("Unmocked metadata request: " + url)

    def execute(self, operations):
        """In-memory effects only; simulates the fixed argv Ansible would dispatch."""
        for operation in operations:
            name, provider, argv = operation["id"], operation["provider"], operation["argv"]
            if name == self.fail_package:
                raise RuntimeError("synthetic package failure")
            if provider in ("formulae", "casks"):
                item = getattr(self, provider)[name]
                assert argv[1] in ("install", "upgrade", "pin")
                if argv[1] == "pin":
                    item["held"] = True
                else:
                    item.update(present=True, version=item["candidate"])
            elif provider == "npm":
                selected_name, selected_version = argv[-1].rsplit("@", 1)
                assert selected_name == name
                self.npm_installed[name] = selected_version
            elif provider == "uv_tools":
                selected_name, selected_version = argv[-1].rsplit("==", 1)
                assert selected_name == name
                self.uv_installed[name] = selected_version
            elif provider == "settings" and name == "public-path":
                assert argv == []
            else:
                raise AssertionError("unsupported synthetic executor")
            self.executed.append(copy.deepcopy(operation))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="public-provider-qa-")
        self.addCleanup(self.scratch.cleanup)
        home = Path(self.scratch.name)
        owned = home / "public"
        self.boundary = patch.dict(engine.observe.__globals__, {"HOME": home, "PUBLIC": owned,
            "ENV": {**engine.ENV, "npm_config_globalconfig": str(owned / ".npm-empty-global-config")}})
        self.boundary.start()
        self.addCleanup(self.boundary.stop)
        self.facade_home = patch.object(engine, "HOME", home)
        self.facade_public = patch.object(engine, "PUBLIC", owned)
        self.facade_home.start()
        self.facade_public.start()
        self.addCleanup(self.facade_home.stop)
        self.addCleanup(self.facade_public.stop)

    def observe(self, config, fake, operation="setup"):
        with patch.object(engine.subprocess, "run", side_effect=AssertionError("real process forbidden")), \
             patch.object(engine.urllib.request, "build_opener", side_effect=AssertionError("real network forbidden")):
            return engine.observe(config, operation, fake)

    def compile(self, config, fake, observation, operation="setup"):
        with patch.object(engine.subprocess, "run", side_effect=AssertionError("real process forbidden")), \
             patch.object(engine.urllib.request, "build_opener", side_effect=AssertionError("real network forbidden")):
            return engine.compile_operations(config, operation, observation["digest"], fake)

    def cycle(self, config, fake, operation="setup"):
        observed = self.observe(config, fake, operation)
        compiled = self.compile(config, fake, observed, operation)
        fake.execute(compiled["operations"])
        shell = compiled.get("shell_path")
        if shell and not shell["satisfied"]:
            path = Path(shell["path"])
            self.assertTrue(path.is_relative_to(Path(self.scratch.name)))
            previous = path.read_text() if path.exists() else ""
            self.assertNotIn("# BEGIN MACSETUP PUBLIC TOOLS", previous)
            path.write_text(previous + "# BEGIN MACSETUP PUBLIC TOOLS\n" + shell["block"] + "\n# END MACSETUP PUBLIC TOOLS\n")
        return compiled

    def test_formula_fresh_install_then_second_run_emits_no_changes(self):
        fake = FakeProviders().formula("git", present=False)
        config = configuration({"formulae": {"git": {}}})
        first = self.cycle(config, fake)
        self.assertEqual(first["operations"][0]["argv"], [engine.BREW, "install", "--formula", "git"])
        self.assertEqual(self.cycle(config, fake)["operations"], [])
        self.assertEqual(len(fake.executed), 1)

    def test_present_setup_does_not_upgrade_to_new_candidate(self):
        fake = FakeProviders().formula("git", version="1.0.0", candidate="9.0.0")
        self.assertEqual(self.cycle(configuration({"formulae": {"git": {}}}), fake)["operations"], [])
        self.assertEqual(fake.formulae["git"]["version"], "1.0.0")

    def test_cask_install_and_hold_then_repeat_is_noop(self):
        fake = FakeProviders()
        fake.casks["fixture-app"] = {"present": False, "version": "1.0", "candidate": "2.0", "held": False, "deps": []}
        config = configuration({"casks": {"fixture-app": {}}})
        self.assertEqual(self.cycle(config, fake)["operations"][0]["argv"], [engine.BREW, "install", "--cask", "fixture-app"])
        self.assertEqual(self.cycle(config, fake)["operations"], [])
        held = configuration({"casks": {"fixture-app": {"hold": True}}})
        self.assertEqual(self.cycle(held, fake)["operations"][0]["argv"], [engine.BREW, "pin", "--cask", "fixture-app"])
        self.assertEqual(self.cycle(held, fake)["operations"], [])

    def test_public_destination_symlinks_preserved_and_rejected_before_observation(self):
        fake, config = self.npm_fixture()
        with tempfile.TemporaryDirectory(prefix="public-path-qa-") as temp:
            home = Path(temp)
            public_root = home / "public"
            public_root.mkdir()
            private_root = home / "untouched"
            private_root.mkdir()
            canary = private_root / "canary"
            canary.write_bytes(b"preserve-private-state")
            for leaf in ("npm", "uv-tools", "bin", "python"):
                link = public_root / leaf
                link.symlink_to(private_root, target_is_directory=True)
                with self.subTest(leaf=leaf), patch.dict(engine.observe.__globals__, {"HOME": home, "PUBLIC": public_root}), self.assertRaises(engine.EngineError):
                    self.observe(config, fake)
                self.assertTrue(link.is_symlink())
                self.assertEqual(canary.read_bytes(), b"preserve-private-state")
                link.unlink()
            self.assertEqual(fake.calls, [])

    def test_uv_exact_tool_uses_managed_python_and_second_run_noop(self):
        fake = FakeProviders().formula("uv", version="0.8.0", candidate="0.8.0")
        python_path = str(engine.PUBLIC / "python/cpython-3.12.3/bin/python3")
        fake.python_records = [{"version": "3.12.3", "implementation": "cpython", "os": "macos", "arch": "aarch64" if engine.platform.machine() == "arm64" else "x86_64", "path": python_path}]
        fake.uv_available["ruff"] = "0.9.1"
        config = configuration({"formulae": {"uv": {}}, "uv_tools": {"ruff": {"version": "0.9.1"}}}, runtimes={"uv_python": ["3.12.3"]})
        operation = self.cycle(config, fake)["operations"][0]
        self.assertEqual(operation["argv"], [engine.PREFIX + "/bin/uv", "--no-config", "tool", "install", "--python", python_path, "ruff==0.9.1"])
        self.assertEqual(self.cycle(config, fake)["operations"], [])

    def test_uv_interpreter_outside_owned_tree_never_executed(self):
        fake = FakeProviders().formula("uv")
        path = "/tmp/private-runtime/bin/python3"
        fake.python_records = [{"version": "3.12.3", "implementation": "cpython", "os": "macos", "arch": "aarch64" if engine.platform.machine() == "arm64" else "x86_64", "path": path}]
        config = configuration({"formulae": {"uv": {}}}, runtimes={"uv_python": ["3.12.3"]})
        with self.assertRaises(engine.EngineError):
            self.observe(config, fake)
        self.assertFalse(any(call[0] == path for call in fake.calls))

    def test_shell_scope_preserves_unrelated_bytes_and_rejects_changed_review(self):
        fake, config = self.npm_fixture()
        profile = engine.HOME / ".zprofile"
        canary = b"# unrelated private shell bytes\nexport FIXTURE_EXISTING=preserved\n"
        profile.write_bytes(canary)
        observed = self.observe(config, fake)
        profile.write_bytes(canary + b"# concurrent change\n")
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observed)
        self.cycle(config, fake)
        self.assertTrue(profile.read_bytes().startswith(canary + b"# concurrent change\n"))
        self.assertEqual(self.cycle(config, fake)["operations"], [])

    def test_linked_shell_profile_not_read_or_replaced(self):
        fake, config = self.npm_fixture()
        target = engine.HOME / "private-shell"
        target.write_bytes(b"private-unrelated-canary")
        profile = engine.HOME / ".zprofile"
        profile.symlink_to(target)
        with self.assertRaises(engine.EngineError):
            self.observe(config, fake)
        self.assertTrue(profile.is_symlink())
        self.assertEqual(target.read_bytes(), b"private-unrelated-canary")

    def test_update_only_selected_installed_package_then_noop(self):
        fake = FakeProviders().formula("git").formula("private-runtime", candidate="99.0.0")
        private_before = copy.deepcopy(fake.formulae["private-runtime"])
        config = configuration({"formulae": {"git": {}}})
        result = self.cycle(config, fake, "update")
        self.assertEqual(result["operations"][0]["argv"], [engine.BREW, "upgrade", "--formula", "git"])
        self.assertEqual(self.cycle(config, fake, "update")["operations"], [])
        self.assertEqual(fake.formulae["private-runtime"], private_before)
        self.assertFalse(any("private-runtime" in call for call in fake.calls))

    def test_partial_failure_retry_changes_only_remaining_package(self):
        fake = FakeProviders().formula("git", present=False).formula("jq", present=False)
        config = configuration({"formulae": {"git": {}, "jq": {}}})
        fake.fail_package = "jq"
        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            self.cycle(config, fake)
        self.assertTrue(fake.formulae["git"]["present"])
        self.assertFalse(fake.formulae["jq"]["present"])
        fake.fail_package = None
        second = self.cycle(config, fake)
        self.assertEqual([op["id"] for op in second["operations"]], ["jq"])
        self.assertEqual(self.cycle(config, fake)["operations"], [])

    def test_hold_apply_then_rerun_preserves_pin(self):
        fake = FakeProviders().formula("git")
        config = configuration({"formulae": {"git": {"hold": True}}})
        self.assertEqual(self.cycle(config, fake)["operations"][0]["argv"], [engine.BREW, "pin", "--formula", "git"])
        self.assertEqual(self.cycle(config, fake)["operations"], [])
        self.assertEqual(self.cycle(configuration({"formulae": {"git": {"hold": False}}}), fake, "update")["operations"], [])
        self.assertTrue(fake.formulae["git"]["held"])

    def test_disabled_and_omitted_packages_are_not_observed_or_changed(self):
        fake = FakeProviders().formula("git")
        for config in (configuration(), configuration({"formulae": {"git": {"enabled": False}}})):
            self.assertEqual(self.cycle(config, fake)["operations"], [])
        self.assertEqual(fake.calls, [])
        self.assertEqual(fake.urls, [])

    def test_unknown_or_changed_digest_cannot_emit_commands(self):
        fake = FakeProviders().formula("git", present=False)
        config = configuration({"formulae": {"git": {}}})
        observed = self.observe(config, fake)
        fake.formulae["git"]["candidate"] = "3.0.0"
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observed)
        with self.assertRaises(engine.EngineError):
            engine.compile_operations(config, "setup", "arbitrary", fake)
        self.assertEqual(fake.executed, [])

    def test_source_drift_invalidates_review(self):
        fake = FakeProviders().formula("git", present=False)
        config = configuration({"formulae": {"git": {}}})
        with patch.dict(engine.scope_fingerprint.__globals__, {"source_digest": lambda: "before"}):
            observed = self.observe(config, fake)
        with patch.dict(engine.scope_fingerprint.__globals__, {"source_digest": lambda: "after"}), self.assertRaises(engine.EngineError):
            self.compile(config, fake, observed)

    def test_reverse_dependency_upgrade_cannot_replace_active_ansible(self):
        fake = FakeProviders().formula("openssl@3", present=True)
        fake.dependents["openssl@3"] = ["ansible"]
        config = configuration({"formulae": {"openssl@3": {}}})
        observed = self.observe(config, fake, "update")
        self.assertEqual(observed["plan"]["status"], "blocked")
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observed, "update")

    def test_controller_and_runtime_dependency_changes_block_before_operations(self):
        for direct, deps in (("ansible", []), ("git", ["ansible"]), ("git", ["python@3.14"])):
            with self.subTest(direct=direct, deps=deps):
                fake = FakeProviders().formula(direct, present=False, deps=deps)
                config = configuration({"formulae": {direct: {}}})
                with patch.object(engine.sys, "executable", "/opt/homebrew/Cellar/python@3.14/3.14.7/bin/python3.14"):
                    observed = self.observe(config, fake)
                    self.assertEqual(observed["plan"]["status"], "blocked")
                    with self.assertRaises(engine.EngineError):
                        self.compile(config, fake, observed)

    def test_missing_held_package_blocks_all_compilation(self):
        fake = FakeProviders().formula("git", present=False).formula("jq", present=False)
        config = configuration({"formulae": {"git": {"hold": True}, "jq": {}}})
        observed = self.observe(config, fake)
        self.assertEqual(observed["plan"]["status"], "blocked")
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observed)
        self.assertEqual(fake.executed, [])

    def npm_fixture(self, desired="3.6.2"):
        fake = FakeProviders().formula("node@22", version="22.1.0", candidate="22.5.0")
        fake.npm_available["prettier"] = desired
        config = configuration({"formulae": {"node@22": {}}, "npm": {"prettier": {"version": desired}}})
        return fake, config

    def test_npm_exact_install_and_repeat_use_selected_node_and_owned_prefix(self):
        fake, config = self.npm_fixture()
        result = self.cycle(config, fake)
        operation = result["operations"][0]
        self.assertEqual(operation["argv"][:2], [engine.PREFIX + "/opt/node@22/bin/node", engine.PREFIX + "/opt/node@22/lib/node_modules/npm/bin/npm-cli.js"])
        self.assertIn("--ignore-scripts", operation["argv"])
        self.assertIn(str(engine.PUBLIC / "npm/node@22"), operation["argv"])
        self.assertEqual(operation["argv"][-1], "prettier@3.6.2")
        self.assertEqual(self.cycle(config, fake)["operations"], [])

    def test_npm_exact_downgrade_is_visible_and_not_latest(self):
        fake, config = self.npm_fixture("3.0.0")
        fake.npm_installed["prettier"] = "4.0.0"
        observed = self.observe(config, fake)
        action = next(a for a in observed["plan"]["actions"] if a["provider"] == "npm")
        self.assertEqual((action["current_version"], action["requested_version"]), ("4.0.0", "3.0.0"))
        result = self.compile(config, fake, observed)
        self.assertEqual(result["operations"][0]["argv"][-1], "prettier@3.0.0")

    def test_selected_npm_install_lifecycle_refuses_all_operations(self):
        fake, config = self.npm_fixture()
        metadata = fake.get_json
        def with_lifecycle(url):
            record = metadata(url)
            if url.startswith("https://registry.npmjs.org/"):
                record["scripts"] = {"postinstall": "FORBIDDEN_INSTALL_SCRIPT"}
            return record
        fake.get_json = with_lifecycle
        observed = self.observe(config, fake)
        self.assertEqual(observed["plan"]["status"], "blocked")
        self.assertNotIn("FORBIDDEN_INSTALL_SCRIPT", json.dumps(observed))
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observed)
        self.assertEqual(fake.executed, [])

    def test_node_series_mismatch_rejects_before_tool_inspection(self):
        fake, config = self.npm_fixture()
        fake.formulae["node@22"]["version"] = "24.1.0"
        with self.assertRaises(engine.EngineError):
            self.observe(config, fake)
        self.assertFalse(any("list" in call and "--global" in call for call in fake.calls))

    def test_generated_environment_does_not_inherit_credentials_or_hooks(self):
        fake, config = self.npm_fixture()
        with patch.dict(engine.os.environ, {"OP_SERVICE_ACCOUNT_TOKEN": "PRIVATE_SENTINEL", "NODE_OPTIONS": "--require /tmp/untrusted", "NPM_TOKEN": "PRIVATE_SENTINEL"}):
            operations = self.compile(config, fake, self.observe(config, fake))["operations"]
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(operations))
        env = operations[0]["environment"]
        self.assertNotIn("NODE_OPTIONS", env)
        self.assertEqual(env["npm_config_userconfig"], "/dev/null")
        self.assertEqual(env["npm_config_ignore_scripts"], "true")
        self.assertEqual(env["HOMEBREW_NO_AUTO_UPDATE"], "1")

    def test_fresh_runtime_is_explicit_prerequisite_not_fake_binding(self):
        fake, config = self.npm_fixture()
        fake.formulae["node@22"]["present"] = False
        observed = self.observe(config, fake)
        self.assertNotIn("npm", observed["bindings"])
        self.assertEqual(observed["plan"]["status"], "unresolved")
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observed)
        stage = engine.prerequisite_scope(config, observed)
        self.assertEqual(set(stage["packages"]["formulae"]), {"node@22"})
        self.assertEqual(stage["packages"]["npm"], {})

    def test_prerequisite_stage_carries_selected_formula_and_cask_holds_first(self):
        fake, config = self.npm_fixture()
        fake.formulae["node@22"]["present"] = False
        fake.formula("pinned-runtime", version="1.0.0", candidate="2.0.0")
        fake.casks["pinned-app"] = {"present": True, "version": "1.0", "candidate": "2.0", "held": False, "deps": []}
        config["packages"]["formulae"]["pinned-runtime"] = {"enabled": True, "hold": True}
        config["packages"]["casks"]["pinned-app"] = {"enabled": True, "hold": True}
        observed = self.observe(config, fake)
        stage = engine.prerequisite_scope(config, observed)
        self.assertTrue(stage["packages"]["formulae"]["pinned-runtime"]["hold"])
        self.assertTrue(stage["packages"]["casks"]["pinned-app"]["hold"])
        compiled = self.compile(stage, fake, self.observe(stage, fake))
        self.assertEqual([item["argv"][1] for item in compiled["operations"]], ["pin", "pin", "install"])

    def test_metadata_failure_is_redacted_no_fallback(self):
        fake = FakeProviders().formula("git", present=False)
        def unavailable(url):
            raise engine.EngineError("Public metadata unavailable")
        fake.get_json = unavailable
        with self.assertRaises(engine.EngineError):
            self.observe(configuration({"formulae": {"git": {}}}), fake)
        self.assertEqual(fake.executed, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
