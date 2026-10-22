"use client";

/**
 * AskUserCard (v1.369.0, IJ harness Phase 6) — a question the model itself
 * asks the user (`ask_user`: Claude Code's AskUserQuestion, Codex's
 * request_user_input), drawn in the composer's place by DockAsk.
 *
 * One to four short questions. A question with choices draws them as rows
 * lettered A, B, C… (the dock's letter keys press them, lib/letteredChoices);
 * a question that allows several picks draws checkboxes; every question also
 * takes a typed answer of the user's own ("Something else"). Send answers,
 * Skip tells the model the user skipped it. The dock's Enter / Esc press
 * these buttons, so a key runs exactly what a click runs.
 *
 * The answer shape is the daemon's (`POST /build/sessions/{id}/answer`): one
 * question → its text (or a list for several picks); several → keyed by each
 * question's id.
 */

import { useId, useState, type FormEvent, type KeyboardEvent } from "react";
import { MessageCircleQuestion } from "lucide-react";
import { Button, LoaderInline } from "@/components/ui";
import { choiceLetter } from "@/lib/letteredChoices";
import type { AskUserAsk, AskUserQuestion } from "@/lib/buildSessions";

export type AskAnswer = string | string[] | Record<string, string | string[]>;

/** One question's answer from its picks and its typed words ("" = none). */
export function questionAnswer(q: AskUserQuestion, picked: readonly number[], typed: string): string | string[] | "" {
  const labels = picked.map((i) => q.options[i]?.label).filter((l): l is string => !!l);
  const own = typed.trim();
  if (q.multi) {
    const all = own ? [...labels, own] : labels;
    return all.length ? all : "";
  }
  if (own) return own;
  return labels[0] ?? "";
}

/** The whole card's answer, or null when no question has one yet. */
export function cardAnswer(
  questions: readonly AskUserQuestion[],
  picks: Record<string, number[]>,
  typed: Record<string, string>,
): AskAnswer | null {
  const each = questions.map((q) => questionAnswer(q, picks[q.id] ?? [], typed[q.id] ?? ""));
  if (each.every((a) => a === "" || (Array.isArray(a) && a.length === 0))) return null;
  if (questions.length === 1) return each[0] as string | string[];
  const out: Record<string, string | string[]> = {};
  questions.forEach((q, i) => {
    const a = each[i];
    if (a !== "" && !(Array.isArray(a) && a.length === 0)) out[q.id] = a as string | string[];
  });
  return out;
}

const ROW =
  "flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-[13px] leading-snug outline-none transition-colors focus-visible:ring-1 focus-visible:ring-accent/60 motion-reduce:transition-none";

function OptionRows({
  q,
  picked,
  disabled,
  onToggle,
}: {
  q: AskUserQuestion;
  picked: readonly number[];
  disabled: boolean;
  onToggle: (index: number) => void;
}) {
  const role = q.multi ? "checkbox" : "radio";
  function onKey(e: KeyboardEvent<HTMLDivElement>, i: number) {
    if (disabled || e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === " ") {
      e.preventDefault();
      onToggle(i);
    }
  }
  return (
    <div
      role={q.multi ? "group" : "radiogroup"}
      aria-label={q.question}
      data-choice-group=""
      className="space-y-0.5"
    >
      {q.options.map((opt, i) => {
        const letter = choiceLetter(i);
        const checked = picked.includes(i);
        return (
          <div
            key={i}
            role={role}
            aria-checked={checked}
            aria-disabled={disabled || undefined}
            aria-keyshortcuts={letter}
            tabIndex={disabled ? -1 : 0}
            data-choice-letter={letter}
            data-testid={`ask-user-choice-${q.id}-${letter}`}
            onClick={() => {
              if (!disabled) onToggle(i);
            }}
            onKeyDown={(e) => onKey(e, i)}
            className={`${ROW} ${
              disabled
                ? "cursor-default opacity-60"
                : checked
                  ? "cursor-pointer bg-accent/[0.08] text-zinc-100"
                  : "cursor-pointer text-zinc-300 hover:bg-white/[0.04] hover:text-zinc-100"
            }`}
          >
            <span
              aria-hidden="true"
              className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md font-mono text-[11px] ${
                checked ? "bg-accent/20 text-accent-soft" : "bg-white/[0.06] text-zinc-400"
              }`}
            >
              {letter}
            </span>
            <span className="min-w-0 break-words">
              {opt.label}
              {opt.description ? <span className="block text-[12px] text-zinc-500">{opt.description}</span> : null}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function AskUserCard({
  ask,
  onAnswer,
  docked = false,
}: {
  ask: AskUserAsk;
  /** Send an answer (null = Skip). Resolves to an error sentence, or null. */
  onAnswer: (answer: AskAnswer | null) => Promise<string | null>;
  docked?: boolean;
}) {
  const baseId = useId();
  const [picks, setPicks] = useState<Record<string, number[]>>({});
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<"" | "send" | "skip">("");
  const [error, setError] = useState<string | null>(null);
  const answer = cardAnswer(ask.questions, picks, typed);

  function toggle(q: AskUserQuestion, i: number) {
    setPicks((prev) => {
      const cur = prev[q.id] ?? [];
      const next = q.multi ? (cur.includes(i) ? cur.filter((x) => x !== i) : [...cur, i]) : cur[0] === i ? [] : [i];
      return { ...prev, [q.id]: next };
    });
  }

  async function send(skip: boolean) {
    if (busy) return;
    if (!skip && answer === null) {
      setError("Pick an answer or type one first.");
      return;
    }
    setBusy(skip ? "skip" : "send");
    setError(null);
    const problem = await onAnswer(skip ? null : answer);
    if (problem) {
      setError(problem);
      setBusy("");
    }
    // Answered: the card goes when the turn says the question is settled.
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void send(false);
  }

  return (
    <form
      role="dialog"
      aria-label="A question for you"
      data-testid="ask-user-card"
      onSubmit={onSubmit}
      className={docked ? "space-y-3 px-4 pb-4 pt-3" : "space-y-3 rounded-xl border border-accent/30 p-3"}
    >
      <div className="flex items-center gap-2">
        <MessageCircleQuestion size={15} className="shrink-0 text-accent-soft" aria-hidden="true" />
        <p className="text-[13px] font-medium text-zinc-100">
          {ask.questions.length > 1 ? "A few questions before it goes on" : "A question before it goes on"}
        </p>
      </div>
      {ask.questions.map((q) => {
        const inputId = `${baseId}-${q.id}`;
        return (
          <div key={q.id} data-testid="ask-user-question" className="space-y-1.5">
            {q.header ? <p className="text-[11px] text-zinc-500">{q.header}</p> : null}
            <p className="text-[13px] leading-relaxed text-zinc-200">{q.question}</p>
            {q.options.length ? (
              <OptionRows q={q} picked={picks[q.id] ?? []} disabled={!!busy} onToggle={(i) => toggle(q, i)} />
            ) : null}
            <label htmlFor={inputId} className="sr-only">
              {q.options.length ? "Or type your own answer" : "Your answer"}
            </label>
            <input
              id={inputId}
              data-testid={`ask-user-text-${q.id}`}
              value={typed[q.id] ?? ""}
              disabled={!!busy}
              onChange={(e) => setTyped((prev) => ({ ...prev, [q.id]: e.target.value }))}
              placeholder={q.options.length ? "Something else…" : "Your answer…"}
              className="w-full rounded-lg border hairline bg-transparent px-2.5 py-1.5 text-[13px] text-zinc-200 outline-none placeholder:text-zinc-600 focus:border-accent/50 disabled:opacity-50"
            />
          </div>
        );
      })}
      {error ? (
        <p role="alert" className="text-[12px] text-tone-danger">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" variant="primary" size="sm" disabled={!!busy} data-dock-primary="">
          {busy === "send" ? <LoaderInline label="Sending…" /> : "Send"}
        </Button>
        <button
          type="button"
          data-testid="ask-user-skip"
          onClick={() => void send(true)}
          disabled={!!busy}
          data-dock-decline=""
          className="rounded-md px-2 py-1 text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-40 motion-reduce:transition-none"
        >
          {busy === "skip" ? <LoaderInline label="Skipping…" /> : "Skip"}
        </button>
      </div>
    </form>
  );
}
