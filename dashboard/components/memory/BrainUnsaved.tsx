"use client";

/**
 * Memory › "Unsaved changes" (v1.406.0): notes a brain profile meant to save
 * to the primary memory base that could not reach it. Each one waits here, so
 * nothing is lost: Try again, or Discard (two presses).
 *
 * Hidden when the list is empty, and on an older Iron Jarvis (404 / no list).
 */

import { useCallback, useEffect, useState } from "react";
import { CloudOff } from "lucide-react";
import { del, get, post } from "@/lib/api";
import { Card, ConfirmButton } from "@/components/ui";
import { decodeBrainPending, type BrainPendingRow } from "@/lib/brainProfiles";

const msg = (e: unknown, fallback: string) => (e as { message?: string })?.message || fallback;

export function BrainUnsaved() {
  const [rows, setRows] = useState<BrainPendingRow[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [rowError, setRowError] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    try {
      setRows(decodeBrainPending(await get<unknown>("/brain/pending")));
    } catch {
      // No answer is not a list: say nothing rather than invent one.
      setRows(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function retry(row: BrainPendingRow) {
    setBusy(row.id);
    setNote("");
    setRowError((m) => ({ ...m, [row.id]: "" }));
    try {
      const res = await post<{ ok?: unknown; saved?: { verified?: unknown } }>(
        `/brain/pending/${encodeURIComponent(row.id)}/retry`,
        {},
      );
      const where = row.base || "your memory base";
      setNote(
        res?.saved?.verified === true
          ? `Saved ${row.title} to ${where}.`
          : `Saved ${row.title} to ${where}, but reading it back did not confirm it yet.`,
      );
      setRows((r) => (r ? r.filter((x) => x.id !== row.id) : r));
    } catch (e) {
      setRowError((m) => ({ ...m, [row.id]: msg(e, "It still couldn't be saved.") }));
    } finally {
      setBusy(null);
    }
  }

  async function discard(row: BrainPendingRow) {
    setNote("");
    try {
      await del(`/brain/pending/${encodeURIComponent(row.id)}`);
      setRows((r) => (r ? r.filter((x) => x.id !== row.id) : r));
      setNote(`Discarded ${row.title}.`);
    } catch (e) {
      setRowError((m) => ({ ...m, [row.id]: msg(e, "Couldn't discard it.") }));
    }
  }

  if (!rows || (rows.length === 0 && !note)) return null;

  return (
    <Card title="Unsaved changes" icon={<CloudOff size={14} />}>
      <div data-testid="brain-unsaved" className="space-y-3">
        <p className="text-[12px] text-zinc-500">
          Notes Iron Jarvis could not save to your memory base yet. Nothing here is lost: try again, or discard one.
        </p>
        {note && (
          <p role="status" data-testid="brain-unsaved-note" className="text-[12px] text-tone-success">
            {note}
          </p>
        )}
        {rows.length > 0 && (
          <ul className="divide-y divide-white/[0.05]">
            {rows.map((r) => (
              <li key={r.id} data-testid={`brain-pending-${r.id}`} className="space-y-1 py-2">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="min-w-0 flex-1">
                    <span className="block text-[13px] text-zinc-100">{r.title}</span>
                    <span className="block text-[12px] text-zinc-500">
                      {[r.base, r.profile_name].filter(Boolean).join(" · ")}
                    </span>
                  </span>
                  <button
                    type="button"
                    data-testid={`brain-retry-${r.id}`}
                    onClick={() => void retry(r)}
                    disabled={busy === r.id}
                    className="btn-ghost btn-sm"
                  >
                    {busy === r.id ? "Trying…" : "Try again"}
                  </button>
                  <ConfirmButton
                    variant="calm"
                    label="Discard"
                    confirmLabel="Discard this note?"
                    onConfirm={() => discard(r)}
                  />
                </div>
                {r.preview && <p className="line-clamp-2 text-[12px] text-zinc-400">{r.preview}</p>}
                {(rowError[r.id] || r.error) && (
                  <p data-testid={`brain-pending-error-${r.id}`} className="text-[12px] text-tone-warn">
                    {rowError[r.id] || r.error}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}
