"""v1.378.0 review (IJ harness part 2, Phase 7) — a changed file the model
named by its WHOLE path is listed by its path inside the folder.

The walkthrough's gap 6: a reply in the folder directly listed its one changed
file as "C:/Users/…/ij-walkth…" (cut off by the row), where the Branch view
says "web/style.css". The model called ``edit_file`` with the absolute path;
the journal keeps the path as the tool was given it, and ``POST /chat/changes``
showed that string as the row's ``rel``. The row now says the path inside the
folder the write was made in (the ``path`` key keeps the whole path, and the
diff's own headers are unchanged). Chat and Build share this route.
"""

from __future__ import annotations

from tests.test_chat_changes_v1328 import _client, _ctx, _invoke, _post, _since

from iron_jarvis.platform import build_platform


def test_a_file_written_by_its_whole_path_is_shown_by_its_path_in_the_folder(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "shop"
    ctx = _ctx(platform, ws)
    (ws / "web").mkdir(parents=True)
    css = ws / "web" / "style.css"
    css.write_text(".buy {\n  background: #999;\n}\n", encoding="utf-8")
    since = _since()

    _invoke(platform, ctx, "write_file", {
        "path": str(css.resolve()), "content": ".buy {\n  background: #2e7d32;\n}\n",
    })

    (c,) = _post(_client(platform), since=since)["changes"]
    assert c["rel"] == "web/style.css"
    assert c["name"] == "style.css"
    # The whole path is still there for the press that opens the file.
    assert c["path"].replace("\\", "/").endswith("/shop/web/style.css")
    assert c["status"] == "modified" and (c["added"], c["removed"]) == (1, 1)


def test_a_file_named_inside_the_folder_keeps_its_short_path(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "shop"
    ctx = _ctx(platform, ws)
    (ws / "notes.txt").write_text("a\n", encoding="utf-8")
    since = _since()
    _invoke(platform, ctx, "write_file", {"path": "notes.txt", "content": "b\n"})
    (c,) = _post(_client(platform), since=since)["changes"]
    assert c["rel"] == "notes.txt"
