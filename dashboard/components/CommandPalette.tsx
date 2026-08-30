"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import {
  Megaphone,
  Search,
  PlugZap,
  Cpu,
  Plus,
  CornerDownLeft,
  MessageSquare,
  Sparkles,
  Database,
  GraduationCap,
  Eraser,
  Package,
  LayoutTemplate,
  BarChart3,
  Bot,
  FolderKanban,
  CalendarClock,
  Phone,
  Users,
  Boxes,
  type LucideIcon,
} from "lucide-react";
import { NAV_ENTRIES } from "@/lib/nav";
import { scorePalette, type PaletteItem } from "@/lib/palette";
import { get } from "@/lib/api";
import { normalizeIso } from "@/lib/format";
import { recordOpen } from "@/lib/appTiles";

/**
 * THE FRONT DOOR (v1.111.0).
 *
 * This used to be a private list of routes copy-pasted out of the sidebar: it
 * went stale the moment a page was renamed, and it could only ever answer
 * "which PAGE is that" — never "which skill", "which chat", "which project",
 * and never "the thing I want is a control halfway down a page". Now it is the
 * single results surface: pages come from the one nav catalogue (lib/nav.ts),
 * deep links reach mid-page capabilities, and the live daemon supplies skills,
 * threads and projects. Ranking lives in lib/palette.ts so it can be unit
 * tested instead of eyeballed.
 *
 * v1.142.0 adds the one thing a client-side matcher can never do: search what
 * was SAID. "In your conversations" is a live lane over the daemon's full-text
 * index — it ranks itself, it merges below the name matches, and on a daemon
 * that has no such index it simply isn't there.
 */

/** A palette item plus the two things the pure scorer has no business knowing:
 *  what it looks like, and what pressing Enter does when it isn't a link. */
interface PaletteRow extends PaletteItem {
  icon: LucideIcon;
  /** When set, run this instead of navigating to href (e.g. open the switcher). */
  run?: () => void;
  /** History lane only: the matching line, already split into plain and
   *  highlighted runs. Rendered as TEXT NODES — see splitSnippet. */
  parts?: SnippetPart[];
  /** History lane only: when the conversation happened ("3d ago" / "Mar 12"). */
  when?: string;
  /** History lane only: a truer badge than the generic kind ("Phone", "Session"). */
  badge?: string;
}

/** Row badge text — small, so a result never leaves you guessing what it IS. */
const KIND_LABEL: Record<PaletteItem["kind"], string> = {
  page: "Page",
  action: "Action",
  skill: "Skill",
  project: "Project",
  thread: "Thread",
  // History rows carry their own, more specific badge (see HISTORY_KINDS); this
  // is only the fallback that keeps the map total.
  history: "Conversation",
};

// ── Pages ────────────────────────────────────────────────────────────────────
// Straight off NAV_ENTRIES. Aliases and blurbs come along untouched: they were
// written for exactly this box (see the essay at the top of lib/nav.ts), and a
// second hand-maintained copy here is what rotted last time.
const PAGE_ITEMS: PaletteRow[] = NAV_ENTRIES.map((e) => ({
  id: `page:${e.href}`,
  kind: "page" as const,
  label: e.label,
  blurb: e.blurb,
  aliases: e.aliases,
  href: e.href,
  icon: e.icon,
}));

// ── Deep links ───────────────────────────────────────────────────────────────
// WHY a curated list and not just pages: this month's top complaint was not
// "I can't find the page", it was "I found the page and still can't find the
// thing" — the capability lives in a tab, a panel, or a button halfway down.
// Each row below is a capability someone actually hunted for, addressed by the
// query string the page already understands. Keep it SHORT and evidence-driven;
// this is not a second nav.
const DEEP_LINK_ITEMS: PaletteRow[] = [
  {
    id: "deep:redact",
    kind: "page",
    label: "Documents → Redact PII",
    blurb: "Scrub names, SSNs and account numbers out of a file before sharing it.",
    aliases: ["redact", "pii", "black out", "remove personal data"],
    href: "/documents?focus=redact",
    icon: Eraser,
  },
  {
    id: "deep:endpoints",
    kind: "page",
    label: "Connections → Your endpoints",
    blurb: "Rename, re-point or remove a model endpoint you added.",
    aliases: ["rename endpoint", "remove endpoint", "custom endpoint"],
    href: "/connections?focus=endpoints",
    icon: PlugZap,
  },
  {
    id: "deep:packs",
    kind: "page",
    label: "Tools → Extensions",
    blurb: "Connect an extension and set what it may run without asking.",
    aliases: ["auto-approve", "mcp", "connect a pack", "connect a plug-in", "plug-ins", "extensions"],
    href: "/tools?focus=packs",
    icon: Package,
  },
  {
    id: "deep:schedule-task",
    kind: "page",
    label: "Schedules → Schedule a task",
    blurb: "An agent runs it on repeat and sends the result to your destinations.",
    aliases: ["schedule a task", "remind me", "every morning", "daily digest", "recurring task"],
    href: "/schedules?focus=add",
    icon: CalendarClock,
  },
  {
    id: "deep:add-destination",
    kind: "page",
    label: "Notifications → Add a destination",
    blurb: "Send alerts to your phone, Slack, Discord, or email — Telegram takes ~2 minutes.",
    aliases: ["telegram", "slack", "discord", "notify my phone", "add destination"],
    href: "/channels?focus=add",
    icon: Megaphone,
  },
  {
    id: "deep:import-ai-memories",
    kind: "page",
    label: "Memory → Import from another AI",
    blurb: "Bring what ChatGPT, Claude or Gemini remembers about you into Iron Jarvis.",
    aliases: ["import chatgpt memories", "chatgpt memory", "import memories", "bring memories", "gemini saved info"],
    // scope picks the tab, focus picks the card, and view=list overrides a
    // persisted graph view (the card only exists in list view).
    href: "/memory?scope=longterm&view=list&focus=import-ai",
    icon: Database,
  },
  {
    id: "deep:add-base",
    kind: "page",
    label: "Memory → Add a memory base",
    blurb: "Point long-term memory at an Obsidian vault, a folder, or Notion.",
    aliases: ["add a memory base", "new memory base", "obsidian", "vault", "notion"],
    // BOTH params, deliberately: /memory?focus=add-base alone opens the page on
    // its DEFAULT (working) scope, where there is no such control — the user
    // lands on the wrong tab and concludes the search lied. `scope` picks the
    // tab, `focus` picks the control inside it; neither implies the other.
    href: "/memory?scope=longterm&focus=add-base",
    icon: Database,
  },
  {
    id: "deep:lessons",
    kind: "page",
    label: "Memory → What I've learned",
    blurb: "Lessons distilled out of past runs, newest first.",
    aliases: ["lessons", "learned", "insights", "distill"],
    href: "/memory?scope=lessons",
    icon: GraduationCap,
  },
  {
    id: "deep:ltm",
    kind: "page",
    label: "Memory → Long-term",
    blurb: "The durable facts and notes Iron Jarvis recalls across sessions.",
    aliases: ["long-term", "obsidian", "notion", "vault", "brain", "store"],
    href: "/memory?scope=longterm",
    icon: Database,
  },
];

// ── Actions ──────────────────────────────────────────────────────────────────
// Things that DO something rather than go somewhere. They also double as the
// empty-query teaching surface below, which is why the list stays at five: an
// opening screen that lists twenty options teaches nothing.
const ACTION_ITEMS: PaletteRow[] = [
  {
    id: "action:new-session",
    kind: "action",
    label: "New session",
    blurb: "Hand Iron Jarvis a task and let an agent run it.",
    aliases: ["run", "task", "agent", "start", "launch", "create"],
    href: "/sessions?new=1",
    icon: Plus,
  },
  {
    id: "action:connect-a-model",
    kind: "action",
    label: "Connect a model",
    blurb: "Add a provider account, API key, or local endpoint.",
    aliases: [
      "llm",
      "api key",
      "oauth",
      "anthropic",
      "openai",
      "google",
      "grok",
      "xai",
      "provider",
      "account",
      "login",
    ],
    href: "/connections",
    icon: PlugZap,
  },
  {
    id: "action:switch-model",
    kind: "action",
    label: "Switch model",
    blurb: "Change which model answers, without leaving this page.",
    aliases: [
      "provider",
      "model",
      "default",
      "active",
      "grok",
      "claude",
      "gpt",
      "gemini",
      "change",
      "router",
    ],
    icon: Cpu,
    // The switcher is mounted elsewhere and owns its own open state; we only
    // knock on its door (same contract the TitleBar uses for us).
    run: () => window.dispatchEvent(new Event("ij:open-switcher")),
  },
  {
    id: "action:view-usage",
    kind: "action",
    label: "View usage & cost",
    blurb: "Token spend and run volume across your providers.",
    aliases: ["tokens", "cost", "spend", "report", "analytics", "billing"],
    href: "/usage",
    icon: BarChart3,
  },
  {
    id: "action:open-templates",
    kind: "action",
    label: "Open task templates",
    blurb: "Saved prompts with the task and agent prefilled.",
    aliases: ["preset", "saved", "reusable", "task", "template"],
    href: "/templates",
    icon: LayoutTemplate,
  },
];

/** GET /skills → `{skills: [...]}`. */
interface SkillRow {
  name: string;
  description?: string;
  source?: string;
}
/** GET /chat/threads → `{threads: [...]}`, newest first, capped at 100 server-side. */
interface ThreadRow {
  id: string;
  title: string;
  persona?: string;
  messages?: number | unknown[];
  updated_at?: string;
}
/** GET /projects → `{projects: [...]}` (the daemon sends far more per row). */
interface ProjectRow {
  id: string;
  name: string;
  brief?: string;
}

/** Blurbs are a one-line hint, not a paragraph — clip before they wrap. */
function clip(text: string | undefined, max = 80): string | undefined {
  const t = (text || "").replace(/\s+/g, " ").trim();
  if (!t) return undefined;
  return t.length > max ? `${t.slice(0, max - 1)}…` : t;
}

/** Newest ~20 threads is the honest ceiling: past that it is archaeology, and
 *  every extra row is one more thing the ranker has to out-score. */
const THREAD_LIMIT = 20;
/** Rows shown for a query. Nine leaves the ask row visible without scrolling. */
const RESULT_LIMIT = 9;
/** Recent chats on the empty screen — a taste, not the whole history. */
const EMPTY_THREADS = 5;

// ── "In your conversations" (v1.142.0) ───────────────────────────────────────
// THE NEED: "that chat from March about the S-corp election". Everything above
// this line matches TITLES — the twenty newest threads, by name. What the user
// actually remembers is something SAID inside a conversation, which no title
// search can reach and which no amount of ranking on the client can invent.
// GET /search/history is the daemon's full-text index over every past chat,
// message thread, round-table and session; this lane is its only UI.
//
// It is deliberately NOT part of scorePalette's input. That matcher ANDs
// substrings over labels, aliases and blurbs — feeding it rows the index
// already ranked by relevance would re-rank them by a rule that cannot see the
// message text, and would drop most of them outright.

/** Section label above the lane. */
const HISTORY_HEADER = "In your conversations";
/** Rows RENDERED: enough to recognise the conversation you meant, few enough
 *  that the ask row and the title matches above stay on screen. */
const HISTORY_LIMIT = 5;
/** Rows ASKED FOR — deliberately wider than the five that are shown.
 *
 * The index answers per MESSAGE, not per conversation (search/index.py's MATCH
 * has no GROUP BY), so one chatty thread can legitimately own every row of a
 * five-row answer. Collapsed to one row per conversation below, that is a lane
 * showing ONE result for a query with dozens of matching conversations — the
 * exact failure this feature exists to fix. Asking for a wider window costs a
 * local SQLite index nothing (its own ceiling is 200) and is the only way five
 * DISTINCT conversations can survive the collapse. */
const HISTORY_FETCH = 20;
/** Below three characters the index returns noise, and every keystroke would
 *  cost a round trip for it. */
const HISTORY_MIN_CHARS = 3;
/** Long enough that a typed word costs one request, not six; short enough that
 *  the lane lands while you are still reading the rows above it. */
const HISTORY_DEBOUNCE_MS = 180;
/** A snippet is one line of a result row, not a paragraph. */
const SNIPPET_MAX = 150;

/** GET /search/history?q&limit → `{hits, mode, count}`. Every field is optional
 *  here on purpose: this is untrusted-shaped data from an endpoint that may be
 *  older, newer, or absent, and a missing field must cost one row, not the box. */
interface HistoryHit {
  kind?: string;
  ref?: string;
  thread_id?: string;
  title?: string;
  /** FTS5 snippet(): the matching text with `[…]` markers around the terms. */
  snippet?: string;
  role?: string;
  at?: string | null;
  project_id?: string;
  score?: number;
  seq?: number;
}
interface HistoryResponse {
  hits?: HistoryHit[];
  mode?: string;
  count?: number;
}

/** One run of snippet text: `hit` marks the part the index matched. */
interface SnippetPart {
  text: string;
  hit: boolean;
}

/** What each indexed kind IS, in the user's words. "Round-table" and "Session"
 *  are the app's own nouns; a kind we don't know about is dropped rather than
 *  rendered as a mystery row. */
const HISTORY_KINDS: Record<string, { badge: string; icon: LucideIcon }> = {
  chat: { badge: "Chat", icon: MessageSquare },
  comm: { badge: "Phone", icon: Phone },
  round: { badge: "Round-table", icon: Users },
  session: { badge: "Session", icon: Boxes },
};

/** Where Enter goes. Chat AND messaging threads both open through the chat
 *  page's `?thread=` param (GET /chat/threads/{id} serves both — a messaging
 *  thread simply comes back owned by the daemon), which is the exact shape the
 *  live thread rows above already use. Sessions are a real route. The
 *  round-table had no per-thread URL, so `/agents?thread=` was a link that
 *  landed on the page and then auto-selected the NEWEST thread — a result that
 *  opens a different conversation than the one you clicked, and says nothing
 *  about it. app/agents/page.tsx now honours the param, so every kind here
 *  opens the conversation the row is actually about. */
function historyHref(kind: string, ref: string): string {
  const id = encodeURIComponent(ref);
  if (kind === "session") return `/sessions/${id}`;
  if (kind === "round") return `/agents?thread=${id}`;
  return `/chat?thread=${id}`;
}

/**
 * Split an FTS5 snippet into plain and matched runs.
 *
 * The snippet is USER AND MODEL TEXT — whatever was typed into a chat months
 * ago — so it is rendered as React text nodes and never as HTML. This function
 * exists precisely so that no dangerouslySetInnerHTML is needed to show which
 * words matched: the `[markers]` become structure here, at parse time.
 *
 * Every hostile shape degrades to plain text instead of throwing: an unclosed
 * `[`, a stray `]`, `[]`, nesting, and a snippet that is nothing but brackets.
 * A user who genuinely typed square brackets gets them highlighted — a cosmetic
 * false positive, and the only alternative would be trusting a marker scheme
 * the text itself can forge.
 */
function splitSnippet(raw: string | undefined): SnippetPart[] {
  const text = (raw || "").replace(/\s+/g, " ").trim();
  if (!text) return [];
  const parts: SnippetPart[] = [];
  let i = 0;
  while (i < text.length) {
    const open = text.indexOf("[", i);
    if (open < 0) break;
    const close = text.indexOf("]", open + 1);
    if (close < 0) break; // unclosed marker: the remainder is plain text
    const inner = text.slice(open + 1, close);
    if (!inner) {
      // "[]" marks nothing. Keep it literal so the two characters don't vanish.
      parts.push({ text: text.slice(i, close + 1), hit: false });
      i = close + 1;
      continue;
    }
    if (open > i) parts.push({ text: text.slice(i, open), hit: false });
    parts.push({ text: inner, hit: true });
    i = close + 1;
  }
  if (i < text.length) parts.push({ text: text.slice(i), hit: false });
  return clampParts(parts);
}

/**
 * Trim the PARSED runs to SNIPPET_MAX visible characters.
 *
 * Clipping the raw string first (the obvious order) was wrong twice over: the
 * `[]` markers spent the character budget the reader never sees, and a cut that
 * landed mid-marker left a bare `[` in the visible text and silently dropped
 * the highlight it opened — "…before the [el…" instead of "…before the el…".
 * Both were reproducible on ordinary snippets, because FTS5 puts a marker pair
 * around every matched term and there are usually several.
 */
function clampParts(parts: SnippetPart[]): SnippetPart[] {
  let total = 0;
  for (const part of parts) total += part.text.length;
  if (total <= SNIPPET_MAX) return parts;
  const out: SnippetPart[] = [];
  let used = 0;
  for (const part of parts) {
    const room = SNIPPET_MAX - 1 - used; // -1 leaves space for the ellipsis
    if (room <= 0) break;
    if (part.text.length <= room) {
      out.push(part);
      used += part.text.length;
      continue;
    }
    // The cut falls inside this run. Keep what fits — a truncated highlight is
    // still a highlight — and never split a surrogate pair, which would render
    // the last emoji as a replacement glyph.
    const cut = part.text.slice(0, room);
    const safe = /[\uD800-\uDBFF]$/.test(cut) ? cut.slice(0, -1) : cut;
    if (safe) out.push({ text: safe, hit: part.hit });
    break;
  }
  // The ellipsis is narration, not content: always its own plain run so it can
  // never end up inside a <mark>.
  out.push({ text: "…", hit: false });
  return out;
}

/** "3d ago" while it is still recent, "Mar 12" once it isn't — because past a
 *  week nobody thinks "142 days ago", they think "that one from March", which
 *  is the whole reason this lane exists. normalizeIso first: the daemon writes
 *  naive UTC, which a browser would otherwise read as local time (and a future
 *  timestamp would print as a negative age). */
function historyWhen(iso: string | null | undefined): string | undefined {
  if (!iso) return undefined;
  const d = new Date(normalizeIso(iso));
  if (Number.isNaN(d.getTime())) return undefined;
  const mins = Math.floor((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "just now"; // covers clock skew (a negative age) too
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

/** Hits → rows, dropping anything unrenderable and collapsing repeats.
 *  ONE ROW PER CONVERSATION: the index can legitimately return three messages
 *  from the same thread, and three rows with the same title stacked on top of
 *  each other read as a bug. The first is kept — it is the best-scoring one. */
function historyRows(hits: HistoryHit[] | undefined): PaletteRow[] {
  const rows: PaletteRow[] = [];
  const seen = new Set<string>();
  // Not `hits ?? []`: a body that answers with something other than a list
  // (an older shape, a proxy's error page) must be no lane, not a throw.
  if (!Array.isArray(hits)) return rows;
  for (const hit of hits) {
    const kind = String(hit?.kind || "");
    const meta = HISTORY_KINDS[kind];
    const ref = String(hit?.ref || hit?.thread_id || "").trim();
    if (!meta || !ref) continue;
    const href = historyHref(kind, ref);
    if (seen.has(href)) continue;
    seen.add(href);
    rows.push({
      id: `history:${href}`,
      kind: "history",
      label: String(hit?.title || "").trim() || "(untitled)",
      href,
      icon: meta.icon,
      badge: meta.badge,
      parts: splitSnippet(hit?.snippet),
      when: historyWhen(hit?.at),
    });
    if (rows.length >= HISTORY_LIMIT) break;
  }
  return rows;
}

/** lib/api's `get` forwards any extra init fields straight into fetch (see
 *  `api`'s `...rest`), so an AbortSignal rides along fine — its published opts
 *  type just doesn't advertise the field. Narrowed here rather than widened in
 *  lib/api.ts, which this lane does not own. */
type AbortableGet = <T>(path: string, opts: { signal: AbortSignal }) => Promise<T>;
const getAbortable = get as unknown as AbortableGet;

export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const prevFocusRef = useRef<HTMLElement | null>(null);

  // Live item sources, fetched once (see ensureLive).
  const [skillItems, setSkillItems] = useState<PaletteRow[]>([]);
  const [threadItems, setThreadItems] = useState<PaletteRow[]>([]);
  const [projectItems, setProjectItems] = useState<PaletteRow[]>([]);

  // The "In your conversations" lane. Unlike the three catalogues above, this
  // one is fetched PER QUERY (see the effect below), never once.
  const [historyItems, setHistoryItems] = useState<PaletteRow[]>([]);
  /** Bumped by every query change. A response whose generation is stale is
   *  DROPPED, not merged: two requests in flight can settle out of order, and
   *  the older one landing last would paint the previous query's results under
   *  the current query's rows. */
  const historyGenRef = useRef(0);
  /** A 404 means an older daemon with no index at all. Asking again on every
   *  keystroke for the rest of the session would be pure noise, so the lane
   *  switches itself off — silently, which is the entire degrade story. */
  const historyOffRef = useRef(false);

  /**
   * ONE GATE PER SOURCE, not one shared flag.
   *
   * A single `fetchedRef` was wrong twice over, and both bugs only showed up
   * with a flaky daemon:
   *  1. ANY one failure reopened the gate for ALL THREE, so the next open
   *     re-requested two catalogues that had already succeeded.
   *  2. It reopened that gate while the other two were still IN FLIGHT — so a
   *     close/reopen inside that window fired a second copy of requests that
   *     had never settled.
   * Keyed refs fix both: a source is asked at most once while it is in flight
   * or after it has succeeded, and the source that failed is the only one
   * retried. A source that succeeds with an EMPTY list is a success, not a
   * miss — it stays gated instead of being re-asked on every single open.
   */
  const loadedRef = useRef<Record<string, boolean>>({});

  function once(key: string, load: () => Promise<unknown>) {
    if (loadedRef.current[key]) return;
    loadedRef.current[key] = true;
    // Every failure degrades to silence, on purpose: a stopped daemon must not
    // take navigation down with it. The static pages/actions/deep links are
    // pure client data and keep working, which is exactly when you most need to
    // reach Connections or Updates.
    load().catch(() => {
      loadedRef.current[key] = false;
    });
  }

  /**
   * Fetch the live catalogue on FIRST open — never at mount. This component is
   * mounted on every page from layout.tsx; fetching eagerly would put three
   * requests on every cold load for a UI most loads never open.
   */
  function ensureLive() {
    once("skills", () =>
      get<{ skills: SkillRow[] }>("/skills").then((d) =>
        setSkillItems(
          (d.skills ?? []).map((s) => ({
            id: `skill:${s.name}`,
            kind: "skill" as const,
            // Shown the way it is INVOKED. Someone who has typed "/redact" in
            // chat is looking for that string, not for "Redact (skill)".
            label: `/${s.name}`,
            blurb: clip(s.description),
            href: `/chat?skill=${encodeURIComponent(s.name)}`,
            icon: Sparkles,
          })),
        ),
      ),
    );

    once("threads", () =>
      get<{ threads: ThreadRow[] }>("/chat/threads").then((d) =>
        setThreadItems(
          (d.threads ?? []).slice(0, THREAD_LIMIT).map((t) => ({
            id: `thread:${t.id}`,
            kind: "thread" as const,
            label: t.title || "(untitled)",
            // Date only. Deliberately NO generic aliases ("chat", "thread") on
            // live rows: twenty items answering to one common word would push
            // the page you actually wanted off a nine-row list.
            blurb: clip(threadWhen(t.updated_at)),
            href: `/chat?thread=${encodeURIComponent(t.id)}`,
            icon: MessageSquare,
          })),
        ),
      ),
    );

    once("projects", () =>
      get<{ projects: ProjectRow[] }>("/projects").then((d) =>
        setProjectItems(
          (d.projects ?? []).map((p) => ({
            id: `project:${p.id}`,
            kind: "project" as const,
            label: p.name,
            blurb: clip(p.brief),
            href: `/projects/${p.id}`,
            icon: FolderKanban,
          })),
        ),
      ),
    );
  }

  // Ctrl/⌘K toggles; Esc closes. Unchanged — the shortcut is muscle memory.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // `e.key` is optional on synthetic/IME events; a throw here would take
      // the whole listener (and therefore Esc) down with it.
      if ((e.metaKey || e.ctrlKey) && e.key?.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    // The visible search button in the TitleBar dispatches this. An event (not
    // a prop or a store) keeps the palette the sole owner of its open state, so
    // any surface can offer a way in without any of them holding a duplicate.
    // OPEN, never toggle: clicking a button labelled "Search" must never be the
    // thing that closes the search.
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("ij:open-palette", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("ij:open-palette", onOpen);
    };
  }, []);

  useEffect(() => {
    if (open) {
      // Remember what had focus so we can restore it on close (don't dump focus to
      // <body> — a keyboard/screen-reader user would lose their place).
      prevFocusRef.current = document.activeElement as HTMLElement | null;
      setQuery("");
      setActive(0);
      ensureLive();
      requestAnimationFrame(() => inputRef.current?.focus());
    } else {
      prevFocusRef.current?.focus?.();
    }
    // ensureLive is stable enough for this effect's purpose (it self-gates on a
    // ref), and listing it would only churn the dependency array.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  /**
   * The one asynchronous lane: full-text search over past conversations.
   *
   * Three guards, each for a failure this pattern has in it:
   *  - DEBOUNCE. The daemon is asked once the typing pauses, not six times
   *    across "s-corp".
   *  - ABORT. The moment the query changes, the outstanding request is torn
   *    down instead of being left to finish work nobody will read.
   *  - GENERATION. Abort is best-effort (a response already in the pipe still
   *    resolves), so the answer is checked against the query counter before it
   *    is allowed anywhere near the visible rows. Belt AND braces, because
   *    "late response overwrites the newer one" is invisible in testing and
   *    infuriating in use.
   *
   * Results are held while the NEXT query is in flight rather than being
   * cleared per keystroke: blanking the lane on every character makes rows
   * appear and disappear under the arrow keys, and a hit list that lags one
   * word behind for 180ms is strictly calmer than one that strobes. They are
   * cleared outright the moment the query is too short to search — including
   * when it is emptied, and when the palette closes.
   */
  useEffect(() => {
    // Every run orphans whatever the previous one might still be waiting on.
    const gen = ++historyGenRef.current;
    const needle = open ? query.trim() : "";
    if (!needle || needle.length < HISTORY_MIN_CHARS || historyOffRef.current) {
      // Identity-stable when it is already empty: no lane, no re-render, no
      // chance of nudging the highlight for a list that did not change.
      setHistoryItems((prev) => (prev.length ? [] : prev));
      return;
    }
    let ctrl: AbortController | null = null;
    const timer = setTimeout(() => {
      ctrl = new AbortController();
      getAbortable<HistoryResponse>(
        `/search/history?q=${encodeURIComponent(needle)}&limit=${HISTORY_FETCH}`,
        { signal: ctrl.signal },
      )
        .then((d) => {
          if (gen !== historyGenRef.current) return; // a newer query won
          setHistoryItems(historyRows(d?.hits));
        })
        .catch((err: unknown) => {
          // Duck-typed rather than instanceof ApiError so this stays honest
          // under any api client that reports a status.
          //
          // WHICH FAILURES TURN THE LANE OFF, and why only these two: 404 is a
          // daemon with no such route (verified — nothing registers a /search
          // prefix before v1.142 and there is no catch-all to dress the miss up
          // as something else, so FastAPI answers a plain 404). 405 is the path
          // existing for some other method: a different daemon, same verdict.
          // Neither can start working later in this page's life, so asking
          // again on every keystroke for the rest of the session is pure noise.
          // This is the pair app/agents/page.tsx already reads as "this daemon
          // doesn't have that feature".
          //
          // Everything else stays retryable ON PURPOSE. status 0 is offline or
          // an abort (lib/api reports both that way); 500 is a daemon that fell
          // over or is still booting; 401/403 is a token the user is about to
          // fix. Latching on any of those would cost the user the lane for the
          // rest of the session over something that resolves itself in seconds.
          const status = (err as { status?: number } | null)?.status;
          if (status === 404 || status === 405) {
            historyOffRef.current = true;
          }
          // Best effort, always: an offline daemon, a 500, a syntax the index
          // hated, an abort — every one of them is an empty lane and no error
          // UI. Navigation must never be worse for having offered search.
          if (gen !== historyGenRef.current) return;
          setHistoryItems((prev) => (prev.length ? [] : prev));
        });
    }, HISTORY_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      ctrl?.abort();
    };
  }, [open, query]);

  const allItems = useMemo(
    () => [...PAGE_ITEMS, ...DEEP_LINK_ITEMS, ...ACTION_ITEMS, ...skillItems, ...threadItems, ...projectItems],
    [skillItems, threadItems, projectItems],
  );

  /** id → row, so the pure scorer can hand back plain items and we can still
   *  recover the icon and the click handler. */
  const byId = useMemo(() => {
    const m = new Map<string, PaletteRow>();
    for (const item of allItems) m.set(item.id, item);
    return m;
  }, [allItems]);

  const q = query.trim();

  /** The flat, selectable list plus the headers that label its segments.
   *  Computed TOGETHER because a header is addressed by row index: worked out
   *  in a second memo, the index would be a copy of this one's arithmetic, and
   *  the day the lane order changes only one of the two would follow.
   *  Headers are decoration; the keyboard model never has to know about them. */
  const { rows, headers } = useMemo<{
    rows: PaletteRow[];
    headers: Record<number, string>;
  }>(() => {
    if (!q) {
      // EMPTY QUERY = the "what can I even do here" screen. The five actions
      // teach the verbs; the recent chats are the single most likely thing a
      // returning user came back for. No conversation lane here: there is no
      // query to search for, and a lane that appears with nothing in it is a
      // worse answer than no lane.
      const h: Record<number, string> = { 0: "Do something" };
      if (threadItems.length) h[ACTION_ITEMS.length] = "Recent chats";
      return { rows: [...ACTION_ITEMS, ...threadItems.slice(0, EMPTY_THREADS)], headers: h };
    }
    const scored = scorePalette(q, allItems, RESULT_LIMIT);
    const matched = scored
      .map((s) => byId.get(s.id))
      .filter((r): r is PaletteRow => Boolean(r));
    // The conversation lane sits BELOW everything the client could match by
    // name and ABOVE the ask row. Below, because a page or a thread TITLE the
    // user named outright is a more certain answer than a phrase buried in a
    // months-old message. Above, because the ask row is the end of the road.
    // A conversation already offered above by title is not offered twice.
    const above = new Set(matched.map((r) => r.href).filter(Boolean));
    const lane = historyItems.filter((r) => !above.has(r.href));
    const h: Record<number, string> = {};
    if (lane.length) h[matched.length] = HISTORY_HEADER;
    // THE ROW THAT MAKES SEARCH NEVER A DEAD END. Appended for every non-empty
    // query, matches or not: "no results" is where a search tells you to go
    // away, and the one thing this app can always do with a sentence is answer
    // it. Always last so it never steals the top slot from a real destination.
    return { rows: [...matched, ...lane, askRow(q)], headers: h };
  }, [q, allItems, byId, threadItems, historyItems]);

  // Clamp rather than trust: live results can arrive after the user has already
  // arrowed down, and an out-of-range index would silently Enter into nothing.
  // Belt to the braces of resetting `active` alongside every setQuery — this is
  // the guard for a shrink that no keystroke caused.
  const activeIdx = rows.length ? Math.min(active, rows.length - 1) : 0;

  function run(row: PaletteRow | undefined) {
    // rows is never empty by construction (see the `rows` memo), but Enter is
    // wired straight to an index — so the one thing that must never happen is
    // an exception out of a keystroke.
    if (!row) return;
    setOpen(false);
    // Clear HERE, not only on the next open. The open effect also resets, but
    // it runs a commit later: for one frame the reopened palette would render
    // last time's query, last time's result list, and the old highlight before
    // snapping to empty. Resetting at the moment we act means the palette is
    // already clean while it is invisible.
    setQuery("");
    setActive(0);
    if (row.run) {
      row.run();
      return;
    }
    // Search is how a lot of navigation actually happens here, so it counts
    // toward "most used" too — counting only the rail would rank the grid by
    // half the story (v1.151.0).
    if (row.href) {
      recordOpen(row.href);
      router.push(row.href);
    }
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 px-4 pt-[14vh] backdrop-blur-sm"
          onClick={() => setOpen(false)}
        >
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="Search Iron Jarvis"
            initial={{ opacity: 0, y: -10, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -10, scale: 0.98 }}
            transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
            className="w-full max-w-xl overflow-hidden rounded-2xl border border-white/10 bg-ink-850/95 shadow-card-hover backdrop-blur-xl"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              // Trap Tab within the dialog (the input is the only tab-stop; results
              // are arrow-key navigated) so focus can't slip behind the overlay.
              if (e.key === "Tab") {
                e.preventDefault();
                inputRef.current?.focus();
              }
            }}
          >
            <div className="flex items-center gap-3 border-b hairline px-4 py-3">
              <Search size={16} aria-hidden="true" className="text-accent-soft/80" />
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => {
                  // Reset the highlight IN THE SAME EVENT as the query, not in
                  // an effect keyed on it. A `useEffect(() => setActive(0),
                  // [query])` lands one commit LATE, so every keystroke that
                  // shortened the list first painted a frame whose
                  // aria-activedescendant named a row from the OLD list — a
                  // screen reader can be told about a row that is already gone.
                  // Batched together, no such frame exists.
                  setQuery(e.target.value);
                  setActive(0);
                }}
                role="combobox"
                aria-expanded
                aria-controls="ij-palette-list"
                aria-autocomplete="list"
                aria-label="Search pages, skills, chats and projects"
                aria-activedescendant={rows.length ? `ij-palette-row-${activeIdx}` : undefined}
                onKeyDown={(e) => {
                  // FLAT keyboard model: one index across every kind of row,
                  // including the ask row. Section headers are skipped by
                  // construction (they aren't rows), so Down never lands on a
                  // thing that can't be chosen.
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    setActive(Math.min(activeIdx + 1, rows.length - 1));
                  } else if (e.key === "ArrowUp") {
                    e.preventDefault();
                    setActive(Math.max(activeIdx - 1, 0));
                  } else if (e.key === "Enter") {
                    e.preventDefault();
                    run(rows[activeIdx]);
                  }
                }}
                placeholder="Search anything — pages, skills, chats, or just ask…"
                className="flex-1 bg-transparent text-sm text-zinc-100 outline-none placeholder:text-zinc-600"
              />
              <kbd className="rounded border border-white/10 bg-white/[0.04] px-1.5 py-0.5 text-[10px] font-medium text-zinc-500">
                esc
              </kbd>
            </div>
            <div
              id="ij-palette-list"
              role="listbox"
              aria-label="Results"
              className="max-h-80 overflow-y-auto p-2"
            >
              {rows.map((row, i) => {
                const Icon = row.icon;
                const on = i === activeIdx;
                const isAsk = row.id === ASK_ID;
                return (
                  // role="presentation" keeps the wrapper out of the a11y tree
                  // so the listbox still sees options as its direct children.
                  <div key={row.id} role="presentation">
                    {headers[i] && (
                      <div className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-600">
                        {headers[i]}
                      </div>
                    )}
                    <button
                      id={`ij-palette-row-${i}`}
                      role="option"
                      aria-selected={on}
                      type="button"
                      onMouseEnter={() => setActive(i)}
                      onClick={() => run(row)}
                      className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm transition-colors ${
                        // The ask row is visually a footer, not a result: a
                        // hairline above it says "past here, we stop matching
                        // and start answering".
                        isAsk ? "mt-1 border-t border-white/10 pt-3" : ""
                      } ${
                        on ? "bg-accent/[0.1] text-accent-soft" : "text-zinc-300 hover:bg-white/[0.04]"
                      }`}
                    >
                      <Icon size={16} aria-hidden="true" className={on ? "text-accent" : "text-zinc-500"} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">{row.label}</span>
                        {row.parts?.length ? (
                          // The matching line, as TEXT NODES. The markers the
                          // index puts around matched terms became structure at
                          // parse time (splitSnippet) precisely so that months-old
                          // user and model text is never handed to an HTML sink.
                          <span className="block truncate text-[11px] text-zinc-500">
                            {row.parts.map((part, p) =>
                              part.hit ? (
                                <mark
                                  key={p}
                                  className="rounded bg-accent/20 px-0.5 text-accent-soft"
                                >
                                  {part.text}
                                </mark>
                              ) : (
                                <span key={p}>{part.text}</span>
                              ),
                            )}
                          </span>
                        ) : row.blurb ? (
                          <span className="block truncate text-[11px] text-zinc-500">{row.blurb}</span>
                        ) : null}
                      </span>
                      {row.when && (
                        <span className="shrink-0 text-[10px] text-zinc-600">{row.when}</span>
                      )}
                      {!isAsk && (
                        <span className="shrink-0 text-[10px] uppercase tracking-wide text-zinc-600">
                          {row.badge ?? KIND_LABEL[row.kind]}
                        </span>
                      )}
                      {on && <CornerDownLeft size={13} aria-hidden="true" className="text-accent-soft/70" />}
                    </button>
                  </div>
                );
              })}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/** Stable id for the ask row so the renderer can spot it without string-matching
 *  the user's own query back out of the label. */
const ASK_ID = "ask:iron-jarvis";

/** The always-last fallback row. Kind "action" because that is what it is —
 *  pressing Enter starts a conversation, it doesn't navigate to a listing. */
function askRow(query: string): PaletteRow {
  return {
    id: ASK_ID,
    kind: "action",
    label: `Ask Iron Jarvis: “${query}”`,
    blurb: "Open a chat with this as your first message.",
    href: `/chat?ask=${encodeURIComponent(query)}`,
    icon: Bot,
  };
}

/** "Jul 27" / "Jul 27, 2025" — enough to recognise a conversation, short enough
 *  to sit on one line. An unparseable date degrades to nothing rather than to
 *  the string "Invalid Date".
 *
 *  normalizeIso for the same reason historyWhen needs it: SQLite hands the
 *  daemon back NAIVE datetimes, so `updated_at` arrives without a zone and a
 *  browser reads it as local time — hours out, and near midnight a whole day
 *  out. That was already wrong on its own; with the conversation lane below
 *  dating the same thread correctly, one box would print two different days
 *  for one conversation. */
function threadWhen(iso: string | undefined): string | undefined {
  if (!iso) return undefined;
  const d = new Date(normalizeIso(iso));
  if (Number.isNaN(d.getTime())) return undefined;
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}
