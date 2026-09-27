"""Shared macOS defaults use fake providers; never change the testing Mac."""
import json
import unittest

import test_base_setup


class MacOSPreferencesTests(unittest.TestCase):
    setUp = test_base_setup.BaseSetupTests.setUp
    run_base = test_base_setup.BaseSetupTests.run_base
    calls = test_base_setup.BaseSetupTests.calls
    selection = test_base_setup.BaseSetupTests.selection

    def seed(self, **values):
        state = json.loads(self.state.read_text())
        state.update(values)
        self.state.write_text(json.dumps(state))

    def test_shared_scalar_defaults_and_numeric_rerun(self):
        args = self.selection(prefs="general=true\nscreenshots=true\ndock=true\napplications=true\n")
        self.run_base(*args)
        state = json.loads(self.state.read_text())
        expected = {
            "NSGlobalDomain NSNavPanelExpandedStateForSaveMode": "1",
            "NSGlobalDomain PMPrintingExpandedStateForPrint": "1",
            "NSGlobalDomain NSDocumentSaveNewDocumentsToCloud": "0",
            "com.apple.screencapture location": str(self.home / "Downloads"),
            "com.apple.screencapture type": "png",
            "com.apple.screencapture disable-shadow": "1",
            "com.apple.dock expose-animation-duration": "0.15",
            "com.apple.dock showhidden": "1",
            "com.apple.universalaccess reduceTransparency": "1",
            "com.apple.dock wvous-br-corner": "2",
            "com.apple.dock wvous-tr-corner": "10",
            "com.apple.dock wvous-bl-corner": "4",
            "com.apple.appstore InAppReviewEnabled": "0",
            "com.apple.ActivityMonitor OpenMainWindow": "1",
            "com.apple.ActivityMonitor ShowCategory": "0",
        }
        self.assertEqual({key: state["preferences"][key] for key in expected}, expected)
        state["preferences"]["com.apple.dock tilesize"] = "48.000000"
        state["preferences"]["com.apple.dock expose-animation-duration"] = "0.150000"
        self.state.write_text(json.dumps(state))
        before = len(self.calls())
        self.run_base(*args)
        self.run_base(*args, "--check")
        self.assertFalse(any(c[0] == "defaults" and c[1][0] == "write" for c in self.calls()[before:]))

    def test_unreadable_dock_layout_preserves_icons(self):
        layout = {"persistent-apps": ["existing app"], "persistent-others": ["existing folder"]}
        self.seed(domains={"com.apple.dock": layout}, export_failures=["com.apple.dock"])
        args = self.selection(prefs="dock=true\ndock_layout=true\n")
        output = self.run_base(*args, code=3)
        self.assertIn("existing icons were preserved", output)
        self.assertEqual(json.loads(self.state.read_text())["domains"]["com.apple.dock"], layout)
        self.assertFalse(any(c[0] == "defaults" and "-array" in c[1] for c in self.calls()))
        original = self.state.read_bytes()
        self.run_base(*args, "--check", code=1)
        self.assertEqual(self.state.read_bytes(), original)

    def test_finder_views_preserve_other_keys_and_unhide_library(self):
        (self.home / "Library").mkdir()
        self.seed(library_hidden=True, finder_views={
            "Unmanaged": "keep",
            "DesktopViewSettings": {"IconViewSettings": {"textSize": 14, "arrangeBy": "none", "iconSize": 32}},
        })
        args = self.selection(prefs="finder=true\n")
        original = self.state.read_bytes()
        output = self.run_base(*args, "--check", code=1)
        self.assertIn("Show the Library folder", output)
        self.assertEqual(self.state.read_bytes(), original)
        self.run_base(*args)
        state = json.loads(self.state.read_text())
        self.assertFalse(state["library_hidden"])
        self.assertEqual(state["finder_views"]["Unmanaged"], "keep")
        self.assertEqual(state["finder_views"]["DesktopViewSettings"]["IconViewSettings"]["textSize"], 14)
        for section in ("DesktopViewSettings", "FK_StandardViewSettings", "StandardViewSettings"):
            self.assertEqual(state["finder_views"][section]["IconViewSettings"]["arrangeBy"], "grid")
            self.assertEqual(state["finder_views"][section]["IconViewSettings"]["iconSize"], 64)
        before = len(self.calls())
        self.run_base(*args)
        self.run_base(*args, "--check")
        self.assertFalse(any(c[0] == "PlistBuddy" and not c[1][1].startswith("Print ") for c in self.calls()[before:]))

    def test_dock_layout_is_separate_opt_in_and_check_only_reports(self):
        layout = {"persistent-apps": [{"tile-data": {"file-label": "Keep until selected"}}],
                  "persistent-others": [{"tile-data": {"file-label": "Downloads"}}], "unmanaged": "keep"}
        self.seed(domains={"com.apple.dock": layout})
        for prefs in ("dock=true\n", "dock=false\ndock_layout=true\n", "dock=true\ndock_layout=false\n"):
            self.run_base(*self.selection(prefs=prefs))
            self.assertEqual(json.loads(self.state.read_text())["domains"]["com.apple.dock"], layout)
        args = self.selection(prefs="dock=true\ndock_layout=true\n")
        original = self.state.read_bytes()
        self.assertIn("Clear Dock icons", self.run_base(*args, "--check", code=1))
        self.assertEqual(self.state.read_bytes(), original)
        self.run_base(*args)
        self.assertEqual(json.loads(self.state.read_text())["domains"]["com.apple.dock"], {
            "persistent-apps": [], "persistent-others": [], "unmanaged": "keep",
        })
        self.run_base(*args, "--check")

    def test_pinyin_preserves_input_sources_and_handles_byhost(self):
        other = '{ InputSourceKind = "Keyboard Layout"; "KeyboardLayout Name" = "Custom"; }'
        self.seed(input_sources={"AppleEnabledInputSources": [other], "AppleSelectedInputSources": [other]})
        args = self.selection(prefs="input=true\n")
        original = self.state.read_bytes()
        self.assertIn("Pinyin:", self.run_base(*args, "--check", code=1))
        self.assertEqual(self.state.read_bytes(), original)
        self.run_base(*args)
        state = json.loads(self.state.read_text())
        for sources in state["input_sources"].values():
            self.assertEqual(sources[0], other)
            self.assertEqual(len(sources), 3)
        before = len(self.calls())
        self.run_base(*args)
        self.run_base(*args, "--check")
        self.assertFalse(any(c[0] == "defaults" and "write" in c[1] for c in self.calls()[before:]))
        self.seed(input_requires_byhost=True)
        self.run_base(*args)
        self.run_base(*args, "--check")
        sources = json.loads(self.state.read_text())["input_sources"]
        self.assertEqual(len(sources["host:AppleEnabledInputSources"]), 2)
        self.assertEqual(len(sources["host:AppleSelectedInputSources"]), 2)

    def test_messages_changes_only_selected_dictionary_keys(self):
        self.seed(domains={"com.apple.messageshelper.MessageController": {
            "OtherKey": "keep", "SOInputLineSettings": {"unmanaged": True, "automaticQuoteSubstitutionEnabled": True},
        }})
        self.run_base(*self.selection(prefs="applications=true\n"))
        self.assertEqual(json.loads(self.state.read_text())["domains"]["com.apple.messageshelper.MessageController"], {
            "OtherKey": "keep", "SOInputLineSettings": {"unmanaged": True,
            "automaticQuoteSubstitutionEnabled": False, "continuousSpellCheckingEnabled": False},
        })

    def test_extended_failures_continue_and_report_incomplete(self):
        self.seed(finder_view_failure=True, input_failure="persist", domains={"com.apple.dock": {
            "persistent-apps": ["keep on failure"],
        }}, preference_failures={
            "com.apple.dock persistent-apps": "persist",
            "com.apple.universalaccess reduceTransparency": "write",
            "com.apple.messageshelper.MessageController SOInputLineSettings": "persist",
        })
        output = self.run_base(*self.selection(prefs="finder=true\ninput=true\ndock=true\ndock_layout=true\napplications=true\n"), code=3)
        for message in ("Base needs attention", "icon view write failed", "Pinyin input source did not persist",
                        "Dock icon removal did not persist", "Messages preference did not persist",
                        "Accessibility > Display > Reduce transparency"):
            self.assertIn(message, output)
        self.assertTrue((self.home / ".zshrc").is_file())

    def test_restart_freeze_never_requests_ssh_or_prompt_in_check(self):
        args = self.selection(prefs="restart_on_freeze=true\n")
        original = self.state.read_bytes()
        self.run_base(*args, "--check", code=1)
        self.assertEqual(self.state.read_bytes(), original)
        sudo = [c[1] for c in self.calls() if c[0] == "sudo"]
        self.assertEqual(sudo, [["-n", "/usr/sbin/systemsetup", "-getrestartfreeze"]])
        self.run_base(*args)
        self.run_base(*args, "--check")
        self.assertTrue(json.loads(self.state.read_text())["restartfreeze"])
        self.seed(systemsetup_failure=True)
        self.assertIn("restart after freeze could not be enabled or verified", self.run_base(*args, code=3))

    def test_new_groups_false_or_omitted_do_not_call_preference_providers(self):
        for prefs in ("", "general=false\nscreenshots=false\ninput=false\ndock_layout=false\napplications=false\nrestart_on_freeze=false\n"):
            args = self.selection(prefs=prefs)
            self.run_base(*args)
            self.run_base(*args, "--check")
        self.assertFalse(any(c[0] in ("defaults", "sudo", "PlistBuddy", "chflags") for c in self.calls()))

    def test_unsafe_nested_preference_destinations_fail_before_mutation(self):
        prefs = self.home / "Library/Preferences"
        prefs.mkdir(parents=True)
        for name, group in (("com.apple.finder.plist", "finder"), ("com.apple.dock.plist", "dock"),
                            ("com.apple.HIToolbox.plist", "input")):
            target = prefs / name
            target.symlink_to(self.state)
            before = len(self.calls())
            self.run_base(*self.selection(prefs=group + "=true\n"), code=2)
            self.assertTrue(target.is_symlink())
            self.assertFalse(any(c[0] in ("brew", "defaults", "sudo", "PlistBuddy") for c in self.calls()[before:]))
            target.unlink()


if __name__ == "__main__":
    unittest.main()
