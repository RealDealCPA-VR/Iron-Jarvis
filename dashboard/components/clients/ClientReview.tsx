"use client";

/**
 * The bulk review on /clients (v1.499.0, vision plan leap 3, Phase 3):
 * "Names Iron Jarvis noticed in your folders", each with a tick box and the
 * evidence that made it a candidate.
 *
 * Names found under a Clients-type folder start ticked (the daemon's
 * `pre_ticked`); every other name starts unticked. ONE press on "These are
 * clients" posts the ticked ids to `POST /clients/confirm-many`, and the
 * daemon re-reads each row and confirms only those still waiting. A name left
 * unticked keeps waiting (it is not turned down); "Not a client" on a row is
 * that row's own decline. The page decides nothing itself.
 */

import { useState } from "react";
import { post } from "@/lib/api";
import { failureOf } from "@/lib/loadFailed";
import {
  CONFIRM_MANY_PATH,
  NOT_A_CLIENT,
  NOTICING_OFF,
  REVIEW_ABOUT,
  REVIEW_EMPTY,
  REVIEW_HEADING,
  THESE_ARE_CLIENTS,
  afterNo,
  clientVerbPath,
  confirmedWords,
  decodeConfirmMany,
  foldersWords,
  pressFailed,
  type WaitingName,
} from "@/lib/clients";
import { countWords } from "@/lib/workRecord";

export function ClientReview({
  waiting,
  noticing = true,
  onChanged,
}: {
  waiting: WaitingName[];
  noticing?: boolean;
  /** Something was answered: read the list again. */
  onChanged?: () => void;
}) {
  // The owner's own ticks, by id; a name with no entry shows its default.
  const [ticks, setTicks] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ words: string; failed: boolean } | null>(null);

  const isTicked = (w: WaitingName) => ticks[w.id] ?? w.preTicked;
  const chosen = waiting.filter(isTicked);

  async function confirmTicked() {
    if (busy || chosen.length === 0) return;
    setBusy(true);
    setNote(null);
    try {
      const res = decodeConfirmMany(await post<unknown>(CONFIRM_MANY_PATH, { ids: chosen.map((w) => w.id) }));
      const nameOf = (id: string) => waiting.find((w) => w.id === id)?.name ?? "";
      const skipped = res.skipped
        .map((s) => [nameOf(s.id), s.why].filter(Boolean).join(": "))
        .filter(Boolean);
      setNote({ words: [confirmedWords(res.confirmed.length), ...skipped].join(" "), failed: false });
      onChanged?.();
    } catch (e) {
      setNote({ words: pressFailed(failureOf(e), "save your answers"), failed: true });
    } finally {
      setBusy(false);
    }
  }

  async function decline(w: WaitingName) {
    if (busy) return;
    setBusy(true);
    setNote(null);
    try {
      await post<unknown>(clientVerbPath(w.id, "decline"), {});
      setNote({ words: afterNo(w.name), failed: false });
      onChanged?.();
    } catch (e) {
      setNote({ words: pressFailed(failureOf(e), "save your answer"), failed: true });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section id="waiting" aria-labelledby="client-review-heading" data-testid="client-review" className="scroll-mt-14">
      <h2 id="client-review-heading" className="text-[15px] font-medium text-zinc-100">
        {REVIEW_HEADING}
      </h2>
      {!noticing && (
        <p className="mt-1 max-w-2xl text-[13px] text-zinc-500" data-testid="client-noticing-off">
          {NOTICING_OFF}
        </p>
      )}
      <p
        role="status"
        data-testid="client-review-note"
        className={`text-[13px] ${note ? "py-1" : ""} ${note?.failed ? "text-tone-danger" : "text-zinc-400"}`}
      >
        {note?.words ?? ""}
      </p>
      {waiting.length === 0 ? (
        <p className="py-1 text-[13px] text-zinc-500" data-testid="client-review-empty">
          {REVIEW_EMPTY}
        </p>
      ) : (
        <>
          <p className="mt-1 max-w-2xl text-[13px] text-zinc-500">{REVIEW_ABOUT}</p>
          <ul className="mt-2 divide-y divide-white/5">
            {waiting.map((w) => {
              const boxId = `client-tick-${w.id}`;
              return (
                <li key={w.id} data-testid="client-waiting" data-id={w.id} className="flex items-start gap-3 py-2">
                  <input
                    id={boxId}
                    type="checkbox"
                    data-testid="client-tick"
                    checked={isTicked(w)}
                    disabled={busy}
                    onChange={(e) => setTicks((t) => ({ ...t, [w.id]: e.target.checked }))}
                    className="mt-1 h-4 w-4 shrink-0"
                  />
                  <div className="min-w-0 flex-1">
                    <label htmlFor={boxId} className="block text-sm text-zinc-100">
                      {w.name}
                    </label>
                    <p className="text-[12px] text-zinc-500">
                      {w.about || [foldersWords(w.folders), countWords(w.work)].join(", ")}
                    </p>
                    {w.evidence.length > 0 && (
                      <ul className="mt-0.5 space-y-0.5 text-[12px] text-zinc-400" data-testid="client-waiting-evidence">
                        {w.evidence.map((line, i) => (
                          <li key={i}>{line}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <button
                    type="button"
                    data-testid="client-waiting-decline"
                    disabled={busy}
                    onClick={() => void decline(w)}
                    className="shrink-0 rounded px-1 py-0.5 text-[12px] text-zinc-500 underline-offset-2 hover:text-zinc-200 hover:underline disabled:opacity-40"
                  >
                    {NOT_A_CLIENT}
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="pt-2">
            <button
              type="button"
              data-testid="client-confirm-many"
              disabled={busy || chosen.length === 0}
              onClick={() => void confirmTicked()}
              className="btn-accent btn-sm"
            >
              {THESE_ARE_CLIENTS}
            </button>
          </div>
        </>
      )}
    </section>
  );
}
