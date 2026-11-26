"use client";

/**
 * WHAT IS FILLING THIS CHAT (the intelligence plan, release 6): the list the
 * plain chat's fullness meter opens. Coding sessions have had this view since
 * v1.415.0 (the Session tab's drop-down); this is the SAME component
 * (components/harness/ContextBreakdown) opened in one more place, with a
 * plain chat's words: your messages and the replies, files you attached,
 * what Iron Jarvis remembered, the tools it was given.
 *
 * It is read when it opens, from the newest reply's own record (lib/
 * chatFilling; the daemon's numbers, sizes only), and read again when a
 * reply ends while it is open (the list on screen stays until the new one
 * is here). No page, no setting, no notice: a small panel over the composer,
 * opened by the fullness gauge (from half full) or by the chat's "⋯" menu
 * (any saved chat), that Escape, the Close press or the gauge itself closes.
 * Every size is an estimate, said as a share ("about 12%"), never a count.
 *
 * ESCAPE is caught first (capture) and kept: the key that closes the list
 * never also reaches the message box, whose Escape stops a running reply
 * (the composer menus' rule, v1.450.0). FOCUS moves into the list when it
 * opens and goes back to what opened it when it closes (the project
 * drawer's rule): a reader who has moved to the message box stays there.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { get } from "@/lib/api";
import { ContextBreakdown } from "@/components/harness/ContextBreakdown";
import {
  FILLING_CLOSE,
  FILLING_FAILED,
  FILLING_LOADING,
  FILLING_NONE,
  FILLING_TITLE,
  OF_THE_ROOM,
  ROOM_LEFT_NONE,
  decodeChatFilling,
  fillingHeadline,
  fillingNote,
  fillingPath,
  type ChatFilling as Filling,
} from "@/lib/chatFilling";

type Load = { state: "loading" } | { state: "failed" } | { state: "ready"; filling: Filling };

const OVER_WORDS = { none: ROOM_LEFT_NONE, ofRoom: OF_THE_ROOM };

function focusable(el: HTMLElement | null | undefined): el is HTMLElement {
  return !!el && el !== document.body && el.isConnected;
}

export function ChatFilling({
  threadId,
  onClose,
  replyRunning = false,
  returnFocusTo,
}: {
  /** The saved chat (null: not saved yet, so there is no record to read). */
  threadId: string | null;
  onClose: () => void;
  /** A reply is running in this chat: when it ends, the list is read again. */
  replyRunning?: boolean;
  /** Where focus goes on close when what opened the list is gone (the "⋯"
   *  menu's row unmounts as it opens the list): the page's named target. */
  returnFocusTo?: () => HTMLElement | null;
}) {
  const [load, setLoad] = useState<Load>(threadId ? { state: "loading" } : { state: "ready", filling: NOTHING });
  // Counts the replies that ended while the list was open (the falling edge
  // of `replyRunning`): each one reads the record again.
  const [ended, setEnded] = useState(0);
  const wasRunning = useRef(replyRunning);
  useEffect(() => {
    if (wasRunning.current && !replyRunning) setEnded((n) => n + 1);
    wasRunning.current = replyRunning;
  }, [replyRunning]);
  // The chat whose list is on screen: a read for the SAME chat keeps it
  // there until the new answer lands (no "Reading…" flash under a reader).
  const shownFor = useRef<string | null>(null);

  useEffect(() => {
    if (!threadId) {
      shownFor.current = null;
      setLoad({ state: "ready", filling: NOTHING });
      return;
    }
    let live = true;
    if (shownFor.current !== threadId) setLoad({ state: "loading" });
    get<unknown>(fillingPath(threadId))
      .then((raw) => {
        if (!live) return;
        const filling = decodeChatFilling(raw);
        shownFor.current = filling ? threadId : null;
        setLoad(filling ? { state: "ready", filling } : { state: "failed" });
      })
      .catch(() => {
        if (!live) return;
        shownFor.current = null;
        setLoad({ state: "failed" });
      });
    return () => {
      live = false;
    };
  }, [threadId, ended]);

  // ESCAPE: caught first (capture) and kept, so the same key never also
  // stops a running reply (the message box's own Escape) or closes anything
  // else on the page.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.isComposing) return;
      e.preventDefault();
      e.stopPropagation();
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  // FOCUS IN on open, and BACK on close. The opener is read on the first
  // render, before the panel exists, so it is never something inside it.
  const panelRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLElement | null>(
    typeof document !== "undefined" ? (document.activeElement as HTMLElement | null) : null,
  );
  const returnRef = useRef(returnFocusTo);
  returnRef.current = returnFocusTo;
  useLayoutEffect(() => {
    const panel = panelRef.current;
    panel?.focus();
    return () => {
      // Only when focus is still in the list (or was lost with it): a reader
      // who has moved on (the message box) is left where they are.
      const now = document.activeElement;
      const lost = !now || now === document.body || (panel?.contains(now) ?? false);
      if (!lost) return;
      const opener = openerRef.current;
      if (focusable(opener) && !(panel?.contains(opener) ?? false)) {
        opener.focus();
        return;
      }
      const back = returnRef.current?.();
      if (focusable(back)) back.focus();
    };
  }, []);

  const filling = load.state === "ready" ? load.filling : null;
  const reading = filling?.reading ?? null;
  return (
    <div
      ref={panelRef}
      role="dialog"
      aria-label={FILLING_TITLE}
      tabIndex={-1}
      data-testid="chat-filling"
      className="fixed bottom-24 right-4 z-40 max-h-[min(70vh,560px)] w-[min(26rem,calc(100vw-2rem))] overflow-y-auto rounded-xl border border-white/[0.06] bg-ink-950/95 p-3 text-left shadow-xl outline-none backdrop-blur"
    >
      <button
        type="button"
        data-testid="chat-filling-close"
        aria-label={FILLING_CLOSE}
        title={FILLING_CLOSE}
        onClick={onClose}
        className="float-right -mr-1 -mt-1 rounded-md p-1 text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
      >
        <X size={14} aria-hidden />
      </button>
      <ContextBreakdown
        prefix="chat-filling"
        reading={reading}
        emptyWords={load.state === "loading" ? FILLING_LOADING : load.state === "failed" ? FILLING_FAILED : FILLING_NONE}
        // The first line is the gauge's own sentence for the same reply's
        // reading; a chat that holds more than its room says that instead,
        // in the chat's own answer's words (lib/chatFilling).
        plain={{
          title: FILLING_TITLE,
          headline: filling ? fillingHeadline(filling) : "",
          ...(filling?.over ? { over: OVER_WORDS } : {}),
        }}
        note={filling && reading ? fillingNote(filling) : undefined}
      />
    </div>
  );
}

const NOTHING: Filling = { found: false, reading: null, windowKnown: true, leftOut: 0, over: false };
