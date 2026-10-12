"use client";

/**
 * v1.335.0: what THIS chat has used, as one quiet line in the chat's "⋯"
 * panel (from pi-durable's per-conversation usage). Read once each time the
 * panel opens (`GET /chat/threads/{id}/usage`); renders nothing while it
 * loads, when the read fails (an older daemon answers 404), or when nothing
 * has been counted yet. A flat-subscription figure is a list-price
 * equivalent and says so, never "spent" (the Usage page's rule).
 */

import { useEffect, useState } from "react";
import { get } from "@/lib/api";
import { formatTokens } from "@/lib/format";

export type ChatUsage = {
  turns: number;
  failed_turns: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  list_price_equivalent_usd: number;
};

function dollars(n: number): string {
  if (!(n > 0)) return "$0";
  return n < 0.01 ? "under $0.01" : `$${n.toFixed(2)}`;
}

/** The line's words, or "" when there is nothing to say. Exported for tests. */
export function usageWords(u: ChatUsage | null | undefined): string {
  if (!u || !(u.turns > 0)) return "";
  const tokens = formatTokens((u.input_tokens || 0) + (u.output_tokens || 0));
  const parts = [`This chat used ${tokens} tokens`];
  if (u.cost_usd > 0) parts.push(dollars(u.cost_usd));
  if (u.list_price_equivalent_usd > 0) {
    parts.push(`≈${dollars(u.list_price_equivalent_usd)} at list price (subscription)`);
  }
  return parts.join(" · ");
}

export function ChatUsageLine({ threadId }: { threadId: string }) {
  const [usage, setUsage] = useState<ChatUsage | null>(null);
  useEffect(() => {
    let live = true;
    // Through a resolved promise, so a `get` that returns nothing (a bare
    // mock) is a missing line, never a crash in the page that opened the menu.
    Promise.resolve()
      .then(() => get<ChatUsage>(`/chat/threads/${encodeURIComponent(threadId)}/usage`))
      .then((u) => {
        if (live) setUsage(u);
      })
      .catch(() => {
        /* an older daemon, or nothing to read: no line */
      });
    return () => {
      live = false;
    };
  }, [threadId]);
  const words = usageWords(usage);
  if (!words) return null;
  return (
    <p
      data-testid="chat-more-usage"
      className="px-2.5 pb-1 pt-1.5 text-[12px] leading-snug text-zinc-500"
    >
      {words}
    </p>
  );
}
