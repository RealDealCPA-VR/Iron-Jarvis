"use client";

/**
 * "Hand it a recording" (idea 33, phase 33-a): the card under the reply.
 *
 *   meeting.m4a                                         40 min 12 s
 *   Turning speech into text on this PC: 12 of 40 minutes
 *   ▓▓▓▓▓▓░░░░░░░░░░░░
 * then
 *   meeting.m4a · 5 decisions, 3 things you owe, 2 things they owe
 *   Decisions        [12:31] The launch moves to March.
 *   Things you owe   [14:02] Send the revised plan on Friday.
 *   Things they owe  [19:40] Maria checks the numbers.
 *   2 items were left out: their words are not in the recording near the time given.
 *   meeting transcript.md · meeting notes.md
 *   Made on this PC by the small offline speech model. …
 *
 * While the recording is still being heard the card reads the job again
 * (GET /documents/recording/{id}) every few seconds, ONLY while the page is
 * on screen, and stops when it ends (G7). When the notes would go to a model
 * off this PC, the one main button is "Write the notes" (D-33.1: asked once
 * per chat; only the press sends). Every word and number comes from the
 * job's record. At 390 px every row wraps; nothing pans sideways.
 */

import { useEffect, useRef, useState } from "react";
import { AudioLines, FileText, TriangleAlert } from "lucide-react";
import { get, post } from "@/lib/api";
import { Button } from "@/components/ui";
import { openPlace } from "@/lib/sourceLink";
import {
  KIND_HEADINGS,
  RECORDING_KINDS,
  baseName,
  decodeRecordingCard,
  notesBody,
  offerWords,
  progressShare,
  stillWorking,
  workingWords,
  type RecordingCard as Card,
  type RecordingScope,
} from "@/lib/recording";

/** How often a card that is still working reads the job again. */
export const RECORDING_POLL_MS = 3000;

function errorWords(err: unknown, fallback: string): string {
  const msg = (err as { message?: string })?.message;
  return typeof msg === "string" && msg.trim() ? msg : fallback;
}

function FileLink({ path }: { path: string }) {
  return (
    <button
      type="button"
      onClick={() => openPlace(path, {})}
      title={path}
      className="inline-flex max-w-full min-w-0 items-center gap-1 text-left text-[12px] text-accent-soft hover:underline"
    >
      <FileText size={12} className="shrink-0" aria-hidden />
      <span className="min-w-0 [overflow-wrap:anywhere]">{baseName(path)}</span>
    </button>
  );
}

export function RecordingCard({
  card: initial,
  onSettle,
  scope,
}: {
  card: Card;
  /** Store the card's new state on the message (so a reopened chat shows it). */
  onSettle: (next: Card) => void;
  /** The chat this card is in, read at the press (its id and Local only). */
  scope?: () => RecordingScope;
}) {
  const [card, setCard] = useState<Card>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const settled = useRef(onSettle);
  settled.current = onSettle;
  const card0 = useRef(card);
  card0.current = card;

  // `initial` is read once: the card then follows its job (a reopened chat
  // mounts it again from the message, where each settled state is kept).

  const working = stillWorking(card);
  const id = card.recording_id;

  // Read the job again while it works, only while the page is on screen.
  useEffect(() => {
    if (!working) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      if (stopped) return;
      if (typeof document === "undefined" || document.visibilityState === "visible") {
        try {
          const fresh = decodeRecordingCard(await get<unknown>(`/documents/recording/${encodeURIComponent(id)}`));
          if (!stopped && fresh) {
            setCard(fresh);
            if (!stillWorking(fresh)) {
              settled.current(fresh);
              return;
            }
          }
        } catch (err) {
          // Its record is gone (kept 30 days): say so and stop reading. Any
          // other miss is retried on the next tick; the card keeps its words.
          if ((err as { status?: unknown })?.status === 404) {
            const gone: Card = {
              ...card0.current,
              status: "failed",
              error: `This recording's record is no longer kept. Its transcript and notes, if they were made, are in the chat's folder.`,
            };
            if (!stopped) setCard(gone);
            return;
          }
        }
      }
      if (!stopped) timer = setTimeout(() => void tick(), RECORDING_POLL_MS);
    };
    timer = setTimeout(() => void tick(), RECORDING_POLL_MS);
    const onShow = () => {
      if (document.visibilityState === "visible" && timer == null) void tick();
    };
    document.addEventListener("visibilitychange", onShow);
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onShow);
    };
  }, [id, working]);

  async function writeNotes() {
    if (!card.offer) return;
    setBusy(true);
    setError("");
    setNote("");
    try {
      const res = await post<{ ok?: boolean; changed?: boolean; why?: string; card?: unknown }>(
        `/documents/recording/${encodeURIComponent(id)}/notes`,
        notesBody(card, scope?.() ?? null),
      );
      const fresh = decodeRecordingCard(res.card);
      if (res.changed && fresh?.offer) {
        setNote(`Nothing was sent. The notes would now be written with ${fresh.offer.name}.`);
      } else if (!res.ok && res.why) {
        setNote(res.why);
      }
      if (fresh) {
        setCard(fresh);
        settled.current(fresh);
      }
    } catch (err) {
      setError(errorWords(err, "The notes were not started. Nothing was sent."));
    } finally {
      setBusy(false);
    }
  }

  const head = (
    <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
      <span className="inline-flex min-w-0 max-w-full items-center gap-1.5 font-semibold text-zinc-100">
        <AudioLines size={14} className="shrink-0 text-accent-soft" aria-hidden />
        <span className="min-w-0 [overflow-wrap:anywhere]">{card.source_name}</span>
      </span>
      {card.length_words && <span className="text-[12px] text-zinc-500">{card.length_words}</span>}
      {card.status === "done" && card.counts_words && (
        <span data-testid="recording-counts" className="text-[12px] text-zinc-400">
          {card.counts_words}
        </span>
      )}
    </div>
  );

  if (card.status === "undone") {
    return (
      <div data-testid="recording-card-undone" className="min-w-0 text-[12px] text-zinc-500 [overflow-wrap:anywhere]">
        Undone — the transcript and notes of {card.source_name} were taken back.
      </div>
    );
  }

  return (
    <div
      data-testid="recording-card"
      data-status={card.status}
      className="w-full max-w-2xl space-y-2 rounded-xl border border-white/[0.1] bg-white/[0.02] p-3 text-[13px]"
    >
      {head}
      {card.transcriber_note && (
        <p className="text-[12px] text-zinc-400 [overflow-wrap:anywhere]">{card.transcriber_note}</p>
      )}
      {working && (
        <div data-testid="recording-working" className="space-y-1.5">
          <p className="text-[12px] text-zinc-300 [overflow-wrap:anywhere]">{workingWords(card)}</p>
          {card.status === "hearing" && card.total_s > 0 && (
            <div
              className="h-1 w-full overflow-hidden rounded-full bg-white/[0.06]"
              role="progressbar"
              aria-label={workingWords(card)}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(progressShare(card) * 100)}
            >
              <div className="h-full bg-accent/60" style={{ width: `${Math.round(progressShare(card) * 100)}%` }} />
            </div>
          )}
        </div>
      )}
      {card.status === "failed" && (
        <p role="alert" data-testid="recording-error" className="text-[12px] text-tone-danger [overflow-wrap:anywhere]">
          <TriangleAlert size={12} className="mr-1 inline align-[-1px]" aria-hidden />
          {card.error || `${card.source_name} could not be turned into text.`}
        </p>
      )}
      {card.status === "done" && card.notes && (
        <div data-testid="recording-notes" className="space-y-2">
          {RECORDING_KINDS.map((kind) =>
            card.items[kind].length ? (
              <section key={kind} className="min-w-0">
                <h4 className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">{KIND_HEADINGS[kind]}</h4>
                <ul className="mt-0.5 space-y-0.5">
                  {card.items[kind].map((it, i) => (
                    <li key={`${kind}-${i}`} className="flex min-w-0 flex-wrap gap-x-1.5 text-[13px] text-zinc-200">
                      <span className="shrink-0 tabular-nums text-zinc-500">[{it.time}]</span>
                      <span className="min-w-0 [overflow-wrap:anywhere]">{it.note}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null,
          )}
          {card.dropped_words && (
            <p data-testid="recording-dropped" className="text-[12px] text-zinc-400 [overflow-wrap:anywhere]">
              {card.dropped_words}
            </p>
          )}
        </div>
      )}
      {card.status === "ask" && card.offer && (
        <div data-testid="recording-offer" className="flex flex-wrap items-center gap-2">
          <p className="min-w-0 flex-1 basis-64 text-[12px] text-zinc-300 [overflow-wrap:anywhere]">
            The transcript is ready. {offerWords(card.offer)}
          </p>
          <Button variant="primary" size="sm" onClick={() => void writeNotes()} disabled={busy} data-testid="recording-write-notes">
            Write the notes
          </Button>
        </div>
      )}
      {card.status === "done" && card.notes_why && (
        <p className="text-[12px] text-zinc-400 [overflow-wrap:anywhere]">{card.notes_why}</p>
      )}
      {card.cut_words && <p className="text-[12px] text-zinc-400 [overflow-wrap:anywhere]">{card.cut_words}</p>}
      {(card.transcript || card.notes) && (
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          {card.transcript && <FileLink path={card.transcript} />}
          {card.notes && <FileLink path={card.notes} />}
        </div>
      )}
      {(card.status === "done" || card.status === "ask") && (
        <p className="text-[11px] text-zinc-500 [overflow-wrap:anywhere]">
          {card.made_words}
          {card.notes ? ` ${card.who_owes_words}` : ""}
        </p>
      )}
      {note && <p className="text-[12px] text-zinc-300 [overflow-wrap:anywhere]">{note}</p>}
      {error && (
        <p role="alert" className="text-[12px] text-tone-danger">
          {error}
        </p>
      )}
    </div>
  );
}
