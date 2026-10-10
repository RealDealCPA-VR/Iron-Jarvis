"use client";

/**
 * The reflection COACH (v1.297.0) — it reads an agent's recent runs and
 * proposes a change to its instructions, with the diff.
 *
 * `GET /agents/{name}/coach` is the report: the last runs scored (outcome,
 * failed tools, denials, unanswered asks, step caps, thumbs-down), the
 * recurring problems clustered by category, and every proposal so far.
 * "Ask the coach" (`POST /agents/{name}/coach`) asks for one more — the
 * daemon answers with a proposal, or with the sentence saying why not
 * ("nothing recurring in the last 10 runs"). A PENDING proposal is a card:
 * the rationale, the before → after line diff, Accept / Decline
 * (`POST /coach/proposals/{id}/accept|decline`). Accept can 409 when the
 * instructions changed since the proposal was written — the sentence is
 * shown and the view refetched, so the stale card leaves honestly.
 *
 * Refetches the moment a `coach.proposal` event lands for this agent.
 *
 * An OLDER DAEMON has no coach route: the GET 404s and the panel renders
 * nothing.
 */

import { useEffect, useRef, useState } from "react";
import { GraduationCap } from "lucide-react";
import { ApiError, post } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { useEvents } from "@/lib/useEvents";
import { diffTexts } from "@/lib/diff";
import type { AgentCoachView, CoachAskResult, CoachCluster, CoachProposal, CoachRun } from "@/lib/types";
import { ErrorNote, LoaderInline } from "@/components/ui";
import { DiffBlock } from "./AgentFiles";
import { GHOST_BASE, GHOST_BTN, SECTION_LABEL, primaryBtn } from "./teamLook";

/** What each cluster category means, as the chip's title. */
export const CLUSTER_DEFINITIONS: Record<string, string> = {
  "tool-misuse": "A tool was called wrongly or failed repeatedly",
  denials: "The agent asked for something that was denied",
  "unanswered-asks": "It asked the user and never got an answer",
  "step-cap": "It ran out of steps before finishing",
  "down-feedback": "Its replies got a thumbs-down",
  failures: "Runs that ended in an error",
  "needs-you": "Runs that stopped waiting on the user",
  slow: "Runs that took much longer than its others",
};

/** The chip's title: the DAEMON's definition when the report carries its
 *  taxonomy (`report.categories`, the fixed eight — the UI never invents
 *  one), else the local table, else the category word itself. */
export function clusterTitle(category: string, definitions?: Record<string, string> | null): string {
  const fromDaemon = definitions?.[category];
  if (typeof fromDaemon === "string" && fromDaemon.trim()) return fromDaemon;
  return CLUSTER_DEFINITIONS[category] ?? CLUSTER_DEFINITIONS[category.replace(/_/g, "-")] ?? category;
}

/**
 * "Last 10 runs: 7 completed · 2 needs you · 1 failed" — outcomes counted in
 * order of first appearance, each spelled with spaces. Empty: "No runs yet".
 */
export function coachSummary(runs: CoachRun[]): string {
  if (!runs || runs.length === 0) return "No runs yet";
  const counts = new Map<string, number>();
  for (const r of runs) {
    const key = String(r.outcome ?? "unknown").toLowerCase().replace(/_/g, " ");
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const parts = [...counts.entries()].map(([k, n]) => `${n} ${k}`);
  return `Last ${runs.length} run${runs.length === 1 ? "" : "s"}: ${parts.join(" · ")}`;
}

/** The clusters worth a chip: by weight, then count; at most `max`. */
export function topClusters(clusters: CoachCluster[], max = 4): CoachCluster[] {
  return [...(clusters ?? [])]
    .filter((c) => c.count > 0)
    .sort((a, b) => b.weight - a.weight || b.count - a.count)
    .slice(0, max);
}

function errText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

/* -------------------------------------------------------------- proposal --- */

function ProposalCard({
  proposal: p,
  onChanged,
}: {
  proposal: CoachProposal;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<"accept" | "decline" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function decide(kind: "accept" | "decline") {
    if (busy) return;
    setBusy(kind);
    setError(null);
    try {
      await post<{ proposal: CoachProposal }>(
        `/coach/proposals/${encodeURIComponent(p.id)}/${kind}`,
      );
      onChanged();
    } catch (err) {
      // 409: the instructions moved since this was written. Say the daemon's
      // sentence AND refetch — the proposal is now stale and the list says so.
      setError(errText(err));
      if (err instanceof ApiError && err.status === 409) onChanged();
    } finally {
      setBusy(null);
    }
  }

  const lines = diffTexts(p.before, p.after);
  const added = lines.filter((l) => l.kind === "added").length;
  const removed = lines.filter((l) => l.kind === "removed").length;

  // v1.329.0 (calm chat wave 9, K4): a proposal is a plain row under the
  // list's hairline, not an accent box. Accept is the row's one primary.
  return (
    <li data-testid={`coach-proposal-${p.id}`} className="space-y-2 px-1 py-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-zinc-500">
        <span className="text-[12px] font-medium text-accent-soft">{p.kind || "change"}</span>
        <span className="min-w-0 truncate font-mono" title={p.target}>
          {p.target}
        </span>
        <span className="ml-auto shrink-0 tabular-nums">
          <span className="text-tone-success">+{added}</span>{" "}
          <span className="text-tone-danger">−{removed}</span>
        </span>
      </div>
      <p className="text-[13px] leading-relaxed text-zinc-200">{p.rationale}</p>
      <DiffBlock lines={lines} testId={`coach-diff-${p.id}`} />
      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          data-testid={`coach-decline-${p.id}`}
          onClick={() => void decide("decline")}
          disabled={busy !== null}
          className={`${GHOST_BASE} hover:text-tone-danger`}
        >
          {busy === "decline" ? "Declining…" : "Decline"}
        </button>
        <button
          type="button"
          data-testid={`coach-accept-${p.id}`}
          onClick={() => void decide("accept")}
          disabled={busy !== null}
          className={primaryBtn(busy === null)}
        >
          {busy === "accept" ? "Accepting…" : "Accept"}
        </button>
      </div>
      {error && <p className="text-[12px] text-tone-danger">{error}</p>}
    </li>
  );
}

/* ----------------------------------------------------------------- panel --- */

/** `<AgentCoach name="analyst" />` — the BARE slug of a custom agent. */
export function AgentCoach({ name }: { name: string }) {
  const path = `/agents/${encodeURIComponent(name)}/coach`;
  const { data, error, reload } = useApi<AgentCoachView>(path);
  const [askBusy, setAskBusy] = useState(false);
  const [askReason, setAskReason] = useState<string | null>(null);
  const [askError, setAskError] = useState<string | null>(null);

  // Refetch on the first unseen coach.proposal event for this agent (the
  // payload names the bare slug; a roster-shaped "custom:<slug>" counts too).
  const { events } = useEvents(60);
  const seenRef = useRef<string | null>(null);
  useEffect(() => {
    const hit = events.find((e) => {
      if (e.type !== "coach.proposal") return false;
      const agent = e.payload?.agent;
      return agent === name || agent === `custom:${name}`;
    });
    if (!hit || hit.id === seenRef.current) return;
    seenRef.current = hit.id;
    reload();
  }, [events, name, reload]);

  // An older daemon (404) — or no answer yet — draws nothing at all.
  if (error && error.status === 404) return null;
  if (!data) return null;

  const runs = data.report?.runs ?? [];
  const clusters = topClusters(data.report?.clusters ?? []);
  const pending = (data.proposals ?? []).filter((p) => p.status === "pending");

  async function ask() {
    if (askBusy) return;
    setAskBusy(true);
    setAskReason(null);
    setAskError(null);
    try {
      const res = await post<CoachAskResult>(path);
      if (res.proposal) reload();
      else setAskReason(res.reason || "The coach has nothing to suggest right now.");
    } catch (err) {
      setAskError(errText(err));
    } finally {
      setAskBusy(false);
    }
  }

  return (
    // v1.329.0: a plain section under a hairline with a quiet label; "Ask the
    // coach" is a ghost (a proposal's Accept is the one primary).
    <section data-testid={`coach-${name}`} className="space-y-3 border-t hairline pt-4">
      <div className="flex items-center gap-2 px-1">
        <h3 className={SECTION_LABEL}>Coach</h3>
        <button
          type="button"
          data-testid={`coach-ask-${name}`}
          onClick={() => void ask()}
          disabled={askBusy}
          className={`${GHOST_BTN} ml-auto`}
        >
          {askBusy ? (
            <LoaderInline label="Thinking…" />
          ) : (
            <>
              <GraduationCap size={13} aria-hidden /> Ask the coach
            </>
          )}
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-1">
        <p data-testid={`coach-summary-${name}`} className="text-[12px] text-zinc-400">
          {coachSummary(runs)}
        </p>
        {clusters.map((c) => (
          <span
            key={c.category}
            data-testid={`coach-cluster-${c.category}`}
            title={clusterTitle(c.category, data.report?.categories)}
            className="rounded-md bg-tone-warn/10 px-1.5 py-px text-[11px] font-medium text-tone-warn"
          >
            {c.category} ×{c.count}
          </span>
        ))}
      </div>

      {askReason && (
        <p data-testid={`coach-reason-${name}`} className="px-1 text-[12px] leading-relaxed text-zinc-500">
          {askReason}
        </p>
      )}
      {askError && <ErrorNote>{askError}</ErrorNote>}

      {pending.length > 0 ? (
        <ul className="divide-y divide-white/[0.06] border-t hairline">
          {pending.map((p) => (
            <ProposalCard key={p.id} proposal={p} onChanged={reload} />
          ))}
        </ul>
      ) : (
        !askReason && (
          <p className="px-1 text-[12px] leading-relaxed text-zinc-500">
            {data.last_reason
              ? data.last_reason
              : `Nothing pending. Ask the coach after ${name} has run a few times.`}
          </p>
        )
      )}
    </section>
  );
}

export default AgentCoach;
