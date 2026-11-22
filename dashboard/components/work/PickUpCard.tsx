"use client";

/**
 * PICK UP WHERE YOU LEFT OFF (next leaps 04-a; build-out plan R2.1, idea 04):
 * the card under the folder chip of a NEW chat, a new Chat › Code session and
 * a new Build › Code session. lib/pickUp.ts holds the words and the rules;
 * the daemon's `workmap/pickup.py` decides what the card holds.
 *
 * - Read ONCE per folder (and per Local only switch), never polled. Nothing
 *   is drawn while it is read, or for a folder with no earlier work.
 * - The full card only when the daemon says `full` (D-04.4: something still
 *   open, or the last work in the past 30 days); otherwise one quiet line,
 *   "Last here: 12 Mar", that opens it.
 * - ONE main action, Continue (G4): the host sends `CONTINUE_SENTENCE` and
 *   nothing else, and names the new chat or session `continuedTitle(card)`
 *   (the work it continues, never the sentence). A second press while the
 *   first is still being sent does nothing. The rest are quiet: the last
 *   work's title opens it, the cross hides the card for this folder until
 *   new work is on record.
 * - `tool` (Build › Code): the user's own coding tool is told only the
 *   sentence, never what the card lists, and the card SAYS so under
 *   Continue (`PICK_UP_TOOL_NOTE`), not only in a hover.
 * - `hidden` (the host's box holds words or files, or a file is still going
 *   up): the card steps aside, as the next-step box does, so a draft is
 *   never replaced by Continue, nor sent with it.
 * - A failed read says so in words, with Try again (never "nothing here");
 *   an Iron Jarvis without this read (a 404) draws nothing.
 *
 * OPEN ITEMS (next leaps 04-b, R2.13): "Still open" holds EVERY open item of
 * the folder, however old, oldest first (the first three, then "Show N
 * more"). Each has ONE main press, Continue (only where the host can say
 * which item: `onContinueItem`, the new chat; it sends `PICK_ITEM_SENTENCE`
 * with the item's id), and a ⋯ menu with Done and No longer needed. Where
 * later work seems to have done one, the card ASKS: "This may be done. Mark
 * it done?" with Mark it done and Not yet (D-04.3: it never closes by
 * itself). "Closed lately" starts FOLDED; each closed item's ⋯ menu has
 * Reopen. Each answer is one POST, and the card moves the item only when the
 * daemon's answer says it moved; a failed press says so in words.
 */

import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowRight, ChevronDown, ChevronRight, MoreHorizontal, X } from "lucide-react";

import { LoadFailedLine } from "@/components/LoadFailedLine";
import { useComposerEmpty } from "@/components/chat/NextStepBox";
import { get, post } from "@/lib/api";
import type { ComposerStore } from "@/lib/composerStore";
import { couldnt, failureOf, serverSentence, type FailureLike } from "@/lib/loadFailed";
import {
  PICK_UP_CHANGE_FAILED,
  PICK_UP_CLOSED_LATELY,
  PICK_UP_CONTINUE,
  PICK_UP_DECIDED,
  PICK_UP_DONE,
  PICK_UP_FAILED_THING,
  PICK_UP_HIDE,
  PICK_UP_ITEM_CONTINUE,
  PICK_UP_ITEM_CONTINUE_TITLE,
  PICK_UP_ITEM_DONE,
  PICK_UP_ITEM_DROP,
  PICK_UP_ITEM_MENU,
  PICK_UP_ITEM_REOPEN,
  PICK_UP_LINES_SHOWN,
  PICK_UP_MAYBE_DONE,
  PICK_UP_MAYBE_NO,
  PICK_UP_MAYBE_YES,
  PICK_UP_PENDING,
  PICK_UP_STILL_OPEN,
  PICK_UP_TOOL_NOTE,
  afterAnswer,
  closedWords,
  continueTitle,
  decodePickUp,
  hideFor,
  isHidden,
  lastHereWords,
  lastTimeWords,
  moreWords,
  openItemClosePath,
  openItemReopenPath,
  outcomeWords,
  pickUpPath,
  showMoreWords,
  type PickUp,
  type PickUpClosed,
  type PickUpItem,
} from "@/lib/pickUp";

type Read =
  | { state: "idle" }
  | { state: "reading"; folder: string }
  | { state: "none"; folder: string }
  | { state: "failed"; folder: string; error: FailureLike }
  | { state: "card"; folder: string; card: PickUp };

type Answer = "done" | "dropped" | "reopen";

const QUIET_LINK =
  "rounded-sm text-zinc-300 underline decoration-white/20 underline-offset-2 transition-colors hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60";
const QUIET_PRESS =
  "rounded-md px-1.5 py-0.5 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:opacity-40";
const MENU_ITEM =
  "flex w-full rounded-lg px-2.5 py-1.5 text-left text-[12px] text-zinc-300 hover:bg-white/[0.06] disabled:opacity-40";

function Lines({ label, rows, testId }: { label: string; rows: readonly string[]; testId: string }) {
  if (rows.length === 0) return null;
  const shown = rows.slice(0, PICK_UP_LINES_SHOWN);
  const more = moreWords(rows.length - shown.length);
  return (
    <div data-testid={testId} className="min-w-0">
      <p className="text-zinc-500">{label}</p>
      <ul className="min-w-0 space-y-0.5">
        {shown.map((t, i) => (
          <li key={i} className="min-w-0 pl-2 text-zinc-300 [overflow-wrap:anywhere]">
            {t}
          </li>
        ))}
      </ul>
      {more ? <p className="pl-2 text-zinc-500">{more}</p> : null}
    </div>
  );
}

/** The ⋯ menu of one item: its presses, as menu items. Escape closes it.
 *  Opened on a row at the window's bottom edge (a phone), it scrolls just
 *  enough to be seen: it opens below its row, inside the page's own
 *  scrolling area, which never clips it (opened at 390 px). */
function ItemMenu({
  id,
  label,
  open,
  busy,
  onToggle,
  children,
}: {
  id: string;
  label: string;
  open: boolean;
  busy: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const listRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (open) listRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [open]);
  return (
    <span className="relative shrink-0">
      <button
        type="button"
        data-testid={`pick-up-item-menu-${id}`}
        aria-label={`${PICK_UP_ITEM_MENU}: ${label}`}
        title={PICK_UP_ITEM_MENU}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={busy}
        onClick={onToggle}
        className="grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:opacity-40"
      >
        <MoreHorizontal size={13} aria-hidden="true" />
      </button>
      {open ? (
        <div
          ref={listRef}
          role="menu"
          aria-label={label}
          data-testid={`pick-up-item-menu-list-${id}`}
          onKeyDown={(e) => {
            if (e.key !== "Escape") return;
            e.stopPropagation();
            onToggle();
          }}
          className="absolute right-0 top-full z-30 mt-0.5 w-44 space-y-0.5 rounded-xl border border-white/10 bg-zinc-900 p-1 shadow-lg shadow-black/40"
        >
          {children}
        </div>
      ) : null}
    </span>
  );
}

const textsOf = (rows: readonly { text: string }[]) => rows.map((r) => r.text);

export function PickUpCard({
  folder,
  local = false,
  chatCode = false,
  tool = false,
  hidden = false,
  disabled = false,
  onContinue,
  onContinueItem,
}: {
  /** The folder the new chat or session is in; null / "" = none (nothing drawn). */
  folder: string | null | undefined;
  /** The new chat or session is Local only (its Local only work is shown). */
  local?: boolean;
  /** A new Chat › Code session (no card for a folder inside a project). */
  chatCode?: boolean;
  /** Build › Code: the user's own coding tool is not shown this card. */
  tool?: boolean;
  /** The host's box holds words or files (one still going up included):
   *  the card steps aside. */
  hidden?: boolean;
  /** The host is starting something already. */
  disabled?: boolean;
  /** Send CONTINUE_SENTENCE (and nothing else), naming the new chat or
   *  session `continuedTitle(card)`. A promise holds the press. */
  onContinue: (card: PickUp) => void | Promise<unknown>;
  /** 04-b: send PICK_ITEM_SENTENCE with this item's id (the chat body's
   *  `pick_up_item`). Left out (a host that cannot say which item): an item
   *  has no Continue of its own, only its ⋯ menu. */
  onContinueItem?: (card: PickUp, item: PickUpItem) => void | Promise<unknown>;
}) {
  const place = (folder ?? "").trim();
  const [read, setRead] = useState<Read>({ state: "idle" });
  const [nonce, setNonce] = useState(0);
  const [opened, setOpened] = useState(false);
  const [hiddenNow, setHiddenNow] = useState(false);
  const [sending, setSending] = useState(false);
  const [allOpen, setAllOpen] = useState(false);
  const [closedShown, setClosedShown] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [itemBusy, setItemBusy] = useState<string | null>(null);
  const [itemProblem, setItemProblem] = useState<string | null>(null);
  const sendingRef = useRef(false);
  const itemBusyRef = useRef(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // ONE read per folder (and per Local only switch, and per Try again).
  useEffect(() => {
    setOpened(false);
    setHiddenNow(false);
    setAllOpen(false);
    setClosedShown(false);
    setMenuFor(null);
    setItemProblem(null);
    if (!place) {
      setRead({ state: "idle" });
      return;
    }
    let live = true;
    setRead({ state: "reading", folder: place });
    get<unknown>(pickUpPath(place, local, chatCode))
      .then((raw) => {
        if (!live) return;
        const card = decodePickUp(raw);
        setRead(card ? { state: "card", folder: place, card } : { state: "none", folder: place });
      })
      .catch((e: unknown) => {
        if (!live) return;
        const error = failureOf(e);
        // An Iron Jarvis with no such read (older than this card) has nothing
        // to say; a read that failed says so.
        setRead(error.status === 404 ? { state: "none", folder: place } : { state: "failed", folder: place, error });
      });
    return () => {
      live = false;
    };
  }, [place, local, chatCode, nonce]);

  if (hidden || !place || read.state === "idle" || read.state === "reading" || read.state === "none") return null;
  if (read.folder !== place) return null;
  if (read.state === "failed") {
    return (
      <div data-testid="pick-up-card" data-state="failed" className="w-full min-w-0 px-1">
        <LoadFailedLine
          thing={PICK_UP_FAILED_THING}
          error={read.error}
          onRetry={() => setNonce((n) => n + 1)}
          className="py-1 text-[12px] text-zinc-500"
          testId="pick-up-failed"
        />
      </div>
    );
  }
  const card = read.card;
  if (hiddenNow || isHidden(place, card)) return null;

  async function press() {
    if (sendingRef.current || disabled) return;
    sendingRef.current = true;
    setSending(true);
    try {
      await onContinue(card);
    } finally {
      sendingRef.current = false;
      if (alive.current) setSending(false);
    }
  }

  async function pressItem(item: PickUpItem) {
    if (!onContinueItem || sendingRef.current || disabled) return;
    sendingRef.current = true;
    setSending(true);
    try {
      await onContinueItem(card, item);
    } finally {
      sendingRef.current = false;
      if (alive.current) setSending(false);
    }
  }

  /** One answer about one item: one POST; the card moves the item only
   *  when the daemon's answer says it moved. */
  async function answer(id: string, what: Answer) {
    if (itemBusyRef.current) return;
    itemBusyRef.current = true;
    setItemBusy(id);
    setItemProblem(null);
    setMenuFor(null);
    try {
      const res = await post<{ item?: { status?: unknown; how?: unknown; closed_at?: unknown } }>(
        what === "reopen" ? openItemReopenPath(id) : openItemClosePath(id),
        what === "reopen" ? undefined : { how: what },
      );
      const got = res?.item;
      if (got && typeof got.status === "string" && alive.current) {
        const next = {
          status: got.status,
          how: typeof got.how === "string" ? got.how : undefined,
          closed_at: typeof got.closed_at === "string" ? got.closed_at : null,
        };
        setRead((r) => (r.state === "card" && r.folder === place ? { ...r, card: afterAnswer(r.card, id, next) } : r));
      }
    } catch (e: unknown) {
      const error = failureOf(e);
      if (alive.current)
        setItemProblem(serverSentence(error) ? `${PICK_UP_CHANGE_FAILED}: ${error.message}` : couldnt("change that item", error));
    } finally {
      itemBusyRef.current = false;
      if (alive.current) setItemBusy(null);
    }
  }

  if (!card.full && !opened) {
    return (
      <div data-testid="pick-up-card" data-state="quiet" className="w-full min-w-0 px-1">
        <button
          type="button"
          data-testid="pick-up-quiet"
          aria-expanded={false}
          title={`${lastTimeWords(card)}: ${card.last.title}`}
          onClick={() => setOpened(true)}
          className="rounded-md px-1 py-0.5 text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
        >
          {lastHereWords(card)}
        </button>
      </div>
    );
  }

  const ended = outcomeWords(card.last.outcome);
  const openShown = allOpen ? card.open : card.open.slice(0, PICK_UP_LINES_SHOWN);
  const openMore = showMoreWords(card.open.length - openShown.length);

  function openRow(item: PickUpItem) {
    const busy = itemBusy === item.id;
    return (
      <li key={item.id} data-testid={`pick-up-item-${item.id}`} className="min-w-0 pl-2">
        <div className="flex min-w-0 items-start gap-1">
          <span className="min-w-0 flex-1 pt-0.5 text-zinc-300 [overflow-wrap:anywhere]">{item.text}</span>
          {onContinueItem ? (
            <button
              type="button"
              data-testid={`pick-up-item-continue-${item.id}`}
              title={PICK_UP_ITEM_CONTINUE_TITLE}
              aria-label={`${PICK_UP_ITEM_CONTINUE}: ${item.text}`}
              disabled={disabled || sending || busy}
              onClick={() => void pressItem(item)}
              className={`${QUIET_PRESS} shrink-0`}
            >
              {PICK_UP_ITEM_CONTINUE}
            </button>
          ) : null}
          <ItemMenu
            id={item.id}
            label={item.text}
            open={menuFor === item.id}
            busy={busy}
            onToggle={() => setMenuFor((cur) => (cur === item.id ? null : item.id))}
          >
            <button
              type="button"
              role="menuitem"
              data-testid={`pick-up-item-done-${item.id}`}
              onClick={() => void answer(item.id, "done")}
              className={MENU_ITEM}
            >
              {PICK_UP_ITEM_DONE}
            </button>
            <button
              type="button"
              role="menuitem"
              data-testid={`pick-up-item-drop-${item.id}`}
              onClick={() => void answer(item.id, "dropped")}
              className={MENU_ITEM}
            >
              {PICK_UP_ITEM_DROP}
            </button>
          </ItemMenu>
        </div>
        {item.maybeDone ? (
          <p data-testid={`pick-up-maybe-${item.id}`} className="flex flex-wrap items-center gap-x-1 text-zinc-500">
            <span title={item.maybeDone.title || undefined}>{PICK_UP_MAYBE_DONE}</span>
            <button
              type="button"
              data-testid={`pick-up-maybe-yes-${item.id}`}
              disabled={busy}
              onClick={() => void answer(item.id, "done")}
              className={QUIET_PRESS}
            >
              {PICK_UP_MAYBE_YES}
            </button>
            <button
              type="button"
              data-testid={`pick-up-maybe-no-${item.id}`}
              disabled={busy}
              onClick={() => void answer(item.id, "reopen")}
              className={QUIET_PRESS}
            >
              {PICK_UP_MAYBE_NO}
            </button>
          </p>
        ) : null}
      </li>
    );
  }

  function closedRow(item: PickUpClosed) {
    const busy = itemBusy === item.id;
    return (
      <li key={item.id} data-testid={`pick-up-closed-${item.id}`} className="flex min-w-0 items-start gap-1 pl-2">
        <span className="min-w-0 flex-1 pt-0.5 text-zinc-400 [overflow-wrap:anywhere]">
          {item.text}
          <span className="text-zinc-600">{` · ${closedWords(item)}`}</span>
        </span>
        <ItemMenu
          id={item.id}
          label={item.text}
          open={menuFor === item.id}
          busy={busy}
          onToggle={() => setMenuFor((cur) => (cur === item.id ? null : item.id))}
        >
          <button
            type="button"
            role="menuitem"
            data-testid={`pick-up-item-reopen-${item.id}`}
            onClick={() => void answer(item.id, "reopen")}
            className={MENU_ITEM}
          >
            {PICK_UP_ITEM_REOPEN}
          </button>
        </ItemMenu>
      </li>
    );
  }

  return (
    <section
      data-testid="pick-up-card"
      data-state="full"
      aria-label={lastTimeWords(card)}
      className="relative w-full min-w-0 space-y-1.5 rounded-xl bg-white/[0.03] px-3 py-2 text-[12px] leading-relaxed shadow-[0_0_0_0.5px_rgb(var(--white)/0.08)]"
    >
      <button
        type="button"
        data-testid="pick-up-hide"
        aria-label={PICK_UP_HIDE}
        title={PICK_UP_HIDE}
        onClick={() => {
          hideFor(place, card);
          setHiddenNow(true);
        }}
        className="absolute right-1.5 top-1.5 grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
      >
        <X size={13} aria-hidden="true" />
      </button>
      <p data-testid="pick-up-last" className="min-w-0 pr-7 text-zinc-400 [overflow-wrap:anywhere]">
        {lastTimeWords(card)}:{" "}
        {card.last.link ? (
          <Link href={card.last.link} className={QUIET_LINK}>
            {card.last.title}
          </Link>
        ) : (
          <span className="text-zinc-300">{card.last.title}</span>
        )}
        {ended ? <span className="text-zinc-500">{` (${ended})`}</span> : null}
      </p>
      <Lines label={PICK_UP_DONE} rows={card.done} testId="pick-up-done" />
      <Lines label={PICK_UP_DECIDED} rows={textsOf(card.decided)} testId="pick-up-decided" />
      {card.open.length > 0 ? (
        <div data-testid="pick-up-open" className="min-w-0">
          <p className="text-zinc-500">{PICK_UP_STILL_OPEN}</p>
          <ul className="min-w-0 space-y-0.5">{openShown.map(openRow)}</ul>
          {openMore ? (
            <button
              type="button"
              data-testid="pick-up-open-more"
              onClick={() => setAllOpen(true)}
              className={`${QUIET_PRESS} ml-1`}
            >
              {openMore}
            </button>
          ) : null}
        </div>
      ) : null}
      {card.closedLately.length > 0 ? (
        <div data-testid="pick-up-closed" className="min-w-0">
          <button
            type="button"
            data-testid="pick-up-closed-toggle"
            aria-expanded={closedShown}
            onClick={() => setClosedShown((v) => !v)}
            className={`${QUIET_PRESS} -ml-1.5 inline-flex items-center gap-0.5 text-zinc-500`}
          >
            {closedShown ? <ChevronDown size={12} aria-hidden="true" /> : <ChevronRight size={12} aria-hidden="true" />}
            {`${PICK_UP_CLOSED_LATELY} (${card.closedLately.length})`}
          </button>
          {closedShown ? <ul className="min-w-0 space-y-0.5">{card.closedLately.map(closedRow)}</ul> : null}
        </div>
      ) : null}
      {itemProblem ? (
        <p role="alert" data-testid="pick-up-item-problem" className="text-tone-warn [overflow-wrap:anywhere]">
          {itemProblem}
        </p>
      ) : null}
      {card.recapPending ? (
        <p data-testid="pick-up-pending" className="text-zinc-500">
          {PICK_UP_PENDING}
        </p>
      ) : null}
      <div className="pt-0.5">
        <button
          type="button"
          data-testid="pick-up-continue"
          title={continueTitle(tool)}
          disabled={disabled || sending}
          onClick={() => void press()}
          className="inline-flex max-w-full items-center gap-1 rounded-full bg-white/[0.08] px-3 py-1 text-[12px] text-zinc-100 transition-colors hover:bg-white/[0.12] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-40"
        >
          {PICK_UP_CONTINUE}
          <ArrowRight size={12} aria-hidden="true" className="shrink-0" />
        </button>
        {tool ? (
          <p data-testid="pick-up-tool-note" className="pt-1 text-zinc-500 [overflow-wrap:anywhere]">
            {PICK_UP_TOOL_NOTE}
          </p>
        ) : null}
      </div>
    </section>
  );
}

/** The new chat's card: it steps aside while the chat's box holds words (a
 *  small subscriber, so the chat page never subscribes to the composer,
 *  v1.250.0) or files (`hasFiles`: attached or still going up). */
export function ChatPickUpCard({
  store,
  hasFiles,
  ...props
}: Omit<Parameters<typeof PickUpCard>[0], "hidden"> & { store: ComposerStore; hasFiles: boolean }) {
  const empty = useComposerEmpty(store);
  return <PickUpCard {...props} hidden={!empty || hasFiles} />;
}

export default PickUpCard;
