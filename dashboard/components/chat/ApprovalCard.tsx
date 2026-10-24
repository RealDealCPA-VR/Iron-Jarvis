"use client";

// The mid-turn permission ask (v1.187.0) — the card a PAUSED chat turn renders
// while the daemon holds a tool call and waits for the user's decision.
//
// This is the piece that turns "silently denied with a footnote" into
// ask-then-proceed. The daemon emits an `approval` SSE frame and blocks the
// call; this card shows WHAT wants to run — for `shell` that means the exact
// command, verbatim, because approving a command you cannot read is not a
// decision — and POSTs exactly one decision back. `approval_resolved` (or the
// stream ending) clears it.
//
// THE THREE ANSWERS MEAN DIFFERENT THINGS and the card says so rather than
// leaving it to hover text:
//   Allow once             this call only; the next call asks again.
//   Allow for this chat    (the `conversation` answer; "Allow for this run"
//                          on an agent run's ask, v1.352.0)
//                          the rest of this turn is granted server-side, and
//                          `onConversation` hands the tool to the composer's
//                          armed set so LATER turns arm it via the existing
//                          "+"-menu machinery — one grant store, not two.
//   Deny                   refuses. The daemon still records the refusal in
//                          the ledger as the user's decision (deny_reason),
//                          so "the user said no" is a fact the app remembers,
//                          not a call that never happened.
//
// ONE CARD FOR A BATCH (v1.247.0): when one step asks for several calls that
// need the same permission ("rename_real_file × 8"), the daemon files ONE ask
// carrying `count` and up to three example argument sets. "Allow these N"
// runs exactly those calls; Deny refuses all of them.
//
// WAITING, NOT EXPIRING (v1.247.0): `timeoutS === 0` means the daemon waits
// until the user answers — so the card says so and shows no countdown. A
// positive value (an unattended door) is named once, never ticked.
//
// ALWAYS, EXACTLY THIS (v1.299.0): when the ask carries `can_always`, a fourth
// answer runs this call AND writes a standing grant keyed on the EXACT
// arguments (`args_hash`) in the run's scope — goal, agent, project or chat —
// for 30 days. "Always allow shell" would be a blank cheque; "always allow
// `git status` here" is a decision. The button exists ONLY when the daemon
// offered it: an older daemon never sent the field and sees today's card.
// The grant is visible and revocable on the Autonomy page (StandingGrants).
//
// ONCE ONLY (v1.332.0): in a "local only" chat a tool that reaches past this PC
// asks on every call, so its card (`onceOnly`) offers Allow once and Deny and
// nothing wider (no tab, no conversation, no Always), with one quiet line that
// says why. The daemon reads any wider answer as once anyway.
//
// A PLAN TO APPROVE (v1.350.0): the optional plan ask (tool `approve_plan`,
// args `{steps}`) is once-only too, known by its name on every surface, and
// says what each answer does to the plan instead of the local-only line.
//
// WHAT IT COSTS (v1.346.0): a call that spends money (media generation) asks
// with `costNote` — the daemon's own words for the quote ("Generate 1 image on
// Pixio for 12 credits (your price)"). The card shows it as ONE quiet line in
// the local-only line's style, right under what will run, and nothing else
// about the card changes: a card with no quote is byte-for-byte today's.
//
// SEE IT BEFORE YOU ALLOW IT (v1.348.0): what will run is drawn by
// components/chat/ApprovalPreview (ideas from 21st.dev agent-elements' Bash
// and Edit approval footers, MIT; no code taken) as a quiet block inside this
// card, never a second card: a file edit is a compact diff, a file write its
// first lines, a command one "$ <command>" line, verbatim and whole, and any
// other argument a tidy `key: value` line (nested values summarised, long
// text cut with "Show in full"; a path is never cut). It draws the REDACTED
// arguments the approval frame carries and nothing more. The buttons, their
// words, ids and the dock's keys are unchanged.
//
// ONE SET OF WORDS (v1.352.0, G24): the answers are named in lib/approvalWords
// and re-exported from here, so the bell and the mission screen say exactly
// what this card says. The wider answer is named for WHERE it applies: "Allow
// for this chat" on a chat's card (docked, or with a composer to arm), "Allow
// for this run" on an agent run's ask (the session page, a project's paused
// task) — an agent run has no conversation. The posted value is still
// `conversation`.
//
// CAN IT BE UNDONE (v1.352.0, G25): the daemon says whether the tool keeps an
// inverse (`reversible`), and the card says it in ONE quiet line under what
// will run: "You can undo this." or "This may not be undoable." A read-only
// tool, and the plan ask, get no line: there is nothing to undo.

import { useState } from "react";
import { ApprovalPreview } from "@/components/chat/ApprovalPreview";
import { Markdown } from "@/components/Markdown";
import { LoaderInline } from "@/components/ui";
import { post } from "@/lib/api";
import { exampleValue } from "@/lib/approvalPreview";
import {
  ALLOW_ONCE,
  ALLOW_TAB,
  ALWAYS_EXACT,
  DENY,
  scopeLabel,
  scopeNoun,
  undoLine,
  type ApprovalScope,
} from "@/lib/approvalWords";
import { EXIT_PLAN_TOOL, isBuiltinBrowserTool, PLAN_TOOL, toolWords } from "@/lib/toolWords";
import type { PendingApproval } from "@/lib/useChatStream";
import { ShieldQuestion } from "lucide-react";

// The card's labels ARE the app's approval words: one definition, read by
// every answering surface (the bell imports lib/approvalWords directly, so it
// never pulls this card into every page).
export {
  ALLOW_ONCE,
  ALLOW_TAB,
  ALWAYS_EXACT,
  APPROVAL_LABELS,
  DENY,
  scopeLabel,
  undoLine,
  type ApprovalScope,
} from "@/lib/approvalWords";

/** The ask as this card reads it: the stream's pending approval, plus the
 *  daemon's `reversible` flag (G25; absent on an older daemon and for a
 *  read-only tool). */
export type ApprovalAsk = PendingApproval & { reversible?: boolean };

/** One example call of a batch, on one compact line. Strings are verbatim (a
 *  path is the record); a nested value is summarised, never raw JSON. */
function ExampleLine({ args }: { args: Record<string, unknown> }) {
  const text = Object.entries(args)
    .map(([k, v]) => `${k}: ${exampleValue(v)}`)
    .join(" · ");
  // v1.329.0 (G2): a call with no arguments says so in a word, never a bare
  // dash the user has to decode.
  return (
    <p className="break-all font-mono text-[11px] text-zinc-400">
      {text || <span className="font-sans text-zinc-500">(empty)</span>}
    </p>
  );
}

/** "Waiting for you" in the words the daemon's wait actually has. */
export function waitingLine(timeoutS: number | undefined): string {
  if (timeoutS === 0) return "Waiting for you. Nothing runs until you answer.";
  if (typeof timeoutS === "number" && timeoutS > 0) {
    const mins = Math.round(timeoutS / 60);
    const span = mins >= 1 ? `${mins} min` : `${timeoutS} s`;
    return `Waiting for you. If nobody answers within ${span}, it is not run.`;
  }
  return "Waiting for you.";
}

export function ApprovalCard({
  approval,
  onConversation,
  docked = false,
  scope,
  planText,
}: {
  approval: ApprovalAsk;
  /** Called on "Allow for this chat" so the page adds the tool to the
   *  composer's armed set — the persistence half of that button's promise. */
  onConversation?: (tool: string) => void;
  /** Calm chat W1-6 (v1.326.0): drawn in the composer's place. The dock's
   *  own card (components/chat/DockAsk) is the box, so the card drops its
   *  own border and tint and keeps only its padding. */
  docked?: boolean;
  /** v1.352.0 (G24): where the wider answer applies. Unset = a chat's card
   *  when it is docked or has a composer to arm (`onConversation`), else an
   *  agent run's ask (the session page, a project's paused task). */
  scope?: ApprovalScope;
  /** v1.369.0: a Build session's plan door (`exit_plan_mode`) whose call
   *  carries no plan: the reply the user read above the card IS the plan,
   *  shown here so the card says what Allow approves. */
  planText?: string;
}) {
  const where: ApprovalScope = scope ?? (docked || onConversation ? "chat" : "run");
  // Which button is in flight; the card disables itself after one click. The
  // resolved frame (or stream end) unmounts it — a second decision has no
  // write path.
  const [sent, setSent] = useState<"" | "once" | "conversation" | "deny" | "tab" | "always">("");
  const [error, setError] = useState<string | null>(null);
  // v1.266.0: a browser action can be allowed FOR ITS TAB — this call, and every
  // later page action in the same tab until it closes. Offered only where it
  // means something: a shell command has no tab. v1.315.0: keyed on the 14
  // BUILT-IN browser tools by exact name (lib/toolWords) — an agent-made custom
  // tool called `browser_backup` runs its author's command, has no tab, and the
  // daemon never grants it one, so offering "Allow for this tab" was a lie.
  // v1.332.0: a local-only card answers for this one call and no wider.
  // v1.350.0: the plan approval (`approve_plan`, a name the daemon keeps for
  // itself) is approved or declined, nothing wider, on EVERY surface: the
  // daemon always sends `once_only` for it, but a listing or a session row
  // carries no flags, so the card knows it by its name too.
  const planAsk = approval.tool === PLAN_TOOL;
  // v1.369.0: a Build session's plan door is once-only BY NAME too (a
  // listing row carries no flags), and is not a local-only card.
  const buildPlanAsk = approval.tool === EXIT_PLAN_TOOL;
  const onceOnly = approval.onceOnly === true || planAsk || buildPlanAsk;
  const browserAction = isBuiltinBrowserTool(approval.tool) && !onceOnly;
  // v1.299.0: offered by the daemon, never inferred here — a batch has no
  // single argument set to key a grant on, and the daemon says so by omission.
  const canAlways = approval.canAlways === true && !onceOnly;

  async function decide(decision: "once" | "conversation" | "deny" | "tab" | "always") {
    if (sent) return;
    setSent(decision);
    setError(null);
    try {
      await post(`/chat/approvals/${encodeURIComponent(approval.id)}`, {
        decision,
      });
      if (decision === "conversation") onConversation?.(approval.tool);
    } catch (err) {
      // A 404 means the wait already ended (timeout, or the turn moved on) —
      // the card is about to unmount; anything else re-enables the buttons so
      // the user is never stranded mid-pause with a dead card.
      setSent("");
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const count = approval.count && approval.count > 1 ? approval.count : 1;
  const batch = count > 1;
  const args = approval.args ?? {};
  const examples = (approval.examples ?? []).filter(
    (e): e is Record<string, unknown> => !!e && typeof e === "object",
  );
  // v1.314.0: lead with what the action DOES in plain words; the exact tool
  // id stays beside it as a <code> chip (the record of what will run). An id
  // the map does not know keeps today's "run <id>" — no invented words.
  const words = toolWords(approval.tool);
  const toolChip = (
    <code className="rounded bg-ink-900/80 px-1 font-mono text-[12px]" title="The exact tool that will run">
      {approval.tool}
    </code>
  );
  const undo = undoLine(approval.reversible);
  const noun = scopeNoun(where);
  // The plan a Build session asks to start: the call's own `plan`, else the
  // reply above the card (the caller hands it in), else nothing to show.
  const buildPlan = buildPlanAsk
    ? (typeof args.plan === "string" && args.plan.trim() ? args.plan : (planText ?? "")).trim()
    : "";
  return (
    <div
      role="alertdialog"
      aria-label={batch ? `Approve ${approval.tool} × ${count}?` : `Approve ${approval.tool}?`}
      className={
        docked
          ? "space-y-2.5 px-4 pb-4 pt-3"
          : "space-y-2.5 rounded-xl border border-tone-warn/30 bg-tone-warn/[0.06] p-3"
      }
      data-testid="chat-approval-card"
      data-count={count}
    >
      <div className="flex items-center gap-2">
        <ShieldQuestion size={15} className="shrink-0 text-tone-warn" aria-hidden="true" />
        <p className="text-[13px] font-medium text-zinc-100">
          {words ? (
            <>
              The assistant wants to {words} <span className="text-zinc-400">({toolChip})</span>
            </>
          ) : (
            <>The assistant wants to run {toolChip}</>
          )}
          {batch ? (
            <>
              {" "}
              <span data-testid="approval-count">× {count}</span>. One answer
              covers all of them.
            </>
          ) : (
            <>. Your call.</>
          )}
        </p>
      </div>

      {batch
        ? examples.length > 0 && (
            <div className="space-y-1">
              {examples.map((e, i) => (
                <ExampleLine key={i} args={e} />
              ))}
              {count > examples.length && (
                <p className="text-[11px] text-zinc-500">
                  …and {count - examples.length} more like these
                </p>
              )}
            </div>
          )
        : buildPlanAsk
          ? buildPlan
            ? (
                <div
                  data-testid="approval-plan-text"
                  className="max-h-48 overflow-y-auto rounded-lg bg-white/[0.03] px-3 py-2 text-[13px] leading-relaxed text-zinc-200"
                >
                  <Markdown content={buildPlan} />
                </div>
              )
            : (
                <p data-testid="approval-plan-above" className="text-[12px] text-zinc-400">
                  The plan is the reply above.
                </p>
              )
          : Object.keys(args).length > 0 && <ApprovalPreview tool={approval.tool} args={args} />}

      {approval.costNote && (
        // v1.381.0 review: a long word (a quoted shell line, a path) wraps
        // instead of widening the card past a phone's 390 px.
        <p data-testid="approval-cost" className="text-[11px] leading-relaxed text-zinc-400 [overflow-wrap:anywhere]">
          {approval.costNote}
        </p>
      )}

      {/* v1.352.0 (G25): can it be undone, in the cost line's quiet style.
          Never on a plan ask (a plan is not an action). */}
      {!planAsk && !buildPlanAsk && undo && (
        <p
          data-testid="approval-undo"
          data-reversible={approval.reversible ? "true" : "false"}
          className="text-[11px] leading-relaxed text-zinc-400"
        >
          {undo}
        </p>
      )}

      <p data-testid="approval-waiting" className="text-[11px] font-medium text-tone-warn">
        {waitingLine(approval.timeoutS)}
      </p>
      <p className="text-[11px] leading-relaxed text-zinc-400">
        {buildPlanAsk ? (
          <>
            Nothing changes until you answer. “Allow once” approves this plan
            and the session starts working on it; Deny keeps it planning.
          </>
        ) : planAsk ? (
          <>
            No step runs until you answer. “Allow once” lets it follow this
            plan; Deny ends the run before any step runs.
          </>
        ) : onceOnly ? (
          <>
            The turn is paused until you answer. “Allow once” runs only this
            call; Deny refuses it and the assistant is told you declined.
          </>
        ) : batch ? (
          <>
            “Allow these {count}” runs exactly these calls; “{noun}” stops
            asking for {approval.tool} here; Deny refuses all {count} and the
            assistant is told you declined.
          </>
        ) : (
          <>
            The {where === "run" ? "run" : "turn"} is paused until you answer.
            “Once” runs only this call; “{noun}” stops asking for{" "}
            {approval.tool} here; Deny refuses it and the assistant is told you
            declined.
          </>
        )}
      </p>

      {/* Only a local-only chat's card says so: a plan ask is once-only for
          its own reason (the line above). v1.375.0: so is a commit or push
          on a Build session's own branch, which says ITS reason. */}
      {onceOnly && !planAsk && !buildPlanAsk && approval.onceReason === "git" ? (
        <p data-testid="approval-git-once" className="text-[11px] leading-relaxed text-zinc-400">
          On this session&apos;s own branch, a commit, a push, a change on GitHub, a git setting or
          git that throws work away asks you every time.
        </p>
      ) : onceOnly && !planAsk && !buildPlanAsk && approval.onceReason === "page" ? (
        <p data-testid="approval-page-once" className="text-[11px] leading-relaxed text-zinc-400">
          In the agent&apos;s browser, a click on something that reads as deleting, sending or
          paying asks you every time.
        </p>
      ) : onceOnly && !planAsk && !buildPlanAsk && approval.onceReason === "command" ? (
        // v1.381.0: a shell step a project's or a plugin's slash command
        // asked for (src/iron_jarvis/harness/command_shell.py). Your own
        // commands follow the session's approval setting.
        <p data-testid="approval-command-once" className="text-[11px] leading-relaxed text-zinc-400">
          Shell steps from a project&apos;s or a plugin&apos;s slash command ask you every time,
          whatever this session&apos;s approval setting. Your own commands follow the setting.
        </p>
      ) : onceOnly && !planAsk && !buildPlanAsk ? (
        <p data-testid="approval-local-only" className="text-[11px] leading-relaxed text-zinc-400">
          This chat is local only, so this asks every time.
        </p>
      ) : null}

      {browserAction && (
        <p data-testid="approval-tab-note" className="text-[11px] leading-relaxed text-zinc-400">
          “Allow for this tab” covers every page action in that browser tab until you close
          it. Payments, passwords and deletions still ask.
        </p>
      )}

      {canAlways && (
        <p data-testid="approval-always-note" className="text-[11px] leading-relaxed text-zinc-400">
          “Always allow exactly this” runs it now and keeps allowing {approval.tool} with
          exactly these arguments here for 30 days. Different arguments still ask. Revoke
          it any time on the Autonomy page.
        </p>
      )}

      {error && <p className="text-[11px] text-tone-danger">{error}</p>}

      <div className="flex flex-wrap items-center gap-2">
        {browserAction && (
          <button
            type="button"
            data-testid="approval-tab"
            onClick={() => void decide("tab")}
            disabled={!!sent}
            className="btn-accent text-xs"
          >
            {sent === "tab" ? <LoaderInline label="Allowing…" /> : ALLOW_TAB}
          </button>
        )}
        <button
          type="button"
          // The dock's Enter presses THIS button (lib/dockAsk): one path.
          data-dock-primary=""
          onClick={() => void decide("once")}
          disabled={!!sent}
          className="btn-accent text-xs"
        >
          {sent === "once" ? (
            <LoaderInline label="Running…" />
          ) : batch ? (
            `Allow these ${count}`
          ) : (
            ALLOW_ONCE
          )}
        </button>
        {!onceOnly && (
          <button
            type="button"
            data-testid="approval-scope"
            data-scope={where}
            onClick={() => void decide("conversation")}
            disabled={!!sent}
            className="btn-ghost text-xs"
          >
            {sent === "conversation" ? <LoaderInline label="Allowing…" /> : scopeLabel(where)}
          </button>
        )}
        {canAlways && (
          <button
            type="button"
            data-testid="approval-always"
            onClick={() => void decide("always")}
            disabled={!!sent}
            title={`Allow ${approval.tool} with exactly these arguments here for 30 days`}
            className="btn-ghost text-xs"
          >
            {sent === "always" ? <LoaderInline label="Allowing…" /> : ALWAYS_EXACT}
          </button>
        )}
        <button
          type="button"
          data-dock-decline=""
          onClick={() => void decide("deny")}
          disabled={!!sent}
          className="btn-ghost text-xs text-tone-danger"
        >
          {sent === "deny" ? <LoaderInline label="Declining…" /> : batch ? `Deny all ${count}` : DENY}
        </button>
      </div>
    </div>
  );
}
