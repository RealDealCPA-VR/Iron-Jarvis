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
//
// v1.329.0 (calm chat W5 G3): ONE shape in every scope. Inside a project chat
// the page passes `lead` (that project) and `foldOthers`: the project's group
// comes first and open (its pinned chats stay in it, first, marked with a
// pin), and every other group starts folded to its heading, which still
// shows the most urgent dot of its chats (waiting, working, new) and how
// many chats it holds; pressing the heading opens it in place. A search
// (`forceOpen`) opens every group. And when two or more VISIBLE rows share a
// title, those rows (only those) get a quiet second part, the time of day or
// the day (lib/sameTitleRows), so four "Check what changed…" rows read apart.

import { useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { ChevronRight, Folder, Pin } from "lucide-react";

import { sameTitleLabels } from "@/lib/sameTitleRows";
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
  /** The project the page is working in: its group comes first and open,
   *  named `name` even before the project list has answered. */
  lead?: LeadProject | null;
  /** Every group but `lead`'s starts folded to its heading (a dot and a
   *  count on it); the heading opens it. */
  foldOthers?: boolean;
  /** Open every group (a search is showing its matches). */
  forceOpen?: boolean;
}

/** The project a list is opened in (ThreadGroups' `lead`). */
export interface LeadProject {
  projectId: string;
  name: string;
}

/** The most urgent status among `ids` (waiting, then working, then new), or
 *  "idle" when none is lit: what a folded group's heading shows. */
export function groupStatus(
  threads: readonly ThreadSummary[],
  statuses: Record<string, ThreadStatus>,
): ThreadStatus {
  let best: ThreadStatus = "idle";
  const rank: Record<ThreadStatus, number> = { waiting: 3, running: 2, unread: 1, idle: 0 };
  for (const t of threads) {
    const s = statuses[t.id] ?? "idle";
    if (rank[s] > rank[best]) best = s;
  }
  return best;
}

/** Group chats by project: newest chat first inside a group, groups ordered
 *  by their newest chat, "No project" last. A chat whose project is not in
 *  `projects` goes to "No project". Pinned chats (by id) form a "Pinned"
 *  group that comes first and are left out of their project's group.
 *  v1.329.0: with a `lead` project its group comes FIRST (before Pinned),
 *  under the lead's name even when `projects` does not list it yet, and its
 *  own pinned chats stay in it, first. */
export function groupThreads<T extends ThreadSummary>(
  threads: T[],
  projects: { id: string; name: string }[],
  pinnedIds: string[] = [],
  lead: LeadProject | null = null,
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
  let first: ThreadGroup<T> | null = null;
  const leadPinned: T[] = [];
  for (const t of sorted) {
    if (lead && t.project_id === lead.projectId) {
      first ??= { key: `p:${lead.projectId}`, projectId: lead.projectId, name: lead.name, threads: [] };
      if (pinned.has(t.id)) leadPinned.push(t);
      else first.threads.push(t);
      continue;
    }
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
  if (first) {
    first.threads = [...leadPinned, ...first.threads];
    out.unshift(first);
  }
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
  lead = null,
  foldOthers = false,
  forceOpen = false,
}: ThreadGroupsProps<T>) {
  const pinKey = (pinnedIds ?? []).join("\u0000");
  const leadId = lead?.projectId ?? null;
  const leadName = lead?.name ?? "";
  const groups = useMemo(
    () =>
      groupThreads(
        threads,
        projects,
        pinnedIds ?? [],
        leadId ? { projectId: leadId, name: leadName } : null,
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- pinKey stands for pinnedIds
    [threads, projects, pinKey, leadId, leadName],
  );
  const [open, setOpen] = useState<Record<string, boolean>>({});
  // v1.329.0: groups the user unfolded (or folded back) by hand, by key.
  const [unfolded, setUnfolded] = useState<Record<string, boolean>>({});
  const clock = now ?? Date.now();

  // What each group shows: folded (heading only), or its rows up to the cut.
  const shown = groups.map((g) => {
    const leadGroup = leadId != null && g.key === `p:${leadId}`;
    const foldable = headings && foldOthers && !leadGroup;
    // The open chat's group never starts folded: the chat on screen stays
    // findable in the list.
    const startsOpen = !foldable || g.threads.some((t) => t.id === activeId);
    const folded = foldable && !forceOpen && !(unfolded[g.key] ?? startsOpen);
    const expanded = !!open[g.key];
    const visible = folded
      ? []
      : expanded
        ? g.threads
        : g.threads.filter((t, i) => i < limit || t.id === activeId);
    return { g, leadGroup, foldable, folded, expanded, visible };
  });
  // Same-titled rows among the ones on screen get a quiet second part.
  const twins = sameTitleLabels(
    shown.flatMap((s) => s.visible),
    clock,
  );

  if (groups.length === 0) return null;

  return (
    <nav
      aria-label="Chats"
      data-testid="thread-groups"
      className="flex min-w-0 flex-col"
      onKeyDown={moveFocus}
    >
      {shown.map(({ g, leadGroup, foldable, folded, expanded, visible }, gi) => {
        const hidden = g.threads.length - visible.length;
        const headingId = `thread-group-heading-${g.key}`;
        const listId = `thread-group-list-${g.key}`;
        const pinnedGroup = g.key === "pinned";
        const icon = pinnedGroup ? (
          <Pin size={12} strokeWidth={2} aria-hidden="true" className="shrink-0" />
        ) : (
          <Folder size={12} strokeWidth={2} aria-hidden="true" className="shrink-0" />
        );
        // A folded heading still says whether something in it needs a look.
        const summary = folded ? groupStatus(g.threads, statuses) : "idle";
        const summaryWords = statusWords(summary);
        return (
          <section
            key={g.key}
            aria-labelledby={headings ? headingId : undefined}
            aria-label={headings ? undefined : g.name}
            data-testid={`thread-group-${g.key}`}
            data-folded={foldable ? (folded ? "true" : "false") : undefined}
            className="min-w-0"
          >
            {headings ? (
              <h3
                id={headingId}
                className={`${gi === 0 ? "mt-1.5" : "mt-3.5"} flex min-w-0 items-center gap-1.5 ${foldable ? "" : "px-2.5"} text-[11px] font-medium uppercase tracking-[0.06em] text-zinc-500`}
              >
                {foldable ? (
                  <button
                    type="button"
                    data-testid={`thread-group-toggle-${g.key}`}
                    aria-expanded={!folded}
                    aria-controls={folded ? undefined : listId}
                    onClick={() => setUnfolded((u) => ({ ...u, [g.key]: folded }))}
                    title={folded ? `Show the chats in ${g.name}` : `Fold ${g.name}`}
                    className="flex h-6 min-w-0 flex-1 items-center gap-1.5 rounded-[8px] px-2.5 text-left uppercase tracking-[0.06em] transition-colors hover:bg-white/[0.06] hover:text-zinc-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
                  >
                    {icon}
                    <span className="min-w-0 truncate">{g.name}</span>
                    {folded && summary !== "idle" ? (
                      <span
                        aria-hidden="true"
                        data-testid={`thread-group-dot-${g.key}`}
                        data-status={summary}
                        title={summaryWords || undefined}
                        className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT[summary]}`}
                      />
                    ) : null}
                    {folded && summaryWords ? (
                      <span className="sr-only">{`, ${summaryWords}`}</span>
                    ) : null}
                    {folded ? (
                      <span className="ml-auto shrink-0 normal-case tracking-normal tabular-nums">
                        {g.threads.length}
                      </span>
                    ) : null}
                    <ChevronRight
                      size={12}
                      aria-hidden="true"
                      className={`shrink-0 transition-transform ${folded ? "" : "ml-auto rotate-90"}`}
                    />
                  </button>
                ) : (
                  <>
                    {icon}
                    <span className="truncate">{g.name}</span>
                  </>
                )}
              </h3>
            ) : null}
            {folded ? null : (
            <ul id={listId} className={`${headings ? "mt-1" : ""} flex min-w-0 flex-col gap-px`}>
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
                        {(!headings || leadGroup) && pinnedIds?.includes(t.id) ? (
                          <Pin
                            size={11}
                            aria-label="Pinned"
                            className="-ml-0.5 shrink-0 text-zinc-500"
                          />
                        ) : null}
                        <span className="min-w-0 flex-1 truncate">{title}</span>
                        {twins.has(t.id) ? (
                          <span
                            data-testid={`thread-twin-${t.id}`}
                            className="shrink-0 text-[12px] tabular-nums text-zinc-500"
                          >
                            {twins.get(t.id)}
                          </span>
                        ) : null}
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
            )}
            {!folded && g.threads.length > limit && (expanded || hidden > 0) ? (
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
