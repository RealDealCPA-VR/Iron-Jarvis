"""v1.210.0 — the CODEBASE vocabulary reaches the one sentence→tools scorer.

`tools/autoselect.select_auto_tools` is the app's ONE deterministic scorer —
both chat lanes and `agents/runtime.arm_for_task` call it — and its vocabulary
was office-document-shaped. Measured on the real selector before this wave:

    "tell me about this code base"  -> []
    "what does this project do"     -> []
    "fix the bug in main.py"        -> []

while "list the files here" armed four tools. The daily-driver thesis is
creative + CODING + office; the coding half was inaudible. Two additions close
it, both READ-ONLY by construction:

  * a codebase-noun rule (codebase/repo/repository/source code/readme/
    "this project"/"architecture of", plus read-verb + "the/this code");
  * `_CODE_FILE_RX` — a bare source/config filename over an EXPLICIT extension
    allowlist (py/ts/tsx/js/.../toml/md/txt...), which `_PATH_RX` cannot see
    because a lone filename has no separator. `com`/`net`/`org` are NOT in the
    list, so "anthropic.com" never reads as a filename.

Neither signal may ever award a `_CHANGE_TOOLS` member: asking ABOUT code is
consent to READ it. The change-verb machinery elsewhere owns mutations.
"""

from __future__ import annotations

from iron_jarvis.tools.autoselect import (
    AUTO_SAFE_TOOLS,
    _CHANGE_TOOLS,
    select_auto_tools,
)

# =============================================================================
# 1. The three measured-broken sentences now arm sensible read tools
# =============================================================================

#: The full codebase-noun selection, pinned as an ORDERED list: find first
#: (`file_search` leads, the folder rule's calibration), then read, then list.
_CODEBASE_PICK = ["file_search", "read_file", "list_files", "list_folder",
                  "read_document"]


def test_tell_me_about_this_code_base_arms_the_readers():
    assert select_auto_tools("tell me about this code base") == _CODEBASE_PICK


def test_what_does_this_project_do_arms_the_readers():
    assert select_auto_tools("what does this project do") == _CODEBASE_PICK


def test_a_named_source_file_arms_read_file_first():
    """The headline filename case: `_PATH_RX` needs a separator, so `main.py`
    scored nothing at all before `_CODE_FILE_RX`."""
    assert select_auto_tools("fix the bug in main.py") == ["read_file", "file_search"]


def test_more_codebase_phrasings_all_reach_the_readers():
    for msg in (
        "explain this code",
        "give me an overview of the repo",
        "where is the config loaded in the source code?",
        "read the readme",
        "what's the architecture of this repository?",
        "walk me through the code",
    ):
        picked = select_auto_tools(msg)
        assert picked, f"{msg!r} armed nothing"
        assert "file_search" in picked, f"{msg!r} -> {picked}"
        assert set(picked) <= AUTO_SAFE_TOOLS, msg


def test_config_filenames_count_too():
    assert select_auto_tools("open Cargo.toml and package.json") == [
        "read_file", "file_search",
    ]


# =============================================================================
# 2. No false positives: office/tax/small-talk sentences stay exactly as they were
# =============================================================================

def test_a_tax_question_stays_empty():
    assert select_auto_tools("what's a 1099-NEC?") == []


def test_an_email_improvement_ask_is_unchanged():
    # Measured [] before this wave; still [] — "e-mail"'s dots are not a
    # filename ("mail" is not an allowlisted extension) and no codebase noun
    # appears.
    msg = "improve this e-mail: thanks for sending the K-1, we'll file by the 15th"
    assert select_auto_tools(msg) == []


def test_a_domain_is_not_a_filename():
    """`com` is deliberately absent from the extension allowlist, so a bare
    domain must not arm `read_file`. The sentence's OTHER words ("check ... the
    docs") legitimately arm document/knowledge tools — pinned byte-identical to
    the pre-v1.210 output so the filename signal provably added nothing."""
    picked = select_auto_tools("check anthropic.com for the docs")
    assert "read_file" not in picked
    assert picked == ["file_search", "read_document", "recall", "ltm_search"]


def test_domains_alone_arm_nothing():
    assert select_auto_tools("is anthropic.com down?") == []


def test_small_talk_stays_empty():
    assert select_auto_tools("hey, how are you today?") == []


def test_the_bare_noun_code_needs_a_read_verb():
    """"the code" alone is this practitioner's word for the IRC, and "edit the
    code" is pinned elsewhere — only a read-shaped verb in front of the bare
    noun qualifies."""
    assert select_auto_tools("what does the code say about depreciation?") == []


# =============================================================================
# 3. Read-shaped codebase asks never arm a mutator
# =============================================================================

def test_no_mutating_tool_from_the_codebase_signals():
    for msg in (
        "tell me about this code base",
        "what does this project do",
        "fix the bug in main.py",
        "explain this code",
        "give me an overview of the repo",
        "read the readme",
        "what's the architecture of this repository?",
        "open Cargo.toml and package.json",
        "summarize index.tsx and app.py for me",
    ):
        leaked = set(select_auto_tools(msg, cap=99)) & _CHANGE_TOOLS
        assert not leaked, f"{msg!r} armed mutator(s) {sorted(leaked)}"


def test_fix_the_bug_does_not_arm_write_file():
    # "fix" + a filename is a READ grant here; write_file needs the
    # change-verb machinery, which this sentence does not trip.
    assert "write_file" not in select_auto_tools("fix the bug in main.py", cap=99)


def test_everything_stays_inside_the_safe_set():
    for msg in ("audit the codebase", "list every file in the repo",
                "review src/main.rs and lib.rs"):
        assert set(select_auto_tools(msg, cap=99)) <= AUTO_SAFE_TOOLS, msg


# =============================================================================
# 4. Determinism — same input, same list, every time
# =============================================================================

def test_selection_is_deterministic():
    for msg in (
        "tell me about this code base",
        "what does this project do",
        "fix the bug in main.py",
        "find the invoice files in my folder and summarize them",
    ):
        first = select_auto_tools(msg)
        for _ in range(3):
            assert select_auto_tools(msg) == first, msg


# =============================================================================
# 5. The office vocabulary keeps its exact pre-v1.210 outputs
# =============================================================================

#: Recorded from the real selector IMMEDIATELY BEFORE the codebase rules were
#: added (2026-08-23), then re-run after: byte-identical. If a later scoring
#: change moves one of these, re-record deliberately — these are the existing
#: vocabulary's contract, not this wave's.
_OFFICE_PINS = [
    ("list the files here",
     ["file_search", "list_folder", "read_document", "list_files"]),
    ("search the web for the latest Python release",
     ["web_search", "code_search", "code_load", "web_fetch"]),
    ("draft a one-page report as a docx about our Q3 numbers",
     ["write_document", "read_document", "file_search", "write_file"]),
    ("find the invoice files in my folder and summarize them",
     ["file_search", "list_files", "list_folder", "read_document"]),
    ("extract the tables from the pdf of my notes and check the formulas in "
     "the sheet",
     ["read_document", "excel_profile", "file_search", "recall", "excel_query",
      "ltm_search"]),
    ("create an excel of my top clients and their fees",
     ["write_document", "excel_query", "excel_profile", "excel_read",
      "file_search", "read_document"]),
    ("what is the total of the Amount column in book.xlsx",
     ["excel_query", "excel_profile", "excel_read", "file_search",
      "read_document"]),
    ("what do we know about the henderson account?",
     ["recall", "ltm_search", "ltm_append"]),
    ("what does our wiki say about the S-corp election?",
     ["recall", "ltm_search", "file_search"]),
    ("redact the pii in these returns",
     ["redact_scan", "redact_pii", "read_document", "file_search"]),
    ("merge these pdfs into one file",
     ["pdf_arrange", "pdf_split", "file_search", "read_document"]),
    ("change the fee for Belmont to 3000", ["excel_edit", "excel_read"]),
    ("process these 15 client documents and give me a summary",
     ["file_search", "read_document"]),
]


def test_the_existing_office_vocabulary_is_untouched():
    for msg, want in _OFFICE_PINS:
        got = select_auto_tools(msg)
        assert got == want, f"{msg!r}: {got} != pinned {want}"
