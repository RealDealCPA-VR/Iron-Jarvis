"use client";

// NEW TASK (v1.307.0): one objective for the whole team. The user does not
// pick agents — Jarvis (a supervisor run) splits the work and hands the parts
// out; the right-hand cards show who it chose. A project is optional: picking
// one grounds the run in that project and works in its folder, exactly as a
// project task does (the daemon's create_session seam).

import { useState } from "react";
import { ArrowUp } from "lucide-react";
import { ApiError, post } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import type { Project } from "@/lib/types";
import { clock, decodeMissions, type MissionRow } from "@/lib/mission";

const STATUS_WORD: Record<string, string> = {
  active: "Working",
  queued: "Queued",
  completed: "Done",
  failed: "Failed",
  cancelled: "Stopped",
};

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
  const recent: MissionRow[] = decodeMissions(missionData);

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
                  <span className="w-16 shrink-0 text-right text-[11px] text-zinc-400">
                    {STATUS_WORD[r.status] ?? r.status}
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
