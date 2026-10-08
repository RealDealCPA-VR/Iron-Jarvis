# Calm UI Redesign: Phase 1 Audit and Proposal

| | |
|---|---|
| Brief | [`BRIEF.md`](BRIEF.md) |
| Branch | `redesign/calm-ui` |
| Baseline | Iron Jarvis **v1.320.0** (master `d043951`), 2026-10-08 |
| Status | **Phase 1 complete. Waiting for VR's `APPROVED.md`. Phase 2 has not started.** |

**Scope of Phase 1.** Nothing outside `docs/redesign/` was changed on this
branch. Separately, master gained one test-only release (v1.320.1, a flaky
test's clock); it is unrelated to this redesign.

**Method.** Three read-only research passes produced this audit:
- an inventory of every shell, navigation, home and chrome surface, from the
  source code;
- a parity inventory of every setting, permission tier, ledger and undo path
  and secret path, from the source code;
- a read-only query of the live install's database, logs and the Electron
  localStorage, for usage evidence.

**Citations.** Every line reference points at the code as of `d043951`.
Anything not backed by code or data is marked **assumed**.

**Context VR should keep in mind.** The previous three releases already moved
toward this brief:
- v1.318.0 added a Simple-mode home and a seven-place menu.
- v1.319.0 shortened the Settings page in Simple mode.
- v1.320.0 added rating of chat replies.

This audit treats those as the current state, not as the goal. Several of
their decisions are reversed below (see §4 and §9).

---

## 1. Surface inventory

Frequency tiers:
- **daily**: used most days;
- **occasional**: a few times a month;
- **setup-only**: used during first setup, then rarely;
- **power-user**: used by people who tune the system.

Evidence sources:
- **[LS]**: the lifetime count of module opens recorded by the dashboard
  (`ironjarvis.overview.usage` in Electron localStorage, counted by
  `recordOpen`). It covers this PC, about 2026-07-01 to today, and counts only
  sidebar, tile, palette and home opens.
- **[DB]**: the live database, last 30 days.
- **[DLOG]**: the daemon log since 2026-09-17.
- **code**: a code reference only.
- **assumed**: no data supports it.

All of the usage evidence comes from **one user, VR**, who is a developer and
runs in Advanced mode. It says nothing about first-time users; see §2.

### 1.1 Always-on shell

| Surface | Route / component file | What it does | Frequency tier | Evidence | Proposed new home |
|---|---|---|---|---|---|
| Title bar | `components/TitleBar.tsx:54`, `app/layout.tsx:91` | Drag bar that holds the controls below | daily | code (every page) | Kept, slimmer: brand, search, bell, status dot |
| Menu button (opens the drawer) | `TitleBar.tsx:172-189` | Opens the nav drawer | daily | code | **Removed on desktop**: the sidebar is always visible (§4.2). Kept on phones as ☰ |
| Brand + home link | `TitleBar.tsx:195-212` | Goes to `/` | daily | code | Kept, at the top of the sidebar |
| Page crumb | `TitleBar.tsx:214-224`, `lib/nav.ts labelForPath` | Names the current page | daily | code | Kept |
| Search / Ctrl K button | `TitleBar.tsx:244-269` | Opens the command palette | daily | [LS] palette counts are mixed into the tally | Kept: sidebar "Search" plus title bar ⌘K |
| Pop-out button | `TitleBar.tsx:275-287` | Opens the page in its own window (desktop app) | occasional | code; no usage data | Kept: page header ⋯ menu |
| Theme dots | `ThemeSwitcher.tsx` via `AdvancedOnly` (`layout.tsx:97-101`) | Switches theme | setup-only | [LS] `ij_theme=mark8`; switches not counted | **Moved** to Settings › Appearance and the palette ("Theme: …") |
| Model chip | `components/ModelSwitcher.tsx` (`layout.tsx:103`) | App default model; "Demo replies" when none is chosen | daily | code; `PUT /settings` default changes in [DLOG] | **Moved into the composer** (one model selector, §9 Q3) |
| Mood orb | `components/MoodOrb.tsx` (`layout.tsx:105`) | Idle / thinking / alert glyph | daily (passive) | code | Merged into the sidebar footer's status dot |
| Notification bell | `components/NotificationBell.tsx` (`layout.tsx:106`) | Asks waiting for you, finished jobs, alerts | daily | code; bell routes in [DLOG] | Kept |
| Daemon banner | `components/DaemonBanner.tsx` (`layout.tsx:111`) | Daemon offline / restarting / stalled | rare (incident) | code | Kept: a conditional line, never permanent |
| Demo-model strip | `components/SimulatedBanner.tsx` (`layout.tsx:119`) | "Replies are scripted" until a model is chosen | setup-only | code | Replaced by the home's conditional line plus the composer's model chip (§4.6) |
| Provider-downgrade banner | `app/page.tsx:764` (Overview only) | A turn silently fell back | rare | code; `provider.downgraded` 6 in [DB] | **Moved to the shell** (it only shows on `/` today, a gap) as a conditional line |
| Nav drawer | `components/Sidebar.tsx:463` (NavDrawer) | Slide-in menu: Simple hubs or Advanced list, theme, footer | daily | code | Replaced by the persistent sidebar (§4.2); drawer kept on phones |
| Simple/Advanced switch | `Sidebar.tsx:332-385`, `lib/uiMode.ts` | Swaps the menu shape and hides power content | setup-only | [LS] `ij_nav_advanced=1` | **Retired** (§9 Q2); the Everything page lists everything |
| Sidebar footer | `Sidebar.tsx:388-458` | Version, daemon status, API host, Ctrl K hint, Deploy link | passive | code | Status dot + version; API host and Deploy move to Settings › System |
| Command palette | `components/CommandPalette.tsx` | Search pages, actions, themes, skills, chats, projects, history | daily | code; [LS] includes palette opens | Kept and extended with settings rows and the full surface manifest (§4.3) |
| First-run wizard (modal) | `components/FirstRunWizard.tsx:185` (opens when `first_run` is true) | 3-step blocking setup | setup-only | code | **Retired as a modal**; its steps become inline chat cards (§4.6) |
| Desktop notify bridge | `components/DesktopNotifyBridge.tsx` | Native toasts | passive | code | Unchanged (it has no UI) |

### 1.2 Navigation entries

**Simple-mode hubs** (`lib/hubs.ts:53-139`):

| Surface | Route / component file | What it does | Frequency tier | Evidence | Proposed new home |
|---|---|---|---|---|---|
| Hub: Home | `/` | Simple home | daily | code | Home = new chat |
| Hub: Work | `/chat` plus tabs (chat, projects, agents, creative, terminals, templates\*, self-dev\*) | Group | daily | [LS] chat 48, terminals 48 | Sidebar: Build, Projects; the rest go to Everything › Work |
| Hub: Files | `/documents` (documents, filesearch, artifacts\*) | Group | occasional | [LS] documents 0, filesearch 0, artifacts 2 | Everything › Work |
| Hub: Automations | `/workflows` (workflows, schedules, reflex\*, sentinels\*, autonomy\*, webhooks\*) | Group | occasional | [LS] workflows 6, schedules 2 | Everything › Automations |
| Hub: About me | `/you` (you, train, memory, skills\*) | Group | occasional | [LS] you 6, train 3, memory 6 | Everything › Knowledge; Settings › Memory & you |
| Hub: Apps & settings | `/connections` (connections, marketplace, channels, settings, updates, plus 8 hidden tabs) | Group | occasional | [LS] connections 17, settings 12 | Sidebar: Settings; Everything › System |
| Hub: Help | `/help` | Help and the Guide | occasional | [LS] none recorded | Palette, plus "Help" in the sidebar footer menu |
| HubTabs row | `components/HubTabs.tsx:20-65` via `PageHeader.tsx:176` | Simple-only tabs above a page title | daily (Simple) | code. Note: it also shows on Build (`terminals/page.tsx:838` uses PageHeader, contrary to HubTabs' comment) | **Retired** with the hubs; the Everything page and the palette replace it |

**Advanced-mode NAV rows** (`lib/nav.ts:73-536`, 33 rows). Each page's
detailed row is in §1.4.

| Surface | Route / component file | What it does | Frequency tier | Evidence | Proposed new home |
|---|---|---|---|---|---|
| Section headers: Work, Automate, Knowledge, Connections, System | `lib/nav.ts` | Group the 33 rows | daily (Advanced) | code | Become the 4 columns of the Everything page (§4.2) |
| Non-rail entry: Session board `/kanban` | `lib/nav.ts:551-567` | Search-only page | power-user | [LS] 0 | Everything › System (a "Board" view of Sessions, §9 Q4) |
| Crumb-only pages: `/marketplace` (Directory), `/ltm`, `/lessons`, `/fleet` | `lib/nav.ts:612-619` | Deep-link pages | occasional / power-user | [LS] fleet 43; marketplace 0 | Directory moves to Settings › Connections › "Add an app" (and joins the palette, which **cannot reach it today**); `/ltm` and `/lessons` stay as Memory scopes |

### 1.3 Overview `/` widgets, in render order (`app/page.tsx`)

| Surface | Route / component file | What it does | Frequency tier | Evidence | Proposed new home |
|---|---|---|---|---|---|
| Page header + version chip | `page.tsx:738` | Title "Home"/"Overview" and the version | daily | code | Version moves to the sidebar footer; no page header on the chat home |
| Offline hint | `page.tsx:756` | Daemon unreachable | rare | code | Shell conditional line |
| Provider-downgrade banner | `page.tsx:764` | See §1.1 | rare | [DB] 6 | Shell conditional line |
| First-run strip (OnboardingWelcome + AskAndStart) | `page.tsx:772`, `components/onboarding/FirstRunStrip.tsx:77-165` | Setup checklist, ask box, 4 starters | setup-only | code | Checklist items become inline cards in chat when relevant (§4.6); the ask box is the composer; the starters become composer chips |
| HomeStart (Simple) | `page.tsx:783`, `components/overview/HomeStart.tsx` | Status line, ask box, last 3 chats, 5 place tiles, "All modules" | daily (Simple) | code (shipped v1.318.0) | Retired: chats go to the sidebar, places to Everything |
| Reactor hero (Advanced) | `page.tsx:795` (ReactorHero `:233`) | Brand visual, status line, 4 stats (model, running, disk, problems) | daily (passive) | code | Status line → conditional home line; stats → Everything › Status page (§4.2) |
| Interrupted-jobs note | `page.tsx:825` | Work cut off by a restart, with Continue | rare | code | Conditional home line |
| Failing background-loop note | `page.tsx:830` | Names a failing loop | rare | code | Conditional home line + Status page |
| Failing tool-pack note | `page.tsx:848` | Names a pack that did not start, Retry | rare | code | Conditional home line + Settings › Connections › Apps & tools |
| AppGrid (3 doors × 10 tiles) | `page.tsx:873`, `components/overview/AppGrid.tsx`, `lib/appTiles.ts:198-253` | Module launcher with drag order and pop-out | daily (Advanced) | [LS] a custom tile order exists (`ironjarvis.overview.order`) | **Retired as a tile grid**; Everything is the list form of the same launcher (§9 Q1) |
| HealthCard | `page.tsx:882`, `components/overview/HealthCard.tsx` | Run quality: reviewed, finished, tools, reply time | power-user | code | Everything › Status |
| OnboardingWelcome (after setup) | `page.tsx:891` | Nudges a failing doctor check | rare | code | Conditional home line |
| PowerTips | `page.tsx:900` | Shortcut tips | setup-only | code | Help page + palette empty state |
| GoalsStrip | `page.tsx:911` | Live goals | occasional | [DB] goals 0 in 30 days | Everything › Automations (Goals); a conditional home line when a goal needs you |
| "Systems & admin" drawer, item Try it now | `page.tsx:938` | One-click session starters | setup-only | code | Composer chips |
| "Systems & admin" drawer, item Ambient operator | `page.tsx:990` | Reflex rules and recent fires | power-user | [DB] reflex 0 | Everything › Automations › Reflexes |
| "Systems & admin" drawer, item While you were away | `page.tsx:1053` | Events since the last visit | occasional | code | Bell (it already carries the same events) + Status |
| "Systems & admin" drawer, item Saved tasks | `page.tsx:1151` | Templates | occasional | [LS] templates 4 | Everything › Work › Templates; composer "+" menu |
| "Systems & admin" drawer, item Connections | `page.tsx:1200` | Provider readiness, browser vault | setup-only | code | Settings › Connections |
| "Systems & admin" drawer, item Recent sessions | `page.tsx:1265` | Last runs | occasional | [LS] sessions 3 | Everything › System › Sessions; the bell |
| "Systems & admin" drawer, item System health | `page.tsx:1325` | DB integrity, WAL, worktrees, loops | power-user | code | Everything › Status (and Settings › System › Maintenance) |
| EventStream | `page.tsx:1413` | Raw live events | power-user | code | Everything › System › Activity |

### 1.4 Main-navigation pages (one row per route in `dashboard/app/*`)

| Surface | Route / component file | What it does | Frequency tier | Evidence | Proposed new home |
|---|---|---|---|---|---|
| Chat | `/chat`, `app/chat/page.tsx` | Talk to Jarvis; projects panel; approvals; receipts | daily | [LS] 48; [DLOG] `POST /chat/stream` 25, `PUT /chat/threads` 51; [DB] 28 user turns over 17 days | **Primary.** It is the home (`/`), and `/chat` keeps working |
| Build | `/terminals`, `app/terminals/page.tsx` | Terminals and CLI panes, rail or canvas | daily | [LS] 48; [DLOG] `POST /terminals` 21 | **Primary.** Sidebar item 1 |
| Projects | `/projects`, `/projects/[id]` | Briefs, folders, knowledge, board | occasional | [LS] 10; [DB] 2 projects, 0 chats tagged | Sidebar item 2 (list + project page); project chats in the sidebar |
| Agents (missions) | `/agents` | One objective, the team works it | occasional | [LS] 13; [DB] 0 missions in 30 days | Everything › Work; offered inline when chat escalates |
| Creative | `/creative` | Generate and browse media | occasional | [LS] 5; [DLOG] `/creative/studio` 2 | Everything › Work |
| Templates | `/templates` | Saved prompts | occasional | [LS] 4 | Everything › Work; composer "+" › Templates |
| Documents | `/documents` | Read, write, redact files | occasional | [LS] 0; [DLOG] `/documents/batch/preview` 79 (probably the chat batch card) | Everything › Work |
| File search | `/filesearch` | Find files by name, content, meaning | occasional | [LS] 0 | Everything › Work; palette "Found" rows |
| Artifacts | `/artifacts` | Code agents wrote | power-user | [LS] 2; [DB] 2 artifacts in 30 days | Everything › Knowledge |
| Workflows | `/workflows` | Visual multi-step pipelines | occasional | [LS] 6; [DB] 3 workflows, 0 runs ever | Everything › Automations; inline card when chat drafts one (exists) |
| Schedules | `/schedules` | Tasks on a timer | occasional | [LS] 2; [DB] 1 (memory review) | Everything › Automations; inline card when chat schedules (§4.5) |
| Reflexes | `/reflex` | When X happens, run Y | power-user | [LS] 1; [DB] 0 | Everything › Automations |
| Sentinels | `/sentinels` | Folder watchers that suggest | power-user | [LS] 2; [DB] 0 | Everything › Automations |
| Autonomy | `/autonomy` | Goals, proposals, grants, kill switch | power-user | [LS] 2; [DB] goals 0, grants 0 | Everything › Automations (Goals); kill switch, caps and grants → Settings › Permissions & ledger |
| Webhooks | `/webhooks` | Inbound and outbound hooks | power-user | [LS] 2; [DB] 0 | Everything › Automations |
| Browser | `/computeruse` | Pair your browser, agent browser | occasional | [LS] 2; [DB] 194 `browser.navigation_completed` | Settings › Connections › Browser |
| Self-development | `/self-dev` | Jarvis edits its own repo | power-user | [LS] 1 | Everything › System |
| You | `/you` | Profile, tone, language | setup-only | [LS] 6 | Settings › Memory & you (profile fields are settings, §6) |
| Train Jarvis on me | `/train` | Import samples, notes, chats | setup-only | [LS] 3 | Settings › Memory & you › "Teach from your files" |
| Memory | `/memory` (+ `/ltm`, `/lessons`) | Notes, lessons, long-term, preferences | occasional | [LS] 6; [DB] 14 lessons | Everything › Knowledge; Settings › Memory & you links there |
| Skills | `/skills` | Playbooks, curator | power-user | [LS] 1; [DB] 7 proposals | Everything › Knowledge |
| Tools | `/tools` | Per-tool approval, MCP packs, custom tools | power-user | [LS] 7 | Split: approvals → Settings › Permissions & ledger; packs and custom tools → Settings › Connections › Apps & tools |
| Connections | `/connections` | Models, accounts, endpoints, Iron-Proxy | setup-only | [LS] 17; [DLOG] `/iron-proxy/accounts` 4 | Settings › Connections (and Models) |
| Directory | `/marketplace` | One-tap connectors | setup-only | [LS] 0 | Settings › Connections › "Add an app" |
| Local fleet | `/fleet` | Local inference endpoints, live | power-user | [LS] 43 (VR's 4th most opened) | Everything › System › Fleet; endpoint config → Settings › Models |
| Secrets | `/secrets` | Encrypted vault | power-user | [LS] 2 | Settings › Connections › Keys & secrets |
| Notifications | `/channels` | Email, Slack, Telegram destinations | setup-only | [LS] 2 | Settings › Connections › Notifications |
| Sessions | `/sessions`, `/sessions/[id]` | Past runs, detail with time-travel | occasional | [LS] 3; [DLOG] `DELETE /sessions` about 20 | Everything › System › Sessions (with Board view, §9 Q4) |
| Session board | `/kanban` | Sessions as cards | power-user | [LS] 0 | View toggle on Sessions (§9 Q4) |
| Activity | `/activity` | Timeline, undo, safety checks | power-user | [LS] 1 | Everything › System; ledger panel → Settings › Permissions & ledger |
| Usage | `/usage` | Tokens and cost | power-user | [LS] **63, VR's most opened page** | Everything › System › Usage (pin-able, §9 Q11) |
| Updates | `/updates` | Version, restart to update | occasional | code | Settings › System › Updates (+ bell when an update is ready) |
| Settings | `/settings` | Config form + appearance + maintenance | occasional | [LS] 12 | Sidebar item 4 (regrouped, §4.4) |
| Help | `/help` | Getting started, the Guide | occasional | [LS] none | Sidebar footer menu + palette; "ask the Guide" in chat |
| Overview | `/` | See §1.3 | daily | [LS] landing is not counted | Becomes the chat home |
| Integrations | `/integrations` | Redirects to `/connections` | — | code | Kept as a redirect |

### 1.5 Chat and Build chrome

Kept as they are (non-negotiable 2). They are listed here for completeness;
restyle only.

| Surface | Route / component file | What it does | Frequency tier | Evidence | Proposed new home |
|---|---|---|---|---|---|
| Chat thread rail | `chat/page.tsx:7600-7800` | Chat list, search, rename, scope | daily | [DLOG] thread PUTs | **Moves into the app sidebar** (one list, not two). Behaviour unchanged; see §9 Q5 |
| Chat header: voice, read-aloud, persona, project panel | `chat/page.tsx:7342-7453`, `:8264` | Per-chat controls | daily | code | Kept (restyle). Persona → composer "+" menu (§9 Q5) |
| Composer "+" menu | `chat/page.tsx:8930-9346` | Attach, project, skills, workflow, connections, auto-tools | daily | code | Kept |
| Composer footer: Share, Approvals, Reasoning, model menu | `chat/page.tsx:9533-9709` | Per-chat posture and model | daily | code | Kept; the model menu becomes the ONE model selector |
| Right project panel | `chat/page.tsx:9847-10069` | Project hub inside chat | occasional | [DB] 0 project chats | Kept |
| Build header, rail, panes, files panel | `terminals/page.tsx:838-1526`, `TerminalPane.tsx`, `PaneRail.tsx` | Terminals | daily | [DLOG] | Kept; drop the HubTabs row (gone with the hubs) |

---

## 2. Usage evidence

**What exists.** All counts are from the read-only report; no page or route
events are stored anywhere.

- **Page opens [LS]:** a lifetime count per route, from about 2026-07-01, on
  this PC only. It counts only menu, tile, palette and home presses. Deep
  links, Back and the landing page are not counted.
  - Top routes: `/usage` 63, `/chat` 48, `/terminals` 48, `/fleet` 43,
    `/connections` 17, `/agents` 13, `/settings` 12, `/projects` 10,
    `/tools` 7, `/you` 6, `/workflows` 6, `/memory` 6, `/creative` 5,
    `/templates` 4.
  - Everything else is 3 or fewer; `/documents`, `/filesearch`, `/kanban` and
    `/marketplace` are 0.
  - The same storage shows `ij_nav_advanced=1` and `ij_theme=mark8`, and the
    tiles were re-ordered by hand (Usage first).
- **Last 30 days [DB]:**
  - Chat: 28 user turns on 17 distinct days, mostly in one long thread;
    3 threads created; 0 tagged to a project.
  - Sessions: 6, all Builder. They are the weekly memory-review job and its
    setup; 0 missions, reflex, goal, comm or workflow runs.
  - Tool calls: 671, all inside those sessions (`history_search` 232,
    `worklist_done` 130, `ltm_append` 52, browser 80+).
  - Browser add-on: 194 page navigations.
  - Zero records for reflexes, sentinels, goals, assignments, webhooks,
    standing grants and dynamic agents/tools.
- **[DLOG] (09-17 onward):** `POST /chat/stream` 25, `PUT /chat/threads` 51,
  `POST /terminals` 21, `/documents/batch/preview` 79, `/iron-proxy/accounts`
  4, `/creative/studio` 2, `/schedules` 1. Polling dominates the rest
  (`/fs/files` 14,575).
- **[DESK]:** the app was active on 21 of the last 30 days.

**What this supports:**
- Chat and Build are the daily surfaces.
- Usage, Fleet and Connections are VR's power-user daily drivers. They must
  stay **one or two clicks away**, which is why §9 Q11 proposes pinning.
- Most automation surfaces have been built but not used in the last 30 days.
  That is **not** evidence they can go (non-negotiable 1). It is evidence they
  should not occupy the first screen.

**What this does not support.** Anything about first-time users. VR is an
expert running Advanced mode, so all first-run claims in this audit are
**assumed** (they come from the v1.313–v1.316 screenshot audits, not from
usage).

**Smallest instrumentation (recommended as plan step S0):**
- **Daemon:** a new `POST /ui/visit {route, via}` route
  (`src/iron_jarvis/daemon/routes/ui.py`). It publishes `ui.page_opened`
  through the existing EventBus, which already persists to `eventrecord`.
  It accepts only `lib/nav.ts` hrefs, so no ids or query strings are stored;
  `via` is one of sidebar, palette, tile, link or deeplink.
- **Dashboard:** a `RouteVisitBeacon` in `app/layout.tsx` that posts once per
  pathname change.
- **Report:** a 30-day group-by on the Usage page.
- **Privacy:** everything stays on this PC, as all IJ telemetry does today.

---

## 3. Reference patterns (ChatGPT, Claude, Grok)

**Source: prior knowledge**, current to about mid-2026. No browsing was done
for this audit; it can be done in Phase 2 if VR wants live screenshots.

| Concern | ChatGPT (web) | Claude.ai | Grok (web) |
|---|---|---|---|
| Primary view | Centered composer on an empty page ("What's on your mind today?"); the conversation is the page | Same: greeting + centered composer; artifacts open in a side pane | Same: centered composer; mode toggles near it |
| Sidebar | New chat, Search chats, Library, GPTs/Codex/Sora entries, **Projects**, then the chat history by date; account at the bottom | New chat, Chats, Projects, Artifacts (and Code), then **Recents**; account at the bottom | Search, Chat, Voice, Imagine, Projects, History |
| Settings | A modal from the account menu: General, Notifications, Personalization (custom instructions, memory), Apps/Connectors, Schedules, Data controls, Security, Account | Settings page: Profile, Appearance, Account, Privacy, Billing, Capabilities (memory, artifacts), Connectors | Settings modal: Appearance, Behavior, Customize, Data controls |
| Model selection | Picker in the chat header | **Inside the composer** (bottom right) | Inside the composer (Auto / Fast / Expert / Heavy) |
| Tool toggles | "+" in the composer: image, deep research, web search, canvas, agent | "Search and tools" in the composer: web search, extended thinking, research, per-connector toggles | Toggles beside the composer (Think, DeepSearch) |
| Projects | Sidebar section; a project holds chats, files and instructions | Sidebar item; a project holds knowledge, instructions and chats | Sidebar item |
| Connectors | Settings › Apps/Connectors; also from the "+" menu | Settings › Connectors; toggled per chat in the tools menu | Limited |

**Borrow:**
- The empty-page composer as home.
- Chats and projects in the sidebar, dated.
- The model selector inside the composer (Claude and Grok) rather than in the
  header.
- Tools under "+" (IJ already has this).
- Settings as a separate grouped surface off the sidebar's bottom.
- Connectors managed in Settings but toggled per chat.

**Deliberately differ:**
1. **Build is a first-class primary surface.** None of the three has local
   terminals.
2. **Trust is visible, not hidden.** Approval cards, the ledger and Undo stay
   in the transcript; those apps hide most of this.
3. **IJ has far more surfaces** (automations, fleet, self-dev), so it needs
   the **Everything** directory plus a strong palette. The reference apps can
   rely on settings alone because they have few surfaces.
4. **Privacy defaults differ.** IJ must never silently move a chat to a cloud
   model (VR's standing rule, `CLAUDE.md` "Never let a real-provider failure
   return mock output"; the v1.162.0 refusal). That shapes first-run (§4.6).

---

## 4. Proposed information architecture

### 4.1 Primary surfaces

| Primary | Where | Change |
|---|---|---|
| **Chat** | `/` (home) and `/chat` (same surface) | Home *is* a new chat: the composer is on the page and focused, with the model selector inline. Chat's behaviour is unchanged (non-negotiable 2); its own thread rail merges into the app sidebar (§9 Q5) |
| **Build** | `/terminals` | Sidebar item 1. Behaviour unchanged |

### 4.2 Sidebar

**The brief's cap:** conversations and projects plus at most 4 top-level
items.

```
( + New chat )        ← action
[ Search  Ctrl K ]    ← opens the palette
1. Build              → /terminals
2. Projects           → /projects
3. Everything         → /everything   (NEW: the directory of every other surface)
4. Settings           → /settings
Chats (dated) · Projects (expandable) · status dot + version in the footer
```

**Everything** (`/everything`) is a single page that lists every surface not
in the sidebar.
- It replaces today's hubs, Advanced menu, tile grid and admin drawer.
- Four columns: **Work** (Agents, Creative, Templates, Documents, File
  search), **Automations** (Workflows, Schedules, Reflexes, Sentinels, Goals,
  Webhooks), **Knowledge** (Memory, Skills, Artifacts) and **System**
  (Status, Sessions, Activity, Usage, Fleet, Self-development).
- Each entry has one plain line.
- Every relocated surface is therefore **two clicks from home** (sidebar
  Everything, then the entry), which satisfies **T2**.
- **Status** is a new section of this page. It holds the Advanced Overview's
  operational content: running work, health, the run-quality card, failing
  loops and packs, and the event stream.

**Phone:** the same sidebar is the ☰ drawer.

### 4.3 Command palette (Ctrl+K)

The palette already reaches 34 pages, deep links, actions, themes, skills,
threads, projects and history. Proposed additions:
- **Every setting** (from the schema in §4.4): its label and aliases open its
  Settings group with the row highlighted. There is also a "Do it in chat"
  row (`/chat?ask=`, never sent).
- **Every Everything entry**, generated from one surface manifest
  (`lib/surfaces.ts`) that is the source for the sidebar, the Everything page,
  the palette and the T2 test. Today `/marketplace` is **not reachable from
  the palette**; the manifest closes that gap structurally.
- **Group order:** Settings, Pages, Actions, Skills, Projects, Chats, History.

### 4.4 Settings: one schema, seven groups

**One source of truth (non-negotiable 3).** A new
`src/iron_jarvis/settings/schema.py` declares every setting once:

```
key · label · aliases · group · type (bool | enum | string | number | list | dict | schedule | connection | secret)
    · scope (daemon | device) · tier (allow | ask | ask-floor) · secret (bool) · restart (bool) · validator · help
```

Three things read that one declaration:
1. **`GET /settings/schema`**, which the dashboard Settings page renders.
   This retires the hand-written `FIELDS` list in `app/settings/page.tsx`.
2. **The daemon's `PUT /settings` whitelist.** It replaces `_SETTINGS_KEYS`
   in `daemon/schemas.py:522-632`. This also fixes the `comm_trust` drop:
   that key is rendered by the UI but missing from the whitelist, so it is
   silently not saved today (`routes/settings.py:189`).
3. **The chat config tools** `config_list`, `config_get` and `config_set`.
   `config_set` carries a **per-key permission key** `config_set:<key>`, the
   same pattern as `custom:<name>`, so each key's tier is enforced by the
   existing gate (`tools/permissions.py:162-284`). A setting with
   `secret: true` is never set through `config_set`; it opens the credential
   card instead (§4.5).

**One writer.** Every config writer funnels through one function,
`apply_config_change(keys, values, actor)`:
- the settings route;
- `/routing/*`, `/iron-proxy/enable`, `/profile/share`, `/mcp/settings`,
  `/triggers/calendar`, `/skills/learning/settings`, `/fleet/code-route` and
  `/autonomy/kill`. Today these all go through `d._persist_config`
  (`daemon/app.py:2544`) with **no ledger and no undo**.

That function:
- writes the `ToolInvocation` ledger row and the `setting_restore` UndoJournal
  row (this exists today only for `PUT /settings`,
  `routes/settings.py:129-176`);
- publishes `config.changed`;
- makes undo **re-authorize**. Today `routes/undo.py:319` replays a setting
  restore without asking the gate.

**The seven Settings groups:**

| Group | Contents (keys in §6) |
|---|---|
| **Models** | default provider/model/persona, reasoning default, strict pin, local primary policy, local and custom endpoints, local quality routing, routing tiers/roles, context windows, compaction |
| **Connections** | accounts and API keys, Iron-Proxy accounts, **Apps & tools** (Directory, MCP packs, custom integrations), Browser access and pairing, **Notifications** (Telegram, Slack, email channels), calendar trigger, keys and secrets vault |
| **Agents & automation** | max steps, tool deadline, autonomy (on, level, dry run, caps, tick), sentinels (on, tick), skill learning, calendar lead, fleet code route |
| **Memory & you** | profile (about, tone, style, formatting, reading level, length, accessibility), "Teach from your files" (Train), profile share with Build CLIs, memory steward, chat files folder |
| **Permissions & ledger** | approval-mode default, per-tool permissions (**gains a write route**: today config.toml only), MCP auto-approve, standing grants (list/revoke), low-trust for inbound messages (`comm_trust`), kill switch, ledger with Undo |
| **Appearance** | theme and theme maker, density; device scope (§9 Q6) |
| **System** | updates, backups and mirror, storage, maintenance, diagnostics, event retention, sandbox runtime, git-native, self-development, daemon token, deploy |

**Records stay on their own pages.** Schedules, workflows, reflexes,
sentinels, goals, webhooks, projects, personas, skills and agents are linked
from the matching group. Their chat parity is through record tools (§6.3).

### 4.5 Contextual surfacing rules

**R1. Never permanent; inline when relevant.** An advanced panel appears in
the transcript at the moment it matters. Today's patterns, kept and extended:
- workflow draft card (exists);
- approval card (exists);
- TurnReceipt with file Undo (exists);
- **config card** (new): "Setting changed: old → new [Undo]"
  (wireframe `config-card.md`);
- **schedule card** (new) when chat creates a schedule;
- **credential card** (new) when a key is needed (`credential-card.md`);
- **connect card** (exists as ConnectDoors) when no model can answer.

**R2. One conditional line at most** above the home composer, and the most
urgent wins:
- daemon offline;
- provider downgraded;
- a model is needed (only when none was auto-found);
- interrupted work;
- a failing loop or pack;
- running or waiting work.

**R3. Status lives in the bell and Everything › Status,** never in a
permanent card.

**R4. Ambiguous, destructive or credential requests get one clarifying
question** (a choice card) before any change (brief Phase 2).

**R5. Every setting that chat can change appears in the palette** with "Do it
in chat".

### 4.6 First-run behaviour

**No blocking screen.** `FirstRunWizard` is retired as a modal (open
question Q7). On first open the app shows the chat home. The daemon already
detects signed-in Claude Code and Codex CLIs, Ollama and local endpoints
(`GET /onboarding` `model.usable`, `routes/settings.py`).

**Auto-detected model: this conflicts with VR's standing rules.** Since
v1.310.0, nothing promotes the default silently (`POST /onboarding/use-model`
is "THE one explicit press"), and VR's rule is never to auto-switch providers
for privacy. The brief asks for "auto-detect … no blocking setup". The
proposal reconciles the two:
- The composer's model chip shows "**Use Claude (signed in on this PC)** ·
  one tap". It is suggested, not applied.
- The first Send with no model chosen asks once, inline, which detected
  model should answer, and remembers the choice.
- No setup screen is ever required, but no provider is chosen without the
  user's press. **Q7 asks VR to choose** between this and true silent
  auto-selection.

**Missing setup is requested inline, only when a task needs it:**
- A voice press with no speech backend → inline card.
- "Email this" with no mail channel → credential/connect card.
- A document task with no document tools armed → the existing honesty note
  plus an arm chip.

The **setup checklist** survives only as the Everything › Status "Setup"
section and in the bell (one item, dismissible).

---

## 5. Before/after map

Every current surface and exactly where it moves. "Sidebar" means the new
persistent sidebar; "E›" means the Everything page section; "S›" means the
Settings group.

| Today | After |
|---|---|
| `/` Overview (Simple Home / Advanced Overview) | `/` = new chat (composer focused). Operational content → E›System›**Status** |
| HomeStart: ask box / starters / recent chats / place tiles / All modules | composer / composer chips / sidebar Chats / Everything / Everything |
| ReactorHero + HealthCard + System health + EventStream | E›System›Status (hero visual kept as the Status header) |
| AppGrid (30 tiles, drag order, pop-out) | Everything (list; pin-able favourites, §9 Q11; pop-out stays in each page's ⋯ menu) |
| Systems & admin: Try it now, Ambient operator, While you were away, Saved tasks, Connections, Recent sessions | composer chips, E›Automations›Reflexes, bell + Status, E›Work›Templates + composer "+", S›Connections, E›System›Sessions + bell |
| FirstRunStrip / OnboardingWelcome / FirstRunWizard | inline chat cards (§4.6) + Status›Setup |
| PowerTips / GoalsStrip / interrupted, loop, pack notes | Help + palette / E›Automations›Goals + home line / home conditional line |
| Title bar theme dots / model chip / mood orb | S›Appearance + palette / composer model selector / sidebar status dot |
| SimulatedBanner / DaemonBanner / ProviderDowngradeBanner (Overview-only) | home conditional line + composer chip / shell line / shell line (now on every page) |
| Nav drawer + Simple/Advanced switch + HubTabs | persistent sidebar (drawer on phone); switch retired; tabs retired |
| `/chat` | `/chat` and `/` (same surface; thread rail merges into the sidebar, §9 Q5) |
| `/terminals` Build | Sidebar 1 |
| `/projects`, `/projects/[id]` | Sidebar 2 (+ projects listed under the chats) |
| `/agents`, `/creative`, `/templates`, `/documents`, `/filesearch` | E›Work |
| `/workflows`, `/schedules`, `/reflex`, `/sentinels`, `/webhooks` | E›Automations |
| `/autonomy` | Goals and proposals → E›Automations›Goals; caps, kill switch, grants → S›Permissions & ledger |
| `/memory` (`/ltm`, `/lessons`), `/skills`, `/artifacts` | E›Knowledge |
| `/you`, `/train` | S›Memory & you (profile fields + "Teach from your files"); also E›Knowledge links |
| `/connections`, `/marketplace`, `/secrets`, `/channels`, `/computeruse` | S›Connections (accounts and keys; Apps & tools; Keys & secrets; Notifications; Browser) |
| `/tools` | per-tool approval → S›Permissions & ledger; packs and custom tools → S›Connections›Apps & tools |
| `/fleet` | E›System›Fleet (endpoint config in S›Models) |
| `/sessions`, `/sessions/[id]`, `/kanban` | E›System›Sessions (list ⇄ board toggle; detail unchanged) |
| `/activity` | E›System›Activity; ledger panel also in S›Permissions & ledger |
| `/usage` | E›System›Usage |
| `/updates` | S›System›Updates (+ bell) |
| `/settings` | Sidebar 4 (7 groups, §4.4) |
| `/self-dev` | E›System›Self-development |
| `/help` | sidebar footer menu + palette + chat ("ask the Guide") |
| `/integrations` | stays a redirect |

**Addresses.** Every current address keeps working (pages keep their routes;
only their entry points move). A relocated page within Settings is a Settings
section with an anchor, and its old route redirects there (e.g. `/channels` →
`/settings#connections-notifications`). Whether old routes redirect or remain
standalone pages is **Q8**.

---

## 6. Settings parity table

### 6.1 How to read the table

- **Tool:** the proposed chat tool. `config_set:<key>` is one generated tool
  with a per-key permission key. `config_secret:<name>` opens the credential
  card (the value never passes through the model). `config_set@device:<key>`
  is a device-scoped setting (§9 Q6).
- **Tier:**
  - **allow**: applied at once, then a "changed" card with Undo;
  - **ask**: a one-press confirm card first, after which a standing "always"
    grant is possible;
  - **ask-floor**: confirm every time, no standing grant, and denied under
    low trust (`core/trust.py LOW_TRUST_DENY`).
- **Secret:** "yes" means the value is a credential.
- **UI:** the location is current (`settings/page.tsx` unless named).
- **Proposed tiers** are proposals for VR (§9 Q10).

### 6.2 Setting keys

| Setting key | Current UI location | Proposed chat tool name | Permission gate tier | Secret |
|---|---|---|---|---|
| default_provider | Settings › Models :119; ModelSwitcher :359; Connections "make default" | config_set:default_provider | ask | no |
| default_model | Settings :130; ModelSwitcher | config_set:default_model | allow | no |
| default_persona | Settings :141 | config_set:default_persona | allow | no |
| strict_model_pin | Settings :148 | config_set:strict_model_pin | ask | no |
| local_primary_policy | Settings :159 | config_set:local_primary_policy | ask-floor (failover can move a chat off-box) | no |
| event_retention_days | Settings :180 | config_set:event_retention_days | ask | no |
| ollama_base_url | Settings :190; Connections :684; ConnectDoors :258 | config_set:ollama_base_url | ask | no |
| ollama_model | Settings :198 | config_set:ollama_model | allow | no |
| custom_base_url | Settings :206; Connections | config_set:custom_base_url | ask | no |
| custom_model | Settings :214 | config_set:custom_model | allow | no |
| prefer_local_when_capable | Settings :225 | config_set:prefer_local_when_capable | allow | no |
| local_quality_bar | Settings :233 | config_set:local_quality_bar | allow | no |
| local_quality_min_samples | Settings :240 | config_set:local_quality_min_samples | allow | no |
| decompose_local_tasks | Settings :247 | config_set:decompose_local_tasks | allow | no |
| voice_transcribe_base_url | Settings :254 | config_set:voice_transcribe_base_url | ask | no |
| voice_transcribe_model | Settings :262 | config_set:voice_transcribe_model | allow | no |
| voice_transcribe_key (vault) | Connections (vault) | config_secret:voice_transcribe_key | ask-floor | **yes** |
| max_agent_steps | Settings :272 | config_set:max_agent_steps | ask | no |
| tool_call_timeout_s | Settings :279 | config_set:tool_call_timeout_s | ask | no |
| comm_trust | Settings :290 (**not saved today**: missing from the whitelist) | config_set:comm_trust | ask-floor (full trust widens inbound runs) | no |
| autonomy_enabled | Settings :305; Autonomy :206 | config_set:autonomy_enabled | ask-floor | no |
| autonomy_level | Settings :312 | config_set:autonomy_level | ask-floor | no |
| autonomy_dry_run | Settings :328 | config_set:autonomy_dry_run | ask-floor (turning it off) / allow (turning it on) | no |
| autonomy_kill_switch | Settings :335; `POST /autonomy/kill` | config_set:autonomy_kill_switch | allow (engage) / ask-floor (release) | no |
| autonomy_tick_seconds | Settings :342 | config_set:autonomy_tick_seconds | ask | no |
| autonomy_max_actions_per_day | Settings :349 | config_set:autonomy_max_actions_per_day | ask | no |
| autonomy_max_tokens_per_day | Settings :356 | config_set:autonomy_max_tokens_per_day | ask | no |
| sentinels_enabled | Settings :363; Sentinels :154 | config_set:sentinels_enabled | ask | no |
| sentinels_tick_seconds | Settings :370 | config_set:sentinels_tick_seconds | ask | no |
| git_native | Settings :379 | config_set:git_native | ask | no |
| self_dev_enabled | Settings :386 | config_set:self_dev_enabled | ask-floor | no |
| self_dev_root | Settings :394 | config_set:self_dev_root | ask-floor | no |
| sandbox_runtime | Settings :403 | config_set:sandbox_runtime | ask-floor | no |
| model_context_windows | ContextWindowsCard :682 | config_set:model_context_windows | ask | no |
| backup_mirror_dir | MaintenanceTools.tsx:482 | config_set:backup_mirror_dir | ask | no |
| backup_mirror_media | MaintenanceTools.tsx | config_set:backup_mirror_media | allow | no |
| browser_access | YourBrowserCard.tsx:649; BrowserSetupModal.tsx:690 | config_set:browser_access | ask-floor (interactive) / allow (lower) | no |
| calendar_trigger_enabled | Reflex page :981 (`POST /triggers/calendar`) | config_set:calendar_trigger_enabled | ask | no |
| calendar_ics_url (vault) | Reflex page :981 | config_secret:calendar_ics_url | ask-floor | **yes** (private feed URL) |
| calendar_lead_minutes | Reflex page | config_set:calendar_lead_minutes | allow | no |
| calendar_tick_seconds | none | config_set:calendar_tick_seconds | ask | no |
| skill_learning_enabled | SuggestedSkills.tsx:68 | config_set:skill_learning_enabled | ask | no |
| skill_learning_auto_approve | SuggestedSkills.tsx | config_set:skill_learning_auto_approve | ask-floor | no |
| routing_model | ModelSwitcher.tsx:380 | config_set:routing_model | ask | no |
| routing_tiers_json | none | config_set:routing_tiers_json | ask | no |
| model_roles | none | config_set:model_roles | ask | no |
| routing_local_ladder | none | config_set:routing_local_ladder | ask | no |
| context_compaction | none | config_set:context_compaction | ask | no |
| fleet_code_route_enabled | `PUT /fleet/code-route` (no UI writer; Fleet reads it :922) | config_set:fleet_code_route_enabled | ask | no |
| fleet_code_target | same | config_set:fleet_code_target | ask | no |
| fleet_code_task_classes | same | config_set:fleet_code_task_classes | ask | no |
| fleet_sampling_enabled | none | config_set:fleet_sampling_enabled | allow | no |
| fleet_sampling_seconds | none | config_set:fleet_sampling_seconds | allow | no |
| fleet_savings_baseline | none | config_set:fleet_savings_baseline | allow | no |
| opencode_local_models | none | config_set:opencode_local_models | ask | no |
| opencode_data_dir | none | config_set:opencode_data_dir | ask | no |
| pi_sessions_dir | none | config_set:pi_sessions_dir | ask | no |
| voice_vosk_model_path | none | config_set:voice_vosk_model_path | ask | no |
| chat_files_root | none | config_set:chat_files_root | ask | no |
| memory_steward_enabled | none | config_set:memory_steward_enabled | ask | no |
| iron_proxy_enabled | IronProxyCard.tsx:311 (`/iron-proxy/enable`) | config_set:iron_proxy_enabled | ask | no |
| profile_share_claude_code | ProfileShareRow.tsx:101 (`PUT /profile/share`) | config_set:profile_share_claude_code | ask (writes a file in the CLI's home) | no |
| profile_share_codex | same | config_set:profile_share_codex | ask | no |
| mcp_auto_approve | Tools page :742; PermissionsPanel (`PATCH /mcp/settings`) | config_set:mcp_auto_approve | ask-floor | no |
| permissions.<tool> (per-tool mode) | **no writer** (config.toml only); Tools page reads it | config_set:permissions.<tool> | ask-floor (never to "allow" a deny-floor tool: `tools/permissions.py:57-105`) | no |
| active_project_id | Projects "Set as focus" | config_set:active_project_id | allow | no |
| computer_use (dict) | `/computeruse` enable (**in-memory only, lost on restart**) | config_set:computer_use | ask-floor | no |
| max_concurrent_sessions | none (config.toml) | config_set:max_concurrent_sessions | ask | no |
| decompose_all_tasks | none (config.toml) | config_set:decompose_all_tasks | ask | no |
| search_roots | none (config.toml) | config_set:search_roots | ask | no |
| default_skills / extra_skill_paths | none (config.toml) | config_set:default_skills / :extra_skill_paths | ask | no |
| obsidian_vault / notion_database_id | none (config.toml) | config_set:obsidian_vault / :notion_database_id | ask | no |
| ocr_* / embedder_* | none (config.toml) | config_set:ocr_* / :embedder_* | ask | no |
| sandbox policy (dict) | none (config.toml) | config_set:sandbox_policy | ask-floor | no |
| profile.about / tone / writing_style / formatting / reading_level / response_length / accessibility / enabled | You :265/:283; Train :189 (`PUT /profile`) | config_set:profile.<field> | allow | no |
| approval mode default (`ij_chat_approval_mode`, localStorage) | Chat footer :9562 | config_set@device:approval_mode | ask-floor (to "yolo") / allow (to stricter) | no |
| chat persona (`ij_chat_persona`, localStorage) | Chat header | config_set@device:chat_persona | allow | no |
| auto tools (`ij_chat_auto_tools`, localStorage) | Chat "+" menu | config_set@device:auto_tools | allow | no |
| read-aloud (`ironjarvis.tts.enabled`) | Chat header | config_set@device:read_aloud | allow | no |
| theme (`ij_theme`) / palettes (`ij_palettes`) | Settings › Appearance; title bar | config_set@device:theme | allow | no |
| Simple/Advanced (`ij_nav_advanced`) | drawer toggle | **retired** (§9 Q2) | — | no |
| daemon token (`ij_token`, localStorage) | DaemonTokenCard | config_secret:daemon_token (**display-only; never via chat**) | ask-floor | **yes** |
| provider API keys (vault, ConnectionRecord) | Connections :812; ConnectDoors :297 | config_secret:connection.<provider> | ask-floor | **yes** |
| comm.channels.<name> (Telegram bot token, Slack, email) | Notifications :414/:487 | config_secret:channel.<name> (+ config_set for non-secret fields) | ask-floor | **yes** |
| custom_integrations | `/integrations*` | config_secret:integration.<name> | ask-floor | **yes** |
| LTM source tokens | Memory › add a base (knowledge.py:394) | config_secret:ltm_source.<name> | ask-floor | **yes** |
| remote-agent tokens | Agents › remote (agents.py:1073) | config_secret:remote_agent.<name> | ask-floor | **yes** |
| generic vault secrets | `/secrets` :58 | config_secret:<name> (today `secret_set`, whose value **passes through the model**) | ask-floor | **yes** |
| Iron-Proxy accounts | IronProxyCard (sign-in pane) | (record) iron_proxy_account_* | ask | yes (sign-in) |

### 6.3 Records

These are not setting keys, so they are not in T4's schema enumeration. They
keep record tools, all with ledger and Undo for T5:

| Record | Today's chat tool (tier) | Proposed additions |
|---|---|---|
| Schedules | `schedule_create` (allow) | `schedule_update`, `schedule_delete`; Undo restores (T5 "schedule" type) |
| Workflows | `workflow_create` (allow) | `workflow_update`, `workflow_delete`, `workflow_schedule` |
| Notification channels | none | `channel_connect` (credential card), `channel_toggle` (T5 "connection" type) |
| Webhooks / Sentinels / Goals | `webhook_add`, `sentinel_add`, `goal_add` (allow) | update/delete + Undo |
| Reflex rules | none | `reflex_create/update/delete` |
| MCP packs (Directory) | none | `app_connect` ("connect my Notion" → credential card) |
| Personas / Agents / Skills / Projects | `create_agent`, `skill_create` (ask) | persona and project create/update |
| Standing grants | none | `grant_revoke` (allow) |

---

## 7. Wireframes

Six views are in [`wireframes/`](wireframes/README.md):
[home](wireframes/home.md), [sidebar and Everything](wireframes/sidebar.md),
[Settings](wireframes/settings.md),
[command palette](wireframes/command-palette.md),
[inline config confirmation card](wireframes/config-card.md) and
[secure credential card](wireframes/credential-card.md). Each has a desktop and
a 390 px layout.

---

## 8. Visual system

The base is the existing token system (`app/globals.css`,
`tailwind.config.ts`), tightened rather than replaced.

### Spacing

A 4 px base, with only these steps: **4, 8, 12, 16, 24, 32, 48, 64**.
- 16 is the gap within a card.
- 24 is the gap between cards.
- 48 is the gap between page regions.
- Chat column max width is **760 px** (centred).
- Settings content max width is **720 px**.
- The phone gutter is **16 px**.
- Generous whitespace means the home is mostly empty above and below the
  composer.

### Type scale

Inter for UI text; the mono stack only for code and ids.

| Token | Size / line | Use |
|---|---|---|
| micro | 10/14 | badges only |
| caption | 11/16 | timestamps, chips |
| meta | 12/18 | help and description text (AA floor) |
| body | 13/20 | dense UI |
| body-lg | 14/22 | chat text and form fields |
| **title-sm** (new) | 16/24 | card titles |
| **title** (new) | 20/28 | page titles |
| **display** (new) | 28/36 | the home greeting only |

There are 3 weights (400, 500, 600). The 408 half-pixel sizes are already held
by a ratchet; the redesign drives them to 0 on rewritten surfaces.

### One accent colour

`--accent-rgb` is the only brand colour, the user's chosen or made theme.
- Status uses the semantic **tone** tokens only (success, danger, warn, info,
  violet): never as decoration and never as a second brand colour.
- The `accent-deep` glow is decoration only.

### Light and dark tokens

- Dark uses the existing `:root` values (`BASE_DARK`) and light uses Daylight
  (`BASE_LIGHT`), in `lib/themePalette.ts`.
- A custom palette generates both with contrast at least 4.5:1 (shipped
  v1.317.0, `data-scheme`).
- Surfaces: ink-950 page, ink-900 recessed, ink-850 card.
- Text: zinc-100 primary, zinc-400 secondary, zinc-500 helper.
- Borders: `white/10` (re-inked on light).

### One primary action per view

- Exactly one solid `btn-accent` per view region:
  - the composer's Send on home and chat;
  - "New chat" in the sidebar;
  - Save in Settings, shown only while there are unsaved changes;
  - the confirm button on a card.
- Everything else is ghost or soft.
- Phase 2 adds a test that counts visible `btn-accent` per page.
- Radius: 8 for controls, 12 for cards, 16 for the composer.
- Motion: 150–220 ms; reduced motion is honoured (exists).

### 390 px

- The sidebar becomes a drawer.
- The composer is pinned to the bottom.
- Settings groups become a drill-down list.
- No horizontal pan (the existing pan probe becomes a Phase 2 test).

---

## 9. Open questions for VR

Nothing below happens without a decision in `APPROVED.md`. For each, my
recommendation comes first.

1. **Retire the Overview's dashboard and the tile grid.**
   - Home becomes the chat; operational content moves to Everything › Status;
     the 30 tiles become the Everything list.
   - Recommend: **yes**.
   - Cost: your hand-ordered tile layout is replaced by pinnable favourites
     (Q11).
2. **Retire the Simple/Advanced switch** (and v1.318.0's seven hubs and tab
   row, and v1.319.0's short Settings view).
   - With a 4-item sidebar, Everything and a palette, nothing needs hiding.
   - Recommend: **yes**.
   - Alternative: keep the switch only to fold rarely used Settings rows.
3. **One model selector.**
   - Remove the title-bar model chip; the composer's selector chooses the
     model for the chat, and "Make this my default" sits inside its menu.
   - Recommend: **yes** (Claude and Grok pattern).
4. **Merges:**
   - (a) Session board becomes a Board view of Sessions — **recommend yes**.
   - (b) You + Train become Settings › Memory & you — **recommend yes**.
   - (c) Connections + Directory + Secrets + Notifications + Browser become
     Settings › Connections — **recommend yes**.
   - (d) Tools splits into Permissions and Apps & tools — **recommend yes**.
   - (e) Workflows, Schedules, Reflexes and Sentinels as one "Automations"
     list with a trigger type — **recommend later**; separate stores, so it
     is a bigger change.
5. **Chat's own thread rail merges into the app sidebar**, so there is one
   chat list, not two.
   - Thread behaviour is unchanged, but the rail's placement changes. Is that
     allowed under "Chat keeps its current behavior"?
   - Recommend: **yes**, as layout only.
   - Related: does the persona selector move to the composer "+" menu?
6. **Device-only settings** (theme, approval default, chat persona, auto
   tools, read-aloud) live in browser storage.
   - To be in "one settings schema" and settable from chat, they need a
     daemon-side home.
   - Recommend: add **scope: device** to the schema, stored by the daemon per
     device id, so chat can set them.
   - Alternative: keep them browser-only and **exclude them from T4** (listed
     as such).
7. **First run: one-tap suggestion versus silent auto-selection** of a
   detected model (§4.6).
   - Silent selection breaks your standing "never auto-switch providers" rule
     and the v1.310.0 "nothing promotes silently" rule.
   - Recommend: **one-tap suggestion**.
8. **Old routes of relocated pages** (`/channels`, `/secrets`, `/connections`,
   `/tools`, `/you`, `/train`, `/marketplace`, `/updates`, `/kanban`): keep
   them as standalone pages, or redirect each to its new Settings or
   Everything anchor?
   - Recommend: **redirect**. Tests that navigate to those routes then need
     the path updates the brief allows, each listed in REPORT.md.
9. **A pasted secret in the composer** is caught by the existing masking
   (`detections/redact.mask`), the message is held, and the credential card
   is offered.
   - It changes Send behaviour for messages that look like keys.
   - Recommend: **yes**.
10. **Per-key permission tiers** in §6. Please confirm or edit the allow /
    ask / ask-floor assignments, especially:
    - default_model **allow**;
    - autonomy_* **ask-floor**;
    - profile fields **allow**;
    - local_primary_policy **ask-floor**.
11. **Pins.** Your own data shows Usage (63), Fleet (43) and Connections (17)
    are daily for you.
    - Recommend: Everything lets you **pin up to 3 entries** into the sidebar
      under the 4 nav items.
    - The brief caps top-level items at 4. Do pins count toward the cap? If
      they do, pins are **out**.
12. **Lint.** The brief requires "lint pass", but the repo has **no linter
    configured** (no ESLint config and no ESLint or Ruff in either package
    file).
    - Recommend: add `ruff check` with a minimal rule set and `next lint`
      with the default Next config, scoped to files the redesign touches;
      otherwise lint would begin with hundreds of existing findings.
    - Alternative: treat `tsc --noEmit` plus `pnpm build` as the lint gate.
13. **Playwright.** The repo has no end-to-end framework; `puppeteer-core` is
    a dev dependency used only by screenshot scripts. T1–T3 need a real
    browser.
    - Recommend: add **Playwright**, as the brief says, running against a
      scratch daemon on a fixed test port with the model pinned to "mock".
      It would use Edge, which is on this PC, or bundled Chromium in CI.
14. **The `comm_trust` bug.** The setting is shown and saved by the UI but
    dropped by the daemon (missing from `_SETTINGS_KEYS`), so it does
    nothing today.
    - Fix now on master as a patch release, or in Phase 2 step S1?
    - Recommend: **now** (small and user-visible).
15. **The `computer_use` enable is never persisted** (lost on restart), and
    **`permissions` has no write route.**
    - Both must gain a durable writer for parity.
    - Recommend: **yes, in S2**.
16. **Name of the directory page:** "Everything", "More", "Library" or "All
    tools"?
    - Recommend: **Everything** ("Library" means files in ChatGPT).
17. **Help:** sidebar footer menu (with version and status) rather than a
    5th nav item.
    - Recommend: **yes**.

---

## 10. Implementation plan (Phase 2, after APPROVED.md)

Each step is small and independently shippable (its own version bump and
release on master after merge), has its own commit on `redesign/calm-ui`,
and states the files it touches and the acceptance tests it advances.
Chat and Build behaviour is guarded by T7 at every step.

| # | Step | Main files | Acceptance tests advanced |
|---|---|---|---|
| S0 | Usage instrumentation: `ui.page_opened` | new `daemon/routes/ui.py`; `dashboard/components/RouteVisitBeacon.tsx`; `app/layout.tsx` | (evidence for later; unit tests) |
| S1 | **Settings schema**: `settings/schema.py`, `GET /settings/schema`, whitelist from the schema (fixes comm_trust) | new `src/iron_jarvis/settings/schema.py`; `daemon/schemas.py` (`_SETTINGS_KEYS` → schema); `routes/settings.py` | T4 (keys enumerable) |
| S2 | **One config writer** with ledger + undo + event; undo re-authorizes; durable writers for `permissions` and `computer_use` | `routes/settings.py`, `daemon/app.py _persist_config`, `routes/{routing,iron_proxy,profile_share,agents(mcp),triggers,skill_learning,fleet,autonomy,computeruse}.py`, `routes/undo.py`, `core/config.py` | T5 (bool/enum/string) |
| S3 | **Chat config tools** `config_list`, `config_get`, `config_set:<key>` generated from the schema; per-key perm keys and tiers; **ConfigCard** with Undo in both chat lanes | new `settings/tools.py`; `core/config.default_permissions`; `tools/permissions.py`; `daemon/chat_turn.py` + `routes/chat.py` (lock-step); `components/chat/ConfigCard.tsx` | T4, T5 |
| S4 | **Credential card**: `POST /config/secret`, `config_secret:<name>` returns a card (no value in model context); composer secret-paste guard (if Q9 = yes); `secret_set` re-routed | `secrets/tools.py`; new `routes/config_secret.py`; `components/chat/CredentialCard.tsx`; `chat/page.tsx` send path | T6 |
| S5 | **Record tools with Undo**: schedules, channels, workflows (+ update/delete), `app_connect` | `scheduling/tools.py`, `comm/tools.py`, `workflows/tools.py`, `connectors/service.py`, `tools/undo.py` (record kinds) | T5 (schedule, connection) |
| S6 | **Surface manifest + Everything page + palette coverage** (settings rows, all surfaces, `/marketplace`) | new `dashboard/lib/surfaces.ts`, `app/everything/page.tsx`; `components/CommandPalette.tsx`; `lib/nav.ts` | **T2** |
| S7 | **Persistent sidebar** (4 items + chats + projects; drawer on phone); retire hubs, HubTabs, the Simple/Advanced switch and AdvancedOnly (per Q2) | `components/Sidebar.tsx`, `app/layout.tsx`, `components/TitleBar.tsx`, `lib/hubs.ts` (removed), `components/HubTabs.tsx` (removed), `lib/uiMode.ts` | **T1** (≤ 4 items) |
| S8 | **Home = chat**: `/` renders the chat surface with the composer focused; conditional status line; Overview content → `/everything#status`; banners into the shell | `app/page.tsx` → chat; new `app/everything/status` section; `components/SimulatedBanner.tsx`, `ProviderDowngradeBanner` | **T1** (no tiles, composer focus), T3 |
| S9 | **First run without a blocking screen**: wizard retired; detection chip in the composer; inline cards for missing setup (per Q7) | `components/FirstRunWizard.tsx` (retired), `components/onboarding/*`, `ModelSwitcher.tsx` → composer | **T3** |
| S10 | **Settings regroup** into 7 groups rendered from the schema, with search and a "Changed here or in chat" ledger panel; Connections, Tools permissions and Autonomy caps move in; old routes redirect (per Q8) | `app/settings/page.tsx` (rewritten), `components/settings/*`, redirects in `next.config` or the route pages | T4, T5 (UI side), T2 |
| S11 | **Visual system pass**: spacing and type tokens, the one-primary test, 390 px pan probe as a test | `tailwind.config.ts`, `app/globals.css`, touched components | DoD visual items |
| S12 | **Playwright harness + T1–T3 e2e**, screenshots to `docs/redesign/screens/` (before = v1.320.0, after), **REPORT.md** | new `dashboard/e2e/*`, `playwright.config.ts`, CI job; `docs/redesign/REPORT.md` | T1, T2, T3, T7, evidence |

**Before-screenshots.** These are captured at the **start** of Phase 2 from
the baseline (v1.320.0) on a scratch daemon, so "before" is exactly the
current product.

**T7 regression gate (every step).** The full backend suite, the full vitest
suite, `tsc` and `pnpm build` (43+ pages) are run, plus any lint the user
approves in Q12.

**Test edits.** Only path or route updates for relocated surfaces are
allowed, each listed in REPORT.md (non-negotiable 7). Pins of the
Simple-mode hubs, home and short Settings view (v1.318–v1.319) would be
**deleted** by S7 and S8 if Q2 is approved. Deleting tests is outside the
brief's allowance, so this needs **explicit approval in APPROVED.md**
(listed as **Q18**):

> **Q18.** If Q2 (retire Simple mode) is approved, may the tests that pin the
> retired v1.318.0–v1.319.0 Simple surfaces be removed? They are
> `home-simple-v1318.test.tsx`, `settings-simple-v1319.test.tsx`, and the
> Simple-mode blocks added to `ux-wave1-shell`, `popout-v1283` and
> `onboarding-welcome-v1197`. Each is replaced by T1/T2 coverage of the new
> shape. Recommend: **yes**.
