"""Presence-only Brew setup still validates local identity, pins and dependencies."""
import unittest

import test_engine
from test_engine import FakeProviders, configuration, engine
from test_core_alias import AliasReader
from test_full_selection import SelectionReader


class NoBrewMetadata(FakeProviders):
    def get_json(self, url):
        if url.startswith("https://formulae.brew.sh/"):
            raise AssertionError("Installed setup must not request unused Brew candidate metadata")
        return super().get_json(url)


class PresentSetupTests(unittest.TestCase):
    setUp = test_engine.EngineTests.setUp
    observe = test_engine.EngineTests.observe
    compile = test_engine.EngineTests.compile
    cycle = test_engine.EngineTests.cycle

    def test_installed_formula_and_cask_preserved_without_remote_candidate(self):
        fake = NoBrewMetadata().formula("node", version="26.8.2", candidate="26.9.0")
        fake.casks["fixture"] = {"present": True, "version": "1.0", "candidate": "9.0", "held": False, "deps": []}
        config = configuration({"formulae": {"node": {}}, "casks": {"fixture": {}}})
        observed = self.observe(config, fake)
        self.assertEqual(observed["plan"]["status"], "planned")
        self.assertEqual(self.compile(config, fake, observed)["operations"], [])
        self.assertEqual(fake.urls, [])
        self.assertEqual(observed["state"]["installed"]["formulae"]["node"]["version"], "26.8.2")

    def test_requested_hold_still_pins_and_repeat_is_noop_without_remote_metadata(self):
        fake = NoBrewMetadata().formula("fixture")
        fake.casks["fixture-app"] = {"present": True, "version": "1.0", "candidate": "9.0", "held": False, "deps": []}
        config = configuration({"formulae": {"fixture": {"hold": True}}, "casks": {"fixture-app": {"hold": True}}})
        self.assertEqual([item["argv"][1] for item in self.cycle(config, fake)["operations"]], ["pin", "pin"])
        self.assertEqual(self.cycle(config, fake)["operations"], [])

    def test_unknown_pin_evidence_blocks_present_packages(self):
        for provider in ("formulae", "casks"):
            fake = NoBrewMetadata()
            getattr(fake, provider)["fixture"] = {"present": True, "version": "1.0", "candidate": "9.0", "held": None, "deps": []}
            with self.subTest(provider=provider), self.assertRaises(engine.EngineError):
                self.observe(configuration({provider: {"fixture": {}}}), fake)

    def test_installed_alias_evidence_and_duplicate_canonical_identity_remain_required(self):
        config = configuration({"formulae": {"old-tool": {}}})
        for field, value in (("oldnames", []), ("tap", "example/tap"), ("name", "../escape")):
            fake = AliasReader()
            fake.formulae["old-tool"]["present"] = True
            fake.record[field] = value
            with self.subTest(field=field), self.assertRaises(engine.EngineError):
                self.observe(config, fake)
            self.assertEqual(fake.urls, [])
        fake = AliasReader()
        for item in fake.formulae.values():
            item["present"] = True
        config = configuration({"formulae": {"old-tool": {}, "new.tool": {}}})
        observed = self.observe(config, fake)
        self.assertEqual(observed["plan"]["status"], "blocked")
        self.assertEqual(fake.urls, [])
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observed)

    def test_installed_qualified_formula_still_requires_trust_and_unchanged_source(self):
        config = configuration({"formulae": {"example/tools/fixture": {}}})
        fake = SelectionReader().formula("example/tools/fixture", present=True)
        observed = self.observe(config, fake)
        fake.checksum = "c" * 64
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observed)
        fake.trusted = False
        with self.assertRaises(engine.EngineError):
            self.observe(config, fake)

    def test_missing_setup_and_installed_update_keep_strict_candidate_consistency(self):
        for provider in ("formulae", "casks"):
            for operation, present in (("setup", False), ("update", True)):
                fake = FakeProviders()
                getattr(fake, provider)["fixture"] = {"present": present, "version": "1.0", "candidate": "2.0", "held": False, "deps": []}
                original = fake.get_json
                def stale(url):
                    result = original(url)
                    if provider == "formulae":
                        result["versions"]["stable"] = "3.0"
                    else:
                        result["version"] = "3.0"
                    return result
                fake.get_json = stale
                with self.subTest(provider=provider, operation=operation), self.assertRaises(engine.EngineError):
                    self.observe(configuration({provider: {"fixture": {}}}), fake, operation)
                self.assertTrue(fake.urls)
                self.assertEqual(fake.executed, [])

    def test_other_missing_install_still_protects_present_held_dependency(self):
        fake = FakeProviders().formula("held-runtime", version="1.0", candidate="9.0", held=True)
        fake.formula("new-tool", present=False, deps=["shared-lib"])
        fake.formula("shared-lib", version="1.0", candidate="2.0")
        fake.dependents["shared-lib"] = ["held-runtime"]
        config = configuration({"formulae": {"held-runtime": {}, "new-tool": {}}})
        observed = self.observe(config, fake)
        self.assertEqual(observed["plan"]["status"], "blocked")
        self.assertFalse(observed["bindings"]["protected_dependency_checks"]["shared-lib"]["unchanged"])
        self.assertEqual(fake.urls, ["https://formulae.brew.sh/api/formula/new-tool.json"])
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observed)

    def test_npm_binds_actual_installed_node_without_brew_candidate_metadata(self):
        fake = NoBrewMetadata().formula("node", version="26.8.2", candidate="26.9.0")
        fake.npm_available["fixture-cli"] = "1.0.0"
        config = configuration({"formulae": {"node": {}}, "npm": {"fixture-cli": {"version": "1.0.0"}}})
        observed = self.observe(config, fake)
        self.assertEqual(observed["bindings"]["npm"]["version"], "26.8.2")
        self.assertIn([engine.PREFIX + "/opt/node/bin/node", "--version"], fake.calls)
        operations = self.compile(config, fake, observed)["operations"]
        npm = next(item for item in operations if item["provider"] == "npm")
        self.assertEqual(npm["argv"][0], engine.PREFIX + "/opt/node/bin/node")
        self.assertFalse(any(item["provider"] == "formulae" for item in operations))


if __name__ == "__main__":
    unittest.main(verbosity=2)
