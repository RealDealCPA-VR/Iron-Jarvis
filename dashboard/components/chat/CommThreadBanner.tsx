"use client";

import { Smartphone } from "lucide-react";

/**
 * Slim header for an open DAEMON-OWNED (messaging) thread: says where the
 * conversation also lives and what a desktop reply does. The origin name is a
 * third-party proper noun (Telegram, Slack) — never our own retired "channel"
 * concept word.
 */
export function CommThreadBanner({
  channel,
  display,
}: {
  /** Messaging origin id, e.g. "telegram". */
  channel: string;
  /** Human sender label, e.g. "Val". */
  display?: string;
}) {
  const name = channel
    ? channel.charAt(0).toUpperCase() + channel.slice(1)
    : "your phone";
  return (
    <div className="flex items-center gap-2 border-b hairline bg-accent/[0.04] px-4 py-2 text-[12px] text-zinc-300">
      <Smartphone size={13} className="shrink-0 text-accent-soft" />
      <span className="min-w-0 truncate">
        This conversation also lives on {name}
        {display ? ` (${display})` : ""}. Replies here are sent to your phone.
      </span>
    </div>
  );
}
