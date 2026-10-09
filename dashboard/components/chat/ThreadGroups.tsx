"use client";

// The chat list, grouped under projects (v1.327.0, calm chat B2; wired into
// the chat page's thread rail in W2-1).
//
// Each project with chats gets a quiet heading (folder icon + name) and its
// chats as 32 px rows: a status dot (accent with a calm pulse = working on it,
// amber = waiting on you, a small accent dot = new since you last looked, none
// otherwise), the title cut with an ellipsis, and a short age on the right
// ("now", "12m", "3h", "2d"). Five rows per group, then "Show more". Chats
// with no project (or whose project is gone) sit under "No project", last.
// Pinned chats (the page's "Pin to top") sit first, under "Pinned".
//
// PURE: it draws what it is given and calls back; it fetches nothing and
// stores nothing. The statuses come from `lib/threadStatus.threadStatuses`
// (the daemon's `running`/`waiting` + this browser's last-viewed stamps).
// Rows are ghosts that fill only on hover; the open chat has a subtle fill.
// Every row and "Show more" is a real button (Tab reaches them), and the
// arrow keys move between rows. Theme tokens only, so every Mark and the
// light Daylight Mark draw it correctly.
//
// The page's per-row extras ride three optional slots, so every capability
// the old flat list had stays where it was: `rowAction` (the ⋯ options,
// drawn BESIDE the row button — a button cannot hold a button), `rowEditor`
// (the inline rename box, drawn in the row's place while it returns
// something) and `rowBadge` (a short word after the title: a messaging
// chat's channel). While a row has an action, a hover-capable screen shows
// the age until the row is hovered or focused and the ⋯ takes its place; a
// touch screen keeps both, the age left of the always-visible ⋯.

import { useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { Folder, Pin } from "lucide-react";

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

/** The heading of the chats pinned to the top. */
export const PINNED_LABEL = "Pinned";

export interface ThreadGroup<T extends ThreadSummary = ThreadSummary> {
  /** `p:<project id>`, `none` or `pinned`. */
  key: string;
  projectId: string | null;
  name: string;
  threads: T[];
}

export interface ThreadGroupsProps<T extends ThreadSummary = ThreadSummary> {
  threads: T[];
  projects: { id: string; name: string }[];
  activeId?: string | null;
  onOpen: (id: string) => void;
  /** Told when a group's "Show more" is pressed (the group also opens in
   *  place). `null` = the "No project" (or "Pinned") group. */
  onShowMore?: (projectId: string | null) => void;
  /** Status per thread id; a missing id draws no dot. */
  statuses?: Record<string, ThreadStatus>;
  /** "Now" for the ages (tests pass a fixed clock). */
  now?: number;
  /** Chats pinned to the top: drawn first, under "Pinned". */
  pinnedIds?: string[];
  /** Rows per group before "Show more" (Infinity = every row). */
  limit?: number;
  /** false = no group headings, one plain list (pinned rows still first and
   *  marked with a pin) — for a list already scoped to one project. */
  headings?: boolean;
  /** Drawn beside the row button, at its right edge (the ⋯ options). */
  rowAction?: (t: T) => ReactNode;
  /** Drawn in the row's place while it returns something (inline rename). */
  rowEditor?: (t: T) => ReactNode;
  /** A short word after the title (a messaging chat's channel). */
  rowBadge?: (t: T) => ReactNode;
}

/** Group chats by project: newest chat first inside a group, groups ordered
 *  by their newest chat, "No project" last. A chat whose project is not in
 *  `projects` goes to "No project". Pinned chats (by id) form a "Pinned"
 *  group that comes first and are left out of their project's group. */
export function groupThreads<T extends ThreadSummary>(
  threads: T[],
  projects: { id: string; name: string }[],
  pinnedIds: string[] = [],
): ThreadGroup<T>[] {
  const names = new Map(projects.map((p) => [p.id, p.name]));
  const pinned = new Set(pinnedIds);
  const sorted = threads
    .map((t, i) => ({ t, i, at: threadTime(t.updated_at) ?? -Infinity }))
    .sort((a, b) => (b.at === a.at ? a.i - b.i : b.at - a.at))
    .map((x) => x.t);
  const groups = new Map<string, ThreadGroup<T>>();
  let none: ThreadGroup<T> | null = null;
  let top: ThreadGroup<T> | null = null;
  for (const t of sorted) {
    if (pinned.has(t.id)) {
      top ??= { key: "pinned", projectId: null, name: PINNED_LABEL, threads: [] };
      top.threads.push(t);
      continue;
    }
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
  if (top) out.unshift(top);
  if (none) out.push(none);
  return out;
}

const DOT: Record<ThreadStatus, string> = {
  // Working on it: the accent with a soft halo, breathing slowly — and still
  // apart from "unread" (a bare accent dot) when motion is reduced.
  running: "bg-accent ring-[3px] ring-accent/25 motion-safe:animate-pulse",
  waiting: "bg-tone-warn",
  unread: "bg-accent",
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

export default function ThreadGroups<T extends ThreadSummary>({
  threads,
  projects,
  activeId = null,
  onOpen,
  onShowMore,
  statuses = {},
  now,
  pinnedIds,
  limit = GROUP_LIMIT,
  headings = true,
  rowAction,
  rowEditor,
  rowBadge,
}: ThreadGroupsProps<T>) {
  const pinKey = (pinnedIds ?? []).join("\u0000");
  const groups = useMemo(
    () => groupThreads(threads, projects, pinnedIds ?? []),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- pinKey stands for pinnedIds
    [threads, projects, pinKey],
  );
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
      {groups.map((g, gi) => {
        const expanded = !!open[g.key];
        const visible = expanded
          ? g.threads
          : g.threads.filter((t, i) => i < limit || t.id === activeId);
        const hidden = g.threads.length - visible.length;
        const headingId = `thread-group-heading-${g.key}`;
        const pinnedGroup = g.key === "pinned";
        return (
          <section
            key={g.key}
            aria-labelledby={headings ? headingId : undefined}
            aria-label={headings ? undefined : g.name}
            data-testid={`thread-group-${g.key}`}
            className="min-w-0"
          >
            {headings ? (
              <h3
                id={headingId}
                className={`${gi === 0 ? "mt-1.5" : "mt-3.5"} flex min-w-0 items-center gap-1.5 px-2.5 text-[11px] font-medium uppercase tracking-[0.06em] text-zinc-500`}
              >
                {pinnedGroup ? (
                  <Pin size={12} strokeWidth={2} aria-hidden="true" className="shrink-0" />
                ) : (
                  <Folder size={12} strokeWidth={2} aria-hidden="true" className="shrink-0" />
                )}
                <span className="truncate">{g.name}</span>
              </h3>
            ) : null}
            <ul className={`${headings ? "mt-1" : ""} flex min-w-0 flex-col gap-px`}>
              {visible.map((t) => {
                const status = statuses[t.id] ?? "idle";
                const words = statusWords(status);
                const active = t.id === activeId;
                const age = formatAge(t.updated_at, clock);
                const title = (t.title || "").trim() || "Untitled chat";
                const editor = rowEditor ? rowEditor(t) : null;
                const action = rowAction && editor == null ? rowAction(t) : null;
                const badge = rowBadge ? rowBadge(t) : null;
                return (
                  <li key={t.id} className="group/thread relative min-w-0">
                    {editor != null ? (
                      editor
                    ) : (
                      <button
                        type="button"
                        data-thread-row=""
                        data-testid={`thread-row-${t.id}`}
                        aria-current={active ? "page" : undefined}
                        onClick={() => onOpen(t.id)}
                        title={title}
                        className={[
                          "flex h-8 w-full min-w-0 items-center gap-2 rounded-[10px] pl-[22px] text-left text-[13px] transition-colors",
                          // Room for the ⋯: always on touch; on a mouse only
                          // while it shows (it takes the age's place).
                          action != null ? "pr-9 [@media(hover:hover)]:pr-2.5" : "pr-2.5",
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
                        {!headings && pinnedIds?.includes(t.id) ? (
                          <Pin
                            size={11}
                            aria-label="Pinned"
                            className="-ml-0.5 shrink-0 text-zinc-500"
                          />
                        ) : null}
                        <span className="min-w-0 flex-1 truncate">{title}</span>
                        {badge}
                        {words ? <span className="sr-only">{`, ${words}`}</span> : null}
                        {age ? (
                          <time
                            dateTime={t.updated_at || undefined}
                            className={`shrink-0 text-[12px] tabular-nums text-zinc-500 transition-opacity ${
                              action != null
                                ? "[@media(hover:hover)]:group-focus-within/thread:opacity-0 [@media(hover:hover)]:group-hover/thread:opacity-0"
                                : ""
                            }`}
                          >
                            {age}
                          </time>
                        ) : null}
                      </button>
                    )}
                    {action}
                  </li>
                );
              })}
            </ul>
            {g.threads.length > limit && (expanded || hidden > 0) ? (
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
