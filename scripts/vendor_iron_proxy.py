"""Vendor the Iron-Proxy single-file bundle into the desktop app (v1.301.0).

Iron-Proxy (https://github.com/RealDealCPA-VR/Iron-Proxy, MIT) is the account
switcher Iron Jarvis starts when the user turns it on in Connections. It is not
published to npm, so the app ships ONE bundled ES module built from a pinned,
CLEAN checkout of that repo:

    uv run python scripts/vendor_iron_proxy.py [--source ../Iron-Proxy]

Steps: refuse a dirty checkout (the copy must name a real commit), build the
workspace and its `bundle` script, run the result with `node --version` to make
sure it starts, then write

    desktop/vendor/iron-proxy/iron-proxy.mjs   the bundle
    desktop/vendor/iron-proxy/LICENSE          Iron-Proxy's MIT licence
    desktop/vendor/iron-proxy/SOURCE.txt       repo, commit, version, sha256

`tests/test_iron_proxy_vendor_v1301.py` checks that SOURCE.txt's sha256 matches
the committed bundle, so a hand edit of the vendored file fails the suite.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEST = REPO / "desktop" / "vendor" / "iron-proxy"
REMOTE = "https://github.com/RealDealCPA-VR/Iron-Proxy"
BUNDLE_REL = Path("packages") / "cli" / "dist-bundle" / "iron-proxy.mjs"


def _run(argv: list[str], cwd: Path) -> str:
    shell = sys.platform == "win32"  # pnpm is a .cmd shim on Windows
    proc = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=shell
    )
    if proc.returncode != 0:
        raise SystemExit(f"{' '.join(argv)} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default=str(REPO.parent / "Iron-Proxy"), help="Iron-Proxy checkout")
    ap.add_argument("--allow-dirty", action="store_true", help="vendor uncommitted work (never for a release)")
    args = ap.parse_args()
    src = Path(args.source).resolve()
    if not (src / "pnpm-workspace.yaml").exists():
        raise SystemExit(f"{src} is not an Iron-Proxy checkout")

    dirty = _run(["git", "status", "--porcelain"], src)
    if dirty and not args.allow_dirty:
        raise SystemExit(f"{src} has uncommitted changes — commit them first (the copy must name a commit):\n{dirty}")
    commit = _run(["git", "rev-parse", "HEAD"], src)

    _run(["pnpm", "-r", "build"], src)
    _run(["pnpm", "--filter", "iron-proxy", "bundle"], src)
    bundle = src / BUNDLE_REL
    if not bundle.exists() or bundle.stat().st_size == 0:
        raise SystemExit(f"the bundle was not produced at {bundle}")
    version = _run(["node", str(bundle), "--version"], src).splitlines()[-1].strip()
    if not version:
        raise SystemExit("the bundle printed no version")

    DEST.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(bundle, DEST / "iron-proxy.mjs")
    shutil.copyfile(src / "LICENSE", DEST / "LICENSE")
    digest = hashlib.sha256((DEST / "iron-proxy.mjs").read_bytes()).hexdigest()
    (DEST / "SOURCE.txt").write_text(
        "\n".join(
            [
                f"repo: {REMOTE}",
                f"commit: {commit}" + (" (dirty)" if dirty else ""),
                f"version: {version}",
                f"sha256: {digest}",
                "built-by: scripts/vendor_iron_proxy.py",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"vendored Iron-Proxy {version} @ {commit[:7]} -> {DEST} (sha256 {digest[:12]}…)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
