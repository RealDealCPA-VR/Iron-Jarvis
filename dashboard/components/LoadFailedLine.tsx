"use client";

/**
 * v1.354.0 (G59): the quiet line a panel shows when its read failed, in the
 * user's words (lib/loadFailed.ts):
 *
 *   "The board couldn't load. Try again."
 *   "The board couldn't load. Iron Jarvis isn't responding."
 *
 * The same quiet grey sentence the panels already drew (no box, no tone, no
 * new look); the HTTP code and the server's own message ride the `title`
 * only. When the panel can re-read, "Try again" is a quiet inline press that
 * does exactly that, so the words name something the user can do here.
 */

import { couldntLoad, failureDetail, noAnswer, TRY_AGAIN_PRESS, type FailureLike } from "@/lib/loadFailed";

export function LoadFailedLine({
  thing,
  error,
  onRetry,
  className = "py-2 text-sm text-zinc-500",
  testId,
}: {
  /** Starts the sentence: "The board", "Saved workflows". */
  thing: string;
  error: FailureLike | null | undefined;
  /** Re-read now. Absent: "Try again." is plain words. */
  onRetry?: () => void;
  className?: string;
  testId?: string;
}) {
  const quiet = noAnswer(error);
  return (
    <p
      className={className}
      title={failureDetail(error)}
      data-testid={testId}
      data-load-failed={quiet ? "no-answer" : "error"}
    >
      {quiet || !onRetry ? (
        couldntLoad(thing, error)
      ) : (
        <>
          {`${thing} couldn't load. `}
          <button
            type="button"
            onClick={onRetry}
            className="rounded text-accent-soft underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
          >
            {TRY_AGAIN_PRESS}
          </button>
          .
        </>
      )}
    </p>
  );
}
