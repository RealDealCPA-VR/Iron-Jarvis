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
 * chatFilling; the daemon's numbers, sizes only). No page, no setting, no
 * notice: a small panel over the composer, opened by the fullness gauge
 * (from half full) or by the chat's "⋯" menu (any saved chat), that Escape,
 * the Close press or the gauge itself closes. Every size is an estimate,
 * said as a share ("about 12%"), never a count.
 */

import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { get } from "@/lib/api";
import { ContextBreakdown } from "@/components/harness/ContextBreakdown";
import { fillWords } from "@/lib/contextWords";
import {
  FILLING_CLOSE,
  FILLING_ESTIMATE,
  FILLING_FAILED,
  FILLING_LOADING,
  FILLING_NONE,
  FILLING_TITLE,
  decodeChatFilling,
  fillingPath,
  leftOutWords,
  type ChatFilling as Filling,
} from "@/lib/chatFilling";

type Load = { state: "loading" } | { state: "failed" } | { state: "ready"; filling: Filling };

export function ChatFilling({
  threadId,
  onClose,
}: {
  /** The saved chat (null: not saved yet, so there is no record to read). */
  threadId: string | null;
  onClose: () => void;
}) {
  const [load, setLoad] = useState<Load>(threadId ? { state: "loading" } : { state: "ready", filling: NOTHING });

  useEffect(() => {
    if (!threadId) {
      setLoad({ state: "ready", filling: NOTHING });
      return;
    }
    let live = true;
    setLoad({ state: "loading" });
    get<unknown>(fillingPath(threadId))
      .then((raw) => {
        if (!live) return;
        const filling = decodeChatFilling(raw);
        setLoad(filling ? { state: "ready", filling } : { state: "failed" });
      })
      .catch(() => {
        if (live) setLoad({ state: "failed" });
      });
    return () => {
      live = false;
    };
  }, [threadId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const filling = load.state === "ready" ? load.filling : null;
  const reading = filling?.reading ?? null;
  const known = filling ? filling.windowKnown : true;
  const left = filling ? leftOutWords(filling.leftOut) : "";
  return (
    <div
      role="dialog"
      aria-label={FILLING_TITLE}
      data-testid="chat-filling"
      className="fixed bottom-24 right-4 z-40 max-h-[min(70vh,560px)] w-[min(26rem,calc(100vw-2rem))] overflow-y-auto rounded-xl border border-white/[0.06] bg-ink-950/95 p-3 text-left shadow-xl backdrop-blur"
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
        // The first line is the gauge's own sentence, for the same reply's reading.
        plain={{ title: FILLING_TITLE, headline: reading ? fillWords("chat", reading.percent, known) : "" }}
        note={reading ? [left, FILLING_ESTIMATE].filter(Boolean).join(" ") : undefined}
      />
    </div>
  );
}

const NOTHING: Filling = { found: false, reading: null, windowKnown: true, leftOut: 0 };
