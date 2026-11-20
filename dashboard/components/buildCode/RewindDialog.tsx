"use client";

/**
 * v1.543.0 — Build › Code: what "Rewind to here…" would do to the folder's
 * files, BEFORE anything changes (`GET …/rewind/preview`;
 * hosted/checkpoints.py). The dialog is the action's second press.
 *
 * It lists the files that go back, the files made since that move to Iron
 * Jarvis's trash, what is left as it is and why, and the commits that stay.
 * Then one choice: conversation and files, the conversation only, or the files
 * only. A tool with no conversation rewind offers the files only. When the
 * files cannot go back right now (a reply runs, a file is open in another
 * program) the reason is said and only the conversation's own press stays.
 *
 * Built on <Modal>: Escape and the backdrop close it (not while busy), focus
 * moves in and returns to the opener.
 */

import { Modal } from "@/components/Modal";
import {
  FILES_DIALOG_TITLE,
  REWIND_BOTH,
  REWIND_CONVERSATION_ONLY,
  REWIND_DIALOG_TITLE,
  REWIND_FILES_ONLY,
  REWIND_FILES_PRESS,
  REWIND_FOOT,
  REWIND_FOOT_OWN,
  REWIND_NOTHING,
  REWIND_READ_AGAIN,
  REWIND_READING,
  REWIND_TO_HERE,
  andMoreWords,
  commitsStayWords,
  filesBackWords,
  filesLeftWords,
  filesNewWords,
  filesProblemWords,
  type RewindFileNote,
  type RewindPlan,
} from "@/lib/buildAppBranch";

const QUIET =
  "inline-flex h-8 items-center rounded-lg px-3 text-[13px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50";
const MAIN =
  "inline-flex h-8 items-center rounded-lg border border-accent/30 bg-accent/[0.08] px-3 text-[13px] font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50";

function NameList({ title, names, total, testId }: { title: string; names: string[]; total: number; testId: string }) {
  if (!total) return null;
  return (
    <div data-testid={testId}>
      <p className="text-[12px] text-zinc-400">{title}</p>
      <ul className="mt-1 space-y-0.5 font-mono text-[12px] text-zinc-300">
        {names.map((n) => (
          <li key={n} className="break-all">
            {n}
          </li>
        ))}
        {total > names.length ? <li className="font-sans text-zinc-500">{andMoreWords(total - names.length)}</li> : null}
      </ul>
    </div>
  );
}

function NoteList({
  title,
  rows,
  total,
  testId,
  danger = false,
}: {
  title: string;
  rows: RewindFileNote[];
  total: number;
  testId: string;
  danger?: boolean;
}) {
  if (!total) return null;
  return (
    <div data-testid={testId}>
      <p className={`text-[12px] ${danger ? "text-tone-danger" : "text-zinc-400"}`}>{title}</p>
      <ul className="mt-1 space-y-0.5 text-[12px] text-zinc-300">
        {rows.map((r) => (
          <li key={r.rel} className="break-all">
            <span className="font-mono">{r.rel}</span>
            {r.why ? <span className="text-zinc-500"> ({r.why})</span> : null}
          </li>
        ))}
        {total > rows.length ? <li className="text-zinc-500">{andMoreWords(total - rows.length)}</li> : null}
      </ul>
    </div>
  );
}

export interface RewindDialogProps {
  /** The preview, once read (null while it is read, or when it failed). */
  plan: RewindPlan | null;
  /** Why the preview could not be read (the daemon's own sentence). */
  error: string | null;
  /** A press is out. */
  busy: boolean;
  /** This tool can rewind the conversation from this message. */
  conversation: boolean;
  onClose: () => void;
  onReadAgain: () => void;
  onBoth: (expect: string) => void;
  onConversation: () => void;
  onFiles: (expect: string) => void;
}

export function RewindDialog({
  plan,
  error,
  busy,
  conversation,
  onClose,
  onReadAgain,
  onBoth,
  onConversation,
  onFiles,
}: RewindDialogProps) {
  const heading = conversation ? REWIND_DIALOG_TITLE : FILES_DIALOG_TITLE;
  const changes = plan ? plan.backCount + plan.madeCount : 0;
  // The files can go back: the plan allows it, nothing is in the way, and
  // there is something to put back.
  const filesOk = Boolean(plan && plan.can && !plan.blocked && changes > 0);
  return (
    <Modal label={heading} onClose={onClose} busy={busy} className="w-full max-w-xl" testId="code-rewind-dialog">
      <div className="space-y-3 px-5 pb-4 pt-5">
        <h2 className="text-[15px] font-medium text-zinc-100">{heading}</h2>
        {!plan && !error ? (
          <p role="status" data-testid="code-rewind-reading" className="text-[13px] text-zinc-400">
            {REWIND_READING}
          </p>
        ) : null}
        {error ? (
          <p role="alert" data-testid="code-rewind-error" className="text-[13px] text-tone-danger">
            {error}
          </p>
        ) : null}
        {plan ? (
          <div className="max-h-[50vh] space-y-3 overflow-y-auto pr-1">
            {changes === 0 && !plan.problemsCount ? (
              <p data-testid="code-rewind-nothing" className="text-[13px] text-zinc-400">
                {REWIND_NOTHING}
              </p>
            ) : null}
            <NameList
              title={filesBackWords(plan.backCount)}
              names={plan.back}
              total={plan.backCount}
              testId="code-rewind-back"
            />
            <NameList
              title={filesNewWords(plan.madeCount)}
              names={plan.made}
              total={plan.madeCount}
              testId="code-rewind-new"
            />
            <NoteList
              title={filesProblemWords(plan.problemsCount)}
              rows={plan.problems}
              total={plan.problemsCount}
              testId="code-rewind-problems"
              danger
            />
            <NoteList
              title={filesLeftWords(plan.leftCount)}
              rows={plan.left}
              total={plan.leftCount}
              testId="code-rewind-left"
            />
            {plan.commitsSince ? (
              <p data-testid="code-rewind-commits" className="text-[12px] text-zinc-400">
                {commitsStayWords(plan.commitsSince)}
              </p>
            ) : null}
            {plan.tooMany ? (
              <p role="status" data-testid="code-rewind-too-many" className="text-[12px] text-tone-danger">
                {plan.tooMany}
              </p>
            ) : null}
            {plan.blocked ? (
              <p role="status" data-testid="code-rewind-blocked" className="text-[12px] text-tone-danger">
                {plan.blocked}
              </p>
            ) : null}
            {plan.endings && plan.backCount > 0 ? (
              <p data-testid="code-rewind-endings" className="text-[12px] text-zinc-400">
                {plan.endings}
              </p>
            ) : null}
            {changes > 0 ? (
              <p data-testid="code-rewind-foot" className="text-[12px] text-zinc-500">
                {plan.own ? REWIND_FOOT_OWN : REWIND_FOOT}
              </p>
            ) : null}
          </div>
        ) : null}
        <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
          <button type="button" className={QUIET} onClick={onClose} disabled={busy} data-testid="code-rewind-cancel">
            Cancel
          </button>
          {error ? (
            <button type="button" className={QUIET} onClick={onReadAgain} disabled={busy} data-testid="code-rewind-again">
              {REWIND_READ_AGAIN}
            </button>
          ) : null}
          {plan && filesOk && conversation ? (
            <button
              type="button"
              className={QUIET}
              onClick={() => onFiles(plan.id)}
              disabled={busy}
              data-testid="code-rewind-files"
            >
              {REWIND_FILES_ONLY}
            </button>
          ) : null}
          {conversation && (plan || error) ? (
            <button
              type="button"
              className={filesOk ? QUIET : MAIN}
              onClick={onConversation}
              disabled={busy}
              data-testid="code-rewind-conversation"
            >
              {filesOk ? REWIND_CONVERSATION_ONLY : REWIND_TO_HERE}
            </button>
          ) : null}
          {plan && filesOk ? (
            <button
              type="button"
              className={MAIN}
              onClick={() => (conversation ? onBoth(plan.id) : onFiles(plan.id))}
              disabled={busy}
              data-testid="code-rewind-go"
            >
              {conversation ? REWIND_BOTH : REWIND_FILES_PRESS}
            </button>
          ) : null}
        </div>
      </div>
    </Modal>
  );
}
