"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import {
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
  type LucideIcon,
} from "lucide-react";
import { NAV_ENTRIES } from "@/lib/nav";
import { scorePalette, type PaletteItem } from "@/lib/palette";
import { get } from "@/lib/api";

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
 */

/** A palette item plus the two things the pure scorer has no business knowing:
 *  what it looks like, and what pressing Enter does when it isn't a link. */
interface PaletteRow extends PaletteItem {
  icon: LucideIcon;
  /** When set, run this instead of navigating to href (e.g. open the switcher). */
  run?: () => void;
}

/** Row badge text — small, so a result never leaves you guessing what it IS. */
const KIND_LABEL: Record<PaletteItem["kind"], string> = {
  page: "Page",
  action: "Action",
  skill: "Skill",
  project: "Project",
  thread: "Thread",
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
    label: "Tools → Plug-ins",
    blurb: "Connect an MCP pack and set what it may run without asking.",
    aliases: ["auto-approve", "mcp", "connect a pack"],
    href: "/tools?focus=packs",
    icon: Package,
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

  /** The flat, selectable list. Headers below are decoration; the keyboard
   *  model never has to know about them. */
  const rows = useMemo<PaletteRow[]>(() => {
    if (!q) {
      // EMPTY QUERY = the "what can I even do here" screen. The five actions
      // teach the verbs; the recent chats are the single most likely thing a
      // returning user came back for.
      return [...ACTION_ITEMS, ...threadItems.slice(0, EMPTY_THREADS)];
    }
    const scored = scorePalette(q, allItems, RESULT_LIMIT);
    const matched = scored
      .map((s) => byId.get(s.id))
      .filter((r): r is PaletteRow => Boolean(r));
    // THE ROW THAT MAKES SEARCH NEVER A DEAD END. Appended for every non-empty
    // query, matches or not: "no results" is where a search tells you to go
    // away, and the one thing this app can always do with a sentence is answer
    // it. Always last so it never steals the top slot from a real destination.
    return [...matched, askRow(q)];
  }, [q, allItems, byId, threadItems]);

  /** Index → section header rendered ABOVE that row (empty screen only). */
  const headers = useMemo<Record<number, string>>(() => {
    if (q) return {};
    const h: Record<number, string> = { 0: "Do something" };
    if (threadItems.length) h[ACTION_ITEMS.length] = "Recent chats";
    return h;
  }, [q, threadItems.length]);

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
    if (row.href) router.push(row.href);
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
                        {row.blurb && (
                          <span className="block truncate text-[11px] text-zinc-500">{row.blurb}</span>
                        )}
                      </span>
                      {!isAsk && (
                        <span className="shrink-0 text-[10px] uppercase tracking-wide text-zinc-600">
                          {KIND_LABEL[row.kind]}
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
 *  the string "Invalid Date". */
function threadWhen(iso: string | undefined): string | undefined {
  if (!iso) return undefined;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return undefined;
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}
