"use client";

/**
 * Third plan TM3 + TM19 — the terminals from before a restart that could not
 * be reopened, ONE ROW EACH, with the daemon's plain reason, "Try again" and
 * "Close".
 *
 * Before: a terminal whose program was uninstalled or moved was dropped with
 * no word (and its kept history deleted at the next save); one the start
 * gave up on was listed only as a count, could not be dismissed, and was
 * tried again at every start. `GET /terminals` now lists each as
 * `restore_failed_panes: [{id, name, cwd, reason}]` (absent when none).
 * "Try again" is `POST /terminals/not-reopened/{id}/retry` (the terminal's
 * row, or a plain sentence); "Close" asks first, as closing a terminal does,
 * then `DELETE /terminals/not-reopened/{id}` forgets it and its kept history.
 */

import { useState } from "react";
import { RotateCcw } from "lucide-react";

import { ConfirmButton } from "@/components/ui";

export type NotReopenedPane = {
  id: string;
  name: string | null;
  cwd: string | null;
  /** The daemon's plain sentence, e.g. "Its program, pwsh, is no longer on this PC." */
  reason: string;
};

/** The daemon's `restore_failed_panes`, or [] when absent or malformed. */
export function decodeNotReopened(raw: unknown): NotReopenedPane[] {
  if (!Array.isArray(raw)) return [];
  const out: NotReopenedPane[] = [];
  for (const r of raw) {
    if (!r || typeof r !== "object") continue;
    const o = r as Record<string, unknown>;
    if (typeof o.id !== "string" || !o.id) continue;
    const reason = typeof o.reason === "string" && o.reason.trim() ? o.reason.trim() : "";
    out.push({
      id: o.id,
      name: typeof o.name === "string" && o.name.trim() ? o.name.trim() : null,
      cwd: typeof o.cwd === "string" && o.cwd.trim() ? o.cwd.trim() : null,
      reason: reason.length <= 300 ? reason : `${reason.slice(0, 297)}…`,
    });
  }
  return out;
}

function titleOf(p: NotReopenedPane): string {
  if (p.name) return p.name;
  if (p.cwd) {
    const parts = p.cwd.split(/[\\/]/).filter(Boolean);
    if (parts.length) return `Terminal in ${parts[parts.length - 1]}`;
  }
  return "A terminal";
}

export function NotReopenedList({
  panes,
  onRetry,
  onClose,
}: {
  panes: NotReopenedPane[];
  /** Resolves to null once it reopened, else the plain sentence to show. */
  onRetry: (id: string) => Promise<string | null>;
  /** Resolves to null once it is forgotten, else the plain sentence to show. */
  onClose: (id: string) => Promise<string | null>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [said, setSaid] = useState<Record<string, string>>({});
  if (panes.length === 0) return null;

  const run = async (id: string, act: (id: string) => Promise<string | null>) => {
    setBusy(id);
    try {
      const sentence = await act(id);
      setSaid((prev) => {
        const next = { ...prev };
        if (sentence) next[id] = sentence;
        else delete next[id];
        return next;
      });
    } finally {
      setBusy(null);
    }
  };

  return (
    <ul data-testid="not-reopened" className="flex flex-col gap-2 px-2.5 py-3">
      {panes.map((p) => (
        <li
          key={p.id}
          data-testid={`not-reopened-${p.id}`}
          className="rounded-[10px] border border-white/[0.06] px-2.5 py-2 text-meta text-zinc-400"
        >
          <div className="truncate text-[13px] text-zinc-200" title={p.cwd ?? undefined}>
            {titleOf(p)}
          </div>
          <p className="mt-0.5">
            Couldn&apos;t be reopened after the restart.{p.reason ? ` ${p.reason}` : ""}
          </p>
          {said[p.id] ? (
            <p role="status" className="mt-1 text-tone-warn/90">
              {said[p.id]}
            </p>
          ) : null}
          <div className="mt-1.5 flex items-center gap-2">
            <button
              type="button"
              data-testid={`not-reopened-retry-${p.id}`}
              disabled={busy !== null}
              onClick={() => void run(p.id, onRetry)}
              className="btn-ghost inline-flex items-center gap-1 py-1 text-xs"
            >
              <RotateCcw size={12} />
              Try again
            </button>
            <ConfirmButton
              testId={`not-reopened-close-${p.id}`}
              disabled={busy !== null}
              onConfirm={() => run(p.id, onClose)}
              label="Close"
              confirmLabel="Confirm close"
              title="Forget this terminal and the history kept for it"
            />
          </div>
        </li>
      ))}
    </ul>
  );
}
