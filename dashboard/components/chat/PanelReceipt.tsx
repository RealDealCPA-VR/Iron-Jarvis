"use client";

/**
 * The quiet receipt pieces of an agent's line in the chat (v1.355.0,
 * product-readiness G62; the facts are lib/panelReceipt's).
 *
 *  - `PanelReplyLine` — under an @-agent's or a remote's reply, the same
 *    quiet voice as Jarvis's own "answered by …": "from hermes · 6 min 12 s"
 *    (the time from the question; left out when either stamp is missing).
 *  - `PanelWaiting` — beside a remote's "still working" line that nothing
 *    has followed yet: "· waiting 3 min" in the warn tone. It ticks ITSELF
 *    (TurnClock's `useNow`), so the chat page never re-renders to move it,
 *    and it shows whole minutes only, so it changes once a minute. No
 *    animation, so nothing for reduced motion to stop.
 *
 * No box, no border: plain text in the chat's existing grey (the calm chat).
 */

import { useNow } from "@/components/chat/TurnClock";
import { secondsText } from "@/components/chat/TurnReceipt";
import { panelReplyMs, waitingWords } from "@/lib/panelReceipt";

export function PanelReplyLine({
  name,
  replyAt,
  askedAt,
}: {
  /** The agent's display name ("hermes", "Builder"). */
  name: string;
  /** When the reply landed (ISO), if known. */
  replyAt?: string;
  /** When the question was sent (ISO), if known. */
  askedAt?: string;
}) {
  const took = secondsText(panelReplyMs(replyAt, askedAt));
  return (
    <div data-testid="panel-receipt" className="text-[12px] text-zinc-500">
      from {name}
      {took ? ` · ${took}` : ""}
    </div>
  );
}

export function PanelWaiting({ since }: { since?: string }) {
  const start = since ? Date.parse(since) : Number.NaN;
  const known = Number.isFinite(start);
  const now = useNow(known);
  return (
    <span
      data-testid="panel-waiting"
      title="Still waiting for this agent's answer. It will appear here when it arrives."
      className="shrink-0 text-tone-warn"
    >
      · {known ? waitingWords(now - start) : "waiting"}
    </span>
  );
}
