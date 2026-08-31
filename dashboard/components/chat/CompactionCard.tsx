"use client";

/**
 * Compaction inspect (v1.169.0) — the standing summary, readable again.
 *
 * A model-written compaction summary is injected into the SYSTEM prompt of
 * every later turn and read back as authoritative — and until now it was
 * readable exactly once, in the response of the compact that created it. The
 * chip (rendered in the thread header slot, same idiom as CommThreadBanner)
 * says a summary is standing in for N older messages; the card shows exactly
 * what that summary says, and — the honest half — what the verification pass
 * REMOVED because the transcript and the execution ledger could not
 * corroborate it.
 *
 * SERVER TRUTH ONLY: the chip renders off GET /chat/threads/{id}/compaction
 * (or the POST /chat/compact response for a not-yet-saved thread), never off
 * the context gauge alone — `compacted` on the gauge is a per-turn report,
 * not proof a summary still stands over the thread as saved.
 *
 * Two honesty wrinkles handled here:
 *   - `strippedNote`: rows written before v1.169.0 (and the agent auto-lane)
 *     persisted only the COUNT of stripped claims, not their text.
 *     `stripped > 0` with an empty claims list must say "not recorded" —
 *     rendering the empty state ("nothing was stripped") there would be a
 *     small lie about the exact thing this card exists to surface.
 *   - `truncatedNote`: the producer bounds the claim TEXTS it records
 *     (compaction.compact_messages keeps at most 20) while `stripped` keeps
 *     the full count — so a list can be real but PARTIAL. Rendering 20 rows
 *     with no hint that more were removed collapses that fourth state into
 *     "claims listed", so the list gets an explicit "and N more" tail.
 */

import { useEffect } from "react";
import { EyeOff, Layers, X } from "lucide-react";
import { Modal } from "@/components/Modal";

/** GET /chat/threads/{id}/compaction — the wire shape (routes/chat.py). */
export interface CompactionInfo {
  found: boolean;
  summary?: string;
  /** How many leading messages the summary replaces. */
  covers?: number;
  /** COUNT of claims the verification pass removed. */
  stripped?: number;
  /** The removed claims' text — may be empty while `stripped` is positive
   *  (older rows / the agent lane persisted only the count). */
  stripped_claims?: string[];
  /** "manual" (the user chose it) | "auto" (the ceiling forced it). */
  trigger?: string;
  provider?: string;
  model?: string;
  created_at?: string;
}

/**
 * The one-line message under "Removed because the record could not
 * corroborate it", or null when the claims themselves are listed. Exported
 * pure so the three states stay pinned by tests:
 *   - claims listed            -> null (the list renders instead)
 *   - stripped 0               -> the empty state
 *   - stripped > 0, no text    -> honest "not recorded", NEVER the empty state
 */
export function strippedNote(stripped: number, claims: string[]): string | null {
  if (claims.length > 0) return null;
  if (stripped > 0) {
    return `${stripped} claim${stripped === 1 ? " was" : "s were"} removed, but the removed text was not recorded for this summary.`;
  }
  return "Nothing was stripped — every checkable claim was corroborated by the record.";
}

/**
 * The "and N more" tail under a PARTIAL claims list, or null when the list is
 * complete (or empty — the count-only case is strippedNote's job). Exported
 * pure so the fourth honest state stays pinned by tests:
 *   - stripped > listed > 0 -> "…and N more removed", NEVER silence — the
 *     producer records at most 20 claim texts while `stripped` keeps the full
 *     count, and a 20-row list with no tail reads as the whole story.
 */
export function truncatedNote(stripped: number, listed: number): string | null {
  if (listed <= 0 || stripped <= listed) return null;
  const extra = stripped - listed;
  return `…and ${extra} more claim${extra === 1 ? " was" : "s were"} removed — the removed text was not recorded beyond the ${listed} listed.`;
}

/** "12 older messages summarized" — exported pure for the tests. */
export function chipLabel(covers: number): string {
  return `${covers} older message${covers === 1 ? "" : "s"} summarized`;
}

/**
 * Header chip: a summary is standing in for the thread's older messages.
 * Renders NOTHING unless the server said one stands — same zero-noise rule
 * as the TurnReceipt.
 */
export function CompactionChip({
  info,
  onView,
}: {
  info: CompactionInfo | null;
  onView: () => void;
}) {
  if (!info?.found) return null;
  return (
    <div className="flex items-center gap-2 border-b hairline bg-accent/[0.04] px-4 py-2 text-[12px] text-zinc-300">
      <Layers size={13} className="shrink-0 text-accent-soft" />
      <span className="min-w-0 truncate">
        {chipLabel(info.covers ?? 0)} — the model reads the summary in their
        place; the full transcript is kept.
      </span>
      <button
        type="button"
        onClick={onView}
        className="ml-auto shrink-0 rounded-md border border-white/10 px-2 py-0.5 text-[11px] font-medium text-zinc-300 transition-colors hover:border-accent/50 hover:text-accent-soft"
        title="Read the summary standing in for the older messages — and what was removed from it as unverifiable"
      >
        view
      </button>
    </div>
  );
}

/**
 * The card: the standing summary, then the clearly-labeled removed-claims
 * section. A modal, because the summary can be long and it is a READING
 * surface — it takes no actions on the thread.
 */
export function CompactionCard({
  info,
  onClose,
}: {
  info: CompactionInfo;
  onClose: () => void;
}) {
  const claims = (info.stripped_claims ?? []).filter(
    (c) => typeof c === "string" && c.trim().length > 0,
  );
  const stripped = info.stripped ?? 0;
  const note = strippedNote(stripped, claims);
  const truncNote = truncatedNote(stripped, claims.length);
  const meta = [
    info.trigger === "manual"
      ? "compacted at your request"
      : info.trigger === "auto"
        ? "compacted automatically at the context ceiling"
        : null,
    info.provider
      ? `written by ${info.provider}${info.model ? ` · ${info.model}` : ""}`
      : null,
    info.created_at ? new Date(info.created_at).toLocaleString() : null,
  ].filter(Boolean);

  return (
    // PORTALLED (v1.216.1) — same class as the Projects folder picker this
    // release fixes: a `fixed inset-0` overlay is pinned to the nearest
    // ancestor with a `backdrop-filter` (every `.card-surface` in this app),
    // not to the viewport. `Modal` renders into document.body and owns the
    // backdrop, Escape and the scroll lock.
    <Modal
      label="Compaction summary"
      onClose={onClose}
      className="w-full max-w-2xl bg-zinc-900"
      testId="compaction-modal"
    >
        <div className="flex items-center gap-2 border-b hairline px-4 py-3">
          <Layers size={15} className="shrink-0 text-accent-soft" />
          <h2 className="min-w-0 truncate text-[13.5px] font-medium text-zinc-200">
            {chipLabel(info.covers ?? 0)}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="ml-auto shrink-0 rounded-md p-1 text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
          >
            <X size={15} />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-3">
          <p className="text-[12px] leading-relaxed text-zinc-500">
            This is the text the model reads in place of the older messages on
            every later turn. Every checkable claim in it was verified against
            the transcript; the full conversation is still stored untouched.
          </p>
          {/* The summary VERBATIM — pre-wrapped, not re-rendered as markdown:
              what the model reads is plain text, and showing a prettified
              version would be showing something else. */}
          <pre className="whitespace-pre-wrap break-words rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5 font-sans text-[12.5px] leading-relaxed text-zinc-300">
            {info.summary ?? ""}
          </pre>

          <div>
            <h3 className="mb-1.5 flex items-center gap-1.5 text-[12px] font-medium text-amber-300/90">
              <EyeOff size={12} className="shrink-0" />
              Removed because the record could not corroborate it
            </h3>
            {note ? (
              <p
                className={`text-[12px] leading-relaxed ${
                  stripped > 0 ? "text-amber-200/80" : "text-zinc-500"
                }`}
              >
                {note}
              </p>
            ) : (
              <>
                <ul className="space-y-1">
                  {claims.map((c, i) => (
                    <li
                      key={`${c}-${i}`}
                      className="rounded-lg border border-amber-500/20 bg-amber-500/[0.05] px-2.5 py-1.5 font-mono text-[11.5px] text-amber-200/90"
                    >
                      {c}
                    </li>
                  ))}
                </ul>
                {truncNote && (
                  <p className="mt-1.5 text-[12px] leading-relaxed text-amber-200/80">
                    {truncNote}
                  </p>
                )}
              </>
            )}
          </div>
        </div>

        {meta.length > 0 && (
          <div className="border-t hairline px-4 py-2 text-[11px] text-zinc-500">
            {meta.join(" · ")}
          </div>
        )}
    </Modal>
  );
}
