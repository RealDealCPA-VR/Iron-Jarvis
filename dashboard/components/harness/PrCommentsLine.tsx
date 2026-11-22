"use client";

/**
 * 26-a (R2.16): the line Iron Jarvis writes in a Chat › Code session when
 * review comments arrive on its pull request ("3 review comments from sam
 * arrived."). The session's NEWEST such line (`card`; lib/prComments
 * cardLineIndex) carries the ONE card: every comment of the pull request
 * still to answer, whichever line said it arrived, each with a tick (an
 * owner's, a member's or a collaborator's are ticked; someone from outside
 * the repository and a bot are not, and say so), its place, its words and
 * Set aside. ONE main button for the view, Answer these: one coding turn in
 * the session, which the page follows like any turn. A quiet text button,
 * Stop telling me about comments here (the bell row; its Undo, Tell me
 * again). An older line is its sentence alone.
 *
 * The comments are read when the card is drawn, when a reply in the session
 * ends, and after a press (lib/prComments: no GitHub call; nothing is ever
 * sent to GitHub). A card with nothing left to answer is its sentence and
 * the quiet stop. A comment's words are plain text, never Markdown: a person
 * wrote them on GitHub.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui";
import {
  PR_COMMENTS_NOTHING_SENT,
  PR_COMMENTS_OPEN,
  PR_COMMENTS_QUIET_NOTE,
  PR_COMMENTS_SET_ASIDE,
  PR_COMMENTS_STOP,
  PR_COMMENTS_TELL_AGAIN,
  answerButtonWords,
  answerPrComments,
  arrivedWords,
  authorLabel,
  decodePrCommentsLine,
  firstTicks,
  getPrComments,
  openComments,
  setAsidePrComment,
  setPrCommentsQuiet,
  stateWords,
  type PrCommentsView,
} from "@/lib/prComments";

const QUIET =
  "inline-flex h-7 items-center rounded-lg px-2 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-40";

function errorText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

export function PrCommentsLine({
  threadId,
  raw,
  content,
  archived,
  busy,
  card = false,
  onAnswered,
}: {
  threadId: string;
  /** The line's `prComments` field ({ids, n, authors, stopped}). */
  raw: unknown;
  /** The line's own words (said as they are for a line that stopped). */
  content: string;
  archived: boolean;
  /** A reply runs in the session: nothing new can start. */
  busy: boolean;
  /** The session's newest line of arrived comments: it carries the card. */
  card?: boolean;
  /** Answer these started this turn: the page follows it. */
  onAnswered?: (turnId: string) => void;
}) {
  const line = decodePrCommentsLine(raw);
  const [view, setView] = useState<PrCommentsView | null>(null);
  const [ticks, setTicks] = useState<Set<string> | null>(null);
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");
  const seq = useRef(0);
  const stopped = !line || line.stopped || line.ids.length === 0;
  const drawn = card && !stopped;

  const load = useCallback(async () => {
    const mine = ++seq.current;
    try {
      const got = await getPrComments(threadId);
      if (mine === seq.current) setView(got);
    } catch {
      /* the line still says what arrived */
    }
  }, [threadId]);

  // The card is read when it is drawn, and again each time a reply in the
  // session ends (an Answer these reply settles its comments then). Never on
  // a clock; an older line reads nothing.
  useEffect(() => {
    if (drawn && !busy) void load();
  }, [drawn, busy, load]);

  if (!line) return null;
  if (stopped) {
    return (
      <p data-testid="pr-comments-line" data-stopped="true" className="text-[13px] text-zinc-400">
        {content}
      </p>
    );
  }
  if (!drawn) {
    return (
      <p data-testid="pr-comments-line" className="text-[13px] text-zinc-400">
        {arrivedWords(line.n, line.authors)}
      </p>
    );
  }
  const open = openComments(view);
  const picked = ticks ?? firstTicks(open);
  const chosen = open.filter((c) => picked.has(c.id) && c.state === "new").map((c) => c.id);
  const locked = archived || busy || working !== "";

  function toggle(id: string, on: boolean) {
    const next = new Set(picked);
    if (on) next.add(id);
    else next.delete(id);
    setTicks(next);
  }

  async function act(name: string, fn: () => Promise<void>, fallback: string) {
    setWorking(name);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(errorText(e, fallback));
    } finally {
      await load();
      setWorking("");
    }
  }

  return (
    <div data-testid="pr-comments-line" className="min-w-0 space-y-2 text-[13px]">
      <p className="text-zinc-300">{arrivedWords(line.n, line.authors)}</p>
      {open.length > 0 ? (
        <ul data-testid="pr-comments-list" className="min-w-0 space-y-1.5">
          {open.map((c) => {
            const state = stateWords(c);
            return (
              <li
                key={c.id}
                data-testid="pr-comment"
                data-id={c.id}
                className="flex min-w-0 gap-2 rounded-lg border border-white/[0.06] px-2 py-1.5"
              >
                <input
                  type="checkbox"
                  data-testid="pr-comment-tick"
                  className="mt-1 h-3.5 w-3.5 shrink-0 accent-accent"
                  checked={picked.has(c.id) && c.state === "new"}
                  disabled={locked || c.state !== "new"}
                  onChange={(e) => toggle(c.id, e.target.checked)}
                  aria-label={`Answer the comment from ${c.author}`}
                />
                <div className="min-w-0 flex-1 space-y-0.5">
                  <p className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-[12px] text-zinc-500">
                    <span className="text-zinc-300">{c.author}</span>
                    <span data-testid="pr-comment-who">{authorLabel(c.authorKind)}</span>
                    <span className="min-w-0 break-all font-mono">{c.place}</span>
                    {state ? <span data-testid="pr-comment-state">{state}</span> : null}
                  </p>
                  <p data-testid="pr-comment-words" className="line-clamp-4 whitespace-pre-wrap break-words text-zinc-300">
                    {c.body}
                  </p>
                  <div className="flex flex-wrap items-center gap-1">
                    <button
                      type="button"
                      data-testid="pr-comment-aside"
                      className={QUIET}
                      disabled={locked || c.state !== "new"}
                      onClick={() =>
                        void act("aside", () => setAsidePrComment(threadId, c.id, true), "It could not be set aside.")
                      }
                    >
                      {PR_COMMENTS_SET_ASIDE}
                    </button>
                    {c.url ? (
                      <a
                        href={c.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[12px] text-accent underline-offset-2 hover:underline"
                      >
                        {PR_COMMENTS_OPEN}
                      </a>
                    ) : null}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
      {view?.quiet ? (
        <p data-testid="pr-comments-quiet-note" className="text-[12px] text-zinc-500">
          {PR_COMMENTS_QUIET_NOTE}
        </p>
      ) : null}
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        {open.length > 0 ? (
          <Button
            variant="primary"
            size="sm"
            data-testid="pr-comments-answer"
            disabled={locked || chosen.length === 0}
            onClick={() =>
              void act(
                "answer",
                async () => {
                  const turn = await answerPrComments(threadId, chosen);
                  setTicks(null);
                  if (turn) onAnswered?.(turn);
                },
                "Iron Jarvis could not start the answer.",
              )
            }
          >
            {answerButtonWords(chosen.length)}
          </Button>
        ) : null}
        {view ? (
          <button
            type="button"
            data-testid="pr-comments-stop"
            className={QUIET}
            disabled={working !== ""}
            onClick={() =>
              void act(
                "quiet",
                async () => {
                  await setPrCommentsQuiet(threadId, !view.quiet);
                },
                "That did not change.",
              )
            }
          >
            {view.quiet ? PR_COMMENTS_TELL_AGAIN : PR_COMMENTS_STOP}
          </button>
        ) : null}
        {open.length > 0 ? <span className="text-[12px] text-zinc-500">{PR_COMMENTS_NOTHING_SENT}</span> : null}
      </div>
      {error ? (
        <p data-testid="pr-comments-error" role="alert" className="text-[12px] text-tone-warn">
          {error}
        </p>
      ) : null}
    </div>
  );
}
