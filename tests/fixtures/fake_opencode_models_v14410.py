"""A fake ``opencode`` for the model-list reader (v1.441.0): answers ONLY
``models --verbose`` with a RECORDED answer of opencode 1.18.18
(``build_app_v14410/opencode_models_verbose.txt``, a scratch home), and logs
its argv, folder and the environment keys the reader sets to ``IJ_FAKE_LOG``.
``IJ_FAKE_OC_RC`` makes it fail with that exit code."""

import json
import os
import sys

LOG = os.environ.get("IJ_FAKE_LOG", "")
if LOG:
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "env": {
            k: os.environ.get(k) for k in ("OPENCODE_DISABLE_MODELS_FETCH", "OPENCODE_PERMISSION",
                                           "OPENCODE_ENABLE_QUESTION_TOOL")}}) + "\n")
rc = int(os.environ.get("IJ_FAKE_OC_RC") or "0")
if rc:
    sys.exit(rc)
if sys.argv[1:] != ["models", "--verbose"]:
    sys.exit(2)
with open(os.environ["IJ_FAKE_OC_MODELS"], encoding="utf-8") as fh:
    sys.stdout.write(fh.read())
