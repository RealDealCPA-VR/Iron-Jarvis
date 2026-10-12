/**
 * v1.335.0 — the chat's "⋯" panel says what THIS chat has used.
 * Read from GET /chat/threads/{id}/usage; nothing is drawn while it loads,
 * when the read fails (an older daemon), or when nothing was counted. A
 * subscription's figure is a list-price equivalent, never money spent.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const get = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ get }));

import { ChatUsageLine, usageWords } from "@/components/chat/ChatUsageLine";

afterEach(() => {
  cleanup();
  get.mockReset();
});

const u = (over = {}) => ({
  turns: 3,
  failed_turns: 0,
  input_tokens: 10_000,
  output_tokens: 2_400,
  cost_usd: 0,
  list_price_equivalent_usd: 0,
  ...over,
});

describe("the words", () => {
  it("names tokens, and money only when there was some", () => {
    expect(usageWords(u())).toBe("This chat used 12.4k tokens");
    expect(usageWords(u({ cost_usd: 0.0412 }))).toBe("This chat used 12.4k tokens · $0.04");
    expect(usageWords(u({ cost_usd: 0.004 }))).toBe("This chat used 12.4k tokens · under $0.01");
  });

  it("calls a subscription's figure what it is", () => {
    expect(usageWords(u({ list_price_equivalent_usd: 0.5 }))).toBe(
      "This chat used 12.4k tokens · ≈$0.50 at list price (subscription)",
    );
  });

  it("says nothing when nothing was counted", () => {
    expect(usageWords(u({ turns: 0 }))).toBe("");
    expect(usageWords(null)).toBe("");
  });
});

describe("the line", () => {
  it("reads this chat's usage and shows it", async () => {
    get.mockResolvedValue(u({ cost_usd: 1.5 }));
    render(<ChatUsageLine threadId="t 1" />);
    expect((await screen.findByTestId("chat-more-usage")).textContent).toBe(
      "This chat used 12.4k tokens · $1.50",
    );
    expect(get).toHaveBeenCalledWith("/chat/threads/t%201/usage");
  });

  it("draws nothing when the daemon cannot answer", async () => {
    get.mockRejectedValue(new Error("404"));
    render(<ChatUsageLine threadId="t1" />);
    await Promise.resolve();
    await Promise.resolve();
    expect(screen.queryByTestId("chat-more-usage")).toBeNull();
  });

  it("a bare mock that returns nothing never crashes the page", async () => {
    get.mockReturnValue(undefined);
    render(<ChatUsageLine threadId="t1" />);
    await Promise.resolve();
    expect(screen.queryByTestId("chat-more-usage")).toBeNull();
  });
});
