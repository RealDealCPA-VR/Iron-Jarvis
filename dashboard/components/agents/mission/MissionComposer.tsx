"use client";

// NEW TASK (v1.307.0): one objective for the whole team. The user does not
// pick agents — Jarvis (a supervisor run) splits the work and hands the parts
// out; the right-hand cards show who it chose. A project is optional: picking
// one grounds the run in that project and works in its folder, exactly as a
// project task does (the daemon's create_session seam).
//
// v1.309.0 (UX/speed wave 1):
// * PREFLIGHT. When the default model is KNOWN down (unreachable, signed out,
//   or in the router's cooldown), the door says so above Start, in plain
//   words, with the way to Connections — before the user spends a paragraph
//   on a mission that will fail. Read off the app's ONE shared /health
//   (`useDaemon().health`), never a poller of its own; silent while nothing
//   is known, and softened to "could not check" when the last /health could
//   not reach the daemon (chat's stale rule). It never switches models:
//   Start still sends the objective
//   alone (the never-auto-switch rule — a local default moving to a cloud
//   API is the user's privacy decision).
// * "NEEDS YOU". A recent row whose mission (or a teammate) is parked on an
//   ask says so in amber, sorted first, instead of "Working".

import Link from "next/link";
import { useState } from "react";
import { ArrowUp } from "lucide-react";
import { ApiError, post } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { useDaemon } from "@/lib/daemon";
import type { Health, Project } from "@/lib/types";
import { clock, decodeMissions, type MissionRow } from "@/lib/mission";

const STATUS_WORD: Record<string, string> = {
  active: "Working",
  queued: "Queued",
  completed: "Done",
  failed: "Failed",
  cancelled: "Stopped",
};

/** One sentence when the DEFAULT provider is known not to answer, else "".
 *  Unknown is never a warning: no /health yet, no row for it, or "auto".
 *
 *  `stale` (v1.309.0 review) mirrors chat's PreflightNote: when the last
 *  /health check could not reach the daemon (`!checking && !online`, the
 *  rule `useProviderHealth` uses), the rows are the LAST KNOWN ones — so the
 *  door says it could not check the default model, never that a task "will
 *  fail" on evidence that may be minutes old. */
export function preflightProblem(
  health: Health | null | undefined,
  stale = false,
): { provider: string; text: string } | null {
  const provider = (health?.default_provider ?? "").trim();
  if (!provider || provider === "auto") return null;
  const row = (health?.providers ?? []).find((p) => p && p.provider === provider);
  if (!row) return null;
  const wait = row.circuit?.open ? Math.max(0, Number(row.circuit.retry_in_s) || 0) : 0;
  if (row.available !== false && wait <= 0) return null;
  if (stale) {
    return {
      provider,
      text: `Iron Jarvis could not check your default model, ${provider} — the last check couldn't reach the daemon, and it looked unavailable before that. If a task fails, check it on Connections.`,
    };
  }
  if (row.available === false) {
    if (row.installed && row.signed_in === false) {
      return {
        provider,
        text: `Your default model, ${provider}, is installed but not signed in — a task started now will fail. Sign it in on Connections first.`,
      };
    }
    return {
      provider,
      text: `Your default model, ${provider}, isn't reachable right now — a task started now will fail. Bring it back, or choose another default on Connections.`,
    };
  }
  return {
    provider,
    text: `Your default model, ${provider}, failed repeatedly and is paused for ${wait} s — a task started now is refused. Wait, or choose another default on Connections.`,
  };
}

/** Still open AND parked on an ask (the mission's or a teammate's). */
function parked(r: MissionRow): boolean {
  return r.waiting && (r.status === "active" || r.status === "queued");
}

/** A recent row's status word — "Needs you" beats "Working" for a parked ask. */
function rowWord(r: MissionRow): string {
  return parked(r) ? "Needs you" : (STATUS_WORD[r.status] ?? r.status);
}

export function MissionComposer({
  onStarted,
  onOpen,
  fixedProject = "",
  projectName = "",
}: {
  /** The mission started — open it (inside its project when it has one). */
  onStarted: (id: string, projectId: string) => void;
  onOpen: (id: string, projectId: string) => void;
  /** v1.308.0: a PROJECT's mission screen — the project is not a choice, the
   *  recent list is that project's, and its team does the work. */
  fixedProject?: string;
  projectName?: string;
}) {
  const [objective, setObjective] = useState("");
  const [picked, setPicked] = useState("");
  const projectId = fixedProject || picked;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: projData } = useApi<{ projects: Project[] }>(fixedProject ? null : "/projects");
  const { data: missionData } = useApi<unknown>(
    fixedProject ? `/missions?project_id=${encodeURIComponent(fixedProject)}` : "/missions",
  );
  const projects = (projData?.projects ?? []).filter((p) => p.status !== "archived");
  // Parked missions first (a stable sort keeps newest-first inside each).
  const recent: MissionRow[] = decodeMissions(missionData)
    .map((r, i) => ({ r, i }))
    .sort((a, b) => Number(parked(b.r)) - Number(parked(a.r)) || a.i - b.i)
    .map(({ r }) => r);
  const daemon = useDaemon();
  const preflight = preflightProblem(daemon.health, !daemon.checking && !daemon.online);

  const start = async () => {
    const text = objective.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    try {
      const row = await post<{ id?: string }>("/missions", {
        objective: text,
        ...(projectId ? { project_id: projectId } : {}),
      });
      if (row && typeof row.id === "string" && row.id) onStarted(row.id, projectId);
      else setError("The daemon did not say which mission it started.");
    } catch (e) {
      setError(
        e instanceof ApiError && (e.status === 404 || e.status === 405)
          ? "This daemon is older than the mission screen — restart Iron Jarvis to update it."
          : e instanceof Error
            ? e.message
            : "Could not start the mission.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <section data-testid="mission-composer" className="card-surface px-5 py-5">
        <h1 className="text-lg font-semibold text-zinc-100">
          {fixedProject
            ? `What should the ${projectName || "project"} team get done?`
            : "What should the team get done?"}
        </h1>
        <p className="mt-1 text-[13px] text-zinc-400">
          {fixedProject
            ? "Give Jarvis one objective for this project. It works in the project's context and folder, hands each part to the project's team, and puts the result here."
            : "Give Jarvis one objective. It splits the work, hands each part to the right agent, and puts the result here — you can watch the team work underneath."}
        </p>
        <textarea
          data-testid="mission-input"
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void start();
            }
          }}
          rows={4}
          placeholder="e.g. Research our three main competitors and write a market strategy report"
          className="field mt-4 w-full resize-y text-[14px]"
          aria-label="Objective"
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          {!fixedProject && (
            <label className="flex items-center gap-2 text-[12px] text-zinc-400">
              Project
              <select
                data-testid="mission-project"
                value={picked}
                onChange={(e) => setPicked(e.target.value)}
                className="field py-1 text-[12px]"
              >
                <option value="">None</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {preflight && (
            <p
              data-testid="mission-preflight"
              role="status"
              className="basis-full rounded-lg border border-amber-400/30 bg-amber-300/10 px-3 py-2 text-[12.5px] text-amber-100"
            >
              {preflight.text}{" "}
              <Link href="/connections" className="font-medium text-amber-200 underline hover:text-amber-100">
                Open Connections
              </Link>
            </p>
          )}
          <button
            type="button"
            data-testid="mission-start"
            disabled={busy || !objective.trim()}
            onClick={() => void start()}
            className="btn-accent ml-auto"
          >
            <ArrowUp size={15} /> {busy ? "Starting…" : "Start"}
          </button>
        </div>
        {error && (
          <p data-testid="mission-start-error" className="mt-2 text-[12px] text-rose-300">
            {error}
          </p>
        )}
      </section>

      {recent.length > 0 && (
        <section data-testid="mission-recent" className="card-surface p-0">
          <header className="border-b hairline px-4 py-2.5 text-[13px] font-semibold text-zinc-200">
            {fixedProject ? "This project's objectives" : "Recent objectives"}
          </header>
          <ul className="divide-y divide-white/5">
            {recent.slice(0, 8).map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  onClick={() => onOpen(r.id, r.project_id ?? "")}
                  className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-white/[0.03]"
                >
                  <span className="min-w-0 flex-1 truncate text-[13px] text-zinc-200">{r.objective}</span>
                  <span className="shrink-0 text-[11px] text-zinc-500">{clock(r.created_at)}</span>
                  <span
                    className={`w-16 shrink-0 text-right text-[11px] ${
                      parked(r) ? "font-medium text-amber-300" : "text-zinc-400"
                    }`}
                  >
                    {rowWord(r)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
