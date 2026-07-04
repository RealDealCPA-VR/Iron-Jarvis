from __future__ import annotations

import os
import tempfile

import pytest

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.platform import build_platform


@pytest.fixture(autouse=True, scope="session")
def _isolate_cli_provider_home():
    """Point locally-installed-CLI-provider detection at an empty home for the
    whole test session.

    CLI-provider detection (``providers/cli_detect``) reads ``GROK_HOME`` /
    ``~/.grok`` off the real disk, so on a dev box where the ``grok`` CLI is
    installed and logged in, a *bare* test would otherwise see a live provider —
    making availability, onboarding, and first-run assertions depend on host
    state. Overriding ``GROK_HOME`` to an empty temp dir keeps every test
    hermetic; the real app still uses the user's real ``GROK_HOME``. Tests that
    exercise detection itself set their own ``GROK_HOME`` via monkeypatch, which
    transparently overrides this default for their duration.
    """
    prev = os.environ.get("GROK_HOME")
    tmp = tempfile.mkdtemp(prefix="ij-test-grokhome-")
    os.environ["GROK_HOME"] = tmp
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("GROK_HOME", None)
        else:
            os.environ["GROK_HOME"] = prev


@pytest.fixture
def project_root(tmp_path):
    return str(tmp_path)


@pytest.fixture
def platform(project_root):
    return build_platform(project_root)


@pytest.fixture
def orchestrator(platform):
    return Orchestrator(platform)
