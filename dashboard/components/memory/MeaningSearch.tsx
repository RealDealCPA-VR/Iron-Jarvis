"use client";

/**
 * SEARCH BY MEANING on screen (next leaps 16-a, build-out plan R2.11). Two
 * shapes, one read (`GET /memory/meaning`, lib/meaningSearch):
 *
 * - `MeaningSearchRow`: the PLAIN settings row on File Search and in
 *   Settings › Memory & you (the setting's one control there). It says how it
 *   stands ("Search by meaning: off. It matches words for now.") and, behind a
 *   quiet "Choose a search model", lists every search model found on this PC
 *   or your network with what it costs here. Never an offer of its own.
 * - `MeaningSearchOffer`: the card under the ONE reply whose by-meaning
 *   search matched words (the daemon marks that reply, once ever). It shows
 *   only while the offer was never answered: the model, what it costs here,
 *   and Turn it on (the one main button), Not now and Use another model.
 *
 * Every press goes to the setting's own routes, which go through the
 * settings writer (one ledger row with Undo); the daemon probes the model
 * before it switches. Reads: once when shown, again while a look for models
 * is running (at most LOOK_REREADS_MAX times), never on a clock otherwise.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { get } from "@/lib/api";
import {
  CHOOSE,
  DID_NOT_WORK,
  GET_A_MODEL,
  IN_USE,
  LAST_RESORT,
  LIBRARY_HREF,
  LOOKING,
  LOOK_REREADS_MAX,
  LOOK_REREAD_MS,
  MEANING_PATH,
  NOT_NOW,
  NOT_NOW_SAID,
  OWN_SERVER,
  SERVERS_HREF,
  TURN_OFF,
  TURN_ON,
  USE_ANOTHER,
  USE_THIS,
  chooseMeaning,
  declineMeaning,
  decodeMeaning,
  noteOf,
  turnMeaningOff,
  unanswered,
  type MeaningCandidate,
  type MeaningView,
} from "@/lib/meaningSearch";

const QUIET =
  "rounded-md px-2 py-1 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-40";
const LINK =
  "rounded-md px-2 py-1 text-[12px] text-accent-soft underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50";

/** One read of the state; again while a look runs (bounded, only mounted). */
function useMeaning(all: boolean) {
  const [view, setView] = useState<MeaningView | null>(null);
  const [failed, setFailed] = useState(false);
  const rereads = useRef(0);
  const here = useRef(true);

  const load = useCallback(async () => {
    try {
      const got = decodeMeaning(await get<unknown>(`${MEANING_PATH}${all ? "?all=1" : ""}`));
      if (!here.current) return;
      setView(got);
      setFailed(got === null);
    } catch {
      if (here.current) setFailed(true);
    }
  }, [all]);

  useEffect(() => {
    here.current = true;
    rereads.current = 0;
    void load();
    return () => {
      here.current = false;
    };
  }, [load]);

  useEffect(() => {
    if (!view?.looking || rereads.current >= LOOK_REREADS_MAX) return;
    const t = setTimeout(() => {
      rereads.current += 1;
      void load();
    }, LOOK_REREAD_MS);
    return () => clearTimeout(t);
  }, [view, load]);

  return { view, setView, failed, load };
}

function errorWords(e: unknown): string {
  return e instanceof Error && e.message ? e.message : DID_NOT_WORK;
}

/** The models found, each with what it costs here, and the way to get one. */
function ModelList({
  view,
  busy,
  onChoose,
}: {
  view: MeaningView;
  busy: boolean;
  onChoose: (c: MeaningCandidate) => void;
}) {
  if (!view.candidates.length) {
    return (
      <div data-testid="meaning-none" className="space-y-1.5 text-[12px] text-zinc-400">
        <p>{view.looking ? LOOKING : view.noneFound}</p>
        {!view.looking ? (
          <>
            <div className="flex flex-wrap items-center gap-1">
              <Link href={LIBRARY_HREF} data-testid="meaning-get-model" className={LINK}>
                {GET_A_MODEL}
              </Link>
              <Link href={SERVERS_HREF} data-testid="meaning-own-server" className={LINK}>
                {OWN_SERVER}
              </Link>
            </div>
            {view.terminalLine ? (
              <p className="break-words">
                {LAST_RESORT} <code className="font-mono text-zinc-300">{view.terminalLine}</code>
              </p>
            ) : null}
          </>
        ) : null}
      </div>
    );
  }
  return (
    <ul data-testid="meaning-models" className="space-y-1.5">
      {view.candidates.map((c) => (
        <li
          key={c.choice}
          data-testid="meaning-model"
          className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2"
        >
          <div className="min-w-0 flex-1">
            <p className="break-words text-[13px] text-zinc-200">
              {c.model} <span className="text-zinc-500">{c.whereWords}</span>
            </p>
            <p data-testid="meaning-cost" className="break-words text-[12px] text-zinc-400">
              {c.costWords}
            </p>
          </div>
          {view.current === c.choice ? (
            <span data-testid="meaning-in-use" className="px-2 py-1 text-[12px] text-zinc-500">
              {IN_USE}
            </span>
          ) : (
            <button
              type="button"
              data-testid="meaning-use"
              disabled={busy || c.works === false}
              onClick={() => onChoose(c)}
              className={QUIET}
            >
              {USE_THIS}
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

/** The plain settings row (File Search, Settings › Memory & you). */
export function MeaningSearchRow() {
  const [open, setOpen] = useState(false);
  const { view, failed, load } = useMeaning(open);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  async function press(run: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      setNote(noteOf(await run()));
      setOpen(false);
      await load();
    } catch (e) {
      setError(errorWords(e));
    } finally {
      setBusy(false);
    }
  }

  if (!view) {
    return failed ? null : <p className="text-[12px] text-zinc-500">{LOOKING}</p>;
  }
  return (
    <section data-testid="meaning-row" data-state={view.state} className="space-y-2">
      <p data-testid="meaning-row-words" className="break-words text-[13px] text-zinc-300">
        {view.words}
      </p>
      <div className="flex flex-wrap items-center gap-1">
        <button type="button" data-testid="meaning-choose" disabled={busy} onClick={() => setOpen((o) => !o)} className={QUIET}>
          {view.on ? USE_ANOTHER : CHOOSE}
        </button>
        {view.on ? (
          <button type="button" data-testid="meaning-off" disabled={busy} onClick={() => void press(turnMeaningOff)} className={QUIET}>
            {TURN_OFF}
          </button>
        ) : null}
      </div>
      {open ? <ModelList view={view} busy={busy} onChoose={(c) => void press(() => chooseMeaning(c.choice))} /> : null}
      <p role="status" data-testid="meaning-note" className="break-words text-[12px] text-zinc-400">
        {note}
      </p>
      {error ? (
        <p role="alert" data-testid="meaning-refused" className="break-words text-[12px] text-tone-danger">
          {error}
        </p>
      ) : null}
    </section>
  );
}

/** The card under the one reply that carried the offer. */
export function MeaningSearchOffer() {
  const [another, setAnother] = useState(false);
  const { view, load } = useMeaning(another);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");

  async function press(run: () => Promise<unknown>, fallback = "") {
    setBusy(true);
    setError("");
    try {
      setSaid(noteOf(await run()) || fallback);
    } catch (e) {
      setError(errorWords(e));
      await load();
    } finally {
      setBusy(false);
    }
  }

  // Answered here: the answer's own words stay under the reply.
  if (said) {
    return (
      <p role="status" data-testid="meaning-offer-said" className="mt-2 break-words text-[12px] text-zinc-400">
        {said}
      </p>
    );
  }
  // Answered anywhere else, or an older daemon: nothing under the reply.
  if (!view || !unanswered(view)) return null;
  const best = view.best;
  return (
    <section
      data-testid="meaning-offer"
      aria-label="Search by meaning"
      className="mt-2 max-w-full space-y-2 rounded-2xl bg-white/[0.04] px-4 py-3"
    >
      <p className="break-words text-[13px] text-zinc-100">{view.words}</p>
      {best ? (
        <>
          <p data-testid="meaning-offer-model" className="break-words text-[13px] text-zinc-300">
            {best.model} is already {best.whereWords}.
          </p>
          <p data-testid="meaning-offer-cost" className="break-words text-[12px] text-zinc-400">
            {best.costWords}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              data-testid="meaning-offer-on"
              disabled={busy}
              onClick={() => void press(() => chooseMeaning(best.choice))}
              className="btn-accent text-xs"
            >
              {TURN_ON}
            </button>
            <button
              type="button"
              data-testid="meaning-offer-not-now"
              disabled={busy}
              onClick={() => void press(declineMeaning, NOT_NOW_SAID)}
              className={QUIET}
            >
              {NOT_NOW}
            </button>
            <button
              type="button"
              data-testid="meaning-offer-another"
              disabled={busy}
              onClick={() => setAnother((a) => !a)}
              className={QUIET}
            >
              {USE_ANOTHER}
            </button>
          </div>
        </>
      ) : null}
      {!best || another ? (
        <ModelList view={view} busy={busy} onChoose={(c) => void press(() => chooseMeaning(c.choice))} />
      ) : null}
      {error ? (
        <p role="alert" data-testid="meaning-offer-refused" className="break-words text-[12px] text-tone-danger">
          {error}
        </p>
      ) : null}
    </section>
  );
}
