"""Fixed Homebrew npm layouts, with no provider execution or host discovery."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_engine
from test_engine import FakeProviders, configuration, engine


class LayoutReader(FakeProviders):
    def __init__(self, layouts):
        super().__init__()
        self.layouts = layouts
        self.formula("node", version="26.8.2", candidate="26.8.2")
        self.npm_available["fixture"] = "1.0.0"

    def has_file(self, path, root):
        return path.removeprefix(root) in self.layouts

    def run(self, argv):
        return super().run([str(item).replace("/libexec/lib/node_modules/", "/lib/node_modules/") for item in argv])


class NpmLayoutTests(unittest.TestCase):
    setUp = test_engine.EngineTests.setUp
    observe = test_engine.EngineTests.observe
    compile = test_engine.EngineTests.compile

    def config(self):
        return configuration({"formulae": {"node": {}}, "npm": {"fixture": {"version": "1.0.0"}}})

    def test_both_supported_layouts_are_bound_without_path_lookup(self):
        for suffix in ("/lib/node_modules/npm/bin/npm-cli.js", "/libexec/lib/node_modules/npm/bin/npm-cli.js"):
            fake = LayoutReader([suffix])
            observed = self.observe(self.config(), fake)
            self.assertEqual(observed["bindings"]["npm"]["npm"], engine.PREFIX + "/opt/node" + suffix)
            self.assertEqual(self.compile(self.config(), fake, observed)["operations"][0]["argv"][1], engine.PREFIX + "/opt/node" + suffix)

    def test_missing_or_ambiguous_layout_fails(self):
        for layouts in ([], ["/lib/node_modules/npm/bin/npm-cli.js", "/libexec/lib/node_modules/npm/bin/npm-cli.js"]):
            with self.subTest(layouts=layouts), self.assertRaises(engine.EngineError):
                self.observe(self.config(), LayoutReader(layouts))

    def test_layout_drift_requires_new_review(self):
        fake = LayoutReader(["/lib/node_modules/npm/bin/npm-cli.js"])
        observed = self.observe(self.config(), fake)
        fake.layouts = ["/libexec/lib/node_modules/npm/bin/npm-cli.js"]
        with self.assertRaises(engine.EngineError):
            self.compile(self.config(), fake, observed)

    def test_absent_owned_prefix_is_empty_without_invoking_npm_or_creating_directories(self):
        fake = LayoutReader(["/lib/node_modules/npm/bin/npm-cli.js"])
        with patch.object(fake, "has_directory", return_value=False):
            observed = self.observe(self.config(), fake)
            compiled = self.compile(self.config(), fake, observed)
        self.assertFalse(any("list" in call for call in fake.calls))
        self.assertFalse(observed["state"]["installed"]["npm"]["fixture"]["present"])
        public = engine.observe.__globals__["PUBLIC"]
        self.assertFalse(public.exists())
        self.assertEqual(compiled["operations"][0]["directory"], str(public / "npm/node"))

    def test_existing_prefix_listing_failure_is_not_treated_as_empty(self):
        fake = LayoutReader(["/lib/node_modules/npm/bin/npm-cli.js"])
        original = fake.run
        def fail_list(argv):
            if "list" in argv:
                raise engine.EngineError("synthetic existing-prefix failure")
            return original(argv)
        with patch.object(fake, "run", side_effect=fail_list), self.assertRaises(engine.EngineError):
            self.observe(self.config(), fake)

    def test_directory_probe_rejects_link_and_file_but_accepts_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prefix"
            reader = engine.ProductionReader()
            self.assertFalse(reader.has_directory(str(path)))
            path.symlink_to(Path(directory) / "missing")
            with self.assertRaises(engine.EngineError):
                reader.has_directory(str(path))
            path.unlink()
            path.write_text("preserve")
            with self.assertRaises(engine.EngineError):
                reader.has_directory(str(path))

    def test_reserved_npm_config_must_remain_absent_without_reading_existing_values(self):
        fake = LayoutReader(["/lib/node_modules/npm/bin/npm-cli.js"])
        observed = self.observe(self.config(), fake)
        compiled = self.compile(self.config(), fake, observed)
        environment = compiled["operations"][0]["environment"]
        self.assertEqual(environment["npm_config_userconfig"], "/dev/null")
        self.assertNotEqual(environment["npm_config_globalconfig"], "/dev/null")
        public = engine.observe.__globals__["PUBLIC"]
        public.mkdir()
        reserved = public / ".npm-empty-global-config"
        reserved.write_bytes(b"opaque owner config must not be loaded")
        with self.assertRaises(engine.EngineError):
            self.compile(self.config(), fake, observed)
        reserved.unlink()
        reserved.symlink_to(public / "missing")
        with self.assertRaises(engine.EngineError):
            self.observe(self.config(), fake)

    def test_actual_file_boundary_allows_only_regular_files_resolving_inside_formula(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "formula"
            root.mkdir()
            script = root / "npm-cli.js"
            script.write_text("// inert\n")
            link = root / "npm-link.js"
            link.symlink_to(script)
            reader = engine.ProductionReader()
            self.assertTrue(reader.has_file(str(script), str(root)))
            self.assertTrue(reader.has_file(str(link), str(root)))
            self.assertFalse(reader.has_file(str(root / "missing"), str(root)))
            outside = Path(directory) / "outside.js"
            outside.write_text("// preserve\n")
            link.unlink()
            link.symlink_to(outside)
            with self.assertRaises(engine.EngineError):
                reader.has_file(str(link), str(root))
            with self.assertRaises(engine.EngineError):
                reader.has_file(str(root), str(root))


if __name__ == "__main__":
    unittest.main(verbosity=2)
