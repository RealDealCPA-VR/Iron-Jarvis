"""v1.463.0: a memory profile's guide named by its TITLE is found.

Found on the live install: the profile named the guide "Agent Operating
Guide"; the memory base (a wiki) opens a page only by its path,
"procedures/agent-operating-guide.md". Every chat for a week was told the
guide "could not be read", and none of them followed it. The page's Check
said the same and offered no way forward.

A guide is followed as instructions, so the second way in is strict: one
result, with the same name.
"""

from __future__ import annotations

import pytest

from iron_jarvis.brain import turn as T
from iron_jarvis.brain.turn import read_guide_note

GUIDE = "# Agent Operating Guide\n\nAlways answer in plain words."


class _Wiki:
    """A base that opens notes only by path, like the user's wiki."""

    name = "wiki"

    def __init__(self, pages, hits=None, search_fails=False):
        self.pages = dict(pages)
        self.hits = hits
        self.search_fails = search_fails
        self.read_calls: list[str] = []
        self.search_calls: list[str] = []

    def read(self, ref):
        self.read_calls.append(ref)
        if ref not in self.pages:
            raise RuntimeError(f"Error calling tool 'read_page': Page not found: {ref}")
        return self.pages[ref]

    def search(self, query, k=5):
        self.search_calls.append(query)
        if self.search_fails:
            raise RuntimeError("search is down")
        if self.hits is not None:
            return self.hits
        return [
            {"title": path.rsplit("/", 1)[-1].rsplit(".", 1)[0], "snippet": "", "ref": path, "source": "wiki"}
            for path in self.pages
        ]


PATH = "procedures/agent-operating-guide.md"


def test_the_live_case_a_title_finds_the_page_at_its_path():
    wiki = _Wiki({PATH: GUIDE, "concepts/other.md": "x"})
    text, used = read_guide_note(wiki, "Agent Operating Guide")
    assert text == GUIDE and used == PATH
    assert wiki.search_calls == ["Agent Operating Guide"]


def test_a_name_that_opens_as_written_never_searches():
    wiki = _Wiki({PATH: GUIDE})
    assert read_guide_note(wiki, PATH) == (GUIDE, PATH)
    assert wiki.search_calls == []


@pytest.mark.parametrize(
    "written", ["agent-operating-guide", "AGENT OPERATING GUIDE", "agent_operating_guide.md", "Agent  Operating Guide"]
)
def test_other_spellings_of_the_same_name_find_it(written):
    wiki = _Wiki({PATH: GUIDE})
    assert read_guide_note(wiki, written) == (GUIDE, PATH)


def test_a_title_in_the_result_counts_as_the_name():
    wiki = _Wiki({"notes/n-0042.md": GUIDE},
                 hits=[{"title": "Agent Operating Guide", "ref": "notes/n-0042.md"}])
    assert read_guide_note(wiki, "Agent Operating Guide") == (GUIDE, "notes/n-0042.md")


def test_a_near_match_is_never_used():
    """The guide is followed as instructions: "…Guide (old)" is another note."""
    wiki = _Wiki({"procedures/agent-operating-guide-old.md": "old rules",
                  "procedures/operating-guide.md": "other rules"})
    with pytest.raises(RuntimeError, match="Page not found: Agent Operating Guide"):
        read_guide_note(wiki, "Agent Operating Guide")
    assert wiki.read_calls == ["Agent Operating Guide"]


def test_two_notes_with_the_name_are_not_guessed_between():
    wiki = _Wiki({"a/agent-operating-guide.md": "one", "b/agent-operating-guide.md": "two"})
    with pytest.raises(RuntimeError, match="Page not found"):
        read_guide_note(wiki, "Agent Operating Guide")
    assert wiki.read_calls == ["Agent Operating Guide"]


def test_a_search_that_fails_keeps_the_first_error():
    wiki = _Wiki({PATH: GUIDE}, search_fails=True)
    with pytest.raises(RuntimeError, match="Page not found: Agent Operating Guide"):
        read_guide_note(wiki, "Agent Operating Guide")


def test_odd_results_are_ignored():
    wiki = _Wiki({PATH: GUIDE}, hits=["text", None, {"title": "Agent Operating Guide"}, {"ref": ""}])
    with pytest.raises(RuntimeError):
        read_guide_note(wiki, "Agent Operating Guide")


def test_a_chat_turn_reads_the_guide_through_it_and_caches_the_answer():
    T.forget_guides()
    wiki = _Wiki({PATH: GUIDE})
    assert T._read_guide(wiki, "wiki", "Agent Operating Guide") == (GUIDE, "")
    assert T._read_guide(wiki, "wiki", "Agent Operating Guide") == (GUIDE, "")
    assert wiki.search_calls == ["Agent Operating Guide"]  # the second came from the cache
    T.forget_guides()


def test_a_guide_that_is_really_missing_still_says_so():
    T.forget_guides()
    wiki = _Wiki({"concepts/other.md": "x"})
    text, error = T._read_guide(wiki, "wiki", "Agent Operating Guide")
    assert text == "" and "Page not found: Agent Operating Guide" in error
    T.forget_guides()
