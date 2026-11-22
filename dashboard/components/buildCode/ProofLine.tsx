"use client";

/**
 * 22-a (R1.9): Iron Jarvis's check of one Build › Code reply, under that
 * reply (lib/buildAppProof). One quiet line from the daemon's own record —
 * "Checked by Iron Jarvis: 14 tests ran, 14 passed. 2 new files: …" — or,
 * the first time in a folder, the card that asks once with the exact
 * command: "Run `npm test` after each reply in this folder?" (Yes is the one
 * main action; "Not in this folder" is a quiet answer). While the check
 * runs the line says so and offers Stop. When the tests still fail after
 * the tool was sent back, the line says "this is not finished" and offers
 * Rewind to before the message (its preview first).
 *
 * Drawn once per record (no redraw per word: the record arrives whole).
 */

import { memo, useState, type ReactNode } from "react";
import { Check, CircleDashed, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui";
import {
  CHECK_NO,
  CHECK_REWIND,
  CHECK_REWIND_TITLE,
  CHECK_STOP,
  CHECK_UNFINISHED,
  CHECK_YES,
  proofShape,
  proofTone,
  type ProofMark,
} from "@/lib/buildAppProof";

/** What the page lets the line do (absent = nothing to press). */
export interface ProofActions {
  /** The card's answer: the command it showed, yes or no, the reply it was under. */
  onAnswer: (command: string, run: boolean, turn: string) => Promise<string | null>;
  /** Stop the running check. */
  onStop: () => void;
  /** Open the rewind preview for the message the work started from (absent =
   *  the folder's checkpoint of that message is not kept). */
  onRewindAsk?: (at: string) => void;
  /** The messages whose checkpoint is kept (Rewind is offered only there). */
  rewindAt?: ReadonlySet<string>;
}

/** The daemon's words with `code` drawn as code (a command, a file name).
 *  Wrapped where it must be, never mid-name: opened at 390 px, `break-all`
 *  split `limits.py` at a line's end; a long command still wraps. */
export function withCode(text: string): ReactNode[] {
  return text.split("`").map((part, i) =>
    i % 2 === 1 ? (
      <code key={i} className="rounded bg-white/[0.06] px-1 font-mono text-[11.5px] text-zinc-300 [overflow-wrap:anywhere]">
        {part}
      </code>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

function CheckCard({ proof, actions }: { proof: ProofMark; actions?: ProofActions | null }) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  const answer = async (run: boolean) => {
    if (!actions || busy) return;
    setBusy(true);
    setSaid("");
    try {
      const problem = await actions.onAnswer(proof.command, run, proof.forTurn);
      if (problem) setSaid(problem);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      data-testid="code-proof-card"
      role="group"
      aria-label="Iron Jarvis asks"
      className="grid min-w-0 gap-2 rounded-xl bg-white/[0.03] p-3 shadow-[0_0_0_0.5px_rgb(var(--accent-rgb)/0.5)]"
    >
      <p data-testid="code-proof-ask" className="min-w-0 break-words text-[13px] text-zinc-200">
        {withCode(proof.line)}
      </p>
      {proof.detail ? <p className="min-w-0 break-words text-[12px] text-zinc-400">{proof.detail}</p> : null}
      {actions ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="primary"
            size="sm"
            data-testid="code-proof-yes"
            disabled={busy}
            onClick={() => void answer(true)}
          >
            {CHECK_YES}
          </Button>
          <button
            type="button"
            data-testid="code-proof-no"
            disabled={busy}
            onClick={() => void answer(false)}
            className="inline-flex h-7 items-center rounded-lg px-2 text-[13px] text-zinc-300 transition-colors hover:bg-white/[0.06] disabled:opacity-50"
          >
            {CHECK_NO}
          </button>
        </div>
      ) : null}
      {said ? (
        <p data-testid="code-proof-refused" role="alert" className="text-[12px] text-tone-warn">
          {said}
        </p>
      ) : null}
    </div>
  );
}

export const ProofLine = memo(function ProofLine({
  proof,
  newestAsk,
  checkingNow = true,
  actions,
}: {
  proof: ProofMark;
  /** This is the session's newest check row (only it may ask or run). */
  newestAsk: boolean;
  /** The session says a check runs now (absent = trust the record). */
  checkingNow?: boolean;
  actions?: ProofActions | null;
}) {
  const shape = proofShape(proof, newestAsk, checkingNow);
  if (shape === "none") return null;
  if (shape === "unfinished") {
    return (
      <p data-testid="code-proof" data-state="unfinished" className="flex items-center gap-2 text-[12px] text-zinc-500">
        <CircleDashed size={13} aria-hidden="true" className="shrink-0" />
        <span className="min-w-0 break-words">{CHECK_UNFINISHED}</span>
      </p>
    );
  }
  if (shape === "card") return <CheckCard proof={proof} actions={actions} />;
  if (shape === "checking") {
    return (
      <p data-testid="code-proof" data-state="checking" role="status" className="flex flex-wrap items-center gap-x-2 text-[12px] text-zinc-500">
        <CircleDashed size={13} aria-hidden="true" className="shrink-0" />
        <span className="min-w-0 break-words">{withCode(proof.line)}</span>
        {actions ? (
          <button
            type="button"
            data-testid="code-proof-stop"
            onClick={actions.onStop}
            className="inline-flex h-6 items-center rounded-md px-1.5 text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.06]"
          >
            {CHECK_STOP}
          </button>
        ) : null}
      </p>
    );
  }
  const tone = proofTone(proof);
  const rewind =
    proof.ended && proof.originAt && actions?.onRewindAsk && actions.rewindAt?.has(proof.originAt) ? actions : null;
  return (
    <div data-testid="code-proof" data-state="done" data-tests={proof.tests} className="grid gap-1 text-[12px] text-zinc-500">
      <p className="flex min-w-0 items-start gap-2">
        {tone === "ok" ? (
          <Check size={13} aria-hidden="true" className="mt-0.5 shrink-0 text-tone-success" />
        ) : tone === "warn" ? (
          <TriangleAlert size={13} aria-hidden="true" className="mt-0.5 shrink-0 text-tone-warn" />
        ) : (
          <CircleDashed size={13} aria-hidden="true" className="mt-0.5 shrink-0" />
        )}
        <span data-testid="code-proof-line" className="min-w-0 break-words">
          {withCode(proof.line)}
        </span>
      </p>
      {rewind ? (
        <button
          type="button"
          data-testid="code-proof-rewind"
          title={CHECK_REWIND_TITLE}
          onClick={() => rewind.onRewindAsk?.(proof.originAt)}
          className="ml-5 inline-flex h-7 w-fit items-center rounded-lg px-2 text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.06]"
        >
          {CHECK_REWIND}
        </button>
      ) : null}
    </div>
  );
});

export default ProofLine;
