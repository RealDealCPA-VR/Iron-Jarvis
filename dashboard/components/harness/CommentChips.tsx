"use client";

/**
 * v1.544.0 — the COMMENTS on changed code, as chips (lib/codeComments).
 *
 *  - `CommentChips`: above the message box, one chip per WAITING comment with
 *    its × and, when its file changed again, "changed since". ONE component
 *    for both Code views (Build › Code and Chat › Code). It reads the
 *    session's store itself, so the box's owner passes one string.
 *  - `SentComments`: under a sent message, the comments that rode it, read
 *    only (the words on hover).
 *  - `useCodeComments`: the waiting comments of one session, for the box's
 *    owner (a message of comments alone can be sent).
 *
 * The one-row toolbar under the box is untouched: chips go above the box.
 */

import { useCallback, useSyncExternalStore } from "react";
import { MessageSquare, X } from "lucide-react";
import {
  COMMENT_CHANGED_SINCE,
  COMMENT_CHANGED_SINCE_TITLE,
  COMMENT_CHIPS_LABEL,
  commentChipWords,
  commentTitle,
  commentWhere,
  getComments,
  removeComment,
  subscribeComments,
  type CodeComment,
  type CommentBody,
} from "@/lib/codeComments";

const NO_COMMENTS: readonly CodeComment[] = Object.freeze([]) as readonly CodeComment[];
const nothing = () => {};
const none = () => NO_COMMENTS;

/** The session's waiting comments (null key: none — an older Iron Jarvis
 *  that takes no comments offers none). */
export function useCodeComments(storeKey: string | null): readonly CodeComment[] {
  const subscribe = useCallback(
    (fn: () => void) => (storeKey ? subscribeComments(storeKey, fn) : nothing),
    [storeKey],
  );
  const read = useCallback(() => (storeKey ? getComments(storeKey) : NO_COMMENTS), [storeKey]);
  return useSyncExternalStore(subscribe, read, none);
}

const CHIP =
  "flex max-w-[280px] items-center gap-1.5 rounded-lg bg-white/[0.06] py-1 pl-2 pr-1.5 text-[12px] text-zinc-300";

export function CommentChips({ storeKey }: { storeKey: string | null }) {
  const list = useCodeComments(storeKey);
  if (!storeKey || list.length === 0) return null;
  return (
    <div
      data-testid="comment-chips"
      role="list"
      aria-label={COMMENT_CHIPS_LABEL}
      className="flex flex-wrap items-center gap-2 px-3 pt-2"
    >
      {list.map((c) => (
        <span
          key={c.id}
          role="listitem"
          data-testid="comment-chip"
          data-stale={c.stale ? "true" : "false"}
          title={commentTitle(c)}
          className={CHIP}
        >
          <MessageSquare size={13} aria-hidden className="shrink-0 text-zinc-500" />
          <span className="truncate">{commentChipWords(c)}</span>
          {c.stale ? (
            <span data-testid="comment-chip-stale" title={COMMENT_CHANGED_SINCE_TITLE} className="shrink-0 text-tone-warn">
              {COMMENT_CHANGED_SINCE}
            </span>
          ) : null}
          <button
            type="button"
            data-testid="comment-chip-remove"
            aria-label={`Remove the comment on ${commentWhere(c)}`}
            onClick={() => removeComment(storeKey, c.id)}
            className="shrink-0 rounded text-zinc-500 hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
          >
            <X size={13} aria-hidden />
          </button>
        </span>
      ))}
    </div>
  );
}

/** Under a sent message: the comments it carried (read only). */
export function SentComments({ comments }: { comments?: readonly CommentBody[] }) {
  if (!comments || comments.length === 0) return null;
  return (
    <div data-testid="sent-comments" className="flex max-w-[88%] flex-wrap justify-end gap-1.5">
      {comments.map((c, i) => (
        <span
          key={`${c.path}-${c.start}-${i}`}
          data-testid="sent-comment"
          title={commentTitle(c)}
          className="flex max-w-[280px] items-center gap-1 rounded-lg bg-white/[0.06] px-2 py-1 text-[12px] text-zinc-300"
        >
          <MessageSquare size={12} aria-hidden className="shrink-0 text-zinc-500" />
          <span className="truncate">{commentChipWords(c)}</span>
        </span>
      ))}
    </div>
  );
}
