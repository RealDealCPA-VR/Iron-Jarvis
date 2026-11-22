"use client";

/**
 * FROM MEMORY (next leaps 15-a, master plan R2.7, decision D-15.1).
 *
 * The quiet line "From memory: 3" in a reply's action row, beside the
 * receipt, and the small pop-up it opens: what Iron Jarvis was GIVEN from
 * memory for that reply (lib/memoryUsed). The owner's note: "a temporary
 * modal that disappears". So the pop-up
 *
 * - is drawn in a portal, FIXED beside the line and always inside the
 *   window (at 390 px it is the window's width less 16 px each side);
 * - is `role="status"` and NEVER takes focus: the composer stays the
 *   owner's; Escape closes it (and only it);
 * - fades after {@link SHOW_MS} untouched, and stays while the pointer or
 *   the keyboard focus is on it, or while a correction is being typed;
 * - opens by itself only when the page says so (`autoOpen`: the newest
 *   reply, the chat on screen, memory shaped the reply, items not shown in
 *   this chat yet, and the setting not "Only when I press");
 * - on a kept preference or a client's preference, That's wrong lets the
 *   owner type the right one (Save) or Forget it, through the EXISTING
 *   routes of the store it is kept in; then "Fixed." with Undo (the
 *   journal's own Undo, `POST /undo/{id}`), or says plainly when it cannot.
 *
 * No new panel and no new page: the same list also sits in the reply's
 * details (TurnReceipt's "From memory" lines).
 *
 * The pop-up is drawn over the page (a portal), so a chat that is mounted but
 * hidden (the Code view in front, {@link MemoryUsedHidden}) closes it and
 * never opens it by itself: it belongs to the chat on screen only.
 */

import Link from "next/link";
import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { BookOpen, Loader2, X } from "lucide-react";
import { ApiError, del, patch, post } from "@/lib/api";
import {
  CANCEL,
  CLOSE_WORDS,
  FIXED,
  FORGET,
  FORGOTTEN,
  HEADING,
  HEADING_NOTE,
  NO_UNDO,
  OPEN,
  PUT_BACK,
  RIGHT_PLACEHOLDER,
  SAVE,
  SHOW_MS,
  UNDO,
  WRONG,
  actionIdOf,
  canFix,
  fixPath,
  itemKey,
  itemWords,
  lineWords,
  moreWords,
  popupItems,
  whenWords,
  type MemoryUsedItem,
} from "@/lib/memoryUsed";

const QUIET_BTN =
  "inline-flex min-h-7 items-center gap-1 rounded-md px-1.5 text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50";
const TEXT_BTN =
  "rounded px-1 text-[12px] text-zinc-400 underline decoration-zinc-600 underline-offset-2 transition-colors hover:text-zinc-100 disabled:opacity-50";
/** The pop-up's widest, and how far it keeps from the window's edge. */
const POP_W = 340;
const EDGE = 16;
/** The last part of SHOW_MS, in which the pop-up fades out. */
export const FADE_MS = 400;

type Done = { state: "fixed" | "forgotten" | "undone"; actionId: string };

/** True while the chat this line sits in is mounted but hidden (the chat
 *  page's Code view is in front). The chat page provides it. */
export const MemoryUsedHidden = createContext(false);
export type Place = { left: number; width: number; top?: number; bottom?: number };

function messageOf(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e && typeof e === "object" && typeof (e as { message?: unknown }).message === "string")
    return (e as { message: string }).message;
  return "That did not go through.";
}

/** Where the pop-up sits for its line: beside it, never past the window's
 *  edge (at 390 px: the window's width less 16 px each side). */
export function placeBeside(anchor: HTMLElement | null): Place | null {
  if (!anchor || typeof window === "undefined") return null;
  const r = anchor.getBoundingClientRect();
  const vw = window.innerWidth || 0;
  const vh = window.innerHeight || 0;
  const width = Math.max(0, Math.min(POP_W, vw - EDGE * 2));
  const left = Math.min(Math.max(EDGE, r.left), Math.max(EDGE, vw - width - EDGE));
  // Below the line when there is room for a short list, else above it.
  if (vh - r.bottom >= 220 || r.top < 220) return { left, width, top: Math.min(r.bottom + 6, Math.max(EDGE, vh - EDGE)) };
  return { left, width, bottom: Math.max(EDGE, vh - r.top + 6) };
}

export function MemoryUsed({
  items,
  autoOpen = false,
  busy = false,
  readOnly = false,
  onShown,
  onClosed,
}: {
  items: MemoryUsedItem[];
  /** The page decided this reply's pop-up appears by itself (once). */
  autoOpen?: boolean;
  /** A turn is running: a pop-up open when it started closes, and while it
   *  runs Escape is left to the composer (its Stop), never caught here. */
  busy?: boolean;
  /** An earlier message: listed only, nothing is changed from it. */
  readOnly?: boolean;
  /** The pop-up was shown (by itself or by a press): the page marks it. */
  onShown?: () => void;
  /** The pop-up went away. */
  onClosed?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [fixing, setFixing] = useState<{ key: string; draft: string } | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [done, setDone] = useState<Record<string, Done>>({});
  const [error, setError] = useState<{ key: string; text: string } | null>(null);
  const [place, setPlace] = useState<Place | null>(null);
  const lineRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const remaining = useRef(SHOW_MS);
  const popId = useId();
  const shownRef = useRef(onShown);
  shownRef.current = onShown;
  const closedRef = useRef(onClosed);
  closedRef.current = onClosed;
  const hidden = useContext(MemoryUsedHidden);

  const show = useCallback(() => {
    remaining.current = SHOW_MS;
    setOpen(true);
    setPlace(placeBeside(lineRef.current));
    shownRef.current?.();
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    setFixing(null);
    setHovered(false);
    setFocused(false);
    closedRef.current?.();
  }, []);

  // By itself: once, when the page says so, and only while the chat is in
  // front (a chat hidden behind the Code view lets the moment pass).
  const autoSeen = useRef(false);
  useEffect(() => {
    if (!autoOpen || autoSeen.current || !items.length) return;
    autoSeen.current = true;
    if (!hidden) show();
  }, [autoOpen, items.length, show, hidden]);

  // The chat went behind the Code view: the pop-up over the page goes too.
  useEffect(() => {
    if (hidden && open) close();
  }, [hidden, open, close]);

  // A turn that starts closes it (once, as it starts: pressing the line
  // during the turn still opens it).
  const wasBusy = useRef(busy);
  useEffect(() => {
    if (busy && !wasBusy.current && open) close();
    wasBusy.current = busy;
  }, [busy, open, close]);

  // THE TIMER: SHOW_MS untouched (its last FADE_MS a fade), paused (not
  // restarted) while something holds it — the pointer, the keyboard focus,
  // a correction being typed or a fix on its way. A hold during the fade
  // takes the fade back, and the fade runs whole when the hold ends.
  const held = hovered || focused || pending !== null || !!fixing?.draft.trim();
  const [leaving, setLeaving] = useState(false);
  useEffect(() => {
    setLeaving(false);
    if (!open || held) return;
    const started = Date.now();
    const left = Math.max(FADE_MS, remaining.current);
    const fade = window.setTimeout(() => setLeaving(true), left - FADE_MS);
    const gone = window.setTimeout(close, left);
    return () => {
      window.clearTimeout(fade);
      window.clearTimeout(gone);
      remaining.current = Math.max(FADE_MS, left - (Date.now() - started));
    };
  }, [open, held, close]);

  // Escape closes the pop-up ONLY (caught first); inside a correction it
  // puts the line back first. While a turn runs, Escape is the composer's
  // Stop and is never caught here.
  useEffect(() => {
    if (!open || busy) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.isComposing) return;
      e.stopPropagation();
      if (fixing && popRef.current?.contains(e.target as Node)) {
        setFixing(null);
        return;
      }
      close();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, busy, fixing, close]);

  // Kept beside its line as the chat scrolls or the window changes size.
  useEffect(() => {
    if (!open) return;
    let frame = 0;
    const again = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        setPlace(placeBeside(lineRef.current));
      });
    };
    window.addEventListener("resize", again);
    window.addEventListener("scroll", again, true);
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", again);
      window.removeEventListener("scroll", again, true);
    };
  }, [open]);

  async function fix(it: MemoryUsedItem, how: "correct" | "forget") {
    const key = itemKey(it);
    if (pending) return;
    const text = (fixing?.key === key ? fixing.draft : "").trim();
    if (how === "correct" && !text) return;
    setPending(key);
    setError(null);
    try {
      const path = fixPath(it);
      const res = how === "correct" ? await patch<unknown>(path, { text }) : await del<unknown>(path);
      setDone((d) => ({ ...d, [key]: { state: how === "correct" ? "fixed" : "forgotten", actionId: actionIdOf(res) } }));
      setFixing(null);
    } catch (e) {
      setError({ key, text: messageOf(e) });
    } finally {
      setPending(null);
    }
  }

  async function undo(it: MemoryUsedItem) {
    const key = itemKey(it);
    const d = done[key];
    if (!d?.actionId || pending) return;
    setPending(key);
    setError(null);
    try {
      await post<unknown>(`/undo/${encodeURIComponent(d.actionId)}`, {});
      setDone((all) => ({ ...all, [key]: { state: "undone", actionId: "" } }));
    } catch (e) {
      setError({ key, text: messageOf(e) });
    } finally {
      setPending(null);
    }
  }

  if (!items.length) return null;
  const listed = popupItems(items);
  const more = items.length - listed.length;

  const popup =
    open && typeof document !== "undefined"
      ? createPortal(
          <div
            ref={popRef}
            id={popId}
            role="status"
            aria-live="polite"
            aria-label={HEADING}
            data-testid="memory-used-popup"
            onPointerEnter={() => setHovered(true)}
            onPointerLeave={() => setHovered(false)}
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
            onFocus={() => setFocused(true)}
            onBlur={(e) => {
              if (!popRef.current?.contains(e.relatedTarget as Node | null)) setFocused(false);
            }}
            style={{
              position: "fixed",
              left: place?.left ?? EDGE,
              width: place?.width ?? POP_W,
              maxWidth: `calc(100vw - ${EDGE * 2}px)`,
              ...(place?.bottom !== undefined ? { bottom: place.bottom } : { top: place?.top ?? EDGE }),
              zIndex: 60,
            }}
            data-leaving={leaving ? "true" : "false"}
            className={`max-h-[60vh] overflow-y-auto rounded-xl border border-white/[0.08] bg-zinc-900/95 px-3 py-2.5 text-[12px] text-zinc-300 shadow-xl backdrop-blur motion-safe:transition-opacity motion-safe:duration-300 ${
              leaving ? "opacity-0" : "opacity-100"
            }`}
          >
            <div className="flex items-start gap-2">
              <BookOpen size={12} aria-hidden="true" className="mt-0.5 shrink-0 text-accent-soft" />
              <div className="min-w-0 flex-1">
                <p className="font-medium text-zinc-200">{HEADING}</p>
                <p className="text-[11px] text-zinc-500">{HEADING_NOTE}</p>
              </div>
              <button
                type="button"
                data-testid="memory-used-close"
                onClick={close}
                aria-label={CLOSE_WORDS}
                title={CLOSE_WORDS}
                className="-mr-1 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
              >
                <X size={12} aria-hidden="true" />
              </button>
            </div>
            <ul className="mt-2 space-y-1.5" data-testid="memory-used-list">
              {listed.map((it) => {
                const key = itemKey(it);
                const d = done[key];
                const when = whenWords(it.when);
                const editing = fixing?.key === key;
                return (
                  <li key={key} data-testid="memory-used-item" data-kind={it.kind} className="min-w-0 leading-5">
                    <div className="flex min-w-0 flex-wrap items-baseline gap-x-1.5">
                      <span className={`min-w-0 break-words ${d && d.state !== "undone" ? "text-zinc-500 line-through" : ""}`}>
                        {itemWords(it)}
                        {when ? <span className="text-zinc-500"> · {when}</span> : null}
                      </span>
                      {it.href && (
                        <Link href={it.href} className={TEXT_BTN}>
                          {OPEN}
                        </Link>
                      )}
                      {!readOnly && canFix(it) && !d && !editing && (
                        <button
                          type="button"
                          data-testid="memory-used-wrong"
                          onClick={() => setFixing({ key, draft: "" })}
                          className={TEXT_BTN}
                        >
                          {WRONG}
                        </button>
                      )}
                    </div>
                    {editing && (
                      <form
                        className="mt-1 flex min-w-0 flex-wrap items-center gap-1.5"
                        onSubmit={(e) => {
                          e.preventDefault();
                          void fix(it, "correct");
                        }}
                      >
                        <input
                          data-testid="memory-used-right"
                          // The owner pressed That's wrong: the field is theirs
                          // now (the pop-up itself never takes the focus).
                          autoFocus
                          aria-label={RIGHT_PLACEHOLDER}
                          placeholder={RIGHT_PLACEHOLDER}
                          value={fixing.draft}
                          maxLength={280}
                          onChange={(e) => setFixing({ key, draft: e.target.value })}
                          className="min-w-0 flex-1 basis-40 rounded-md border border-white/[0.1] bg-black/30 px-2 py-1 text-[12px] text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-accent/50"
                        />
                        <button
                          type="submit"
                          data-testid="memory-used-save"
                          disabled={!fixing.draft.trim() || pending !== null}
                          className={TEXT_BTN}
                        >
                          {SAVE}
                        </button>
                        <button
                          type="button"
                          data-testid="memory-used-forget"
                          disabled={pending !== null}
                          onClick={() => void fix(it, "forget")}
                          className={TEXT_BTN}
                        >
                          {FORGET}
                        </button>
                        <button type="button" onClick={() => setFixing(null)} className={TEXT_BTN}>
                          {CANCEL}
                        </button>
                        {pending === key && <Loader2 size={11} aria-hidden="true" className="animate-spin text-zinc-500" />}
                      </form>
                    )}
                    {d && (
                      <div data-testid="memory-used-done" data-state={d.state} className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[12px]">
                        {d.state === "undone" ? (
                          <span className="text-zinc-400">{PUT_BACK}</span>
                        ) : (
                          <>
                            <span className="text-accent-soft">{d.state === "fixed" ? FIXED : FORGOTTEN}</span>
                            {d.actionId ? (
                              <button
                                type="button"
                                data-testid="memory-used-undo"
                                disabled={pending !== null}
                                onClick={() => void undo(it)}
                                className={TEXT_BTN}
                              >
                                {UNDO}
                              </button>
                            ) : (
                              <span className="text-zinc-500">{NO_UNDO}</span>
                            )}
                          </>
                        )}
                      </div>
                    )}
                    {error?.key === key && (
                      <p data-testid="memory-used-error" className="mt-0.5 text-[12px] text-tone-warn">
                        {error.text}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
            {more > 0 && <p className="mt-1.5 text-[11px] text-zinc-500">{moreWords(more)}</p>}
          </div>,
          document.body,
        )
      : null;

  return (
    <>
      <button
        ref={lineRef}
        type="button"
        data-testid="memory-used-line"
        aria-expanded={open}
        aria-controls={open ? popId : undefined}
        onClick={() => (open ? close() : show())}
        title={HEADING_NOTE}
        className={QUIET_BTN}
      >
        <BookOpen size={11} aria-hidden="true" className="shrink-0" />
        {lineWords(items.length)}
      </button>
      {popup}
    </>
  );
}
