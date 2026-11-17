/**
 * Third north-star fix plan, group 18, SB3 (decision G7): the browser
 * sidebar's conversation is a chat the daemon writes, marked
 * `comm_channel: "browser sidebar"` (`browser/panel_chat.SIDEBAR_CHANNEL`).
 *
 * It is daemon-owned like a phone chat, so an open one shows the messaging
 * banner. That banner promised "Replies here are sent to your phone", which
 * is untrue for a sidebar chat (no phone line is ever bound to it). It now
 * says where the chat came from and where to carry it on; a phone chat's
 * banner is unchanged.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CommThreadBanner, isSidebarChat, SIDEBAR_CHANNEL } from "@/components/chat/CommThreadBanner";

afterEach(cleanup);

describe("the banner of a chat the browser sidebar wrote", () => {
  it("names the sidebar and never promises a phone", () => {
    render(<CommThreadBanner channel="browser sidebar" display="Browser sidebar" />);
    expect(screen.getByText(/This chat is from the browser sidebar\./)).toBeTruthy();
    expect(screen.queryByText(/sent to your phone/)).toBeNull();
    expect(screen.queryByText(/also lives on/)).toBeNull();
  });

  it("keeps a phone chat's banner as it was", () => {
    render(<CommThreadBanner channel="telegram" display="Val" />);
    expect(
      screen.getByText(/This conversation also lives on Telegram \(Val\)\. Replies here are sent to your phone\./),
    ).toBeTruthy();
  });

  it("matches the daemon's marker exactly", () => {
    expect(SIDEBAR_CHANNEL).toBe("browser sidebar");
    expect(isSidebarChat("browser sidebar")).toBe(true);
    expect(isSidebarChat("telegram")).toBe(false);
    expect(isSidebarChat("")).toBe(false);
    expect(isSidebarChat(undefined)).toBe(false);
  });
});
