# Vision plan: a memory that compounds, an app that improves itself, many Jarvises

Written 2026-11-14 at v1.490.4. The owner chose three leaps and said to go full
steam on the first:

- **Leap 3. A memory that compounds.** Iron Jarvis keeps its own record of
  every piece of work (which folder, which files, what was decided), and
  brings it to every new request by itself.
- **Leap 5. It improves itself.** Each night it measures where it fell short
  on the owner's real work and offers a fix.
- **Leap 7. Many Jarvises.** Several installs pass work, results and chosen
  memory to each other under clear permissions.

Each section says what exists today (read from the code on 2026-11-14), the
phases, and the decisions that are the owner's.

## Rules that hold for all three

1. **Local first.** Nothing here sends anything anywhere new. A chat or
   coding session switched to Local only keeps even its title away from work
   that leaves the network (the rule `recent_activity` already follows).
2. **Suggest, never act.** Anything the app learns about the owner or wants
   to change in itself is offered; the owner accepts or declines. A confirmed
   item is the only kind that reaches a prompt.
3. **The record decides, not the model.** A stored fact names its evidence
   (the chat, the run, the file). A model-written recap is kept only where the
   ledger supports it (the compaction rule, v1.153.0).
4. **Each phase is a release** with its own tests and its Handbook section.

---

## Leap 3. A memory that compounds

### What exists today

| Piece | What it is | Gap |
|---|---|---|
| Preferences, lessons | How the owner likes things done | Not about the work itself |
| Memory bases, memory profiles | Notes the owner or Jarvis saved | Only what someone chose to save |
| History index | Keyword search over chat messages and run summaries | Coding sessions are left out; no folder, no files |
| Project recent activity | Up to 8 title lines | Only inside a Project; the owner works by folder |
| Run summaries, archive summaries | One line or one file per piece of work | Never read back into a later chat |

Nothing records, per folder, what was done there across chats, coding
sessions and agent runs. Nothing knows a client. Nothing keeps a recap of
finished work.

### Phase 1 (this release): the work record

- A table, `workentry`: one row per piece of work that has a folder. Kinds:
  a chat, a coding session (Chat › Code), a coding session in a real tool
  (Build › Code), an agent run. Each row holds the title, the folder, the
  files it made or changed, how it ended, and when.
- Built only from what Iron Jarvis already stores. No model call. No file on
  disk is read. Refreshed right before it is used (a long first build is
  finished in the background). A deleted chat or run leaves the record, and
  each piece is checked against its source again before a chat is told of it.
- **Every chat and coding session with a folder is told what was done there
  before** (newest six: titles and file names), and a message that names
  earlier work elsewhere ("the March reconciliation for Hartleys") is shown
  where that work lives. The matching rule is strict so that one client's
  work is not pulled into an unrelated chat (shipped in v1.491.0 after an
  independent review found the first rule too loose).
- A new part of the Memory page, **Your work, by folder**: every folder Iron
  Jarvis has worked in, what happened there, with a link back to each piece.

### Phase 2: recaps (shipped in v1.494.0)

When a piece of work ends, a short recap is written (what was done, what was
decided, what is still open) and checked against the record before it is
kept. It is written by the model that did the work; a Local only piece is
recapped only by a model on this network. Recaps are added under the titles
that later chats in the same folder are told.

As built: chats and Chat › Code sessions, ten minutes after they go quiet;
one model call each, at most 30 a day, only for work that changed after the
update. A line is kept only when every number, quote, file name, path and
e-mail address in it is in the conversation. A recap is told only while the
conversation is unchanged, and only to chats in the same folder (an
independent review found the first cut told one client's recap to a chat in
another client's folder). A model that bills per use writes no recap.
"Forget this folder" (the owner's decision 2) is on the Memory page. Not
done: recaps for agent runs and Build › Code; recaps inside a Project's own
recent activity.

### Phase 3: clients and matters

Iron Jarvis notices names that recur across folders and work ("Hartley's")
and asks once: "Is Hartley's a client?" A confirmed client gets a page: its
folders, its work, its files, the decisions on record, the people named.
Preferences can then belong to a client ("Hartley's wants cash basis").

### Phase 4: recall everywhere

The same record reaches agent runs, helpers, the real coding tools in
Build › Code (through the Memory box), the phone, and a `work_history` tool
for questions like "what did we do for Hartley's in March?".

### Phase 5: it gets better with age

Monthly and yearly roll-ups per client ("what changed since last year's
return"), open items carried forward, and a "pick up where we left off" line
when a folder is opened again.

### Decisions for the owner

1. May Phase 2's recaps be written by a cloud model for work that was done
   on a cloud model? (Recommended: yes; Local only work stays local.)
2. Should a folder be removable from the record ("forget this folder")?
   (Recommended: yes, in Phase 2, as a two-press action.)

---

## Leap 5. It improves itself

### What exists today

Eleven kinds of proposal already exist, each with its own screen: coach edits
for an agent, team-note habits, coding lessons, skill drafts, tool requests,
memory housekeeping, preferences, and more. Measures exist too: the hands-off
rate, the fix request (22 kinds of shortfall), reviewer findings.

Three things are missing:

1. **One place.** No screen lists every proposal together.
2. **A loop.** The coach, the fix request and the hands-off numbers only run
   when someone presses a button. Nothing ties a measured shortfall to a
   proposal by itself.
3. **Proof it helped.** Only team notes are measured before and after.

### Phases

- **Phase 1: the Improvements inbox.** One page listing every waiting
  proposal of every kind, each with its evidence and its Accept / Decline.
  No new proposal types; one read over the stores that exist.
- **Phase 2: the nightly review.** In the night window, on the idle pacing
  the coding lessons already use (never near an account's limit, never for
  Local only work on a cloud model), Iron Jarvis reads the day's shortfalls
  and runs the proposers that fit: the coach for an agent that struggled, a
  skill draft for work done the same way three times, a preference for a
  repeated correction. One morning line: "3 suggestions from yesterday".
- **Phase 3: did it help?** Every accepted change is measured before and
  after, the way team notes are, and a change that made things worse says so.
- **Phase 4: fixes to Iron Jarvis itself.** For shortfalls only a code change
  can fix, the nightly review writes the fix request and, when the owner has
  turned Self-development on, queues it as a night task on its own branch
  with tests. The owner reviews and merges. Nothing merges by itself.

### Decisions for the owner

1. May the nightly review spend plan usage? (Recommended: yes, with a daily
   cap and only below 70% of a window, as the coding lessons do.)
2. Phase 4 touches Iron Jarvis's own code. (Recommended: off until Phases
   1-3 have run for two weeks.)

---

## Leap 7. Many Jarvises

### What exists today

One PC can hand a task to another through the task door; the answer and up
to five files come home. Setting it up takes manual copying on both PCs and
two restarts. There is one door token, no list of peers, no per-peer
permissions, no shared memory rules, and no way to move a whole chat or
coding session.

### Phases

- **Phase 1: pairing.** "Pair with another Jarvis": one PC shows a short
  code, the other types it, and both sides are set up in one step (each gets
  its own token for the other, each a name). A Peers list shows who is
  paired, when each last answered, and a Remove press.
- **Phase 2: permissions per peer.** For each peer: may it send tasks, may it
  read the work record (which folders), may it ask questions back. Everything
  is off until switched on.
- **Phase 3: shared memory with rules.** A memory base two installs both
  use, with who wrote each note, and a check that stops one install from
  overwriting another's change.
- **Phase 4: hand over a whole piece of work.** Send a chat or a coding
  session, with its files, to a peer; it arrives as a piece of work there,
  with the record of where it came from.
- **Phase 5: the firm.** A staff list, work passed between people, and later
  a client-facing Jarvis that can only see that client's folder.

### Decisions for the owner

1. Pairing needs the "Let other computers reach this PC" setting on both
   PCs. (Recommended: the pairing screen says so and turns it on with one
   confirmation, restart included.)
2. Between offices, the PCs must reach each other (Tailscale is already
   treated as the owner's own network). (Recommended: document Tailscale as
   the supported way; no cloud relay.)

---

## Order of work

1. Leap 3 Phase 1 (this release).
2. Leap 3 Phase 2, then Leap 5 Phase 1 (both read the same end-of-work data).
3. Leap 7 Phase 1.
4. Leap 3 Phase 3, Leap 5 Phase 2.
5. The rest, in the order each section lists.
