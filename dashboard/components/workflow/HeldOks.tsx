"use client";

/**
 * WHAT A SCHEDULE OR A WORKFLOW MAY DO WITHOUT ASKING (v1.505.0, north-star
 * 5.2, decision D10).
 *
 * "Always for this schedule" / "Always for this workflow" on an approval card
 * keeps a standing grant ON that schedule or workflow: one tool, exactly those
 * arguments, 30 days. Its row on the Schedules / Workflows page lists each one
 * here, with a two-press Remove (`POST /grants/{id}/revoke`), so an OK given
 * once is never invisible. The daemon puts the live ones on the row
 * (`standing_grants`, the grants list's own shape: labels are the redacted
 * one-liners, never the arguments). An older daemon sends none: nothing shows.
 */

import { useState } from "react";
import { ApiError, post } from "@/lib/api";
import type { StandingGrant } from "@/lib/types";
import { ConfirmButton } from "@/components/ui";
import { expiresWords } from "@/components/StandingGrants";

/** The row's `standing_grants`, kept to well-formed entries (a row from an
 *  older daemon, or garbage, is an empty list). */
export function heldOksOf(raw: unknown): StandingGrant[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (g): g is StandingGrant =>
      !!g && typeof g === "object" && typeof (g as StandingGrant).id === "string" &&
      typeof (g as StandingGrant).tool === "string",
  );
}

export function HeldOks({
  grants,
  place,
  name,
  onRemoved,
}: {
  grants: unknown;
  /** Whose row this is: the words say "this schedule" / "this workflow" /
   *  "this rule" (leap 05-c: a rule's OKs, kept after its rehearsal or by
   *  "Always for this rule"). */
  place: "schedule" | "workflow" | "rule";
  /** The schedule's / workflow's name (test ids, the Remove title). */
  name: string;
  /** Called once a Remove has landed, so the page re-reads its rows. */
  onRemoved?: () => void;
}) {
  const rows = heldOksOf(grants);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (rows.length === 0) return null;

  async function remove(g: StandingGrant) {
    setBusy(g.id);
    setError(null);
    try {
      await post(`/grants/${encodeURIComponent(g.id)}/revoke`);
      onRemoved?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div data-testid={`held-oks-${name}`} className="mt-1 space-y-1">
      <span className="block text-[11px] text-zinc-500">Allowed without asking on this {place}:</span>
      <ul className="space-y-1" aria-label={`Allowed without asking on ${name}`}>
        {rows.map((g) => {
          const expiry = expiresWords(g.expires_at);
          return (
            <li
              key={g.id}
              data-testid={`held-ok-${g.id}`}
              className="flex flex-wrap items-center gap-2 text-[11px]"
            >
              <span className="min-w-0 max-w-[260px] truncate font-mono text-zinc-300" title={g.label || g.tool}>
                {g.label || g.tool}
              </span>
              {expiry && <span className="text-zinc-500">{expiry}</span>}
              <ConfirmButton
                onConfirm={() => remove(g)}
                label={busy === g.id ? "Removing…" : "Remove"}
                confirmLabel="Remove?"
                disabled={busy !== null}
                testId={`held-ok-remove-${g.id}`}
                title={`Remove this OK from ${name}: the ${place} asks again from its next run`}
              />
            </li>
          );
        })}
      </ul>
      {error && <span className="block text-[11px] text-tone-danger">{error}</span>}
    </div>
  );
}
