"use client";

/**
 * A FAILED REPLY IN A CODING SESSION (harness gaps, fix 2) — the chat page's
 * failure line, at a session's scale (lib/failureLine, lib/failureActions,
 * the one CliSignInPress).
 *
 * The v1.388.0 re-audit (0g, G.3, G.8, H3): a Build reply that failed said
 * "Press Retry" with no Retry anywhere, kept no technical text (the reply line
 * held only `title. fix`), offered no Sign in for a signed-out Claude Code or
 * Codex, and drew a boxed note under a red line. Now, on the reply itself:
 *
 *  - ONE calm danger line — the daemon's sentence, or its headline alone when
 *    a press stands beside it (the chat's rule: the press does what the fix
 *    sentence sent the user to go and do);
 *  - the press that fixes it (`failureActions.failureAction`, read off the
 *    line's typed `errorKind` / `errorProvider`, else /health's sign-in
 *    state): Sign in to Claude Code / Codex, or the link to where keys live;
 *  - Retry on the NEWEST failed or cut-off reply (lib/buildRetry) — never
 *    while a reply runs, never on a safety refusal (its way out stays the
 *    rewind list), never beside an account's limit card;
 *  - v1.422.0 (Wave 2 "Never lose work", B1): on a failed reply whose rounds
 *    the daemon KEPT (`canContinue`), "Continue from where it stopped" comes
 *    first — the reply carries on from the work it already did — and Retry
 *    (the question again from the start) is the second press;
 *  - the server's own words folded under Details — a real toggle (keyboard
 *    and touch reach it, never a hover), masked by the daemon, kept on the
 *    line so a reload still has them.
 */

import { useId, useState } from "react";
import Link from "next/link";
import { ChevronRight, CircleAlert, History, Play, RotateCcw } from "lucide-react";
import { CliSignInPress } from "@/components/connections/CliSignInPress";
import {
  DETAILS_WORDS,
  KEY_LINK_HREF,
  KEY_LINK_WORDS,
  REWIND_PICK_WORDS,
  failureAction,
} from "@/lib/failureActions";
import { CONTINUE_TITLE, CONTINUE_WORDS, RETRY_FROM_START_TITLE, RETRY_WORDS } from "@/lib/buildRetry";
import { CHOOSE_LOCAL_WORDS, cloudModelName, sendOnceWords } from "@/lib/localOnly";
import type { ModelOption } from "@/lib/types";
import { CONTINUED_BELOW, CUT_CARRIED_ON } from "@/lib/buildResume";
import type { BuildMessage } from "@/lib/buildSessions";
import type { Health } from "@/lib/types";

const PRESS =
  "inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50";
/** v1.422.0 (B1): the line's MAIN press ("Continue from where it stopped"). */
const PRESS_MAIN =
  "inline-flex items-center gap-1.5 rounded-md bg-white/[0.06] px-2 py-1 text-[12px] font-medium text-zinc-100 transition-colors hover:bg-white/[0.1] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50";

/** The server's own words behind a failure, folded: a quiet toggle and the
 *  text under it. Nothing when there are none. */
export function FailureDetails({ technical, testId }: { technical?: string | null; testId: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const text = typeof technical === "string" ? technical.trim() : "";
  if (!text) return null;
  return (
    <>
      <button
        type="button"
        data-testid={`${testId}-toggle`}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-[12px] text-zinc-500 transition-colors hover:text-zinc-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
      >
        <ChevronRight
          size={12}
          aria-hidden
          className={`transition-transform motion-reduce:transition-none ${open ? "rotate-90" : ""}`}
        />
        {DETAILS_WORDS}
      </button>
      {open ? (
        <pre
          id={id}
          data-testid={testId}
          className="max-h-48 basis-full overflow-y-auto whitespace-pre-wrap break-all font-mono text-[11px] leading-4 text-zinc-500"
        >
          {text}
        </pre>
      ) : null}
    </>
  );
}

export function HarnessFailureLine({
  m,
  newest,
  busy,
  health,
  turnProvider,
  retry,
  onRetry,
  canRewind,
  onRewind,
  carriedOn = false,
  canContinue = false,
  onContinue,
  continuedBelow = false,
  models = [],
  localOnlyHeld = [],
  onChooseLocal,
  onSendOnce,
}: {
  /** The failed (or cut-off) reply. */
  m: BuildMessage;
  /** It is the conversation's newest message. */
  newest: boolean;
  busy: boolean;
  health: Health | null | undefined;
  /** The provider the turn ran on (the reply's route, else the session's). */
  turnProvider: string;
  /** lib/buildRetry.retryOffered for this reply. */
  retry: boolean;
  onRetry: () => void;
  /** A message of the session can be rewound to (the refusal's press). */
  canRewind: boolean;
  onRewind: () => void;
  /** v1.391.0: a restart cut this reply off and the app carried it on by
   *  itself (lib/buildResume) — the line says so instead of "press Retry". */
  carriedOn?: boolean;
  /** v1.422.0 (B1): lib/buildRetry.continueOffered for this reply — its
   *  rounds were kept, so "Continue from where it stopped" is the first
   *  press (Retry the second). */
  canContinue?: boolean;
  onContinue?: () => void;
  /** v1.422.0 (B1): the user already continued this failed reply below. */
  continuedBelow?: boolean;
  /** v1.449.0 (G-N item 8): the model catalog (the refused model's name). */
  models?: readonly ModelOption[];
  /** v1.449.0: the apps holding the session Local only — with any, no
   *  "Send once" (the daemon would refuse it; the chat page's rule). */
  localOnlyHeld?: readonly string[];
  /** v1.449.0: "Choose a local model" opens the session's model menu. */
  onChooseLocal?: () => void;
  /** v1.449.0: "Send to <model> once" — this reply's question again, with
   *  the refused provider allowed for that one message. */
  onSendOnce?: (provider: string) => void;
}) {
  const failed = typeof m.error === "string" && m.error.trim() !== "";
  if (!failed && m.interrupted !== true) return null;
  if (carriedOn) {
    // v1.391.0: the reply goes on below — not a failure to act on.
    return (
      <div data-testid="harness-reply-failure" className="mt-1 flex flex-wrap items-center gap-x-1 gap-y-0.5">
        <p data-testid="harness-reply-carried" className="flex basis-full items-start gap-1.5 text-[12px] text-zinc-400">
          <CircleAlert size={12} className="mt-0.5 shrink-0" aria-hidden /> {CUT_CARRIED_ON}
        </p>
        {retry ? (
          <button
            type="button"
            data-testid="harness-retry"
            onClick={onRetry}
            title="Send this reply's question again"
            className={PRESS}
          >
            <RotateCcw size={12} aria-hidden />
            {RETRY_WORDS}
          </button>
        ) : null}
      </div>
    );
  }
  const action = failed
    ? failureAction({ fixKind: m.errorKind, fixProvider: m.errorProvider }, health, turnProvider)
    : null;
  const live = newest && !busy;
  const press = live && action && action.kind !== "refusal" && action.kind !== "local_only" ? action : null;
  const headline = typeof m.errorTitle === "string" ? m.errorTitle.trim() : "";
  const words = failed ? (press && headline ? headline : (m.error as string)) : "This answer is incomplete.";
  return (
    <div data-testid="harness-reply-failure" className="mt-1 flex flex-wrap items-center gap-x-1 gap-y-0.5">
      {failed ? (
        <p
          data-testid="harness-reply-error"
          className="flex min-w-0 basis-full items-start gap-1.5 text-[12px] text-tone-danger"
        >
          <CircleAlert size={12} className="mt-0.5 shrink-0" aria-hidden /> {words}
        </p>
      ) : (
        <p className="flex basis-full items-center gap-1.5 text-[12px] text-tone-warn">
          <CircleAlert size={12} aria-hidden /> {words}
        </p>
      )}
      {/* v1.378.0: A SAFETY REFUSAL on the newest reply. The provider judges
          the whole conversation, so the way out is back to before the message
          that set it off: the press opens the "/rewind" list. Never a Retry —
          the same conversation is declined again. */}
      {live && action?.kind === "refusal" && canRewind ? (
        <button type="button" data-testid="harness-refusal-rewind" onClick={onRewind} className={PRESS}>
          <History size={12} aria-hidden />
          {REWIND_PICK_WORDS}
        </button>
      ) : null}
      {/* v1.449.0 (Wave B, G-N item 8): A LOCAL ONLY REFUSAL on the newest
          reply — the chat page's two ways out: pick a local model (opens the
          session's model menu), or send THIS one message to the model it
          named (the press is the OK; the session stays Local only). Never
          offered while an app holds the session local. No Retry: it would be
          refused again. */}
      {live && action?.kind === "local_only" && onChooseLocal ? (
        <button type="button" data-testid="harness-local-choose" onClick={onChooseLocal} className={PRESS}>
          {CHOOSE_LOCAL_WORDS}
        </button>
      ) : null}
      {/* (review) …and only with a provider to send to: a line the daemon
          stamped with no `errorProvider` has nothing for the press to allow,
          and `sendOnceReply` would drop an empty one on the floor. */}
      {live && action?.kind === "local_only" && action.provider && onSendOnce && localOnlyHeld.length === 0 ? (
        <button
          type="button"
          data-testid="harness-local-send-once"
          onClick={() => onSendOnce(action.provider)}
          title="Send only this message there. The session stays local only."
          className={PRESS}
        >
          {sendOnceWords(cloudModelName(action.provider, "", models))}
        </button>
      ) : null}
      {/* THE PRESS THAT FIXES IT, before Retry: the one Sign in (a Build
          terminal with the CLI started in it, on this PC's own login), or
          the link to where keys live. */}
      {press?.kind === "sign_in" ? (
        <span data-testid="harness-sign-in" className="text-[12px]">
          <CliSignInPress provider={press.cli.provider} variant="inline" testIdPrefix="harness" />
        </span>
      ) : null}
      {press?.kind === "key" ? (
        <Link href={KEY_LINK_HREF} data-testid="harness-key-link" className={PRESS}>
          {KEY_LINK_WORDS}
        </Link>
      ) : null}
      {/* v1.422.0 (B1): the reply's rounds were KEPT — carry it on from the
          work it already did (the main press); Retry asks the question again
          from the start. */}
      {canContinue && onContinue ? (
        <button
          type="button"
          data-testid="harness-continue"
          onClick={onContinue}
          title={CONTINUE_TITLE}
          className={PRESS_MAIN}
        >
          <Play size={12} aria-hidden />
          {CONTINUE_WORDS}
        </button>
      ) : null}
      {retry ? (
        <button
          type="button"
          data-testid="harness-retry"
          onClick={onRetry}
          title={canContinue ? RETRY_FROM_START_TITLE : "Send this reply's question again"}
          className={PRESS}
        >
          <RotateCcw size={12} aria-hidden />
          {RETRY_WORDS}
        </button>
      ) : null}
      {continuedBelow ? (
        <p data-testid="harness-continued-below" className="basis-full text-[12px] text-zinc-500">
          {CONTINUED_BELOW}
        </p>
      ) : null}
      <FailureDetails technical={m.errorDetail} testId="harness-reply-error-details" />
    </div>
  );
}
