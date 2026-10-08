# Home (`/`) — a new chat

**Today** (v1.320.0), the Simple home shows these, top to bottom:
- a page title;
- the setup checklist while setup is unfinished;
- an "Ask Jarvis anything" box that *opens* Chat;
- starter tasks;
- "Pick up where you left off";
- six "Where to go" tiles.

The full Overview (Advanced mode) adds a status panel, a 30-module grid, a
run-quality card and an admin drawer.

**Proposed:** the home **is** a chat. The composer is on the page and focused,
the model is chosen inside it, and recent chats and projects are in the
sidebar. Nothing else is permanent. Status, setup gaps and warnings appear as
one quiet line only when they apply (see AUDIT §4.5–4.6).

## Desktop (about 1280 px)

```
┌──────────────┬──────────────────────────────────────────────────────────────────┐
│ ◉ Iron Jarvis│                                              ●  [🔔]   [⌘K]        │
│              │                                                                  │
│ ( + New chat)│                                                                  │
│              │                                                                  │
│  ⌂ Build     │                                                                  │
│  ▣ Projects  │                     What can I help with?                        │
│  ◇ Everything│                                                                  │
│  ⚙ Settings  │   ┌──────────────────────────────────────────────────────────┐   │
│              │   │ Ask anything…  (focused)                                  │   │
│  Chats       │   │                                                          │   │
│  Today       │   │ [+]  [📎]  [🎙]          Claude Opus 4.8 ▾        (  ↑  ) │   │
│  · Quarterly…│   └──────────────────────────────────────────────────────────┘   │
│  · Email to …│                                                                  │
│  Yesterday   │     Summarize a PDF · Draft a reply · Tidy a folder · Plan my week│
│  · Ledger t… │                                                                  │
│              │                                                                  │
│  Projects    │                                                                  │
│  · Q3 Bookk… │                                                                  │
│  · Website   │                                                                  │
│              │                                                                  │
│ ─────────────│                                                                  │
│ VR  ·  v1.32x│                                                                  │
└──────────────┴──────────────────────────────────────────────────────────────────┘
```

- The **one primary action** is Send (`↑`). "+ New chat" is the sidebar's own
  primary; the two never compete because they sit in different regions.
- **Model selector inline** (`Claude Opus 4.8 ▾`), the same menu as today's
  composer picker. The title-bar model chip is retired; see AUDIT §9 (Q3).
- **Starter chips:** four plain everyday tasks. They fill the box and run
  nothing until you press Send (as `/chat?ask=` does today). They hide once
  the account has ten or more chats.
- **No tiles, stat cards or module grid** (brief T1).

### Conditional lines (shown only when they apply, above the composer)

```
   ┌──────────────────────────────────────────────────────────────────────┐
   │ ⚠ Replies are a scripted demo — no AI is connected.   [ Connect… ]   │   ← no model chosen
   └──────────────────────────────────────────────────────────────────────┘
   ┌──────────────────────────────────────────────────────────────────────┐
   │ ↻ 2 jobs were interrupted by the last restart.        [ Continue ]   │   ← boot reconcile
   └──────────────────────────────────────────────────────────────────────┘
   ┌──────────────────────────────────────────────────────────────────────┐
   │ ● 1 task running · 1 waiting for you                     [ Open ]    │   ← live work
   └──────────────────────────────────────────────────────────────────────┘
```

Only the most urgent line shows, and the rest fold into it ("+2 more"). A
first-run user with a signed-in Claude Code or Codex CLI, or a running local
model, sees **no** line: the model is chosen automatically on first launch
(AUDIT §4.6, open question Q7).

## Phone (390 px)

```
┌──────────────────────────────────┐
│ ☰  Iron Jarvis          🔔   ⌘K │
│                                  │
│                                  │
│      What can I help with?       │
│                                  │
│                                  │
│                                  │
│ Summarize a PDF · Draft a reply  │
│ Tidy a folder · Plan my week     │
│ ┌──────────────────────────────┐ │
│ │ Ask anything…                │ │
│ │ [+] [🎙]   Opus 4.8 ▾  ( ↑ ) │ │
│ └──────────────────────────────┘ │
└──────────────────────────────────┘
```

On a phone the composer is pinned to the bottom, the sidebar is the ☰ drawer
(see `sidebar.md`), and nothing scrolls sideways.
