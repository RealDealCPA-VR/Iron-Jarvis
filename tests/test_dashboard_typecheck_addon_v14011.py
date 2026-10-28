"""v1.401.1: a dashboard test that reaches the add-on's ``chrome`` global is
kept out of the dashboard's type check.

v1.400.9 added ``dashboard/__tests__/addon-picker-v1400-9.test.ts``, which
imports ``extensions/chrome/src/content/picker.ts`` and ``content/index.ts``.
Both use the browser's global ``chrome``. The dashboard has no types for it,
and its tsconfig includes every ``**/*.ts``, so ``npx tsc --noEmit`` (the Tests
workflow's Typecheck step) and ``pnpm build`` (the installer job) followed the
import and failed with "Cannot find name 'chrome'". vitest never type-checks, so
every local suite stayed green, and no installer was published for v1.400.9 or
v1.401.0.

The rule this pins: a dashboard test whose imports reach add-on code that uses
``chrome`` is listed in ``dashboard/tsconfig.json`` ``exclude`` (vitest still
runs it). A test that imports only add-on code free of ``chrome`` (the browser
identity helper) stays type-checked.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
ADDON_SRC = (ROOT / "extensions" / "chrome" / "src").resolve()

_IMPORT = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(\s*|^\s*import\s+)["']([^"']+)["']""",
    re.MULTILINE,
)
_CHROME = re.compile(r"(?<![\w.$])chrome\s*\.")


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8", "replace").replace("\r\n", "\n")


def _code(text: str) -> str:
    """The text with comments removed, so a comment naming chrome.* is not use."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"(?m)//.*$", "", text)


def _resolve(spec: str, importer: Path) -> Path | None:
    if not spec.startswith("."):
        return None
    base = (importer.parent / spec).resolve()
    for cand in (base, base.with_suffix(".ts"), base.with_suffix(".tsx"),
                 base / "index.ts", base / "index.tsx"):
        if cand.is_file():
            return cand
    return None


def _addon_reach(test_file: Path) -> set[Path]:
    """Every add-on source file the test reaches through relative imports."""
    seen: set[Path] = set()
    todo = [
        hit for spec in _IMPORT.findall(_code(_read(test_file)))
        if (hit := _resolve(spec, test_file)) is not None and ADDON_SRC in hit.parents
    ]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        for spec in _IMPORT.findall(_code(_read(f))):
            hit = _resolve(spec, f)
            if hit is not None and ADDON_SRC in hit.parents:
                todo.append(hit)
    return seen


def _uses_chrome(files: set[Path]) -> list[str]:
    return sorted(f.relative_to(ROOT).as_posix() for f in files if _CHROME.search(_code(_read(f))))


def _excluded() -> set[str]:
    config = json.loads(_read(DASH / "tsconfig.json"))
    return {str(e).replace("\\", "/") for e in config.get("exclude", [])}


def _dashboard_tests() -> list[Path]:
    return sorted(p for p in (DASH / "__tests__").rglob("*") if p.suffix in (".ts", ".tsx"))


def test_a_test_that_reaches_the_chrome_global_is_kept_out_of_the_type_check():
    excluded = _excluded()
    offenders = []
    for test_file in _dashboard_tests():
        uses = _uses_chrome(_addon_reach(test_file))
        rel = test_file.relative_to(DASH).as_posix()
        if uses and rel not in excluded:
            offenders.append(f"{rel} reaches {uses}")
    assert not offenders, (
        "these dashboard tests import add-on code that uses the `chrome` global; "
        "`npx tsc --noEmit` and `pnpm build` will fail on them. Add each to "
        "dashboard/tsconfig.json `exclude`: " + "; ".join(offenders)
    )


def test_the_guard_sees_both_kinds_of_add_on_import():
    """Anti-vacuity: the picker test reaches chrome (and is excluded), while the
    browser-identity test reaches add-on code that never touches chrome (and is
    still type-checked)."""
    picker = DASH / "__tests__" / "addon-picker-v1400-9.test.ts"
    reached = _uses_chrome(_addon_reach(picker))
    assert "extensions/chrome/src/content/picker.ts" in reached
    assert "extensions/chrome/src/content/index.ts" in reached
    assert "__tests__/addon-picker-v1400-9.test.ts" in _excluded()

    identity = DASH / "__tests__" / "browser-edge-v1259.test.tsx"
    reach = _addon_reach(identity)
    assert any(p.name == "browser-identity.ts" for p in reach)
    assert _uses_chrome(reach) == []
    assert "__tests__/browser-edge-v1259.test.tsx" not in _excluded()


def test_only_tests_are_excluded_never_app_code():
    """An exclude is a hole in the type check: it is for a test file that reaches
    add-on code, never for the dashboard's own pages or libraries."""
    for entry in _excluded() - {"node_modules", "scripts"}:
        assert entry.startswith("__tests__/"), entry
        assert (DASH / entry).is_file(), f"stale exclude: {entry}"
