"""A stand-in for ``npx`` (v1.415.0 direct-launch pins). Wrapped by a test in an
``npx.cmd`` so the transport starts it the way it starts the real one
(``cmd.exe`` -> ``npx.cmd`` -> this -> ``node <server>``).

Two modes, like the real npx:

* ``npx -y --package=<spec> -- node -e <js>`` (the ASK): print the one
  ``IJASK{...}`` line the real ask prints, built from ``FAKE_NPX_*`` env vars,
  with npx's install ``.bin`` folder in front of PATH. Exits.
* ``npx -y <spec> <args...>`` (a START): run ``node $FAKE_NPX_SERVER <args>``
  as a child sharing our stdio and wait for it — the launcher idling beside
  the real server. ``FAKE_NPX_LOG`` (optional) gets one line per call.
"""

import json
import os
import subprocess
import sys

args = sys.argv[1:]
log = os.environ.get("FAKE_NPX_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(("ask" if any(a.startswith("--package=") for a in args) else "start") + "\n")

if any(a.startswith("--package=") for a in args):
    path = os.environ.get("FAKE_NPX_BIN", "") + os.pathsep + os.environ.get("PATH", "")
    print("npm warn exec something noisy", file=sys.stderr)
    print("IJASK" + json.dumps({
        "node": os.environ["FAKE_NPX_NODE"],
        "path": path,
        "cache": os.environ["FAKE_NPX_CACHE"],
        "gp": os.environ["FAKE_NPX_GP"],
        "lp": os.environ["FAKE_NPX_LP"],
    }))
    sys.exit(0)

i = 0
while i < len(args) and args[i].startswith("-"):
    i += 1
rest = args[i + 1:]
child = subprocess.Popen([os.environ["FAKE_NPX_NODE"], os.environ["FAKE_NPX_SERVER"], *rest])
sys.exit(child.wait())
