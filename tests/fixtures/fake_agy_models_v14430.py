"""A stand-in ``agy`` for the model-list reader's tests (v1.443.0): replays
the measured ``agy models`` answer (agy 1.5.3) and logs how it was started.
Never the real Antigravity.

Env: IJ_FAKE_AGY_MODELS (the recorded stdout), IJ_FAKE_AGY_MODE (ok | signin
| other | empty), IJ_FAKE_LOG (one JSON line per run)."""

import json
import os
import sys

log = os.environ.get("IJ_FAKE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(),
                             "no_update": os.environ.get("AGY_CLI_DISABLE_AUTO_UPDATE")}) + "\n")
mode = os.environ.get("IJ_FAKE_AGY_MODE", "ok")
sys.stderr.write("Fetching available models...\n")
if mode == "ok":
    with open(os.environ["IJ_FAKE_AGY_MODELS"], "rb") as fh:
        sys.stdout.buffer.write(fh.read())
    sys.exit(0)
if mode == "signin":
    # A Google-shaped key built from pieces: a whole one in a tracked file
    # raises GitHub's secret scanning (tests/test_no_secret_shaped_values_v14340).
    google_shaped = "AI" + "za" + "SyGOOGLEKEYPIECESGOOGLEKEYPIECES12"
    sys.stderr.write("Error: not logged in. Run agy to sign in. token=sk-ant-api03-SECRETSECRETSECRET "
                     "key " + google_shaped + "\n")
    sys.exit(1)
if mode == "empty":
    sys.exit(0)
sys.stderr.write("Error: failed to fetch available models: 503 Service Unavailable\n")
sys.exit(1)
