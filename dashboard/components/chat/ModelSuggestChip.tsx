"use client";

/**
 * THE ONE-TAP MODEL SUGGESTION (calm UI redesign S9, AUDIT §4.6 / Q7).
 *
 * First run has no setup screen. While replies are the scripted demo and this
 * PC already has a real model (a signed-in Claude Code or Codex, a local
 * server, a pasted key), the composer offers it in one line:
 *
 *     Use Claude Code for answers · one tap
 *
 * It is a SUGGESTION, never a silent pick (Q7, the never-auto-switch rule):
 * nothing changes until the press, which goes through the same one explicit
 * door the wizard and the empty state use (POST /onboarding/use-model). When
 * there is more than one candidate, the others are one press away in the
 * model menu ("Other…"), because cloud versus local is the user's call.
 */

import { useState } from "react";
import { Sparkles } from "lucide-react";
import { useDaemon } from "@/lib/daemon";
import { answerCandidates, chooseForAnswers, isDemoDefault } from "@/lib/onboarding";

export function ModelSuggestChip({ onOther }: { onOther?: () => void }) {
  const { health, refresh } = useDaemon();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  if (note) {
    return (
      <span role="status" data-testid="model-suggest-done" className="text-[12px] text-tone-success">
        {note}
      </span>
    );
  }
  if (!isDemoDefault(health)) return null;
  const candidates = answerCandidates(health);
  if (candidates.length === 0) return null;
  const top = candidates[0];

  async function press() {
    setBusy(true);
    setError("");
    try {
      const res = await chooseForAnswers(top.provider, refresh);
      setNote(res.promoted ? `Done — ${top.label} now answers.` : res.reason || "Nothing changed.");
    } catch (err) {
      setError((err as { message?: string })?.message || "Couldn't switch — try the model menu.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <span data-testid="model-suggest" className="inline-flex flex-wrap items-center gap-1.5 text-[12px]">
      <button
        type="button"
        onClick={() => void press()}
        disabled={busy}
        title={top.where ? `Your words ${top.where}.` : undefined}
        className="inline-flex items-center gap-1 rounded-full border border-accent/30 bg-accent/[0.06] px-2.5 py-0.5 text-accent-soft hover:bg-accent/[0.12] disabled:opacity-50"
      >
        <Sparkles size={11} aria-hidden /> Use {top.label} for answers
      </button>
      <span className="text-zinc-500">one tap</span>
      {candidates.length > 1 && onOther && (
        <button type="button" onClick={onOther} className="text-zinc-500 underline-offset-2 hover:text-zinc-300 hover:underline">
          Other…
        </button>
      )}
      {error && (
        <span role="alert" className="text-tone-danger">
          {error}
        </span>
      )}
    </span>
  );
}
