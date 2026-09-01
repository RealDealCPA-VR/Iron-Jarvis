"use client";

// The user's SAVED workflows, listed on the Workflows page where they can see
// them — with Load and Delete on every row.
//
// Before v1.222.0 the only way to delete a saved workflow was a trash icon
// that appeared on HOVER inside the canvas's Load ▾ dropdown. The route and
// the handler existed and were tested; the user's own report was that they
// could not delete a workflow. Nothing on the page said the list existed, and
// an icon at opacity 0 inside a closed dropdown is not a feature anyone who
// does nothing differently will find (the v1.218.0 lesson).
//
// Delete is HONEST about what still names the workflow: schedules and reflex
// rules fire it by name, and after the delete they fail with "no saved
// workflow" until re-pointed. The confirm step asks the daemon
// (GET /workflows/{name}/references) and shows that list before the user
// commits; when the check itself fails it says so instead of claiming
// "nothing uses this".

import { useCallback, useEffect, useState } from "react";
import { FolderOpen, Trash2, Workflow, X } from "lucide-react";
import { useApi } from "@/lib/useApi";
import { del, get, ApiError } from "@/lib/api";
import { Card, Empty, ErrorNote, LoaderInline, SkeletonRows } from "@/components/ui";

/** One row of GET /workflows (project_id is omitted by the list route). */
export interface SavedWorkflowDef {
  id?: string;
  name: string;
  description?: string;
  steps_json?: string;
  created_at?: string;
}

/** GET /workflows/{name}/references — automations that fire this def by name. */
export interface WorkflowReference {
  kind: "schedule" | "reflex" | string;
  name: string;
  id?: string;
  enabled?: boolean;
}

/** Window event fired whenever the saved-workflow LIST changes (a save, a
 *  rename, a delete) — from this list or from the canvas. Listeners refresh
 *  their copy; `deleted` names a row that no longer exists so an editor that
 *  was editing it stops treating it as saved. Distinct from
 *  `ij:workflow-changed`, which means "the canvas's LOADED workflow changed"
 *  and carries steps for the builder chat. */
export const WORKFLOWS_LIST_EVENT = "ij:workflows-list-changed";

export interface WorkflowsListDetail {
  saved?: string;
  deleted?: string;
}

export function announceWorkflowsChanged(detail: WorkflowsListDetail): void {
  try {
    window.dispatchEvent(new CustomEvent(WORKFLOWS_LIST_EVENT, { detail }));
  } catch {
    /* no window (SSR) — nothing to tell */
  }
}

/** Step count of a saved def; a malformed or absent steps_json is 0 steps,
 *  never a crash (agent-authored rows have been odd before). */
export function savedStepCount(stepsJson: string | undefined | null): number {
  if (!stepsJson) return 0;
  try {
    const parsed = JSON.parse(stepsJson) as unknown;
    return Array.isArray(parsed) ? parsed.length : 0;
  } catch {
    return 0;
  }
}

/** One line naming what still fires the workflow, for the confirm step. */
export function referencesSentence(refs: WorkflowReference[]): string {
  if (refs.length === 0) return "";
  const parts = refs.map((r) =>
    r.kind === "schedule"
      ? `schedule “${r.name}”`
      : r.kind === "reflex"
        ? `reflex rule “${r.name || r.id || "unnamed"}”`
        : `${r.kind} “${r.name}”`,
  );
  return `Still used by ${parts.join(", ")} — ${
    refs.length === 1 ? "it" : "they"
  } will fail with “no saved workflow” until re-pointed.`;
}

function errText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

export function SavedWorkflows() {
  const { data, error, loading, reload } = useApi<{ workflows: SavedWorkflowDef[] }>(
    "/workflows",
  );
  const rows = Array.isArray(data?.workflows) ? data!.workflows : [];

  // Which row is in its confirm step, what the daemon said still uses it
  // (null = not asked yet / could not ask), and whether the delete is in flight.
  const [pending, setPending] = useState<string | null>(null);
  const [refs, setRefs] = useState<WorkflowReference[] | null>(null);
  const [refsFailed, setRefsFailed] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Stay in step with the canvas: a Save/rename/delete there changes this list.
  useEffect(() => {
    const onChanged = () => reload();
    window.addEventListener(WORKFLOWS_LIST_EVENT, onChanged);
    return () => window.removeEventListener(WORKFLOWS_LIST_EVENT, onChanged);
  }, [reload]);

  const load = useCallback((d: SavedWorkflowDef) => {
    // Same event + shape the canvas's Load ▾ path and the starters use; the
    // canvas tracks it as the LOADED def (rename-in-place, pin-preserving run).
    window.dispatchEvent(
      new CustomEvent("ij:load-workflow", {
        detail: {
          name: d.name,
          description: d.description ?? "",
          steps_json: typeof d.steps_json === "string" ? d.steps_json : "[]",
        },
      }),
    );
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const askDelete = useCallback(async (name: string) => {
    setErr(null);
    setNotice(null);
    setPending(name);
    setRefs(null);
    setRefsFailed(false);
    try {
      const r = await get<{ references: WorkflowReference[] }>(
        `/workflows/${encodeURIComponent(name)}/references`,
      );
      setRefs(Array.isArray(r.references) ? r.references : []);
    } catch {
      // Unknown is not "nothing": the confirm step says the check failed.
      setRefsFailed(true);
    }
  }, []);

  const cancelDelete = useCallback(() => {
    setPending(null);
    setRefs(null);
    setRefsFailed(false);
  }, []);

  const confirmDelete = useCallback(
    async (name: string) => {
      setDeleting(name);
      setErr(null);
      try {
        await del(`/workflows/${encodeURIComponent(name)}`);
        setNotice(`Deleted “${name}”.`);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 404)) {
          setErr(errText(e));
          setDeleting(null);
          return;
        }
        // Already gone — the list was stale; refreshing is the whole fix.
        setNotice(`“${name}” was already deleted.`);
      }
      // The canvas owns the saved layout for this name and forgets it when it
      // hears the `deleted` announcement below.
      setDeleting(null);
      setPending(null);
      setRefs(null);
      setRefsFailed(false);
      reload();
      announceWorkflowsChanged({ deleted: name });
    },
    [reload],
  );

  const offline = error && error.status === 0;

  return (
    <Card
      title={rows.length ? `Saved workflows · ${rows.length}` : "Saved workflows"}
      icon={<Workflow size={15} />}
    >
      {loading && !data ? (
        <SkeletonRows rows={3} />
      ) : !data ? (
        // No response in hand means UNKNOWN — never "you have none".
        <p className="py-2 text-sm text-zinc-500">
          {offline
            ? "Saved workflows unavailable — the daemon looks offline."
            : `Saved workflows unavailable — the daemon returned an error (HTTP ${error?.status ?? "?"}).`}
        </p>
      ) : rows.length === 0 ? (
        <Empty icon={<Workflow size={22} />}>
          {/* Worded apart from the Templates card's "No saved workflows yet"
              line, which shows at the same moment — two identical sentences
              on one page read as a glitch. */}
          Nothing saved yet — build one in the editor above and press Save, or
          start from a template below.
        </Empty>
      ) : (
        <ul className="space-y-1.5" data-testid="saved-workflows">
          {rows.map((d) => {
            const n = savedStepCount(d.steps_json);
            const confirming = pending === d.name;
            return (
              <li
                key={d.id ?? d.name}
                className="rounded-lg border border-white/[0.05] bg-white/[0.02] px-3 py-2"
              >
                <div className="flex items-center gap-2">
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md border border-accent/30 bg-accent/10 text-accent-soft">
                    <Workflow size={13} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] font-medium text-zinc-100">
                      {d.name}
                    </div>
                    <div className="truncate text-[11px] text-zinc-500">
                      {n} step{n === 1 ? "" : "s"}
                      {d.description ? ` · ${d.description}` : ""}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => load(d)}
                    disabled={confirming}
                    title={`Load “${d.name}” into the editor`}
                    className="btn-ghost !px-2.5 !py-1 text-xs"
                  >
                    <FolderOpen size={13} /> Load
                  </button>
                  {!confirming && (
                    <button
                      type="button"
                      onClick={() => void askDelete(d.name)}
                      aria-label={`Delete ${d.name}`}
                      title={`Delete “${d.name}”`}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-xs font-medium text-zinc-400 transition-colors hover:border-rose-500/40 hover:text-rose-300"
                    >
                      <Trash2 size={13} /> Delete
                    </button>
                  )}
                </div>
                {confirming && (
                  <div
                    className="mt-2 space-y-2 rounded-md border border-rose-500/25 bg-rose-500/[0.06] px-3 py-2"
                    data-testid={`confirm-delete-${d.name}`}
                  >
                    <p className="text-xs text-zinc-200">
                      Delete “{d.name}”? This can’t be undone.
                    </p>
                    {refs === null && !refsFailed ? (
                      <p className="text-[11px] text-zinc-500">
                        <LoaderInline label="Checking what uses it…" />
                      </p>
                    ) : refsFailed ? (
                      <p className="text-[11px] text-amber-200">
                        Couldn’t check whether a schedule or reflex rule still uses
                        it — if one does, it will fail until re-pointed.
                      </p>
                    ) : refs && refs.length > 0 ? (
                      <p className="text-[11px] text-amber-200">{referencesSentence(refs)}</p>
                    ) : (
                      <p className="text-[11px] text-zinc-500">
                        Nothing scheduled or automated uses it.
                      </p>
                    )}
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => void confirmDelete(d.name)}
                        disabled={deleting === d.name}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-rose-500/40 bg-rose-500/[0.12] px-2.5 py-1 text-xs font-medium text-rose-200 transition-colors hover:bg-rose-500/[0.2] disabled:opacity-50"
                      >
                        {deleting === d.name ? (
                          <LoaderInline label="Deleting…" />
                        ) : (
                          <>
                            <Trash2 size={13} /> Delete workflow
                          </>
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={cancelDelete}
                        disabled={deleting === d.name}
                        className="btn-ghost !px-2.5 !py-1 text-xs"
                      >
                        <X size={13} /> Cancel
                      </button>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {notice && <p className="mt-2 text-[11px] text-emerald-300">{notice}</p>}
      {err && (
        <div className="mt-2">
          <ErrorNote>{err}</ErrorNote>
        </div>
      )}
    </Card>
  );
}
