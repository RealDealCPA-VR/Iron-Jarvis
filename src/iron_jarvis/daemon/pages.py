"""THE PAGE TABLE: every page of the dashboard, the name it has on screen, its
address and one plain sentence of what can be done there (v1.506.0,
north-star plan 6.1).

Before this the daemon knew no pages: the feature lookup
(``capability_search``) listed tools, settings, a few page-only steps and
"not offered" lines, so a request whose answer is "that is done on the Rules
page" found nothing, and the lookup's own rule then told the model to say it
found no feature. Now:

* the lookup has one entry per row (``capability_search/index._place_entries``),
  answered as "The page ‹name› (‹address›): ‹what›";
* a row a lookup RETURNED this turn becomes a door under the reply, beside the
  other doors (``chat_turn.with_page_door``, both lanes; the same cap and the
  same one-door-per-address rule as every door);
* the scan test (``tests/test_north_star_pages_v1506.py``) holds every
  ``dashboard/app/*/page.tsx`` to a row and every row's address to a page that
  exists.

A row's NAME is the one the sidebar, Everything and Ctrl K use
(``dashboard/lib/nav.ts``, ``lib/surfaces.ts`` and the Settings rows of
``components/settings/SettingsHome.tsx``); VOCABULARY.md decides the word. A
row's address is the one the other doors already use for that place (the
Directory and the Browser page have one address each), so a lookup's door and
a word door never sit side by side.

Pure data; no import of anything else in the daemon.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Page:
    """One place in the dashboard."""

    #: The lookup's id for it ("page_" + a short name; never a step's id).
    id: str
    #: Its name on screen.
    name: str
    #: Its address.
    href: str
    #: One plain sentence: what can be done there.
    what: str
    #: The ``dashboard/app/<route>/page.tsx`` folders this row stands for (a
    #: page that only forwards, or shows another page, is counted with it).
    routes: tuple[str, ...] = ()
    #: A section of the Settings page (its door says "in Settings").
    in_settings: bool = False

    def door(self) -> dict[str, str]:
        label = f"Open {self.name} in Settings" if self.in_settings else f"Open {self.name}"
        return {"href": self.href, "label": label}


def _section(section: str) -> str:
    return f"/settings?section={section}"


PAGES: tuple[Page, ...] = (
    # --- Work
    Page("page_chat", "Chat", "/chat",
         "Talk to Iron Jarvis; the Chat | Code switch at the top opens Code, where Iron Jarvis writes, runs "
         "and checks code in a folder, one coding session each. A chat is renamed, archived or deleted from "
         "its ⋯ menu there, and a coding session is archived or deleted from its menu in Code's list. Code's "
         "Last night view lists the night's tasks, and Merge all that passed merges every branch whose tests "
         "passed into your main branch, the tests run after each, with Undo; it never pushes, and chat does "
         "not merge them.",
         routes=("chat",)),
    Page("page_build", "Build", "/terminals",
         "Terminals on this PC, and Build's Code view, where the user's own Claude Code, Codex, Pi, OpenCode "
         "or Grok does the work. Under a coding session's newest finished reply there, Commit (its message "
         "already written) and Open a pull request are one press each; chat does not commit that work or "
         "open its pull request.",
         routes=("terminals",)),
    Page("page_projects", "Projects", "/projects",
         "Keep each piece of work together (a brief, its folder and its files) so every chat and task about "
         "it knows the background; make, change, archive or delete a project.",
         routes=("projects",)),
    Page("page_agent_runs", "Agent runs", "/sessions",
         "Every task an agent ran, step by step, with its result.",
         routes=("sessions",)),
    Page("page_session_board", "Session board", "/kanban",
         "Every agent run as a card in its lane (running, waiting for your review, finished, failed); drag "
         "one out of Waiting for you to approve or reject it.",
         routes=("kanban",)),
    Page("page_activity", "Activity", "/activity",
         "Every action, tool and decision, newest first, with Undo for a change that can be undone.",
         routes=("activity",)),
    Page("page_creative", "Creative", "/creative",
         "Make and browse pictures, video and audio.",
         routes=("creative",)),
    # --- Automate
    Page("page_workflows", "Workflows", "/workflows",
         "Wire agents into a multi-step workflow on a visual canvas, save it and run it.",
         routes=("workflows",)),
    Page("page_schedules", "Schedules", "/schedules",
         "Hand a task to an agent on a schedule (every morning, every Monday); it runs and reports to your "
         "destinations. Pause, change or delete a schedule there.",
         routes=("schedules",)),
    Page("page_templates", "Templates", "/templates",
         "Saved prompts you reuse, with the task and the agent filled in, and starter skill bundles to load "
         "or remove; make, change or delete a template.",
         routes=("templates",)),
    Page("page_agents", "Agents", "/agents",
         "Your own agents, and one objective for your team of agents to work on, for tasks bigger than one "
         "chat.",
         routes=("agents",)),
    Page("page_tools", "Tools", "/tools",
         "Add abilities (extensions and your own command tools) and choose which tools ask first.",
         routes=("tools",)),
    Page("page_autonomy", "Autonomy", "/autonomy",
         "What Iron Jarvis wants to do on its own, your goals, and Emergency stop, which stops it all.",
         routes=("autonomy",)),
    Page("page_watchers", "Watchers", "/sentinels",
         "Watch a folder and get a suggestion when files arrive or change.",
         routes=("sentinels",)),
    Page("page_browser", "Browser", "/computeruse",
         "Set up and pair the browser add-on, choose what Iron Jarvis may do in your browser, and the Jarvis "
         "browser.",
         routes=("computeruse",)),
    Page("page_webhooks", "Webhooks", "/webhooks",
         "Let another app start work here, or send Iron Jarvis's events to a web address.",
         routes=("webhooks",)),
    Page("page_rules", "Rules", "/reflex",
         "When a webhook fires or a message arrives, run something automatically; make, pause or delete a "
         "rule.",
         routes=("reflex",)),
    Page("page_self_development", "Self-development", "/self-dev",
         "Let Iron Jarvis improve its own code, with every change reviewed by you first.",
         routes=("self-dev",)),
    # --- Knowledge
    Page("page_you", "You", "/you",
         "Who you are, how you want answers written and in which language, for every model.",
         routes=("you",)),
    Page("page_train", "Train Jarvis on me", "/train",
         "Hand over your writing, notes, wiki and past conversations, all in one place.",
         routes=("train",)),
    Page("page_memory", "Memory", "/memory",
         "What Iron Jarvis remembers: working notes, lessons, long-term facts, your preferences, your memory "
         "bases, and the work it did in each folder (where a folder can be forgotten).",
         routes=("memory", "lessons", "ltm")),
    Page("page_documents", "Documents", "/documents",
         "Read the text out of a PDF, Word or Excel file, redact a document, or have a document written.",
         routes=("documents",)),
    Page("page_file_search", "File Search", "/filesearch",
         "Search a drive on this PC by file name, contents or meaning.",
         routes=("filesearch",)),
    Page("page_skills", "Skills", "/skills",
         "Reusable skills your agents and chats call on, including your Claude Code ones.",
         routes=("skills",)),
    Page("page_what_agents_made", "What agents made", "/artifacts",
         "The scripts and files agents wrote to get things done, kept so you can read them and run them "
         "again.",
         routes=("artifacts",)),
    Page("page_improvements", "Improvements", "/improvements",
         "Every suggestion Iron Jarvis is waiting on you to accept or decline: preferences it noticed, skill "
         "drafts, changes to an agent's instructions, lessons, memory tidy-ups and requests for a tool.",
         routes=("improvements",)),
    Page("page_clients", "Clients", "/clients",
         "The people and businesses you do work for, each one's folders, work and preferences, and the names "
         "Iron Jarvis noticed that wait for your answer.",
         routes=("clients",)),
    # --- Connections
    Page("page_connections", "Connections", "/connections",
         "Your model plans and sign-ins, API keys, your own model servers, your web services and several "
         "accounts.",
         routes=("connections", "integrations")),
    Page("page_directory", "Directory", "/settings?section=connections-apps",
         "Connect Notion, GitHub, Slack, Gmail, Microsoft 365 and other apps.",
         routes=("marketplace",), in_settings=True),
    Page("page_model_servers", "Model servers", "/fleet",
         "Every model server you can reach, and what each one has loaded.",
         routes=("fleet",)),
    Page("page_secrets", "Secrets", "/secrets",
         "The encrypted store of your keys and passwords: add, replace or remove one (a value is never shown "
         "back).",
         routes=("secrets",)),
    Page("page_notifications", "Notifications", "/channels",
         "Where Iron Jarvis messages you (Telegram, Slack, Discord, email), and two-way chat with it from your "
         "phone.",
         routes=("channels",)),
    # --- System
    Page("page_usage", "Usage", "/usage",
         "What each AI model used and cost, and how much work ran.",
         routes=("usage",)),
    Page("page_updates", "Updates", "/updates",
         "What version you are on and what is new, Stable or Fresh updates, and the switch that installs an "
         "update when Iron Jarvis restarts.",
         routes=("updates",)),
    Page("page_settings", "Settings", "/settings",
         "Every setting of Iron Jarvis, kept across restarts.",
         routes=("settings",)),
    Page("page_help", "Help", "/help",
         "How Iron Jarvis works, the Handbook, and the Guide, which answers questions about the app.",
         routes=("help",)),
    Page("page_everything", "Everything", "/everything",
         "Every part of Iron Jarvis on one page, each with one line about what it is for.",
         routes=("everything",)),
    # --- Settings sections that are places of their own
    Page("page_local_models", "What your local models can do", _section("models-local"),
         "Each local model's measured skills, and which tasks it is trusted with.",
         in_settings=True),
    Page("page_model_team", "Model team", _section("models-team"),
         "The agents that take pieces of work, each on its own model, and how much of each plan they used.",
         in_settings=True),
    Page("page_other_computers", "Your other computers", _section("connections-computers"),
         "Pair this computer with another one running Iron Jarvis (one shows a six-digit pairing code, the "
         "other types it), so each can hand the other tasks; check or remove a paired computer.",
         in_settings=True),
    Page("page_memory_profile", "Memory profile", _section("memory-brain-profile"),
         "Your main memory base, and how Iron Jarvis searches it and saves new knowledge to it.",
         in_settings=True),
    Page("page_standing_grants", "Standing grants", _section("permissions-grants"),
         "Approvals you gave that stay in place; revoke any of them.",
         in_settings=True),
    Page("page_maintenance", "Maintenance", _section("system-maintenance"),
         "Back up Iron Jarvis now, restore it from a backup, and restart it.",
         in_settings=True),
    Page("page_access_token", "Access token", _section("system-token"),
         "The key a browser outside the app uses to reach Iron Jarvis.",
         in_settings=True),
)

#: id -> row.
BY_ID: dict[str, Page] = {p.id: p for p in PAGES}


def door_for(page_id: str) -> "dict[str, str] | None":
    """The door of the row *page_id*, or ``None`` for an id that is no row
    (an id is only ever looked up here, never turned into an address)."""
    page = BY_ID.get(str(page_id or ""))
    return page.door() if page is not None else None
