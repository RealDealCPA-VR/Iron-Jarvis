"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import {
  Search,
  PlugZap,
  Cpu,
  Wrench,
  Gauge,
  Plus,
  CornerDownLeft,
  Images,
  LayoutDashboard,
  MessageSquare,
  Boxes,
  Bot,
  Sparkles,
  KeyRound,
  CalendarClock,
  BrainCircuit,
  SquareKanban,
  LayoutTemplate,
  BarChart3,
  GitBranch,
  Workflow,
  MonitorCog,
  SquareTerminal,
  Webhook,
  Radar,
  GraduationCap,
  Database,
  FileSearch,
  FileText,
  Package,
  Plug,
  Megaphone,
  DownloadCloud,
  Settings,
  LifeBuoy,
  type LucideIcon,
} from "lucide-react";

interface Command {
  id: string;
  label: string;
  hint?: string;
  icon: LucideIcon;
  href: string;
  keywords?: string;
  /** When set, run this instead of navigating to href (e.g. open the switcher). */
  action?: () => void;
}

// Quick actions (do something) listed first, then a full "jump to" index of
// every route in the app so ⌘K can reach anywhere.
const COMMANDS: Command[] = [
  // ── Actions ──────────────────────────────────────────────────────────────
  { id: "new-session", label: "New session", hint: "Action", icon: Plus, href: "/sessions?new=1", keywords: "run task agent start launch create" },
  { id: "connect", label: "Connect a model", hint: "Action", icon: PlugZap, href: "/connections", keywords: "llm api key oauth anthropic openai google grok xai provider account login" },
  { id: "switch-model", label: "Switch model", hint: "Action", icon: Cpu, href: "#", keywords: "provider model default active grok claude gpt gemini change router", action: () => window.dispatchEvent(new Event("ij:open-switcher")) },
  { id: "open-usage", label: "View usage & cost", hint: "Action", icon: BarChart3, href: "/usage", keywords: "tokens cost spend report analytics billing" },
  { id: "open-templates", label: "Open task templates", hint: "Action", icon: LayoutTemplate, href: "/templates", keywords: "preset saved reusable task template" },
  // ── Jump to ──────────────────────────────────────────────────────────────
  { id: "overview", label: "Overview", hint: "Work", icon: LayoutDashboard, href: "/", keywords: "home dashboard health metrics" },
  { id: "chat", label: "Chat", hint: "Work", icon: MessageSquare, href: "/chat", keywords: "talk ask agent assistant conversation message tool" },
  { id: "sessions", label: "Sessions", hint: "Work", icon: Boxes, href: "/sessions", keywords: "runs history" },
  { id: "creative", label: "Creative", hint: "Work", icon: Images, href: "/creative", keywords: "gallery media image video audio pixio generate studio" },
  { id: "kanban", label: "Project board (Kanban)", hint: "Work", icon: SquareKanban, href: "/projects", keywords: "board tasks status kanban project" },
  { id: "templates", label: "Templates", hint: "Work", icon: LayoutTemplate, href: "/templates", keywords: "preset saved reusable" },
  { id: "agents", label: "Agents", hint: "Work", icon: Bot, href: "/agents", keywords: "builder supervisor planner researcher reviewer" },
  { id: "tools", label: "Tools", hint: "Work", icon: Wrench, href: "/tools", keywords: "custom tool create reusable command" },
  { id: "autonomy", label: "Autonomy", hint: "Automation", icon: Gauge, href: "/autonomy", keywords: "goals proposals pulse motivation autopilot pending approve sentinels" },
  { id: "self-dev", label: "Self-development", hint: "Work", icon: GitBranch, href: "/self-dev", keywords: "self dev git code improve" },
  { id: "workflows", label: "Workflows", hint: "Automation", icon: Workflow, href: "/workflows", keywords: "pipeline chain steps" },
  { id: "schedules", label: "Schedules", hint: "Automation", icon: CalendarClock, href: "/schedules", keywords: "cron timer recurring" },
  { id: "computeruse", label: "Computer Use", hint: "Automation", icon: MonitorCog, href: "/computeruse", keywords: "desktop screen control approval" },
  { id: "terminals", label: "Build (Terminals)", hint: "Work", icon: SquareTerminal, href: "/terminals", keywords: "build building shell console command terminal" },
  { id: "webhooks", label: "Webhooks", hint: "Automation", icon: Webhook, href: "/webhooks", keywords: "trigger http callback" },
  { id: "sentinels", label: "Sentinels", hint: "Automation", icon: Radar, href: "/sentinels", keywords: "watchers monitor file watch suggest" },
  { id: "skills", label: "Skills", hint: "Knowledge", icon: Sparkles, href: "/skills", keywords: "abilities tools" },
  { id: "memory", label: "Memory", hint: "Knowledge", icon: BrainCircuit, href: "/memory", keywords: "context recall working lessons long-term knowledge" },
  { id: "lessons", label: "Memory → What I've learned", hint: "Knowledge", icon: GraduationCap, href: "/memory?scope=lessons", keywords: "lessons learned insights distill" },
  { id: "ltm", label: "Memory → Long-term", hint: "Knowledge", icon: Database, href: "/memory?scope=longterm", keywords: "obsidian notion store vault brain" },
  { id: "filesearch", label: "File Search", hint: "Knowledge", icon: FileSearch, href: "/filesearch", keywords: "find files grep index" },
  { id: "documents", label: "Documents", hint: "Knowledge", icon: FileText, href: "/documents", keywords: "files upload pdf" },
  { id: "artifacts", label: "Artifacts", hint: "Knowledge", icon: Package, href: "/artifacts", keywords: "output results files" },
  { id: "connections", label: "Connections", hint: "Connections", icon: PlugZap, href: "/connections", keywords: "models providers api key" },
  { id: "secrets", label: "Secrets", hint: "Connections", icon: KeyRound, href: "/secrets", keywords: "vault credentials env" },
  { id: "integrations", label: "Integrations", hint: "Connections", icon: Plug, href: "/integrations", keywords: "services connect" },
  { id: "channels", label: "Channels", hint: "Connections", icon: Megaphone, href: "/channels", keywords: "slack telegram discord notify" },
  { id: "usage", label: "Usage", hint: "System", icon: BarChart3, href: "/usage", keywords: "tokens cost spend analytics" },
  { id: "updates", label: "Updates", hint: "System", icon: DownloadCloud, href: "/updates", keywords: "version upgrade release" },
  { id: "settings", label: "Settings", hint: "System", icon: Settings, href: "/settings", keywords: "config preferences" },
  { id: "help", label: "Help", hint: "System", icon: LifeBuoy, href: "/help", keywords: "docs support guide" },
];

export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const prevFocusRef = useRef<HTMLElement | null>(null);

  // ⌘K / Ctrl+K toggles; Esc closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (open) {
      // Remember what had focus so we can restore it on close (don't dump focus to
      // <body> — a keyboard/screen-reader user would lose their place).
      prevFocusRef.current = document.activeElement as HTMLElement | null;
      setQuery("");
      setActive(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    } else {
      prevFocusRef.current?.focus?.();
    }
  }, [open]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return COMMANDS;
    return COMMANDS.filter((c) =>
      `${c.label} ${c.hint ?? ""} ${c.keywords ?? ""}`.toLowerCase().includes(q),
    );
  }, [query]);

  useEffect(() => {
    setActive(0);
  }, [query]);

  function run(cmd: Command | undefined) {
    if (!cmd) return;
    setOpen(false);
    if (cmd.action) {
      cmd.action();
      return;
    }
    router.push(cmd.href);
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
            aria-label="Command palette"
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
              <Search size={16} className="text-accent-soft/80" />
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    setActive((a) => Math.min(a + 1, results.length - 1));
                  } else if (e.key === "ArrowUp") {
                    e.preventDefault();
                    setActive((a) => Math.max(a - 1, 0));
                  } else if (e.key === "Enter") {
                    e.preventDefault();
                    run(results[active]);
                  }
                }}
                placeholder="Search or jump to…"
                className="flex-1 bg-transparent text-sm text-zinc-100 outline-none placeholder:text-zinc-600"
              />
              <kbd className="rounded border border-white/10 bg-white/[0.04] px-1.5 py-0.5 text-[10px] font-medium text-zinc-500">
                esc
              </kbd>
            </div>
            <div className="max-h-80 overflow-y-auto p-2">
              {results.length === 0 ? (
                <div className="px-3 py-6 text-center text-sm text-zinc-500">
                  No matches.
                </div>
              ) : (
                results.map((c, i) => {
                  const Icon = c.icon;
                  const on = i === active;
                  return (
                    <button
                      key={c.id}
                      onMouseEnter={() => setActive(i)}
                      onClick={() => run(c)}
                      className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm transition-colors ${
                        on ? "bg-accent/[0.1] text-accent-soft" : "text-zinc-300 hover:bg-white/[0.04]"
                      }`}
                    >
                      <Icon size={16} className={on ? "text-accent" : "text-zinc-500"} />
                      <span className="flex-1 font-medium">{c.label}</span>
                      {c.hint && <span className="text-[11px] text-zinc-500">{c.hint}</span>}
                      {on && <CornerDownLeft size={13} className="text-accent-soft/70" />}
                    </button>
                  );
                })
              )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
