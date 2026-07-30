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
        blurb: "Health, metrics, and live activity for the Iron Jarvis daemon.",
      },
      // Projects is NOT a nav destination anymore: the module lives inside
      // Chat (composer toggle + right-rail workspace); the wide surfaces
      // (board/media/tasks) open from there. The routes stay alive.
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
        blurb: "Live terminals on a free-form canvas, opened in any project folder.",
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
        aliases: ["personas", "roles", "team", "subagents", "assistants", "panel"],
        blurb: "Assemble a panel of agents, give each a role, let them talk it out.",
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
          "capabilities",
        ],
        blurb: "What agents can DO — plus per-tool approval and plug-ins (MCP).",
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
        label: "Computer Control",
        icon: MonitorCog,
        aliases: ["browser", "click for me", "screen control", "web automation", "rpa"],
        blurb: "Let agents drive a real browser, gated behind your approval.",
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
        label: "Self-improvement",
        icon: GitBranch,
        aliases: ["improve itself", "edit its own code", "fix itself", "source", "repo"],
        blurb: "Let Iron Jarvis improve its own source — every change review-gated.",
      },
    ],
  },
  {
    label: "Knowledge",
    items: [
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
        aliases: ["how does this work", "support", "docs", "guide", "getting started"],
        blurb: "What Iron Jarvis is and how to get your first result out of it.",
      },
    ],
  },
];

/** Flattened, in nav order. */
export const NAV_ENTRIES: NavEntry[] = NAV.flatMap((s) => s.items);
