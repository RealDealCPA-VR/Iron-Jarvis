"""v1.545.0 — one test, two coding tools.

``fake_harness_v1409.py`` replays ONE recording, named by ``IJ_FAKE_SCENARIO``.
A session that switches tools starts a second, different tool, so this small
front picks the recording by the tool it was started as:

    python fake_two_tools_v1545.py <tool id> <the tool's own arguments…>

and replays ``IJ_FAKE_SCENARIO_<TOOL ID>`` (upper case, ``-`` as ``_``)
through the same fake. Its start row in the log names the tool (``tool``).
"""

import json
import os
import runpy
import sys

tool = sys.argv[1]
os.environ["IJ_FAKE_SCENARIO"] = os.environ["IJ_FAKE_SCENARIO_" + tool.upper().replace("-", "_")]
log = os.environ.get("IJ_FAKE_LOG", "")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"tool": tool, "started": True}) + "\n")
sys.argv = [sys.argv[0]] + sys.argv[2:]
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_harness_v1409.py"),
               run_name="__main__")
