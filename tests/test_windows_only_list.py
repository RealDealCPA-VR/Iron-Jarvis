"""The Windows-only list names real tests, and the skip is for other systems.

A name that no longer exists would skip nothing and hide nothing, but a stale
list is how a renamed test starts failing on Linux again with no one knowing
why; a wrong matcher could skip a test on Windows.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.windows_only import WINDOWS_ONLY, is_windows_only

TESTS = Path(__file__).parent


def _test_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")
    }


@pytest.mark.parametrize("file", sorted(WINDOWS_ONLY))
def test_every_listed_test_exists(file):
    path = TESTS / file
    assert path.is_file(), f"{file} is listed but is not a test file"
    names = WINDOWS_ONLY[file]
    if "*" in names:
        return
    missing = sorted(set(names) - _test_names(path))
    assert not missing, f"{file}: listed but not found: {missing}"


def test_the_matcher_reads_a_node_id():
    file = "test_app_alias_v14071.py"
    name = WINDOWS_ONLY[file][0]
    assert is_windows_only(f"tests/{file}::{name}")
    assert is_windows_only(f"tests/{file}::{name}[C:\\x-True]")
    assert is_windows_only(f"tests\\{file}::TestClass::{name}")
    assert not is_windows_only(f"tests/{file}::test_something_else")
    assert not is_windows_only(f"tests/test_not_listed.py::{name}")


def test_on_windows_nothing_is_skipped_by_the_list(pytestconfig):
    import os

    if os.name != "nt":
        pytest.skip("this checks the Windows side")
    import tests.conftest as c

    class _Item:
        nodeid = f"tests/test_app_alias_v14071.py::{WINDOWS_ONLY['test_app_alias_v14071.py'][0]}"
        marks: list = []

        def add_marker(self, m):
            self.marks.append(m)

    item = _Item()
    c.pytest_collection_modifyitems(pytestconfig, [item])
    assert item.marks == []
