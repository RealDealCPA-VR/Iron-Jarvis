"use client";

/**
 * The skill CURATOR (v1.297.0) — the shelf-keeper for skills agents and
 * proposals write.
 *
 * Agents can now mint skills mid-run and the learning loop lands accepted
 * proposals as skills; left alone, the catalog silts up with one-offs. The
 * daemon's curator (`GET /skills/curator`) names the CANDIDATES its next
 * sweep would archive (unpinned, idle past the settings' thresholds, with
 * the reason), what is already ARCHIVED, and the sweep's settings. Here the
 * user can pin a candidate (never swept), archive it now, restore an
 * archived one, or run a sweep — DRY RUN by default: the result sentence
 * says what WOULD go, and nothing moves until the box is unticked.
 *
 * Collapsed by default to one line: "Curator — N candidates · M archived ·
 * last sweep …". Hidden entirely when the daemon has no curator (the page
 * passes no view).
 */

import { useState } from "react";
import { Archive, ChevronDown, ChevronRight, Pin, Sparkles } from "lucide-react";
import { ApiError, post } from "@/lib/api";
import type { SkillCuratorRunResult, SkillCuratorView } from "@/lib/types";
import { ErrorNote, SuccessNote, LoaderInline } from "@/components/ui";
import { timeAgo } from "@/lib/format";

function errText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

/** The collapsed header line. The status fields sit FLAT on the view — the
 *  daemon's `overview()` is its `status()` with the lists in place of the
 *  counts; a nested `status` object never arrives. */
export function curatorHeadline(v: SkillCuratorView): string {
  const c = v.candidates?.length ?? 0;
  const a = v.archived?.length ?? 0;
  const sweep = v.last_sweep_at ? `last sweep ${timeAgo(v.last_sweep_at)}` : "never swept";
  return `Curator — ${c} candidate${c === 1 ? "" : "s"} · ${a} archived · ${sweep}`;
}

/** The sentence a sweep's answer reads as, dry or real. A dry run moves
 *  nothing, so its `archived` is `[]` by contract — what it WOULD move is
 *  `would_archive`. */
export function sweepSentence(r: SkillCuratorRunResult, dryRun: boolean): string {
  const kept = r.kept?.length ?? 0;
  if (dryRun) {
    const would = r.would_archive ?? [];
    const n = would.length;
    const names = n > 0 ? ` (${would.join(", ")})` : "";
    return n === 0
      ? `Dry run: nothing would be archived; ${kept} kept.`
      : `Dry run: would archive ${n} skill${n === 1 ? "" : "s"}${names} and keep ${kept}.`;
  }
  const n = r.archived?.length ?? 0;
  const names = n > 0 ? ` (${r.archived.join(", ")})` : "";
  const backup = r.backup ? ` Backup at ${r.backup}.` : "";
  return n === 0
    ? `Nothing archived; ${kept} kept.${backup}`
    : `Archived ${n} skill${n === 1 ? "" : "s"}${names}; ${kept} kept.${backup}`;
}

/** Settings as "key: value" pairs, nested values flattened to JSON. */
export function settingPairs(settings: Record<string, unknown> | null | undefined): Array<[string, string]> {
  return Object.entries(settings ?? {}).map(([k, v]) => [
    k.replace(/_/g, " "),
    typeof v === "string" ? v : JSON.stringify(v),
  ]);
}

const BTN = "rounded-md border px-1.5 py-0.5 text-[10.5px] font-medium transition-colors disabled:opacity-50";
const BTN_QUIET = `${BTN} border-white/10 text-zinc-400 hover:border-accent/40 hover:text-accent-soft`;

export function SkillCurator({
  view,
  onRefresh,
  onSkillsChanged,
}: {
  view: SkillCuratorView;
  /** Refetch the curator view after an action. */
  onRefresh: () => void;
  /** Refetch the skills list — a pin, archive or restore changes its rows. */
  onSkillsChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [dryRun, setDryRun] = useState(true);
  const [sweepBusy, setSweepBusy] = useState(false);
  const [sweepResult, setSweepResult] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function sweep() {
    if (sweepBusy) return;
    setSweepBusy(true);
    setError(null);
    setSweepResult(null);
    try {
      const r = await post<SkillCuratorRunResult>("/skills/curator/run", { dry_run: dryRun });
      setSweepResult(sweepSentence(r, dryRun));
      if (!dryRun) {
        onRefresh();
        onSkillsChanged();
      }
    } catch (err) {
      setError(errText(err));
    } finally {
      setSweepBusy(false);
    }
  }

  async function act(name: string, kind: "pin" | "archive" | "restore") {
    const key = `${kind}:${name}`;
    if (busy) return;
    if (kind === "archive" && armed !== key) {
      setArmed(key);
      return;
    }
    setBusy(key);
    setArmed(null);
    setError(null);
    try {
      await post(`/skills/curator/${encodeURIComponent(name)}/${kind}`);
      onRefresh();
      onSkillsChanged();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(null);
    }
  }

  const candidates = view.candidates ?? [];
  const archived = view.archived ?? [];
  const settings = settingPairs(view.settings);

  return (
    <section
      data-testid="curator-panel"
      className="rounded-2xl border border-white/[0.07] bg-white/[0.02]"
    >
      <button
        type="button"
        data-testid="curator-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.03]"
      >
        {open ? (
          <ChevronDown size={13} className="shrink-0 text-zinc-500" aria-hidden />
        ) : (
          <ChevronRight size={13} className="shrink-0 text-zinc-500" aria-hidden />
        )}
        <Sparkles size={13} className="shrink-0 text-accent-soft/80" aria-hidden />
        <span className="min-w-0 flex-1 truncate">{curatorHeadline(view)}</span>
        {view.enabled === false && (
          <span className="shrink-0 rounded-md border border-zinc-500/25 bg-zinc-500/10 px-1.5 py-px text-[10px] text-zinc-400">
            sweeps off
          </span>
        )}
      </button>

      {open && (
        <div className="space-y-4 border-t border-white/[0.06] px-3.5 py-3">
          {/* Sweep */}
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              data-testid="curator-sweep"
              onClick={() => void sweep()}
              disabled={sweepBusy}
              className="btn-ghost py-1 text-[11.5px]"
            >
              {sweepBusy ? <LoaderInline label="Sweeping…" /> : "Sweep now"}
            </button>
            <label className="flex items-center gap-1.5 text-[11.5px] text-zinc-400">
              <input
                type="checkbox"
                data-testid="curator-dry-run"
                checked={dryRun}
                onChange={(e) => setDryRun(e.target.checked)}
              />
              Dry run — only say what would be archived
            </label>
          </div>
          {sweepResult && (
            <div data-testid="curator-sweep-result">
              <SuccessNote>{sweepResult}</SuccessNote>
            </div>
          )}
          {error && <ErrorNote>{error}</ErrorNote>}

          {/* Candidates */}
          <div className="space-y-1">
            <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
              Candidates · {candidates.length}
            </div>
            {candidates.length === 0 ? (
              <p className="text-[11.5px] text-zinc-500">Nothing qualifies for the next sweep.</p>
            ) : (
              <ul className="space-y-0.5">
                {candidates.map((c) => {
                  const archiveKey = `archive:${c.name}`;
                  return (
                    <li
                      key={c.name}
                      data-testid={`curator-candidate-${c.name}`}
                      className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg px-1.5 py-1 text-[11.5px] hover:bg-white/[0.03]"
                    >
                      <span className="min-w-0 truncate font-medium text-zinc-200">{c.name}</span>
                      <span className="shrink-0 text-[10px] text-zinc-600">{c.created_by}</span>
                      <span className="min-w-0 flex-1 truncate text-zinc-500" title={c.reason}>
                        {c.reason}
                      </span>
                      <span className="shrink-0 text-[10.5px] tabular-nums text-zinc-600">
                        {c.use_count} use{c.use_count === 1 ? "" : "s"} · idle {c.idle_days}d
                      </span>
                      <button
                        type="button"
                        data-testid={`curator-pin-${c.name}`}
                        onClick={() => void act(c.name, "pin")}
                        disabled={busy !== null}
                        className={BTN_QUIET}
                      >
                        <Pin size={10} className="mr-1 inline-block" aria-hidden /> Pin
                      </button>
                      <button
                        type="button"
                        data-testid={`curator-archive-${c.name}`}
                        onClick={() => void act(c.name, "archive")}
                        disabled={busy !== null}
                        className={`${BTN} ${
                          armed === archiveKey
                            ? "border-rose-500/50 bg-rose-500/15 text-rose-200"
                            : "border-white/10 text-zinc-400 hover:border-rose-500/30 hover:text-rose-300"
                        }`}
                      >
                        {busy === archiveKey
                          ? "Archiving…"
                          : armed === archiveKey
                            ? "Archive it?"
                            : "Archive now"}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {/* Archived */}
          <div className="space-y-1">
            <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
              Archived · {archived.length}
            </div>
            {archived.length === 0 ? (
              <p className="text-[11.5px] text-zinc-500">The archive is empty.</p>
            ) : (
              <ul className="space-y-0.5">
                {archived.map((a) => (
                  <li
                    key={a.name}
                    data-testid={`curator-archived-${a.name}`}
                    className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg px-1.5 py-1 text-[11.5px] hover:bg-white/[0.03]"
                  >
                    <Archive size={11} className="shrink-0 text-zinc-600" aria-hidden />
                    <span className="min-w-0 truncate font-medium text-zinc-300">{a.name}</span>
                    <span className="min-w-0 flex-1 truncate text-zinc-500" title={a.description}>
                      {a.description}
                    </span>
                    <span className="shrink-0 text-[10.5px] tabular-nums text-zinc-600" title={a.archived_at}>
                      {timeAgo(a.archived_at)}
                    </span>
                    <button
                      type="button"
                      data-testid={`curator-restore-${a.name}`}
                      onClick={() => void act(a.name, "restore")}
                      disabled={busy !== null}
                      className={`${BTN} border-emerald-500/25 text-emerald-300 hover:bg-emerald-500/[0.1]`}
                    >
                      {busy === `restore:${a.name}` ? "Restoring…" : "Restore"}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Settings, read-only */}
          {settings.length > 0 && (
            <div className="space-y-1">
              <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                Settings
              </div>
              <dl className="flex flex-wrap gap-1.5">
                {settings.map(([k, v]) => (
                  <div
                    key={k}
                    className="rounded-md border border-white/10 px-1.5 py-px text-[10.5px] text-zinc-400"
                  >
                    <dt className="inline text-zinc-500">{k}: </dt>
                    <dd className="inline font-mono text-zinc-300">{v}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export default SkillCurator;
