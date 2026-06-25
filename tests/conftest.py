from __future__ import annotations

import pytest

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.platform import build_platform


@pytest.fixture
def project_root(tmp_path):
    return str(tmp_path)


@pytest.fixture
def platform(project_root):
    return build_platform(project_root)


@pytest.fixture
def orchestrator(platform):
    return Orchestrator(platform)
