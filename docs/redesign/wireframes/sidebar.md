# Sidebar

**Brief rule:** the conversation list plus at most **4** top-level nav items.

**Today** (v1.320.0), there are two menus behind a hamburger button:
- Simple mode lists 7 places (Home, Work, Files, Automations, About me,
  Apps & settings, Help).
- Advanced mode lists 33 pages in 5 sections.

**Proposed:** one always-visible sidebar on desktop, and a drawer on a phone.

## Desktop

```
┌──────────────────────────┐
│ ◉ Iron Jarvis      [⇤]   │  ← collapse to icons
│                          │
│ (  + New chat        )   │  ← the sidebar's one primary action
│ [ 🔍 Search   Ctrl K ]   │  ← opens the command palette (not a nav item)
│                          │
│  ⌂  Build                │  1
│  ▣  Projects             │  2
│  ◇  Everything           │  3   (the directory of every other surface)
│  ⚙  Settings             │  4
│                          │
│  CHATS                   │
│  Today                   │
│   · Quarterly numbers    │  ← hover: ⋯ (rename, move to project, pin, delete)
│   · Email to the landlord│
│  Previous 7 days         │
│   · Ledger totals        │
│   · Tidy Downloads       │
│  [ Show all chats ]      │
│                          │
│  PROJECTS                │
│   ▸ Q3 Bookkeeping   3   │  ← click: the project's chats + its page
│   ▸ Website              │
│                          │
│ ──────────────────────── │
│ ● Running · v1.32x       │  ← status dot; click → Everything › Activity
└──────────────────────────┘
```

- **The four nav items:** Build, Projects, Everything and Settings. "New
  chat" is an action, not a destination; Search is the palette; the chat and
  project lists are the conversation list the brief excludes from the count.
- **Everything** (`/everything`, new) is one page that lists every other
  surface, grouped, each with one plain line. Any relocated surface is
  therefore two clicks from home (Everything → surface), which satisfies T2.
  It replaces today's Simple hubs, the Advanced menu, the 30-tile module grid
  and the Overview's admin drawer.
- **The Simple/Advanced switch is retired.** Everything always lists
  everything, so there is nothing left to hide; see AUDIT §9 (Q2).
- **Build stays one click away**, as the brief requires for the two primary
  surfaces.

## Everything (`/everything`)

```
┌────────────────────────────────────────────────────────────────────────┐
│ Everything                                       [ 🔍 Filter… ]        │
│                                                                        │
│ WORK               AUTOMATIONS            KNOWLEDGE         SYSTEM     │
│ Agents (missions)  Workflows              Memory            Activity   │
│ Creative           Schedules              You (profile)     Sessions   │
│ Templates          Reflexes               Train Jarvis      Usage      │
│ Documents          Sentinels              Skills            Updates    │
│ File search        Goals & autonomy       Artifacts         Fleet      │
│ Browser            Webhooks                                 Self-dev   │
│                                                                        │
│ Each entry: name + one line ("Run a task on a timer, report where you  │
│ choose"). Settings groups are listed under Settings, not here.          │
└────────────────────────────────────────────────────────────────────────┘
```

## Phone drawer (390 px)

```
┌──────────────────────────┐
│ ◉ Iron Jarvis       [✕]  │
│ ( + New chat )           │
│ [ 🔍 Search ]            │
│  ⌂ Build                 │
│  ▣ Projects              │
│  ◇ Everything            │
│  ⚙ Settings              │
│ CHATS                    │
│  · Quarterly numbers     │
│  · Email to the landlord │
│ PROJECTS                 │
│  ▸ Q3 Bookkeeping        │
└──────────────────────────┘
```
