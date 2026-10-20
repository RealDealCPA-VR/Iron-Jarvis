/**
 * What Jarvis may READ, as the chat's approval chip knows it (v1.360.0, the
 * v1.356.0 G81 regression).
 *
 * The chip (components/chat/PermissionChip.tsx) said "Reads files anywhere on
 * this PC without asking" in every level, even after the user limited reading
 * to chosen folders. The truth lives in ONE place, GET /settings/access
 * (`reads`), which the Access summary's Reading files row already reads; this
 * module holds the last answer for the chip, in memory, for the window:
 *
 *   - `refreshReadFacts()` asks once when the user reaches for the chip
 *     (pointer over it, focus, open) and the last answer is older than
 *     `READ_FACTS_TTL_MS`; one request at a time. Nothing is read on mount,
 *     so a page that never touches the chip makes no extra request.
 *   - `noteReadFacts(reads)` is how the Access summary hands over what it
 *     read (it re-reads after the folders setting is saved), so the chip is
 *     current the moment the user comes back to a chat.
 *   - `useReadFacts()` is the chip's subscription.
 *
 * An answer without `reads` (an older Iron Jarvis, a stand-in) changes
 * nothing, and a failed read keeps what was known: the chip then says what it
 * said before (lib/permissionLevels READS_LINE), never a guess.
 *
 * Kept out of lib/api.ts (mocked wholesale by ~71 test files); `get` is
 * called inside a promise so a mock without it cannot throw into a render.
 */

import { useSyncExternalStore } from "react";
import { get } from "@/lib/api";
import type { ReadFacts } from "@/lib/permissionLevels";

/** How long one answer is trusted before reaching for the chip asks again. */
export const READ_FACTS_TTL_MS = 10_000;

/** The route the facts come from (the Access summary's own). */
export const READ_FACTS_PATH = "/settings/access";

let facts: ReadFacts | undefined;
let triedAt = 0;
let inFlight = false;
const listeners = new Set<() => void>();

function emit(): void {
  for (const fn of Array.from(listeners)) {
    try {
      fn();
    } catch {
      /* one listener never stops the others */
    }
  }
}

function same(a: ReadFacts | undefined, b: ReadFacts | undefined): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

/** Keep what a reader of GET /settings/access learned. Anything that is not
 *  a `reads` object is ignored (it says nothing about reading). */
export function noteReadFacts(reads: unknown, now: number = Date.now()): void {
  if (!reads || typeof reads !== "object" || Array.isArray(reads)) return;
  triedAt = now;
  const next = { ...(reads as ReadFacts) };
  if (same(facts, next)) return;
  facts = next;
  emit();
}

/** Ask Iron Jarvis again when the last answer is stale. Never throws. */
export function refreshReadFacts(now: number = Date.now()): void {
  if (inFlight) return;
  if (triedAt && now - triedAt < READ_FACTS_TTL_MS) return;
  inFlight = true;
  triedAt = now;
  Promise.resolve()
    .then(() => get<{ reads?: unknown }>(READ_FACTS_PATH))
    .then((res) => {
      if (res && typeof res === "object") noteReadFacts((res as { reads?: unknown }).reads);
    })
    .catch(() => {
      /* keep what was known; the chip says READS_LINE until an answer lands */
    })
    .finally(() => {
      inFlight = false;
    });
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** The last reading facts this window learned; undefined = none yet. */
export function useReadFacts(): ReadFacts | undefined {
  return useSyncExternalStore(
    subscribe,
    () => facts,
    () => undefined,
  );
}

/** Tests only: forget everything. */
export function __resetReadFacts(): void {
  facts = undefined;
  triedAt = 0;
  inFlight = false;
  emit();
}
