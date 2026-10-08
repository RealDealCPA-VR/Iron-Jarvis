"use client";

/**
 * "CHANGED HERE OR IN CHAT" (calm UI redesign S10, AUDIT §4.4 Permissions &
 * ledger). Every configuration change — a setting, a schedule or workflow
 * changed from chat, a credential saved on a card — newest first, with where it
 * was made and its Undo while one is still possible. Names and summaries only;
 * a credential's row never carries a value (the daemon never sends one).
 */

import { useCallback, useEffect, useState } from "react";
import { History, RotateCcw } from "lucide-react";
import { get, post } from "@/lib/api";
import { Card } from "@/components/ui";
import { timeAgo } from "@/lib/format";

export interface LedgerRow {
  action_id: string;
  tool: string;
  summary: string;
  where: "here" | "chat";
  at: string | null;
  undoable: boolean;
  undone: boolean;
}

export function LedgerPanel({ refreshKey = 0 }: { refreshKey?: number }) {
  const [rows, setRows] = useState<LedgerRow[] | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(() => {
    Promise.resolve(get<{ changes?: LedgerRow[] }>("/config/ledger?limit=30"))
      .then((d) => setRows(Array.isArray(d?.changes) ? d!.changes! : []))
      .catch(() => setRows((r) => r ?? []));
  }, []);
  useEffect(() => {
    load();
  }, [load, refreshKey]);

  async function undo(id: string) {
    setBusy(id);
    setError("");
    try {
      await post(`/undo/${encodeURIComponent(id)}`, {});
      load();
    } catch (e) {
      setError((e as { message?: string })?.message || "Couldn't undo that.");
    } finally {
      setBusy("");
    }
  }

  return (
    <Card title="Changed here or in chat" icon={<History size={15} />}>
      {rows === null ? null : rows.length === 0 ? (
        <p className="text-[12px] text-zinc-500">No changes yet. Settings you change here or in chat are listed with an Undo.</p>
      ) : (
        <ul className="divide-y divide-white/[0.05]" data-testid="settings-ledger">
          {rows.map((r) => (
            <li key={r.action_id} className="flex items-center gap-3 py-2 text-[13px]" data-testid="settings-ledger-row">
              <span className="min-w-0 flex-1">
                <span className={`block truncate ${r.undone ? "text-zinc-500 line-through" : "text-zinc-200"}`}>
                  {r.summary || r.tool}
                </span>
                <span className="text-[11px] text-zinc-500">
                  {r.where === "chat" ? "In chat" : "Here"}
                  {r.at ? ` · ${timeAgo(r.at)}` : ""}
                  {r.undone ? " · undone" : ""}
                </span>
              </span>
              {r.undoable && (
                <button
                  type="button"
                  onClick={() => void undo(r.action_id)}
                  disabled={busy === r.action_id}
                  className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-[12px] text-zinc-300 hover:border-white/20 disabled:opacity-50"
                >
                  <RotateCcw size={11} aria-hidden /> Undo
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p role="alert" className="mt-2 text-[12px] text-tone-danger">
          {error}
        </p>
      )}
    </Card>
  );
}
