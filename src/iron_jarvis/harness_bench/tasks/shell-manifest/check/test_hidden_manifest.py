# IJ-BENCH-HIDDEN-CHECK shell-manifest
import hashlib
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HEADER = "# manifest v2 - written by tools/make_manifest.py"


def _expected():
    rel_paths = []
    data = os.path.join(HERE, "data")
    for folder, _dirs, files in os.walk(data):
        for name in files:
            full = os.path.join(folder, name)
            rel_paths.append(os.path.relpath(full, HERE).replace(os.sep, "/"))
    lines = [HEADER]
    for rel in sorted(rel_paths):
        with open(os.path.join(HERE, rel), "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        lines.append(f"{digest}  {rel}")
    return "\n".join(lines) + "\n"


class HiddenManifest(unittest.TestCase):
    def test_manifest_is_current(self):
        with open(os.path.join(HERE, "MANIFEST.txt"), "rb") as fh:
            got = fh.read().decode("utf-8").replace("\r\n", "\n")
        self.assertEqual(got, _expected())

    def test_new_file_is_listed(self):
        with open(os.path.join(HERE, "MANIFEST.txt"), encoding="utf-8") as fh:
            self.assertIn("  data/c.txt", fh.read())
