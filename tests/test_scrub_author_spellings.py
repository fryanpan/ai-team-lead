"""The author exception has to cover the first name, not only the full one.

Prose refers to a person by first name. The resolver's four sources all yield
full names, so widening it to more full spellings — which is what it was
widened to do on 2026-09-19 — could not reach the case that blocked a push the
same day: the owner's own first name, standing alone in a sentence.

The repo is built in a temp dir so the real repo's identity is never read.
"""
import importlib.util
import os
import subprocess
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location(
    "sh", os.path.join(os.path.dirname(__file__), "..", "scripts",
                       "scrub-haiku.py"))
sh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sh)


class AuthorSpellings(unittest.TestCase):
    def repo(self, name, email):
        tmp = tempfile.mkdtemp()
        self.addCleanup(subprocess.run, ["rm", "-rf", tmp])
        for args in (["init", "-q", "-b", "main"],
                     ["config", "user.name", name],
                     ["config", "user.email", email]):
            subprocess.run(["git", *args], cwd=tmp, check=True,
                           capture_output=True)
        with open(os.path.join(tmp, "f.txt"), "w") as f:
            f.write("x\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, check=True,
                       capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "c"], cwd=tmp, check=True,
                       capture_output=True)
        return tmp

    def names_in(self, tmp):
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            return sh.repo_author()
        finally:
            os.chdir(cwd)

    def test_first_and_last_name_are_both_spellings_of_the_owner(self):
        names = self.names_in(self.repo("Ada Lovelace", "ada@example.invalid"))
        self.assertIn("Ada Lovelace", names)
        self.assertIn("Ada", names)
        self.assertIn("Lovelace", names)

    def test_a_single_word_name_adds_nothing(self):
        """No split to do, and no chance of a one-token blanket exception."""
        names = self.names_in(self.repo("Selftest", "st@example.invalid"))
        self.assertIn("Selftest", names)
        self.assertNotIn("", names)

    def test_a_short_particle_does_not_become_an_exception(self):
        """`de` alone would excuse far more than the owner's name."""
        names = self.names_in(self.repo("Ada de Lovelace", "a@example.invalid"))
        self.assertIn("Lovelace", names)
        self.assertNotIn("de", names)


if __name__ == "__main__":
    unittest.main()
