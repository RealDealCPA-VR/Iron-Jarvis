"use client";

/**
 * Standing grants (v1.299.0) — every "Always allow exactly this" the user has
 * said, in one list, each revocable.
 *
 * A grant is keyed on the EXACT arguments of one tool call (`args_hash`) in
 * ONE scope — a goal, an agent, a project, or chat — and expires in 30 days.
 * The goals ladder may also write a per-tool grant (args_hash "" = any
 * arguments); those rows show "any arguments" rather than a label.
 *
 * Wire: GET /grants?live=1 → {grants: [...]}, POST /grants/{id}/revoke →
 * {grant}; events grant.created / grant.revoked refresh the list. An OLDER
 * DAEMON has no /grants route: a 404 renders NOTHING — not an error, not an
 * empty card — because a control the daemon cannot honour is a lie. Revoke is
 * two presses (ConfirmButton): a standing permission is removed on purpose.
 */

import { useEffect, useRef, useState } from "react";
import { KeyRound } from "lucide-react";
import { ApiError, post } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { useEvents } from "@/lib/useEvents";
import type { StandingGrant } from "@/lib/types";
import { Card, ConfirmButton, ErrorNote } from "@/components/ui";

/** "expires in 29 d" / "expires today" / "expired" / "" (no expiry). */
export function expiresWords(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "";
  const days = Math.floor((t - now) / 86_400_000);
  if (t <= now) return "expired";
  if (days < 1) return "expires today";
  return `expires in ${days} d`;
}

/** The scope in plain words: "goal · inbox-zero", "agent · remy", "chat". */
export function scopeWords(g: Pick<StandingGrant, "scope_kind" | "scope_id">): string {
  if (g.scope_kind === "chat") return "chat";
  return g.scope_id ? `${g.scope_kind} · ${g.scope_id}` : g.scope_kind;
}

/** One grant row: scope · tool · label · expiry · uses + a two-press Revoke.
 *  Shared by the Autonomy card and each goal's own list (v1.299.0). */
export function GrantRow({
  grant: g,
  busy,
  onRevoke,
  showScope = true,
}: {
  grant: StandingGrant;
  busy: boolean;
  onRevoke: (g: StandingGrant) => void | Promise<void>;
  showScope?: boolean;
}) {
  const expiry = expiresWords(g.expires_at);
  return (
    <li
      data-testid={`standing-grant-${g.id}`}
      className="flex flex-wrap items-center gap-2 rounded-lg border border-white/[0.05] bg-white/[0.015] px-3 py-2 text-xs"
    >
      {showScope && (
        <span className="shrink-0 text-[11px] uppercase tracking-wide text-zinc-500">
          {scopeWords(g)}
        </span>
      )}
      <span className="font-mono text-zinc-100">{g.tool}</span>
      <span
        className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-400"
        title={g.label || undefined}
      >
        {g.args_hash ? g.label || g.args_hash.slice(0, 12) : "any arguments"}
      </span>
      <span className="shrink-0 tabular-nums text-[11px] text-zinc-500">
        {expiry ? `${expiry} · ` : ""}
        {g.uses} use{g.uses === 1 ? "" : "s"}
      </span>
      <ConfirmButton
        onConfirm={() => onRevoke(g)}
        label={busy ? "Revoking…" : "Revoke"}
        confirmLabel="Revoke?"
        title={`Revoke this grant for ${g.tool} — it asks again from the next call`}
      />
    </li>
  );
}

export function StandingGrants() {
  const grants = useApi<{ grants: StandingGrant[] }>("/grants?live=1");
  const { events } = useEvents(40);
  const seenRef = useRef<string | null>(null);
  const reload = grants.reload;
  // The useLiveGoals idiom: a grant.* event since the last look = stale.
  useEffect(() => {
    const newest = events[0];
    if (!newest?.id) return;
    const boundary = seenRef.current;
    seenRef.current = newest.id;
    let stale = false;
    for (const e of events) {
      if (e.id === boundary) break;
      if (typeof e.type === "string" && e.type.startsWith("grant.")) stale = true;
    }
    if (stale) reload();
  }, [events, reload]);

  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // No route (older daemon) or no daemon: nothing. The page's OfflineHint
  // already covers the second case; a second notice would say it twice.
  if (grants.error && (grants.error.status === 404 || grants.error.status === 0)) return null;
  if (!grants.data && !grants.error) return null; // first load — no husk

  const rows = (grants.data?.grants ?? []).filter((g) => g && typeof g.id === "string");

  async function revoke(g: StandingGrant) {
    setBusy(g.id);
    setError(null);
    try {
      await post(`/grants/${encodeURIComponent(g.id)}/revoke`);
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      title={`Standing grants${rows.length ? ` · ${rows.length}` : ""}`}
      icon={<KeyRound size={15} />}
    >
      <div data-testid="standing-grants">
        <p className="mb-2.5 text-[11.5px] leading-relaxed text-zinc-500">
          Every “Always allow exactly this” you have said: one tool, one exact set of
          arguments, one scope, 30 days. Different arguments still ask.
        </p>
        {grants.error ? (
          <ErrorNote>{grants.error.message}</ErrorNote>
        ) : rows.length === 0 ? (
          <p className="text-sm text-zinc-500">
            None standing. The button appears on an approval card when the daemon can key a
            grant on the exact call.
          </p>
        ) : (
          <ul className="space-y-1.5">
            {rows.map((g) => (
              <GrantRow key={g.id} grant={g} busy={busy === g.id} onRevoke={revoke} />
            ))}
          </ul>
        )}
        {error && (
          <div className="mt-2">
            <ErrorNote>{error}</ErrorNote>
          </div>
        )}
      </div>
    </Card>
  );
}
