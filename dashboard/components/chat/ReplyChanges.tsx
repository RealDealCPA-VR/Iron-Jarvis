"use client";

/**
 * ReplyChanges (v1.328.0, calm chat W3-2) — the "2 files changed +14 −3" line
 * under a chat reply whose turn wrote or edited files.
 *
 * LAZY: nothing is asked until the reply is on screen (an
 * IntersectionObserver on this element), and then once per reply through
 * `lib/turnChanges` (a map keyed by the turn's window, in-flight asks joined).
 * Without an IntersectionObserver nothing is asked at all: "on screen" cannot
 * be known, and every browser the app runs in has one. A reply that wrote
 * nothing gets no window and so never asks. Render never waits: the line
 * appears when the answer lands, and a failed ask draws nothing.
 *
 * A saved chat reopened later asks again by the timing stored on the message
 * (the map lives only as long as the page).
 */

import { useEffect, useRef, useState } from "react";
import type { TurnChangesArgs, TurnChangeSet } from "./ChangedFiles";
import { ChangedFiles, type ChangeUndoState } from "./ChangedFiles";
import {
  forgetTurnChanges,
  loadTurnChanges,
  peekTurnChanges,
  turnChangesKey,
} from "@/lib/turnChanges";

export function ReplyChanges({
  turn,
  undoFor,
  onUndo,
}: {
  /** The turn's window (`turnWindow`); null = the reply wrote nothing. */
  turn: TurnChangesArgs | null;
  /** The chat's undo journal match for a file (the receipt's `undoFor`). */
  undoFor?: (path: string) => ChangeUndoState | null | undefined;
  /** The chat's one undo implementation (the receipt's `onUndo`). */
  onUndo?: (actionId: string, path: string) => void | Promise<void>;
}) {
  const key = turn ? turnChangesKey(turn) : "";
  const turnRef = useRef(turn);
  turnRef.current = turn;
  const boxRef = useRef<HTMLDivElement>(null);
  // The window that has been on screen (per window, so a row handed another
  // reply waits for that one to be seen too).
  const [seenKey, setSeenKey] = useState("");
  const seen = !!key && seenKey === key;
  // The answer, tagged with the window it answers, so a row that is handed a
  // different reply (a chat switch keeps the row) never shows the old one.
  const [got, setGot] = useState<{ key: string; set: TurnChangeSet } | null>(() => {
    const held = turn ? peekTurnChanges(turn) : undefined;
    return held ? { key, set: held } : null;
  });
  // Bumped after an Undo so the rows are read again.
  const [round, setRound] = useState(0);

  // On screen yet? One observer per reply, gone after the first sighting.
  useEffect(() => {
    if (!key || seen) return;
    const el = boxRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        setSeenKey(key);
        io.disconnect();
      }
    });
    io.observe(el);
    return () => io.disconnect();
  }, [key, seen]);

  useEffect(() => {
    const w = turnRef.current;
    if (!key || !seen || !w) return;
    let alive = true;
    loadTurnChanges(w)
      .then((r) => {
        if (alive) setGot({ key, set: r });
      })
      .catch(() => {
        // Nothing to show: a reply without its changes line reads as before.
      });
    return () => {
      alive = false;
    };
  }, [key, seen, round]);

  if (!key) return null;
  const set = got && got.key === key ? got.set : (turn && peekTurnChanges(turn)) || null;
  return (
    <div ref={boxRef} data-testid="reply-changes">
      {set && set.changes.length > 0 && (
        <div className="mt-1">
          <ChangedFiles
            changes={set.changes}
            more={set.more}
            undoFor={undoFor}
            onUndo={onUndo}
            onUndone={() => {
              const w = turnRef.current;
              if (w) forgetTurnChanges(w);
              setRound((n) => n + 1);
            }}
          />
        </div>
      )}
    </div>
  );
}

export default ReplyChanges;
