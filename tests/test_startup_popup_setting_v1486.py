"""Asking Iron Jarvis to stop opening when the PC starts offers the settings
tools (v1.486.0).

Seen in the live runs: "stop it popping up every time the pc starts please i
only want it when i click it" offered nothing, and the model asked what was
popping up. The "Start Iron Jarvis with Windows" row's own phrases ("start at
login", "launch at startup") never appear in such a sentence. One shape in
``settings/trigger._EXTRA_REFERENCES`` reads it: stop / don't (let) / no
longer, then it / the app / Jarvis (or nothing), then opening / popping up /
starting, then when / every time the PC / computer / Windows starts. A program
the user names is not Iron Jarvis and matches nothing.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from iron_jarvis.settings import trigger as T
from iron_jarvis.settings.tools import wants_settings

ASKS = [
    "stop it popping up every time the pc starts please i only want it when i click it",
    "don't let Jarvis open when my computer boots",
    "stop opening when windows starts",
    "please stop the app starting every time my laptop turns on",
    "Iron Jarvis should no longer launch when the computer starts up",
    "do not let it come up each time the machine restarts",
]
NOT_ASKS = [
    "stop Outlook popping up when the pc starts",
    "my pc starts slowly",
    "the kids stop playing when the computer starts acting up",
    "when the pc starts, open my email",
    "stop and think: when windows starts, which programs load?",
]


def _shape():
    return [p for p in T._EXTRA_REFERENCES if "longer" in p.pattern][0]


@pytest.mark.parametrize("text", ASKS)
def test_the_request_offers_the_settings_tools(text):
    assert _shape().search(text), text
    assert wants_settings(text)


@pytest.mark.parametrize("text", NOT_ASKS)
def test_another_program_or_an_ordinary_sentence_does_not_match_the_shape(text):
    assert not _shape().search(text), text


def test_the_shape_is_linear_on_pathological_text():
    """A child process with a deadline (a hang detector, never a speed bar)."""
    code = textwrap.dedent(
        """
        import time
        from iron_jarvis.settings import trigger as T
        pat = [p for p in T._EXTRA_REFERENCES if "longer" in p.pattern][0]
        N = 200_000
        for text in ("stop " * (N // 5), "stop it popping up " * (N // 19),
                     "stop opening " + "x " * (N // 2) + " when the pc",
                     "don't let " * (N // 10)):
            t0 = time.perf_counter(); pat.search(text)
            print(round(time.perf_counter() - t0, 3), flush=True)
        """
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.count("\n") == 4, out.stdout
