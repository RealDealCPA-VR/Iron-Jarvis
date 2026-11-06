/**
 * v1.441.0 — the review's fixes to the shared "Check usage now" control
 * (components/accounts/UsageRefresh):
 *
 *   U5. a failed read's words WRAP (a phone cut them after a few words) and
 *       the note's whole text is its tooltip;
 *   U6. "as of N min ago" redraws itself every USAGE_AS_OF_TICK_MS while the
 *       window is seen — an idle pane never keeps saying "just now".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const hooks = vi.hoisted(() => ({ answer: null as unknown }));

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  get: async () => ({}),
  post: async () => hooks.answer,
  patch: async () => ({}),
  put: async () => ({}),
  del: async () => ({}),
}));

import { UsageRefresh, USAGE_AS_OF_TICK_MS } from "@/components/accounts/UsageRefresh";

beforeEach(() => {
  hooks.answer = null;
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("U5: a failed read's words are never cut", () => {
  it("they wrap, in the warn tone, and the whole text is the tooltip", async () => {
    const words = "This login has no Claude plan usage to report; sign in with a Claude plan to see it.";
    hooks.answer = { account: null, read: { at: "2026-11-06T10:00:00Z", ok: false, words, throttled: false } };
    render(<UsageRefresh accountId="w" accountName="Work" />);
    fireEvent.click(screen.getByTestId("usage-refresh-w"));
    const note = screen.getByTestId("usage-refresh-note-w");
    await waitFor(() => expect(note.textContent).toBe(words));
    expect(note.className).not.toContain("truncate");
    expect(note.className).toContain("whitespace-normal");
    expect(note.className).toContain("text-tone-warn");
    expect(note.getAttribute("title")).toBe(words);
  });

  it("the quiet 'as of' line keeps one line, with its words as the tooltip", () => {
    render(<UsageRefresh accountId="w" accountName="Work" readAt={new Date(Date.now() - 4 * 60_000).toISOString()} />);
    const note = screen.getByTestId("usage-refresh-note-w");
    expect(note.textContent).toBe("as of 4 min ago");
    expect(note.className).toContain("truncate");
    expect(note.getAttribute("title")).toBe("as of 4 min ago");
  });
});

describe("U6: 'as of' redraws itself", () => {
  it("an idle control moves from 'just now' to minutes ago with no new read", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(new Date("2026-11-06T10:00:00Z"));
    render(<UsageRefresh accountId="w" accountName="Work" readAt="2026-11-06T10:00:00Z" />);
    const note = screen.getByTestId("usage-refresh-note-w");
    expect(note.textContent).toBe("as of just now");
    await act(async () => {
      vi.setSystemTime(new Date("2026-11-06T10:02:30Z"));
      vi.advanceTimersByTime(USAGE_AS_OF_TICK_MS);
    });
    expect(note.textContent).toBe("as of 3 min ago");
    expect(USAGE_AS_OF_TICK_MS).toBe(30_000);
  });
});
