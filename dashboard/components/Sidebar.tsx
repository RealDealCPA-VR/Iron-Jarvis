"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { AnimatePresence, m } from "framer-motion"; // v1.250.0 (S-08)
import {
  Package,
  MoveUpRight,
  Settings,
  SlidersHorizontal,
  X,
  AppWindow,
} from "lucide-react";
import { API_BASE } from "@/lib/api";
import { recordOpen } from "@/lib/appTiles";
import { popoutBridge, type PopoutBridge } from "@/lib/desktopShell";
import { useDaemon } from "@/lib/daemon";
import { NAV, type NavEntry as NavItem, type NavSectionDef as NavSection } from "@/lib/nav";
import { HUBS, hubFor, tabLabel, visibleTabs } from "@/lib/hubs";
import { useAdvancedMode } from "@/lib/uiMode";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";

// NAV lives in lib/nav.ts now (v1.111.0). The global search needs the same
// catalogue (plus aliases + blurbs), and two copies WOULD drift — the
// palette carried a stale private page list for months and nobody noticed.
// This file renders the drawer; lib/nav.ts owns what is in it.

/**
 * The essentials shown in Simple mode (the default): the four heroes plus the
 * bare minimum to connect a model, remember things, and get help. Everything
 * else in NAV is revealed only when the "Advanced" toggle is on. Keyed by href
 * so labels can be de-jargoned freely without breaking the filter.
 */
// v1.318.0: Simple mode lists the seven places in lib/hubs.ts (Home, Work,
// Files, Automations, About me, Apps & settings, Help) instead of a filtered
// copy of this menu; the old essentials list is gone with it. Advanced still
// renders NAV in full.

/**
 * The Simple/Advanced switch, through the ONE store every reader shares
 * (lib/uiMode.ts, v1.318.0) — the Overview and the page headers follow a
 * flip here at once instead of on the next reload.
 */
function useNavMode(): [boolean, () => void] {
  const [advanced, setAdvanced] = useAdvancedMode();
  return [advanced, () => setAdvanced(!advanced)];
}

/** The arc-reactor brand mark. `big` = the dominant collapsed-rail reactor. */
function ArcMark({ big = false }: { big?: boolean }) {
  const size = big ? "h-12 w-12" : "h-9 w-9";
  return (
    <span className={`relative grid ${size} place-items-center`}>
      <span className="absolute inset-0 rounded-xl bg-accent/15 blur-[6px]" />
      <svg
        viewBox="0 0 24 24"
        className={`relative ${size} drop-shadow-[0_0_6px_rgb(var(--accent-rgb)/0.55)]`}
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
  collapsed = false,
  onNavigate,
}: {
  layoutId: string;
  advanced: boolean;
  /** Icons-only rail: labels + section headers hidden, tooltips carry names. */
  collapsed?: boolean;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);
  // POP-OUT WINDOWS (v1.283.0): each row carries a hover door to open that
  // module in its own window. Desktop only; never offered inside a pop-out
  // (a window within a window is not the feature); never for the Overview.
  const [popout, setPopout] = useState<PopoutBridge | null>(null);
  useEffect(() => {
    const b = popoutBridge();
    setPopout(b && !b.isPopout ? b : null);
  }, []);
  if (!advanced) {
    return (
      <SimpleNavLinks layoutId={layoutId} collapsed={collapsed} onNavigate={onNavigate} popout={popout} />
    );
  }
  return (
    <>
      {NAV.map((section) => {
        const items = section.items;
        if (items.length === 0) return null;
        return (
        <div key={section.label} className="space-y-1 pb-2">
          {!collapsed && (
            <div className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-600">
              {section.label}
            </div>
          )}
          {items.map((item) => {
            const active = isActive(item.href);
            const Icon = item.icon;
            const door = popout && !collapsed && item.href !== "/";
            return (
              <div key={item.href} className="group/row relative">
              <Link
                href={item.href}
                // v1.151.0: count opens LOCALLY so the Overview's app grid can
                // lead with what this person actually uses. Never leaves the
                // machine — it is a localStorage tally, not telemetry.
                onClick={() => {
                  recordOpen(item.href);
                  onNavigate?.();
                }}
                title={collapsed ? item.label : undefined}
                className={`group relative flex items-center rounded-xl py-2.5 text-sm transition-colors ${
                  collapsed ? "justify-center px-0" : "gap-3 px-3"
                } ${
                  active
                    ? "text-accent-soft"
                    : "text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-100"
                }`}
              >
                {active && (
                  <m.span
                    layoutId={layoutId}
                    className="absolute inset-0 rounded-xl border border-accent/25 bg-accent/[0.08] shadow-[inset_0_0_0_1px_rgb(var(--accent-rgb)/0.06)]"
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
                {!collapsed && (
                  <span className="relative z-10 font-medium">{item.label}</span>
                )}
              </Link>
              {door && (
                <button
                  type="button"
                  data-testid={`popout-row-${item.href.slice(1)}`}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    void popout.open(item.href);
                    onNavigate?.();
                  }}
                  aria-label={`Open ${item.label} in a new window`}
                  title={`Open ${item.label} in a new window`}
                  className="absolute right-2 top-1/2 z-20 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-md text-zinc-500 opacity-0 transition-opacity hover:bg-white/[0.08] hover:text-zinc-100 focus:opacity-100 group-hover/row:opacity-100"
                >
                  <AppWindow size={13} strokeWidth={2} />
                </button>
              )}
              </div>
            );
          })}
        </div>
        );
      })}
    </>
  );
}

/**
 * Simple mode's menu (v1.318.0): the seven places, by the job a person came
 * to do. The place you are in opens to show its pages underneath, so the menu
 * says where you are and what is next to it. Every page is still in Ctrl K,
 * and the Advanced switch below still shows the full list.
 */
function SimpleNavLinks({
  layoutId,
  collapsed,
  onNavigate,
  popout,
}: {
  layoutId: string;
  collapsed: boolean;
  onNavigate?: () => void;
  /** The desktop's pop-out bridge (null in a browser or inside a pop-out). */
  popout: PopoutBridge | null;
}) {
  const pathname = usePathname() ?? "";
  const here = hubFor(pathname);
  // The same "open in its own window" door the full menu's rows carry.
  const door = (href: string, label: string) =>
    popout && !collapsed && href !== "/" ? (
      <button
        type="button"
        data-testid={`popout-row-${href.slice(1)}`}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          void popout.open(href);
          onNavigate?.();
        }}
        aria-label={`Open ${label} in a new window`}
        title={`Open ${label} in a new window`}
        className="absolute right-2 top-1/2 z-20 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-md text-zinc-500 opacity-0 transition-opacity hover:bg-white/[0.08] hover:text-zinc-100 focus:opacity-100 group-hover/row:opacity-100"
      >
        <AppWindow size={13} strokeWidth={2} />
      </button>
    ) : null;
  return (
    <div className="space-y-1 pb-2" data-testid="simple-nav">
      {HUBS.map((hub) => {
        const active = here?.hub.key === hub.key;
        const Icon = hub.icon;
        const subs = active && !collapsed ? visibleTabs(hub, pathname) : [];
        return (
          <div key={hub.key}>
            <div className="group/row relative">
            <Link
              href={hub.href}
              onClick={() => {
                recordOpen(hub.href);
                onNavigate?.();
              }}
              title={collapsed ? hub.label : hub.blurb}
              data-hub={hub.key}
              className={`group relative flex items-center rounded-xl py-2.5 text-sm transition-colors ${
                collapsed ? "justify-center px-0" : "gap-3 px-3"
              } ${active ? "text-accent-soft" : "text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-100"}`}
            >
              {active && (
                <m.span
                  layoutId={layoutId}
                  className="absolute inset-0 rounded-xl border border-accent/25 bg-accent/[0.08] shadow-[inset_0_0_0_1px_rgb(var(--accent-rgb)/0.06)]"
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
              {!collapsed && <span className="relative z-10 font-medium">{hub.label}</span>}
            </Link>
            {door(hub.href, hub.label)}
            </div>
            {subs.length > 1 && (
              <div className="ml-6 mt-1 space-y-0.5 border-l border-white/[0.06] pl-3">
                {subs.map((tab) => {
                  const on = tab.href === here?.tab.href;
                  return (
                    <div key={tab.href} className="group/row relative">
                    <Link
                      href={tab.href}
                      aria-current={on ? "page" : undefined}
                      onClick={() => {
                        recordOpen(tab.href);
                        onNavigate?.();
                      }}
                      className={`block rounded-lg px-2.5 py-1.5 text-[13px] transition-colors ${
                        on ? "text-accent-soft" : "text-zinc-500 hover:bg-white/[0.04] hover:text-zinc-200"
                      }`}
                    >
                      {tabLabel(tab.href)}
                    </Link>
                    {door(tab.href, tabLabel(tab.href))}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
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
                ? "bg-accent shadow-[0_0_6px_rgb(var(--accent-rgb)/0.6)]"
                : "bg-zinc-600"
            }`}
          />
        </span>
      </button>
    </div>
  );
}

/** Shared daemon-status footer (version + connection dot + API host + deploy). */
function SidebarFooter({ advanced = true }: { advanced?: boolean } = {}) {
  const { online: connected, health } = useDaemon();
  const version = health?.version;
  // v1.198.0: the shortcut keycap used to hardcode "⌘K" — a Mac key shown to
  // every user of a Windows-only packaged app. SSR renders the Windows label
  // (stable hydration, and the packaged audience IS Windows); a mount-time
  // check swaps in ⌘K for Macs running the dashboard from source.
  const [cmdLabel, setCmdLabel] = useState("Ctrl+K");
  useEffect(() => {
    if (/Mac|iP(hone|ad|od)/.test(navigator.platform || navigator.userAgent)) {
      setCmdLabel("⌘K");
    }
  }, []);
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
          {advanced
            ? connected
              ? "daemon connected"
              : "daemon offline"
            : // v1.318.0: Simple mode says it in plain words.
              connected
              ? "Running"
              : "Not running — reopen Iron Jarvis"}
        </span>
      </div>
      {advanced && (
        <div className="truncate font-mono text-[11px] text-zinc-600" title={API_BASE}>
          {API_BASE.replace(/^https?:\/\//, "")}
        </div>
      )}
      <div className="flex items-center gap-1.5 pt-0.5 text-[11px] text-zinc-600">
        <kbd className="rounded border border-white/10 bg-white/[0.03] px-1.5 py-0.5 font-sans text-[10px] text-zinc-500">
          {cmdLabel}
        </kbd>
        <span>commands</span>
      </div>
      {advanced && (
      <a
        href="https://github.com/RealDealCPA-VR/Iron-Jarvis/blob/master/DEPLOY.md"
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1 pt-0.5 text-[11px] text-zinc-600 transition-colors hover:text-accent-soft"
      >
        Deploy to a server <MoveUpRight size={11} />
      </a>
      )}
    </div>
  );
}

/** The desktop sidebar rail. Hidden below the `md` breakpoint (see MobileNav).
 *  Collapsible: the collapsed rail is a DOMINANT arc reactor over icons-only
 *  nav — no words, tooltips carry the names. */
export function NavDrawer() {
  const [open, setOpen] = useState(false);
  const [advanced, toggleAdvanced] = useNavMode();
  const pathname = usePathname();

  // The TitleBar's hamburger owns opening (v1.111.0): the drawer subscribes to
  // ij:toggle-nav instead of rendering its own trigger, so ONE control serves
  // every screen size — the old md:hidden mobile drawer, generalized.
  useEffect(() => {
    const onToggle = () => setOpen((o) => !o);
    window.addEventListener("ij:toggle-nav", onToggle);
    return () => window.removeEventListener("ij:toggle-nav", onToggle);
  }, []);

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
    <AnimatePresence>
        {open && (
          <>
            <m.div
              key="backdrop"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              onClick={() => setOpen(false)}
              className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
            />
            <m.aside
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
              {/* v1.313.0: the theme row lives here too. The title bar hides
                  its row below sm (no room at 390px), so on a phone this was
                  the only way to change the look — and there was none. Same
                  switcher, same apply (data-theme + ij_theme), kept in step
                  with the bar's row by watching data-theme. */}
              <div className="border-t border-white/[0.06] px-5 py-3">
                <ThemeSwitcher variant="drawer" />
              </div>
              <SidebarFooter advanced={advanced} />
            </m.aside>
          </>
        )}
    </AnimatePresence>
  );
}
