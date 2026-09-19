/**
 * v1.282.0 — the receipt says what Jarvis remembered, in the user's words.
 *
 * A `remember_preference` call used to show as "1 tool" and a door ("See the
 * lesson it saved"); the sentence itself was one click away. The app learning
 * something about the user is worth saying where they stand, so the receipt's
 * collapsed line carries "Remembered: …" in the accent, visible without
 * expanding — and a turn that kept nothing says nothing.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { TurnReceipt } from "@/components/chat/TurnReceipt";

afterEach(() => {
  cleanup();
});

describe("TurnReceipt — Remembered (v1.282.0)", () => {
  it("says the kept sentences on the collapsed line, without expanding", () => {
    render(
      <TurnReceipt
        toolsUsed={["remember_preference"]}
        remembered={["Prefers short answers with numbered steps", "Wants to be called VR"]}
      />,
    );
    const line = screen.getByTestId("turn-remembered");
    expect(line.textContent).toBe(
      "Remembered: Prefers short answers with numbered steps; Wants to be called VR",
    );
    // Still collapsed: the line is on the summary button itself.
    expect(screen.getByRole("button", { expanded: false }).textContent).toContain("Remembered:");
  });

  it("renders the receipt for a remembered sentence alone, and nothing for none", () => {
    const { container, unmount } = render(<TurnReceipt remembered={["Prefers PowerShell"]} />);
    expect(screen.getByTestId("turn-remembered").textContent).toBe("Remembered: Prefers PowerShell");
    unmount();
    const empty = render(<TurnReceipt remembered={[]} toolsUsed={[]} />);
    expect(empty.container.innerHTML).toBe("");
    expect(container.innerHTML).toBe("");
  });

  it("ignores junk entries", () => {
    render(<TurnReceipt remembered={["", "  ", "Keep replies short"] as string[]} />);
    expect(screen.getByTestId("turn-remembered").textContent).toBe("Remembered: Keep replies short");
  });
});

describe("the done frame's remembered list reaches the result (v1.282.0)", () => {
  it("decodes only non-empty strings, and stays absent when the frame has none", async () => {
    const { decodeSSE } = await vi.importActual<typeof import("@/lib/useChatStream")>(
      "@/lib/useChatStream",
    );
    const ev = decodeSSE("done", JSON.stringify({ reply: "r", remembered: ["Prefers X", "", 7] })) as {
      remembered?: string[];
    };
    expect(ev.remembered).toEqual(["Prefers X"]);
    const none = decodeSSE("done", JSON.stringify({ reply: "r" })) as { remembered?: string[] };
    expect("remembered" in none).toBe(false);
  });

  it("the real hook carries it onto the resolved result", async () => {
    const { useChatStream } = await vi.importActual<typeof import("@/lib/useChatStream")>(
      "@/lib/useChatStream",
    );
    const { renderHook, act } = await import("@testing-library/react");
    const frames = `event: done\ndata: ${JSON.stringify({ reply: "ok", remembered: ["Prefers X"] })}\n\n`;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(frames));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { status: 200 })));
    try {
      const { result } = renderHook(() => useChatStream());
      let settled: { reply: string; remembered?: string[] } | null = null;
      await act(async () => {
        settled = (await result.current.run({ messages: [] })) as typeof settled;
      });
      expect(settled!.remembered).toEqual(["Prefers X"]);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
