"use client";

/**
 * A database set aside at start (v1.504.0, north-star plan 4.3).
 *
 * When the database was found damaged at start, Iron Jarvis renamed it aside
 * and started with an EMPTY one, and said so only in its log; within a week
 * the backup rotation had pruned every archive that held the real data. The
 * daemon now keeps a marker (`GET /maintenance/set-aside`): this note says
 * what happened in plain words, and ONE press restores the newest backup made
 * before it (`POST /maintenance/set-aside/restore`, the same restore as
 * Settings, which first saves the current state so it can be undone).
 * "Keep starting fresh" answers it instead. Renders nothing when nothing was
 * set aside. While it is unanswered the older backups are kept.
 */

import { useState } from "react";
import { AlertTriangle } from "lucide-react";

import { ApiError, post } from "@/lib/api";
import { useApi } from "@/lib/useApi";

export const SET_ASIDE_PATH = "/maintenance/set-aside";

export interface SetAsideStatus {
  set_aside: { file: string; at: string; reason: string } | null;
  latest_good: { name: string; bytes: number; modified_at: string } | null;
}

function when(iso: string | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function DatabaseSetAsideNote() {
  const status = useApi<SetAsideStatus>(SET_ASIDE_PATH);
  const [busy, setBusy] = useState<"" | "restore" | "keep">("");
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const marker = status.data?.set_aside;
  if (!marker && !done) return null;

  async function act(kind: "restore" | "keep") {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "restore") {
        const r = await post<{ restored_from: string; undo?: string }>(`${SET_ASIDE_PATH}/restore`);
        setDone(
          `Restored the backup ${r.restored_from}. Iron Jarvis is restarting now. ${r.undo ?? ""}`.trim(),
        );
      } else {
        await post(`${SET_ASIDE_PATH}/keep`);
        setDone("Kept the fresh start. Older backups are rotated as usual again.");
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }

  if (done) {
    return (
      <p role="status" data-testid="db-set-aside-done" className="mb-3 text-[12px] text-zinc-300">
        {done}
      </p>
    );
  }
  const good = status.data?.latest_good;
  return (
    <div
      role="alert"
      data-testid="db-set-aside"
      className="mb-3 rounded-xl border border-amber-400/30 bg-amber-400/[0.07] p-3 text-[12.5px] leading-relaxed text-amber-100"
    >
      <p className="flex items-start gap-2">
        <AlertTriangle size={15} className="mt-0.5 shrink-0" aria-hidden />
        <span>
          Your database was found damaged on {when(marker?.at)} and was set aside, so Iron Jarvis
          started with an empty one. The damaged file is kept as{" "}
          <span className="break-all font-mono text-[11px]">{marker?.file}</span>. Backups made before
          then are kept until you choose.
        </span>
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        {good ? (
          <button
            type="button"
            className="btn-accent btn-sm"
            disabled={busy !== ""}
            onClick={() => void act("restore")}
            data-testid="db-set-aside-restore"
          >
            {busy === "restore" ? "Restoring…" : `Restore the backup from ${when(good.modified_at)}`}
          </button>
        ) : (
          <span className="text-[11.5px] text-amber-100/80">
            There is no backup from before then to restore.
          </span>
        )}
        <button
          type="button"
          className="btn-ghost btn-sm"
          disabled={busy !== ""}
          onClick={() => void act("keep")}
          data-testid="db-set-aside-keep"
        >
          {busy === "keep" ? "Saving…" : "Keep starting fresh"}
        </button>
      </div>
      {error && (
        <p className="mt-1.5 text-[11.5px] text-rose-200" data-testid="db-set-aside-error">
          {error}
        </p>
      )}
    </div>
  );
}
