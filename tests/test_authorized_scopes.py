"""Approved package scopes: inert state transitions, no trust/install/auth calls."""
import copy
import json
from pathlib import Path
import types
import sys
import unittest
from unittest.mock import patch

import test_engine
import test_entrypoint
from test_engine import FakeProviders, configuration, engine, planner
from test_full_selection import SelectionReader


class LifecycleReader(FakeProviders):
    def __init__(self):
        super().__init__()
        self.formula("node", version="26.8.2", candidate="26.8.2")
        self.npm_available.update({"@posthog/cli": "0.18.3", "unrelated": "1.0.0"})
        self.scripts = {"postinstall": "node fixture-install.js"}
        self.dist = {"integrity": "sha512-" + "A" * 88, "tarball": "https://registry.npmjs.org/fixture/-/fixture-0.18.3.tgz"}

    def get_json(self, url):
        record = super().get_json(url)
        if "posthog" in url:
            record.update(scripts=copy.deepcopy(self.scripts), dist=copy.deepcopy(self.dist))
        return record


class ApprovedTapReader(SelectionReader):
    def __init__(self):
        super().__init__()
        self.remote = "https://github.com/getsentry/homebrew-tools"
        self.formula("example/tools/fixture", present=False)

    def run(self, argv):
        return super().run([item.replace("getsentry/tools/sentry-cli", "example/tools/fixture") for item in argv]).replace("example/tools/fixture", "getsentry/tools/sentry-cli").replace("example/tools", "getsentry/tools")


class AuthorizedScopeTests(unittest.TestCase):
    setUp = test_engine.EngineTests.setUp
    observe = test_engine.EngineTests.observe
    compile = test_engine.EngineTests.compile

    def npm_config(self, opted=False):
        entry = {"version": "0.18.3"}
        if opted:
            entry["allow_lifecycle_scripts"] = True
        return configuration({"formulae": {"node": {}}, "npm": {"@posthog/cli": entry, "unrelated": {"version": "1.0.0"}}})

    def tap_config(self):
        return configuration({"formulae": {"getsentry/tools/sentry-cli": {"trust": True}}})

    def test_trust_and_lifecycle_schema_are_closed_strict_and_exact(self):
        for packages in ({"formulae": {"git": {"trust": True}}},
                         {"formulae": {"example/tools/fixture": {"trust": "true"}}},
                         {"formulae": {"example/tools/fixture": {"trust": True}}},
                         {"casks": {"fixture": {"trust": True}}},
                         {"npm": {"fixture": {"allow_lifecycle_scripts": True}}},
                         {"npm": {"fixture": {"version": "0.18.3", "allow_lifecycle_scripts": "true"}}},
                         {"npm": {"fixture": {"version": "0.18.3", "allow_lifecycle_scripts": True}}},
                         {"npm": {"fixture": {"version": "0.18.3", "allow_lifecycle_scripts": False}}},
                         {"npm": {"@posthog/cli": {"version": "0.18.4", "allow_lifecycle_scripts": True}}},
                         {"uv_tools": {"fixture": {"version": "0.18.3", "allow_lifecycle_scripts": True}}}):
            with self.subTest(packages=packages), self.assertRaises(planner.ConfigError):
                configuration(packages)
        for name in ("getsentry/tools/sentry-cli", "resend/cli/resend", "mobile-dev-inc/tap/maestro"):
            self.assertTrue(configuration({"formulae": {name: {"trust": True}}})["packages"]["formulae"][name]["trust"])

    def test_default_lifecycle_blocked_and_unrelated_operation_keeps_scripts_off(self):
        fake = LifecycleReader()
        config = self.npm_config()
        observed = self.observe(config, fake)
        self.assertEqual(observed["plan"]["status"], "blocked")
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observed)
        opted = self.npm_config(True)
        result = self.compile(opted, fake, self.observe(opted, fake))
        operations = {item["id"]: item for item in result["operations"] if item["provider"] == "npm"}
        self.assertEqual(operations["@posthog/cli"]["argv"][-1], "@posthog/cli@0.18.3")
        self.assertEqual(operations["@posthog/cli"]["environment"]["npm_config_ignore_scripts"], "false")
        self.assertNotIn("--ignore-scripts", operations["@posthog/cli"]["argv"])
        self.assertEqual(operations["unrelated"]["environment"]["npm_config_ignore_scripts"], "true")
        self.assertIn("--ignore-scripts", operations["unrelated"]["argv"])

    def test_lifecycle_script_or_distribution_changes_invalidate_review(self):
        config = self.npm_config(True)
        for field, replacement in (("scripts", {"postinstall": "node changed.js"}),
                                   ("dist", {"integrity": "sha512-" + "B" * 88, "tarball": "https://registry.npmjs.org/fixture/-/fixture-0.18.3.tgz"})):
            fake = LifecycleReader()
            observed = self.observe(config, fake)
            setattr(fake, field, replacement)
            with self.subTest(field=field), self.assertRaises(engine.EngineError):
                self.compile(config, fake, observed)

    def test_tap_prerequisite_only_emits_item_trust_then_requires_fresh_formula_review(self):
        config = self.tap_config()
        fake = ApprovedTapReader()
        fake.tap_present = fake.trusted = False
        observed = self.observe(config, fake)
        self.assertFalse(any(call[1:2] == ["info"] for call in fake.calls))
        self.assertTrue(all(item["action"] == "prepare-tap" for item in observed["plan"]["actions"]))
        self.assertEqual(engine.prerequisite_scope(config, observed), config)
        with self.assertRaises(engine.EngineError):
            engine.compile_operations(config, "setup", "not-reviewed", fake)
        prepared = self.compile(config, fake, observed)
        commands = [item["argv"] for item in prepared["operations"]]
        self.assertTrue(any(command[1:2] == ["tap"] for command in commands))
        self.assertIn([engine.BREW, "trust", "--formula", "getsentry/tools/sentry-cli"], commands)
        self.assertFalse(any(command[1:2] in (["install"], ["upgrade"]) for command in commands))
        self.assertFalse(any("--tap" in command for command in commands))
        fake.tap_present = fake.trusted = True  # In-memory preparation, not a process.
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observed)
        ready = self.observe(config, fake)
        self.assertIsNone(engine.prerequisite_scope(config, ready))
        self.assertEqual(self.compile(config, fake, ready)["operations"][0]["argv"][1], "install")

    def test_installed_trusted_formula_rerun_does_not_repeat_trust_or_install(self):
        config = self.tap_config()
        fake = ApprovedTapReader().formula("example/tools/fixture", present=True)
        result = self.compile(config, fake, self.observe(config, fake))
        self.assertEqual(result["operations"], [])

    def test_no_formula_loading_after_tap_drift_before_item_trust(self):
        config = self.tap_config()
        fake = ApprovedTapReader()
        fake.trusted = False
        observed = self.observe(config, fake)
        fake.head = "e" * 40
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observed)
        self.assertFalse(any(call[1:2] == ["info"] for call in fake.calls))

    def test_update_never_prepares_missing_tap_even_when_configuration_opts_in(self):
        fake = ApprovedTapReader()
        fake.tap_present = fake.trusted = False
        with self.assertRaises(engine.EngineError):
            self.observe(self.tap_config(), fake, "update")
        self.assertFalse(fake.executed)
        self.assertFalse(any(call[1:2] == ["info"] for call in fake.calls))

    def test_offline_and_noninteractive_routes_never_prepare_tap_or_run_scripts(self):
        fixture = test_entrypoint.EntrypointTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.selected.write_text(json.dumps({"schema_version": 1, "profile": "dev", "packages": {
            "formulae": {"node": {}, "getsentry/tools/sentry-cli": {"trust": True}},
            "npm": {"@posthog/cli": {"version": "0.18.3", "allow_lifecycle_scripts": True}}}}))
        status, output, _ = fixture.main(["--plan"])
        self.assertEqual(status, 3)
        self.assertFalse(json.loads(output)["executable"])
        status, _, error = fixture.main(["--apply", "--non-interactive"])
        self.assertEqual(status, 2)
        self.assertIn("Needs human", error)

    def runtime(self, executable, prefix, base_prefix, ansible):
        return patch.dict(engine.observe.__globals__, {"sys": types.SimpleNamespace(
            executable=executable, prefix=prefix, base_prefix=base_prefix,
            modules={**sys.modules, "ansible": types.SimpleNamespace(__file__=ansible)})})

    def test_actual_homebrew_controller_and_python_formula_remain_protected(self):
        fake = FakeProviders().formula("fixture-lib", present=False, deps=["openssl@3"])
        fake.formula("openssl@3", version="3.1.0", candidate="3.2.0")
        fake.dependents["openssl@3"] = ["ansible", "python@3.14"]
        config = configuration({"formulae": {"fixture-lib": {}}})
        for runtime in ("ansible", "python@3.14"):
            prefix = engine.PREFIX + "/Cellar/" + runtime + "/1.0"
            with self.subTest(runtime=runtime), self.runtime(prefix + "/bin/python", prefix, prefix, prefix + "/ansible/__init__.py"):
                observed = self.observe(config, fake)
                self.assertEqual(observed["plan"]["status"], "blocked")
                with self.assertRaises(engine.EngineError):
                    self.compile(config, fake, observed)

    def test_isolated_controller_does_not_confuse_unrelated_installed_ansible_with_active_runtime(self):
        fake = FakeProviders().formula("fixture-lib", present=False, deps=["openssl@3"])
        fake.formula("openssl@3", version="3.2.0", candidate="3.2.0")
        fake.formula("python@3.14", version="3.14.7", candidate="3.14.7")
        fake.formula("asc", version="5.2.1", candidate="5.3.4")
        fake.dependents["openssl@3"] = ["ansible", "python@3.14"]
        config = configuration({"formulae": {"fixture-lib": {}, "python@3.14": {"hold": True}, "asc": {"hold": True}}})
        root = str(Path(self.scratch.name) / "isolated")
        with self.runtime(root + "/bin/python", root, root, root + "/lib/ansible/__init__.py"):
            observed = self.observe(config, fake)
            self.assertEqual(observed["bindings"]["active_runtime"]["formulae"], [])
            compiled = self.compile(config, fake, observed)
            self.assertEqual([item["argv"][1] for item in compiled["operations"]], ["pin", "pin", "install"])
            changed = root + "/different"
            with self.runtime(changed + "/bin/python", changed, changed, changed + "/lib/ansible/__init__.py"), self.assertRaises(engine.EngineError):
                self.compile(config, fake, observed)

    def test_virtualenv_base_python_is_protected_even_outside_its_prefix(self):
        root = str(Path(self.scratch.name) / "controller")
        base = engine.PREFIX + "/opt/python@3.14"
        with self.runtime(root + "/bin/python", root, base, root + "/lib/ansible/__init__.py"):
            self.assertIn("python@3.14", engine.active_runtime_evidence()["formulae"])

    def test_current_shared_dependency_is_proven_but_outdated_or_changed_evidence_blocks(self):
        config = configuration({"formulae": {"fixture-lib": {}, "private-runtime": {"hold": True}}})
        fake = FakeProviders().formula("fixture-lib", present=False, deps=["ca-certificates"])
        fake.formula("private-runtime", version="1.0.0", candidate="1.0.0")
        fake.formula("ca-certificates", version="2026.1", candidate="2026.1")
        fake.dependents["ca-certificates"] = ["private-runtime"]
        observed = self.observe(config, fake)
        self.assertTrue(observed["bindings"]["protected_dependency_checks"]["ca-certificates"]["unchanged"])
        self.assertEqual([item["argv"][1] for item in self.compile(config, fake, observed)["operations"]], ["pin", "install"])
        fake.formulae["ca-certificates"]["outdated"] = True
        blocked = self.observe(config, fake)
        self.assertEqual(blocked["plan"]["status"], "blocked")
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observed)

    def test_nonzero_dependency_revision_requires_matching_installed_revision(self):
        fake = FakeProviders().formula("fixture-lib", present=False, deps=["ca-certificates"])
        fake.formula("private-runtime", version="1.0", candidate="1.0")
        fake.formula("ca-certificates", version="2026.1", candidate="2026.1")
        fake.formulae["ca-certificates"].update(revision=1, outdated=False)
        fake.dependents["ca-certificates"] = ["private-runtime"]
        config = configuration({"formulae": {"fixture-lib": {}, "private-runtime": {"hold": True}}})
        self.assertEqual(self.observe(config, fake)["plan"]["status"], "blocked")
        fake.formulae["ca-certificates"]["version"] = "2026.1_1"
        self.assertEqual(self.observe(config, fake)["plan"]["status"], "planned")


if __name__ == "__main__":
    unittest.main(verbosity=2)
