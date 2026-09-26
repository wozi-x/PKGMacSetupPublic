"""Exercise migration with real Zsh, synthetic startup files and no network."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.rc = self.home / ".zshrc"
        self.rc.write_text('alias custom="echo preserved"\nZSH_THEME=custom\nplugins=(git custom)\n')
        framework = self.home / ".oh-my-zsh"
        framework.mkdir()
        (framework / "oh-my-zsh.sh").write_text('function omz() { :; }\nprint loaded >> "$HOME/loads"\n')
        self.env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "TERM": "dumb"}

    def run_migration(self, check=False, code=0):
        result = subprocess.run(["/bin/bash", "-p", str(ROOT / "migrate-ohmyzsh.sh"), str(check).lower()],
                                env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def test_backup_preservation_and_rerun(self):
        original = self.rc.read_bytes()
        self.run_migration()
        self.assertTrue(self.rc.read_bytes().startswith(original))
        backup = list(self.home.glob(".zshrc.pre-base-ohmyzsh.*"))
        self.assertEqual(len(backup), 1)
        self.assertEqual(backup[0].read_bytes(), original)
        migrated = self.rc.read_bytes()
        self.run_migration()
        self.assertEqual(self.rc.read_bytes(), migrated)
        self.assertEqual(list(self.home.glob(".zshrc.pre-base-ohmyzsh.*")), backup)
        self.assertEqual((self.home / "loads").read_text(), "loaded\nloaded\n")

    def test_existing_active_source_is_not_loaded_twice(self):
        with self.rc.open("a") as stream:
            stream.write('export ZSH="$HOME/.oh-my-zsh"\nsource "$ZSH/oh-my-zsh.sh"\n')
        self.run_migration()
        self.assertEqual((self.home / "loads").read_text(), "loaded\n")

    def test_explicit_empty_theme_and_plugins_are_preserved(self):
        self.rc.write_text('ZSH_THEME=""\nplugins=()\n')
        with (self.home / ".oh-my-zsh/oh-my-zsh.sh").open("a") as stream:
            stream.write('print -r -- "$ZSH_THEME|${#plugins}" > "$HOME/choices"\n')
        self.run_migration()
        self.assertEqual((self.home / "choices").read_text(), "|0\n")

    def test_commented_or_inactive_source_gets_fallback(self):
        self.rc.write_text('# source "$ZSH/oh-my-zsh.sh"\nif false; then source "$ZSH/oh-my-zsh.sh"; fi\n')
        self.run_migration()
        self.assertEqual((self.home / "loads").read_text(), "loaded\n")

    def test_check_never_executes_or_writes(self):
        original = self.rc.read_bytes()
        self.run_migration(check=True, code=1)
        self.assertEqual(self.rc.read_bytes(), original)
        self.assertFalse((self.home / "loads").exists())
        self.assertFalse(list(self.home.glob(".zshrc.pre-base-ohmyzsh.*")))
        self.run_migration()
        before = (self.home / "loads").read_bytes()
        self.run_migration(check=True)
        self.assertEqual((self.home / "loads").read_bytes(), before)

    def test_early_return_and_exit_roll_back(self):
        for statement in ("return", "exit 0"):
            with self.subTest(statement=statement):
                original = (statement + "\n").encode()
                self.rc.write_bytes(original)
                self.assertIn("original .zshrc restored", self.run_migration(code=1))
                self.assertEqual(self.rc.read_bytes(), original)

    def test_conflict_syntax_and_symlink_preserved(self):
        for content in ('source ~/.zprezto/init.zsh\n', 'if then broken\n'):
            self.rc.write_text(content)
            self.run_migration(code=1)
            self.assertEqual(self.rc.read_text(), content)
            self.assertFalse(list(self.home.glob(".zshrc.pre-base-ohmyzsh.*")))
        self.rc.unlink()
        target = self.home / "elsewhere"
        target.write_text("# preserve\n")
        self.rc.symlink_to(target)
        self.run_migration(code=1)
        self.assertTrue(self.rc.is_symlink())
        self.assertEqual(target.read_text(), "# preserve\n")

    def test_startup_edit_is_not_overwritten_on_failure(self):
        self.rc.write_text('print "# edited by startup" > "$HOME/.zshrc"\nreturn\n')
        self.assertIn("preserved the newer file", self.run_migration(code=1))
        self.assertEqual(self.rc.read_text(), "# edited by startup\n")

    def test_timeout_restores_original(self):
        original = 'while true; do :; done\n'
        self.rc.write_text(original)
        self.assertIn("original .zshrc restored", self.run_migration(code=1))
        self.assertEqual(self.rc.read_text(), original)


if __name__ == "__main__":
    unittest.main()
