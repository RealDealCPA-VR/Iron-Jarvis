"use client";

/**
 * The Improvements page's list (v1.495.0, vision plan leap 5, Phase 1): every
 * suggestion Iron Jarvis is waiting on the user to accept or decline, from
 * every place that keeps one, grouped by what it is.
 *
 * It decides nothing by itself. Each item carries its own Accept and Decline
 * (the address the suggestion's own screen presses, lib/improvements.ts
 * checks it is one of those), so an answer given here is exactly the answer
 * given there, with the same refusals in the same words. When that route
 * answers with a note of its own (a habit saved while team notes are off),
 * the note is what the page says.
 *
 * Reads: once when shown, when the window is looked at again, after an
 * answer or a refusal that means the suggestion changed, and when Iron
 * Jarvis says a new suggestion was made. Never on a clock.
 *
 * 17-a (the night review): at the top, the ONE-TIME OFFER while the owner has
 * not answered it (nothing runs until yes; the answer is the setting itself,
 * through PUT /settings), and last night's line only when it made something
 * that still waits or was skipped for a reason only the owner can change. A
 * suggestion the review made says why, under its title.
 *
 * 19-b: an offer to run a repeated task by itself has a quiet Rehearse
 * beside Accept: it runs 06-a's rehearsal of the schedule once, changing
 * nothing and making no schedule, and the report shows on the card (no
 * Keep it: Accept makes the schedule).
 *
 * 18-a (results): a second part under the waiting ones, "Results of changes
 * you accepted", from the same read (`results` on GET /improvements): each
 * accepted change's before and after in the daemon's own words. No button.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Lightbulb } from "lucide-react";
import { get, post, put } from "@/lib/api";
import { diffLines } from "@/lib/diff";
import { failureOf, type FailureLike } from "@/lib/loadFailed";
import { useEvents } from "@/lib/useEvents";
import { LoadFailedLine } from "@/components/LoadFailedLine";
import { Empty } from "@/components/ui";
import { RehearsalReport, decodeRehearsal, type RehearsalView } from "@/components/RehearsalReport";
import {
  CUT_NOTE,
  IMPROVEMENTS_PATH,
  IMPROVEMENT_EVENT_TYPES,
  NIGHT_OFFER_NO,
  NIGHT_OFFER_OFF,
  NIGHT_OFFER_ON,
  NIGHT_OFFER_TITLE,
  NIGHT_OFFER_YES,
  NIGHT_REVIEW_PATH,
  NIGHT_REVIEW_SETTING,
  NOTHING_WAITING,
  NOTHING_WAITING_TITLE,
  OFFER_ONLY_NOTE,
  RESULTS_NOTE,
  RESULTS_TITLE,
  TAKEN_OFF,
  acceptedWords,
  answerNote,
  decodeInbox,
  doorNav,
  noticeDoor,
  decodeNightReview,
  groupsToShow,
  nightOfferWords,
  offerRehearsalId,
  offerRehearse,
  nightWhy,
  waitingWords,
  type ChangeResult,
  type ImprovementAction,
  type ImprovementDetail,
  type ImprovementItem,
  type ImprovementsInbox as Inbox,
  type NightOffer,
  type NightReview,
} from "@/lib/improvements";

/** A focus read is skipped when the list was read this recently. */
export const FOCUS_REREAD_MS = 5000;
/** Several events in a burst are one read. */
export const EVENT_REREAD_MS = 800;
/** A two-press button goes back to one press after this long. */
export const ARMED_MS = 4000;
/** The second press of a two-press button counts only after this long, so a
 *  double click never passes for two presses. */
export const SECOND_PRESS_AFTER_MS = 400;
export const PRESS_AGAIN = "Press again to confirm";
export const DID_NOT_WORK = "That did not work. Try again.";

function DetailBody({ detail }: { detail: ImprovementDetail }) {
  const rows = useMemo(() => {
    if (detail.mode !== "diff") return null;
    const lines = diffLines(detail.before ? detail.before.split("\n") : [], detail.after.split("\n"));
    return lines.filter((l, i, all) => !(i === all.length - 1 && l.kind === "same" && !l.text));
  }, [detail]);
  return (
    <>
      <pre
        data-testid="improvement-detail-body"
        data-mode={detail.mode}
        className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-white/[0.03] px-3 py-2 text-[12px] leading-relaxed text-zinc-300"
      >
        {rows
          ? rows.map((l, i) => (
              <div
                key={i}
                className={
                  l.kind === "added"
                    ? "text-tone-success"
                    : l.kind === "removed"
                      ? "text-tone-danger line-through"
                      : "text-zinc-500"
                }
              >
                {l.kind === "added" ? "+ " : l.kind === "removed" ? "- " : "  "}
                {l.text}
              </div>
            ))
          : detail.after}
      </pre>
      {detail.cut ? <p className="pt-1 text-[12px] text-zinc-500">{CUT_NOTE}</p> : null}
    </>
  );
}

/** The whole text, folded: it is only drawn once opened. */
function Detail({ detail }: { detail: ImprovementDetail }) {
  const [open, setOpen] = useState(false);
  return (
    <details
      data-testid="improvement-detail"
      className="text-[12px] text-zinc-400"
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
    >
      <summary className="cursor-pointer select-none py-0.5 hover:text-zinc-200">
        {detail.mode === "diff" ? "See the change" : "Read it"}
        {detail.label ? `: ${detail.label}` : ""}
      </summary>
      {open ? <DetailBody detail={detail} /> : null}
    </details>
  );
}

const ACCEPT_CLASS = "btn-accent text-xs";
const DECLINE_CLASS =
  "rounded-md px-2 py-1 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-40";

/** One decision. A `confirm` one takes two presses, and a double click is
 *  one press. */
function PressButton({
  action,
  testId,
  className,
  busy,
  onPress,
}: {
  action: ImprovementAction;
  testId: string;
  className: string;
  busy: boolean;
  onPress: () => void;
}) {
  const [armed, setArmed] = useState(false);
  const armedAt = useRef(0);
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), ARMED_MS);
    return () => clearTimeout(t);
  }, [armed]);
  return (
    <button
      type="button"
      data-testid={testId}
      data-armed={armed ? "true" : "false"}
      disabled={busy || !!action.blocked}
      title={action.blocked || action.note || undefined}
      onClick={() => {
        if (action.confirm && !armed) {
          armedAt.current = Date.now();
          setArmed(true);
          return;
        }
        if (action.confirm && Date.now() - armedAt.current < SECOND_PRESS_AFTER_MS) return;
        setArmed(false);
        onPress();
      }}
      className={className}
    >
      {armed ? PRESS_AGAIN : action.label}
    </button>
  );
}

function ImprovementCard({
  item,
  onDecided,
  onChanged,
}: {
  item: ImprovementItem;
  onDecided: (item: ImprovementItem, accepted: boolean, said: string, stopped?: boolean) => void;
  /** The route says this suggestion is gone or already answered. */
  onChanged: (words: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function press(action: ImprovementAction, accepted: boolean, stopped = false) {
    setBusy(true);
    setError(null);
    try {
      const answer = await post<unknown>(action.path, action.body);
      onDecided(item, accepted, answerNote(answer), stopped);
      // 03-a: a Yes that starts nothing opens a new chat in that folder.
      const door = accepted ? noticeDoor(answer) : "";
      if (door) doorNav.go(door);
    } catch (e) {
      // The suggestion's own route refused: its sentence, as it said it.
      const words = e instanceof Error && e.message ? e.message : DID_NOT_WORK;
      setError(words);
      const status = failureOf(e).status;
      // Gone, or answered somewhere else: the list is out of date.
      if (status === 404 || status === 409) onChanged(words);
    } finally {
      setBusy(false);
    }
  }

  // 19-b: Rehearse on an offered schedule (runs only when pressed).
  const rehearse = offerRehearse(item);
  const [rehearsal, setRehearsal] = useState<{ id: string; view: RehearsalView | null } | null>(() => {
    const id = offerRehearsalId(item);
    return id ? { id, view: null } : null;
  });
  const [rehearsing, setRehearsing] = useState(false);

  async function startRehearsal() {
    if (!rehearse) return;
    setRehearsing(true);
    setError(null);
    try {
      const view = decodeRehearsal(await post<unknown>(rehearse.path, {}));
      if (view) setRehearsal({ id: view.id, view });
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : DID_NOT_WORK);
      const status = failureOf(e).status;
      if (status === 404 || status === 409) onChanged(e instanceof Error && e.message ? e.message : DID_NOT_WORK);
    } finally {
      setRehearsing(false);
    }
  }

  const night = nightWhy(item);
  return (
    <li
      data-testid={`improvement-${item.id}`}
      data-kind={item.kind}
      className="space-y-2 rounded-2xl bg-white/[0.04] px-4 py-3"
    >
      <p className="text-[14px] leading-snug text-zinc-100 [overflow-wrap:anywhere]">{item.title}</p>
      {night ? (
        <p data-testid="improvement-night-why" className="text-[12px] text-zinc-400">
          {night}
        </p>
      ) : null}
      {item.why ? <p className="text-[13px] leading-relaxed text-zinc-300">{item.why}</p> : null}
      {item.evidence.length ? (
        // Evidence can be the owner's own words (20-a): a path or a link is
        // one long word, so it wraps anywhere rather than widen the page.
        <ul data-testid="improvement-evidence" className="space-y-0.5 text-[12px] text-zinc-400 [overflow-wrap:anywhere]">
          {item.evidence.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      ) : null}
      {item.detail ? <Detail detail={item.detail} /> : null}
      {item.accept?.blocked ? (
        <p data-testid="improvement-blocked" className="text-[12px] text-tone-warn">
          {item.accept.blocked}
        </p>
      ) : item.accept?.note ? (
        <p data-testid="improvement-does" className="text-[12px] text-zinc-500">
          {item.accept.note}
        </p>
      ) : null}
      {!item.accept && !item.decline ? (
        <p data-testid="improvement-offer-only" className="text-[12px] text-zinc-500">
          {OFFER_ONLY_NOTE}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        {item.accept ? (
          <PressButton
            action={item.accept}
            testId="improvement-accept"
            className={ACCEPT_CLASS}
            busy={busy}
            onPress={() => void press(item.accept!, true)}
          />
        ) : null}
        {item.decline ? (
          <PressButton
            action={item.decline}
            testId="improvement-decline"
            className={DECLINE_CLASS}
            busy={busy}
            onPress={() => void press(item.decline!, false)}
          />
        ) : null}
        {item.stop ? (
          // 03-a: the third answer, as quiet as No.
          <PressButton
            action={item.stop}
            testId="improvement-stop"
            className={DECLINE_CLASS}
            busy={busy}
            onPress={() => void press(item.stop!, false, true)}
          />
        ) : null}
        {rehearse ? (
          <button
            type="button"
            data-testid="improvement-rehearse"
            disabled={busy || rehearsing}
            title={rehearse.note || undefined}
            onClick={() => void startRehearsal()}
            className={DECLINE_CLASS}
          >
            {rehearse.label}
          </button>
        ) : null}
        {item.home ? (
          <Link
            href={item.home.href}
            data-testid="improvement-home"
            className="rounded-md px-2 py-1 text-[12px] text-accent-soft underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
          >
            {item.home.label}
          </Link>
        ) : null}
      </div>
      {rehearsal ? (
        <RehearsalReport
          key={rehearsal.id}
          id={rehearsal.id}
          initial={rehearsal.view}
          keepable={false}
          onClose={() => setRehearsal(null)}
        />
      ) : null}
      {error ? (
        <p role="alert" data-testid="improvement-refused" className="text-[12px] text-tone-danger">
          {error}
        </p>
      ) : null}
    </li>
  );
}

/** 17-a: the night review's one-time offer. One main button; "No thanks" is
 *  an answer too (the offer is not shown again). Either answer is the
 *  setting itself, so Settings shows it and can change it back. */
function NightOfferCard({ offer, onAnswered }: { offer: NightOffer; onAnswered: (on: boolean) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function answer(on: boolean) {
    setBusy(true);
    setError(null);
    try {
      await put("/settings", { values: { [NIGHT_REVIEW_SETTING]: on } });
      onAnswered(on);
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : DID_NOT_WORK);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      data-testid="improvements-night-offer"
      aria-label={NIGHT_OFFER_TITLE}
      className="space-y-2 rounded-2xl bg-white/[0.04] px-4 py-3"
    >
      <p className="text-[14px] leading-snug text-zinc-100">{NIGHT_OFFER_TITLE}</p>
      <p className="text-[13px] leading-relaxed text-zinc-300">{nightOfferWords(offer)}</p>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid="improvements-night-yes"
          disabled={busy}
          onClick={() => void answer(true)}
          className={ACCEPT_CLASS}
        >
          {NIGHT_OFFER_YES}
        </button>
        <button
          type="button"
          data-testid="improvements-night-no"
          disabled={busy}
          onClick={() => void answer(false)}
          className={DECLINE_CLASS}
        >
          {NIGHT_OFFER_NO}
        </button>
      </div>
      {error ? (
        <p role="alert" data-testid="improvements-night-refused" className="text-[12px] text-tone-danger">
          {error}
        </p>
      ) : null}
    </section>
  );
}

export function ImprovementsInbox() {
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [night, setNight] = useState<NightReview | null>(null);
  const [failure, setFailure] = useState<FailureLike | null>(null);
  const [gone, setGone] = useState<ReadonlySet<string>>(() => new Set());
  const [note, setNote] = useState("");
  const seq = useRef(0);
  const readAt = useRef(0);
  const here = useRef(true);

  useEffect(() => {
    here.current = true;
    return () => {
      here.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    const mine = ++seq.current;
    readAt.current = Date.now();
    // 17-a: the night review's offer and line, read beside the list. A
    // daemon without it (or a read that fails) shows neither: the list is
    // what this page is for.
    const nightRead = get<unknown>(NIGHT_REVIEW_PATH).then(decodeNightReview, () => null);
    try {
      const raw = await get<unknown>(IMPROVEMENTS_PATH);
      const nightNow = await nightRead;
      if (!here.current || mine !== seq.current) return; // a newer read is out: its answer wins
      setInbox(decodeInbox(raw));
      setNight(nightNow);
      setFailure(null);
      // What the list says now is the truth: an answer that did not take
      // (the suggestion is still there) shows again.
      setGone(new Set());
    } catch (e) {
      if (!here.current || mine !== seq.current) return;
      setFailure(failureOf(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // The window is looked at again: read once, unless it was just read.
  useEffect(() => {
    const again = () => {
      if (document.visibilityState === "hidden") return;
      if (Date.now() - readAt.current < FOCUS_REREAD_MS) return;
      void load();
    };
    window.addEventListener("focus", again);
    document.addEventListener("visibilitychange", again);
    return () => {
      window.removeEventListener("focus", again);
      document.removeEventListener("visibilitychange", again);
    };
  }, [load]);

  // Iron Jarvis made (or settled) a suggestion: read again, a burst as one.
  const { events } = useEvents(5, { types: IMPROVEMENT_EVENT_TYPES });
  const newest = events[0]?.id ?? "";
  useEffect(() => {
    if (!newest) return;
    const t = setTimeout(() => void load(), EVENT_REREAD_MS);
    return () => clearTimeout(t);
  }, [newest, load]);

  const decided = useCallback(
    (item: ImprovementItem, accepted: boolean, said: string, stopped?: boolean) => {
      setGone((prev) => new Set(prev).add(item.id));
      // The route's own note is for an accept (and for 03-a's Stop, which
      // says what it turned off); a decline is just taken off.
      setNote(accepted ? said || acceptedWords(item.kind) : stopped ? said || TAKEN_OFF : TAKEN_OFF);
      void load();
    },
    [load],
  );

  const changed = useCallback(
    (words: string) => {
      setNote(words);
      void load();
    },
    [load],
  );

  const nightAnswered = useCallback(
    (on: boolean) => {
      setNote(on ? NIGHT_OFFER_ON : NIGHT_OFFER_OFF);
      setNight((prev) => (prev ? { ...prev, offer: null } : prev));
      void load();
    },
    [load],
  );

  const groups = useMemo(() => (inbox ? groupsToShow(inbox, gone) : []), [inbox, gone]);
  const waiting = groups.reduce((n, g) => n + g.items.length + g.more, 0);

  return (
    <div data-testid="improvements-inbox" className="space-y-6">
      {/* Always mounted, so a screen reader hears the words when they come. */}
      <p role="status" data-testid="improvements-note" className="min-h-5 text-[13px] text-zinc-300">
        {note}
      </p>

      {failure && !inbox ? (
        <LoadFailedLine thing="Your suggestions" error={failure} onRetry={() => void load()} testId="improvements-failed" />
      ) : null}
      {failure && inbox ? (
        <LoadFailedLine
          thing="The newest suggestions"
          error={failure}
          onRetry={() => void load()}
          testId="improvements-stale"
        />
      ) : null}

      {night?.offer ? <NightOfferCard offer={night.offer} onAnswered={nightAnswered} /> : null}
      {night?.night ? (
        <p data-testid="improvements-night-line" className="text-[13px] text-zinc-300">
          {night.night.line}
        </p>
      ) : null}

      {inbox?.unreadable.length ? (
        <p data-testid="improvements-unreadable" className="notice-warn rounded-lg px-3 py-2 text-[13px]">
          {`Iron Jarvis could not read all of these just now, so some may be missing below: ${inbox.unreadable
            .map((u) => u.label)
            .join(", ")}.`}
        </p>
      ) : null}

      {inbox && waiting > 0 ? (
        <p data-testid="improvements-count" className="text-[13px] text-zinc-400">
          {waitingWords(waiting)}
        </p>
      ) : null}

      {inbox && waiting === 0 && !inbox.unreadable.length ? (
        <Empty icon={<Lightbulb size={18} />} title={NOTHING_WAITING_TITLE}>
          {NOTHING_WAITING}
        </Empty>
      ) : null}

      {groups.map((g) => (
        <section key={g.kind} data-testid={`improvements-group-${g.kind}`} aria-label={g.label} className="space-y-2">
          <h2 className="text-[13px] font-medium text-zinc-300">
            {g.label} <span className="text-zinc-500">· {g.items.length + g.more}</span>
          </h2>
          <ul className="space-y-2">
            {g.items.map((item) => (
              <ImprovementCard key={item.id} item={item} onDecided={decided} onChanged={changed} />
            ))}
          </ul>
          {g.more > 0 ? (
            <p data-testid={`improvements-more-${g.kind}`} className="text-[12px] text-zinc-500">
              {`${g.more} more not shown here. Answer some of these, or open where they live to see them all.`}
            </p>
          ) : null}
        </section>
      ))}

      {inbox?.results.length ? <ResultsPart results={inbox.results} /> : null}
    </div>
  );
}

/** 18-a: the second part, under the waiting suggestions: what happened since
 *  each change the owner accepted, in the daemon's own words. No button:
 *  nothing here changes anything. */
function ResultsPart({ results }: { results: ChangeResult[] }) {
  return (
    <section data-testid="improvements-results" aria-label={RESULTS_TITLE} className="space-y-2">
      <h2 className="text-[13px] font-medium text-zinc-300">
        {RESULTS_TITLE} <span className="text-zinc-500">· {results.length}</span>
      </h2>
      <p className="text-[12px] text-zinc-500">{RESULTS_NOTE}</p>
      <ul className="space-y-2">
        {results.map((r) => (
          <li
            key={r.id}
            data-testid={`improvement-result-${r.id}`}
            data-state={r.state}
            className="space-y-1 rounded-2xl bg-white/[0.04] px-4 py-3"
          >
            <p className="break-words text-[14px] leading-snug text-zinc-100">{r.title}</p>
            <p data-testid="improvement-result-words" className="break-words text-[13px] leading-relaxed text-zinc-300">
              {r.words}
            </p>
            {r.home ? (
              <Link
                href={r.home.href}
                data-testid="improvement-result-home"
                className="inline-block rounded-md py-1 text-[12px] text-accent-soft underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
              >
                {r.home.label}
              </Link>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
