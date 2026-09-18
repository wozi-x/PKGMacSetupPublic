"""Core rename metadata boundaries; injected provider data only."""
import json
import unittest

import test_engine
from test_engine import FakeProviders, configuration, engine


class AliasReader(FakeProviders):
    def __init__(self):
        super().__init__()
        self.formula("old-tool", present=False)
        self.formula("new.tool", present=False)
        self.record = {"name": "new.tool", "full_name": "new.tool", "tap": "homebrew/core", "oldnames": ["old-tool"]}
        self.api_name = "new.tool"

    def run(self, argv):
        result = super().run(argv)
        if argv[1:3] == ["info", "--json=v2"] and argv[-1] == "old-tool":
            value = json.loads(result)
            value["formulae"][0].update(self.record)
            return json.dumps(value)
        return result

    def get_json(self, url):
        result = super().get_json(url)
        result["name"] = self.api_name
        return result


class CoreAliasTests(unittest.TestCase):
    setUp = test_engine.EngineTests.setUp
    observe = test_engine.EngineTests.observe
    compile = test_engine.EngineTests.compile

    def test_verified_old_name_fetches_canonical_core_metadata_and_binds_identity(self):
        config = configuration({"formulae": {"old-tool": {}}})
        fake = AliasReader()
        observed = self.observe(config, fake)
        self.assertEqual(fake.urls, ["https://formulae.brew.sh/api/formula/new.tool.json"])
        self.assertEqual(observed["bindings"]["brew_sources"]["old-tool"]["canonical_core_name"], "new.tool")
        self.assertEqual(self.compile(config, fake, observed)["operations"][0]["argv"][-1], "old-tool")
        fake.record["oldnames"] = []
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observed)

    def test_unattested_or_noncore_or_unsafe_alias_refuses_metadata_request(self):
        config = configuration({"formulae": {"old-tool": {}}})
        for field, value in (("oldnames", []), ("tap", "example/tap"), ("name", "../escape"), ("full_name", "example/tap/new.tool")):
            fake = AliasReader()
            fake.record[field] = value
            with self.subTest(field=field), self.assertRaises(engine.EngineError):
                self.observe(config, fake)
            self.assertEqual(fake.urls, [])

    def test_contradictory_public_metadata_is_rejected(self):
        fake = AliasReader()
        fake.api_name = "another-tool"
        with self.assertRaises(engine.EngineError):
            self.observe(configuration({"formulae": {"old-tool": {}}}), fake)

    def test_selected_old_and_canonical_name_collision_blocks_all_execution(self):
        config = configuration({"formulae": {"old-tool": {}, "new.tool": {}}})
        fake = AliasReader()
        observed = self.observe(config, fake)
        self.assertEqual(observed["plan"]["status"], "blocked")
        with self.assertRaises(engine.EngineError):
            self.compile(config, fake, observed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
