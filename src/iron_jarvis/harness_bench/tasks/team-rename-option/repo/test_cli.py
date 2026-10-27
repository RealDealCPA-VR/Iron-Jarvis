import contextlib
import io
import json
import os
import tempfile
import unittest

from syncer.cli import main


def run(argv, environ=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv, environ={} if environ is None else environ)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def test_config_defaults(self):
        code, out, _ = run(["config"])
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "remote: (none)")
        self.assertIn("workers: 2", out)

    def test_plan(self):
        code, out, _ = run(["plan", "b.txt", "a.txt", "--workers", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), ["worker 1: a.txt (4 tries)", "worker 1: b.txt (4 tries)"])

    def test_the_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"workers": 3}, fh)
            code, out, _ = run(["plan", "a", "b", "c", "d", "--config", path])
        self.assertEqual(code, 0)
        self.assertEqual([line.split(":")[0] for line in out.splitlines()],
                         ["worker 1", "worker 2", "worker 3", "worker 1"])

    def test_bad_settings(self):
        code, _, err = run(["config"], environ={"SYNCER_WORKERS": "many"})
        self.assertEqual(code, 1)
        self.assertIn("error:", err)


if __name__ == "__main__":
    unittest.main()
