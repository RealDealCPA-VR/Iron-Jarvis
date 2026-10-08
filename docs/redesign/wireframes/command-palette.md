# Command palette (Ctrl+K)

**Today** (v1.320.0), the palette already searches pages, deep links,
actions, themes, skills, chat threads, projects and full-text chat history.

**Proposed** additions:
- every **setting** (by label and alias), opening its Settings group with the
  row highlighted;
- a **"Do it in chat"** row for settings that can be changed in chat
  (pre-filled, never sent);
- every surface listed on **Everything**, which the T2 manifest checks.

```
┌────────────────────────────────────────────────────────────────────┐
│ 🔍  telegram                                                        │
├────────────────────────────────────────────────────────────────────┤
│ SETTINGS                                                            │
│  ⚙  Telegram notifications         Connections › Notifications  ↵   │
│  💬 "Turn on Telegram notifications" — do it in chat                │
│ PAGES                                                               │
│  ◇  Notifications (channels)        Everything › System              │
│ ACTIONS                                                             │
│  ⚡ Send a test notification                                         │
│ CHATS                                                               │
│  ·  "Set up the Telegram bot" — 3 days ago                          │
├────────────────────────────────────────────────────────────────────┤
│ ↑↓ move · ↵ open · Tab: do it in chat · Esc close                   │
└────────────────────────────────────────────────────────────────────┘
```

- The groups always appear in this order: **Settings, Pages, Actions,
  Skills, Projects, Chats, History**. Within a group the existing ranker
  (`lib/palette.ts`) orders the rows.
- **"Do it in chat"** opens a new chat with the sentence typed into the box
  (`/chat?ask=`) and sends nothing.
- With an empty query the palette shows: Recent pages, New chat, Open Build,
  Settings.

## Phone (390 px)

The palette fills the screen and the same groups stack. The search box sits
at the top and the keyboard opens straight away.
