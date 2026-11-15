"""The dependency graph in ``docs/DEPENDENCIES.md`` matches the code.

``scripts/gen_dep_graph.py`` reads every import, route and dashboard path and
writes the page; an agent planning an upgrade reads it (or asks ``--impact``).
A page that has drifted from the code would send that agent the wrong way, so:

* the committed page is exactly what the script writes today;
* the parser gets the cases that decide an edge right (relative imports,
  load-time vs inside a function, ``TYPE_CHECKING``-only);
* known real edges are on the page, so an empty or broken scan cannot pass.

Files only: no daemon, no network, no model.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load():
    path = REPO / "scripts" / "gen_dep_graph.py"
    spec = importlib.util.spec_from_file_location("gen_dep_graph", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_dep_graph"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def dep():
    return _load()


@pytest.fixture(scope="module")
def graph(dep):
    return dep.Graph()


def test_the_page_is_what_the_script_writes_today(dep, graph):
    committed = (REPO / "docs" / "DEPENDENCIES.md").read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert committed == dep.render(graph), (
        "docs/DEPENDENCIES.md is out of step with the code: "
        "run `uv run python scripts/gen_dep_graph.py` and commit the page")


def test_relative_imports_resolve_and_load_time_is_split_from_lazy(dep):
    src = (
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "from ..core import ids\n"
        "from . import store\n"
        "try:\n"
        "    from iron_jarvis.tools import registry\n"
        "except ImportError:\n"
        "    registry = None\n"
        "if TYPE_CHECKING:\n"
        "    from iron_jarvis.agents import runtime\n"
        "def later():\n"
        "    from iron_jarvis.providers import router\n"
    )
    tree = ast.parse(src)
    name = "iron_jarvis.workmap.clients.wall"
    every = dep._imports_in(tree, name, is_pkg=False)
    eager = dep._imports_in(dep._eager_part(tree), name, is_pkg=False)
    # `from ..core import ids` inside workmap/clients/ is iron_jarvis.workmap.core
    assert "iron_jarvis.workmap.core.ids" in every
    assert "iron_jarvis.workmap.clients.store" in every
    assert "iron_jarvis.tools.registry" in eager            # inside a top-level try
    assert "iron_jarvis.providers.router" in every          # inside a function...
    assert "iron_jarvis.providers.router" not in eager      # ...is not load time
    assert "iron_jarvis.agents.runtime" not in eager        # TYPE_CHECKING is type-only


def test_known_edges_are_on_the_page(graph):
    every = graph.pkg_edges()
    eager = graph.pkg_edges(eager=True)
    # settings/records_clients.py imports the clients record at load time.
    assert "workmap" in eager["settings"]
    # Both chat lanes import the clients wall (daemon -> workmap).
    assert "workmap" in every["daemon"]
    # core imports nothing at load time from another package: it is the base.
    assert not eager.get("core")
    # Every package the rules table claims is on the page.
    page = (REPO / "docs" / "DEPENDENCIES.md").read_text(encoding="utf-8")
    for pkg in graph.packages():
        assert f"| `{pkg}` |" in page, pkg


def test_routes_and_dashboard_callers_are_found(graph):
    assert "src/iron_jarvis/daemon/routes/clients.py" in graph.routes["/clients"]
    assert "/clients" in graph.dash_calls["dashboard/lib/clients.ts"]
    assert "dashboard/lib/clients.ts" in graph.dash_imports["dashboard/components/clients/ClientPage.tsx"]


def test_impact_names_the_files_and_the_tests(dep, graph):
    out = dep.impact(graph, "src/iron_jarvis/workmap/clients/wall.py")
    assert "src/iron_jarvis/daemon/chat_turn.py" in out
    assert "src/iron_jarvis/daemon/routes/chat.py" in out
    assert "tests/test_clients_wall_v1499.py" in out
    lib = dep.impact(graph, "pypdf")
    assert "src/iron_jarvis/documents/readers.py" in lib
    # Git Bash turns "/clients" into a Windows path; the query undoes that.
    route = dep.impact(graph, "C:/Program Files/Git/clients")
    assert route.startswith("Route /clients")
    assert "Nothing called" in dep.impact(graph, "no_such_thing")
