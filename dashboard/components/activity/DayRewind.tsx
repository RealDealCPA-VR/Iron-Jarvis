"use client";

/**
 * YOUR DAY (next leaps 40-a, master plan R2.18, idea 40 "Rewind the day"):
 * the Activity page's second tab. One day of everything Iron Jarvis changed
 * (files, settings, keys, records, memory notes), each with its before and
 * after and Undo.
 *
 *  - A date picker (Previous day / the date / Next day); today by default.
 *  - "Can't be undone" first: what has no Undo (a message sent, commands
 *    run, what Iron Jarvis keeps no way back for), each with its own why.
 *  - Then one section per place the changes came from (a chat, an agent run,
 *    a schedule, a coding session, a settings page), split by hour, newest
 *    first. Each row: what changed, when, Before and after, Undo (two
 *    presses, like the Timeline's).
 *  - Tick several and the ONE main button is **Undo these (3)**: one
 *    confirmation that lists every change by name (D-40.1), then they go
 *    back newest first; the result says how many were put back and names
 *    each that was not, with why.
 *  - Calm (G7): read when the day is opened; while TODAY is on screen a new
 *    change or Undo refreshes it, at most every few seconds. Nothing moves
 *    while idle. At 390 px every row stacks and wraps (G11).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { CalendarDays, ChevronLeft, ChevronRight, Undo2 } from "lucide-react";
import { Button, Empty, ErrorNote, LoaderInline, SkeletonRows, SuccessNote, WarnNote } from "@/components/ui";
import { IconButton } from "@/components/IconButton";
import { Modal } from "@/components/Modal";
import { BeforeAfter } from "@/components/activity/BeforeAfter";
import { useEvents } from "@/lib/useEvents";
import {
  byHour,
  changeName,
  DAY_EVENT_TYPES,
  DAY_REFRESH_MS,
  hourWords,
  readDay,
  shiftDay,
  todayLocal,
  undoMany,
  UNDO_MANY_MAX,
  type DayChange,
  type DayChanges,
  type DaySource,
  type UndoManyResult,
} from "@/lib/dayChanges";

/** Each kind of place, in one word (VOCABULARY.md). */
const SOURCE_WORDS: Record<DaySource["kind"], string> = {
  chat: "Chat",
  run: "Agent run",
  schedule: "Schedule",
  coding: "Coding session",
  settings: "Settings",
  other: "",
};

export const NOTHING_LINE = "Iron Jarvis changed nothing on this day.";
// 40-a review: true of every row under it; each row says its own reason
// (a terminal's line stayed on this PC; a message sent left it).
export const CANNOT_LINE = "There is no way back for these. Each says why.";

function SourceHead({ source, count }: { source: DaySource; count: number }) {
  const kind = SOURCE_WORDS[source.kind];
  const name = source.name;
  return (
    <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
      {source.href ? (
        <Link href={source.href} className="min-w-0 text-sm font-medium text-zinc-100 hover:underline [overflow-wrap:anywhere]">
          {name}
        </Link>
      ) : (
        <span className="min-w-0 text-sm font-medium text-zinc-100 [overflow-wrap:anywhere]">{name}</span>
      )}
      {kind && source.kind !== "chat" && source.kind !== "settings" && (
        <span className="text-[12px] text-zinc-500">{kind}</span>
      )}
      <span className="text-[12px] text-zinc-500">
        {count} change{count === 1 ? "" : "s"}
      </span>
    </div>
  );
}

function ChangeRow({
  change,
  ticked,
  onTick,
  open,
  onOpen,
  armed,
  busy,
  onUndo,
}: {
  change: DayChange;
  ticked: boolean;
  onTick: (on: boolean) => void;
  open: boolean;
  onOpen: () => void;
  armed: boolean;
  busy: boolean;
  onUndo: () => void;
}) {
  const name = changeName(change);
  return (
    <li data-testid="day-change" data-state={change.state} className="flex min-w-0 flex-col gap-1.5 py-2">
      <div className="flex min-w-0 flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
        <label className="flex min-w-0 flex-1 items-start gap-2">
          {change.state === "can" ? (
            <input
              type="checkbox"
              data-testid="day-change-tick"
              aria-label={`Pick ${name}`}
              checked={ticked}
              onChange={(e) => onTick(e.target.checked)}
              className="mt-0.5 shrink-0 accent-accent"
            />
          ) : (
            <span aria-hidden className="mt-0.5 inline-block w-[13px] shrink-0" />
          )}
          <span className="min-w-0 text-[13px] text-zinc-200 [overflow-wrap:anywhere]">{change.what}</span>
        </label>
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 pl-[21px] sm:pl-0">
          {change.clock && <span className="text-[12px] text-zinc-500">{change.clock}</span>}
          {change.state === "undone" && <span className="text-[12px] text-zinc-500">Undone</span>}
          {change.state === "cannot" && (
            <span data-testid="day-change-cannot" className="text-[12px] text-tone-warn [overflow-wrap:anywhere]">
              Can&apos;t be undone: {change.why}
            </span>
          )}
          {change.hasBeforeAfter && (
            <button
              type="button"
              data-testid="day-change-open"
              aria-expanded={open}
              onClick={onOpen}
              className="text-[12px] text-zinc-400 hover:text-zinc-100"
            >
              Before and after
            </button>
          )}
          {change.state === "can" && (
            <button
              type="button"
              data-testid="day-change-undo"
              disabled={busy}
              onClick={onUndo}
              className={`inline-flex items-center gap-1 text-[12px] disabled:opacity-50 ${
                armed ? "font-medium text-tone-warn" : "text-zinc-400 hover:text-zinc-100"
              }`}
            >
              {busy ? <LoaderInline label="Undoing…" /> : <><Undo2 size={12} aria-hidden /> {armed ? "Confirm undo?" : "Undo"}</>}
            </button>
          )}
        </div>
      </div>
      {open && (
        <div className="min-w-0 pl-0 sm:pl-[21px]">
          <BeforeAfter id={change.id} />
        </div>
      )}
    </li>
  );
}

export function DayRewind({ date, onDate }: { date: string; onDate: (next: string) => void }) {
  const today = todayLocal();
  const [day, setDay] = useState<DayChanges | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ticked, setTicked] = useState<Set<string>>(() => new Set());
  const [opened, setOpened] = useState<Set<string>>(() => new Set());
  const [armed, setArmed] = useState<string | null>(null);
  const [undoing, setUndoing] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<UndoManyResult | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const seq = useRef(0);

  const load = useCallback(async () => {
    const mine = ++seq.current;
    try {
      const next = await readDay(date);
      if (mine !== seq.current) return;
      setDay(next);
      setError(null);
      // A ticked change that cannot be undone any more is no longer ticked.
      const can = new Set(next.groups.flatMap((g) => g.changes.filter((c) => c.state === "can").map((c) => c.id)));
      setTicked((prev) => new Set([...prev].filter((id) => can.has(id))));
    } catch (e: unknown) {
      if (mine !== seq.current) return;
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [date]);

  useEffect(() => {
    setDay(null);
    setTicked(new Set());
    setOpened(new Set());
    setResult(null);
    setFailed(null);
    void load();
  }, [load]);

  // G7: while TODAY is on screen, a new change or an Undo made elsewhere
  // refreshes the list, at most once every DAY_REFRESH_MS. An older day
  // never refreshes by itself.
  const { events } = useEvents(20, { types: [...DAY_EVENT_TYPES] });
  const lastSeen = useRef<string | null>(null);
  useEffect(() => {
    const newest = events[0]?.id ?? null;
    if (lastSeen.current === null) {
      lastSeen.current = newest ?? "";
      return;
    }
    if (!newest || newest === lastSeen.current || date !== today) return;
    lastSeen.current = newest;
    const t = setTimeout(() => void load(), DAY_REFRESH_MS);
    return () => clearTimeout(t);
  }, [events, date, today, load]);

  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(null), 3500);
    return () => clearTimeout(t);
  }, [armed]);

  const all = useMemo(() => (day ? day.groups.flatMap((g) => g.changes) : []), [day]);
  const picked = useMemo(() => all.filter((c) => ticked.has(c.id)), [all, ticked]);

  const tick = (id: string, on: boolean) =>
    setTicked((prev) => {
      const next = new Set(prev);
      if (on && next.size < UNDO_MANY_MAX) next.add(id);
      else next.delete(id);
      return next;
    });
  const toggleOpen = (id: string) =>
    setOpened((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  async function putBack(ids: string[]) {
    setFailed(null);
    setResult(null);
    try {
      const out = await undoMany(ids);
      setResult(out);
      setTicked(new Set());
    } catch (e: unknown) {
      setFailed(e instanceof Error ? e.message : String(e));
    } finally {
      await load();
    }
  }

  async function undoOne(id: string) {
    if (armed !== id) {
      setArmed(id);
      return;
    }
    setArmed(null);
    setUndoing(id);
    try {
      await putBack([id]);
    } finally {
      setUndoing(null);
    }
  }

  async function undoPicked() {
    setUndoing("many");
    try {
      await putBack(picked.map((c) => c.id));
    } finally {
      setUndoing(null);
      setConfirming(false);
    }
  }

  return (
    <div data-testid="day-rewind" className="flex min-w-0 flex-col gap-4">
      <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <IconButton
            label="Previous day"
            testId="day-previous"
            icon={<ChevronLeft size={15} aria-hidden />}
            onClick={() => onDate(shiftDay(date, -1))}
          />
          <label className="inline-flex items-center gap-1.5 text-[13px] text-zinc-300">
            <CalendarDays size={14} aria-hidden className="text-zinc-500" />
            <span className="sr-only">Day</span>
            <input
              type="date"
              data-testid="day-date"
              value={date}
              max={today}
              onChange={(e) => {
                if (/^\d{4}-\d{2}-\d{2}$/.test(e.target.value)) onDate(e.target.value);
              }}
              className="rounded-md border border-white/10 bg-transparent px-2 py-1 text-[13px] text-zinc-200"
            />
          </label>
          <IconButton
            label="Next day"
            testId="day-next"
            icon={<ChevronRight size={15} aria-hidden />}
            disabled={date >= today}
            onClick={() => onDate(shiftDay(date, 1))}
          />
          {day?.words && <span className="text-[13px] text-zinc-400">{day.today ? `Today, ${day.words}` : day.words}</span>}
        </div>
        {picked.length > 0 && (
          <Button
            variant="primary"
            size="sm"
            data-testid="day-undo-these"
            disabled={undoing !== null}
            onClick={() => setConfirming(true)}
          >
            Undo these ({picked.length})
          </Button>
        )}
      </div>
      {picked.length >= UNDO_MANY_MAX && (
        <p data-testid="day-tick-cap" className="text-[12px] text-zinc-500">
          Up to {UNDO_MANY_MAX} changes go back at once; undo these first, then pick more.
        </p>
      )}

      {result && (
        result.skipped.length ? (
          <WarnNote>
            <span data-testid="day-result" className="[overflow-wrap:anywhere]">{result.words}</span>
          </WarnNote>
        ) : (
          <SuccessNote>
            <span data-testid="day-result" className="[overflow-wrap:anywhere]">{result.words}</span>
          </SuccessNote>
        )
      )}
      {failed && <ErrorNote>Nothing was undone: {failed}</ErrorNote>}
      {error && <ErrorNote>This day could not be read: {error}</ErrorNote>}
      {!day && !error && <SkeletonRows rows={4} />}

      {day && day.cannot.length > 0 && (
        <section data-testid="day-cannot" className="min-w-0 rounded-xl border border-white/[0.06] p-3">
          <h3 className="text-sm font-medium text-zinc-100">Can&apos;t be undone</h3>
          <p className="mt-0.5 text-[12px] text-zinc-500">{CANNOT_LINE}</p>
          <ul className="mt-2 flex min-w-0 flex-col divide-y divide-white/[0.04]">
            {day.cannot.map((c) => (
              <li key={c.id} data-testid="day-cannot-row" className="flex min-w-0 flex-col gap-0.5 py-1.5 sm:flex-row sm:items-baseline sm:gap-3">
                <span className="min-w-0 text-[13px] text-zinc-200 [overflow-wrap:anywhere]">{c.what}</span>
                <span className="min-w-0 text-[12px] text-zinc-500 [overflow-wrap:anywhere]">
                  {[
                    c.count > 1 && c.firstClock && c.firstClock !== c.clock ? `${c.firstClock} to ${c.clock}` : c.clock,
                    c.source?.name,
                    c.why,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {day && day.groups.length === 0 && day.cannot.length === 0 && (
        <Empty icon={<CalendarDays size={22} aria-hidden />}>{NOTHING_LINE}</Empty>
      )}

      {day?.groups.map((g) => (
        <section key={g.source.id || g.source.name} data-testid="day-group" className="min-w-0">
          <SourceHead source={g.source} count={g.changes.length} />
          {byHour(g.changes).map((h) => (
            <div key={`${g.source.id}-${h.hour}`} className="mt-1 min-w-0">
              <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">{hourWords(h.hour)}</div>
              <ul className="flex min-w-0 flex-col divide-y divide-white/[0.04]">
                {h.changes.map((c) => (
                  <ChangeRow
                    key={c.id}
                    change={c}
                    ticked={ticked.has(c.id)}
                    onTick={(on) => tick(c.id, on)}
                    open={opened.has(c.id)}
                    onOpen={() => toggleOpen(c.id)}
                    armed={armed === c.id}
                    busy={undoing === c.id}
                    onUndo={() => void undoOne(c.id)}
                  />
                ))}
              </ul>
            </div>
          ))}
        </section>
      ))}

      {day?.more && (
        <p className="text-[12px] text-zinc-500">This day holds more changes than are shown; the newest are here.</p>
      )}

      {confirming && (
        <Modal label="Undo these changes" onClose={() => setConfirming(false)} busy={undoing === "many"} testId="day-confirm" className="w-full max-w-lg">
          <div className="flex min-w-0 flex-col gap-3 p-5">
            <h2 className="text-base font-medium text-zinc-100">
              Undo {picked.length === 1 ? "this change" : `these ${picked.length} changes`}?
            </h2>
            <ul data-testid="day-confirm-list" className="flex max-h-72 min-w-0 list-disc flex-col gap-1 overflow-y-auto pl-5 text-[13px] text-zinc-300">
              {picked.map((c) => (
                <li key={c.id} className="[overflow-wrap:anywhere]">
                  {changeName(c)}
                  {c.clock ? <span className="text-zinc-500"> · {c.clock}</span> : null}
                </li>
              ))}
            </ul>
            <p className="text-[12px] text-zinc-500">
              They go back newest first. A change that was edited again since is left as it is, and named.
            </p>
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="secondary" size="sm" data-testid="day-confirm-cancel" disabled={undoing === "many"} onClick={() => setConfirming(false)}>
                Cancel
              </Button>
              <Button variant="primary" size="sm" data-testid="day-confirm-go" disabled={undoing === "many"} onClick={() => void undoPicked()}>
                {undoing === "many" ? <LoaderInline label="Undoing…" /> : "Undo these"}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

export default DayRewind;
