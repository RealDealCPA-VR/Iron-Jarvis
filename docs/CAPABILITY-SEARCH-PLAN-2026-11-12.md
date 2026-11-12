# Capability search: chat finds the right tool by asking, not by word tables

Plan written 2026-11-12 against v1.477.2. Nothing here is built yet.

## Phase 0 result (measured 2026-11-12, v1.477.2)

The gate is passed: the plan goes ahead. Today's word tables reach far less
than the 90% that would have stopped it.

| | Reached |
|---|---|
| All held-out requests | **50 of 164 (30%)** |
| English | 48 of 135 (36%) |
| Spanish, French, Portuguese, German | 2 of 29 (7%) |
| Jobs a tool does | 44 of 138 |
| Jobs that need a page | 5 of 14 |
| Jobs Iron Jarvis does not offer (the true "not offered" line) | 1 of 12 |
| Near-misses offered a tool that changes something | 7 of 99 |

Of the 114 misses:

- 68 were offered **nothing at all**;
- 17 were offered a **wrong** tool that changes something (for example "dig
  up when the bagel place opens on Sundays" was offered the schedule tools
  and no web search);
- 10 could at least explain or open the right page.

16 of the 47 jobs were never reached by any wording: making or pausing an
agent, disconnecting an app, keeping a chat private, a free local model, a
goal, a night task, a new personality, reviewing preferences, a second
account, starting with the PC, the update channel, saving a skill, voice,
another computer, and an internet service calling in.

How the set was made, and its limits:

- 47 jobs and their right answers were written down first
  (`tests/fixtures/capability_heldout_v1478/`).
- The requests were written by Claude Haiku from an empty folder, given
  only plain job descriptions. It never saw the patterns. Every label was
  checked by hand; 9 unclear rows were dropped.
- No model ran. A reply with no tool can still answer in words, so 30% is
  "can do it from chat", not "gives a useful reply".
- One writer, one pass, a fresh install with no apps connected. A second
  writer would move the number a few points either way.
- The earlier figure in this plan ("one phrasing in three is missed") came
  from sets written by people who had read the patterns. This set shows the
  true miss rate on new wording is about two in three.

`tests/test_capability_reach_v1478.py` re-runs the measurement in about
three seconds, with no model.

## Phase 1 result (built 2026-11-12, to ship as v1.478.0, off by default)

The index and the read-only lookup tool `find_capability` are built. With the
setting **Feature lookup** off (the default), chat is unchanged. An
independent reviewer found 11 defects before release; all are fixed.

On the same held-out set, the right entry is in the lookup's first five
answers for:

| Query | Right entry found |
|---|---|
| The user's own words, unchanged | 67 of 164 (41%) |
| Search words written by a model that saw only the tool's description | **123 of 164 (75%)** |
| Non-English requests, model's search words | 27 of 29 |
| Word tables OR lookup | 134 of 164 (82%) |

This is a search result. No real chat ran, and nothing the lookup finds
joins a reply yet; that is Phase 2.

Seen in the misses and deliberately NOT fixed, because the set is held out:
settings with long help text crowd out tools on words like "how often"; an
older agent tool outranks the one chat should use; "the app is broken"
matches the connected-app tools; three tools lack the everyday words for
what they do. These go into Phase 2 with a fresh test set.

## The problem, measured

Which tools a chat reply gets is decided before the model answers, by
matching the user's words against hand-written patterns.

- 14 source files hold those matchers, with **443 compiled patterns**
  (counted today: `tools/autoselect.py` alone has 128).
- They gate 52 automatic tools, 54 record tools (schedules, projects,
  agents, apps, destinations and so on), every setting, and the page doors.
- The app's own records say how well this works on wording nobody wrote a
  pattern for:
  - page steps: about one new phrasing in three is missed (v1.436.0);
  - skills: 11 of 16 fitting messages were offered the right skill
    (v1.476.0);
  - near-misses: 11 of 60 still armed a tool that changes something
    (v1.434.0).
- The patterns are English only.
- Each miss has been fixed by adding a pattern. That is why the count is 443.

When the tables miss, the model has no tool for the request. It then answers
"I can't do that", or reaches for the command line. Both are wrong answers
for a non-technical user.

## The idea

Give the model one small tool that is always there: **look up what Iron
Jarvis can do**. The model describes the job in its own words ("run this
every Monday", "rename these files", "connect my calendar"). Iron Jarvis
answers from an index of everything it can do, and the matching tools join
the reply for the next step.

Why this is better than more patterns:

- The model rewords the request for us. "Cada lunes a las 7" and "every
  Monday at 7" become the same lookup.
- A lookup returns the best few matches, so a near-miss still finds the tool.
- The model can look again with other words. A pattern cannot.
- Every lookup is a recorded miss of the word tables. We get a free list of
  what real users ask for that the tables do not know.

The word tables stay as the fast path. A request they recognise works
exactly as today, with no extra step.

## What already exists and is reused

- **Joining a tool in the middle of a reply.** `offer_script_tool` and
  `offer_file_tool` (v1.476.0) already add a tool for the next round, in
  both chat lanes, with the deny, ceiling and Auto-tools checks. This plan
  turns that into one shared function.
- **Offer a short list, let the model load one.** Skills work this way since
  v1.476.0 (`skills_offer_block`, `skill_load`).
- **Search without a model.** The Guide's search (`guide/corpus.py`) is
  word-ranked, deterministic and needs no embedding model.
- **One plain sentence for every tool.** `mission.card_words` has words for
  every built-in tool, and a test fails when a new tool has none.
- **True "not offered" lines and doors.** `daemon/page_doors.py` already
  holds them, each pinned to the code that makes it true.
- **Ranking an app's tools by the message.** `ToolRegistry.pick_for_message`.

## Safety rule (the part that must not move)

In chat, a tool the word tables arm runs without a card if it is an
allow-tier tool. The user's own words are the consent. A tool the MODEL
asks for has no such consent: a web page could tell the model to look up
"delete" and use what it finds.

So:

1. **A lookup never grants a change.** A tool found by lookup that changes
   anything joins the reply as *ask first*: the usual card, every time, in
   the stream lane. In the POST lane (the phone, the API), which cannot show
   a card, it does not join and the model is told to say "do this from the
   chat page".
2. **Read-only tools found by lookup join and run**, as they do today. They
   change nothing.
3. **Every existing gate still applies**: the user's *Never run*, low trust
   (a reply that read flagged content gets no new tools), Local only (a tool
   that reaches outside keeps its card or stays out), a weak model's tool
   budget, the browser sidebar's own tool list, and the safety cards that
   always ask.
4. **Bounded**: at most 3 tools per lookup and 2 lookups per reply.
5. **Third-party text is scanned.** A connected app's tool description is
   that app's words. It goes through the same check as a skill's
   description before the model sees it.

Each rule gets a test that drives the real stream lane and the real
permission check, the way v1.270.1 requires ("prove the call ran through
the layer that would have refused it").

## Phases

Each phase ships by itself and is useful by itself.

### Phase 0: measure today (no product change)

Write a held-out set the patterns have never seen:

- 150 requests, reworded by someone who has not read the patterns. 30 of
  them not in English.
- 100 near-misses that must not arm a tool that changes something.
- The 30 walkthrough requests from the chat-first plan.

Run them through today's arming functions (no model needed) and record the
reach rate. **Gate:** if today's tables already reach 90% or more, stop
here. The plan is not worth its cost.

### Phase 1: the index and the lookup, answers only

- `capabilities/index.py`: one entry per tool, per setting, per record
  kind, per connected app, per door and per "not offered" line. Built at
  start, rebuilt when apps or settings change.
- A read-only tool, working name `find_tool`. It returns names, one line
  each, and whether the tool asks first.
- Nothing joins yet. The model can already answer "yes, I can do that from
  here" or the true "not offered" line, where today it guesses.

### Phase 2: found tools join the reply

- One shared join function, replacing the two special cases from v1.476.0.
- Both lanes, lock-step. The safety rules above.
- The reply's work line says "Looked up what it can do".

### Phase 3: settings and records

- Every settings row is in the index with its label, other names and help.
  A lookup for a setting joins the settings reader and, by its own tier,
  the right change tool.
- The 54 record tools the same way.

### Phase 4: apps

- A connected app's tools are found by lookup (today: only when the message
  names the app).
- An app that is not connected answers with its Connect door.

### Phase 5: stop growing the tables

- New wording gaps are closed by improving index entries, not by a new
  pattern.
- After a month, read the lookup log: which requests needed a lookup most.
  Decide then whether any table can be retired. None is deleted in this
  plan.

### Later, not in this plan

Coding sessions, agent runs and the browser sidebar keep their own tool
lists for now.

## How we will know it worked

Measured on the Phase 0 set, with a real model, before and after:

| Measure | Today | Target |
|---|---|---|
| Right tool reachable, held-out requests | 50 of 164 (30%) | 95% or more |
| Same, non-English requests | 2 of 29 (7%) | 90% or more |
| A change that ran with no card and no matching words | 0 | 0, by construction and by test |
| Near-misses offered a tool that changes something | 7 of 99 | no worse |
| Extra time on a reply the tables already handle | none | none |
| Extra time on a reply that needs a lookup | no tool at all | one extra step |

Two models are measured: Claude Haiku on the plan, and the owner's own
model server. A weak local model may not call the lookup at all; if so, the
result is "no gain for that model", and the tables still serve it.

## Risks

- **The model does not look.** Mitigation: a short map of what Iron Jarvis
  can do (about 15 lines) in the stable part of the prompt, so the model
  knows a lookup is worth making. Its token cost is measured in Phase 1.
- **More cards.** A request the tables miss now shows a card where today it
  did nothing. That is the intended trade, and it is the owner's decision 1.
- **One more step of delay** on the replies that need it.
- **Two lanes drifting apart.** The join is one function both lanes call,
  with a lock-step test.
- **Cost of the measurement.** About 400 short replies on the Claude plan
  and the same on the local server.

## Decisions for the owner

1. **A change found by lookup always shows a card first.** Recommended: yes.
   The other choice (run it like a table-matched tool) lets a web page talk
   the model into a change.
2. **With Auto tools off, the lookup still answers but joins nothing.**
   Recommended: yes.
3. **Local models get the lookup too.** Recommended: yes, and measure it.
4. **Spend plan usage on the Phase 0 and final measurements.** Recommended:
   yes, on Haiku.

## Size

Phase 0 is small: a test set and a script. Phases 1 to 4 are about one wave
each of the usual shape (a builder and an independent reviewer per lane).
Each phase is its own release.
