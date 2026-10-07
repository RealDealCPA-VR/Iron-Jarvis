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
import { chooseForAnswers, friendlyProvider, type AnswerCandidate } from "@/lib/onboarding";

export type PressNote = { kind: "ok" | "same" | "error"; text: string } | null;

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function useAnswerPress(after: () => void) {
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<PressNote>(null);

  async function press(c: AnswerCandidate, onPromoted?: (provider: string) => void) {
    setBusy(c.provider);
    setNote(null);
    try {
      const answer = await chooseForAnswers(c.provider, after);
      if (answer.promoted) {
        const { provider, model } = answer.promoted;
        setNote({
          kind: "ok",
          text: `Done — answers now come from ${friendlyProvider(provider)} (${provider}${
            model ? ` · ${model}` : ""
          }).`,
        });
        onPromoted?.(c.provider);
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
