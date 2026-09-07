"""The browser add-on's IDENTITY is pinned, and it is pinned from the MANIFEST.

Ship 1 of the Browser capability (v1.235.0). Decision D27A: the add-on must have a
deterministic Chrome extension id, because the daemon's origin allowlist names that
id and nothing else — `/browser/ws` accepts `chrome-extension://<pinned>` and closes
every other origin with 1008. An id that drifts is a feature that stops working on
somebody else's machine with no error anyone can read.

Chrome derives the id from the manifest's `key` field: base64-decode the SPKI,
SHA-256 it, take the first 16 bytes, and map each hex digit through 0-9a-f -> a-p.
So the id is not a choice, it is a CONSEQUENCE of the key, and the only honest way
to pin it is to recompute it.

What each assertion catches, and why a green suite would otherwise miss it:

* The id is DERIVED FROM THE COMMITTED KEY, not asserted twice. A test that compared
  `identity.PINNED_EXTENSION_ID` to a literal copy of itself would pass forever while
  the manifest carried a completely unrelated key — and the failure would appear only
  on a real Chrome, as a socket closed for a bad origin. This recomputes from
  `manifest.json` and compares to the Python constant, so the two files cannot drift.
* The MANIFEST and the PYTHON module carry the same key. Two sources of one secret-ish
  fact is the shape this repo has been bitten by before; one of them has to be
  authoritative and the other has to be checked against it.
* No INSTALL-TIME all-sites permission (Q02). `host_permissions` present in the
  manifest would grant every site the moment the user loads the add-on, silently
  turning an opt-in capability into a standing one. Chrome shows that as a scary
  install warning and, worse, it would work — so nothing would fail.
* `optional_host_permissions` carries BOTH schemes. Without them the runtime request
  cannot be made at all, and the add-on would sit permanently unable to read a page
  while reporting itself connected.
* The MINIMAL permission set. A permission added casually is a permission the user
  granted without being asked about it; this pins the four the plan justifies.
* The popup declares NO PAIRING UI (D28). Pairing is initiated and approved from
  Jarvis, so a Pair button inside the add-on would be a second, unreviewable door to
  the same credential.
* The Node verifier implements the SAME algorithm as the Python one. Two
  implementations of one derivation drift, and the CI check would then pass while the
  daemon rejected the real browser.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from iron_jarvis.browser import identity

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "extensions" / "chrome" / "manifest.json"
VERIFY_ID = REPO / "extensions" / "chrome" / "scripts" / "verify-id.mjs"
POPUP_HTML = REPO / "extensions" / "chrome" / "src" / "popup" / "popup.html"


def _manifest() -> dict:
    # utf-8 explicitly: a manifest read under the runner's default codepage has
    # bitten this repo elsewhere, and json.load would fail unhelpfully.
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _chrome_id_from_spki_b64(spki_b64: str) -> str:
    """Chrome's own derivation, written out longhand ON PURPOSE.

    This deliberately does NOT call ``identity.extension_id_from_spki_b64``: a test
    that reuses the implementation it is checking pins nothing. If the two ever
    disagree, one of them is wrong and this test says so.
    """
    der = base64.b64decode(spki_b64, validate=True)
    digest = hashlib.sha256(der).hexdigest()[:32]
    return "".join(chr(ord("a") + int(ch, 16)) for ch in digest)


def test_the_pinned_id_is_derived_from_the_committed_manifest_key():
    key = _manifest()["key"]
    assert "\n" not in key and "\r" not in key, (
        "the manifest key must be ONE line — Chrome rejects a wrapped key, and the "
        "wrapping survives a copy/paste from openssl output"
    )
    assert _chrome_id_from_spki_b64(key) == identity.PINNED_EXTENSION_ID, (
        "manifest.json's key does not derive identity.PINNED_EXTENSION_ID — the "
        "daemon's /browser/ws origin allowlist would refuse the real add-on"
    )


def test_the_manifest_key_and_the_python_key_are_the_same_key():
    assert _manifest()["key"] == identity.PINNED_EXTENSION_KEY, (
        "two copies of the add-on's public key disagree; the manifest is what Chrome "
        "reads, so identity.PINNED_EXTENSION_KEY is the one that is wrong"
    )


def test_the_repo_derivation_agrees_with_the_longhand_one():
    assert identity.extension_id_from_spki_b64(
        identity.PINNED_EXTENSION_KEY
    ) == _chrome_id_from_spki_b64(identity.PINNED_EXTENSION_KEY)


def test_the_id_has_chromes_shape():
    ext_id = identity.PINNED_EXTENSION_ID
    assert len(ext_id) == identity.EXTENSION_ID_LENGTH == 32
    assert re.fullmatch(r"[a-p]{32}", ext_id), (
        "a Chrome extension id is 32 characters from a-p; anything else was invented "
        "rather than derived"
    )


def test_no_install_time_host_permissions(  # Q02
):
    m = _manifest()
    assert "host_permissions" not in m, (
        "Q02: all-sites access must NEVER be granted at install time. With this key "
        "present the add-on can read every page the moment it is loaded, and nothing "
        "in the product would report that as wrong"
    )


def test_optional_host_permissions_carry_both_schemes():
    assert sorted(_manifest()["optional_host_permissions"]) == [
        "http://*/*",
        "https://*/*",
    ], "without both schemes the runtime grant cannot be requested at all"


def test_the_permission_set_stays_minimal():
    assert sorted(_manifest()["permissions"]) == sorted(
        ["tabs", "scripting", "downloads", "storage"]
    ), (
        "these four are the ones the plan justifies: tab metadata, on-demand "
        "injection, download completion (D23), and the pairing token's storage. A "
        "fifth means the user granted something nobody argued for"
    )


def test_manifest_v3_and_a_module_service_worker():
    m = _manifest()
    assert m["manifest_version"] == 3
    assert m["background"]["service_worker"]
    assert "content_scripts" not in m, (
        "the content script is injected per call with chrome.scripting, so a page is "
        "only touched when a tool runs; a static match would touch every page always"
    )


def test_the_popup_declares_no_pairing_ui(  # D28
):
    raw = POPUP_HTML.read_text(encoding="utf-8").replace("\r\n", "\n")
    # Strip HTML comments FIRST. The file explains in prose why it has no pairing
    # UI, so a naive search for "pair" matches the very comment that documents the
    # rule — the pin has to read what the popup RENDERS, not what it says about
    # itself.
    html = re.sub(r"<!--.*?-->", "", raw, flags=re.S).lower()
    for forbidden in ("<textarea", "<select", "<form", "<input"):
        assert forbidden not in html, (
            f"the popup must not contain {forbidden!r}: D28 keeps pairing approval, "
            "chat, model selection, automation UI and tool settings out of it"
        )
    # No control the user can press may offer to pair: pairing is approved in
    # Jarvis, and a second door to the same credential is a second thing to audit.
    for label in re.findall(r"<button[^>]*>(.*?)</button>", html, flags=re.S):
        assert "pair" not in label, (
            f"the popup renders a button offering to pair ({label.strip()[:60]!r}); "
            "D28 puts that in Iron Jarvis only"
        )


def test_the_node_verifier_shares_the_python_algorithm():
    """The CI check and the daemon must agree about what the id is.

    Skipped rather than failed when node is absent: this pins agreement between two
    implementations, and a machine with no node has no second implementation to
    disagree with. The check still runs in CI, where node is always installed.
    """
    assert VERIFY_ID.is_file(), "scripts/verify-id.mjs is the build-time gate for D27A"
    try:
        proc = subprocess.run(
            ["node", str(VERIFY_ID)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(VERIFY_ID.parent.parent),
        )
    except (FileNotFoundError, OSError):
        pytest.skip("node is not on PATH; the CI runner always has it")
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        pytest.fail("verify-id.mjs hung")
    assert proc.returncode == 0, (
        f"verify-id.mjs disagrees with the Python derivation:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert identity.PINNED_EXTENSION_ID in (proc.stdout + proc.stderr), (
        "the verifier must NAME the id it derived, so a failure in CI is readable "
        "without re-running anything"
    )


def test_the_private_key_is_not_in_the_repo():
    """The public key ships; the private key must not exist here at all (D27A)."""
    # TRACKED files only. An rglob also finds certifi's CA bundle inside .venv and
    # inside build output, which are not "in the repository" in the sense that
    # matters here: the question is what a push would publish.
    try:
        tracked = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(REPO),
        )
    except (FileNotFoundError, OSError):  # pragma: no cover - git is always here
        pytest.skip("git is not available")
    strays = [
        line for line in tracked.stdout.splitlines() if line.strip().endswith(".pem")
    ]
    assert not strays, (
        f"a private key is TRACKED by git: {strays}. Only the public `key` and the "
        "derived id are committed (D27A)"
    )


def test_the_extension_id_env_override_is_honoured(monkeypatch):
    """The daemon reads the id per call so a dev can load an unpacked build.

    Pinned because the origin allowlist consults ``pinned_extension_id()``: if that
    ever froze the constant at import time, the override would silently do nothing and
    a developer's own build would be refused with no way to fix it.
    """
    other = "a" * 32
    monkeypatch.setenv(identity.EXTENSION_ID_ENV, other)
    assert identity.pinned_extension_id() == other
    assert identity.extension_origin(other) == f"chrome-extension://{other}"
    monkeypatch.setenv(identity.EXTENSION_ID_ENV, "not a real id")
    assert identity.pinned_extension_id() == identity.PINNED_EXTENSION_ID, (
        "a malformed override must fall back to the pinned id, not widen the "
        "allowlist to a garbage origin"
    )
