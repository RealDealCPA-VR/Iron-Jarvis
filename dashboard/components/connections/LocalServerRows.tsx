"use client";

/**
 * LM Studio and llama.cpp, found running on this PC (v1.358.0, lane 8D).
 *
 * Rows in the Accounts card, right under "Local Ollama" and in its look (the
 * same icon box, the same calm press and calm badge — no new card): the
 * daemon's cached probe (`GET /connections/local-servers`,
 * providers/local_servers_seen — a read-only look at each server's model list
 * on this PC's own address, never waited on) says which one answers, and the
 * row offers it in one press the way the Ollama row does:
 *
 *  - running, the model-server slot free → "Running on this PC, not
 *    connected yet." + "Use LM Studio on this PC" (POST
 *    /connections/local-servers/{key}/connect: a fresh check, then its
 *    address saved through the one settings writer). It connects; which
 *    model answers stays the user's choice — nothing switches on its own;
 *  - its address is the saved one → "Detected · ready to use";
 *  - another address holds the slot → where to add it instead (never
 *    replaced).
 *
 * A server that is not running draws no row (the Ollama row already says how
 * to get a local model). Nothing renders while the first read is out, after a
 * failed read, or on an older daemon.
 */

import { useState } from "react";
import { Loader2, Server } from "lucide-react";
import { post, ApiError } from "@/lib/api";
import { usePolledApi } from "@/lib/useApi";
import { useDaemon } from "@/lib/daemon";
import { Badge } from "@/components/ui";
import { CALM_ACTION } from "@/components/connections/endpointChip";

/** One row of GET /connections/local-servers. */
export interface LocalServerRow {
  key: string;
  name: string;
  label: string;
  base_url: string;
  running: boolean;
  models: number;
  /** "free" | "saved" | "taken" — the one model-server slot. */
  slot: string;
}

/** How often the rows re-read the daemon's cached answer (it never waits on
 *  a port, so this is cheap). */
export const LOCAL_SERVERS_POLL_MS = 15_000;

export function decodeLocalServers(raw: unknown): LocalServerRow[] {
  const list = raw && typeof raw === "object" ? (raw as { servers?: unknown }).servers : null;
  if (!Array.isArray(list)) return [];
  const out: LocalServerRow[] = [];
  for (const r of list) {
    if (!r || typeof r !== "object") continue;
    const o = r as Record<string, unknown>;
    if (typeof o.key !== "string" || !o.key || typeof o.name !== "string") continue;
    out.push({
      key: o.key,
      name: o.name,
      label: typeof o.label === "string" ? o.label : o.name,
      base_url: typeof o.base_url === "string" ? o.base_url : "",
      running: o.running === true,
      models: typeof o.models === "number" ? o.models : 0,
      slot: typeof o.slot === "string" ? o.slot : "",
    });
  }
  return out;
}

function LocalServerRowView({ row, onChanged }: { row: LocalServerRow; onChanged: () => void }) {
  const { refresh } = useDaemon();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function connect() {
    setBusy(true);
    setError(null);
    try {
      const res = await post<{ detail?: string }>(
        `/connections/local-servers/${encodeURIComponent(row.key)}/connect`,
      );
      setDone(typeof res?.detail === "string" && res.detail ? res.detail : `${row.name} is connected.`);
      refresh(); // the model menus follow at once, not on the next poll
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const ready = row.slot === "saved";
  return (
    <div
      data-testid={`local-server-row-${row.key}`}
      className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0"
    >
      <div className="flex min-w-0 items-center gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-white/[0.08] bg-white/[0.03]">
          <Server size={16} className="text-tone-info" aria-hidden />
        </span>
        <div className="min-w-0">
          <div className="text-sm font-medium text-zinc-100">{row.name}</div>
          <div className="truncate text-[11px] text-zinc-500">A model server on this PC</div>
          {done ? (
            <div role="status" className="mt-1 text-[11px] leading-relaxed text-tone-success">
              {done}
            </div>
          ) : row.slot === "free" ? (
            <div data-testid={`use-local-server-${row.key}`} className="mt-1 text-[11px] leading-relaxed text-zinc-500">
              Running on this PC, not connected yet.{" "}
              <button
                type="button"
                onClick={() => void connect()}
                disabled={busy}
                className={`-ml-1 ${CALM_ACTION}`}
              >
                {busy && <Loader2 size={13} className="animate-spin motion-reduce:animate-none" aria-hidden />}
                Use {row.name} on this PC
              </button>
            </div>
          ) : row.slot === "taken" ? (
            <div data-testid={`local-server-taken-${row.key}`} className="mt-1 text-[11px] leading-relaxed text-zinc-500">
              Running on this PC. Your own model server is set to another address, so add this one
              with Add an endpoint, then {row.name} on this PC.
            </div>
          ) : null}
          {error && (
            <span role="alert" className="mt-0.5 block text-[11px] text-tone-danger">
              {error}
            </span>
          )}
        </div>
      </div>
      {ready || done ? (
        <Badge variant="calm" value="Detected · ready to use" tone="green" keepCase />
      ) : (
        <Badge variant="calm" value="Running on this PC" tone="slate" keepCase />
      )}
    </div>
  );
}

/** The rows, for the Accounts card's list (each one its own divided row). */
export function LocalServerRows() {
  const { data, reload } = usePolledApi<unknown>("/connections/local-servers", LOCAL_SERVERS_POLL_MS);
  const rows = decodeLocalServers(data).filter((r) => r.running);
  if (rows.length === 0) return null;
  return (
    <>
      {rows.map((r) => (
        <LocalServerRowView key={r.key} row={r} onChanged={reload} />
      ))}
    </>
  );
}
