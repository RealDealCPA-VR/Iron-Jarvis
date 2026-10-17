"""v1.346.0 ("media in chat"), lane X: a media-MAKING imperative ASK-arms
``generate_media`` through the one sentence->tools scorer; a LOOK at media,
a report of a request, or an edit of a file that exists arms nothing.

Driven through the REAL ``select_ask_tools`` (never membership alone — a name
in ``ASK_TIER_TOOLS`` with no scoring rule arms nothing, which is how
``history_search``, ``view_image`` and ``rename_file`` each shipped dead).
Every table row names the sentence, so a miss reads as a sentence and a name.
"""

from __future__ import annotations

import pytest

from iron_jarvis.tools.autoselect import (
    ASK_TIER_TOOLS,
    AUTO_SAFE_TOOLS,
    select_ask_tools,
    select_auto_tools,
)

TOOL = "generate_media"

#: Making imperatives — the user's verbs (make/generate/create/draw/render/
#: design/compose/produce/animate) beside the user's nouns (image/picture/
#: photo/illustration/logo/poster/thumbnail/video/clip/animation/song/music/
#: track/jingle/soundtrack/voice/narration/voiceover/sound effect/sfx).
MUST_ARM: list[str] = [
    "make a logo for my tax firm",
    "generate an image of a sunset over Seattle",
    "draw me a picture of a cat wearing a hat",
    "create a 10 second video of waves on a beach",
    "compose a jingle for the radio ad",
    "produce a voiceover for this script",
    "animate this logo so it spins",
    "design a poster for the open house",
    "render an animation of the sales chart",
    "make a sound effect of a door closing",
    "please generate a thumbnail for the video",
    "can you make me a song about taxes",
    "Make a short clip for instagram.",
    "I need a flyer. Create a poster for it",
    "write me a song about spring",
    "create a photo of a modern office lobby",
    "generate some sfx for the game menu",
    "produce a narration of this paragraph",
    "make an illustration for the children's book",
    "design a banner for the website header",
]

#: A look, a question, a report, or an edit of what exists — NEVER a making.
MUST_NOT_ARM: list[str] = [
    "show me a picture of a cat",
    "find an image of the eiffel tower",
    "what does a sunset look like",
    "the memo says to make a logo",
    "why did the video generation fail",
    "make a copy of this image",
    "make the picture bigger",
    "resize the photo to 800px",
    "convert this image to png",
    "create a folder for the images",
    "explain how to make a logo in canva",
    "describe this picture",
    "write a caption for the photo",
    "did you make the video?",
    "they want to create a video for the launch",
    "list the images in this folder",
    "what music does the client like",
    "summarize the video transcript",
    "how do I make a song in garageband",
    "open the logo file",
]


def test_generate_media_is_ask_tier_and_never_auto_safe():
    assert TOOL in ASK_TIER_TOOLS
    assert TOOL not in AUTO_SAFE_TOOLS


@pytest.mark.parametrize("sentence", MUST_ARM, ids=MUST_ARM)
def test_a_media_making_imperative_ask_arms_generate_media(sentence):
    picked = select_ask_tools(sentence)
    assert TOOL in picked, f"{sentence!r} did not ask-arm {TOOL}: {picked}"
    # ASK tier only: the auto (granting) pass never arms a spend.
    assert TOOL not in select_auto_tools(sentence), f"{sentence!r} auto-armed a spend"


@pytest.mark.parametrize("sentence", MUST_NOT_ARM, ids=MUST_NOT_ARM)
def test_a_look_a_question_a_report_or_an_edit_arms_nothing(sentence):
    picked = select_ask_tools(sentence)
    assert TOOL not in picked, f"{sentence!r} ask-armed {TOOL}: {picked}"


def test_the_tables_are_big_enough():
    assert len(MUST_ARM) >= 10 and len(MUST_NOT_ARM) >= 10
