"""Synthetic, offline contract tests: never invoke a package manager or host setup."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rebuild_planner", ROOT / "planner.py")
planner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = planner
SPEC.loader.exec_module(planner)


def config(packages=None, **extra):
    value = {"schema_version": 1, "profile": "dev", "packages": packages or {}}
    value.update(extra)
    return value


def snapshot(provider="formulae", name="git", *, present=True, version="1.0.0",
             held=False, available=True, versions=None, candidate="2.0.0"):
    return {
        "installed": {provider: {name: {"present": present, "version": version, "held": held}}},
        "availability": {provider: {name: {"available": available,
                                           "versions": versions or [], "candidate": candidate}}},
        "capabilities": {"cask_hold": True},
        "bindings": {}, "platform": "arm64",
    }


class PlannerTests(unittest.TestCase):
    def load(self, value):
        return planner.load_config(yaml.safe_dump(value))

    def plan(self, value, state=None, operation="setup"):
        # A pure planner must not run a manager, shell, or network operation.
        with patch.object(subprocess, "run", side_effect=AssertionError("subprocess forbidden")), \
             patch.object(subprocess, "Popen", side_effect=AssertionError("process forbidden")), \
             patch.object(os, "system", side_effect=AssertionError("shell forbidden")), \
             patch.object(socket, "socket", side_effect=AssertionError("network forbidden")):
            return planner.build_plan(self.load(value), state=state, operation=operation)

    def action(self, result, provider="formulae", name="git"):
        matches = [a for a in result["actions"] if a["provider"] == provider and a["id"] == name]
        self.assertEqual(len(matches), 1, result)
        return matches[0]["action"]

    def test_minimal_complete_empty_selection(self):
        result = self.plan(config(), snapshot())
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["profile"], "dev")

    def test_private_section_rejected_even_disabled(self):
        with self.assertRaises(planner.ConfigError):
            self.load(config(private={"synthetic_feature": {"enabled": False}}))

    def test_duplicate_keys_at_any_depth_rejected(self):
        for text in (
            "schema_version: 1\nprofile: dev\nprofile: admin\n",
            "schema_version: 1\nprofile: dev\npackages:\n  formulae:\n    git: {}\n    git: {}\n",
            "schema_version: 1\nprofile: dev\npackages:\n  npm:\n    prettier: {version: '1.0.0', version: '2.0.0'}\n",
        ):
            with self.subTest(text=text), self.assertRaises(planner.ConfigError):
                planner.load_config(text)

    def test_unknown_keys_and_wrong_types_rejected(self):
        bad = [
            config(extends="other.yml"), config(profile="third"), config(schema_version=True),
            config(schema_version="1"), config(packages=["git"]), config(settings={"mystery": True}),
            config(settings={"remote_login": "false"}), config({"brew": {"git": {}}}),
            config({"formulae": ["git"]}), config({"formulae": {"git": None}}),
            config({"formulae": {"git": {"enabled": "false"}}}),
            config({"formulae": {"git": {"state": "absent"}}}),
            config({"npm": {"prettier": {"version": 3.6}}}),
            config(runtimes={"uv_python": [3.12]}),
        ]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(planner.ConfigError):
                self.load(value)

    def test_executable_yaml_tags_aliases_and_merge_rejected(self):
        for suffix in (
            "packages: !!python/object/apply:os.system ['echo forbidden']",
            "packages: &p {}\nsettings: *p", "packages: {<<: {formulae: {git: {}}}}",
        ):
            with self.subTest(suffix=suffix), self.assertRaises(planner.ConfigError):
                planner.load_config("schema_version: 1\nprofile: dev\n" + suffix)

    def test_oversize_document_rejected(self):
        with self.assertRaises(planner.ConfigError):
            planner.load_config("#" + "x" * (1024 * 1024 + 1) + "\nschema_version: 1\nprofile: dev\n")

    def test_excessive_nesting_rejected_without_recursion_traceback(self):
        text = "schema_version: 1\nprofile: dev\npackages: " + "[" * 2000 + "0" + "]" * 2000
        with self.assertRaises(planner.ConfigError) as caught:
            planner.load_config(text)
        self.assertNotIn("Traceback", str(caught.exception))

    def test_multi_document_and_empty_yaml_rejected(self):
        for text in ("", "null", "[]", "schema_version: 1\nprofile: dev\n---\nprofile: admin\n"):
            with self.subTest(text=text), self.assertRaises(planner.ConfigError):
                planner.load_config(text)

    def test_canonical_ids_reject_manager_escape_forms(self):
        for provider in ("formulae", "casks", "npm", "uv_tools"):
            for name in ("../private", "/tmp/tool", "https://example.invalid/tool.tgz",
                         "git+ssh://private/tool", "file:../tool", "$(id)", "a;id", "--help",
                         "{{ lookup('pipe', 'id') }}", "tool@1.2.3"):
                # Real Homebrew formula version IDs are the one explicit exception.
                if provider == "formulae" and name == "tool@1.2.3":
                    continue
                with self.subTest(provider=provider, name=name), self.assertRaises(planner.ConfigError):
                    self.load(config({provider: {name: {}}}))

    def test_valid_scoped_npm_and_versioned_formula(self):
        self.load(config({"formulae": {"node@22": {}}, "npm": {"@scope/tool": {"version": "1.2.3"}}}))

    def test_manager_field_limits(self):
        for provider, record in (
            ("formulae", {"version": "1.2.3"}), ("casks", {"version": "1.2.3"}),
            ("mas", {"version": "1.2.3"}), ("mas", {"hold": True}),
            ("npm", {"hold": True}), ("uv_tools", {"hold": True}),
            ("npm", {"version": "1.2.3", "hold": True}),
        ):
            name = "123456789" if provider == "mas" else "example"
            with self.subTest(provider=provider, record=record), self.assertRaises(planner.ConfigError):
                self.load(config({provider: {name: record}}))

    def test_exact_versions_reject_ranges_tags_and_whitespace(self):
        for provider in ("npm", "uv_tools"):
            for version in ("latest", "^1.2.3", "~1.2.3", ">=1.2.3", "1.*", " 1.2.3", "1.2.3 "):
                with self.subTest(provider=provider, version=version), self.assertRaises(planner.ConfigError):
                    self.load(config({provider: {"tool": {"version": version}}}))

    def test_npm_prerelease_numeric_components_are_strict_semver(self):
        for version in ("1.2.3-01", "1.2.3-alpha.01", "01.2.3"):
            with self.subTest(version=version), self.assertRaises(planner.ConfigError):
                self.load(config({"npm": {"tool": {"version": version}}}))
        for version in ("1.2.3-0", "1.2.3-rc.1+build.001", "1.2.3-alpha01"):
            with self.subTest(version=version):
                self.load(config({"npm": {"tool": {"version": version}}}))

    def test_python_requires_full_unique_versions(self):
        for versions in (["3.12"], ["latest"], ["3.12.3", "3.12.3"], ["../python"], "3.12.3"):
            with self.subTest(versions=versions), self.assertRaises(planner.ConfigError):
                self.load(config(runtimes={"uv_python": versions}))

    def test_mas_ids_must_be_quoted_numeric_strings(self):
        for name in (123456789, "app-name", "00123"):
            with self.subTest(name=name), self.assertRaises(planner.ConfigError):
                self.load(config({"mas": {name: {}}}))

    def test_cross_manager_collision_rejected(self):
        for providers, name in ((("formulae", "uv_tools"), "ruff"),
                                (("formulae", "casks"), "example"),
                                (("formulae", "npm"), "prettier")):
            with self.subTest(providers=providers):
                result = self.plan(config({provider: {name: {}} for provider in providers}))
                self.assertEqual(result["status"], "blocked")
                self.assertTrue(all(a["action"] == "blocked" for a in result["actions"]))

    def test_dev_may_explicitly_select_apps(self):
        self.load(config({"casks": {"devonthink": {}, "carbon-copy-cloner": {}}}))

    def test_disabled_duplicate_owner_does_not_block_selected_owner(self):
        result = self.plan(config({"formulae": {"ruff": {}}, "uv_tools": {"ruff": {"enabled": False}}}),
                           snapshot("formulae", "ruff"))
        self.assertEqual(self.action(result, "formulae", "ruff"), "noop")
        self.assertEqual(self.action(result, "uv_tools", "ruff"), "preserve")

    def test_missing_evidence_is_unresolved_not_install(self):
        result = self.plan(config({"formulae": {"git": {}}}))
        self.assertEqual(result["evidence"], "configuration-only")
        self.assertEqual(self.action(result), "unresolved")
        self.assertEqual(result["status"], "unresolved")

    def test_present_never_upgrades_during_setup(self):
        self.assertEqual(self.action(self.plan(config({"formulae": {"git": {}}}), snapshot())), "noop")

    def test_explicit_absent_available_plans_install(self):
        result = self.plan(config({"formulae": {"git": {}}}), snapshot(present=False))
        self.assertEqual(self.action(result), "install")
        self.assertEqual(result["evidence"], "synthetic")

    def test_unavailable_never_falls_back(self):
        result = self.plan(config({"formulae": {"git": {}}}), snapshot(present=False, available=False))
        self.assertEqual(self.action(result), "blocked")
        self.assertEqual(result["status"], "blocked")

    def test_unknown_availability_stays_unresolved(self):
        state = snapshot(present=False)
        del state["availability"]
        self.assertEqual(self.action(self.plan(config({"formulae": {"git": {}}}), state)), "unresolved")

    def test_disabled_preserves_without_metadata(self):
        result = self.plan(config({"formulae": {"git": {"enabled": False}}}))
        self.assertEqual(self.action(result), "preserve")

    def test_removed_or_unselected_never_gets_action(self):
        state = snapshot()
        for selection in ({}, {"casks": {"firefox": {"enabled": False}}}):
            result = self.plan(config(selection), state)
            self.assertFalse(any(a["id"] == "git" for a in result["actions"]))
            self.assertFalse(any(a["action"] in ("uninstall", "remove", "unpin") for a in result["actions"]))

    def test_update_skips_missing_packages(self):
        result = self.plan(config({"formulae": {"git": {}}}), snapshot(present=False), "update")
        self.assertEqual(self.action(result), "preserve")

    def test_update_selected_installed_only(self):
        state = snapshot()
        state["installed"]["formulae"]["private-runtime"] = {"present": True, "held": True, "version": "1"}
        result = self.plan(config({"formulae": {"git": {}}}), state, "update")
        self.assertEqual(self.action(result), "change-version")
        self.assertEqual(len(result["actions"]), 1)

    def test_update_candidate_direction_is_not_assumed(self):
        for current, candidate in (("2.0.0", "1.0.0"), ("2026.4-custom", "2026.3")):
            result = self.plan(config({"formulae": {"git": {}}}),
                               snapshot(version=current, candidate=candidate), "update")
            self.assertEqual(self.action(result), "change-version")
            self.assertIn("requires-review", result["actions"][0]["reason"])

    def test_native_pin_preserved_even_when_not_requested(self):
        for record in ({}, {"hold": False}):
            result = self.plan(config({"formulae": {"git": record}}), snapshot(held=True), "update")
            self.assertIn(self.action(result), ("noop", "preserve"))

    def test_hold_existing_plans_hold_and_second_run_noop(self):
        selection = config({"formulae": {"git": {"hold": True}}})
        self.assertEqual(self.action(self.plan(selection, snapshot())), "hold")
        self.assertIn(self.action(self.plan(selection, snapshot(held=True))), ("noop", "preserve"))

    def test_hold_absent_blocks_without_install(self):
        result = self.plan(config({"formulae": {"git": {"hold": True}}}), snapshot(present=False))
        self.assertEqual(self.action(result), "blocked")

    def test_cask_hold_capability_required(self):
        for capability, expected in ((False, "blocked"), (None, "unresolved"), (True, "hold")):
            state = snapshot("casks", "firefox")
            state["capabilities"] = {} if capability is None else {"cask_hold": capability}
            result = self.plan(config({"casks": {"firefox": {"hold": True}}}), state)
            self.assertEqual(self.action(result, "casks", "firefox"), expected)

    def tool_fixture(self, provider="npm", requested="1.2.3", current="1.0.0"):
        state = snapshot(provider, "tool", version=current, versions=[requested])
        if provider == "npm":
            packages = {"formulae": {"node@22": {}}, "npm": {"tool": {"version": requested}}}
            state["installed"]["formulae"] = {"node@22": {"present": True, "version": "22.1.0"}}
            state["bindings"]["npm"] = {"runtime": "node@22", "verified": True}
            return config(packages), state
        packages = {"formulae": {"uv": {}}, "uv_tools": {"tool": {"version": requested}}}
        state["installed"]["formulae"] = {"uv": {"present": True, "version": "0.8.0"}}
        state["installed"]["uv_python"] = {"3.12.3": {"present": True, "version": "3.12.3"}}
        state["bindings"]["uv_tools"] = {"runtime": "3.12.3", "verified": True}
        return config(packages, runtimes={"uv_python": ["3.12.3"]}), state

    def test_exact_match_noop_in_setup_and_update(self):
        for provider in ("npm", "uv_tools"):
            value, state = self.tool_fixture(provider, current="1.2.3")
            for operation in ("setup", "update"):
                self.assertEqual(self.action(self.plan(value, state, operation), provider, "tool"), "noop")

    def test_exact_upgrade_and_downgrade_are_version_changes(self):
        for provider in ("npm", "uv_tools"):
            for current in ("1.0.0", "2.0.0"):
                value, state = self.tool_fixture(provider, current=current)
                result = self.plan(value, state)
                self.assertEqual(self.action(result, provider, "tool"), "change-version")
                self.assertIn("1.2.3", json.dumps(result))

    def test_exact_unavailable_and_unknown_do_not_fall_back(self):
        value, state = self.tool_fixture()
        state["availability"]["npm"]["tool"]["versions"] = ["2.0.0"]
        self.assertEqual(self.action(self.plan(value, state), "npm", "tool"), "blocked")
        del state["availability"]["npm"]["tool"]["versions"]
        self.assertEqual(self.action(self.plan(value, state), "npm", "tool"), "unresolved")

    def test_runtime_binding_missing_unverified_or_wrong_rejected(self):
        for provider in ("npm", "uv_tools"):
            for binding in (None, {"runtime": "node@24" if provider == "npm" else "3.11.9", "verified": True},
                            {"runtime": "node@22" if provider == "npm" else "3.12.3", "verified": False}):
                value, state = self.tool_fixture(provider)
                if binding is None:
                    del state["bindings"][provider]
                else:
                    state["bindings"][provider] = binding
                result = self.plan(value, state)
                self.assertIn(self.action(result, provider, "tool"), ("blocked", "unresolved"))

    def test_update_missing_tool_skips_before_runtime_work(self):
        for provider in ("npm", "uv_tools"):
            state = snapshot(provider, "tool", present=False)
            result = self.plan(config({provider: {"tool": {}}}), state, "update")
            self.assertEqual(self.action(result, provider, "tool"), "preserve")

    def test_ambiguous_selected_runtime_rejected(self):
        value, state = self.tool_fixture()
        value["packages"]["formulae"]["node@24"] = {}
        try:
            result = self.plan(value, state)
        except planner.ConfigError:
            return
        self.assertEqual(result["status"], "blocked")

    def test_missing_tool_prerequisite_does_not_silently_add_one(self):
        result = self.plan(config({"uv_tools": {"ruff": {}}}))
        self.assertIn(result["status"], ("unresolved", "blocked"))
        self.assertFalse(any(a["provider"] == "formulae" and a["action"] == "install" for a in result["actions"]))

    def test_mas_deferred_outside_explicit_finish(self):
        state = snapshot("mas", "123456789", present=False)
        result = self.plan(config({"mas": {"123456789": {}}}), state)
        self.assertEqual(self.action(result, "mas", "123456789"), "deferred")

    def test_platform_evidence_does_not_override_unavailability(self):
        for architecture in ("arm64", "x86_64"):
            state = snapshot(present=False, available=False)
            state["platform"] = architecture
            self.assertEqual(self.action(self.plan(config({"formulae": {"git": {}}}), state)), "blocked")

    def test_input_objects_and_files_not_mutated(self):
        value, state = self.tool_fixture()
        normalized = self.load(value)
        expected_config, expected_state = copy.deepcopy(normalized), copy.deepcopy(state)
        planner.build_plan(normalized, state=state)
        self.assertEqual(normalized, expected_config)
        self.assertEqual(state, expected_state)

    def test_malformed_snapshot_rejected_not_truthy_interpreted(self):
        states = [
            {"unexpected": True}, {"installed": []}, {"platform": "other"},
            {"installed": {"formulae": {"git": {"present": "false"}}}},
            {"installed": {"formulae": {"git": {"present": True, "held": "false"}}}},
            {"availability": {"formulae": {"git": {"available": "false"}}}},
            {"capabilities": {"cask_hold": "true"}},
            {"bindings": {"npm": {"runtime": "node@22", "verified": "true"}}},
        ]
        for state in states:
            with self.subTest(state=state), self.assertRaises(planner.ConfigError):
                self.plan(config({"formulae": {"git": {}}}), state)

    def test_unknown_installed_map_is_not_absence(self):
        for state in ({}, {"installed": {}}, {"installed": {"formulae": {}}}):
            result = self.plan(config({"formulae": {"git": {}}}), state)
            self.assertEqual(self.action(result), "unresolved")

    def test_exact_change_cannot_override_native_hold(self):
        value, state = self.tool_fixture()
        state["installed"]["npm"]["tool"]["held"] = True
        result = self.plan(value, state)
        self.assertEqual(self.action(result, "npm", "tool"), "blocked")

    def test_python_unavailable_has_no_latest_fallback(self):
        value = config({"formulae": {"uv": {}}}, runtimes={"uv_python": ["3.12.3"]})
        state = snapshot("uv_python", "3.12.3", present=False, available=False)
        state["installed"]["formulae"] = {"uv": {"present": True, "version": "0.8.0"}}
        result = self.plan(value, state)
        self.assertEqual(self.action(result, "uv_python", "3.12.3"), "blocked")
        self.assertNotIn("latest", json.dumps(result["actions"]))

    def test_finish_does_not_select_general_packages(self):
        value = config({"formulae": {"git": {}}, "mas": {"123456789": {}}})
        state = snapshot()
        state["installed"]["mas"] = {"123456789": {"present": True, "version": "1.0.0"}}
        result = self.plan(value, state, "finish")
        self.assertIn(self.action(result), ("preserve", "deferred"))

    def test_unrecognized_operation_rejected(self):
        with self.assertRaises(planner.ConfigError):
            self.plan(config(), operation="erase")

    def test_plan_is_deterministic_and_never_executable(self):
        value, state = self.tool_fixture()
        first = self.plan(value, state)
        self.assertEqual(first, self.plan(value, state))
        self.assertFalse(first["executable"])
        self.assertIn("not approval", " ".join(first["warnings"]))

    def test_examples_are_independent_valid_complete_selections(self):
        paths = sorted((ROOT / "examples").glob("*.yml"))
        self.assertEqual(len(paths), 3)
        for path in paths:
            with self.subTest(path=path.name):
                value = planner.load_config(path.read_text())
                result = planner.build_plan(value)
                self.assertFalse(result["executable"])
                self.assertEqual(result["evidence"], "configuration-only")
                self.assertNotEqual(result["status"], "planned")

    def test_parse_errors_never_echo_values(self):
        sentinel = "PRIVATE_SENTINEL_DO_NOT_PRINT"
        for text in (
            f"schema_version: 1\nprofile: dev\nprivate: {sentinel}\n",
            f"schema_version: 1\nprofile: [{sentinel}\n",
            f"schema_version: 1\nprofile: dev\n{sentinel}: 1\n",
        ):
            with self.subTest(text=text), self.assertRaises(planner.ConfigError) as caught:
                planner.load_config(text)
            self.assertNotIn(sentinel, str(caught.exception))

    def test_non_utf8_api_text_is_redacted_config_error(self):
        with self.assertRaises(planner.ConfigError) as caught:
            planner.load_config("schema_version: 1\nprofile: dev\n#\ud800PRIVATE_SENTINEL")
        self.assertNotIn("PRIVATE_SENTINEL", str(caught.exception))


class CliTests(unittest.TestCase):
    def invoke(self, text, *arguments):
        with tempfile.TemporaryDirectory(prefix="rebuild-plan-qa-") as temp:
            root = Path(temp)
            source = root / "config.yml"
            source.write_text(text)
            before = source.read_bytes(), source.stat().st_mtime_ns, source.stat().st_ino
            result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "plan.py"),
                                     "--config", str(source), *arguments], capture_output=True,
                                    text=True, timeout=10, cwd=root,
                                    env={"PATH": "/nonexistent", "HOME": str(root),
                                         "PYTHONDONTWRITEBYTECODE": "1",
                                         "OP_SERVICE_ACCOUNT_TOKEN": "PRIVATE_ENV_SENTINEL"})
            self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns, source.stat().st_ino), before)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["config.yml"])
            self.assertNotIn("PRIVATE_ENV_SENTINEL", result.stdout + result.stderr)
            return result

    def test_cli_configuration_only_never_claims_installed_state(self):
        result = self.invoke(yaml.safe_dump(config({"formulae": {"git": {}}})), "--plan")
        self.assertNotEqual(result.returncode, 1, result.stderr)
        self.assertIn("configuration-only", result.stdout)
        self.assertIn("unresolved", result.stdout)

    def test_cli_has_no_apply_or_user_supplied_state_bypass(self):
        for flag in ("--apply", "--state", "--profile"):
            result = self.invoke(yaml.safe_dump(config()), "--plan", flag, "admin")
            self.assertNotEqual(result.returncode, 0)

    def test_cli_parse_error_is_redacted_and_no_traceback(self):
        result = self.invoke("schema_version: 1\nprofile: [PRIVATE_SENTINEL_DO_NOT_PRINT\n", "--plan")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("PRIVATE_SENTINEL_DO_NOT_PRINT", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_excessive_nesting_is_safe_error(self):
        result = self.invoke("schema_version: 1\nprofile: dev\npackages: " + "[" * 2000 + "0" + "]" * 2000, "--plan")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
