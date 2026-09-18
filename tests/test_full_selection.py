"""Additional selection boundaries using only injected readers and temp applications."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_engine
from test_engine import FakeProviders, configuration, engine, planner


class SelectionReader(FakeProviders):
    def __init__(self):
        super().__init__()
        self.tap_present = True
        self.trusted = True
        self.remote = "https://github.com/example/homebrew-tools"
        self.head = "a" * 40
        self.dirty = ""
        self.checksum = "b" * 64
        self.app = "Fixture.app"

    def run(self, argv):
        args = list(argv)
        if args == [engine.BREW, "tap"]:
            self.calls.append(args)
            return "example/tools\n" if self.tap_present else ""
        if args == [engine.BREW, "trust", "--json=v1"]:
            self.calls.append(args)
            return json.dumps({"taps": [], "formulae": ["example/tools/fixture"] if self.trusted else [], "casks": [], "commands": []})
        if args[0] == "/usr/bin/git":
            self.calls.append(args)
            assert args[1:5] == ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]
            if args[-3:] == ["remote", "get-url", "origin"]:
                return self.remote
            if args[-2:] == ["rev-parse", "HEAD"]:
                return self.head
            if args[-3:] == ["status", "--porcelain", "--untracked-files=all"]:
                return self.dirty
            raise AssertionError("Unexpected Git operation")
        result = super().run(args)
        if args[1:3] == ["info", "--json=v2"]:
            data = json.loads(result)
            if "/" in args[-1]:
                data["formulae"][0].update(full_name=args[-1], tap=args[-1].rsplit("/", 1)[0], ruby_source_checksum={"sha256": self.checksum})
            if "casks" in data:
                data["casks"][0]["artifacts"] = [{"app": [self.app]}]
            return json.dumps(data)
        return result


class FullSelectionTests(unittest.TestCase):
    setUp = test_engine.EngineTests.setUp
    observe = test_engine.EngineTests.observe
    compile = test_engine.EngineTests.compile

    def test_existing_trusted_qualified_formula_uses_bound_local_source_not_core_api(self):
        config = configuration({"formulae": {"example/tools/fixture": {}}})
        fake = SelectionReader().formula("example/tools/fixture", present=False)
        observation = self.observe(config, fake)
        compiled = self.compile(config, fake, observation)
        self.assertEqual(compiled["operations"][0]["argv"][-1], "example/tools/fixture")
        self.assertFalse(fake.urls)
        fake.checksum = "c" * 64
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observation)

    def test_missing_untrusted_dirty_or_redirected_tap_never_loads_formula(self):
        config = configuration({"formulae": {"example/tools/fixture": {}}})
        for field, value in (("tap_present", False), ("trusted", False), ("dirty", " M formula.rb"), ("remote", "https://invalid.example/repo")):
            fake = SelectionReader().formula("example/tools/fixture", present=False)
            setattr(fake, field, value)
            with self.subTest(field=field), self.assertRaises(engine.EngineError):
                self.observe(config, fake)
            self.assertFalse(any(call[1:2] == ["info"] for call in fake.calls))
            self.assertFalse(fake.executed)

    def test_tap_revision_change_invalidates_review(self):
        config = configuration({"formulae": {"example/tools/fixture": {}}})
        fake = SelectionReader().formula("example/tools/fixture")
        observation = self.observe(config, fake)
        fake.head = "d" * 40
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observation)

    def external(self):
        applications = Path(self.scratch.name) / "Applications"
        applications.mkdir()
        self.addCleanup(patch.stopall)
        patch.dict(engine.observe.__globals__, {"APPLICATIONS": applications}).start()
        fake = SelectionReader()
        fake.casks["fixture"] = {"present": False, "version": "1.0", "candidate": "2.0", "held": False, "deps": []}
        return applications / "Fixture.app", fake, configuration({"casks": {"fixture": {"accept_external": True}}})

    def test_external_app_setup_and_update_preserve_without_version_or_adoption(self):
        app, fake, config = self.external()
        app.mkdir()
        canary = app / "private-state"
        canary.write_bytes(b"preserve")
        before = app.stat().st_ino
        for operation in ("setup", "update"):
            observed = self.observe(config, fake, operation)
            self.assertEqual(observed["plan"]["actions"][0]["action"], "preserve")
            self.assertNotIn("version", observed["state"]["installed"]["casks"]["fixture"])
            self.assertEqual(self.compile(config, fake, observed, operation)["operations"], [])
        self.assertEqual(app.stat().st_ino, before)
        self.assertEqual(canary.read_bytes(), b"preserve")

    def test_missing_external_app_installs_normally(self):
        app, fake, config = self.external()
        observed = self.observe(config, fake)
        self.assertEqual(self.compile(config, fake, observed)["operations"][0]["argv"][1], "install")
        self.assertFalse(app.exists())

    def test_external_app_link_or_world_writable_fails_closed(self):
        app, fake, config = self.external()
        app.symlink_to(Path(self.scratch.name), target_is_directory=True)
        with self.assertRaises(engine.EngineError):
            self.observe(config, fake)
        app.unlink()
        app.mkdir(mode=0o777)
        app.chmod(0o777)
        with self.assertRaises(engine.EngineError):
            self.observe(config, fake)

    def test_external_app_permission_change_invalidates_review(self):
        app, fake, config = self.external()
        app.mkdir(mode=0o755)
        observed = self.observe(config, fake)
        app.chmod(0o700)
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observed)

    def test_external_artifact_escape_and_hold_conflict_are_rejected(self):
        app, fake, config = self.external()
        fake.app = "../Fixture.app"
        with self.assertRaises(engine.EngineError):
            self.observe(config, fake)
        with self.assertRaises(planner.ConfigError):
            configuration({"casks": {"fixture": {"accept_external": True, "hold": True}}})


if __name__ == "__main__":
    unittest.main(verbosity=2)
