"""12-a (build-out plan R2.5): coding work from the phone starts only in a
folder the user has USED in Iron Jarvis — a coding session's folder (by its
display name or its own name, ignoring case), a project's root, an earlier
night task's folder. Anything else is refused in words that name the user's
folders; a typed path counts only when it is one of them; two folders of one
name always ask which (a numbered pick), and the pick leads to the card.
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.comm.prompts import CODE_FOLDER_KIND, CODE_START_KIND
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Project
from iron_jarvis.harness import night as N
from iron_jarvis.harness import phone_code as PC
from tests._fakes.phone_code import ME, prompts, rig, tasks, text, used_repo
from tests._fakes.phone_files import run

pytestmark = pytest.mark.usefixtures("no_network")


def _who(platform, poll) -> PC.Asking:
    tid = poll.thread_store.resolve("tg", str(ME), "Val").id
    return PC.Asking(channel="tg", sender_id=str(ME), thread_id=tid, reply_to=str(ME),
                     store=poll.prompt_store)


def test_a_used_folder_is_found_by_its_display_name_or_its_own_name(platform, tmp_path):
    repo = used_repo(platform, tmp_path / "code" / "site-2026", name="Website")
    engine = platform.engine
    for said in ("website", "WEBSITE", "the website folder", "my Website repo", "site-2026", "“website”"):
        found = PC.find_folder(engine, said)
        assert [f["path"] for f in found] == [str(repo)], said
    assert found[0]["name"] == "Website"


def test_an_unused_folder_is_refused_in_words_naming_the_users_folders(platform, tmp_path):
    used_repo(platform, tmp_path / "code" / "website")
    used_repo(platform, tmp_path / "code" / "shop")
    elsewhere = tmp_path / "never" / "blog"
    elsewhere.mkdir(parents=True)
    engine = platform.engine
    assert PC.find_folder(engine, "blog") == []
    words = PC.none_words(engine)
    assert words.startswith("I only start coding work in folders you have used in Iron Jarvis. Yours: ")
    assert "website" in words and "shop" in words and "blog" not in words


def test_a_typed_path_counts_only_when_it_is_a_used_folder(platform, tmp_path):
    repo = used_repo(platform, tmp_path / "code" / "website")
    stranger = tmp_path / "secret" / "vault"
    stranger.mkdir(parents=True)
    engine = platform.engine
    assert [f["path"] for f in PC.find_folder(engine, str(repo))] == [str(repo)]
    assert PC.find_folder(engine, str(stranger)) == []
    assert PC.find_folder(engine, r"\\server\share\website") == []  # never resolved


def test_project_roots_and_earlier_night_tasks_count_and_an_archived_project_does_not(platform, tmp_path):
    root = tmp_path / "clients" / "acme"
    root.mkdir(parents=True)
    old = tmp_path / "old" / "legacy"
    old.mkdir(parents=True)
    night_folder = tmp_path / "jobs" / "api"
    night_folder.mkdir(parents=True)
    with session_scope(platform.engine) as db:
        db.add(Project(name="Acme Books", root=str(root)))
        db.add(Project(name="Legacy", root=str(old), status="archived"))
        db.commit()
    N.add_task(platform.engine, folder=str(night_folder), words="tidy")
    engine = platform.engine
    assert [f["path"] for f in PC.find_folder(engine, "acme books")] == [str(root)]
    assert [f["path"] for f in PC.find_folder(engine, "api")] == [str(night_folder)]
    assert PC.find_folder(engine, "legacy") == []


def test_no_folders_at_all_says_where_to_start(platform):
    assert PC.none_words(platform.engine) == PC.NO_FOLDERS


def test_two_folders_of_one_name_always_ask_which_and_the_pick_gets_the_card(platform, tmp_path):
    a = used_repo(platform, tmp_path / "code" / "website")
    b = used_repo(platform, tmp_path / "clients" / "website")
    poll, fake = rig(platform)
    fake.updates = [text(1, "/code website: fix the broken contact form")]
    res = run(poll)
    assert res[0]["status"] == "code_card" and res[0]["kind"] == CODE_FOLDER_KIND
    pick = fake.texts[-1]
    assert "More than one folder is called website. Which one?" in pick
    # Newest use first; each named by the last two parts of its path.
    assert "1. website (clients/website)" in pick and "2. website (code/website)" in pick
    assert "Reply with its number." in pick
    assert prompts(platform, CODE_START_KIND) == []  # no card yet
    # "1" picks clients/website; its card names that folder.
    fake.updates = [text(2, "1")]
    res = run(poll)
    assert res[0]["status"] == "code_card" and res[0]["kind"] == CODE_START_KIND
    card = fake.texts[-1]
    assert card.startswith("Iron Jarvis: Start coding work in website (clients/website) now?"), card
    ref = PC.read_ref(prompts(platform, CODE_START_KIND)[0])
    assert ref["path"] == str(b) and ref["path"] != str(a)
    assert ref["words"] == "fix the broken contact form"
    assert tasks(platform) == []  # still nothing started


def test_the_chat_tool_finds_folders_the_same_way(platform, tmp_path):
    used_repo(platform, tmp_path / "code" / "website")
    poll, _fake = rig(platform)
    who = _who(platform, poll)

    async def go(folder):
        return await PC.from_words(platform, who, folder, "fix the broken contact form")

    refused = asyncio.run(go("blog"))
    assert not refused.ok and "Yours: website" in refused.words
    card = asyncio.run(go("the website folder"))
    assert card.ok and card.kind == CODE_START_KIND
