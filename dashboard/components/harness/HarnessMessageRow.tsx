"use client";

/**
 * ONE message of a coding session's transcript (v1.417.0, C2.1) — lifted out
 * of HarnessSession so it can be MEMOISED: the chat page's `MessageRow` rule
 * (v1.250.0). The session re-renders for many things (a tool step, the box,
 * a card); a settled message changes only when ITS props do, so a long
 * conversation is no longer redrawn whole for each of them.
 *
 * Props are plain values plus ONE handlers object (`h`) whose functions are
 * stable for the session's life (they read the session's latest closures
 * through a ref). `undoFor`/`onUndo` are passed as they are: the receipts and
 * the changed-files line call `undoFor` while they render, so a new Undo
 * state must redraw them. The DOM is exactly the one HarnessSession drew.
 */

import { memo, useId, useMemo, useState, type ComponentProps, type ReactNode } from "react";
import { Check, ChevronDown, CircleAlert, GitFork, Loader2, TriangleAlert } from "lucide-react";
import { HarnessFailureLine } from "@/components/harness/HarnessFailure";
import { retryOffered } from "@/lib/buildRetry";
import { RESUMED_LINE, resumedFrom } from "@/lib/buildResume";
import { receiptFacts } from "@/lib/receiptFacts";
import { answeredModelName } from "@/lib/answeredModel";
import type { ModelOption } from "@/lib/types";
import { MemoMarkdown } from "@/components/Markdown";
import { StepRun, WorkLine, workDuration } from "@/components/chat/WorkLine";
import { failedSteps, finishWarn, finishWords } from "@/lib/stepWords";
import { replyShapeFor, replyWorkSteps, settledReplyItems } from "@/lib/replyItems";
import { ReplyChanges } from "@/components/chat/ReplyChanges";
import { TurnReceipt } from "@/components/chat/TurnReceipt";
import { PreferenceSuggestion } from "@/components/chat/PreferenceSuggestion";
import { decodeSuggestion } from "@/lib/preferences";
import { DoorsStrip } from "@/components/chat/DoorsStrip";
import type { ChangeUndoState } from "@/components/chat/ChangedFiles";
import { paneTurnWindow, paneWorkInput } from "@/components/terminal/paneWork";
import { RewindControl } from "@/components/harness/RewindControl";
import { HARNESS_REPLY_PROSE, HARNESS_USER_BUBBLE } from "@/components/harness/harnessLook";
import { CapMetLine, SummarizedLine } from "@/components/harness/HarnessLongJobs";
import { decodeCapMet, decodeSummarized } from "@/lib/buildLongJobs";
import { SavedHelperRows } from "@/components/harness/HarnessHelpers";
import { AttemptsCardView } from "@/components/harness/HarnessAttempts";
import { TeamReplyLine } from "@/components/harness/HarnessTeam";
import { decodeReplyTeam, type TeamRoster } from "@/lib/sessionTeam";
import { SettledPlan } from "@/components/harness/HarnessPlan";
import { replyPlan } from "@/lib/buildContext";
import { SentAttachments } from "@/components/harness/HarnessAttachments";
import { SentPicks } from "@/components/harness/HarnessComposerMenus";
import { BRANCH_LABEL, BRANCH_TITLE_QUESTION, BRANCH_TITLE_REPLY, type BranchAt } from "@/lib/buildNext";
import type { RewindExtras } from "@/lib/buildRewind";
import { accountMoveLine, decodeAccountLimit, decodeAccountMove, limitCardWords } from "@/lib/buildAccounts";
import {
  decodeFinish,
  decodeStepLimit,
  KEEP_GOING_TEXT,
  stepLimitWords,
  type BuildMessage,
} from "@/lib/buildSessions";

/** v1.377.0 (Phase 6): "Branch from here" on a message — one press starts a
 *  NEW session (nothing of this one changes, so no confirm). Quiet until the
 *  message is hovered or the control has focus, like Rewind. */
function BranchButton({
  at,
  turnId,
  working,
  disabled,
  onBranch,
}: {
  at: BranchAt;
  turnId: string;
  working: boolean;
  disabled: boolean;
  onBranch: (turnId: string, at: BranchAt) => void;
}) {
  return (
    <button
      type="button"
      data-testid={`harness-branch-${at}`}
      data-turn={turnId}
      disabled={disabled || working}
      title={at === "reply" ? BRANCH_TITLE_REPLY : BRANCH_TITLE_QUESTION}
      onClick={() => onBranch(turnId, at)}
      className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px] text-zinc-500 opacity-0 transition-opacity hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:cursor-not-allowed group-hover/msg:opacity-100 [@media(hover:none)]:opacity-100"
    >
      {working ? <Loader2 size={12} className="animate-spin" aria-hidden /> : <GitFork size={12} aria-hidden />}
      {BRANCH_LABEL}
    </button>
  );
}

/** One piece of the reply line's summary; `warn` = said in the warn tone. */
export interface ReplyLinePart {
  key: string;
  text: string;
  warn?: boolean;
}

/**
 * v1.418.0 (fix plan C2.5): the words of a settled reply's ONE line, read off
 * its own record — how long it worked, how many steps, the finish check's
 * verdict, what failed, a cut-off — each only when present:
 *   "Worked for 2 min 14 s · 7 steps · Checked: tests passed"
 * ("answered by …", the tools and the files are the receipt's own words,
 * drawn right after it on the same line). Pure.
 */
export function replyLineParts(m: BuildMessage): ReplyLinePart[] {
  const steps = replyWorkSteps(m.steps);
  const parts: ReplyLinePart[] = [];
  const dur = workDuration(m.timing ?? null);
  const thought = typeof m.thinking === "string" && m.thinking.trim().length > 0;
  if (dur) parts.push({ key: "time", text: steps.length || thought ? `Worked for ${dur}` : `Answered in ${dur}` });
  if (steps.length) parts.push({ key: "steps", text: steps.length === 1 ? "1 step" : `${steps.length} steps` });
  const fin = decodeFinish(m.finish);
  if (fin) parts.push({ key: "finish", text: finishWords(fin), warn: finishWarn(fin) });
  const failed = failedSteps(steps as { ok?: boolean | null }[]);
  if (failed) parts.push({ key: "failed", text: `${failed} failed`, warn: true });
  if (m.truncated || m.interrupted) parts.push({ key: "cut", text: "cut off", warn: true });
  return parts;
}

/**
 * v1.418.0 (fix plan C2.5): ONE line under a settled reply. Its summary
 * (replyLineParts) is a press that opens everything that used to stack under
 * the reply — the "Worked for" fold with every step and the finish check,
 * the task list, the changed files (with Undo) and, under the newest reply,
 * the next-step recap — and the receipt ("answered by …", its warnings
 * always visible) and Branch from here sit on the same line.
 */
function ReplyLine({
  parts,
  hasPanel,
  panel,
  after,
}: {
  parts: ReplyLinePart[];
  hasPanel: boolean;
  panel: () => ReactNode;
  after: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const warn = parts.some((p) => p.warn);
  return (
    <div data-testid="reply-line" className="mt-1 flex min-w-0 flex-wrap items-center">
      {hasPanel ? (
        <button
          type="button"
          data-testid="reply-line-toggle"
          aria-expanded={open}
          aria-controls={open ? panelId : undefined}
          onClick={() => setOpen((v) => !v)}
          title={open ? "Hide what this reply did" : "Show what this reply did: its steps, the check, the plan and the files"}
          className="inline-flex min-h-7 min-w-0 max-w-full items-center gap-1.5 rounded-md px-1.5 text-left text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
        >
          {warn ? (
            <TriangleAlert size={12} aria-hidden="true" className="shrink-0 text-tone-warn" />
          ) : (
            <Check size={12} aria-hidden="true" className="shrink-0" />
          )}
          <span data-testid="reply-line-words" className="min-w-0 truncate">
            {parts.length
              ? parts.map((p, i) => (
                  <span key={p.key} className={p.warn ? "text-tone-warn" : undefined}>
                    {i > 0 ? " · " : ""}
                    {p.text}
                  </span>
                ))
              : "Details"}
          </span>
          <ChevronDown
            size={12}
            aria-hidden="true"
            className={`shrink-0 transition-transform motion-reduce:transition-none ${open ? "rotate-180" : ""}`}
          />
        </button>
      ) : null}
      {after}
      {open && hasPanel ? (
        <div
          id={panelId}
          data-testid="reply-line-panel"
          className="ml-1.5 mt-1 min-w-0 basis-full space-y-2 border-l border-white/[0.08] py-1 pl-3"
        >
          {panel()}
        </div>
      ) : null}
    </div>
  );
}

/** What a row hands back to its session — one object, stable for the
 *  session's life (HarnessSession builds it once). */
export interface HarnessRowHandlers {
  /** A rewind to before a message landed (its question back in the box). */
  onRewound: (question: string, extras?: RewindExtras) => void;
  /** "Branch from here". */
  onBranch: (turnId: string, at: BranchAt) => void;
  /** An attempt was kept (read the session again). */
  onAttemptKept: () => void;
  /** The cap line's Keep going. */
  onKeepGoing: () => void;
  /** Retry the reply at this index. */
  onRetry: (index: number) => void;
  /** Open the "/rewind" list. */
  onOpenRewind: () => void;
  /** Open a document the receipt names. */
  onOpenDocument: (path: string) => void;
}

type Health = ComponentProps<typeof HarnessFailureLine>["health"];

export interface HarnessMessageRowProps {
  m: BuildMessage;
  index: number;
  threadId: string;
  /** The session's ledger id (`build:<id>`), for the changed-files line. */
  sessionId: string;
  archived: boolean;
  busy: boolean;
  /** This is the question being sent (not yet written by the daemon). */
  isPending: boolean;
  /** "question:<turn>" / "reply:<turn>" while that branch starts, else null. */
  branching: string | null;
  /** This is the reply that ends the conversation. */
  newest: boolean;
  /** A restart cut this reply off and the app carried it on (lib/buildResume). */
  carriedOn: boolean;
  /** The session has a message to rewind to. */
  canRewind: boolean;
  /** The `at` of the user message before this reply (its changes window). */
  prevUserAt: string | undefined;
  /** The session's team (EMPTY_ROSTER when it has none) — for a team reply. */
  teamRoster: TeamRoster;
  models: readonly ModelOption[];
  health: Health;
  /** The session's provider (a reply that kept none is read on it). */
  provider: string;
  /** v1.418.0 (C2.5): the next-step box's recap, under the NEWEST settled
   *  reply only ("" elsewhere) — folded into this reply's line (the box keeps
   *  its buttons visible). */
  nextRecap?: string;
  undoFor: (path: string) => ChangeUndoState | null;
  onUndo: (actionId: string, path: string) => Promise<void>;
  h: HarnessRowHandlers;
}

function HarnessMessageRowImpl({
  m,
  index,
  threadId,
  sessionId,
  archived,
  busy,
  isPending,
  branching,
  newest,
  carriedOn,
  canRewind,
  prevUserAt,
  teamRoster,
  models,
  health,
  provider,
  nextRecap = "",
  undoFor,
  onUndo,
  h,
}: HarnessMessageRowProps) {
  // v1.418.0 (C2.3): the reply in order — its text and its runs of steps,
  // placed the way this window saw them run (lib/replyItems).
  const items = useMemo(
    () => (m.role === "assistant" ? settledReplyItems(m.content ?? "", m.steps, replyShapeFor(m.turnId)) : []),
    [m],
  );
  const lineParts = useMemo(() => (m.role === "assistant" ? replyLineParts(m) : []), [m]);
  // v1.405.0: the reply's changes window (the user message before it, its
  // own timing and documents) — read once per message, never per render.
  const turnWin = useMemo(() => {
    if (m.role !== "assistant") return null;
    const before = prevUserAt === undefined ? [] : [{ role: "user" as const, content: "", at: prevUserAt }];
    const w = paneTurnWindow([...before, m], before.length);
    return w ? { ...w, sessionId } : null;
  }, [m, prevUserAt, sessionId]);
  return (
    m.role === "user" ? (
      m.steer ? (
        <div className="flex justify-end">
          <p data-testid="harness-steer-line" className="max-w-[88%] text-right text-[12px] text-zinc-500">
            Note while it worked: {m.content}
          </p>
        </div>
      ) : (
        <div className="group group/msg flex flex-col items-end gap-1">
          {m.content ? (
            <div data-testid="harness-user-bubble" className={HARNESS_USER_BUBBLE}>
              {m.content}
            </div>
          ) : null}
          {/* v1.371.0: the pictures and files the message carried. */}
          <SentAttachments paths={m.attachmentPaths ?? []} />
          {/* v1.373.0: the folder's files picked with "@", and the
              skill picked with "/". */}
          <SentPicks files={m.filePaths} skill={m.skill} />
          {/* v1.370.0: rewind to before this message (two presses;
              a refusal is listed in words). Only a message the
              daemon wrote has a turn to rewind to. */}
          {m.turnId && !isPending ? (
            <RewindControl
              threadId={threadId}
              turnId={m.turnId}
              disabled={busy || archived}
              onRewound={h.onRewound}
              onTouched={() => h.onRewound("")}
            />
          ) : null}
          {/* v1.377.0: a new session from before this message (it
              goes in that session's box). */}
          {m.turnId && !isPending ? (
            <BranchButton
              at="question"
              turnId={m.turnId}
              working={branching === `question:${m.turnId}`}
              disabled={!!branching}
              onBranch={h.onBranch}
            />
          ) : null}
        </div>
      )
    ) : (
      <div data-testid="harness-reply" className="group/msg min-w-0">
        {/* v1.391.0: this reply carried on one a restart cut off. */}
        {resumedFrom(m) ? (
          <p data-testid="harness-resumed-line" className="mb-1 text-[12px] text-zinc-400">
            {RESUMED_LINE}
          </p>
        ) : null}
        {/* v1.393.0: this reply carried on with another account of
            the same group ("<A> reached its limit; carried on with
            <B> (same group)."). */}
        {(() => {
          const move = decodeAccountMove(m.accountMove);
          return move ? (
            <p data-testid="harness-account-move" className="mb-1 text-[12px] text-zinc-400">
              {accountMoveLine(move)}
            </p>
          ) : null;
        })()}
        {/* v1.403.0: a team reply says who led, worked and reviewed —
            and, in the warn tone and never folded away, every time
            the lead did a piece itself. */}
        <TeamReplyLine raw={m.team} />
        {/* v1.392.0: the reply summarized its own older rounds. */}
        <SummarizedLine summarized={decodeSummarized(m.summarized)} />
        {/* v1.372.0: the helper agents this reply's turn started. */}
        <SavedHelperRows
          raw={m.helpers}
          models={models}
          // v1.403.0: in a team reply each helper is named by its part.
          team={decodeReplyTeam(m.team) ? teamRoster : null}
        />
        {/* v1.397.0: the attempts on several models, side by side —
            and Keep (only while nothing runs). */}
        {m.turnId && m.attempts ? (
          <AttemptsCardView
            raw={m.attempts}
            threadId={threadId}
            turnId={m.turnId}
            canKeep={!busy && !archived}
            onKept={h.onAttemptKept}
          />
        ) : null}
        {/* v1.418.0 (C2.3): the reply IN ORDER — its words and its runs of
            steps (each run one folded line that opens to its rows). */}
        <div data-testid="harness-reply-items" className="min-w-0 space-y-2">
          {items.map((it, k) =>
            it.kind === "text" ? (
              <div key={k} data-testid="harness-reply-text" className={HARNESS_REPLY_PROSE}>
                <MemoMarkdown content={it.text} />
              </div>
            ) : (
              <StepRun key={k} steps={it.steps as { name: string }[]} />
            ),
          )}
        </div>
        {/* v1.384.0: the reply met its STEP LIMIT — said plainly.
            v1.406.0 part 4: its ONE press to carry on ("Keep going",
            the same as typing "continue"; never automatic) is the
            next-step box's first button now, under the newest reply
            — one place, never twice. */}
        {(() => {
          const sl = decodeStepLimit(m.stepLimit);
          if (!sl) return null;
          return (
            <div data-testid="harness-step-limit" className="mt-2 flex flex-wrap items-center gap-2">
              <p className="flex items-center gap-1.5 text-[12px] text-zinc-400">
                <CircleAlert size={12} aria-hidden className="shrink-0" />
                {stepLimitWords(sl)}
              </p>
            </div>
          );
        })()}
        {/* v1.392.0 (decision 8): the session's cap ended the reply —
            the step-limit way: said plainly (its Keep going is the
            next-step box's, v1.406.0 part 4). */}
        <CapMetLine
          cap={decodeCapMet(m.capMet)}
          offer={false}
          keepGoingText={KEEP_GOING_TEXT}
          onKeepGoing={h.onKeepGoing}
        />
        {(() => {
          // Phase 5: the limit card this reply ended on, as one quiet
          // line in the transcript (the live card is below).
          const lim = decodeAccountLimit(m.accountLimit);
          return lim ? (
            <p data-testid="harness-reply-limit" className="mt-1 flex items-start gap-1.5 text-[12px] text-tone-warn">
              <CircleAlert size={12} className="mt-0.5 shrink-0" aria-hidden /> {lim.accountName}:{" "}
              {limitCardWords(lim).title}
            </p>
          ) : null;
        })()}
        {/* Harness gaps (fix 2): the chat's failure line at a
            session's scale (HarnessFailure) — the sentence (or its
            headline beside a press), Sign in / the key link, Retry on
            the NEWEST failed or cut-off reply, the server's own words
            folded under Details. A safety refusal keeps its rewind
            list (v1.378.0), never a Retry. A reply that ended on an
            account's limit card says only that it is incomplete (the
            card is its way out). */}
        <HarnessFailureLine
          m={m.accountLimit ? { ...m, error: undefined } : m}
          newest={newest}
          busy={busy}
          health={health}
          turnProvider={m.route?.provider || provider}
          retry={retryOffered(m, { newest, busy, archived })}
          onRetry={() => h.onRetry(index)}
          canRewind={canRewind}
          onRewind={h.onOpenRewind}
          carriedOn={carriedOn}
        />
        <ReplyLine
          parts={lineParts}
          hasPanel={
            lineParts.length > 0 ||
            !!turnWin ||
            !!nextRecap ||
            !!replyPlan(m.plan, !!m.error) ||
            (typeof m.thinking === "string" && !!m.thinking.trim()) ||
            (Array.isArray(m.steps) && m.steps.length > 0) ||
            (Array.isArray(m.toolsUsed) && m.toolsUsed.length > 0)
          }
          panel={() => (
            <>
              <WorkLine
                {...paneWorkInput(m)}
                // The line above already says how long it worked ("Worked
                // for" is said once): the fold lists what it did.
                timing={null}
                cutOff={m.truncated ? "truncated" : m.interrupted ? "interrupted" : null}
                // Part 3, Phase 2: the reply's finish check, in one sentence.
                finish={decodeFinish(m.finish)}
              />
              {/* v1.374.0: the turn's task list, folded into its reply. */}
              <SettledPlan plan={replyPlan(m.plan, !!m.error)} />
              <ReplyChanges turn={turnWin} undoFor={undoFor} onUndo={onUndo} />
              {nextRecap ? (
                <p data-testid="next-step-recap" className="text-[13px] leading-relaxed text-zinc-400">
                  {nextRecap}
                </p>
              ) : null}
            </>
          )}
          after={
            <>
          <TurnReceipt
            inline
            modelName={answeredModelName(m.route, models)}
            route={m.route}
            toolsUsed={m.toolsUsed}
            deniedTools={m.deniedTools}
            trust={m.trust}
            trustReason={m.trustReason}
            trustNote={m.trustNote}
            usage={m.usage}
            steps={m.steps}
            timing={m.timing}
            documents={m.documents}
            onOpenDocument={h.onOpenDocument}
            undoFor={undoFor}
            onUndo={onUndo}
            // Harness gaps (fix 3): every receipt fact the chat page
            // passes (app/chat/page.tsx), read off the daemon's reply
            // line and decoded the chat's way — the approvals the
            // user gave, the older messages left out to fit, the
            // envelope's adaptation, a kept preference, the folder's
            // rules and the earlier chats read.
            {...receiptFacts(m)}
          />
          {/* v1.377.0: a new session with the conversation up to
              and including this reply. */}
          {m.turnId ? (
            <BranchButton
              at="reply"
              turnId={m.turnId}
              working={branching === `reply:${m.turnId}`}
              disabled={!!branching}
              onBranch={h.onBranch}
            />
          ) : null}
            </>
          }
        />
        {/* v1.414.0: a repeated correction, offered as a standing
            preference — the chat's own quiet Keep · Edit · Not this
            line (components/chat/PreferenceSuggestion). Its answer
            comes back folded into the line by the daemon on the next
            read; the line keeps it meanwhile. */}
        {(() => {
          const sug = decodeSuggestion(m.suggestion);
          return sug && sug.state !== "gone" ? (
            <PreferenceSuggestion key={sug.id} suggestion={sug} />
          ) : null;
        })()}
        <DoorsStrip doors={m.doors} />
      </div>
    )
  );
}

/** Memoised: a settled message redraws only when its own props change. */
export const HarnessMessageRow = memo(HarnessMessageRowImpl);

/** The `at` of the user message before each message (undefined when none) —
 *  one pass over the conversation, so each row gets a plain value. */
export function prevUserAts(messages: readonly BuildMessage[]): (string | undefined)[] {
  const out: (string | undefined)[] = [];
  let last: string | undefined;
  let seen = false;
  for (const msg of messages) {
    out.push(seen ? last : undefined);
    if (msg.role === "user") {
      seen = true;
      last = typeof msg.at === "string" ? msg.at : undefined;
    }
  }
  return out;
}
