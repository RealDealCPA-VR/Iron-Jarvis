"use client";

import { PanelRight, Smartphone } from "lucide-react";

/** The origin of a chat the browser sidebar wrote (third review SB3: the
 *  daemon's `browser/panel_chat.SIDEBAR_CHANNEL`). It is never bound to a
 *  phone, so nothing typed here is sent anywhere. */
export const SIDEBAR_CHANNEL = "browser sidebar";

/** True for a chat the browser sidebar wrote. */
/** What an open sidebar chat says, in its banner and when the user types in
 *  it on the Chat page (the page has no way to carry it on). */
export const SIDEBAR_CHAT_WORDS =
  "This chat is from the browser sidebar. To carry it on, ask in the sidebar.";

export function isSidebarChat(channel: string | null | undefined): boolean {
  return (channel ?? "") === SIDEBAR_CHANNEL;
}

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
  if (isSidebarChat(channel)) {
    // A sidebar chat is not a phone conversation: promising "sent to your
    // phone" here would be untrue. It is carried on where it was started.
    return (
      <div className="flex items-center gap-2 border-b hairline bg-accent/[0.04] px-4 py-2 text-[12px] text-zinc-300">
        <PanelRight size={13} className="shrink-0 text-accent-soft" />
        <span className="min-w-0 truncate">
          {SIDEBAR_CHAT_WORDS}
        </span>
      </div>
    );
  }
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
