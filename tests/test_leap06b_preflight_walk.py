"""Leap 06-b review 2: every tool a rehearsal HOLDS that checks a path in its
own code has a preflight, so the report can say "would be refused" where the
real run would refuse.

Walks the REAL registry: for each tool ``rehearsal.holds`` holds, reads the
source of its class and every class it inherits from below ``Tool``; when
that source runs a path check (``safe_path``, the documents' write and read
resolvers, ``fs_read_ok``), the tool must declare ``workspace_paths`` or
override ``Tool.rehearsal_preflight``. A new tool that writes a file it
names fails here until it does.
"""

from __future__ import annotations

import inspect
import re

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.tools import rehearsal as R
from iron_jarvis.tools.base import Tool

#: Reviewed: a held tool whose path check reads nothing it is given.
EXEMPT = {
    "delegate_remote": "its safe_path checks the names of files a remote agent sends back; in a "
                       "rehearsal nothing is sent (SIDE_DOORS remote_handoff), so nothing comes back",
}

PATH_CHECKS = re.compile(r"\b(safe_path|_resolve_write_path|_resolve_read_path|fs_read_ok)\(")


def _own_source(tool: object) -> str:
    parts = []
    for cls in type(tool).__mro__:
        if cls in (Tool, object) or not issubclass(cls, Tool):
            continue
        try:
            parts.append(inspect.getsource(cls))
        except (OSError, TypeError):
            pass
    return "\n".join(parts)


def _has_preflight(tool: object) -> bool:
    return bool(getattr(tool, "workspace_paths", ())) or (
        type(tool).rehearsal_preflight is not Tool.rehearsal_preflight
    )


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    app = create_app(str(tmp_path_factory.mktemp("walk") / "home"))
    return app.state.platform.registry


def test_every_held_tool_that_checks_a_path_has_a_preflight(registry):
    missing = []
    checked = []
    for name in sorted(registry.names()):
        tool = registry.get(name)
        if tool is None or not R.holds(tool):
            continue
        if PATH_CHECKS.search(_own_source(tool)):
            checked.append(name)
            if not _has_preflight(tool) and name not in EXEMPT:
                missing.append(name)
    assert missing == [], f"held tools that check a path with no rehearsal preflight: {missing}"
    # The walk sees the file tools at all (it is not reading nothing).
    assert set(EXEMPT) <= set(checked), "an exemption for a tool the walk no longer sees"
    for name in ("write_file", "multi_edit", "apply_patch", "notebook_edit", "batch_documents", "stack_apply"):
        assert name in checked, (name, checked)


def test_the_walk_sees_a_new_tool_without_one():
    from iron_jarvis.tools.base import safe_path  # noqa: F401  (named in the source below)

    class Sneaky(Tool):
        name = "sneaky_write"

        async def execute(self, args, ctx):  # pragma: no cover - never run
            safe_path(ctx.workspace, args["path"])

    tool = Sneaky()
    assert R.holds(tool)
    assert PATH_CHECKS.search(_own_source(tool))
    assert not _has_preflight(tool)
