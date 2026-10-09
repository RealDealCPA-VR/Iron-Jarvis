"use client";

// An installed app asks to use THIS turn's model for its own question
// (v1.324.0, MCP sampling). Nothing is sent to any model until the user
// presses Allow once, and then only to the turn's own provider and model —
// never a fallback (the never-auto-switch rule). It is billed like any chat
// call. The page passes `onDecide`, which wraps lib/mcpInteract's
// `decideSampling` (POST /chat/mcp/sampling/{id}).
//
// The app's request is folded under a <details> as PLAIN TEXT: what it wants
// to ask is the thing being decided, so it is one click away, never hidden.

import { useState, type KeyboardEvent } from "react";
import { Sparkles } from "lucide-react";
import { Button, LoaderInline } from "@/components/ui";
import { providerDisplay } from "@/lib/onboarding";
import { outcomeWords, type McpOutcome, type McpSamplingAsk } from "@/lib/mcpInteract";

type Decision = "approve" | "deny";

/** "Claude (claude-sonnet-4-5)" — the provider word plus the model id. */
export function modelWords(ask: Pick<McpSamplingAsk, "model" | "modelId">): string {
  const p = providerDisplay(ask.model);
  if (p && ask.modelId) return `${p} (${ask.modelId})`;
  return p || ask.modelId || "your model";
}

function stop(e: KeyboardEvent) {
  e.stopPropagation();
}

export function SamplingCard({
  ask,
  onDecide,
  docked = false,
}: {
  ask: McpSamplingAsk;
  onDecide: (decision: Decision) => Promise<boolean>;
  /** Calm chat W1-6 (v1.326.0): drawn in the composer's place, inside the
   *  dock's own card — so no border or tint of its own. */
  docked?: boolean;
}) {
  const [busy, setBusy] = useState<Decision | "">("");
  const [decided, setDecided] = useState<McpOutcome | null>(null);
  const [error, setError] = useState<string | null>(null);
  const outcome: McpOutcome | null = ask.outcome ?? decided;
  const closed = outcome !== null;
  const who = ask.pack || "An app";

  async function decide(d: Decision) {
    if (busy || closed) return;
    setBusy(d);
    setError(null);
    let ok = false;
    try {
      ok = await onDecide(d);
    } catch {
      ok = false;
    }
    setBusy("");
    if (ok) setDecided(d === "approve" ? "approved" : "denied");
    else setError("That did not go through. Try again.");
  }

  return (
    <div
      role={closed ? "group" : "alertdialog"}
      aria-label={`${who} wants to ask your model`}
      data-testid="mcp-sampling-card"
      data-outcome={outcome ?? ""}
      onKeyDown={stop}
      className={
        docked
          ? "space-y-2.5 px-4 pb-4 pt-3"
          : "space-y-2.5 rounded-xl border border-amber-400/30 bg-amber-400/[0.06] p-3"
      }
    >
      <div className="flex items-center gap-2">
        <Sparkles size={15} className="shrink-0 text-amber-300" aria-hidden="true" />
        <p data-testid="mcp-sampling-title" className="text-sm font-medium text-amber-100">
          {who} wants to ask {modelWords(ask)} a question for its own use
        </p>
      </div>
      <p className="text-[11px] leading-relaxed text-zinc-400">
        If you allow it, your model answers once and the answer goes to the app. It counts
        toward your usage like any chat. Nothing is sent until you choose.
      </p>

      <details className="rounded-lg border border-white/[0.08] bg-white/[0.02] px-3 py-2">
        <summary className="cursor-pointer text-xs text-zinc-300">See what it wants to ask</summary>
        <div className="mt-2 space-y-2" data-testid="mcp-sampling-request">
          {ask.system && (
            <div>
              <p className="text-[11px] font-medium text-zinc-500">Its instructions</p>
              <p className="whitespace-pre-wrap break-words text-xs text-zinc-300">{ask.system}</p>
            </div>
          )}
          {ask.messages.map((m, i) => (
            <div key={i}>
              <p className="text-[11px] font-medium text-zinc-500">
                {m.role === "user" ? "It asks" : "Earlier answer"}
              </p>
              <p className="whitespace-pre-wrap break-words text-xs text-zinc-300">{m.text}</p>
            </div>
          ))}
          {ask.more > 0 && (
            <p className="text-[11px] text-zinc-500">
              …and {ask.more} more {ask.more === 1 ? "message" : "messages"}
            </p>
          )}
          {ask.maxTokens !== null && (
            <p className="text-[11px] text-zinc-500" title={`${ask.maxTokens} tokens`}>
              Longest answer allowed: about {Math.max(1, Math.round(ask.maxTokens * 0.75))} words.
            </p>
          )}
        </div>
      </details>

      {error && (
        <p role="alert" className="text-[11px] text-tone-danger">
          {error}
        </p>
      )}

      {closed ? (
        <p data-testid="mcp-sampling-outcome" className="text-xs font-medium text-zinc-300">
          {outcomeWords(outcome, ask.pack)}
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {/* The dock's Enter / Esc press these buttons (lib/dockAsk). */}
          <Button
            variant="primary"
            size="sm"
            onClick={() => void decide("approve")}
            disabled={!!busy}
            data-dock-primary=""
          >
            {busy === "approve" ? <LoaderInline label="Allowing…" /> : "Allow once"}
          </Button>
          <Button
            variant="danger"
            size="sm"
            onClick={() => void decide("deny")}
            disabled={!!busy}
            data-dock-decline=""
          >
            {busy === "deny" ? <LoaderInline label="Declining…" /> : "Deny"}
          </Button>
        </div>
      )}
    </div>
  );
}
