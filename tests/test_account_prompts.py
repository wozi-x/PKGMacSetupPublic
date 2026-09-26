"""Account pauses use fake app launching and a disposable terminal."""
import os
from pathlib import Path
import pty
import re
import select
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AccountPromptTests(unittest.TestCase):
    def source(self):
        function = re.search(r"^account_ready\(\) \{.*?^\}", (ROOT / "setup.sh").read_text(), re.M | re.S).group()
        return "set -eu\nnote() { printf '%s\\n' \"$*\"; }\nclean_run() { printf 'OPEN:%s\\n' \"$*\"; }\n" + function + "\naccount_ready 'App Store'\n"

    def test_waits_for_readiness_skip_and_quit(self):
        for answer, expected in ((b"\n", 0), (b"s\n", 1), (b"q\n", 130)):
            with self.subTest(answer=answer):
                master, slave = pty.openpty()
                try:
                    process = subprocess.Popen(["/bin/bash", "-c", self.source()], stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
                    try:
                        self.assertTrue(select.select([process.stdout], [], [], 5)[0])
                        self.assertIn(b"Open App Store", process.stdout.readline())
                        self.assertIsNone(process.poll(), "must wait for human readiness")
                        os.write(master, answer)
                        stdout, stderr = process.communicate(timeout=5)
                        self.assertEqual(process.returncode, expected, stderr)
                        self.assertIn(b"OPEN:/usr/bin/open -a App Store", stdout)
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.communicate()
                finally:
                    os.close(master)
                    os.close(slave)

    def test_noninteractive_does_not_launch_or_prompt(self):
        result = subprocess.run(["/bin/bash", "-c", self.source()], input=b"", capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
