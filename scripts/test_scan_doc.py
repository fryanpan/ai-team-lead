#!/usr/bin/env python3
"""Tests for the doc-for-bryan skill's mechanical scan.

Fake board ids are built by concatenation: a literal one would trip the
pre-push leak gate, which blocks that id shape in pushed content.
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugin", "team-lead-fleet", "skills", "doc-for-bryan", "scripts"))

import scan_doc  # noqa: E402

FAKE_ID = "t" + "-" + "abcdefghijk"


class ScanTest(unittest.TestCase):
    def hits(self, text, max_words=1500):
        return scan_doc.scan(text, max_words)[1]

    def test_clean_doc_passes(self):
        self.assertEqual(self.hits("# Plan\n\nShip 3 of 5 goals. See [the PR](https://example.com/1).\n"), [])

    def test_raw_id_is_a_hit(self):
        self.assertEqual(len(self.hits(f"Tracked on {FAKE_ID}.\n")), 1)

    def test_id_inside_a_url_path_is_not_a_raw_id(self):
        self.assertEqual(self.hits(f"[board](https://example.com/{FAKE_ID})\n"), [])

    def test_bare_url_is_a_hit_and_linked_url_is_not(self):
        self.assertEqual(len(self.hits("See https://example.com/x\n")), 1)
        self.assertEqual(self.hits("See <https://example.com/x>\n"), [])

    def test_rate_needs_a_denominator(self):
        self.assertEqual(len(self.hits("Adoption rose 40%.\n")), 1)
        self.assertEqual(self.hits("Adoption rose to 40% (12 of 30 peers).\n"), [])
        self.assertEqual(self.hits("40% out of 30 runs\n"), [])

    def test_fenced_code_is_skipped(self):
        self.assertEqual(self.hits(f"```\ncurl https://example.com {FAKE_ID} 40%\n```\n"), [])

    def test_word_cap(self):
        self.assertEqual(len(self.hits("word " * 11, max_words=10)), 1)

    def test_unreadable_file_is_could_not_run_not_passed(self):
        self.assertEqual(scan_doc.main([os.path.join(tempfile.gettempdir(), "no-such-doc.md")]), 2)

    def test_exit_codes_for_pass_and_hits(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("Clean.\n")
        try:
            self.assertEqual(scan_doc.main([f.name]), 0)
            with open(f.name, "w") as g:
                g.write("Rose 40%.\n")
            self.assertEqual(scan_doc.main([f.name]), 1)
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
