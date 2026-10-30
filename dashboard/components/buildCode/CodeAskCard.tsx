"use client";

/**
 * A coding tool's question for the user, in the composer's place (Build's
 * Code view, v1.409.0) — the chat dock's look and its key rules
 * (components/chat/DockAsk, lib/dockAsk): Enter presses the card's main
 * answer, Esc its "no", only from inside the card or with nothing else
 * focused, never in the first moment after the card appears, never from a
 * control that does something with the key itself.
 *
 * TWO KINDS:
 *  - an APPROVAL (Claude Code's "can I use this tool", Codex's "can I run
 *    this command / apply this change"): Allow once · Allow for this session
 *    (only when the tool offers a session-wide allow) · Deny. Enter NEVER
 *    allows one that asks once every time, nor any card while the session
 *    runs in Full access (lib/buildApp.enterAllowsCodeAsk): Allow is a click
 *    there, Esc still denies.
 *  - a QUESTION (the tool asking the user to choose): each question's
 *    choices lettered A, B, C… (a letter key picks, never sends), an optional
 *    "Something else" field, Send · Skip.
 *
 * The page owns the answer (`onAnswer`): it posts it and says why it failed.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { Button } from "@/components/ui";
import { DiffBody } from "@/components/chat/DiffView";
import { ALLOW_ONCE, DENY } from "@/lib/approvalWords";
import { DOCK_KEY_ARM_MS } from "@/components/chat/DockAsk";
import { dockKeyAction, TAKES_TYPING } from "@/lib/dockAsk";
import { choiceLetter } from "@/lib/letteredChoices";
import {
  approvalHeading,
  codeAskKeyHint,
  enterAllowsCodeAsk,
  questionAnswers,
  type ApprovalEvent,
  type OpenAsk,
  type QuestionEvent,
} from "@/lib/buildApp";

export type CodeAnswer =
  | { decision: "allow" | "allow_session" | "deny"; message?: string; answers?: Record<string, string> };

const EDGE_WARN =
  "shadow-[0_0_0_0.5px_rgb(var(--tone-warn)/0.55),0_6px_24px_rgb(0_0_0/0.35)] [[data-scheme=light]_&]:shadow-[0_0_0_0.5px_rgb(var(--tone-warn)/0.6),0_4px_18px_rgb(var(--white)/0.08)]";
const EDGE_ACCENT =
  "shadow-[0_0_0_0.5px_rgb(var(--accent-rgb)/0.5),0_6px_24px_rgb(0_0_0/0.35)] [[data-scheme=light]_&]:shadow-[0_0_0_0.5px_rgb(var(--accent-rgb)/0.55),0_4px_18px_rgb(var(--white)/0.08)]";

function bodyHasFocus(): boolean {
  const ae = document.activeElement;
  return !ae || ae === document.body || ae === document.documentElement;
}

function ApprovalBody({
  ask,
  toolName,
  busy,
  onAnswer,
}: {
  ask: ApprovalEvent;
  toolName: string;
  busy: boolean;
  onAnswer: (a: CodeAnswer) => void;
}) {
  return (
    <div data-testid="code-ask-approval" data-kind={ask.kind} className="space-y-2.5 px-4 pb-4 pt-3">
      <p className="text-[13px] font-medium text-zinc-100">{approvalHeading(ask, toolName)}</p>
      {ask.command ? (
        <pre
          data-testid="code-ask-command"
          className="max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-[12px] leading-5 text-zinc-300"
        >
          <span aria-hidden="true" className="select-none text-zinc-500">
            ${" "}
          </span>
          {ask.command}
        </pre>
      ) : null}
      {ask.path ? (
        <p className="truncate font-mono text-[12px] text-zinc-400" title={ask.path}>
          {ask.path}
        </p>
      ) : null}
      {ask.detail ? <p className="whitespace-pre-wrap break-words text-[13px] text-zinc-400">{ask.detail}</p> : null}
      {ask.diff ? (
        <div data-testid="code-ask-diff" className="max-h-64 overflow-auto rounded-md">
          <DiffBody
            change={{ path: ask.path || "file", status: "modified", added: null, removed: null, diff: ask.diff }}
          />
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          data-testid="code-ask-allow"
          disabled={busy}
          onClick={() => onAnswer({ decision: "allow" })}
          data-dock-primary=""
        >
          {ALLOW_ONCE}
        </Button>
        {ask.canSession ? (
          <Button
            variant="secondary"
            size="sm"
            data-testid="code-ask-allow-session"
            disabled={busy}
            onClick={() => onAnswer({ decision: "allow_session" })}
          >
            Allow for this session
          </Button>
        ) : null}
        <Button
          variant="secondary"
          size="sm"
          data-testid="code-ask-deny"
          disabled={busy}
          onClick={() => onAnswer({ decision: "deny" })}
          data-dock-decline=""
        >
          {DENY}
        </Button>
      </div>
    </div>
  );
}

function QuestionBody({
  ask,
  toolName,
  busy,
  onAnswer,
}: {
  ask: QuestionEvent;
  toolName: string;
  busy: boolean;
  onAnswer: (a: CodeAnswer) => void;
}) {
  const [picked, setPicked] = useState<Record<number, string[]>>({});
  const [other, setOther] = useState<Record<number, string>>({});
  const answers = useMemo(() => questionAnswers(ask, picked, other), [ask, picked, other]);
  const ready = Object.keys(answers).length > 0;
  const pick = (qi: number, label: string, multi: boolean) =>
    setPicked((prev) => {
      const cur = prev[qi] ?? [];
      const has = cur.includes(label);
      const next = multi ? (has ? cur.filter((x) => x !== label) : [...cur, label]) : has ? [] : [label];
      return { ...prev, [qi]: next };
    });
  return (
    <div data-testid="code-ask-question" className="space-y-3 px-4 pb-4 pt-3">
      <p className="text-[13px] font-medium text-zinc-100">{toolName} has a question for you</p>
      {ask.questions.map((q, qi) => (
        <fieldset key={qi} className="space-y-1.5" data-choice-group="">
          <legend className="text-[13px] text-zinc-200">
            {q.header ? <span className="mr-1.5 text-zinc-500">{q.header}:</span> : null}
            {q.question}
          </legend>
          {q.options.map((o, oi) => {
            const on = (picked[qi] ?? []).includes(o.label);
            return (
              <button
                key={o.label}
                type="button"
                role={q.multi ? "checkbox" : "radio"}
                aria-checked={on}
                data-testid={`code-ask-option-${qi}-${oi}`}
                data-choice-letter={choiceLetter(oi)}
                onClick={() => pick(qi, o.label, q.multi)}
                className={`flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 ${
                  on ? "bg-accent/[0.12] text-zinc-100" : "text-zinc-300 hover:bg-white/[0.05]"
                }`}
              >
                <span aria-hidden="true" className="mt-px w-4 shrink-0 font-mono text-[12px] text-zinc-500">
                  {choiceLetter(oi)}
                </span>
                <span className="min-w-0">
                  <span className="block">{o.label}</span>
                  {o.description ? <span className="block text-[12px] text-zinc-500">{o.description}</span> : null}
                </span>
              </button>
            );
          })}
          {q.freeText ? (
            <input
              type="text"
              aria-label={`Something else for: ${q.question}`}
              placeholder="Something else…"
              data-testid={`code-ask-other-${qi}`}
              value={other[qi] ?? ""}
              onChange={(e) => setOther((prev) => ({ ...prev, [qi]: e.target.value }))}
              className="field h-8 w-full text-[13px]"
            />
          ) : null}
        </fieldset>
      ))}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          data-testid="code-ask-send"
          disabled={busy || !ready}
          onClick={() => onAnswer({ decision: "allow", answers })}
          data-dock-primary=""
        >
          Send
        </Button>
        <Button
          variant="secondary"
          size="sm"
          data-testid="code-ask-skip"
          disabled={busy}
          onClick={() => onAnswer({ decision: "deny" })}
          data-dock-decline=""
        >
          Skip
        </Button>
      </div>
    </div>
  );
}

export function CodeAskCard({
  asks,
  toolName,
  dangerousMode,
  onAnswer,
  onStop,
  error,
  armDelayMs = DOCK_KEY_ARM_MS,
}: {
  /** The open asks in asking order; the first is shown. */
  asks: readonly OpenAsk[];
  toolName: string;
  /** The session runs in a dangerous mode (Full access). */
  dangerousMode: boolean;
  /** Post the answer; resolves when it landed (the card then goes). */
  onAnswer: (ask: OpenAsk, answer: CodeAnswer) => Promise<void>;
  onStop?: () => void;
  /** Why the last answer did not land, in words. */
  error?: string | null;
  armDelayMs?: number;
}) {
  const head = asks[0] ?? null;
  const headId = head?.requestId ?? null;
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const shownAtRef = useRef(0);
  const [busy, setBusy] = useState(false);
  const enterOkRef = useRef(true);
  enterOkRef.current = enterAllowsCodeAsk(head, dangerousMode);

  useEffect(() => {
    if (!headId) return;
    shownAtRef.current = Date.now();
    setBusy(false);
    const ae = document.activeElement;
    const composerHadIt = !!ae && !!ae.closest?.("[data-code-composer]");
    if (bodyHasFocus() || composerHadIt) wrapRef.current?.focus({ preventScroll: true });
  }, [headId]);

  const answer = useCallback(
    (a: CodeAnswer) => {
      if (!head || busy) return;
      setBusy(true);
      onAnswer(head, a).finally(() => setBusy(false));
    },
    [head, busy, onAnswer],
  );

  const press = useCallback(
    (which: "primary" | "decline"): boolean => {
      if (Date.now() - shownAtRef.current < armDelayMs) return false;
      if (which === "primary" && !enterOkRef.current) return false;
      const btn = wrapRef.current?.querySelector<HTMLButtonElement>(`[data-dock-${which}]`);
      if (!btn || btn.disabled) return false;
      btn.click();
      return true;
    },
    [armDelayMs],
  );

  // Nothing focused: the keys still answer (unless a dialog is open, or the
  // card is in a view that is not on screen).
  useEffect(() => {
    if (!headId) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || !bodyHasFocus()) return;
      if (document.querySelector('[aria-modal="true"]')) return;
      if (wrapRef.current?.closest("[inert]")) return;
      const action = dockKeyAction(e, null, false);
      if (action && press(action)) e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [headId, press]);

  function onKeyDownCapture(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    const target = e.target as Element;
    // A letter picks the matching lettered choice of its question (never
    // sends), from inside the card, never from a field that takes typing.
    if (
      e.key.length === 1 &&
      /[a-z]/i.test(e.key) &&
      !e.ctrlKey &&
      !e.altKey &&
      !e.metaKey &&
      !target.closest(TAKES_TYPING)
    ) {
      if (Date.now() - shownAtRef.current < armDelayMs) return;
      const group = target.closest("[data-choice-group]") ?? wrapRef.current?.querySelector("[data-choice-group]");
      const row = group?.querySelector<HTMLElement>(`[data-choice-letter="${e.key.toUpperCase()}"]`);
      if (row) {
        row.click();
        row.focus({ preventScroll: true });
        e.preventDefault();
        e.stopPropagation();
      }
      return;
    }
    const action = dockKeyAction(
      {
        key: e.key,
        shiftKey: e.shiftKey,
        ctrlKey: e.ctrlKey,
        altKey: e.altKey,
        metaKey: e.metaKey,
        repeat: e.repeat,
        isComposing: false,
      },
      target,
      true,
    );
    if (action && press(action)) {
      e.preventDefault();
      e.stopPropagation();
    }
  }

  if (!head) return null;
  const total = asks.length;
  return (
    <div
      ref={wrapRef}
      data-testid="code-ask"
      data-kind={head.type}
      role="region"
      aria-label={total > 1 ? `A question for you, 1 of ${total}` : "A question for you"}
      tabIndex={-1}
      onKeyDownCapture={onKeyDownCapture}
      className={`relative z-[1] rounded-[20px] bg-ink-800 outline-none ${head.type === "approval" ? EDGE_WARN : EDGE_ACCENT}`}
    >
      {(total > 1 || onStop) && (
        <div className="-mb-1 flex items-center gap-3 px-4 pt-2.5 text-[12px] text-zinc-500">
          {total > 1 && <span data-testid="code-ask-count">1 of {total}</span>}
          {onStop && (
            <button
              type="button"
              data-testid="code-ask-stop"
              onClick={onStop}
              title="Stop this reply"
              className="ml-auto rounded-md px-2 py-0.5 text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
            >
              Stop
            </button>
          )}
        </div>
      )}
      {head.type === "approval" ? (
        <ApprovalBody key={head.requestId} ask={head} toolName={toolName} busy={busy} onAnswer={answer} />
      ) : (
        <QuestionBody key={head.requestId} ask={head} toolName={toolName} busy={busy} onAnswer={answer} />
      )}
      {error ? (
        <p role="alert" data-testid="code-ask-error" className="px-4 pb-3 text-[12px] text-tone-danger">
          {error}
        </p>
      ) : null}
      <p data-testid="code-ask-keys" className="px-4 pb-3 text-[11px] text-zinc-600">
        {codeAskKeyHint(head, dangerousMode)}
      </p>
    </div>
  );
}

export default CodeAskCard;
