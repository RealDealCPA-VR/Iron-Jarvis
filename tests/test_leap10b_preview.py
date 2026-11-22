"""10-b (build-out plan R2.4): the one line an approval shows on the phone.

Built by code from the card itself, never by a model: a coding session's
card in its own words, else what the call sets out to do with the card's own
cost note or two of its redacted arguments. One line, defanged (no web
address that can be pressed, nothing Slack reads as a link or a mention),
masked again, at most 300 characters. Local only work gets none (its alert
is the title only), and a bare ask keeps the old line exactly.
"""

from __future__ import annotations

from iron_jarvis.comm.notifier import format_event
from iron_jarvis.comm.prompts import PREVIEW_MAX, approval_preview


def _alert(payload: dict) -> str:
    return format_event({"type": "approval.requested", "payload": payload})


def test_two_redacted_arguments_say_what_the_call_does():
    line = approval_preview({"tool": "shell", "args": {"command": "dir Intake", "cwd": "Documents",
                                                       "timeout": 30}})
    assert line == "An agent wants to run a command: command dir Intake, cwd Documents"


def test_where_it_acts_is_shown_and_a_files_own_text_never_is():
    # A write's arguments are a path and the text written: the preview names
    # the path, never what goes in the file (it would cross to the service).
    line = approval_preview({"tool": "write_file", "args": {
        "content": "Notes from the March meeting with the client", "path": "Documents/Intake/notes.txt"}})
    assert "Documents/Intake/notes.txt" in line
    assert "March meeting" not in line and "content" not in line
    line = approval_preview({"tool": "edit_file", "args": {
        "old_string": "draft words", "new_string": "final words", "path": "Documents/a.md"}})
    assert "Documents/a.md" in line and "words" not in line
    # Only bulk text to show: no preview, the old line stays.
    assert approval_preview({"tool": "write_file", "args": {"content": "only the text"}}) == ""


def test_the_cards_own_cost_note_wins_over_the_arguments():
    line = approval_preview({"tool": "image_generate", "args": {"prompt": "a cat"},
                             "cost_note": "This uses about 4 credits of your Pixio plan."})
    assert line.endswith(": This uses about 4 credits of your Pixio plan.")
    assert "a cat" not in line


def test_a_batch_says_how_many_calls():
    line = approval_preview({"tool": "rename_file", "args": {"path": "Documents/Intake/a.pdf"},
                             "count": 14})
    assert "(14 calls)" in line and "Documents/Intake/a.pdf" in line


def test_a_coding_sessions_card_keeps_its_own_words():
    payload = {"tool": "shell", "surface": "build", "title": "Fix the nav bar", "args": {"command": "x"}}
    assert approval_preview(payload).startswith("Your coding session “Fix the nav bar” is waiting")


def test_one_line_defanged_masked_and_capped():
    line = approval_preview({"tool": "web_fetch", "args": {
        "url": "https://user:not-a-pass@evil.example.test/path\n<!channel> <@U123>",
        "note": "x" * 500,
    }})
    assert "\n" not in line and len(line) <= PREVIEW_MAX
    assert "https://" not in line and "hxxps://" in line and "evil[.]example[.]test" in line
    assert "not-a-pass" not in line  # masked again
    assert "<" not in line and ">" not in line


def test_local_only_work_gets_no_preview():
    payload = {"tool": "shell", "args": {"command": "type secrets.txt"}, "local_only": True}
    assert approval_preview(payload) == ""
    assert "secrets.txt" not in _alert(payload)


def test_the_alert_leads_with_the_preview_and_a_bare_ask_keeps_its_line():
    assert _alert({"tool": "shell", "args": {"command": "dir"}}) == (
        "⏸ An agent wants to run a command: command dir — approve from the dashboard bell, or "
        "reply here: approve / deny.")
    assert _alert({"tool": "shell", "args": {}}) == (
        "⏸ An agent is asking to use shell — approve from the dashboard bell, or reply here: "
        "approve / deny.")


def test_a_preview_never_breaks_the_alert():
    assert approval_preview(None) == ""
    assert approval_preview({"tool": "shell", "args": "not a dict"}) == ""
    assert approval_preview({"tool": "shell", "args": {"x": object()}})  # str() of anything
