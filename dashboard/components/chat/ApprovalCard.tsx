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
//   Allow for conversation the rest of this turn is granted server-side, and
//                          `onConversation` hands the tool to the composer's
//                          armed set so LATER turns arm it via the existing
//                          "+"-menu machinery — one grant store, not two.
//   Deny                   refuses. The daemon still records the refusal in
//                          the ledger as the user's decision (deny_reason),
//                          so "the user said no" is a fact the app remembers,
//                          not a call that never happened.

import { useState } from "react";
import { LoaderInline } from "@/components/ui";
import { post } from "@/lib/api";
import type { PendingApproval } from "@/lib/useChatStream";
import { ShieldQuestion } from "lucide-react";

/** One argument rendered for the decision. `command`/`code` are the payload
 *  for the tools this ships for (shell/repl) and render as a block — the rest
 *  ride a compact key: value line. */
function ArgLine({ name, value }: { name: string; value: unknown }) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if ((name === "command" || name === "code") && typeof value === "string") {
    return (
      <pre className="max-h-40 overflow-auto rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2 font-mono text-[11px] leading-relaxed text-zinc-200">
        {value}
      </pre>
    );
  }
  return (
    <p className="truncate text-[11px] text-zinc-400">
      <span className="text-zinc-500">{name}:</span> {text}
    </p>
  );
}

export function ApprovalCard({
  approval,
  onConversation,
}: {
  approval: PendingApproval;
  /** Called on "Allow for this conversation" so the page adds the tool to the
   *  composer's armed set — the persistence half of that button's promise. */
  onConversation?: (tool: string) => void;
}) {
  // Which button is in flight; the card disables itself after one click. The
  // resolved frame (or stream end) unmounts it — a second decision has no
  // write path.
  const [sent, setSent] = useState<"" | "once" | "conversation" | "deny">("");
  const [error, setError] = useState<string | null>(null);

  async function decide(decision: "once" | "conversation" | "deny") {
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

  const args = approval.args ?? {};
  return (
    <div
      role="alertdialog"
      aria-label={`Approve ${approval.tool}?`}
      className="space-y-2.5 rounded-xl border border-amber-400/30 bg-amber-400/[0.06] p-3"
      data-testid="chat-approval-card"
    >
      <div className="flex items-center gap-2">
        <ShieldQuestion size={15} className="shrink-0 text-amber-300" aria-hidden="true" />
        <p className="text-[12.5px] font-medium text-amber-100">
          The assistant wants to run{" "}
          <code className="rounded bg-black/30 px-1 font-mono text-[11.5px]">
            {approval.tool}
          </code>{" "}
          — your call.
        </p>
      </div>

      {Object.keys(args).length > 0 && (
        <div className="space-y-1.5">
          {Object.entries(args).map(([k, v]) => (
            <ArgLine key={k} name={k} value={v} />
          ))}
        </div>
      )}

      <p className="text-[11px] leading-relaxed text-zinc-400">
        The turn is paused until you answer. “Once” runs only this call;
        “this conversation” stops asking for {approval.tool} here; Deny
        refuses it and the assistant is told you declined.
      </p>

      {error && <p className="text-[11px] text-rose-300">{error}</p>}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void decide("once")}
          disabled={!!sent}
          className="btn-accent text-xs"
        >
          {sent === "once" ? <LoaderInline label="Running…" /> : "Allow once"}
        </button>
        <button
          type="button"
          onClick={() => void decide("conversation")}
          disabled={!!sent}
          className="btn-ghost text-xs"
        >
          {sent === "conversation" ? (
            <LoaderInline label="Allowing…" />
          ) : (
            "Allow for this conversation"
          )}
        </button>
        <button
          type="button"
          onClick={() => void decide("deny")}
          disabled={!!sent}
          className="btn-ghost text-xs text-rose-300 hover:text-rose-200"
        >
          {sent === "deny" ? <LoaderInline label="Declining…" /> : "Deny"}
        </button>
      </div>
    </div>
  );
}
