# IJ-BENCH-HIDDEN-CHECK frontier-broken-build
"""Hidden check: the report builds, with the required dependency versions,
the same way every time, in a plain isolated Python (no site-packages at all:
nothing extra installed is ever reachable); nothing vendored changed; every
minimark / tinylex module the build imports is the vendored release, byte for
byte, wherever the build reads it from."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CRLF = bytes([13, 10])
LF = bytes([10])
ZIP_REL = "vendor/archives/tinylex-0.4.2.zip"
#: Hash seeds for the builds: a report that depends on chance (a set's order)
#: comes out differently under them.
SEEDS = ("11", "29", "4242")
#: How every build runs: -s and -S (no user or system site-packages, so a
#: library that happens to be installed in the grading Python is never
#: importable: task.md and README.md ask for "a plain Python 3.12 that has
#: nothing extra installed"), and _env() strips every PYTHON* variable.
PLAIN = ("-s", "-S")

EXPECTED_SHA = "a2cb5c6801906c49b665ec52686537c4681ef6710bb24e23fe0da73e28318408"
EXPECTED_REPORT = '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>Quarterly Report — Q3 2026</title>\n</head>\n<body>\n<h1>Quarterly Report — Q3 2026</h1>\n<p class="tags">Tags: compliance, finance, hiring, planning, regions, risk, sales, summary, supply</p>\n<section>\n<h2>Overview</h2>\n<p>The third quarter closed <strong>ahead of plan</strong>. Revenue grew 8% on the second quarter, and R&amp;D spending stayed below 12% of revenue for the first time this year.</p>\n<p>The full numbers are in <code>results.csv</code> on the <a href="https://example.com/finance?q=3&amp;view=full">finance dashboard</a>.</p>\n</section>\n<section>\n<h2>Results</h2>\n<p>Sales by region, in thousands of dollars:</p>\n<table>\n<thead>\n<tr><th>Region</th><th style="text-align:right">Q2</th><th style="text-align:right">Q3</th><th style="text-align:center">Change</th></tr>\n</thead>\n<tbody>\n<tr><td>North</td><td style="text-align:right">1,204</td><td style="text-align:right">1,310</td><td style="text-align:center">+8.8%</td></tr>\n<tr><td>South</td><td style="text-align:right">980</td><td style="text-align:right">1,052</td><td style="text-align:center">+7.3%</td></tr>\n<tr><td>West &amp; Coast</td><td style="text-align:right">1,442</td><td style="text-align:right">1,515</td><td style="text-align:center">+5.1%</td></tr>\n</tbody>\n</table>\n<p>Every region grew. <em>West &amp; Coast</em> is still the largest region, but its growth was &lt; 6%.</p>\n</section>\n<section>\n<h2>Risks</h2>\n<h3>What could go wrong</h3>\n<ul>\n<li>Supply delays: lead times for the &quot;core&quot; boards are up to 14 weeks.</li>\n<li>A new data rule: exports must not contain &lt;email&gt; addresses.</li>\n<li>Currency: a 5% swing in EUR moves revenue by about $0.4M.</li>\n</ul>\n<h3>What we are doing</h3>\n<p>We moved two suppliers to <em>quarterly</em> contracts and added a check to the <code>export</code> job.</p>\n</section>\n<section>\n<h2>Next steps</h2>\n<ul>\n<li>Hire four engineers for <strong>Q&amp;A</strong> and tooling.</li>\n<li>Close the Lisbon office move by November.</li>\n<li>Publish the Q4 plan on the <a href="https://example.com/plan">planning page</a>.</li>\n</ul>\n</section>\n<p class="footer">Last updated: 2026-09-30</p>\n</body>\n</html>\n'

UNCHANGED = {
    'content/next-steps.md': '41153940b0737f8729edb51b96062d79bb7fe06a1947faef5c114e2384dc19aa',
    'content/overview.md': '9c97d3586844807d24ee75c79c726b006aced4168499727e66db1b6d624b891e',
    'content/results.md': '9c2e1290764829a6847decfe6aa0351a7eb4b63a916dce8f42642233eb60a69e',
    'content/risks.md': '2b31c46d9aa8d1c090925a991a65a4db90a5c826f69ec9ab60569a7382c2473b',
    'requirements.txt': '70e307d108ed9ad2ec3989a916592df8423bb9f4c2316f6211141fd150be58d9',
    'vendor/README.md': '7c2de24622efed964ef2d3b395d96ddd7ad1eaef2a842cb3f9cb70fae299947b',
    'vendor/archives/tinylex-0.4.2.zip': '7af4c9467c8ce49f27c34c59f782992a284b890c97d8969e55609baffee11b9f',
    'vendor/minimark-1.2/CHANGELOG.md': '9b612679ac6e6f0ebc691bc70c7362705402d51aa8efd36da6b36c4cee173562',
    'vendor/minimark-1.2/PKG-INFO': 'd086eba405fbd06e4b0332d41b99493deab13a950c7b156f4e4165e07023e109',
    'vendor/minimark-1.2/minimark/__init__.py': 'a8fec0b0f8e578436fd092f4082b028a66640aed89f4bd23596b0999a0ffff09',
    'vendor/minimark-2.1/CHANGELOG.md': '3dd786de312913b0137f244965736d6b1a46966cec00000fe286d28eba4dcbec',
    'vendor/minimark-2.1/PKG-INFO': '97661c3f40a8014d54aded0462c8032595682f70b3994414b32348c0e89117fc',
    'vendor/minimark-2.1/minimark/__init__.py': 'cb27e525fa290b6f68f3cc02037d8c054ba5eaa28634d5e352f1ad859304bf8e',
    'vendor/minimark-2.1/minimark/blocks.py': 'b64abadf2adbfcbb449f1de6b7fe7bfb31d878e89a28f2b58bb4f3186ab248f4',
    'vendor/minimark-2.1/minimark/document.py': 'a0abdbc3c024a209d7a427e4f48adb9290fd9f25e8f7b48ed6adf9ed27b13654',
    'vendor/minimark-2.1/minimark/inline.py': '0d36c97fc938478bc6ed93b323f9b326c6ad477f5df5ad2d7eba905c351b4a46',
    'vendor/tinylex-0.3/PKG-INFO': 'f6accdcd32a7aeae088cdf731368a0d524b1148fa920b3da49b356a37d01e8c9',
    'vendor/tinylex-0.3/tinylex/__init__.py': '789ab4c6b52910ba8eb36d15cf49365be9c8742c76743e815f1e897e2842a5c3',
}


def _env(seed):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
    env["PYTHONHASHSEED"] = seed
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run(argv, seed):
    return subprocess.run(
        argv, cwd=HERE, env=_env(seed), capture_output=True, timeout=120,
        stdin=subprocess.DEVNULL,
    )


def _text(data):
    return data.decode("utf-8", errors="replace")


def _first_difference(got, want):
    got_lines = got.split("\n")
    want_lines = want.split("\n")
    for i, (g, w) in enumerate(zip(got_lines, want_lines)):
        if g != w:
            return f"line {i + 1}:\n  expected: {w!r}\n  got:      {g!r}"
    return f"the report has {len(got_lines)} lines; {len(want_lines)} were expected"


WRAPPER = r"""
import json, os, runpy, sys
here = os.getcwd()
sys.path.insert(0, here)
sys.argv = [os.path.join(here, "build.py")]
code = 0
try:
    runpy.run_path(os.path.join(here, "build.py"), run_name="__main__")
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
mods = {}
for name, mod in sorted(sys.modules.items()):
    if mod is not None and name.split(".")[0] in ("minimark", "tinylex"):
        mods[name] = {"version": getattr(mod, "__version__", None), "file": getattr(mod, "__file__", None)}
print("IJCHECK " + json.dumps({"code": code, "modules": mods}))
"""


#: The releases requirements.txt asks for, as vendored.
RELEASES = {"minimark": "minimark 2.1.0", "tinylex": "tinylex 0.4.2"}
MINIMARK_TREE = "vendor/minimark-2.1/"


def _release_files():
    """``{"minimark/document.py": sha256, "tinylex/_tokens.py": sha256, ...}``:
    every module file of the two vendored releases. minimark's from the pinned
    hashes of its vendored tree; tinylex's from the vendored archive, after
    checking the archive itself is the pinned one."""
    files = {
        rel[len(MINIMARK_TREE):]: digest
        for rel, digest in UNCHANGED.items()
        if rel.startswith(MINIMARK_TREE + "minimark/") and rel.endswith(".py")
    }
    with open(os.path.join(HERE, *ZIP_REL.split("/")), "rb") as fh:
        archive = fh.read()
    if hashlib.sha256(archive).hexdigest() != UNCHANGED[ZIP_REL]:
        raise AssertionError(f"{ZIP_REL} is not the vendored tinylex 0.4.2 archive")
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for name in zf.namelist():
            if name.startswith("tinylex/") and name.endswith(".py"):
                files[name] = hashlib.sha256(zf.read(name).replace(CRLF, LF)).hexdigest()
    return files


class HiddenBuildReport(unittest.TestCase):
    def test_1_the_build_gives_the_expected_report_every_time(self):
        for seed in SEEDS:
            with self.subTest(hash_seed=seed):
                shutil.rmtree(os.path.join(HERE, "dist"), ignore_errors=True)
                result = _run([sys.executable, *PLAIN, "build.py"], seed)
                self.assertEqual(
                    result.returncode, 0,
                    "python build.py failed in a plain isolated Python:\n" + _text(result.stderr)[-2000:],
                )
                path = os.path.join(HERE, "dist", "report.html")
                self.assertTrue(os.path.isfile(path), "build.py did not write dist/report.html")
                with open(path, "rb") as fh:
                    raw = fh.read()
                self.assertNotIn(b"\r", raw, "dist/report.html must have LF line endings")
                data = raw.replace(CRLF, LF)
                if hashlib.sha256(data).hexdigest() != EXPECTED_SHA:
                    self.fail(
                        "dist/report.html is not the expected report; first difference at "
                        + _first_difference(_text(data), EXPECTED_REPORT)
                    )

    def test_2_the_build_imports_the_required_versions(self):
        result = _run([sys.executable, *PLAIN, "-c", WRAPPER], "7")
        out = _text(result.stdout)
        marked = [line for line in out.splitlines() if line.startswith("IJCHECK ")]
        self.assertTrue(marked, "the build did not run:\n" + _text(result.stderr)[-2000:])
        info = json.loads(marked[-1][len("IJCHECK "):])
        self.assertEqual(info["code"], 0, "the build failed:\n" + _text(result.stderr)[-2000:])
        mods = info["modules"]
        self.assertIn("minimark", mods, "the build never imported minimark")
        self.assertEqual(mods["minimark"]["version"], "2.1.0", "the build used the wrong minimark")
        self.assertIn("tinylex", mods, "the build never imported tinylex")
        self.assertEqual(mods["tinylex"]["version"], "0.4.2", "the build used the wrong tinylex")
        # The SAME rule for both libraries: every module the build imported
        # from them is a file of the vendored release, byte for byte, wherever
        # the build read it from (the vendored tree or archive, or an unmodified
        # copy of either, a zip included). A changed or extra file is not the
        # release requirements.txt asks for (vendor/README.md: unmodified).
        release = _release_files()
        for name, mod in sorted(mods.items()):
            path = mod["file"]
            if not path:
                continue  # no file to compare (its version and the report are checked)
            top = name.split(".")[0]
            label = RELEASES[top]
            norm = path.replace("\\", "/")
            parts = norm.split("/")
            self.assertIn(top, parts[:-1], f"{name}: unexpected file {path}")
            key = "/".join(parts[len(parts) - 1 - parts[::-1].index(top):])
            self.assertIn(key, release, f"{name}: {path} is not part of {label}")
            if ".zip/" in norm:
                archive, member = norm.split(".zip/", 1)
                with zipfile.ZipFile(archive + ".zip") as zf:
                    data = zf.read(member)
            else:
                with open(path, "rb") as fh:
                    data = fh.read()
            digest = hashlib.sha256(data.replace(CRLF, LF)).hexdigest()
            self.assertEqual(digest, release[key], f"{name}: {path} differs from the {label} release")

    def test_3_vendor_content_and_requirements_are_unchanged(self):
        seen = {}
        for top in ("vendor", "content"):
            for dirpath, dirs, files in os.walk(os.path.join(HERE, top)):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for name in files:
                    if name.endswith(".pyc"):
                        continue
                    full = os.path.join(dirpath, name)
                    rel = os.path.relpath(full, HERE).replace(os.sep, "/")
                    with open(full, "rb") as fh:
                        data = fh.read()
                    if not rel.endswith(".zip"):
                        data = data.replace(CRLF, LF)
                    seen[rel] = hashlib.sha256(data).hexdigest()
        with open(os.path.join(HERE, "requirements.txt"), "rb") as fh:
            seen["requirements.txt"] = hashlib.sha256(fh.read().replace(CRLF, LF)).hexdigest()
        added = sorted(set(seen) - set(UNCHANGED))
        removed = sorted(set(UNCHANGED) - set(seen))
        changed = sorted(r for r in UNCHANGED if r in seen and seen[r] != UNCHANGED[r])
        problems = []
        if added:
            problems.append("added: " + ", ".join(added))
        if removed:
            problems.append("removed: " + ", ".join(removed))
        if changed:
            problems.append("changed: " + ", ".join(changed))
        self.assertEqual(problems, [], "vendor/, content/ and requirements.txt must stay as they were")


if __name__ == "__main__":
    unittest.main()
