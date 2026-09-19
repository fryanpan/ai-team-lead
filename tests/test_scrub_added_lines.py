"""The leak gate scans what a push ADDS, not the whole of every file it touches.

Bryan decided this on 2026-09-19 ("Added lines only"), after the whole-file scan
blocked a 60-commit push on lines that were already on the remote.

These tests build a throwaway git repo in a temp dir, so no protected term ever
enters this repo's history — which is the same hazard the gate exists to stop.
"""

import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRUB = os.path.join(REPO, "scripts", "scrub-check.py")
SECRET = "zzqprojectname"  # stands in for a private project key


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True).stdout


class AddedLinesScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(subprocess.run, ["rm", "-rf", self.tmp])
        git(self.tmp, "init", "-q", "-b", "main")
        git(self.tmp, "config", "user.email", "t@example.com")
        git(self.tmp, "config", "user.name", "T")

        self.deny = os.path.join(self.tmp, "denylist.txt")
        with open(self.deny, "w") as f:
            f.write(SECRET + "\n")

        # A fixture registry, not /dev/null: the gate refuses when a pattern
        # source cannot be read, which is the behaviour we want everywhere
        # except here. It carries no projects, so every term comes from the
        # denylist above and the test knows exactly what it planted.
        self.registry = os.path.join(self.tmp, "registry.yaml")
        with open(self.registry, "w") as f:
            f.write("projects: {}\n")

        # A file that ALREADY carries the term, committed as the baseline. This
        # is the case the old whole-file scan got wrong.
        self.existing = os.path.join(self.tmp, "notes.md")
        with open(self.existing, "w") as f:
            f.write(f"old line naming {SECRET}\n")
        git(self.tmp, "add", "-A")
        git(self.tmp, "commit", "-q", "-m", "baseline")
        self.base = git(self.tmp, "rev-parse", "HEAD").strip()

    def run_scrub(self, rng):
        env = dict(os.environ)
        env["SCRUB_DENYLIST"] = self.deny
        env["SCRUB_REGISTRY"] = self.registry
        p = subprocess.run([sys.executable, SCRUB, "--diff-range", rng],
                           cwd=self.tmp, capture_output=True, text=True, env=env)
        return p.returncode, p.stdout + p.stderr

    def commit(self, text, msg="change"):
        with open(self.existing, "a") as f:
            f.write(text)
        git(self.tmp, "add", "-A")
        git(self.tmp, "commit", "-q", "-m", msg)

    def test_untouched_existing_line_does_not_block(self):
        """Appending a clean line to a file that already carries the term passes."""
        self.commit("a clean new line\n")
        code, out = self.run_scrub(f"{self.base}..HEAD")
        self.assertEqual(code, 0, out)

    def test_an_added_line_carrying_the_term_blocks(self):
        """The gate can still fail. Without this the zero above proves nothing."""
        self.commit(f"newly added line naming {SECRET}\n")
        code, out = self.run_scrub(f"{self.base}..HEAD")
        self.assertEqual(code, 1, out)
        self.assertIn("added by this push", out)

    def test_unreadable_diff_refuses_rather_than_passing(self):
        """Could-not-run is the third state and must not read as clean."""
        code, out = self.run_scrub("no-such-ref..also-missing")
        self.assertEqual(code, 2, out)
        self.assertIn("could not read the diff", out)


if __name__ == "__main__":
    unittest.main()
