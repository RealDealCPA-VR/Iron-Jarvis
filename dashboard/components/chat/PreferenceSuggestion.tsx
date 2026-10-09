"use client";

/**
 * v1.305.0 — ONE quiet question under a reply, when a correction repeats.
 *
 * The daemon noticed the user asking for the same thing in two different
 * turns ("shorter please" … "too long again") and proposes keeping it as a
 * standing preference. This line asks — in the TurnReceipt's family: the same
 * 11px type, the same accent, the same 10px icon weight — and never more
 * loudly than that. No modal, no toast, no focus taken: the composer stays
 * the user's, and ignoring the line costs nothing (the suggestion simply
 * waits on the Memory page).
 *
 *   Keep      → POST /memory/preferences/{id}/keep  → "Remembered: …", the
 *               v1.282.0 receipt wording, in the accent
 *   Edit      → the sentence becomes an inline field, prefilled; Enter keeps
 *               the edited words, Escape puts the line back
 *   Not this  → POST /memory/preferences/{id}/decline → final; never asked again
 *
 * The answer is handed to `onSettle` so the page stores it ON THE MESSAGE
 * (like `remembered`) and a reload renders the outcome, not the question.
 * The line also keeps the answer itself, so it never flips back to asking if
 * the message prop arrives stale.
 */
import { useId, useRef, useState } from "react";
import { Lightbulb, Loader2 } from "lucide-react";
import { get, post } from "@/lib/api";
import {
  keptTextFrom,
  quotable,
  quoteMeta,
  settledElsewhere,
  timesWord,
  type ChatSuggestion,
} from "@/lib/preferences";

/** The HTTP status a failed call carried (ApiError.status), else 0. Read by
 *  shape, not `instanceof`: tests mock lib/api.ts wholesale. */
function statusOf(e: unknown): number {
  const st = e && typeof e === "object" ? (e as { status?: unknown }).status : undefined;
  return typeof st === "number" ? st : 0;
}

export interface PreferenceSuggestionProps {
  suggestion: ChatSuggestion;
  /** The user answered: the page stores this on the message and saves. */
  onSettle?: (next: ChatSuggestion) => void;
}

export function PreferenceSuggestion({ suggestion, onSettle }: PreferenceSuggestionProps) {
  const [local, setLocal] = useState<ChatSuggestion | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<"keep" | "decline" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const inputRef = useRef<HTMLInputElement>(null);

  const s = local ?? suggestion;
  const domId = `pref-suggestion-${s.id}`;

  async function settle(kind: "keep" | "decline", text?: string) {
    if (pending) return;
    setPending(kind);
    setError(null);
    try {
      const path = `/memory/preferences/${encodeURIComponent(s.id)}/${kind}`;
      const edited = text !== undefined && text.trim() !== s.text ? text.trim() : undefined;
      const res = await post<unknown>(path, kind === "keep" && edited ? { text: edited } : {});
      const next: ChatSuggestion =
        kind === "keep"
          ? { ...s, state: "kept", text: keptTextFrom(res, edited ?? s.text) }
          : { ...s, state: "declined" };
      setLocal(next);
      setEditing(false);
      onSettle?.(next);
    } catch (e) {
      // 409 (no longer a suggestion) / 404 (asked again or forgotten): the
      // question was answered ELSEWHERE — the Memory page, another window.
      // Re-read the list and settle to the real outcome, stored on the
      // message like any answer, instead of an error under a stale ask.
      const st = statusOf(e);
      if (st === 409 || st === 404) {
        try {
          const real = settledElsewhere(s, await get<unknown>("/memory/preferences"));
          if (real) {
            setLocal(real);
            setEditing(false);
            onSettle?.(real);
            return;
          }
        } catch {
          // The re-read failed too: fall through to the original sentence.
        }
      }
      // A genuine refusal (a flagged edit, the network) is said where the
      // user pressed — the line stays.
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(null);
    }
  }

  // Asked again or forgotten elsewhere: the question is simply over.
  if (s.state === "gone") return null;
  if (s.state === "kept") {
    return (
      <div id={domId} data-testid="pref-suggestion" data-state="kept" className="mt-1 text-[11px]">
        <span data-testid="pref-suggestion-kept" className="text-accent-soft">
          Remembered: {s.text}
        </span>
      </div>
    );
  }
  if (s.state === "declined") {
    return (
      <div id={domId} data-testid="pref-suggestion" data-state="declined" className="mt-1 text-[11px] text-zinc-600">
        Won&apos;t suggest &ldquo;{quotable(s.text)}&rdquo; again.
      </div>
    );
  }

  const quotesTitle = s.quotes
    .map((q) => {
      const meta = quoteMeta(q);
      return `“${q.quote}”${meta ? ` — ${meta}` : ""}`;
    })
    .join("\n");
  const busy = pending !== null;
  const action =
    "rounded px-1 py-px text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-accent-soft disabled:opacity-40";

  return (
    <div id={domId} data-testid="pref-suggestion" data-state="open" className="mt-1 text-[11px] text-zinc-500">
      <div className="inline-flex max-w-full flex-wrap items-center gap-x-1.5 gap-y-1">
        <Lightbulb size={10} className="shrink-0 text-accent-soft/70" aria-hidden="true" />
        {editing ? (
          <input
            ref={inputRef}
            data-testid="pref-suggestion-input"
            aria-label="Your standing preference, in your words"
            value={draft}
            maxLength={280}
            disabled={busy}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // The field is its own island: Enter keeps HERE and never
              // reaches the composer's send; Escape puts the line back and
              // never reaches a running turn's Stop.
              if (e.key === "Enter") {
                e.preventDefault();
                e.stopPropagation();
                if (draft.trim()) void settle("keep", draft);
              } else if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                setEditing(false);
                setError(null);
              }
            }}
            className="min-w-[16rem] max-w-full flex-1 rounded border border-white/[0.08] bg-white/[0.03] px-1.5 py-0.5 text-[11.5px] text-zinc-200 outline-none focus:border-accent/40"
          />
        ) : (
          <span className="min-w-0">
            You&apos;ve said this{" "}
            {s.quotes.length > 0 ? (
              <button
                type="button"
                aria-expanded={open}
                aria-controls={open ? panelId : undefined}
                title={quotesTitle}
                onClick={() => setOpen((v) => !v)}
                data-testid="pref-suggestion-why"
                className="underline decoration-zinc-700 decoration-dotted underline-offset-2 transition-colors hover:text-zinc-300"
              >
                {timesWord(s.count)}
              </button>
            ) : (
              timesWord(s.count)
            )}
            : <span className="text-zinc-300">&ldquo;{quotable(s.text)}&rdquo;</span> — keep it as a
            standing preference?
          </span>
        )}
        <span className="inline-flex items-center gap-0.5">
          {editing ? (
            <>
              <button
                type="button"
                data-testid="pref-suggestion-keep"
                disabled={busy || !draft.trim()}
                onClick={() => void settle("keep", draft)}
                className={action}
              >
                {pending === "keep" && <Loader2 size={10} className="mr-0.5 inline animate-spin" />}
                Keep
              </button>
              <span aria-hidden="true">·</span>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setEditing(false);
                  setError(null);
                }}
                className={action}
              >
                Cancel
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                data-testid="pref-suggestion-keep"
                disabled={busy}
                onClick={() => void settle("keep")}
                className={action}
              >
                {pending === "keep" && <Loader2 size={10} className="mr-0.5 inline animate-spin" />}
                Keep
              </button>
              <span aria-hidden="true">·</span>
              <button
                type="button"
                data-testid="pref-suggestion-edit"
                disabled={busy}
                onClick={() => {
                  setDraft(s.text);
                  setEditing(true);
                  setError(null);
                  // Focus only because the user asked to type here.
                  window.setTimeout(() => inputRef.current?.focus(), 0);
                }}
                className={action}
              >
                Edit
              </button>
              <span aria-hidden="true">·</span>
              <button
                type="button"
                data-testid="pref-suggestion-decline"
                disabled={busy}
                onClick={() => void settle("decline")}
                title="Never suggest this again"
                className={action}
              >
                {pending === "decline" && <Loader2 size={10} className="mr-0.5 inline animate-spin" />}
                Not this
              </button>
            </>
          )}
        </span>
      </div>
      {error && (
        <p data-testid="pref-suggestion-error" className="mt-1 text-[10.5px] text-rose-300/90">
          {error}
        </p>
      )}
      {open && s.quotes.length > 0 && (
        <div
          id={panelId}
          data-testid="pref-suggestion-quotes"
          className="mt-1.5 max-w-[560px] space-y-1 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2"
        >
          {s.quotes.map((q, i) => {
            const meta = quoteMeta(q);
            return (
              <p key={i} className="text-[11.5px] leading-relaxed">
                <span className="text-zinc-300">&ldquo;{q.quote}&rdquo;</span>
                {meta && <span className="text-zinc-500"> — {meta}</span>}
              </p>
            );
          })}
        </div>
      )}
    </div>
  );
}
