"use client";

/**
 * v1.290.0 — Other agents: the Claude Code and Codex sessions on this PC.
 *
 * READ-ONLY. The daemon reads the harnesses' own session logs
 * (`GET /history/sessions`, `GET /history/sessions/{harness}/{id}`) and runs
 * the same safety rules over them; nothing is written, moved or sent anywhere,
 * and the card subtitle says so in those words.
 *
 * A session's detail puts its findings FIRST (the same FindingRow the Safety
 * checks card uses), then a compact event timeline: one line per action, the
 * command/path/url clipped, ok/failed, and any text collapsed by default.
 */

import { useState } from "react";
import { ApiError, get } from "@/lib/api";
import {
  Ban,
  Bot,
  FilePen,
  FileText,
  FileX,
  Globe,
  MessageSquare,
  Terminal,
  Users,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";
import { useApi } from "@/lib/useApi";
import { timeAgo } from "@/lib/format";
import { Card, DataError, OfflineHint, SkeletonRows } from "@/components/ui";
import {
  FindingRow,
  SubagentTag,
  actionWord,
  clip,
  eventTarget,
} from "@/components/SafetyChecks";
import type {
  AgentEvent,
  HistorySession,
  HistorySessionDetail,
  HistorySessionsResponse,
} from "@/lib/types";

type Filter = "all" | "claude-code" | "codex";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "claude-code", label: "Claude Code" },
  { key: "codex", label: "Codex" },
];

const HARNESS_WORDS: Record<string, string> = {
  "claude-code": "Claude Code",
  codex: "Codex",
};

const HARNESS_STYLE: Record<string, string> = {
  "claude-code": "border-violet-400/30 bg-violet-400/[0.08] text-violet-200",
  codex: "border-sky-400/30 bg-sky-400/[0.08] text-sky-200",
};

function HarnessBadge({ harness }: { harness: string }) {
  return (
    <span
      data-testid="harness-badge"
      className={`inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold ${
        HARNESS_STYLE[harness] ?? "border-white/15 bg-white/[0.04] text-zinc-300"
      }`}
    >
      {HARNESS_WORDS[harness] ?? harness}
    </span>
  );
}

const ACTION_ICONS: Record<string, LucideIcon> = {
  "command.executed": Terminal,
  "file.read": FileText,
  "file.write": FilePen,
  "file.delete": FileX,
  "network.request": Globe,
  "tool.called": Wrench,
  "approval.denied": Ban,
  "prompt.submitted": MessageSquare,
  "response.completed": Bot,
};

/** How much of an event's text is ever rendered, even when expanded. */
const TEXT_CLIP = 1500;
/** Events per page: the detail route is PAGED (a session can hold thousands).
 *  Findings always cover the whole session; only the timeline pages. */
export const HISTORY_PAGE = 500;

function detailPath(harness: string, id: string, offset: number): string {
  return `/history/sessions/${encodeURIComponent(harness)}/${encodeURIComponent(id)}?offset=${offset}&limit=${HISTORY_PAGE}`;
}

function EventRow({ e }: { e: AgentEvent }) {
  const [open, setOpen] = useState(false);
  const Icon = ACTION_ICONS[e.action] ?? Wrench;
  const target = eventTarget(e);
  const text = e.text ?? "";
  return (
    <li data-testid="history-event" data-action={e.action} className="py-1.5">
      <div className="flex min-w-0 items-center gap-2 text-[12px]">
        <Icon size={13} className="shrink-0 text-zinc-500" aria-hidden="true" />
        <span className="shrink-0 text-zinc-400">{actionWord(e.action)}</span>
        {e.tool ? (
          <span className="shrink-0 font-mono text-[11px] text-zinc-500">{e.tool}</span>
        ) : null}
        {e.subagent ? <SubagentTag name={e.subagent} testId="history-event-subagent" /> : null}
        {target ? (
          <code
            title={target.length > 160 ? target.slice(0, 2000) : undefined}
            className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-zinc-300"
          >
            {clip(target, 160)}
          </code>
        ) : (
          <span className="flex-1" />
        )}
        {e.ok === true ? (
          <span data-testid="history-event-status" className="shrink-0 text-[10.5px] text-emerald-400">
            ok
          </span>
        ) : e.ok === false ? (
          <span data-testid="history-event-status" className="shrink-0 text-[10.5px] text-rose-400">
            failed
          </span>
        ) : null}
        {text ? (
          <button
            type="button"
            data-testid="history-event-text-toggle"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            className="shrink-0 text-[10.5px] text-zinc-500 hover:text-zinc-200"
          >
            {open ? "Hide text" : "Show text"}
          </button>
        ) : null}
      </div>
      {open && text ? (
        <pre
          data-testid="history-event-text"
          className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-white/[0.06] bg-black/20 px-2.5 py-1.5 font-mono text-[11px] text-zinc-400"
        >
          {text.length > TEXT_CLIP ? `${text.slice(0, TEXT_CLIP)}…` : text}
        </pre>
      ) : null}
    </li>
  );
}

function SessionDetail({
  session,
  onClose,
}: {
  session: HistorySession;
  onClose: () => void;
}) {
  const detail = useApi<HistorySessionDetail>(detailPath(session.harness, session.id, 0));
  // Later pages, appended in order behind the first one useApi holds.
  const [more, setMore] = useState<AgentEvent[]>([]);
  const [moreLoading, setMoreLoading] = useState(false);
  const [moreError, setMoreError] = useState<ApiError | null>(null);
  // The daemon answered an empty page before the total was reached (the log
  // shrank under us): stop offering more — "N of M" stays on screen, honestly.
  const [ended, setEnded] = useState(false);
  const d = detail.data;
  const events = d ? [...(d.events ?? []), ...more] : [];
  const total = d ? Math.max(d.events_total ?? events.length, events.length) : 0;
  const hasMore = !!d && !ended && events.length < total;

  async function loadMore() {
    if (moreLoading) return;
    setMoreLoading(true);
    setMoreError(null);
    try {
      const page = await get<HistorySessionDetail>(
        detailPath(session.harness, session.id, events.length),
      );
      const got = page?.events ?? [];
      setMore((prev) => [...prev, ...got]);
      if (got.length === 0) setEnded(true);
    } catch (e) {
      setMoreError(e instanceof ApiError ? e : new ApiError(String(e), 0));
    } finally {
      setMoreLoading(false);
    }
  }
  const findings = d?.findings ?? [];
  return (
    <div
      data-testid="history-detail"
      className="min-w-0 rounded-xl border border-white/[0.08] bg-white/[0.02] p-3"
    >
      <div className="flex items-start gap-2">
        <HarnessBadge harness={session.harness} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-zinc-100">
            {session.title || "(untitled)"}
          </div>
          <div className="truncate text-[11px] text-zinc-500" title={session.project}>
            {session.project || "no project"}
          </div>
        </div>
        <button
          type="button"
          aria-label="Close session"
          data-testid="history-detail-close"
          onClick={onClose}
          className="rounded-md p-1 text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200"
        >
          <X size={14} />
        </button>
      </div>

      {detail.error ? (
        <div className="mt-3">
          {detail.error.status === 0 ? (
            <OfflineHint />
          ) : (
            <DataError error={detail.error} what="this session" />
          )}
        </div>
      ) : !d ? (
        <div className="mt-3">
          <SkeletonRows rows={3} />
        </div>
      ) : (
        <>
          <h3 className="mt-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-zinc-500">
            Safety findings
          </h3>
          {d.findings_error ? (
            <p
              data-testid="history-findings-error"
              className="mt-1.5 rounded-lg border border-amber-500/25 bg-amber-500/10 px-2.5 py-1.5 text-[11.5px] text-amber-200"
            >
              The safety checks could not run on this session: {d.findings_error}
            </p>
          ) : findings.length === 0 ? (
            <p data-testid="history-findings-none" className="mt-1.5 text-[12px] text-zinc-500">
              No safety findings — {total.toLocaleString()} event
              {total === 1 ? "" : "s"} checked.
            </p>
          ) : (
            <ul data-testid="history-findings" className="mt-1.5 space-y-2">
              {findings.map((f, i) => (
                <FindingRow key={`${f.rule_id}-${i}`} finding={f} />
              ))}
            </ul>
          )}

          <h3 className="mt-4 text-[11px] font-semibold uppercase tracking-[0.12em] text-zinc-500">
            What happened
            <span data-testid="history-events-count" className="ml-1 normal-case tracking-normal">
              ({events.length === total
                ? `${total.toLocaleString()} events`
                : `${events.length.toLocaleString()} of ${total.toLocaleString()} events`})
            </span>
          </h3>
          {d.session?.truncated ? (
            <p className="mt-1 text-[11px] text-zinc-500">
              This log is very large; only its first part was read.
            </p>
          ) : null}
          {events.length === 0 ? (
            <p className="mt-1.5 text-[12px] text-zinc-500">No events in this session.</p>
          ) : (
            <ul className="mt-1 divide-y divide-white/[0.04]">
              {events.map((e, i) => (
                <EventRow key={`${e.ref ?? ""}-${i}`} e={e} />
              ))}
            </ul>
          )}
          {moreError ? (
            <div className="mt-2">
              {moreError.status === 0 ? (
                <OfflineHint />
              ) : (
                <DataError error={moreError} what="more events" />
              )}
            </div>
          ) : null}
          {hasMore ? (
            <button
              type="button"
              data-testid="history-show-more"
              disabled={moreLoading}
              onClick={loadMore}
              className="mt-2 text-[11.5px] font-medium text-accent-soft hover:underline disabled:opacity-60"
            >
              {moreLoading
                ? "Loading…"
                : `Show more (${events.length.toLocaleString()} of ${total.toLocaleString()} events shown)`}
            </button>
          ) : null}
        </>
      )}
    </div>
  );
}

export function AgentHistoryCard() {
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<HistorySession | null>(null);
  const list = useApi<HistorySessionsResponse>(
    filter === "all" ? "/history/sessions" : `/history/sessions?harness=${filter}`,
  );
  const sessions = (list.data?.sessions ?? []).filter(
    (s) => filter === "all" || s.harness === filter,
  );
  const emptyWho =
    filter === "all" ? "Claude Code or Codex" : (HARNESS_WORDS[filter] ?? filter);

  return (
    <Card
      title="Other agents"
      icon={<Users size={15} />}
      right={
        <div
          role="group"
          aria-label="Which agent"
          className="inline-flex rounded-lg border border-white/10 p-0.5"
        >
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              data-testid={`history-filter-${f.key}`}
              aria-pressed={filter === f.key}
              onClick={() => {
                setFilter(f.key);
                setSelected(null);
              }}
              className={`rounded-md px-2 py-0.5 text-[11px] font-medium transition-colors ${
                filter === f.key
                  ? "bg-white/[0.08] text-zinc-100"
                  : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      }
    >
      <p data-testid="history-subtitle" className="mb-3 text-[12.5px] leading-relaxed text-zinc-500">
        Read-only history of the Claude Code and Codex sessions on this PC, checked against the same
        safety rules. Nothing is changed and nothing is sent anywhere.
      </p>

      {list.error ? (
        list.error.status === 0 ? (
          <OfflineHint />
        ) : (
          <div data-testid="history-error">
            <DataError error={list.error} what="other agents' sessions" />
          </div>
        )
      ) : list.loading && !list.data ? (
        <SkeletonRows rows={3} />
      ) : sessions.length === 0 ? (
        <p data-testid="history-empty" className="py-4 text-center text-sm text-zinc-500">
          No {emptyWho} sessions found on this PC.
        </p>
      ) : (
        <div
          className={
            selected
              ? "grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]"
              : ""
          }
        >
          <ul data-testid="history-list" className="max-h-[32rem] space-y-1 overflow-y-auto">
            {sessions.map((s) => {
              const active = selected?.harness === s.harness && selected?.id === s.id;
              return (
                <li key={`${s.harness}:${s.id}`}>
                  <button
                    type="button"
                    data-testid="history-row"
                    data-harness={s.harness}
                    aria-pressed={active}
                    onClick={() => setSelected(active ? null : s)}
                    className={`w-full rounded-lg border px-3 py-2 text-left transition-colors ${
                      active
                        ? "border-accent/30 bg-accent/[0.06]"
                        : "border-white/[0.06] bg-white/[0.015] hover:border-white/15"
                    }`}
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <HarnessBadge harness={s.harness} />
                      <span className="min-w-0 flex-1 truncate text-[13px] text-zinc-100">
                        {s.title || "(untitled)"}
                      </span>
                      <span className="shrink-0 text-[11px] text-zinc-500">
                        {timeAgo(s.ended || s.started)}
                      </span>
                    </div>
                    <div className="mt-0.5 flex min-w-0 items-center gap-2 text-[11px] text-zinc-500">
                      <span className="min-w-0 flex-1 truncate" title={s.project}>
                        {s.project || "no project"}
                      </span>
                      <span className="shrink-0">
                        {s.events.toLocaleString()} events · {s.tools.toLocaleString()} tools
                        {s.subagents ? (
                          <span data-testid="history-row-subagents">
                            {" "}· {s.subagents.toLocaleString()} subagent
                            {s.subagents === 1 ? "" : "s"}
                            {s.subagent_tools
                              ? ` (${s.subagent_tools.toLocaleString()} tools)`
                              : ""}
                          </span>
                        ) : null}
                      </span>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
          {selected ? (
            <SessionDetail
              key={`${selected.harness}:${selected.id}`}
              session={selected}
              onClose={() => setSelected(null)}
            />
          ) : null}
        </div>
      )}
    </Card>
  );
}
