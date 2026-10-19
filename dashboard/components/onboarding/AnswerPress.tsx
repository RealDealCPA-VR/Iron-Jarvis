"use client";

/**
 * The ONE explicit "Use <it> for answers" press (W2-1), as a hook + its
 * result line (v1.310.0, wave 2). Shared by the connect doors and the
 * wizard's finale so both say the same three things the same way: the new
 * default, the daemon's own "you already chose X" (nothing changed), or its
 * 409 sentence. It never fires on its own — every call is a user's click —
 * because cloud vs local is the user's privacy decision (the v1.162.0 rule).
 */

import { useState } from "react";
import { chooseForAnswers, type AnswerCandidate } from "@/lib/onboarding";

export type PressNote = { kind: "ok" | "same" | "error"; text: string } | null;

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** v1.353.0 (G35): the confirmation in the words the user PRESSED. It used to
 *  name the promoted API row ("answers now come from Anthropic (anthropic ·
 *  claude-opus-4-8)") for a press labelled "Claude (your Claude Code
 *  sign-in)" — a different-sounding thing, with two raw ids. Same sentence
 *  the checklist's step 1 says once a model answers ("<label> answers you
 *  now"). */
export function chosenNote(label: string): string {
  return `Done — ${label} answers you now.`;
}

export function useAnswerPress(after: () => void) {
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<PressNote>(null);

  /** `onPromoted(provider, label)` runs only when the daemon really made the
   *  pressed one the default — the label is the press's own words, so a
   *  caller can keep saying it after the doors are gone. */
  async function press(
    c: AnswerCandidate,
    onPromoted?: (provider: string, label: string) => void,
  ) {
    setBusy(c.provider);
    setNote(null);
    try {
      const answer = await chooseForAnswers(c.provider, after);
      if (answer.promoted) {
        setNote({ kind: "ok", text: chosenNote(c.label) });
        onPromoted?.(c.provider, c.label);
      } else {
        setNote({ kind: "same", text: answer.reason || "Nothing changed." });
      }
    } catch (e) {
      setNote({ kind: "error", text: errText(e) });
    } finally {
      setBusy(null);
    }
  }

  return { busy, note, press };
}

/** What the press answered: a status line, or an alert for a refusal. */
export function AnswerPressNote({ note }: { note: PressNote }) {
  if (!note) return null;
  if (note.kind === "error") {
    return (
      <p role="alert" className="mt-2 text-xs text-rose-300">
        {note.text}
      </p>
    );
  }
  return (
    <p
      role="status"
      className={`mt-2 text-xs ${note.kind === "ok" ? "text-emerald-300" : "text-zinc-300"}`}
    >
      {note.text}
    </p>
  );
}
