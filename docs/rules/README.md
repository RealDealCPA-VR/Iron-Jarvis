# The agent rules: how they are organised, and what the 2026-11-14 split did

This folder holds the rules AI agents follow when they change Iron Jarvis.
This page explains the layout and records every decision made when the old
single file was split. It is written by hand.

## The layout

| File | What it is | Who reads it |
|---|---|---|
| `CLAUDE.md` (repository root) | The rules for EVERY change, the north star, commands, the release flow. Under 300 lines. | Every agent session, in full, at its start. |
| `docs/rules/<area>.md` (18 files) | The full rules for one area of the code. Starts with a generated contents list. | An agent about to change that area. |
| `CLAUDE.md` in `src/`, `dashboard/`, `desktop/`, `packaging/`, `extensions/`, `tests/`, `scripts/`, `.github/` | A generated pointer: which area file covers which code. | Loaded by the agent tool when it reads a file in that folder. |
| `docs/rules/ARCHIVE-2026-11-14.md` | The old file, unchanged. Frozen. | Nobody, normally. It is the proof that nothing was lost. |
| `docs/rules/SPLIT-RECORD-2026-11-14.md` | A generated table: every piece of the old file, its old lines, a hash of its text, where it went. | Anyone asking "where did this rule go?" |
| `scripts/agent_rules_split_table.tsv` | The sorting decisions: one row per bullet of the old file. | The split script. |

## Why

The old `CLAUDE.md` was 19,951 lines and 1,429,770 bytes, about 357,000 tokens.
Every agent session loaded all of it before its first word. 18,771 of those
lines were one section, "Hard rules", holding one long bullet per release.

## What the split did, step by step

1. **Kept the old file whole.** `ARCHIVE-2026-11-14.md` is byte for byte the
   root `CLAUDE.md` of commit `69d133ee` (v1.496.0). Its SHA-256 (LF line ends)
   is in the split record, and `tests/test_agent_rules_layout.py` checks it.
2. **Cut the old file into pieces** at every `## ` heading and every top-level
   bullet: 372 pieces (364 bullets and 8 other parts). Joined in order, the
   pieces are the old file exactly; the script refuses to go on otherwise.
3. **Moved each bullet, unchanged, to exactly one area file.** Which file is
   decided by `scripts/agent_rules_split_table.tsv`. That table was made in two
   passes: a first sorting by counting which code each bullet names, then a
   hand review of all 361 headlines, which changed 105 of them. The split was
   first written against v1.491.1; when it landed on top of v1.496.0, the three
   bullets added in between (v1.493.0's coding check loop, v1.495.0's chat and
   agent-run check loop, v1.496.0's improvements inbox) were sorted by hand into
   `build.md`, `chat.md` and `memory.md`.
   (The v1.494.0 bullet was already in the table.)
4. **Listed cross-references.** When a bullet also says a lot about a second
   area, its headline is listed in that area file under "Also touches this
   area". These lists are pointers only; the text exists once.
5. **Checked it.** `split_agent_rules.py verify` re-reads the written files and
   requires every moved piece to be found exactly once, unchanged, in its own
   file and in no other. Result at split time: 366 pieces moved, 366 found once.
6. **Rewrote the root file short** (see "The root file" below).

### Where the parts that are not bullets went

| Old part | Where it went |
|---|---|
| Title and introduction | Rewritten in the root file. |
| "The three processes" | Root file, same facts, as a table and three lines. |
| "Commands" | Root file (short). The full old text is in `ci.md`. |
| "Release flow" | Root file (seven steps). The full old text is in `ci.md`. |
| "Hard rules" heading | Root file keeps a "Hard rules" section with the cross-cutting ones. |
| "Map (where things live)" | Root file keeps a short map. Each old map entry is a bullet in its area file. |
| "Verifying against the LIVE app" | Root file, same commands. |
| "Direction (the product thesis)" | Replaced by "North star" in the root file. The old paragraph is in the archive (lines 19239-19248). |

### Bullets per area file

See the table at the top of `SPLIT-RECORD-2026-11-14.md`.

## The root file: every decision

The starting point was a short draft another agent proposed. Compared with that
draft:

**Changed**

| Draft line | What was done | Why |
|---|---|---|
| "Code changes land on a branch and never auto-merge." | Removed. | Not true as written: releases push straight to master, and coding sessions work in the folder by default. The true, narrower rules are in `build.md`. |
| "Every write the user can see gets an undo entry." | Replaced with "Never promise an Undo the journal cannot perform." | Commands and some spreadsheet changes have no undo. The old file's own rule is the second wording. |
| "The focus surfaces are Chat and Build." | Replaced by the "North star" section. | It narrowed the product, which the owner has said not to do. |
| "Bump with the script, then `uv lock`." | "The script runs `uv lock`." | The script already does. |
| "his daily driver" | "their daily driver" | The owner's pronouns are not stated anywhere. |

**Added** (each was in the old file and applies to every change)

- Tests and scratch daemons never reach the owner's live services.
- No test value shaped like a real credential.
- A held-out test set is never tuned to.
- The two chat lanes change together.
- A secret is never a tool argument, a log line, an event or an error message.
- A skipped human gate is proven through the layer that would have refused.
- Every child process starts through `core/procs`; a native binding that can
  wait inside Windows runs in a child process.
- A release must sign; a Handbook section means regenerating the changelog.
- Fetch before you bump and before you push; no pull requests. (This one came
  from the owner's standing instructions, not the old file.)
- Worktree rules: `uv sync --extra dev` first; never `git stash`.

**Kept from the draft as written:** the rest, including its "Adding a rule"
limits, which are now enforced by `scripts/gen_agent_rules.py --check`.

### The north star

The "North star" section is NEW text. It was drafted from four sources and
needs the owner's confirmation:

- the old "Direction" paragraph (daily driver; connect surfaces; never trade
  trust for magic; suggest, don't act; everything reviewable);
- rules the old file repeats across many releases (honest refusals, Local
  only, undo, "approve once", one word per thing, chat reaches everything);
- `docs/VISION-PLAN-2026-11-14.md` (compounding memory, self-improvement, many
  Jarvises);
- the owner's recorded decisions (client data and security first; do not
  narrow the product; folder-first; plain words).

Its order is a judgement: trust first, then the user's data, then
recoverability, then finishing the job. If the owner wants a different order,
change the list in the root file; nothing else depends on it.

## Other files changed in the same commit

| File | Change |
|---|---|
| `AGENTS.md` | Rewritten. It told agents to run the whole suite locally and said "43/43 routes"; both were out of date. |
| `.claude/session-start.sh` | Its reminder said "edit all 3 version files"; it now names the bump script. |
| `tests/test_bulk_job_repair_v1177.py` | One test read the release-gate sentence from `CLAUDE.md`; it now reads the root file plus `ci.md`. |
| `docs/rules/.ignore` | Keeps the archive out of ripgrep searches, so a search does not hit every rule twice. |

No product code changed: nothing under `src/`, `dashboard/`, `desktop/` or
`extensions/` except the generated pointer files.

## How to work with these files

- **Add a rule to an area:** add a top-level bullet under `## Rules` in the
  area file, starting with a bold one-sentence rule, then run
  `uv run python scripts/gen_agent_rules.py`.
- **Check everything is in step:** `uv run python scripts/gen_agent_rules.py --check`.
- **Find where an old rule went:** search `SPLIT-RECORD-2026-11-14.md` for its
  headline, or search the area files for any phrase from it.
- **Add a package under `src/iron_jarvis/`:** add it to `PACKAGE_AREAS` in
  `scripts/gen_agent_rules.py`; the check fails until you do.
- **Prune:** removing or merging an old bullet in an area file is allowed and
  is an ordinary reviewed edit. The archive keeps the original.

## If master's old-style CLAUDE.md changed before this landed

Other sessions were still adding bullets to the old file while this was built.
To bring a newer old file in, on the split's branch:

```bash
git show origin/master:CLAUDE.md > /tmp/old-claude.md
uv run python scripts/split_agent_rules.py split /tmp/old-claude.md --again
uv run python scripts/gen_agent_rules.py
uv run python scripts/gen_agent_rules.py --check
```

A bullet the table does not know stops the run and is named with a suggested
area: add its row to `scripts/agent_rules_split_table.tsv` and run again. Then
update the numbers on this page. Once the split is on master, never run
`split` again: the area files are edited by hand from then on.

## Undoing it

`git revert` of the split's commit restores the old root file and removes
these files. Or copy `ARCHIVE-2026-11-14.md` back over the root `CLAUDE.md`.
