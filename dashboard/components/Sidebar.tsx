"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import {
  LayoutDashboard,
  MessageSquare,
  Boxes,
  Images,
  Sparkles,
  BrainCircuit,
  Package,
  Workflow,
  Bot,
  Wrench,
  CalendarClock,
  FileSearch,
  FileText,
  FolderKanban,
  KeyRound,
  Plug,
  PlugZap,
  Megaphone,
  Webhook,
  MonitorCog,
  Radar,
  SquareTerminal,
  MoveUpRight,
  GitBranch,
  Gauge,
  DownloadCloud,
  Settings,
  LifeBuoy,
  BarChart3,
  LayoutTemplate,
  SlidersHorizontal,
  Menu,
  X,
  type LucideIcon,
} from "lucide-react";
import { API_BASE } from "@/lib/api";
import { useDaemon } from "@/lib/daemon";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

interface NavSection {
  label: string;
  items: NavItem[];
}

// FOUR HERO SURFACES lead the nav: Chat (talk), Build (terminals — make
// things), Projects (the context spine), Sessions (review the work). Every
// other page is support cast, grouped behind them and mostly Advanced-only.
const NAV: NavSection[] = [
  {
    label: "Work",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboard },
      { href: "/chat", label: "Chat", icon: MessageSquare },
      { href: "/terminals", label: "Build", icon: SquareTerminal },
      { href: "/projects", label: "Projects", icon: FolderKanban },
      { href: "/sessions", label: "Sessions", icon: Boxes },
      { href: "/creative", label: "Creative", icon: Images },
    ],
  },
  {
    label: "Automate",
    items: [
      { href: "/workflows", label: "Workflows", icon: Workflow },
      { href: "/schedules", label: "Schedules", icon: CalendarClock },
      // Kanban lives INSIDE a project now (Projects → open a project → Board).
      { href: "/templates", label: "Templates", icon: LayoutTemplate },
      { href: "/agents", label: "Agents", icon: Bot },
      { href: "/tools", label: "Tools", icon: Wrench },
      { href: "/autonomy", label: "Autonomy", icon: Gauge },
      { href: "/sentinels", label: "Sentinels", icon: Radar },
      { href: "/computeruse", label: "Computer Control", icon: MonitorCog },
      { href: "/webhooks", label: "Webhooks", icon: Webhook },
      { href: "/self-dev", label: "Self-improvement", icon: GitBranch },
    ],
  },
  {
    label: "Knowledge",
    items: [
      // ONE memory surface (working / lessons / long-term live inside as scopes).
      { href: "/memory", label: "Memory", icon: BrainCircuit },
      { href: "/documents", label: "Documents", icon: FileText },
      { href: "/filesearch", label: "File Search", icon: FileSearch },
      { href: "/skills", label: "Skills", icon: Sparkles },
      { href: "/artifacts", label: "Artifacts", icon: Package },
    ],
  },
  {
    label: "Connections",
    items: [
      { href: "/connections", label: "Connections", icon: PlugZap },
      { href: "/secrets", label: "Secrets", icon: KeyRound },
      { href: "/integrations", label: "Integrations", icon: Plug },
      { href: "/channels", label: "Channels", icon: Megaphone },
    ],
  },
  {
    label: "System",
    items: [
      { href: "/usage", label: "Usage", icon: BarChart3 },
      { href: "/updates", label: "Updates", icon: DownloadCloud },
      { href: "/settings", label: "Settings", icon: Settings },
      { href: "/help", label: "Help", icon: LifeBuoy },
    ],
  },
];

/**
 * The essentials shown in Simple mode (the default): the four heroes plus the
 * bare minimum to connect a model, remember things, and get help. Everything
 * else in NAV is revealed only when the "Advanced" toggle is on. Keyed by href
 * so labels can be de-jargoned freely without breaking the filter.
 */
const ESSENTIAL_HREFS = new Set<string>([
  "/", // Overview
  "/chat", // Chat (hero)
  "/terminals", // Build (hero)
  "/projects", // Projects (hero — the context spine)
  "/sessions", // Sessions (hero)
  "/creative", // Creative — see what Iron Jarvis makes
  "/memory", // Memory (the one unified surface)
  "/connections", // Connections
  "/settings", // Settings
  "/help", // Help
]);

/**
 * Persisted Simple/Advanced nav mode. Seeded to Simple (false) for a stable SSR
 * render, then hydrated from localStorage in an effect to avoid a mismatch.
 * Persists on change. Each rail (desktop / mobile) owns its own copy; only one
 * is ever visible at a given breakpoint, so they don't need live cross-sync.
 */
function useNavMode(): [boolean, () => void] {
  const [advanced, setAdvanced] = useState(false);

  useEffect(() => {
    try {
      setAdvanced(localStorage.getItem("ij_nav_advanced") === "1");
    } catch {
      /* localStorage unavailable — stay in Simple mode. */
    }
  }, []);

  const toggle = () =>
    setAdvanced((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("ij_nav_advanced", next ? "1" : "0");
      } catch {
        /* ignore persistence failures */
      }
      return next;
    });

  return [advanced, toggle];
}

/** The arc-reactor brand mark. */
function ArcMark() {
  return (
    <span className="relative grid h-9 w-9 place-items-center">
      <span className="absolute inset-0 rounded-xl bg-accent/15 blur-[6px]" />
      <svg
        viewBox="0 0 24 24"
        className="relative h-9 w-9 drop-shadow-[0_0_6px_rgba(34,211,238,0.55)]"
        fill="none"
        stroke="currentColor"
      >
        <circle cx="12" cy="12" r="9.2" className="stroke-accent/30" strokeWidth="1.2" />
        <g className="stroke-accent">
          {Array.from({ length: 8 }).map((_, i) => {
            const a = (i * Math.PI) / 4;
            const x1 = 12 + Math.cos(a) * 4.4;
            const y1 = 12 + Math.sin(a) * 4.4;
            const x2 = 12 + Math.cos(a) * 7.6;
            const y2 = 12 + Math.sin(a) * 7.6;
            return (
              <line
                key={i}
                x1={x1}
                y1={y1}
                x2={x2}
                y2={y2}
                strokeWidth="1.1"
                strokeLinecap="round"
                opacity={0.7}
              />
            );
          })}
        </g>
        <circle cx="12" cy="12" r="3.4" className="fill-accent/20 stroke-accent" strokeWidth="1.3" />
        <circle cx="12" cy="12" r="1.2" className="fill-accent-soft" stroke="none" />
      </svg>
    </span>
  );
}

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-3 text-accent">
      <ArcMark />
      <div>
        <div className="text-[15px] font-semibold tracking-tight text-zinc-50">Iron Jarvis</div>
        <div className="text-[11px] tracking-wide text-zinc-500">control center</div>
      </div>
    </Link>
  );
}

/**
 * The shared nav list, used by both the desktop rail and the mobile drawer.
 * In Simple mode (`advanced === false`) only essential items render, and any
 * section left with no visible items is dropped entirely.
 */
function NavLinks({
  layoutId,
  advanced,
  onNavigate,
}: {
  layoutId: string;
  advanced: boolean;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);
  return (
    <>
      {NAV.map((section) => {
        const items = advanced
          ? section.items
          : section.items.filter((item) => ESSENTIAL_HREFS.has(item.href));
        if (items.length === 0) return null;
        return (
        <div key={section.label} className="space-y-1 pb-2">
          <div className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-600">
            {section.label}
          </div>
          {items.map((item) => {
            const active = isActive(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={onNavigate}
                className={`group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors ${
                  active
                    ? "text-accent-soft"
                    : "text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-100"
                }`}
              >
                {active && (
                  <motion.span
                    layoutId={layoutId}
                    className="absolute inset-0 rounded-xl border border-accent/25 bg-accent/[0.08] shadow-[inset_0_0_0_1px_rgba(34,211,238,0.06)]"
                    transition={{ type: "spring", stiffness: 380, damping: 32 }}
                  />
                )}
                <span
                  className={`relative z-10 transition-colors ${
                    active ? "text-accent" : "text-zinc-500 group-hover:text-zinc-300"
                  }`}
                >
                  <Icon size={17} strokeWidth={2} />
                </span>
                <span className="relative z-10 font-medium">{item.label}</span>
              </Link>
            );
          })}
        </div>
        );
      })}
    </>
  );
}

/**
 * The Simple/Advanced switch. Sits at the bottom of the nav (above the footer)
 * in both rails. Subtle, arc-reactor-cyan when active, with a "showing
 * essentials" hint while in Simple mode.
 */
function NavModeToggle({
  advanced,
  onToggle,
}: {
  advanced: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="border-t border-white/[0.06] px-3 py-2">
      <button
        type="button"
        onClick={onToggle}
        aria-pressed={advanced}
        title={advanced ? "Show only the essentials" : "Show every tool"}
        className={`group flex w-full items-center gap-3 rounded-xl px-3 py-2 text-sm transition-colors ${
          advanced
            ? "text-accent-soft"
            : "text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-100"
        }`}
      >
        <span
          className={`transition-colors ${
            advanced ? "text-accent" : "text-zinc-500 group-hover:text-zinc-300"
          }`}
        >
          <SlidersHorizontal size={17} strokeWidth={2} />
        </span>
        <span className="flex flex-col items-start leading-tight">
          <span className="font-medium">Advanced</span>
          {!advanced && (
            <span className="text-[10px] font-normal text-zinc-600">
              showing essentials
            </span>
          )}
        </span>
        <span
          className={`ml-auto flex h-4 w-7 items-center rounded-full border px-0.5 transition-colors ${
            advanced
              ? "justify-end border-accent/40 bg-accent/20"
              : "justify-start border-white/10 bg-white/[0.03]"
          }`}
        >
          <span
            className={`h-2.5 w-2.5 rounded-full transition-colors ${
              advanced
                ? "bg-accent shadow-[0_0_6px_rgba(34,211,238,0.6)]"
                : "bg-zinc-600"
            }`}
          />
        </span>
      </button>
    </div>
  );
}

/** Shared daemon-status footer (version + connection dot + API host + deploy). */
function SidebarFooter() {
  const { online: connected, health } = useDaemon();
  const version = health?.version;
  return (
    <div className="space-y-2 border-t border-white/[0.06] px-5 py-4">
      {/* Version — the single source of truth (live from the daemon's /health,
          so it reflects the ACTUAL running build, not a baked constant). Links
          to Updates so it doubles as the "am I current?" affordance. */}
      <Link
        href="/updates"
        title="View updates"
        className="group inline-flex items-center gap-1.5 text-[11px] transition-colors"
      >
        <Package size={11} className="text-zinc-600 group-hover:text-accent-soft" />
        <span className="font-mono text-zinc-500 group-hover:text-accent-soft">
          {version ? `v${version}` : "—"}
        </span>
      </Link>
      <div className="flex items-center gap-2 text-[11px]">
        <span
          className={`h-2 w-2 rounded-full ${
            connected
              ? "bg-emerald-400 shadow-[0_0_8px_2px_rgba(52,211,153,0.5)] animate-pulse-glow"
              : "bg-zinc-600"
          }`}
        />
        <span className={connected ? "text-emerald-300/90" : "text-zinc-500"}>
          {connected ? "daemon connected" : "daemon offline"}
        </span>
      </div>
      <div className="truncate font-mono text-[11px] text-zinc-600" title={API_BASE}>
        {API_BASE.replace(/^https?:\/\//, "")}
      </div>
      <div className="flex items-center gap-1.5 pt-0.5 text-[11px] text-zinc-600">
        <kbd className="rounded border border-white/10 bg-white/[0.03] px-1.5 py-0.5 font-sans text-[10px] text-zinc-500">
          ⌘K
        </kbd>
        <span>commands</span>
      </div>
      <a
        href="https://github.com/RealDealCPA-VR/Iron-Jarvis/blob/master/DEPLOY.md"
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1 pt-0.5 text-[11px] text-zinc-600 transition-colors hover:text-accent-soft"
      >
        Deploy to a server <MoveUpRight size={11} />
      </a>
    </div>
  );
}

/** The desktop sidebar rail. Hidden below the `md` breakpoint (see MobileNav). */
export function Sidebar() {
  const [advanced, toggleAdvanced] = useNavMode();
  return (
    <aside className="hidden w-64 shrink-0 flex-col border-r border-white/[0.06] bg-ink-900/70 backdrop-blur-xl md:flex">
      <div className="px-5 py-5">
        <Brand />
      </div>
      <div className="mx-5 h-px bg-accent-line opacity-60" />
      <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-4">
        <NavLinks layoutId="nav-active" advanced={advanced} />
      </nav>
      <NavModeToggle advanced={advanced} onToggle={toggleAdvanced} />
      <SidebarFooter />
    </aside>
  );
}

/**
 * Mobile navigation: a hamburger button (shown only below `md`) that opens a
 * slide-over drawer with the same nav. Lives in the top bar so the desktop rail
 * is untouched.
 */
export function MobileNav() {
  const [open, setOpen] = useState(false);
  const [advanced, toggleAdvanced] = useNavMode();
  const pathname = usePathname();

  // Close the drawer whenever the route changes.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Lock body scroll + close on Escape while the drawer is open.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="flex items-center gap-2 md:hidden">
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Open navigation"
        className="grid h-9 w-9 place-items-center rounded-xl border border-white/10 bg-white/[0.02] text-zinc-300 transition-colors hover:border-white/20 hover:text-zinc-100"
      >
        <Menu size={18} />
      </button>
      <Link href="/" className="flex items-center gap-2 text-accent">
        <span className="text-[14px] font-semibold tracking-tight text-zinc-50">Iron Jarvis</span>
      </Link>

      <AnimatePresence>
        {open && (
          <>
            <motion.div
              key="backdrop"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              onClick={() => setOpen(false)}
              className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
            />
            <motion.aside
              key="drawer"
              role="dialog"
              aria-modal="true"
              aria-label="Navigation"
              initial={{ x: "-100%" }}
              animate={{ x: 0 }}
              exit={{ x: "-100%" }}
              transition={{ type: "spring", stiffness: 360, damping: 38 }}
              className="fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] flex-col border-r border-white/[0.06] bg-ink-900/95 backdrop-blur-xl"
            >
              <div className="flex items-center justify-between px-5 py-5">
                <Brand />
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  aria-label="Close navigation"
                  className="grid h-8 w-8 place-items-center rounded-lg border border-white/10 text-zinc-400 transition-colors hover:text-zinc-100"
                >
                  <X size={16} />
                </button>
              </div>
              <div className="mx-5 h-px bg-accent-line opacity-60" />
              <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-4">
                <NavLinks
                  layoutId="nav-active-mobile"
                  advanced={advanced}
                  onNavigate={() => setOpen(false)}
                />
              </nav>
              <NavModeToggle advanced={advanced} onToggle={toggleAdvanced} />
              <SidebarFooter />
            </motion.aside>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}
