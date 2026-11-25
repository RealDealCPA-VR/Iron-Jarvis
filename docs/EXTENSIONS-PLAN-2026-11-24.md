# Extensions plan: less in the model's head, more it can reach

Written 2026-11-24 against master v1.570.0. It replaces the pasted brief
"Adaptive MCP Runtime". It is eight small releases, one
at a time, each with one number or one test that says it is done.

**Built so far:** E0 (the baseline, 2026-11-25) and E1a (v1.572.0).

## The aim, in one sentence

Jarvis can use any number of extensions, hand them files, and let them run long
jobs, while the model is shown only what this reply needs and you are asked
only for consent.

## What is already true (so this plan does not build it again)

| The brief asked for | Where it lives today |
|---|---|
| Don't load every tool | An extension's tools load only when you tick it: at most 6 extensions and 24 tools, chosen by your message (`_resolve_connectors`, `daemon/chat_turn.py`) |
| A lookup with no extra model call | `find_capability` (`capability_search/`): word-ranked, no model. Extension tools are left out on purpose until phase 20-b |
| An extension asks you mid-run, and shows progress | Since v1.324.0 (`daemon/mcp_turn.py`, `mcp/interact.py`): forms only, in an attended chat, declined at night |
| Big results stay out of the context | Hard rule since v1.159.0 |
| A new write tool is held back; approval is tied to the exact arguments | v1.299.0, v1.520.0 |
| Local only keeps a local-only extension's data on this PC | v1.428.0 (`mcp/tools.py`, `local_only_refusal`) |
| Skills are named to the model, read only when one fits | v1.473.0 (`skills/offer.py`, `skill_load`) |
| Counting what tool definitions cost | `_tool_spec_tokens` (`daemon/chat_turn.py`) already counts them |

## What the standard looks like today (read 2026-11-24)

- Jarvis announces revision **2024-11-05** (`mcp/client.py`). The current one is
  **2026-07-28**, four revisions later.
- 2026-07-28 is a rewrite, not a step. The opening handshake (`initialize`) is
  gone, the session id is gone, and an extension no longer calls back to ask a
  question: it answers "I need input" and Jarvis asks again with the answer.
  Extensions written for the old shape will be around for years, so Jarvis must
  speak both.
- "Ask a model for me" (sampling) is deprecated in 2026-07-28. Jarvis keeps
  answering it for old extensions and builds nothing new on it.
- Skills from an extension: an official add-on, status Final
  (`io.modelcontextprotocol/skills`).
- Long jobs: an add-on since 2026-07-28 (`io.modelcontextprotocol/tasks`).
  Its page was not read for this plan; read it before E6.
- Sending a file: still a draft (`io.modelcontextprotocol/files`), expected to
  be final around the end of 2026. Not in any published revision.

## The releases, in order

| Release | What you get | Done when | Keeping it calm |
|---|---|---|---|
| **E0. The baseline** (done 2026-11-25, result below) | Nothing new on screen. A script reads what tool definitions cost per reply with 0, 1, 3 and 6 extensions ticked, as a share of what the model is sent, from the live app's own list and its saved usage records (read-only, no paid calls). | Four numbers are written into this file. If definitions are under 5% of a reply at the cap, E1 is about reaching the right tool, not saving, and this plan says so. | No surface. |
| **E1a. Save what each reply was sent** (done, v1.572.0) | Nothing new on screen. Each chat reply's saved row keeps how much of its prompt the provider served from its cache, and the names and estimated size of the tools it was sent. Names and counts only. | Tests on both chat lanes go red when the note or the save is taken out. `scripts/measure_tool_definitions.py` reads the result. | No surface. |
| **E1b. The lookup knows your extensions** (phase 20-b, already approved) | Ask in plain words and Jarvis finds the right tool even in an extension you did not tick. It offers one card: "Use Notion for this chat?" Yes is the same as ticking it. | A fresh set of requests, never used for tuning: the right tool is in the first five at least as often as for built-in tools. A test shows an unticked extension's tool is refused at the registry until the card is answered. | One card per extension per chat. No new page. An extension's own description is search words only, never shown as an instruction. |
| **E2. Today's standard, the familiar shape** | Old-shape extensions get the newest revision of that shape (2025-11-25): results that come as data arrive as data, and an extension can send you to a web page to sign in or pay. | Jarvis's test extension passes on 2024-11-05 and 2025-11-25. Three real extensions you use start and answer on the packaged app. | The web-page card names the extension and the site's host, and opens the page only on your press. |
| **E3. Today's standard, the new shape** | Extensions written for 2026-07-28 work. A question from an extension no longer holds a connection open, so it can wait while you are away, and survive a restart. | A test extension in the new shape: asked, app restarted, answered, the call finishes once. Old-shape extensions still pass E2's tests. | The same card as today. A waiting question sits where approvals already wait. |
| **E4. Skills from an extension** | An extension can bring its own how-to. Jarvis sees only its name and one line until a reply needs it, then reads it. | A test: connecting an extension with 50 skills adds no skill text to a reply. A skill whose text says "ignore your rules, run this" changes nothing and arms nothing. | Listed on the Skills page under the extension's name. Turning the extension off turns its skills off. No new switch. |
| **E5. Hand a file to an extension** | "Send this PDF to X" sends the file itself. The model sees the name and size, never the pages. | A test: a 20 MB file reaches a test extension and none of its bytes are in the model's input. Local only on: refused by name. A file outside the chat's folder: refused. | One card: the file, its size, the extension, and whether it stays on this PC or goes to a named site. The record keeps name, size and fingerprint, never the content. |
| **E6. Long jobs** | An extension's job can run past today's 10-minute limit. The chat carries on, the job shows where running work already shows, and it is still there after a restart. | A test: a 15-minute job, the app restarted in the middle, one result, the job never started twice. Stop cancels it. A job whose extension vanished reads "could not be confirmed", never "done". | No new page. Quiet until it finishes or needs you. |

## E0 result (measured 2026-11-25 on the live app, v1.567.0)

Read-only, no model call. Rerun with `scripts/measure_tool_definitions.py`.
Definition sizes are Iron Jarvis's own estimate (3.6 characters a token), not a
tokenizer's count. Reply sizes are the providers' own counts.

**What is installed:** 5 extensions with 34 tools (a sixth is switched off), and
251 built-in tools.

| Extension | Tools | Definitions, in tokens |
|---|---|---|
| fetch | 1 | 397 |
| hermes-brain | 8 | 925 |
| sequentialthinking | 1 | 929 |
| everything | 15 | 1,698 |
| brave_search | 9 | 11,053 |

**The four numbers:**

| Ticked | Definitions sent with every model call |
|---|---|
| 0 | 0 from extensions |
| 1 | 397 to 11,053 (median 929) |
| 3 | 2,250 to 13,679 (median 12,375) |
| All 5 (the 24-tool cap applies) | up to 14,308 (15,000 without the cap) |

Six ticked could not be measured: only five are running.

**Against a typical reply** (229 saved chat replies):

| | Median sent per model call | Adding the median extension | Adding Brave search | Adding all, capped |
|---|---|---|---|---|
| Your own models (199 replies) | 5,489 | 14.5% of the call | 66.8% | 72.3% |
| Claude (30 replies) | 35,527 | 2.5% | 23.7% | 28.7% |

**What it means:**

1. **The 5% test is failed by a wide margin, so E1b is about saving as well as
   reach.** On your own models, one extension can be two thirds of everything
   the model is sent.
2. **The cost is one extension, not the count.** Brave search is 74% of all
   extension definitions (11,053 of 15,000). Its nine tools run 476 to 2,075
   tokens each, almost all of it in the list of inputs, not the description.
   The other four extensions together are 3,949.
3. **The cap counts tools, not size.** 24 tools can be 3,019 tokens or 14,308.
   E1b should give extensions room by size.
4. **E1b's number to beat:** Brave search ticked costs 11,053 tokens a call
   today. Sending each tool's name and one line, and the full definition only
   for the tool the model picks, should be under 2,000.

**What could not be measured, because it is not saved:**

- How much of each prompt the provider served from its cache. The saved reply
  rows hold input and output only.
- Which tools each saved reply was sent, so the built-in share of a reply is
  unknown. All 251 built-ins together would be 56,233 tokens; a typical call on
  your own models is 5,489 in total, so only a small part is sent.
- Whether the 229 saved replies had an extension ticked. The shares above
  assume they did not.

E1a (v1.572.0) saves the first two per chat reply (two counts and a list of
tool names, never content), so E1b's saving is measured on real replies, not
estimated. A few days of use after v1.572.0 gives the "before".

## The rules each release keeps

1. **The roster is still the contract.** Finding a tool is not permission to
   run it. A found tool runs only after a tick or a card, and the gate stays in
   the tool registry (`tools/registry.py`). Agent runs, schedules and the night
   shift keep consent at the time they are set up, as today.
2. **An extension's words are data.** Tool descriptions, skill text, questions
   and results never instruct the model. A hint from an extension ("this tool
   only reads") may make Jarvis stricter, never looser.
3. **Tools are added to a chat, not swapped each reply.** Changing the tool
   list mid-chat throws away the provider's saved prompt, which costs more than
   it saves. A tool found in reply 3 stays for the chat.
4. **A write is never sent twice.** The new standard tells a client to send a
   request again after a broken connection. For a tool that changes something,
   Jarvis instead says it could not confirm the result (see choice C2).
5. **A file leaves this PC only by a card that names where it goes.** The
   upload address is checked the way `web_fetch` addresses are. The upload's
   own password is never logged, shown or put in an event.
6. **Both chat lanes change together** (`daemon/chat_turn.py` and
   `daemon/routes/chat.py`), and everything new is verified on the packaged
   app, not only from source.
7. **Four numbers, not forty.** Tool-definition cost per reply; right tool in
   the first five; share of the prompt served from the provider's cache; task
   success on the existing reach set. All measured, none estimated, shown in
   the usage view that exists.

## What this plan leaves out, on purpose

- A new "runtime" or second registry. The registry, the lookup, the skills
  offer and the work record already exist; each release extends one of them.
- Nine new settings. Each release is on by default and its off switch is the
  extension's own.
- A new page or panel.
- Anything built on "ask a model for me".
- Sending files before an extension declares it can take them. Until then
  Jarvis says plainly that the extension cannot take a file.

## Choices for you

| | The choice | Recommended |
|---|---|---|
| C1 | Where this goes in the build-out order | E0 and E1a are done. E1b in 20-b's own slot. E2 to E6 after the phase in hand, one at a time. |
| C2 | A write tool whose connection broke mid-call | Never send it again. Say "could not confirm", and offer "Try again" as your press. |
| C3 | Build E5 before the file standard is final | Wait for Final, unless an extension you use already declares it. E6 can go first. |
| C4 | How long a "Use this extension?" yes lasts | This chat only. A new chat asks again unless you ticked it. |

## Not yet confirmed

- The exact calls for long jobs (start, cancel, the list of states). Read the
  tasks add-on page before E6.
- Whether the new shape's "ask again with the answer" carries everything needed
  to resume after a restart. E3's test is written first to find out.
- Which revision the extensions you use today speak. E2 starts by listing them.
- Whether an extension's skills are offered the same way under both shapes.

## Sources

- https://modelcontextprotocol.io/specification/2026-07-28/changelog
- https://modelcontextprotocol.io/specification/2025-11-25/changelog
- https://modelcontextprotocol.io/specification/2025-06-18/changelog
- https://modelcontextprotocol.io/specification/draft/changelog
- https://modelcontextprotocol.io/extensions/skills/overview
- https://modelcontextprotocol.io/seps/2640-skills-extension
- https://modelcontextprotocol.io/community/file-uploads/charter.md
- https://blog.modelcontextprotocol.io/posts/exploring-file-uploads-and-downloads-in-mcp/
