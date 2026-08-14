"use client";

// The unified memory surface: Working / What I've learned / Long-term scopes
// on one page. `?scope=` picks the tab (working default | lessons | longterm).
//
// Memory housekeeping (v1.143.0) sits BELOW the scopes because it is about the
// memory rather than a slice of it — and because it is a queue, not a store:
// most days it is empty, and on an older daemon (no /memory/review) it renders
// nothing at all. It mounts here rather than inside MemorySurface so the
// /lessons and /ltm wrappers keep their single-scope focus.
//
// Base availability (v1.173.0) mounts here for the same reason and one more:
// a base that cannot be READ is not a fact about the long-term tab, it is a
// fact about everything this page claims to remember. The daemon does the
// judging (`/ltm/sources` -> `bases[]`); this only renders it, and it renders
// three states, never two — a base whose availability is UNKNOWN stays grey.
// Painting an unchecked base green is the whole failure this feature exists to
// stop, and painting it red would send the user hunting a bug that isn't there.
//
// It mounts on THIS page only, and that is a deliberate call, not an oversight:
// /ltm and /lessons are single-scope deep-link wrappers kept alive so old links
// keep working (they render `<MemorySurface initialScope=…/>` and nothing
// else), while /memory — including /memory?scope=longterm, where the Long-term
// tab actually lives — is the canonical surface. The card is also the only
// caller that asks the daemon to PROBE (`probe=true`), so mounting it in a
// second place would double the network checks per visit. If the wrappers ever
// stop being wrappers, this card moves into MemorySurface rather than being
// copied.
import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Database, RefreshCw } from "lucide-react";
import { get } from "@/lib/api";
import { Card } from "@/components/ui";
import { MemorySurface } from "@/components/memory/MemorySurface";
import { MemoryReview } from "@/components/memory/MemoryReview";

/** One row of `/ltm/sources` -> `bases[]`. `available` is deliberately
 *  THREE-valued: true (reachable), false (checked and it isn't), null/absent
 *  (not known — an older daemon, a remote kind with no cheap probe, or a
 *  check that timed out). */
interface MemoryBase {
  name: string;
  kind?: string;
  available?: boolean | null;
  detail?: string;
  /** Where the base lives — a folder, a url, or the command that starts it. */
  path?: string;
}

interface BaseTone {
  label: string;
  dot: string;
  pill: string;
}

/** STRICT comparisons on purpose: `available` is a tri-state, and a truthy /
 *  falsy test would fold "not checked" (null, undefined) into "unavailable". */
function baseTone(available: boolean | null | undefined): BaseTone {
  if (available === true) {
    return {
      label: "Reachable",
      dot: "bg-emerald-400",
      pill: "border-emerald-500/25 bg-emerald-500/10 text-emerald-300",
    };
  }
  if (available === false) {
    return {
      label: "Unavailable",
      dot: "bg-amber-400",
      pill: "border-amber-500/25 bg-amber-500/10 text-amber-300",
    };
  }
  return {
    label: "Not checked",
    dot: "bg-zinc-500",
    pill: "border-white/[0.07] bg-white/[0.03] text-zinc-400",
  };
}

/**
 * The availability strip. Absent entirely when the daemon does not answer with
 * `bases` (pre-v1.172.0, or unreachable): an empty box advertising a check
 * that never ran is worse than no box.
 *
 * NOT exported, and neither is anything else here: an App Router page file may
 * only export `default` (plus Next's own reserved fields). `next build`
 * type-checks the module against that list — `.next/types/app/memory/page.ts`
 * runs `checkFields<Diff<{default, metadata, …}, TEntry>>` — so one extra named
 * export fails the build, not a lint. The test mounts the PAGE instead, which
 * also pins that this card is actually wired into it.
 */
function MemoryBaseHealth() {
  const [bases, setBases] = useState<MemoryBase[] | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (refresh = false) => {
    setBusy(true);
    try {
      // `probe=true` is what makes the daemon do NETWORK checks for remote
      // bases. It is opt-in per request on purpose: the same endpoint feeds
      // the Long-term tab's source list and the project page's LTM chip, and
      // neither asked to wait on a dead brain. This card did ask — it is the
      // one surface whose entire job is the verdict.
      const data = await get<{ bases?: MemoryBase[] }>(
        refresh ? "/ltm/sources?refresh=true&probe=true" : "/ltm/sources?probe=true",
      );
      setBases(Array.isArray(data.bases) ? data.bases : null);
    } catch {
      // No answer is not a verdict — say nothing rather than invent one.
      setBases(null);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (!bases || bases.length === 0) return null;

  const broken = bases.filter((b) => b.available === false).length;

  return (
    <Card
      title="Memory bases"
      icon={<Database size={14} />}
      right={
        <div className="flex items-center gap-3">
          {broken > 0 && (
            <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-amber-300">
              <AlertTriangle size={12} />
              {broken} unavailable
            </span>
          )}
          <button
            type="button"
            onClick={() => void load(true)}
            disabled={busy}
            title="Check every base again now"
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/[0.07] bg-white/[0.03] px-2.5 py-1 text-[11px] text-zinc-300 transition-colors hover:bg-white/[0.06] disabled:opacity-50"
          >
            <RefreshCw size={12} className={busy ? "animate-spin" : ""} />
            Re-check
          </button>
        </div>
      }
    >
      <ul className="space-y-2">
        {bases.map((base) => {
          const tone = baseTone(base.available);
          return (
            <li
              key={base.name}
              className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${tone.dot}`} />
                <span className="text-[13px] font-medium text-zinc-200">
                  {base.name}
                </span>
                <span
                  className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${tone.pill}`}
                >
                  {tone.label}
                </span>
                {base.path && (
                  <span className="min-w-0 truncate text-[11px] text-zinc-500">
                    {base.path}
                  </span>
                )}
              </div>
              {/* The explanation is NOT behind a disclosure: the whole point is
                  that a blind base announces itself where the user stands. */}
              {base.detail && (
                <p
                  className={`mt-1 text-[11px] leading-relaxed ${
                    base.available === false ? "text-amber-200/80" : "text-zinc-500"
                  }`}
                >
                  {base.detail}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

export default function MemoryPage() {
  return (
    <>
      <MemorySurface />
      {/* PageShell's own space-y-6 stops at its children, so re-create the gap.
          space-y-6 (not two mt-6 wrappers) because either child can render
          NOTHING — an absent card must cost no vertical space at all. */}
      <div className="mt-6 space-y-6">
        <MemoryBaseHealth />
        <MemoryReview />
      </div>
    </>
  );
}
