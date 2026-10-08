"use client";

// The mission screen's LEFT RAIL (v1.307.0): New task, then the places the
// user goes next. It is page-local navigation — the app-wide drawer is
// untouched — and every row is a plain link except the two that belong to the
// Agents page itself (New task, Agents = the team screens).

import Link from "next/link";
import type { ReactNode } from "react";
import {
  FileText,
  FolderKanban,
  MessageSquare,
  Plus,
  Settings,
  Users,
  Wrench,
} from "lucide-react";

export type RailTarget = "new" | "team";

interface Row {
  key: string;
  label: string;
  icon: ReactNode;
  href?: string;
  action?: RailTarget;
}

export const MISSION_RAIL: Row[] = [
  { key: "new", label: "New task", icon: <Plus size={16} />, action: "new" },
  { key: "chat", label: "Chat", icon: <MessageSquare size={16} />, href: "/chat" },
  { key: "projects", label: "Projects", icon: <FolderKanban size={16} />, href: "/projects" },
  { key: "agents", label: "Agents", icon: <Users size={16} />, action: "team" },
  { key: "tools", label: "Tools", icon: <Wrench size={16} />, href: "/tools" },
  { key: "files", label: "Files", icon: <FileText size={16} />, href: "/documents" },
  { key: "settings", label: "Settings", icon: <Settings size={16} />, href: "/settings" },
];

export function MissionRail({
  active,
  onAction,
}: {
  /** The row to highlight ("new" while composing; none while watching). */
  active: string | null;
  onAction: (target: RailTarget) => void;
}) {
  return (
    <nav
      aria-label="Agents navigation"
      data-testid="mission-rail"
      className="card-surface flex flex-row gap-1 p-1.5 lg:flex-col lg:p-2"
    >
      {MISSION_RAIL.map((row) => {
        const on = active === row.key;
        // v1.315.0 (UX wave 3): below lg the rail was a clipped second menu
        // ("New task · Chat · Projects · Agent…") as the first block of the
        // page. The app-wide destinations are plain links the nav drawer
        // already offers, so below lg only the two page-local rows (New task,
        // Agents) show. Hidden by CSS, never by conditional rendering: every
        // row still renders (agents-mission-v1307 pins all seven).
        const pageLocal = Boolean(row.action);
        const cls = `${pageLocal ? "flex flex-1 justify-center lg:flex-none lg:justify-start" : "hidden lg:flex"} shrink-0 items-center gap-2.5 rounded-xl px-3 py-2 text-[13px] transition-colors ${
          on
            ? "bg-accent/15 font-semibold text-accent"
            : "text-zinc-300 hover:bg-white/5 hover:text-zinc-100"
        } ${row.key === "new" ? "lg:mb-2" : ""}`;
        if (row.action) {
          return (
            <button
              key={row.key}
              type="button"
              data-testid={`mission-rail-${row.key}`}
              aria-current={on ? "page" : undefined}
              className={cls}
              onClick={() => onAction(row.action as RailTarget)}
            >
              {row.icon}
              <span>{row.label}</span>
            </button>
          );
        }
        return (
          <Link
            key={row.key}
            href={row.href as string}
            data-testid={`mission-rail-${row.key}`}
            className={cls}
          >
            {row.icon}
            <span>{row.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
