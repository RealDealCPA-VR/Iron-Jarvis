"""Network-drive support in the filesystem browser (v1.73.0).

The pickers' drive rows now carry a ``kind`` (local/network/removable) so a
file-server letter is badged, and the enumerator's existence probe falls back
to ``os.stat`` for defined-but-lazy network mappings (touching one triggers
the Windows auto-reconnect). UNC listing itself needs no backend change —
``/fs/list`` already accepts arbitrary absolute paths — so these tests pin
the enumeration contract.
"""

from __future__ import annotations

import os

import pytest

from iron_jarvis.fsbrowser import browser


def test_every_drive_carries_a_kind():
    for d in browser.drives():
        assert d["kind"] in ("local", "network", "removable")
        assert d["path"] and d["label"]


def test_drive_list_shape_unchanged_plus_kind():
    out = browser.drives()
    assert out, "at least the current drive/home must be present"
    assert set(out[0].keys()) == {"path", "label", "kind"}


@pytest.mark.skipif(os.name != "nt", reason="Windows drive classification")
def test_network_kind_gets_a_network_label(monkeypatch):
    # Force the classifier: every root reads as a mapped network drive.
    monkeypatch.setattr(browser, "_drive_kind", lambda root: "network")
    out = browser.drives()
    letters = [d for d in out if d["path"].endswith(":\\")]
    assert letters, "a Windows box always has at least one drive letter"
    for d in letters:
        assert d["kind"] == "network"
        assert "(network)" in d["label"]


@pytest.mark.skipif(os.name != "nt", reason="Windows drive classification")
def test_drive_kind_never_raises():
    # Real call against every letter INCLUDING undefined ones — the classifier
    # must classify, not crash (cosmetic feature, best-effort by design).
    for letter in "CQZ":
        assert browser._drive_kind(f"{letter}:\\") in (  # noqa: SLF001
            "local",
            "network",
            "removable",
        )
