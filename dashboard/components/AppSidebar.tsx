"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  AppWindow,
  ChevronsLeft,
  ChevronsRight,
  FolderKanban,
  HelpCircle,
  MessageSquare,
  MoveUpRight,
  Plus,
} from "lucide-react";
import { recordOpen } from "@/lib/appTiles";
import { popoutBridge, type PopoutBridge } from "@/lib/desktopShell";
import { useDaemon } from "@/lib/daemon";
import { useChatProjects } from "@/lib/chatList";
import { NEW_CHAT_EVENT, claimChatSlot, releaseChatSlot } from "@/lib/sidebarSlot";
import { EVERYTHING_TAB_EVENT, PINS_EVENT, SIDEBAR_HREFS, readPins, surfaceFor, type Surface } from "@/lib/surfaces";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { Button } from "@/components/ui";
import { MoodOrb } from "@/components/MoodOrb";
import SidebarChats from "@/components/chat/SidebarChats";

/**
 * THE CALM SIDEBAR (calm UI redesign S7, AUDIT §4.2, wireframe sidebar.md).
 *
 * One persistent column on a desktop, the same body in the ☰ drawer on a
 * phone:
 *
 *   ( + New chat )                     the sidebar's one primary action
 *   Build · Projects · Everything · Settings      four items, never more
 *   (your pins, up to three)           added on Everything; none by default
 *   CHATS                              on the chat surface: Chat's own thread
 *                                      list (portaled in, the drawer too);
 *                                      elsewhere the same grouped list
 *                                      (SidebarChats: projects, dots, ages)
 *   PROJECTS
 *   ● Running · v1.x   Help ▾          status, version, the Help menu (Q17)
 *
 * It replaces v1.318.0's Simple hubs and the Advanced menu (Q2): with four
 * items, Everything and the palette, nothing needs hiding. The four come from
 * the surface manifest (lib/surfaces.ts), never a copy here.
 */

const COLLAPSE_KEY = "ij_sidebar_collapsed";
/** Routes that render the chat surface (S8: home is a chat). */
export const CHAT_PATHS = ["/", "/chat"];

/** The arc-reactor brand mark. */
export function ArcMark({ size = "h-8 w-8" }: { size?: string }) {
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
            return (
              <line
                key={i}
                x1={12 + Math.cos(a) * 4.4}
                y1={12 + Math.sin(a) * 4.4}
                x2={12 + Math.cos(a) * 7.6}
                y2={12 + Math.sin(a) * 7.6}
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

function usePopout(): PopoutBridge | null {
  const [popout, setPopout] = useState<PopoutBridge | null>(null);
  useEffect(() => {
    const b = popoutBridge();
    setPopout(b && !b.isPopout ? b : null);
  }, []);
  return popout;
}

function usePins(): string[] {
  const [pins, setPins] = useState<string[]>([]);
  useEffect(() => {
    const sync = () => setPins(readPins());
    sync();
    window.addEventListener(PINS_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(PINS_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  return pins;
}

/** One nav row: icon + words (icons only when collapsed), with the desktop
 *  shell's "open in its own window" door (v1.283.0). */
function NavRow({
  s,
  active,
  collapsed,
  popout,
  onNavigate,
  pinned = false,
}: {
  s: Surface;
  active: boolean;
  collapsed: boolean;
  popout: PopoutBridge | null;
  onNavigate?: () => void;
  pinned?: boolean;
}) {
  const Icon = s.icon;
  return (
    <div className="group/row relative">
      <Link
        href={s.href}
        aria-current={active ? "page" : undefined}
        data-testid={`sidebar-nav-${s.href.slice(1)}`}
        data-pinned={pinned ? "true" : undefined}
        onClick={() => {
          recordOpen(s.href);
          onNavigate?.();
        }}
        title={collapsed ? s.label : undefined}
        className={`relative flex items-center rounded-lg py-2 text-sm transition-colors ${
          collapsed ? "justify-center px-0" : "gap-3 px-2.5"
        } ${active ? "bg-accent/[0.08] text-accent-soft" : "text-zinc-300 hover:bg-white/[0.04] hover:text-zinc-100"}`}
      >
        <Icon size={16} strokeWidth={2} className={active ? "text-accent" : "text-zinc-500"} aria-hidden />
        {!collapsed && <span className="font-medium">{s.label}</span>}
      </Link>
      {popout && !collapsed && (
        <button
          type="button"
          data-testid={`popout-row-${s.href.slice(1)}`}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            void popout.open(s.href);
            onNavigate?.();
          }}
          aria-label={`Open ${s.label} in a new window`}
          title={`Open ${s.label} in a new window`}
          className="absolute right-1.5 top-1/2 z-20 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-md text-zinc-500 opacity-0 transition-opacity hover:bg-white/[0.08] hover:text-zinc-100 focus:opacity-100 group-hover/row:opacity-100"
        >
          <AppWindow size={13} strokeWidth={2} />
        </button>
      )}
    </div>
  );
}

/** The projects under the chats: the active ones, newest first as the daemon
 *  lists them, at most six. The list comes from the same store as the chats
 *  (lib/chatList), so the sidebar reads /projects once, and it reads NO chats
 *  (on the chat surface the page's rail holds them). */
function SidebarProjects({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname() ?? "";
  const all = useChatProjects();
  const projects = (all ?? []).filter((p) => p.status !== "archived").slice(0, 6);
  if (projects.length === 0) return null;
  return (
    <div data-testid="sidebar-projects">
      <div className="px-2.5 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">Projects</div>
      {projects.map((p) => {
        const href = `/projects/${p.id}`;
        const on = pathname === href;
        return (
          <Link
            key={p.id}
            href={href}
            onClick={() => onNavigate?.()}
            aria-current={on ? "page" : undefined}
            title={p.name}
            className={`flex items-center gap-2 truncate rounded-lg px-2.5 py-1.5 text-[13px] ${
              on ? "text-accent-soft" : "text-zinc-300 hover:bg-white/[0.04] hover:text-zinc-100"
            }`}
          >
            <FolderKanban size={13} className="shrink-0 text-zinc-500" aria-hidden />
            <span className="truncate">{p.name}</span>
          </Link>
        );
      })}
    </div>
  );
}

/** Status + version + the Help menu (AUDIT Q17: Help lives here, not as a
 *  fifth nav item). The dot opens Everything › Status. */
function SidebarFooter({ collapsed, onNavigate }: { collapsed: boolean; onNavigate?: () => void }) {
  const { online, health } = useDaemon();
  const version = health?.version;
  const [helpOpen, setHelpOpen] = useState(false);
  useEffect(() => {
    if (!helpOpen) return;
    const close = (e: KeyboardEvent) => e.key === "Escape" && setHelpOpen(false);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [helpOpen]);
  const help = [
    { href: "/help", label: "Help and guides" },
    { href: `/chat?ask=${encodeURIComponent("@guide ")}`, label: "Ask the Guide" },
    { href: "/updates", label: "What's new and updates" },
  ];
  return (
    <div className="relative border-t border-white/[0.06] px-2 py-2">
      <div className={`flex items-center gap-2 ${collapsed ? "flex-col" : ""}`}>
        <Link
          href="/everything#status"
          onClick={() => {
            // Same-page hash links change no route: tell the page too.
            window.dispatchEvent(new CustomEvent(EVERYTHING_TAB_EVENT, { detail: "status" }));
            onNavigate?.();
          }}
          data-testid="sidebar-status"
          title={online ? "Running — open Status" : "Not running — reopen Iron Jarvis"}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-1.5 py-1.5 text-[12px] hover:bg-white/[0.04]"
        >
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${
              online ? "bg-emerald-400 shadow-[0_0_8px_2px_rgba(52,211,153,0.45)]" : "bg-zinc-600"
            }`}
            aria-hidden
          />
          {!collapsed && (
            <span className="truncate">
              <span className={online ? "text-zinc-300" : "text-zinc-500"}>{online ? "Running" : "Not running"}</span>
              <span className="font-mono text-zinc-600">{version ? ` · v${version}` : ""}</span>
            </span>
          )}
        </Link>
        <MoodOrb />
        <button
          type="button"
          onClick={() => setHelpOpen((o) => !o)}
          aria-haspopup="menu"
          aria-expanded={helpOpen}
          aria-label="Help"
          title="Help"
          className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-100"
        >
          <HelpCircle size={15} />
        </button>
      </div>
      {helpOpen && (
        <div
          role="menu"
          aria-label="Help"
          className="absolute bottom-12 left-2 right-2 z-50 rounded-xl border border-white/10 bg-ink-900/95 p-1 shadow-xl backdrop-blur-xl"
        >
          {help.map((h) => (
            <Link
              key={h.href}
              role="menuitem"
              href={h.href}
              onClick={() => {
                setHelpOpen(false);
                onNavigate?.();
              }}
              className="block rounded-lg px-3 py-2 text-[13px] text-zinc-200 hover:bg-white/[0.06]"
            >
              {h.label}
            </Link>
          ))}
          <div className="px-3 py-1.5 text-[11px] text-zinc-500">
            <kbd className="rounded border border-white/10 px-1 font-sans">Ctrl K</kbd> searches everything
          </div>
          <a
            role="menuitem"
            href="https://github.com/RealDealCPA-VR/Iron-Jarvis/blob/master/DEPLOY.md"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1 rounded-lg px-3 py-2 text-[12px] text-zinc-400 hover:bg-white/[0.06]"
          >
            Deploy to a server <MoveUpRight size={11} />
          </a>
        </div>
      )}
    </div>
  );
}

/** Everything the rail and the drawer share. */
export function SidebarBody({
  variant,
  collapsed = false,
  onNavigate,
}: {
  variant: "rail" | "drawer";
  collapsed?: boolean;
  onNavigate?: () => void;
}) {
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const popout = usePopout();
  const pins = usePins();
  const onChat = CHAT_PATHS.includes(pathname);
  const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);

  // On the chat surface the CHATS space is the chat page's OWN thread rail
  // (search, the row options, the project scope, the archive), portaled in:
  // in the persistent rail while the screen is wide, and (v1.329.0, calm chat
  // W4 F2) in the phone drawer while it is open, so a phone has ONE chat list
  // too. The drawer's slot carries its close, so opening a chat shuts it.
  // Elsewhere the space is SidebarChats: the same grouped list, read-only.
  const [slotEl, setSlotEl] = useState<HTMLDivElement | null>(null);
  const navigateRef = useRef(onNavigate);
  navigateRef.current = onNavigate;
  useEffect(() => {
    if (!slotEl) return;
    if (variant === "drawer") {
      claimChatSlot(slotEl, () => navigateRef.current?.());
      return () => releaseChatSlot(slotEl);
    }
    const mq = typeof window.matchMedia === "function" ? window.matchMedia("(min-width: 768px)") : null;
    const sync = () => (!mq || mq.matches ? claimChatSlot(slotEl) : releaseChatSlot(slotEl));
    sync();
    mq?.addEventListener?.("change", sync);
    return () => {
      mq?.removeEventListener?.("change", sync);
      releaseChatSlot(slotEl);
    };
  }, [slotEl, variant]);

  function newChat() {
    onNavigate?.();
    if (onChat) {
      window.dispatchEvent(new Event(NEW_CHAT_EVENT));
      return;
    }
    router.push("/chat?new=1");
  }

  const four = SIDEBAR_HREFS.map((h) => surfaceFor(h)).filter((s): s is Surface => !!s);
  const pinned = pins.map((h) => surfaceFor(h)).filter((s): s is Surface => !!s && !SIDEBAR_HREFS.includes(s.href as never));

  return (
    <>
      <div className={`shrink-0 px-2 pt-2 ${collapsed ? "" : "space-y-1.5"}`}>
        {/* AUDIT §8: the sidebar's ONE solid primary action. */}
        <Button
          variant="primary"
          onClick={newChat}
          data-testid="sidebar-new-chat"
          title="New chat"
          aria-label={collapsed ? "New chat" : undefined}
          className={`w-full ${collapsed ? "justify-center !px-0" : "justify-start"}`}
        >
          <Plus size={15} aria-hidden />
          {!collapsed && "New chat"}
        </Button>
      </div>
      <nav aria-label="Main" className="shrink-0 space-y-0.5 px-2 pt-2" data-testid="sidebar-nav">
        {four.map((s) => (
          <NavRow key={s.href} s={s} active={isActive(s.href)} collapsed={collapsed} popout={popout} onNavigate={onNavigate} />
        ))}
        {pinned.length > 0 && (
          <div className="mt-1 space-y-0.5 border-t border-white/[0.05] pt-1" data-testid="sidebar-pins">
            {pinned.map((s) => (
              <NavRow key={s.href} s={s} active={isActive(s.href)} collapsed={collapsed} popout={popout} onNavigate={onNavigate} pinned />
            ))}
          </div>
        )}
      </nav>
      {!collapsed && (
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-2 pb-2" data-testid="sidebar-lists">
          <div className="flex items-center gap-1.5 px-2.5 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
            <MessageSquare size={11} aria-hidden /> Chats
          </div>
          {onChat ? (
            <div
              id={variant === "rail" ? "ij-sidebar-chat-slot" : "ij-drawer-chat-slot"}
              ref={setSlotEl}
              className="flex min-h-[12rem] flex-1 flex-col"
            />
          ) : (
            <SidebarChats onNavigate={onNavigate} />
          )}
          <SidebarProjects onNavigate={onNavigate} />
        </div>
      )}
      {collapsed && <div className="flex-1" />}
      {variant === "drawer" && (
        // v1.313.0: the theme row lives in the drawer too — a phone has no
        // other quick way to change the look.
        <div className="shrink-0 border-t border-white/[0.06] px-4 py-3">
          <ThemeSwitcher variant="drawer" />
        </div>
      )}
      <SidebarFooter collapsed={collapsed} onNavigate={onNavigate} />
    </>
  );
}

/** The persistent sidebar on a wide screen (md and up). Collapsible to icons. */
export function AppSidebar() {
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => {
    try {
      setCollapsed(window.localStorage.getItem(COLLAPSE_KEY) === "1");
    } catch {
      /* storage blocked */
    }
  }, []);
  // A pop-out window is one module, not the whole app: no sidebar there.
  const [isPopout, setIsPopout] = useState(false);
  useEffect(() => {
    setIsPopout(popoutBridge()?.isPopout === true);
  }, []);
  if (isPopout) return null;
  const toggle = () => {
    setCollapsed((c) => {
      try {
        window.localStorage.setItem(COLLAPSE_KEY, c ? "0" : "1");
      } catch {
        /* storage blocked */
      }
      return !c;
    });
  };
  return (
    <aside
      data-testid="app-sidebar"
      aria-label="Sidebar"
      className={`hidden shrink-0 flex-col border-r border-white/[0.06] bg-ink-950/40 md:flex ${collapsed ? "w-14" : "w-64"}`}
    >
      <div className={`flex shrink-0 items-center px-2 pt-2 ${collapsed ? "justify-center" : "justify-between"}`}>
        {!collapsed && (
          <Link href="/" className="flex items-center gap-2 rounded-lg px-1 py-1 text-zinc-100" aria-label="Iron Jarvis — home">
            <ArcMark size="h-6 w-6" />
            <span className="text-sm font-semibold tracking-tight">Iron Jarvis</span>
          </Link>
        )}
        <button
          type="button"
          onClick={toggle}
          aria-label={collapsed ? "Expand the sidebar" : "Collapse the sidebar"}
          aria-pressed={collapsed}
          title={collapsed ? "Expand" : "Collapse"}
          className="grid h-8 w-8 place-items-center rounded-lg text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200"
        >
          {collapsed ? <ChevronsRight size={15} /> : <ChevronsLeft size={15} />}
        </button>
      </div>
      <SidebarBody variant="rail" collapsed={collapsed} />
    </aside>
  );
}

