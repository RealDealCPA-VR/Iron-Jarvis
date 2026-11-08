"""v1.450.1: a definition question ranks the vocabulary document first.

The vocabulary is the one place each concept is named and defined; the
Handbook mentions every concept many times over and, as it grew with
v1.450.0's notes, pushed the definition of "memory base" out of the top three.
"""

from __future__ import annotations

import pytest

from iron_jarvis.guide.corpus import GuideIndex, is_definition_question


@pytest.fixture(scope="module")
def index():
    return GuideIndex()


@pytest.mark.parametrize(
    "q",
    ["what is a memory base", "What's Local only?", "what are objectives",
     "define a workflow", "what does Local only mean"],
)
def test_definition_questions_are_read_as_such(q):
    assert is_definition_question(q)


@pytest.mark.parametrize(
    "q",
    ["how do updates install", "make a workflow that runs nightly",
     "is the memory base answering", "so what is left to do"],
)
def test_other_questions_are_not(q):
    assert not is_definition_question(q)


def test_a_definition_question_puts_the_vocabulary_first(index):
    assert index.search("what is a memory base")[0][1].doc == "vocabulary"


def test_a_how_to_question_is_not_bent_toward_the_vocabulary(index):
    top = [s for _, s in index.search("how do updates install")[:3]]
    assert any(s.doc == "handbook" and "Restart to update" in s.text for s in top)
    assert all(s.doc != "vocabulary" for s in top)
