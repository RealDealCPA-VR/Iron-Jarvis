"use client";

// The chat list, grouped under projects (v1.327.0, calm chat B2).
//
// Each project with chats gets a quiet heading (folder icon + name) and its
// chats as 32 px rows: a status dot (accent = working on it, amber = waiting
// on you, violet = new since you last looked, none otherwise), the title cut
// with an ellipsis, and a short age on the right ("now", "12m", "3h", "2d").
// Five rows per group, then "Show more". Chats with no project (or whose
// project is gone) sit under "No project", last.
//
// PURE: it draws what it is given and calls back; it fetches nothing and
// stores nothing. The statuses come from `lib/threadStatus.threadStatuses`
// (the daemon's `running`/`waiting` + this browser's last-viewed stamps).
// Rows are ghosts that fill only on hover; the open chat has a subtle fill.
// Every row and "Show more" is a real button (Tab reaches them), and the
// arrow keys move between rows. Theme tokens only, so every Mark and the
// light Daylight Mark draw it correctly.

import { useMemo, useState, type KeyboardEvent } from "react";
import { Folder } from "lucide-react";

import {
  formatAge,
  statusWords,
  threadTime,
  type ThreadStatus,
  type ThreadSummary,
} from "@/lib/threadStatus";

/** Rows shown per group before "Show more". */
export const GROUP_LIMIT = 5;

/** The heading of the chats that belong to no (known) project. */
export const NO_PROJECT_LABEL = "No project";

export interface ThreadGroup {
  /** `p:<project id>` or `none`. */
  key: string;
  projectId: string | null;
  name: string;
  threads: ThreadSummary[];
}

export interface ThreadGroupsProps {
  threads: ThreadSummary[];
  projects: { id: string; name: string }[];
  activeId?: string | null;
  onOpen: (id: string) => void;
  /** Told when a group's "Show more" is pressed (the group also opens in
   *  place). `null` = the "No project" group. */
  onShowMore?: (projectId: string | null) => void;
  /** Status per thread id; a missing id draws no dot. */
  statuses?: Record<string, ThreadStatus>;
  /** "Now" for the ages (tests pass a fixed clock). */
  now?: number;
}

/** Group chats by project: newest chat first inside a group, groups ordered
 *  by their newest chat, "No project" last. A chat whose project is not in
 *  `projects` goes to "No project". */
export function groupThreads(
  threads: ThreadSummary[],
  projects: { id: string; name: string }[],
): ThreadGroup[] {
  const names = new Map(projects.map((p) => [p.id, p.name]));
  const sorted = threads
    .map((t, i) => ({ t, i, at: threadTime(t.updated_at) ?? -Infinity }))
    .sort((a, b) => (b.at === a.at ? a.i - b.i : b.at - a.at))
    .map((x) => x.t);
  const groups = new Map<string, ThreadGroup>();
  let none: ThreadGroup | null = null;
  for (const t of sorted) {
    const pid = t.project_id && names.has(t.project_id) ? t.project_id : null;
    if (pid == null) {
      none ??= { key: "none", projectId: null, name: NO_PROJECT_LABEL, threads: [] };
      none.threads.push(t);
      continue;
    }
    const key = `p:${pid}`;
    let g = groups.get(key);
    if (!g) {
      g = { key, projectId: pid, name: names.get(pid) || "Project", threads: [] };
      groups.set(key, g);
    }
    g.threads.push(t);
  }
  const out = [...groups.values()];
  if (none) out.push(none);
  return out;
}

const DOT: Record<ThreadStatus, string> = {
  running: "bg-accent",
  waiting: "bg-tone-warn",
  unread: "bg-tone-violet",
  idle: "bg-transparent",
};

function moveFocus(e: KeyboardEvent<HTMLElement>) {
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  const rows = Array.from(
    e.currentTarget.querySelectorAll<HTMLButtonElement>("button[data-thread-row]"),
  );
  const at = rows.indexOf(document.activeElement as HTMLButtonElement);
  if (at < 0) return;
  const next = rows[e.key === "ArrowDown" ? at + 1 : at - 1];
  if (!next) return;
  e.preventDefault();
  next.focus();
}

export default function ThreadGroups({
  threads,
  projects,
  activeId = null,
  onOpen,
  onShowMore,
  statuses = {},
  now,
}: ThreadGroupsProps) {
  const groups = useMemo(() => groupThreads(threads, projects), [threads, projects]);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const clock = now ?? Date.now();

  if (groups.length === 0) return null;

  return (
    <nav
      aria-label="Chats"
      data-testid="thread-groups"
      className="flex min-w-0 flex-col"
      onKeyDown={moveFocus}
    >
      {groups.map((g) => {
        const expanded = !!open[g.key];
        const visible = expanded
          ? g.threads
          : g.threads.filter((t, i) => i < GROUP_LIMIT || t.id === activeId);
        const hidden = g.threads.length - visible.length;
        const headingId = `thread-group-heading-${g.key}`;
        return (
          <section
            key={g.key}
            aria-labelledby={headingId}
            data-testid={`thread-group-${g.key}`}
            className="min-w-0"
          >
            <h3
              id={headingId}
              className="mt-3.5 flex min-w-0 items-center gap-1.5 px-2.5 text-[11px] font-medium uppercase tracking-[0.06em] text-zinc-500"
            >
              <Folder size={12} strokeWidth={2} aria-hidden="true" className="shrink-0" />
              <span className="truncate">{g.name}</span>
            </h3>
            <ul className="mt-1 flex min-w-0 flex-col gap-px">
              {visible.map((t) => {
                const status = statuses[t.id] ?? "idle";
                const words = statusWords(status);
                const active = t.id === activeId;
                const age = formatAge(t.updated_at, clock);
                return (
                  <li key={t.id} className="min-w-0">
                    <button
                      type="button"
                      data-thread-row=""
                      data-testid={`thread-row-${t.id}`}
                      aria-current={active ? "page" : undefined}
                      onClick={() => onOpen(t.id)}
                      className={[
                        "flex h-8 w-full min-w-0 items-center gap-2 rounded-[10px] pl-[22px] pr-2.5 text-left text-[13px] transition-colors",
                        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60",
                        active
                          ? "bg-white/[0.07] text-zinc-100"
                          : "text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-200",
                      ].join(" ")}
                    >
                      <span
                        aria-hidden="true"
                        data-testid={`thread-dot-${t.id}`}
                        data-status={status}
                        title={words || undefined}
                        className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT[status]}`}
                      />
                      <span className="min-w-0 flex-1 truncate">
                        {(t.title || "").trim() || "(untitled)"}
                      </span>
                      {words ? <span className="sr-only">{`, ${words}`}</span> : null}
                      {age ? (
                        <time
                          dateTime={t.updated_at || undefined}
                          className="shrink-0 text-[12px] tabular-nums text-zinc-500"
                        >
                          {age}
                        </time>
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ul>
            {g.threads.length > GROUP_LIMIT && (expanded || hidden > 0) ? (
              <button
                type="button"
                data-testid={`thread-show-more-${g.key}`}
                aria-expanded={expanded}
                onClick={() => {
                  setOpen((o) => ({ ...o, [g.key]: !expanded }));
                  if (!expanded) onShowMore?.(g.projectId);
                }}
                className="mt-px flex h-7 w-full items-center rounded-[10px] pl-[22px] pr-2.5 text-left text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
              >
                {expanded ? "Show less" : "Show more"}
              </button>
            ) : null}
          </section>
        );
      })}
    </nav>
  );
}
