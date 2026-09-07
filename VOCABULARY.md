# Vocabulary — one name per concept

The rule: a user meets **one word per idea**, everywhere — on screen, in
search, and (phase 2) in the assistant's own sentences. Before v1.113.0 the
same Notion memory was a *base* where you created it, a *source* in the filter
beside it, a *brain* in directory copy, and a *connector* in chat's "+" menu.
Nobody hit an error; they hit hesitation — and hesitation never files a bug
report, it just stops opening the app.

`dashboard/__tests__/vocabulary.test.ts` enforces this table. If you're naming
something new, look here first; if the idea isn't here, add it here in the same
PR that ships it.

## The canon

| Concept | The one name | Retired words (kept as search aliases) |
|---|---|---|
| Anything recall reads | **memory base** | source, brain, LTM |
| Anything the app talks to | **connection** | integration, connector |
| An MCP server (a connection that adds tools) | **extension** | plug-in, tool pack, MCP pack |
| Anything Iron Jarvis knows how to do | **skill** | — |
| Where alerts go | **Notifications** (page) / **destination** (row) | channels |
| A destination you can talk back to | **two-way destination** | gateway, messaging platform |
| The one-tap connect gallery | **Directory** | marketplace |
| Talking it out with several agents at once (the Agents page; each conversation is a *thread*, its seated agents the *panel*) | **round-table** | — |
| The user's own Chrome/Edge, driven by Jarvis | **Your browser** | browser bridge, chrome extension, browser agent |
| A Jarvis-owned browser profile (phase 2) | **Jarvis browser** | managed browser, headless browser |
| The thing you load into Chrome so Jarvis can reach it | **browser add-on** | chrome extension, browser extension |

Two-way is a per-destination *upgrade*, not a new noun (decided in the
messaging plan, shipped v1.136.0): the toggle that creates one reads
**"Chat with Iron Jarvis from this destination"**. "Gateway" and "messaging
platform" never shipped as user-visible words, so they are not search aliases
in `lib/nav.ts` — they are retired-on-arrival; if one ever gets added to
search, it goes in as an alias only.

## Reserved

- **Pack** — reserved for the future staff-export bundle ("hand your setup to
  firm staff as a Pack", decided 2026-07-25). Do not use it for anything else;
  freeing this word was the point of renaming MCP packs away from "pack".

### plug-in → extension (v1.216.0)

From a UX review of the Tools page: *"'Plug-ins (MCP)' is insider jargon on a
first-run screen."* The word was chosen to free up "pack", which it did — but
it never told a first-time reader what the thing IS, and the page it leads is
the one place a new user meets the concept.

**extension** is the word the surrounding software already uses for "a separate
program that adds abilities" (browsers, editors, IDEs), so it arrives already
understood. The section that hosts them says *Extensions* and keeps the
protocol in a parenthetical for people who know it: MCP is the wire, not the
noun. "plug-in" joins "tool pack" as a retired word kept as a search alias, so
anyone who learned the old name still finds the page.

### Computer Control → Browser, and why the add-on is not an "extension" (v1.235.0)

Two things arrived at once and the second one nearly broke the rule above.

The page at `/computeruse` was called **Computer Control**, which described the
mechanism (a headless Chromium the app drives) rather than the thing the user
came for. It is now **Browser**, and it hosts two modes with two names: **Your
browser**, the Chrome the user is logged into, reached through an add-on they
load themselves; and **Jarvis browser**, a profile the app owns, which is phase
2 and says so. The route stays `/computeruse` — identities are contracts, see
the three-layer rule below.

The trap: the thing you load into Chrome is, in Chrome's own words, an
extension — and **extension** was already spent here on an MCP server
(v1.216.0, above). One word cannot mean two things on the same screen, and the
MCP meaning is older, load-bearing, and printed on a page users already know.
So the browser side yields: it is the **browser add-on** everywhere a user can
read it. Engineering identifiers keep Chrome's word (`extensions/chrome/`,
`extension_id`, `chrome-extension://`) because those are the wire, not the
noun.

## The three-layer rule

1. **What users read** (titles, labels, copy, tooltips, toasts) — follows this
   table, no exceptions.
2. **The wire** (API routes, request fields, config keys, source *names* like
   the builtin `brain`) — never renamed for vocabulary reasons; identities are
   contracts.
3. **What the model reads** (tool descriptions, system prompts) — should follow
   the table too, because the model's replies teach users vocabulary; migrate
   deliberately and re-test agent behaviour when you do (phase 2, open).

## Allowed exceptions

- Third-party concepts keep their own names: a **Slack channel** is Slack's
  word; a **Notion database** is Notion's.
- Identifiers shown as identifiers (the `brain` source id in a picker) are
  data, not prose.

## When search vocabulary changes

Every rename adds the OLD word to the page's `aliases` in `dashboard/lib/nav.ts`
— muscle memory must keep working forever. `nav.test.ts` pins the friction
aliases; extend it when you retire a word.
