# IJ-BENCH-HIDDEN-CHECK team-rename-option
"""Hidden check: every rule of RENAME.md. A "process" is simulated by importing
syncer afresh (every syncer module dropped from sys.modules first), so each
alias test starts with nothing warned."""
import contextlib
import dataclasses
import importlib
import inspect
import io
import json
import os
import re
import sys
import tempfile
import unittest
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OLD_WORDS = re.compile(r"max[ _-]retries", re.IGNORECASE)
#: The test methods each original test file had: the task says to keep every test.
ORIGINAL_TESTS = {
    "test_cli.py": ["test_config_defaults", "test_plan", "test_the_config_file", "test_bad_settings"],
    "test_config.py": ["test_defaults", "test_file", "test_environment_wins_over_the_file", "test_unknown_key",
                       "test_wrong_type"],
    "test_remote.py": ["test_put_and_get", "test_the_default_setting", "test_tries_again", "test_gives_up"],
    "test_rename.py": ["test_the_keyword", "test_the_attribute", "test_the_flag_and_the_printed_line"],
    "test_report.py": ["test_all_synced", "test_names_the_failures"],
    "test_retry.py": ["test_succeeds_after_failures", "test_gives_up", "test_zero_means_one_try",
                      "test_waits_between_tries"],
    "test_scheduler.py": ["test_round_robin", "test_the_default_tries", "test_bad_workers"],
    "test_transfer.py": ["test_upload_in_name_order", "test_download", "test_own_setting_wins_over_the_remotes",
                         "test_the_remotes_setting_by_default"],
}


def _project_module(name, module):
    """syncer, or any other non-test module of the project (a helper module
    the work may have added beside the package keeps its state there too)."""
    if name == "syncer" or name.startswith("syncer."):
        return True
    path = getattr(module, "__file__", None)
    if not path:
        return False
    here = os.path.normcase(os.path.realpath(HERE))
    path = os.path.normcase(os.path.realpath(path))
    return path.startswith(here + os.sep) and not os.path.basename(path).startswith("test_")


def fresh():
    """syncer imported as a new process would import it."""
    for name, module in list(sys.modules.items()):
        if _project_module(name, module):
            del sys.modules[name]
    return importlib.import_module("syncer")


@contextlib.contextmanager
def recording():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        yield caught


def deprecations(caught):
    return [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]


class Flaky:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise sys.modules["syncer"].TransientError("again")
        return "done"


class Transport:
    def __init__(self, failures=0):
        self.failures = failures
        self.calls = 0
        self.stored = {}

    def _maybe_fail(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise sys.modules["syncer"].TransientError("dropped")

    def get(self, path):
        self._maybe_fail()
        return self.stored[path]

    def put(self, path, data):
        self._maybe_fail()
        self.stored[path] = data


def write_json(data):
    handle, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


def cli(argv, environ=None):
    main = importlib.import_module("syncer.cli").main
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv, environ={} if environ is None else environ)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class A_NewNames(unittest.TestCase):
    def test_a_keyword_everywhere(self):
        s = fresh()
        with recording() as caught:
            fn = Flaky(2)
            self.assertEqual(s.with_retries(fn, retry_limit=2), "done")
            fn = Flaky(3)
            with self.assertRaises(s.TransientError):
                s.with_retries(fn, retry_limit=2)
            self.assertEqual(fn.calls, 3)
            fn = Flaky(1)
            with self.assertRaises(s.TransientError):
                s.with_retries(fn, retry_limit=0)
            self.assertEqual(fn.calls, 1)
            fn = Flaky(3)
            self.assertEqual(s.with_retries(fn), "done")  # the default is still 3
            remote = s.Remote("mem://x", Transport(), retry_limit=4)
            self.assertEqual(remote.retry_limit, 4)
            self.assertEqual(s.Remote("mem://x", Transport()).retry_limit, 3)
            transport = Transport(1)
            s.Remote("mem://x", transport, retry_limit=1).put("a", b"1")
            self.assertEqual(transport.calls, 2)
            with self.assertRaises(s.TransientError):
                s.Remote("mem://x", Transport(2), retry_limit=1).put("a", b"1")
            with self.assertRaises(s.TransientError):
                s.upload(s.Remote("mem://x", Transport(1), retry_limit=5), {"a": b"1"}, retry_limit=0)
            transport = Transport(2)
            transport.stored["a"] = b"1"
            self.assertEqual(s.download(s.Remote("mem://x", transport, retry_limit=0), ["a"], retry_limit=2),
                             {"a": b"1"})
            self.assertEqual(s.upload(s.Remote("mem://x", Transport(2), retry_limit=2), {"a": b"1"}), ["a"])
            self.assertEqual(s.plan_jobs(["a"], retry_limit=4)[0].tries, 5)
            self.assertEqual(s.plan_jobs(["a"])[0].tries, 4)
            text = s.summary({"a": "ok", "b": "failed"}, retry_limit=2)
            self.assertEqual(text.splitlines()[2], "retry limit: 2")
            self.assertIsNone(OLD_WORDS.search(text))
        self.assertEqual(deprecations(caught), [], "a new name warned")

    def test_b_every_function_takes_the_keyword(self):
        s = fresh()
        for fn in (s.with_retries, s.Remote, s.upload, s.download, s.plan_jobs, s.summary):
            with self.subTest(function=getattr(fn, "__name__", fn)):
                self.assertIn("retry_limit", inspect.signature(fn).parameters)

    def test_c_error_messages_say_the_new_name(self):
        s = fresh()
        with self.assertRaises(ValueError) as ctx:
            s.with_retries(Flaky(0), retry_limit=-1)
        self.assertIn("retry_limit", str(ctx.exception))
        self.assertIsNone(OLD_WORDS.search(str(ctx.exception)))

    def test_d_config(self):
        s = fresh()
        names = [f.name for f in dataclasses.fields(s.Config)]
        self.assertIn("retry_limit", names)
        self.assertNotIn("max_retries", names)
        with recording() as caught:
            self.assertEqual(s.load_config(environ={}).retry_limit, 3)
            path = write_json({"retry_limit": 7, "workers": 3})
            try:
                self.assertEqual(s.load_config(path, environ={}).retry_limit, 7)
                self.assertEqual(s.load_config(path, environ={"SYNCER_RETRY_LIMIT": "9"}).retry_limit, 9)
            finally:
                os.remove(path)
            with self.assertRaises(s.ConfigError):
                s.load_config(environ={"SYNCER_RETRY_LIMIT": "lots"})
        self.assertEqual(deprecations(caught), [], "a new name warned")

    def test_e_command_line(self):
        fresh()
        with recording() as caught:
            code, out, _ = cli(["config", "--retry-limit", "6"])
            self.assertEqual(code, 0)
            self.assertIn("retry limit: 6", out.splitlines())
            code, out, _ = cli(["config"], environ={"SYNCER_RETRY_LIMIT": "2"})
            self.assertIn("retry limit: 2", out.splitlines())
            path = write_json({"retry_limit": 5})
            try:
                code, out, _ = cli(["config", "--config", path])
                self.assertIn("retry limit: 5", out.splitlines())
                code, out, _ = cli(["config", "--config", path, "--retry-limit", "1"],
                                   environ={"SYNCER_RETRY_LIMIT": "2"})
                self.assertIn("retry limit: 1", out.splitlines())
            finally:
                os.remove(path)
            code, out, _ = cli(["plan", "b", "a", "--retry-limit", "1", "--workers", "1"])
            self.assertEqual(out.splitlines(), ["worker 1: a (2 tries)", "worker 1: b (2 tries)"])
            code, out, _ = cli(["config"])
            self.assertIn("retry limit: 3", out.splitlines())
            self.assertIsNone(OLD_WORDS.search(out))
        self.assertEqual(deprecations(caught), [], "a new name warned")


class B_AliasesWarnOnce(unittest.TestCase):
    def assert_once(self, messages, *names):
        self.assertEqual(len(messages), 1, f"want exactly one DeprecationWarning, got {messages}")
        for name in names:
            self.assertIn(name, messages[0])

    def test_a_the_keyword(self):
        s = fresh()
        with recording() as caught:
            self.assertEqual(s.with_retries(Flaky(1), max_retries=1), "done")
            fn = Flaky(3)
            with self.assertRaises(s.TransientError):
                s.with_retries(fn, max_retries=1)
            self.assertEqual(fn.calls, 2)
            self.assertEqual(s.Remote("mem://x", Transport(), max_retries=2).retry_limit, 2)
            with self.assertRaises(s.TransientError):
                s.upload(s.Remote("mem://x", Transport(1)), {"a": b"1"}, max_retries=0)
            transport = Transport(1)
            transport.stored["a"] = b"1"
            self.assertEqual(s.download(s.Remote("mem://x", transport, retry_limit=0), ["a"], max_retries=1),
                             {"a": b"1"})
            self.assertEqual(s.plan_jobs(["a"], max_retries=1)[0].tries, 2)
            self.assertIn("retry limit: 4", s.summary({}, max_retries=4).splitlines())
        self.assert_once(deprecations(caught), "max_retries", "retry_limit")

    def test_b_the_attribute(self):
        s = fresh()
        remote = s.Remote("mem://x", Transport(), retry_limit=5)
        config = s.load_config(environ={})
        with recording() as caught:
            self.assertEqual(remote.max_retries, 5)
            self.assertEqual(remote.max_retries, 5)
            self.assertEqual(config.max_retries, 3)
        self.assert_once(deprecations(caught), "max_retries", "retry_limit")

    def test_c_the_config_key(self):
        s = fresh()
        path = write_json({"max_retries": 5, "remote": "r"})
        try:
            with recording() as caught:
                self.assertEqual(s.load_config(path, environ={}).retry_limit, 5)
                self.assertEqual(s.load_config(path, environ={}).retry_limit, 5)
                code, out, _ = cli(["config", "--config", path])
                self.assertIn("retry limit: 5", out.splitlines())
        finally:
            os.remove(path)
        self.assert_once(deprecations(caught), "max_retries", "retry_limit")

    def test_d_the_environment_variable(self):
        s = fresh()
        with recording() as caught:
            self.assertEqual(s.load_config(environ={"SYNCER_MAX_RETRIES": "4"}).retry_limit, 4)
            self.assertEqual(s.load_config(environ={"SYNCER_MAX_RETRIES": "6"}).retry_limit, 6)
            code, out, _ = cli(["config"], environ={"SYNCER_MAX_RETRIES": "2"})
            self.assertIn("retry limit: 2", out.splitlines())
        self.assert_once(deprecations(caught), "SYNCER_MAX_RETRIES", "SYNCER_RETRY_LIMIT")

    def test_e_the_flag(self):
        fresh()
        with recording() as caught:
            for _ in range(2):
                code, out, _ = cli(["config", "--max-retries", "2"])
                self.assertEqual(code, 0)
                self.assertIn("retry limit: 2", out.splitlines())
            code, out, _ = cli(["plan", "a", "--max-retries", "1"])
            self.assertEqual(out.splitlines(), ["worker 1: a (2 tries)"])
        self.assert_once(deprecations(caught), "--max-retries", "--retry-limit")

    def test_f_the_five_names_count_separately(self):
        s = fresh()
        path = write_json({"max_retries": 1})
        try:
            with recording() as caught:
                for _ in range(2):
                    s.with_retries(Flaky(0), max_retries=1)
                    s.Remote("mem://x", Transport()).max_retries
                    s.load_config(path, environ={})
                    s.load_config(environ={"SYNCER_MAX_RETRIES": "1"})
                    cli(["config", "--max-retries", "1"])
        finally:
            os.remove(path)
        messages = deprecations(caught)
        self.assertEqual(len(messages), 5, messages)
        self.assertEqual(sum("SYNCER_MAX_RETRIES" in m for m in messages), 1, messages)
        self.assertEqual(sum("--max-retries" in m for m in messages), 1, messages)


class C_OldAndNewTogether(unittest.TestCase):
    def test_a_keywords(self):
        s = fresh()
        remote = s.Remote("mem://x", Transport())
        calls = [
            lambda: s.with_retries(Flaky(0), max_retries=1, retry_limit=1),
            lambda: s.Remote("mem://x", Transport(), max_retries=1, retry_limit=2),
            lambda: s.upload(remote, {"a": b"1"}, max_retries=0, retry_limit=0),
            lambda: s.download(remote, [], max_retries=0, retry_limit=1),
            lambda: s.plan_jobs(["a"], max_retries=1, retry_limit=1),
            lambda: s.summary({}, max_retries=1, retry_limit=1),
        ]
        for i, call in enumerate(calls):
            with self.subTest(call=i):
                with recording():
                    with self.assertRaises(TypeError):
                        call()

    def test_b_config_and_environment(self):
        s = fresh()
        path = write_json({"max_retries": 1, "retry_limit": 1})
        try:
            with recording():
                with self.assertRaises(s.ConfigError):
                    s.load_config(path, environ={})
                with self.assertRaises(s.ConfigError):
                    s.load_config(environ={"SYNCER_MAX_RETRIES": "1", "SYNCER_RETRY_LIMIT": "1"})
        finally:
            os.remove(path)

    def test_c_flags(self):
        fresh()
        with recording():
            code, _, _ = cli(["config", "--max-retries", "1", "--retry-limit", "1"])
        self.assertEqual(code, 2)


def _read(rel):
    with open(os.path.join(HERE, rel), encoding="utf-8") as fh:
        return fh.read()


def _sections(text):
    """[(heading or "", level, lines)] of a Markdown text."""
    out = [("", 0, [])]
    for line in text.splitlines():
        m = re.match(r"(#+)\s", line)
        if m:
            out.append((line, len(m.group(1)), []))
        else:
            out[-1][2].append(line)
    return out


class D_Docs(unittest.TestCase):
    def test_a_no_old_names_outside_the_deprecated_section(self):
        files = ["README.md", "docs/cli.md", "docs/api.md"]
        files += [os.path.join("docs", f) for f in sorted(os.listdir(os.path.join(HERE, "docs")))
                  if f.endswith(".md") and f not in ("cli.md", "api.md", "configuration.md")]
        files += [os.path.join("examples", f) for f in sorted(os.listdir(os.path.join(HERE, "examples")))]
        for rel in files:
            with self.subTest(file=rel):
                found = OLD_WORDS.search(_read(rel))
                self.assertIsNone(found, f"{rel} still says {found.group(0) if found else ''!r}")

    def test_b_the_new_names_are_documented(self):
        self.assertTrue(re.search(r"retry_limit|--retry-limit", _read("README.md")))
        self.assertIn("--retry-limit", _read("docs/cli.md"))
        self.assertIn("retry_limit", _read("docs/api.md"))
        example = json.loads(_read("examples/syncer.json"))
        self.assertEqual(example.get("retry_limit"), 5)
        self.assertNotIn("max_retries", example)

    def test_c_configuration_md(self):
        sections = _sections(_read("docs/configuration.md"))
        inside, outside = [], []
        deprecated_level = None
        for heading, level, lines in sections:
            if deprecated_level is not None and level and level <= deprecated_level:
                deprecated_level = None
            if deprecated_level is None and re.search(r"deprecat", heading, re.IGNORECASE):
                deprecated_level = level
            target = inside if deprecated_level is not None else outside
            target.append(heading)
            target += lines
        inside_text, outside_text = "\n".join(inside), "\n".join(outside)
        self.assertTrue(inside, "docs/configuration.md has no section whose heading says Deprecated")
        found = OLD_WORDS.search(outside_text)
        self.assertIsNone(found, f"an old name outside the Deprecated section: {found.group(0) if found else ''!r}")
        for name in ("max_retries", "SYNCER_MAX_RETRIES", "--max-retries", "retry_limit", "SYNCER_RETRY_LIMIT",
                     "--retry-limit"):
            self.assertIn(name, inside_text, f"the Deprecated section does not name {name}")
        for name in ("retry_limit", "SYNCER_RETRY_LIMIT", "--retry-limit"):
            self.assertIn(name, outside_text, f"the settings do not name {name}")

    def test_d_changelog(self):
        text = _read("CHANGELOG.md")
        self.assertIn("2.4.0", text)
        entry = text[text.index("2.4.0"):]
        entry = entry[: entry.index("2.3.0")] if "2.3.0" in entry else entry
        self.assertIn("max_retries", entry)
        self.assertIn("retry_limit", entry)


class E_TestsKept(unittest.TestCase):
    def test_every_original_test_is_kept(self):
        for rel, names in ORIGINAL_TESTS.items():
            with self.subTest(file=rel):
                self.assertTrue(os.path.isfile(os.path.join(HERE, rel)), f"{rel} was removed")
                text = _read(rel)
                missing = [n for n in names if not re.search(rf"def {n}\b", text)]
                self.assertEqual(missing, [], f"{rel} lost tests")


if __name__ == "__main__":
    unittest.main()
