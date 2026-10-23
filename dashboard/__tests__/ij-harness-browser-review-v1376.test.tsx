/**
 * v1.376.0 (IJ harness part 2, Phase 5) REVIEW — the screen's half of the
 * review's decisions about the agent's own browser:
 *
 *  - a click that reads as deleting, sending or paying asks every time: the
 *    daemon's approval frame carries once_reason "page", the stream hook
 *    keeps it (it whitelisted "git" alone), and the card says the rule in
 *    words — not the local-only line;
 *  - the Browser pane says why the agent's browser closed for the review's
 *    new reasons (Edge went away, the cap of four, Local only changed).
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { closedWords, decodeAgentBrowserView } from "@/lib/agentBrowser";
import { useChatStream } from "@/lib/useChatStream";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("a click that reads as deleting, sending or paying", () => {
  it("draws its own rule on the card, Allow once and Deny only", () => {
    render(
      <ApprovalCard
        approval={{
          id: "a1", callId: "c1", tool: "page_click", args: { target: "Delete account" },
          onceOnly: true, onceReason: "page",
          costNote: "Click “Delete account” on localhost:5173/settings in the agent's browser.",
        }}
        docked
      />,
    );
    expect(screen.getByTestId("approval-page-once")).toHaveTextContent(
      "a click on something that reads as deleting, sending or paying asks you every time",
    );
    expect(screen.queryByTestId("approval-local-only")).toBeNull();
    expect(screen.queryByTestId("approval-git-once")).toBeNull();
    expect(screen.getByTestId("approval-cost")).toHaveTextContent("Delete account");
    expect(screen.getByRole("button", { name: "Allow once" })).toBeInTheDocument();
    expect(screen.queryByTestId("approval-scope")).toBeNull();
  });

  it("keeps once_reason page from the stream (it kept only git)", async () => {
    let ctl!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({ start(c) { ctl = c; } });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } })));
    let api!: ReturnType<typeof useChatStream>;
    function Probe() {
      api = useChatStream();
      return null;
    }
    render(<Probe />);
    let done!: Promise<unknown>;
    act(() => {
      done = api.run({ messages: [{ role: "user", content: "click it" }] } as never, () => {}, () => {});
    });
    const frame = {
      id: "ap1", call_id: "c1", tool: "page_click", args: { target: "Delete account" },
      once_only: true, once_reason: "page", cost_note: "Click “Delete account” on localhost:5173.",
    };
    await act(async () => {
      ctl.enqueue(new TextEncoder().encode(`event: approval\ndata: ${JSON.stringify(frame)}\n\n`));
    });
    await waitFor(() => expect(api.approval?.tool).toBe("page_click"), { timeout: 5000 });
    expect(api.approval?.onceReason).toBe("page");
    await act(async () => {
      ctl.close();
      await done.catch(() => {});
    });
  });
});

describe("why the agent's browser closed", () => {
  const closed = (reason: string) =>
    closedWords(decodeAgentBrowserView({ view: { open: false, where: "localhost:5173", closed_reason: reason } })!);

  it("names the review's new reasons in plain words", () => {
    expect(closed("crashed")).toBe("Its browser stopped on its own (Edge was closed or crashed).");
    expect(closed("room")).toMatch(/to make room for another session's \(at most four are open at once\)/);
    expect(closed("local only changed")).toBe("Its browser closed when Local only changed.");
    expect(closed("ended")).toBe("Its browser is closed.");
  });
});
