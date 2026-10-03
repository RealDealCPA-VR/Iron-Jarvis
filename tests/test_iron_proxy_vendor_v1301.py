"""v1.301.0 — the vendored Iron-Proxy bundle and the desktop wiring that ships it.

Iron-Proxy (RealDealCPA-VR/Iron-Proxy, MIT) is started by the daemon, with
Electron's own Node, when the user turns it on in Connections. These pins keep
the four places that must agree in step: the committed bundle + its provenance,
electron-builder's extraResources, afterPack's install inventory, and the env
the desktop hands the daemon.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "desktop" / "vendor" / "iron-proxy"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _source() -> dict[str, str]:
    rows = {}
    for line in _text(VENDOR / "SOURCE.txt").splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            rows[key.strip()] = value.strip()
    return rows


def test_the_bundle_is_exactly_the_file_its_provenance_names():
    src = _source()
    assert src["repo"] == "https://github.com/RealDealCPA-VR/Iron-Proxy"
    assert re.fullmatch(r"[0-9a-f]{40}", src["commit"]), "a release vendors a CLEAN commit"
    assert re.fullmatch(r"\d+\.\d+\.\d+", src["version"])
    data = (VENDOR / "iron-proxy.mjs").read_bytes()
    assert len(data) > 50_000
    assert hashlib.sha256(data).hexdigest() == src["sha256"], (
        "the vendored bundle was edited by hand or rewritten on checkout — "
        "re-run scripts/vendor_iron_proxy.py (and keep the .gitattributes -text rule)"
    )
    # Self-contained: the workspace packages are inlined, nothing to resolve at runtime.
    assert "from '@iron-proxy/" not in data.decode("utf-8") and 'from "@iron-proxy/' not in data.decode("utf-8")


def test_the_licence_ships_with_it():
    assert "MIT License" in _text(VENDOR / "LICENSE")


def test_git_never_rewrites_the_bundle():
    assert "desktop/vendor/iron-proxy/** -text" in _text(ROOT / ".gitattributes")


def test_the_installer_copies_it_and_the_inventory_checks_it():
    pkg = json.loads(_text(ROOT / "desktop" / "package.json"))
    extra = pkg["build"]["extraResources"]
    assert {"from": "vendor/iron-proxy", "to": "iron-proxy"} in extra
    after = _text(ROOT / "desktop" / "afterPack.js")
    inventory = re.search(r"buildManifest\(resourcesDir, \[([^\]]*)\]", after)
    assert inventory and '"iron-proxy"' in inventory.group(1)
    assert 'path.join(resourcesDir, "iron-proxy", "iron-proxy.mjs")' in after


def test_the_desktop_tells_the_daemon_only_when_the_bundle_exists():
    main = _text(ROOT / "desktop" / "main.js")
    body = main[main.index("function ironProxyEnv()") :]
    body = body[: body.index("\n}\n")]
    assert "if (!fs.existsSync(IRON_PROXY_BUNDLE)) return {};" in body
    assert "IRONJARVIS_IRON_PROXY_NODE: process.execPath" in body
    assert "IRONJARVIS_IRON_PROXY_BUNDLE: IRON_PROXY_BUNDLE" in body
    assert 'path.join(RES_DIR, "iron-proxy", "iron-proxy.mjs")' in main
    # Both spawn paths (packaged daemon and dev `uv run`) pass it.
    assert "...ironProxyEnv()" in main
    assert main.count("ironProxyEnv()") >= 3  # the definition + the two spawn sites


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_bundle_starts_and_reports_the_vendored_version():
    out = subprocess.run(
        ["node", str(VENDOR / "iron-proxy.mjs"), "--version"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == _source()["version"]
