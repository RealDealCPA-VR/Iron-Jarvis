"use client";

/**
 * v1.397.0: TRY ONE TASK ON SEVERAL MODELS — a coding session's attempts
 * (lib/buildAttempts, the daemon's harness/attempts.py).
 *
 *   while they run   one row per attempt, like a helper's row: its model (and
 *                    account), "Working · 3 steps", its branch — and an
 *                    approval card of its own, answered right on the row.
 *   when they end    ONE card, side by side: model, why it stopped, the
 *                    finish check's tests, files changed, time, usage or cost
 *                    (list price for a plan, metered for pay per use, free for
 *                    a local model) — and Keep on one. Keep commits that
 *                    attempt's work and merges it into the session's branch;
 *                    the others' folders go to the trash, their branches kept.
 *
 * The card reads the reply line's `attempts` (the daemon writes it before the
 * turn's last frame), so a reload, a return and a restart all show the same
 * card. Every button is `type="button"` (a component a form may contain).
 */

import { useState, type ReactNode } from "react";
import { Check, GitBranch, Loader2 } from "lucide-react";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { WorkRow } from "@/components/chat/WorkLine";
import { ConfirmButton } from "@/components/ui";
import {
  afterKeepWords,
  attemptName,
  cardLead,
  decodeAttemptsCard,
  discardAttempts,
  endWords,
  filesWords,
  hasLiveFolders,
  keepAttempt,
  liveWords,
  testsWords,
  timeWords,
  usageWords,
  type AttemptView,
  type AttemptsCard,
} from "@/lib/buildAttempts";

const FINAL = new Set(["done", "failed", "stopped", "limit"]);

/** The turn's attempts while it runs (their order kept). */
export function LiveAttemptRows({ attempts }: { attempts: readonly AttemptView[] }) {
  if (!attempts.length) return null;
  return (
    <div data-testid="harness-attempts-live" className="grid gap-2">
      {attempts.map((a) => (
        <LiveAttemptRow key={a.id} attempt={a} />
      ))}
    </div>
  );
}

function LiveAttemptRow({ attempt: a }: { attempt: AttemptView }) {
  const running = !FINAL.has(a.state) && a.state !== "waiting";
  const failed = a.state === "failed" || a.state === "limit";
  return (
    <div data-testid="attempt-row" data-state={a.state} data-attempt={a.id} className="min-w-0">
      <WorkRow
        icon={FINAL.has(a.state) && !failed ? Check : GitBranch}
        running={running}
        failed={failed}
        title={`${attemptName(a)} — ${liveWords(a)}`}
        detail={a.branch ? `On ${a.branch}` : null}
      />
      {a.state === "waiting" && a.ask ? (
        <div data-testid="attempt-ask" className="mt-1 pl-[22px]">
          <ApprovalCard
            scope="run"
            approval={{
              id: a.ask.id,
              callId: "",
              tool: a.ask.tool,
              args: a.ask.args,
              timeoutS: 0,
              ...(a.ask.costNote ? { costNote: a.ask.costNote } : {}),
              ...(a.ask.onceOnly ? { onceOnly: true } : {}),
              ...(a.ask.onceReason ? { onceReason: a.ask.onceReason } : {}),
            }}
          />
        </div>
      ) : null}
    </div>
  );
}

/** A reply's saved attempts card (the line's `attempts`), with Keep. */
export function AttemptsCardView({
  raw,
  threadId,
  turnId,
  canKeep,
  onKept,
}: {
  raw: unknown;
  threadId: string;
  turnId: string;
  /** Nothing runs and the session is not archived: Keep may be pressed. */
  canKeep: boolean;
  /** After a Keep that went through (the session is read again). */
  onKept: () => void;
}) {
  const card = decodeAttemptsCard(raw);
  const [busy, setBusy] = useState<string | null>(null);
  const [said, setSaid] = useState<{ ok: boolean; words: string } | null>(null);
  if (!card) return null;
  async function keep(a: AttemptView) {
    if (busy) return;
    setBusy(a.id);
    setSaid(null);
    const out = await keepAttempt(threadId, turnId, a.id);
    setBusy(null);
    setSaid({ ok: out.ok, words: [out.words, out.note].filter(Boolean).join(" ") });
    if (out.ok) onKept();
  }
  // Review (v1.397.0): DISCARD ALL — the attempts' folders to the trash,
  // their branches kept (Keep's own trash), two presses like any discard.
  async function discardAll() {
    if (busy) return;
    setBusy("discard");
    setSaid(null);
    const out = await discardAttempts(threadId, turnId);
    setBusy(null);
    setSaid({ ok: out.ok, words: [out.words, out.note].filter(Boolean).join(" ") });
    if (out.ok) onKept();
  }
  const lead = cardLead(card);
  // (Not while a Keep is on its way: a press then would be swallowed.)
  const canDiscard = canKeep && hasLiveFolders(card) && (busy === null || busy === "discard");
  return (
    <section data-testid="attempts-card" className="mb-2 min-w-0" aria-label="Attempts on several models">
      <div className="grid min-w-0 gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {card.items.map((a) => (
          <AttemptColumn
            key={a.id}
            card={card}
            attempt={a}
            // A finished attempt, or one that changed files before it was
            // stopped (its work is there to keep, the card says how it ended).
            keepable={
              canKeep && !card.kept && !card.fromSession && a.keepable && !a.trashed &&
              (a.why === "finished" || a.files.length > 0)
            }
            working={busy === a.id}
            disabled={!!busy}
            onKeep={() => void keep(a)}
          />
        ))}
      </div>
      {lead || canDiscard ? (
        <div className="mt-2 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          {lead ? (
            <p data-testid="attempts-lead" className="min-w-0 text-[12px] leading-5 text-zinc-500">
              {lead}
            </p>
          ) : null}
          {canDiscard ? (
            <span data-testid="attempts-discard" className="shrink-0">
              <ConfirmButton
                variant="calm"
                label="Discard all"
                confirmLabel="Move their folders to the trash"
                title="Every attempt's folder goes to the trash; their branches stay"
                onConfirm={discardAll}
              />
            </span>
          ) : null}
        </div>
      ) : null}
      {card.keepNote ? (
        <p data-testid="attempts-keep-note" className="mt-1 text-[12px] leading-5 text-tone-warn">
          {card.keepNote}
        </p>
      ) : null}
      {said ? (
        <p
          data-testid="attempts-said"
          role={said.ok ? "status" : "alert"}
          className={`mt-1 text-[12px] leading-5 ${said.ok ? "text-zinc-400" : "text-tone-danger"}`}
        >
          {said.words}
        </p>
      ) : null}
    </section>
  );
}

function AttemptColumn({
  card,
  attempt: a,
  keepable,
  working,
  disabled,
  onKeep,
}: {
  card: AttemptsCard;
  attempt: AttemptView;
  keepable: boolean;
  working: boolean;
  disabled: boolean;
  onKeep: () => void;
}) {
  const kept = card.kept === a.id;
  const after = afterKeepWords(card, a);
  return (
    <article
      data-testid="attempt-column"
      data-attempt={a.id}
      data-why={a.why}
      data-kept={kept ? "true" : "false"}
      className={`min-w-0 rounded-xl px-3 py-2 ${kept ? "bg-accent/[0.08] ring-1 ring-accent/30" : "bg-white/[0.04]"}`}
    >
      <h3 className="break-words text-[13px] font-medium text-zinc-200" title={[a.provider, a.model].filter(Boolean).join(" · ")}>
        {attemptName(a)}
      </h3>
      <dl className="mt-1 grid gap-0.5 text-[12px] leading-5">
        <Fact label="Ended" testid="attempt-why" tone={a.why === "finished" ? "" : "warn"}>
          {endWords(a)}
        </Fact>
        <Fact label="Check" testid="attempt-tests" tone={a.tests === "failed" ? "warn" : ""}>
          {testsWords(a.tests)}
        </Fact>
        <Fact label="Files" testid="attempt-files" title={a.files.join("\n")}>
          {filesWords(a.files)}
        </Fact>
        <Fact label="Time" testid="attempt-time">
          {timeWords(a.ms)}
        </Fact>
        <Fact label="Use" testid="attempt-usage">
          {usageWords(a)}
        </Fact>
      </dl>
      {a.branch ? (
        <p className="mt-1 break-all text-[11px] text-zinc-500" title="This attempt's own branch">
          {a.branch}
        </p>
      ) : null}
      {after ? (
        <p data-testid="attempt-after" className="mt-1 text-[12px] text-zinc-400">
          {after}
        </p>
      ) : null}
      {keepable ? (
        <button
          type="button"
          data-testid="attempt-keep"
          disabled={disabled}
          onClick={onKeep}
          title={
            card.adopt
              ? "Keep this attempt: its work is committed and this session moves onto its branch"
              : "Keep this attempt: its work is committed and merged into this session's branch"
          }
          className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-accent/15 px-3 py-1 text-[12px] font-medium text-accent transition-colors hover:bg-accent/25 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:opacity-50"
        >
          {working ? <Loader2 size={12} className="animate-spin motion-reduce:animate-none" aria-hidden /> : null}
          Keep
        </button>
      ) : null}
    </article>
  );
}

function Fact({
  label,
  testid,
  tone = "",
  title,
  children,
}: {
  label: string;
  testid: string;
  tone?: "" | "warn";
  title?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex min-w-0 gap-2">
      <dt className="w-10 shrink-0 text-zinc-500">{label}</dt>
      <dd data-testid={testid} title={title} className={`min-w-0 break-words ${tone === "warn" ? "text-tone-warn" : "text-zinc-300"}`}>
        {children}
      </dd>
    </div>
  );
}
