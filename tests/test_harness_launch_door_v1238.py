"""A user must be able to REACH the harness path, not merely have it exist (v1.238.0).

Ship 4's review found the whole outward-harness path unreachable from the product: the
daemon could prepare a recipe, but nothing ever asked it to. The Launch menu already
told the user, in green, that the harness had Jarvis capabilities over HTTP. That is
this repository's oldest recurring failure — shipping the mechanism is not shipping the
feature — and it had already been made twice in this project before this ship.

WHY THE DOOR IS A NEW PANE, which is the part a future reader will want to undo.
A recipe's job is to put ``IRONJARVIS_MCP_URL`` and a pane-scoped token into the
harness's ENVIRONMENT, and the daemon merges that environment before the shell is
spawned. The ordinary Launch menu types a command into a shell that is ALREADY RUNNING,
so a pane launched that way can never receive them. The token cannot be typed instead:
a shell keeps history and scrollback, and this token drives the user's logged-in
browser. So "launch a harness with Jarvis capabilities" has to create the pane, and the
menu says so on the button rather than quietly doing something different from the row
above it.

These are SOURCE PINS over the dashboard, from Python, because the wiring they protect
is a prop handed between two components: TypeScript compiles perfectly with the prop
declared and never passed, which is precisely how the path came to be unreachable.
CRLF is normalised at the reader, needles carry no newline, and nothing is matched
inside a fixed-size window.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAGE = REPO / "dashboard" / "app" / "terminals" / "page.tsx"
PANE = REPO / "dashboard" / "components" / "terminal" / "TerminalPane.tsx"


def _read(path: Path) -> str:
    """One CRLF normalisation per file, at the reader — never at a call site."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_the_pane_offers_a_capable_launch_door():
    src = _read(PANE)
    assert "onLaunchWithCapabilities" in src, (
        "TerminalPane has no capable-launch door, so the recipe machinery is "
        "reachable from nothing and the harness path is dead in the product"
    )
    assert 'data-testid={`launch-capable-${c.id}`}' in src, (
        "the capable-launch button has no testid, so no frontend test can address it"
    )


def test_the_door_says_it_opens_a_new_pane():
    """The button must not look like the row above it, which behaves differently."""
    src = _read(PANE)
    assert "With Jarvis capabilities — opens a new pane" in src, (
        "the capable-launch section does not tell the user it creates a pane. Two "
        "buttons that look alike and behave differently is the worse half of a "
        "surface that lies"
    )


def test_only_a_cli_whose_recipe_verified_a_method_is_offered():
    """A CLI with no verified method gets no capable door — offering one would
    promise an isolation the recipe could not confirm (D18, Q04)."""
    src = _read(PANE)
    assert 'c.recipe && c.recipe.method !== "none"' in src, (
        "the capable-launch list is not filtered by a verified recipe method, so a "
        "CLI whose recipe verified nothing is offered a capability it will not get"
    )


def test_the_page_actually_passes_the_handler():
    """THE PIN THAT MATTERS. A declared-but-never-passed prop typechecks cleanly."""
    src = _read(PAGE)
    assert "onLaunchWithCapabilities={" in src, (
        "the Build page never passes onLaunchWithCapabilities, so the door renders "
        "nowhere. This is the exact shape of the defect Ship 4's review found: the "
        "mechanism present, the feature unreachable, and everything compiling"
    )
    assert "addTerminal(t.cwd, cli)" in src, (
        "the handler does not create a pane with the recipe. Anything else — a patch, "
        "a typed command — cannot deliver the environment the harness needs"
    )


def test_pane_creation_forwards_the_recipe_to_the_daemon():
    """The request must actually carry it; the daemon cannot infer a recipe."""
    src = _read(PAGE)
    start = src.index("const addTerminal")
    body = src[start : src.index("\n  );", start)]
    assert "recipe: recipe ?? undefined" in body, (
        "POST /terminals is sent without the recipe, so the daemon prepares nothing "
        "and the pane spawns with no address and no token"
    )
