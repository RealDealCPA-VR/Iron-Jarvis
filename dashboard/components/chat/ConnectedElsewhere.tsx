"use client";

/**
 * Readiness 12B (G15): "GitHub connected · 26 tools · Continue" under the
 * newest reply, when the app the chat offered to connect was connected
 * SOMEWHERE ELSE (the Directory, an OAuth sign-in, a key saved in Settings ›
 * Connections). The SAME words and press as the chat's own key card
 * (components/chat/ConfigCards → CredentialCard), so the two ways in read as
 * one. A press continues; nothing is sent by itself (lib/connectedElsewhere).
 */

import { ArrowRight, Check } from "lucide-react";
import { connectedNowWords, type ConnectedNow } from "@/lib/connectedElsewhere";

export function ConnectedElsewhere({
  connected,
  onContinue,
}: {
  connected: ConnectedNow;
  /** Absent while a turn runs: the line says it, the press waits. */
  onContinue?: () => void;
}) {
  return (
    <div
      data-testid="connected-elsewhere"
      className="mt-1.5 flex flex-wrap items-center gap-3 text-[13px]"
    >
      <span className="inline-flex items-center gap-1.5 font-medium text-tone-success">
        <Check size={13} aria-hidden /> {connectedNowWords(connected)}
      </span>
      {onContinue && (
        <button
          type="button"
          data-testid="connected-elsewhere-continue"
          onClick={onContinue}
          title="Carry on with what you asked, now that it is connected"
          className="inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-[12px] text-accent-soft transition-colors hover:bg-accent/[0.08]"
        >
          Continue
          <ArrowRight size={12} aria-hidden />
        </button>
      )}
    </div>
  );
}
