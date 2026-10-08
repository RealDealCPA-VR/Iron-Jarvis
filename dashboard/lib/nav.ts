import {
  LayoutDashboard,
  MessageSquare,
  Boxes,
  History,
  Images,
  Sparkles,
  BrainCircuit,
  Code2,
  Workflow,
  Bot,
  Wrench,
  CalendarClock,
  FileSearch,
  FileText,
  KeyRound,
  PlugZap,
  Megaphone,
  Webhook,
  Zap,
  MonitorCog,
  Radar,
  SquareTerminal,
  GitBranch,
  Gauge,
  Server,
  DownloadCloud,
  Settings,
  LifeBuoy,
  BarChart3,
  LayoutTemplate,
  UserRound,
  GraduationCap,
  FolderKanban,
  SquareKanban,
  type LucideIcon,
} from "lucide-react";

/**
 * The navigation catalogue — the SINGLE source of truth for "what pages exist".
 *
 * This used to be a private `const NAV` inside Sidebar.tsx, which meant the only
 * way to reach a page was to already know its name and find it in the rail. The
 * global search ("one front door") needs the same data, so it lives here and the
 * rail consumes it.
 */
export interface NavEntry {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Plain-English terms a user might type to reach this page. */
  aliases: string[];
  /** One short line: what lives on this page (shown in search results). */
  blurb: string;
}

export interface NavSectionDef {
  label: string;
  items: NavEntry[];
}

// ALIASES ARE NOT DECORATION. This month's user reports were all the same
// shape: someone knew what they wanted to DO ("rename endpoint", "redact") and
// could not guess which page name held it. Every entry therefore carries the
// words a NON-TECHNICAL person types, not the words we chose for the rail.
// When you add a page, add its aliases in the same change — the nav test fails
// otherwise, on purpose: an unfindable page may as well not ship.

// THREE HERO SURFACES lead the nav: Chat (talk — with the project panel),
// Build (terminals — make things), Projects (the context spine hub). Every
// other page is support cast, grouped behind them and mostly Advanced-only;
// Sessions/Activity are review surfaces shown only in Advanced mode.
export const NAV: NavSectionDef[] = [
  {
    label: "Work",
    items: [
      {
        href: "/",
        label: "Overview",
        icon: LayoutDashboard,
        aliases: ["home", "dashboard", "status", "health", "start", "main page"],
        blurb: "Start here: ask for something, pick up where you left off, see what is running.",
      },
      // Projects has its own row (below, since v1.151.1) AND lives inside
      // Chat (composer toggle + right-rail workspace).
      {
        href: "/chat",
        label: "Chat",
        icon: MessageSquare,
        // "projects" points here on purpose: the Projects module now lives
        // inside chat, so someone hunting for their project lands right.
        aliases: ["talk", "ask", "message", "projects", "assistant", "conversation"],
        blurb: "Talk to Iron Jarvis — quick answers, or real multi-step work.",
      },
      {
        href: "/terminals",
        label: "Build",
        icon: SquareTerminal,
        // Nobody types "Build" when they want a shell — they type "terminal".
        aliases: ["terminal", "shell", "command line", "console", "code", "cli"],
        blurb: "Command-line windows for coding tools like Claude Code, opened in any project folder.",
      },
      {
        // v1.151.1: Projects was MISSING from this catalogue entirely — no rail
        // row, no search entry, no tile — while the comment above this array
        // calls it one of the three hero surfaces and CLAUDE.md calls it the
        // product's context spine. Its only route in was a shortcut card on the
        // Overview, so removing that card (redundant with the new tile grid)
        // made a real page unreachable and surfaced the older gap.
        href: "/projects",
        label: "Projects",
        icon: FolderKanban,
        // People ask for the THING they are working on, not for "projects".
        aliases: [
          "project",
          "context",
          "workspace",
          "client",
          "matter",
          "case",
          "folder",
          "board",
          "kanban",
        ],
        blurb:
          "Keep each piece of work together — a brief, its folder and files — so every chat and task about it knows the background.",
      },
      {
        href: "/sessions",
        label: "Sessions",
        icon: Boxes,
        aliases: ["runs", "past work", "history", "agent runs", "transcript", "what the agent did"],
        blurb: "Run agents and inspect past sessions.",
      },
      {
        href: "/activity",
        label: "Activity",
        icon: History,
        // "history" answers here AND at /sessions on purpose: both are honest
        // answers to "show me what already happened".
        aliases: ["log", "what happened", "audit", "timeline", "undo", "recent", "history"],
        blurb: "Every action, tool, and decision — replayable, newest first.",
      },
      {
        href: "/creative",
        label: "Creative",
        icon: Images,
        aliases: ["images", "pictures", "video", "music", "art", "generate media"],
        blurb: "Generate and browse images, video, and audio.",
      },
    ],
  },
  {
    label: "Automate",
    items: [
      {
        href: "/workflows",
        label: "Workflows",
        icon: Workflow,
        aliases: ["automation", "pipeline", "steps", "flow chart", "multi-step"],
        blurb: "Wire agents into a visual, multi-step workflow, then run it.",
      },
      {
        href: "/schedules",
        label: "Schedules",
        icon: CalendarClock,
        // "cron" earns a slot even though the page deliberately hides it.
        aliases: [
          "recurring",
          "every day",
          "cron",
          "timer",
          "reminder",
          "remind me",
          "later",
          "morning briefing",
          "daily digest",
          "automate",
        ],
        blurb: "Hand work to an agent on a schedule — it runs and reports to your destinations.",
      },
      // Kanban lives INSIDE a project now (Projects → open a project → Board).
      {
        href: "/templates",
        label: "Templates",
        icon: LayoutTemplate,
        aliases: ["saved prompts", "presets", "reuse", "prompt library", "starters"],
        blurb: "Saved prompts you reuse, with the task and agent prefilled.",
      },
      {
        href: "/agents",
        label: "Agents",
        icon: Bot,
        // v1.309.0: the page is ONE objective worked by the team (v1.308.0) —
        // the old blurb still promised the round table, so search described a
        // screen that no longer exists and "objective" (the word the screen
        // uses) found nothing. "mission" stays as an alias: it is the URL's
        // word. "panel" went: the @-mention panel lives in chat now.
        aliases: [
          "objective",
          "mission",
          "personas",
          "roles",
          "team",
          "your team",
          "subagents",
          "assistants",
        ],
        blurb: "Give Jarvis one objective; your team of agents works it and the result comes back here.",
      },
      {
        href: "/tools",
        label: "Tools",
        icon: Wrench,
        // "auto-approve" is the #1 reported miss: people look for the approval
        // switch under Settings, but per-tool permission lives here.
        aliases: [
          "auto-approve",
          "permissions",
          "approve tools",
          "mcp",
          "mcp servers",
          "packs",
          "plugins",
          "plug-ins",
          "extensions",
          "capabilities",
        ],
        blurb: "What agents can DO — plus per-tool approval and extensions (MCP).",
      },
      {
        href: "/autonomy",
        label: "Autonomy",
        icon: Gauge,
        aliases: ["trust", "on its own", "suggestions", "proposals", "kill switch"],
        blurb: "What Iron Jarvis wants to do on its own — and the switch that stops it.",
      },
      {
        href: "/sentinels",
        label: "Sentinels",
        icon: Radar,
        aliases: ["watchers", "watch a folder", "monitor", "alerts", "triggers"],
        blurb: "Always-on watchers that only ever SUGGEST — never act alone.",
      },
      {
        href: "/computeruse",
        label: "Browser",
        icon: MonitorCog,
        // v1.235.0: the label became Browser (D03/D04 — "Your browser" is the
        // canonical name). The route stays /computeruse so links, docs and the
        // frozen RAIL pin keep resolving. "browser" is NOT an alias any more:
        // nav.test.ts refuses an alias that merely restates the label.
        aliases: [
          "chrome",
          "my browser",
          "tabs",
          "computer control",
          "click for me",
          "screen control",
          "web automation",
          "rpa",
        ],
        blurb: "Read and drive your own browser, plus agent control of a separate one.",
      },
      {
        href: "/webhooks",
        label: "Webhooks",
        icon: Webhook,
        aliases: ["callbacks", "incoming url", "http hooks", "integrations", "post to a url"],
        blurb: "Inbound and outbound webhook registrations.",
      },
      {
        href: "/reflex",
        label: "Reflexes",
        icon: Zap,
        aliases: ["when this then that", "rules", "auto react", "if this", "triggers"],
        blurb: "When a webhook fires or a message arrives, run something automatically.",
      },
      {
        href: "/self-dev",
        // v1.313.0: one name everywhere — the page title, Settings' toggle and
        // Help all say "Self-development"; the old word stays findable.
        label: "Self-development",
        icon: GitBranch,
        aliases: [
          "self-improvement",
          "improve itself",
          "edit its own code",
          "fix itself",
          "source",
          "repo",
        ],
        blurb: "Let Iron Jarvis improve its own source — every change review-gated.",
      },
    ],
  },
  {
    label: "Knowledge",
    items: [
      // "You" sits at the head of Knowledge because everything under it is what
      // Iron Jarvis knows, and this is the part that is about the PERSON. It is
      // an ESSENTIAL href (Sidebar.tsx): a page that decides how every answer
      // is written must not be Advanced-only — that mistake is on record from
      // v1.101.0, where "it's in the sidebar" turned out to mean "in the
      // sidebar for me, not for the user".
      {
        href: "/you",
        label: "You",
        icon: UserRound,
        // People do not search for "profile" — they describe the symptom
        // ("too wordy") or the need ("answer in english", "dyslexia").
        aliases: [
          "profile",
          "my profile",
          "about me",
          "tone",
          "writing style",
          "answer in english",
          "language",
          "response length",
          "too wordy",
          "shorter answers",
          "dyslexia",
          "accessibility",
          "reading level",
          "personality",
          "how it talks to me",
        ],
        blurb:
          "Who you are, how you want answers written, and which language — applied to every model.",
      },
      {
        href: "/train",
        // v1.313.0: the page's own title, so the crumb and the row agree.
        label: "Train Jarvis on me",
        icon: GraduationCap,
        // Nobody types "onboarding". They type what they want to hand over.
        aliases: [
          "train on me",
          "learn my style",
          "writing samples",
          "my voice",
          "import my notes",
          "teach it about me",
          "onboarding",
          "wiki",
          "knowledge base setup",
        ],
        blurb:
          "One on-ramp for your writing, notes, wiki, and past conversations — every doorway in one place.",
      },
      // ONE memory surface (working / lessons / long-term live inside as scopes).
      {
        href: "/memory",
        label: "Memory",
        icon: BrainCircuit,
        // People never call it "Memory" first: they say "where are my notes"
        // or "what does it remember about me".
        aliases: [
          "memory base",
          "notes",
          "brain",
          "remember",
          "what it knows",
          "lessons",
          "long-term",
          "knowledge base",
        ],
        blurb: "One memory surface: working notes, lessons learned, and long-term facts.",
      },
      {
        href: "/documents",
        label: "Documents",
        icon: FileText,
        // "redact"/"pii" are here because scrubbing a client file is a document
        // operation, but people hunt for it under Settings or Secrets.
        aliases: [
          "redact",
          "pii",
          "pdf",
          "word",
          "excel",
          "read a file",
          "write a document",
          "extract text",
        ],
        blurb: "Read text out of any PDF/Word/Excel file, or have a real document written.",
      },
      {
        href: "/filesearch",
        label: "File Search",
        icon: FileSearch,
        aliases: ["find a file", "search my drive", "where is", "grep", "search file contents"],
        blurb: "Search any local drive by file name, contents, or meaning.",
      },
      {
        href: "/skills",
        label: "Skills",
        icon: Sparkles,
        aliases: ["slash commands", "how-tos", "playbooks", "claude skills", "recipes"],
        blurb: "Reusable skills your agents call on, including your Claude Code ones.",
      },
      {
        href: "/artifacts",
        label: "Artifacts",
        icon: Code2,
        aliases: ["scripts", "generated code", "outputs", "saved code", "run again"],
        blurb: "The code agents wrote to get things done — kept, readable, runnable.",
      },
    ],
  },
  {
    label: "Connections",
    items: [
      // Marketplace left the nav: it's reached from the chat "+" menu's
      // Connectors flyout (the route stays alive).
      {
        href: "/connections",
        label: "Connections",
        icon: PlugZap,
        // Every one of these came off a real report. "rename endpoint" in
        // particular sent people to Settings for a week before it landed here.
        aliases: [
          "rename endpoint",
          "endpoints",
          "ollama",
          "vllm",
          "api keys",
          "api key",
          "add a model",
          "openai",
          "anthropic",
          "sign in",
          "accounts",
          "providers",
        ],
        blurb: "Your accounts and model endpoints — connect once, everything can use them.",
      },
      // Advanced-only by construction: NOT in Sidebar's ESSENTIAL_HREFS.
      {
        href: "/fleet",
        label: "Local fleet",
        icon: Server,
        // "ollama"/"vllm" answer HERE as well as at /connections — deliberately.
        // A dead ollama endpoint was the exact thing a user searched for this
        // month, and both pages are a legitimate answer: /connections to edit
        // it, /fleet to see whether it is actually serving anything.
        aliases: [
          "local models",
          "my own models",
          "offline models",
          "ollama",
          "vllm",
          "gpu",
          "loaded models",
          "what is running",
        ],
        blurb: "Every inference endpoint you can reach — what's loaded and serving.",
      },
      {
        href: "/secrets",
        label: "Secrets",
        icon: KeyRound,
        aliases: ["passwords", "credentials", "tokens", "vault", "env vars", "save a password"],
        blurb: "Encrypted credential store — values are write-only, never shown back.",
      },
      {
        href: "/channels",
        label: "Notifications",
        icon: Megaphone,
        // "channels" stays an alias: the page carried that name until v1.113.0
        // and search must keep answering the old vocabulary forever.
        aliases: [
          "notify me",
          "email",
          "slack",
          "telegram",
          "discord",
          "text me",
          "alerts to phone",
          "channels",
          "add a destination",
        ],
        blurb: "Where Iron Jarvis sends alerts, with a test send for each.",
      },
    ],
  },
  {
    label: "System",
    items: [
      {
        href: "/usage",
        label: "Usage",
        icon: BarChart3,
        // Billing anxiety is the trigger: "how much am I spending" comes long
        // before anyone thinks of the word "usage".
        aliases: ["tokens", "cost", "spend", "how much", "billing", "credits", "quota"],
        blurb: "Token spend and run volume across your providers.",
      },
      {
        href: "/updates",
        label: "Updates",
        icon: DownloadCloud,
        aliases: ["version", "update", "upgrade", "new version", "changelog", "release"],
        blurb: "What version you're on, what's new, and the restart-to-install switch.",
      },
      {
        href: "/settings",
        label: "Settings",
        icon: Settings,
        aliases: ["preferences", "options", "config", "setup", "change theme"],
        blurb: "Tune how Iron Jarvis behaves — your preferences, saved across restarts.",
      },
      {
        href: "/help",
        label: "Help",
        icon: LifeBuoy,
        aliases: [
          "how does this work",
          "support",
          "docs",
          "guide",
          "getting started",
          // v1.223.0: the built-in Iron Jarvis Guide lives on this page.
          "ask the guide",
          "ask about iron jarvis",
          "how do i",
          "what does",
          "expert",
        ],
        blurb:
          "What Iron Jarvis is, how to get your first result out of it, and the Guide — ask it anything about this app.",
      },
    ],
  },
];

/** Flattened, in nav order. */
export const NAV_ENTRIES: NavEntry[] = NAV.flatMap((s) => s.items);

/**
 * v1.315.0 (UX wave 3, "kanban-orphan-route") — pages the search box and the
 * title-bar crumb must FIND that are deliberately NOT sidebar rows. NAV_ENTRIES
 * is the rail (nav.test pins it, and the Sidebar renders every row of it), so
 * a page reached from the bell or a deep link lives here instead: the palette
 * offers it (components/CommandPalette PAGE_ITEMS) and `labelForPath` names
 * it — never a new rail row. The Session board was reachable only from the
 * bell, its crumb said the developer word "Kanban", and typing "kanban" sent
 * people to Projects alone (that Projects alias stays — nav.test pins it).
 */
export const NON_RAIL_ENTRIES: NavEntry[] = [
  {
    href: "/kanban",
    label: "Session board",
    icon: SquareKanban,
    aliases: [
      "kanban",
      "board of runs",
      "session lanes",
      "approve sessions",
      "review board",
      "drag to approve",
    ],
    blurb:
      "Every session as a card in its lane — running, waiting for your review, finished or failed. Drag from In Review to approve or reject.",
  },
];

/**
 * The nav label for a pathname — longest-prefix match so nested routes
 * (`/sessions/abc123`) resolve to their parent entry; "/" and unknown paths
 * answer null rather than a guess. The title bar's "where am I" and a pop-out
 * window's title share this one rule (v1.283.0).
 */
export function labelForPath(pathname: string | null | undefined): string | null {
  if (!pathname || pathname === "/") return null;
  const covers = (href: string) => pathname === href || pathname.startsWith(`${href}/`);
  let best: { href: string; label: string } | null = null;
  for (const entry of NAV_ENTRIES) {
    if (entry.href === "/") continue; // matches everything; never a useful label
    if (!covers(entry.href)) continue;
    if (!best || entry.href.length > best.href.length) best = entry;
  }
  // v1.313.0: a crumb override wins over a nav row of the SAME or a shorter
  // href, so "/fleet" reads "Fleet" (its page title) while the sidebar keeps
  // its longer "Local fleet", and the deep-link routes get a crumb at all.
  // v1.315.0: a NON_RAIL_ENTRIES page is named by its own label (one place —
  // the palette shows the same words), ahead of the crumb overrides.
  const crumbs: [string, string][] = [
    ...NON_RAIL_ENTRIES.map((e): [string, string] => [e.href, e.label]),
    ...Object.entries(CRUMB_LABELS),
  ];
  for (const [href, label] of crumbs) {
    if (!covers(href)) continue;
    if (!best || href.length >= best.href.length) best = { href, label };
  }
  return best?.label ?? null;
}

/**
 * v1.313.0 — crumb names that are NOT sidebar rows. The title bar's "/ Page"
 * crumb must name a page the way the page names itself (its PageHeader
 * title), and every route that renders a page must have one. Two kinds live
 * here, deliberately OUTSIDE `NAV` (nav.test pins the sidebar's structure,
 * and these must never appear as rows, palette entries or tiles):
 *  - deep-link routes with no rail row of their own: /marketplace
 *    (its page is titled "Directory"), and /ltm + /lessons, which render the
 *    Memory surface and so ARE the Memory page;
 *  - a route whose sidebar word is longer than its page title (/fleet).
 * /integrations is a server redirect to /connections and needs no entry.
 */
const CRUMB_LABELS: Readonly<Record<string, string>> = {
  // v1.315.0: /kanban moved to NON_RAIL_ENTRIES ("Session board") — it is
  // findable in search now, so it carries a label, aliases and a blurb.
  "/marketplace": "Directory",
  "/ltm": "Memory",
  "/lessons": "Memory",
  "/fleet": "Fleet",
};
