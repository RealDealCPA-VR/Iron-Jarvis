"use client";

/**
 * A SECOND MODEL double-checks finished work (the intelligence plan, 5b;
 * lib/doubleCheck, the daemon's `checks/second`).
 *
 * - `CheckedWithDoubleCheck`: the reply's one checked line (CheckedLine),
 *   and, while the daemon says the reply's double-check is RUNNING, one quiet
 *   line under it ("Sonnet 4.5 is double-checking this…"). When it lands the
 *   line is the daemon's again, read from its record (the model's verdict
 *   under its own name). Asked every few seconds only while it runs, and at
 *   most RUN_ASKS_MAX times; nothing is said before the first answer.
 * - `DoubleCheckOffer`: the ONE card, under the one reply the daemon marked
 *   (once ever), only while the offer is unanswered: the daemon's own
 *   sentence naming the exact model and account, Yes (the one main button)
 *   and No.
 * - `DoubleCheckRow`: Settings › Models › Second model double-check, the
 *   row's one control: how it stands, and the same yes or Turn off.
 */

import { useEffect, useRef, useState } from "react";
import { CheckedLine } from "@/components/chat/CheckedLine";
import type { CheckedMark } from "@/lib/checked";
import {
  DID_NOT_WORK,
  NO,
  NOTHING_TO_OFFER,
  OFF_WORDS,
  RUN_ASKS_MAX,
  RUN_ASK_MS,
  TURN_OFF,
  YES,
  answerDoubleCheck,
  decodeDoubleCheck,
  readDoubleCheck,
  readRun,
  waitingWords,
  type DoubleCheckView,
} from "@/lib/doubleCheck";

const QUIET =
  "rounded-md px-2 py-1 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-40";

export function CheckedWithDoubleCheck({ mark, doubleCheck }: { mark: CheckedMark | null; doubleCheck?: unknown }) {
  const dc = decodeDoubleCheck(doubleCheck);
  const token = dc?.kind === "running" ? dc.token : "";
  const [latest, setLatest] = useState<CheckedMark | null>(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!token) return;
    let here = true;
    let asked = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ask = async () => {
      asked += 1;
      try {
        const got = await readRun(token);
        if (!here) return;
        if (got.running && asked < RUN_ASKS_MAX) {
          setRunning(true);
          timer = setTimeout(() => void ask(), RUN_ASK_MS);
          return;
        }
        setRunning(false);
        if (got.checked) setLatest(got.checked);
      } catch {
        if (here) setRunning(false);
      }
    };
    void ask();
    return () => {
      here = false;
      if (timer) clearTimeout(timer);
    };
  }, [token]);

  return (
    <>
      <CheckedLine mark={latest ?? mark} />
      {running && dc?.kind === "running" ? (
        <p data-testid="double-check-waiting" role="status" className="mt-1 text-[12px] text-zinc-500">
          {waitingWords(dc.who)}
        </p>
      ) : null}
      {dc?.kind === "offer" ? <DoubleCheckOffer /> : null}
    </>
  );
}

function useView() {
  const [view, setView] = useState<DoubleCheckView | null>(null);
  const here = useRef(true);
  const load = async () => {
    try {
      const got = await readDoubleCheck();
      if (here.current) setView(got);
    } catch {
      if (here.current) setView(null);
    }
  };
  useEffect(() => {
    here.current = true;
    void load();
    return () => {
      here.current = false;
    };
  }, []);
  return { view, setView, load };
}

export function DoubleCheckOffer() {
  const { view, load } = useView();
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  const [error, setError] = useState("");

  async function press(answer: string) {
    setBusy(true);
    setError("");
    try {
      setSaid((await answerDoubleCheck(answer)).note || " ");
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : DID_NOT_WORK);
      await load();
    } finally {
      setBusy(false);
    }
  }

  if (said.trim()) {
    return (
      <p role="status" data-testid="double-check-offer-said" className="mt-2 break-words text-[12px] text-zinc-400">
        {said}
      </p>
    );
  }
  // Answered anywhere else, nothing to offer now, or an older daemon: nothing.
  if (!view || view.state !== "unset" || !view.offer) return null;
  const offer = view.offer;
  return (
    <section
      data-testid="double-check-offer"
      aria-label="Second model double-check"
      className="mt-2 max-w-full space-y-2 rounded-2xl bg-white/[0.04] px-4 py-3"
    >
      <p data-testid="double-check-offer-words" className="break-words text-[13px] text-zinc-100">
        {offer.words}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid="double-check-yes"
          disabled={busy}
          onClick={() => void press(offer.choice)}
          className="btn-accent text-xs"
        >
          {YES}
        </button>
        <button
          type="button"
          data-testid="double-check-no"
          disabled={busy}
          onClick={() => void press("no")}
          className={QUIET}
        >
          {NO}
        </button>
      </div>
      {error ? (
        <p role="alert" data-testid="double-check-error" className="break-words text-[12px] text-tone-warn">
          {error}
        </p>
      ) : null}
    </section>
  );
}

export function DoubleCheckRow() {
  const { view, setView, load } = useView();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function press(answer: string) {
    setBusy(true);
    setError("");
    try {
      const got = await answerDoubleCheck(answer);
      if (got.view) setView(got.view);
      else await load();
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : DID_NOT_WORK);
      await load();
    } finally {
      setBusy(false);
    }
  }

  if (!view) return null;
  return (
    <div data-testid="double-check-row" className="space-y-2 text-[13px]">
      {view.state === "on" && view.choice ? (
        <div className="flex flex-wrap items-center gap-2">
          <p data-testid="double-check-row-words" className="min-w-0 break-words text-zinc-200">
            On: {view.words}.
          </p>
          <button
            type="button"
            data-testid="double-check-off"
            disabled={busy}
            onClick={() => void press("no")}
            className={QUIET}
          >
            {TURN_OFF}
          </button>
        </div>
      ) : view.offer ? (
        <div className="space-y-2">
          {view.state === "off" ? (
            <p data-testid="double-check-row-words" className="text-zinc-400">
              {OFF_WORDS}
            </p>
          ) : null}
          <p data-testid="double-check-offer-words" className="break-words text-zinc-200">
            {view.offer.words}
          </p>
          <button
            type="button"
            data-testid="double-check-yes"
            disabled={busy}
            onClick={() => void press(view.offer!.choice)}
            className="btn-accent text-xs"
          >
            {YES}
          </button>
        </div>
      ) : (
        <p data-testid="double-check-row-words" className="text-zinc-400">
          {view.state === "on" ? view.words : NOTHING_TO_OFFER}
        </p>
      )}
      {error ? (
        <p role="alert" data-testid="double-check-error" className="break-words text-[12px] text-tone-warn">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export default CheckedWithDoubleCheck;
