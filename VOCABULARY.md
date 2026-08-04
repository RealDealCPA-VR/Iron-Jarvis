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
| An MCP server (a connection that adds tools) | **plug-in** | tool pack, MCP pack |
| Anything Iron Jarvis knows how to do | **skill** | — |
| Where alerts go | **Notifications** (page) / **destination** (row) | channels |
| A destination you can talk back to | **two-way destination** | gateway, messaging platform |
| The one-tap connect gallery | **Directory** | marketplace |

Two-way is a per-destination *upgrade*, not a new noun (decided in the
messaging plan, shipped v1.136.0): the toggle that creates one reads
**"Chat with Iron Jarvis from this destination"**. "Gateway" and "messaging
platform" never shipped as user-visible words, so they are not search aliases
in `lib/nav.ts` — they are retired-on-arrival; if one ever gets added to
search, it goes in as an alias only.

## Reserved

- **Pack** — reserved for the future staff-export bundle ("hand your setup to
  firm staff as a Pack", decided 2026-07-25). Do not use it for anything else;
  freeing this word was the point of renaming MCP packs to plug-ins.

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
