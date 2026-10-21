# IJ-BENCH-HIDDEN-CHECK hard-markdown-exporter
import contextlib
import hashlib
import io
import json
import os
import runpy
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from reporter import exporters  # noqa: E402
from reporter.model import Report  # noqa: E402

SAMPLE_MD = (
    "# Quarterly sales\n"
    "\n"
    "| Region | Units | Revenue | On target | Note |\n"
    "| --- | ---: | ---: | --- | --- |\n"
    "| North | 1,200 | 15,000.50 | yes | best \\| ever |\n"
    "| South | 30 | 99.00 | no | line one<br>line two |\n"
    "| East |  | 7.25 |  |  |\n"
    "\n"
    "> Figures are provisional.\n"
    "> Ask finance before sharing.\n"
)
EDGE_MD = (
    "# A \\| B<br>C\n"
    "\n"
    "| x\\|y | n |\n"
    "| --- | --- |\n"
    "\n"
    "_No rows._\n"
    "\n"
    "> one\n"
    "> two \\| three\n"
    "> four\n"
)
PLAIN_MD = (
    "# Plain\n"
    "\n"
    "| flag | count |\n"
    "| --- | ---: |\n"
    "| yes | -3 |\n"
    "| no | 1,000,000 |\n"
)
TODAY = {
    ("csv", "sample"): "4da27a9b0be8741fd6e57eae085b73937aa5942b1780938f1f02f90b8e664d78",
    ("csv", "edge"): "4840abdc169a6cc479716c1f1befa60ab407b3218e642e5246193bcfc31b0f7a",
    ("json", "sample"): "7a93f8182c419d3dbd3f47fafc36aace8c8c12b4f303b50c67a8e3ddf4302b36",
    ("json", "edge"): "14f3dd0865dbe8e87590cd458f7ac10c888ef53663dbde1bcba0b7331ce6ca96",
    ("html", "sample"): "b69e251163de20617cb61fa58902dca1e872d7a50b971ffde9caa2fc97ca5a77",
    ("html", "edge"): "c41469d84c9d7eab84d75892e20a399578a8eef7f290773b6b6653e0e8f71e7d",
}
CRLF = bytes([13, 10])
LF = bytes([10])


def sample():
    with open(os.path.join(HERE, "samples", "sales.json"), encoding="utf-8") as fh:
        return Report.from_dict(json.load(fh))


def edge():
    return Report(title="A | B\nC", columns=["x|y", "n"], rows=[],
                  notes=["one\ntwo | three", "four"])


def cli(*argv):
    out = io.StringIO()
    saved = sys.argv[:]
    sys.argv = ["reporter", *argv]
    try:
        with contextlib.redirect_stdout(out):
            try:
                runpy.run_module("reporter.cli", run_name="__main__", alter_sys=False)
            except SystemExit as exc:
                if exc.code not in (0, None):
                    raise AssertionError(f"the command failed: {exc.code}") from None
    finally:
        sys.argv = saved
    return out.getvalue()


def read(path):
    with open(path, "rb") as fh:
        return fh.read().replace(CRLF, LF).decode("utf-8")


class HiddenMarkdown(unittest.TestCase):
    def test_the_sample(self):
        self.assertEqual(exporters.get("markdown")(sample()), SAMPLE_MD)

    def test_names(self):
        self.assertIs(exporters.get("md"), exporters.get("markdown"))
        self.assertEqual(exporters.get("markdown").extension, "md")
        self.assertIn("markdown", exporters.names())
        self.assertNotIn("md", exporters.names())

    def test_escaping_no_rows_and_notes(self):
        self.assertEqual(exporters.get("markdown")(edge()), EDGE_MD)
        plain = Report(title="Plain", columns=["flag", "count"], rows=[[True, -3], [False, 1000000]])
        self.assertEqual(exporters.get("md")(plain), PLAIN_MD)
        windows = Report(title="T", columns=["c"], rows=[["a\r\nb"]], notes=["x\r\ny"])
        self.assertEqual(
            exporters.get("markdown")(windows),
            "# T\n\n| c |\n| --- |\n| a<br>b |\n\n> x\n> y\n",
        )

    def test_the_other_formats_did_not_change(self):
        for (name, which), digest in TODAY.items():
            with self.subTest(name=name, which=which):
                report = sample() if which == "sample" else edge()
                text = exporters.get(name)(report)
                self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), digest)

    def test_the_command_line(self):
        self.assertEqual(
            cli("--list-formats").splitlines(),
            ["csv (.csv)", "html (.html)", "json (.json)", "markdown (.md)"],
        )
        folder = tempfile.mkdtemp(prefix="ij-md-")
        try:
            report = os.path.join(folder, "sales.json")
            shutil.copyfile(os.path.join(HERE, "samples", "sales.json"), report)
            printed = cli(report, "--format", "md").strip()
            target = os.path.join(folder, "sales.md")
            self.assertEqual(os.path.normcase(os.path.abspath(printed)), os.path.normcase(target))
            self.assertEqual(read(target), SAMPLE_MD)
            other = os.path.join(folder, "other.txt")
            cli(report, "--format", "markdown", "--out", other)
            self.assertEqual(read(other), SAMPLE_MD)
            cli(report, "--format", "htm")
            self.assertTrue(os.path.exists(os.path.join(folder, "sales.html")))
        finally:
            shutil.rmtree(folder, ignore_errors=True)
