"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import {
  LayoutDashboard,
  Boxes,
  SquareKanban,
  Sparkles,
  BrainCircuit,
  Package,
  Workflow,
  Bot,
  CalendarClock,
  FileSearch,
  Database,
  KeyRound,
  Plug,
  Megaphone,
  Webhook,
  type LucideIcon,
} from "lucide-react";
import { API_BASE } from "@/lib/api";
import { useEvents } from "@/lib/useEvents";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

const NAV: NavItem[] = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/sessions", label: "Sessions", icon: Boxes },
  { href: "/kanban", label: "Kanban", icon: SquareKanban },
  { href: "/agents", label: "Agents", icon: Bot },
  { href: "/workflows", label: "Workflows", icon: Workflow },
  { href: "/schedules", label: "Schedules", icon: CalendarClock },
  { href: "/skills", label: "Skills", icon: Sparkles },
  { href: "/memory", label: "Memory", icon: BrainCircuit },
  { href: "/ltm", label: "Long-term Memory", icon: Database },
  { href: "/filesearch", label: "File Search", icon: FileSearch },
  { href: "/artifacts", label: "Artifacts", icon: Package },
  { href: "/secrets", label: "Secrets", icon: KeyRound },
  { href: "/integrations", label: "Integrations", icon: Plug },
  { href: "/channels", label: "Channels", icon: Megaphone },
  { href: "/webhooks", label: "Webhooks", icon: Webhook },
];

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

export function Sidebar() {
  const pathname = usePathname();
  const { connected } = useEvents(1);
  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-white/[0.06] bg-ink-900/70 backdrop-blur-xl">
      <div className="px-5 py-5">
        <Link href="/" className="flex items-center gap-3 text-accent">
          <ArcMark />
          <div>
            <div className="text-[15px] font-semibold tracking-tight text-zinc-50">
              Iron Jarvis
            </div>
            <div className="text-[11px] tracking-wide text-zinc-500">
              control center
            </div>
          </div>
        </Link>
      </div>

      <div className="mx-5 h-px bg-accent-line opacity-60" />

      <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-4">
        {NAV.map((item) => {
          const active = isActive(item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors ${
                active
                  ? "text-accent-soft"
                  : "text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-100"
              }`}
            >
              {active && (
                <motion.span
                  layoutId="nav-active"
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
      </nav>

      <div className="space-y-2 border-t border-white/[0.06] px-5 py-4">
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
      </div>
    </aside>
  );
}
