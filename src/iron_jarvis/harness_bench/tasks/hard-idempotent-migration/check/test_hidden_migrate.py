# IJ-BENCH-HIDDEN-CHECK hard-idempotent-migration
import copy
import hashlib
import json
import os
import runpy
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "customers")
sys.path.insert(0, HERE)

import migrate  # noqa: E402

MIGRATED = {
    "ada.json": {"first_name": "Ada", "last_name": "Lovelace", "joined": "1815-12-10",
                 "phone": "+15550102000", "tags": ["newsletter", "vip"]},
    "grace.json": {"first_name": "Grace Brewster", "last_name": "Hopper", "joined": "1906-12-09",
                   "phone": "+15550103000", "tags": ["cobol", "navy"], "notes": "prefers email"},
    "alan.json": {"first_name": "Alan Mathison", "last_name": "Turing", "joined": "1912-06-23",
                  "phone": None, "tags": []},
    "cher.json": {"first_name": "", "last_name": "Cher", "joined": "1946-05-20",
                  "phone": None, "tags": ["music"]},
    "zoe.json": {"first_name": "Zoë", "last_name": "Ångström", "joined": "2001-01-01",
                 "phone": "+15550104000", "tags": ["beta"]},
}
REJECTED = ["bad_date.json", "bad_phone.json", "broken.json", "future.json"]

FRESH = {
    "a.json": {"name": "Lee, Ann", "joined": "3/4/2021", "phone": "15550106000", "tags": "x, Y"},
    "b.json": {"version": 1, "name": "Bo", "joined": "29 Feb 2020", "tags": "a,a"},
    "c.json": {"name": "Cy Young", "joined": "2021-02-29"},
    "e.json": {"name": "Dee Dee Ramone", "joined": "13/01/2000", "phone": "5550108000"},
    "f.json": [1, 2],
    "g.json": {"name": "   ", "joined": "2000-01-01"},
    "h.json": {"name": "Eve Online", "joined": "2000-01-01", "phone": "+44 20 7946 0000"},
    "i.json": {"name": "Ivy  League", "joined": "7 MAY 1999", "phone": "(555)010-7000",
               "tags": " ,", "extra": {"k": [1, 2]}},
    "j.json": {"name": "No Date"},
    "k.json": {"name": "Kay", "joined": "1999/05/07"},
}
FRESH_V2 = '{"version": 2, "first_name": "Dot", "last_name": "Matrix", "joined": "1970-01-01", "phone": null, "tags": []}'
FRESH_MIGRATED = {
    "a.json": {"first_name": "Ann", "last_name": "Lee", "joined": "2021-03-04",
               "phone": "+15550106000", "tags": ["x", "y"]},
    "b.json": {"first_name": "", "last_name": "Bo", "joined": "2020-02-29",
               "phone": None, "tags": ["a"]},
    "i.json": {"first_name": "Ivy", "last_name": "League", "joined": "1999-05-07",
               "phone": "+15550107000", "tags": [], "extra": {"k": [1, 2]}},
}
FRESH_REJECTED = ["c.json", "e.json", "f.json", "g.json", "h.json", "j.json", "k.json"]


def _v2(fields):
    out = dict(fields)
    out["version"] = 2
    return out


def _canonical(record):
    return (json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _bytes(path):
    with open(path, "rb") as fh:
        return fh.read().replace(b"\r\n", b"\n")


def _snapshot(folder):
    return {name: _bytes(os.path.join(folder, name)) for name in sorted(os.listdir(folder))}


def _fresh_folder():
    folder = tempfile.mkdtemp(prefix="ij-mig-")
    for name, record in FRESH.items():
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(json.dumps(record).encode("utf-8"))
    with open(os.path.join(folder, "d.json"), "wb") as fh:
        fh.write(FRESH_V2.encode("utf-8"))
    with open(os.path.join(folder, "notes.md"), "wb") as fh:
        fh.write(b"not a record\n")
    return folder


class HiddenMigration(unittest.TestCase):
    def test_the_projects_records_were_migrated(self):
        for name, fields in MIGRATED.items():
            with self.subTest(name=name):
                self.assertEqual(_bytes(os.path.join(DATA, name)), _canonical(_v2(fields)))

    def test_the_rest_was_left_alone(self):
        for name in ORIGINAL:
            with self.subTest(name=name):
                self.assertEqual(
                    hashlib.sha256(_bytes(os.path.join(DATA, name))).hexdigest(),
                    ORIGINAL[name],
                )
        self.assertEqual(_bytes(os.path.join(DATA, "REJECTED.txt")),
                         "".join(n + "\n" for n in REJECTED).encode("utf-8"))

    def test_a_fresh_folder_twice(self):
        folder = _fresh_folder()
        try:
            v2_before = _bytes(os.path.join(folder, "d.json"))
            rejected_before = {n: _bytes(os.path.join(folder, n)) for n in FRESH_REJECTED}
            self.assertEqual(migrate.migrate_folder(folder), FRESH_REJECTED)
            for name, fields in FRESH_MIGRATED.items():
                with self.subTest(name=name):
                    self.assertEqual(_bytes(os.path.join(folder, name)), _canonical(_v2(fields)))
            self.assertEqual(_bytes(os.path.join(folder, "d.json")), v2_before)
            for name, raw in rejected_before.items():
                self.assertEqual(_bytes(os.path.join(folder, name)), raw, name)
            self.assertEqual(_bytes(os.path.join(folder, "REJECTED.txt")),
                             "".join(n + "\n" for n in FRESH_REJECTED).encode("utf-8"))
            first = _snapshot(folder)
            self.assertEqual(migrate.migrate_folder(folder), FRESH_REJECTED)
            self.assertEqual(_snapshot(folder), first)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_an_all_good_folder_gets_an_empty_list(self):
        folder = tempfile.mkdtemp(prefix="ij-mig-")
        try:
            with open(os.path.join(folder, "a.json"), "wb") as fh:
                fh.write(json.dumps(FRESH["a.json"]).encode("utf-8"))
            self.assertEqual(migrate.migrate_folder(folder), [])
            self.assertEqual(_bytes(os.path.join(folder, "REJECTED.txt")), b"")
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_the_command_line(self):
        folder = _fresh_folder()
        saved = sys.argv[:]
        try:
            sys.argv = [os.path.join(HERE, "migrate.py"), folder]
            try:
                runpy.run_path(os.path.join(HERE, "migrate.py"), run_name="__main__")
            except SystemExit:
                # SCHEMA.md says nothing about the exit status (a command that
                # exits 1 because files were rejected is within it): the
                # command's EFFECTS below decide.
                pass
            self.assertEqual(_bytes(os.path.join(folder, "a.json")),
                             _canonical(_v2(FRESH_MIGRATED["a.json"])))
            self.assertTrue(os.path.exists(os.path.join(folder, "REJECTED.txt")))
        finally:
            sys.argv = saved
            shutil.rmtree(folder, ignore_errors=True)

    def test_migrate_record(self):
        record = {"name": "Lee, Ann", "joined": "3/4/2021", "phone": "15550106000", "tags": "x, Y"}
        before = copy.deepcopy(record)
        self.assertEqual(migrate.migrate_record(record), _v2(FRESH_MIGRATED["a.json"]))
        self.assertEqual(record, before)
        done = json.loads(FRESH_V2)
        self.assertEqual(migrate.migrate_record(copy.deepcopy(done)), done)
        for bad in (
            {"name": "A B", "joined": "02/30/2020"},
            {"name": "A B", "joined": "2021-13-01"},
            {"name": "A B", "joined": "1 Sept 2020"},
            {"name": "A B", "joined": "2020-01-01", "phone": "123"},
            {"name": "A B", "joined": "2020-01-01", "phone": "25550102000"},
            {"name": "A B", "joined": "2020-01-01", "version": 3},
            {"joined": "2020-01-01"},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    migrate.migrate_record(bad)


ORIGINAL = {
    "katherine.json": "d6d8731a71a7323d3cd6120978d1eb1781bee994014d3467bcdc3794167e5f3c",
    "bad_date.json": "81d47c35f8756b5d9bd0dcd79a98cf052f1e9d93fa1090727b9647826e45aaeb",
    "bad_phone.json": "b909cf1b86b68c0ecb37be57add8d9f1e7dc181ffe49c774c69506e334306a14",
    "broken.json": "387c38bd110cb50be5aa1d86f9610e3fa9df3f5448f01b3b4b1f3104af1c107c",
    "future.json": "b07382f991898742db39452f421ede15307e313de6bd656d1ba4d6cd3f7df235",
    "README.txt": "e10326c8a59d7d8e1e2c31c3a0e9d66d6fd29a3e532bd21c7fe8966d47efe955",
}
