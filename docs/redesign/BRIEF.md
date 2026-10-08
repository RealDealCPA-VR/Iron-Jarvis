# Iron Jarvis: Calm UI Redesign Brief

Save this file in the repo at `docs/redesign/BRIEF.md`. Both /goal runs point at it.

## Problem

The overview screen and secondary surfaces are overwhelming. Iron Jarvis can do a lot, but it shows all of it at once. Users coming from ChatGPT, Claude, and Grok expect a calm, chat-first interface where the conversation is the product and details live in settings or subpages. Chat and Build are the surfaces that get used and they work well. Everything else feels like a mountain.

## Goal

A first-time user opens Iron Jarvis, sees a familiar chat-first layout, and gets value immediately without configuring anything. Every power feature stays available, but is found through progressive disclosure, the command palette, or by asking the agent in chat.

## Non-negotiables

1. **No capability is removed.** Reflexes, goal contracts, workflows, permission gate and trust ladder, ledger and undo, documents, projects, LTM sources, skills import, profile, Telegram, MCP packs, and the event bus all stay reachable. This is a relocation and hierarchy job, not a feature cut. Any proposed removal or merge goes in AUDIT.md as an open question and only happens if VR approves it in APPROVED.md.
2. **Chat and Build keep their current behavior.** Restyle only for visual consistency.
3. **One source of truth for settings.** The Settings UI and the chat configuration tools read and write the same store, generated from one settings schema.
4. **Config changes made through chat go through the existing permission gate and write to the ledger with undo.** No parallel path that bypasses either.
5. **Secrets never appear in chat text.** When a setting needs a credential or API key, the agent shows a secure input card; the value is never echoed back in the transcript or logs.
6. **Work on branch `redesign/calm-ui`.** Commit after each plan step with a clear message.
7. **Do not edit or delete existing tests to make them pass.** The only allowed test edits are path/route updates for relocated surfaces, and each one is listed in REPORT.md.
8. **Do not narrow scope on your own judgment.** Raise scope questions as notes; do not silently drop items.
9. **Verify, don't assume.** Claims like "nobody uses X" must cite code references or usage data. Anything assumed is labeled "assumed" in AUDIT.md.

## Phase 1: Audit and proposal

Output: `docs/redesign/AUDIT.md` plus `docs/redesign/wireframes/`. Change nothing outside `docs/redesign/` in this phase.

AUDIT.md must contain these sections, in this order:

1. **Surface inventory.** A table of every top-level surface, panel, nav item, tile, and widget on the overview and in main navigation. Columns: Surface | Route / component file | What it does | Frequency tier (daily / occasional / setup-only / power-user) | Evidence (usage data, code reference, or "assumed") | Proposed new home.
2. **Usage evidence.** If the event bus, ledger, or logs record which surfaces are opened, query the last 30 days and summarize. If no such data exists, say so plainly and recommend the smallest instrumentation that would capture it.
3. **Reference patterns.** How ChatGPT, Claude, and Grok web apps structure their primary view, sidebar, settings, model selection, tool toggles, projects, and connectors. State whether this came from browsing or from prior knowledge. Note what Iron Jarvis should borrow and where it should deliberately differ.
4. **Proposed information architecture.**
   - Primary: Chat and Build.
   - Sidebar: conversations and projects. Top-level nav items, excluding the conversation list, capped at 4.
   - Command palette (Cmd/Ctrl+K) that reaches every relocated surface and every setting.
   - Settings: grouped sections (for example Models, Connections, Agents and Automation, Memory, Permissions and Ledger, Appearance). Propose the actual grouping from the inventory.
   - Contextual surfacing rules: advanced panels appear inline when relevant (a workflow card when the agent creates a workflow, a ledger entry with Undo after a config change), not as permanent tiles.
   - First-run behavior: auto-detect existing CLI logins and local model endpoints; no blocking setup screen; missing setup is requested inline only when a task needs it.
5. **Before/after map.** Every current surface and exactly where it moves.
6. **Settings parity table.** Every setting key | Current UI location | Proposed chat tool name | Permission gate tier | Contains a secret (yes/no).
7. **Wireframes** in `docs/redesign/wireframes/` (ASCII or HTML): home, sidebar, Settings, command palette, inline config confirmation card, secure credential card.
8. **Visual system.** Spacing scale, type scale, one accent color, light and dark tokens, and the rule of one primary action per view.
9. **Open questions for VR.** Proposed merges, removals, renames, and anything ambiguous.
10. **Implementation plan.** Ordered, small, independently shippable steps. Each step lists the files it touches and the acceptance tests it advances.

Phase 1 ends when AUDIT.md is complete. Do not start Phase 2. VR reviews AUDIT.md and writes `docs/redesign/APPROVED.md` with decisions on the open questions.

## Phase 2: Implementation

Start only when `docs/redesign/APPROVED.md` exists. Follow the approved plan in AUDIT.md, as amended by APPROVED.md.

### Conversational configuration layer

- Generate chat tools (for example `config.get`, `config.set`, `config.list`, or one tool per settings group) from the same settings schema that renders the Settings UI, so parity is structural, not hand-maintained.
- Every `set` call: checks the permission gate tier, writes a ledger entry with before and after values, and returns an inline confirmation card in chat showing what changed, old -> new, and an Undo button.
- For ambiguous, destructive, or credential-related requests, the agent asks one clarifying question instead of guessing.
- Natural-language examples that must work: "connect my Notion", "run this workflow every weekday at 7am", "turn on Telegram notifications", "use the local Qwen model for coding tasks", "undo that".

### Acceptance tests (must exist and pass)

Use the repo's existing test framework. If there is no end-to-end framework, add Playwright.

- **T1 Home minimalism.** On `/`: sidebar top-level nav items <= 4 (excluding the conversation list); no dashboard, stat tiles, or module grids; the composer has focus on load.
- **T2 Reachability.** A surface manifest generated from the AUDIT inventory. For each surface, the test reaches it through the command palette, and through at most 2 clicks from home.
- **T3 Fresh profile.** With an empty config directory, the app opens to chat with no blocking modal and can send a message to a mocked model.
- **T4 Config parity.** Enumerates every settings schema key and asserts a matching chat tool exists.
- **T5 Ledger and undo.** For at least one setting of each type (boolean, enum, string, schedule, connection), a chat-tool change creates a ledger entry and Undo restores the prior value.
- **T6 Secret handling.** Setting a credential through chat never writes the secret into the transcript, logs, or ledger values.
- **T7 Regression.** All pre-existing Chat and Build tests pass.
- Build, typecheck, and lint pass.

### Evidence

- Before and after screenshots in `docs/redesign/screens/` for home, Settings, and command palette, at desktop width and at 390px wide.
- `docs/redesign/REPORT.md` containing: the Definition of Done checklist with pass/fail and evidence for each item, the exact commands run and their exit codes, a list of any test files edited and why, and anything left unfinished.

## Definition of Done

- Home shows only: chat composer (with inline model selector), recent conversations/projects, and at most one lightweight entry point to Build.
- Every relocated feature is reachable within 2 clicks or via the command palette (T2).
- Every Settings UI setting can also be changed through chat, with a ledger entry and Undo (T4, T5).
- A fresh profile reaches a working chat with no required setup screen (T3).
- No capability was removed without VR's approval in APPROVED.md.
- Visual style: generous whitespace, one accent color, consistent typography, one primary action per view, works at 390px wide.
