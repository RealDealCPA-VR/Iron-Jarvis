/**
 * v1.332.0 (L3) — in a "local only" chat, a tool that reaches past this PC
 * asks EVERY time, and its card says so.
 *
 * The daemon marks such a card `once_only: true` (and reads any wider answer
 * as once). The page must keep the mark through the stream decoder's
 * whitelist and the hook, and the card must then offer exactly "Allow once"
 * and "Deny" plus one quiet line saying why: no "Allow for this tab", no
 * "Allow for this conversation", no "Always allow exactly this". The same
 * card is what the dock draws in the composer's place. Outside local only the
 * card is unchanged (each block carries that control).
 */
import { act, cleanup, fireEvent, render, renderHook, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, API_BASE: "http://localhost", ijToken: () => "", post: H.post };
});

import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { DockAsk } from "@/components/chat/DockAsk";
import type { DockAskItem } from "@/lib/dockAsk";
import { decodeSSE, useChatStream, type PendingApproval } from "@/lib/useChatStream";

const NAV: PendingApproval = {
  id: "apr_1",
  callId: "c1",
  tool: "browser_navigate",
  args: { url: "https://example.com/" },
  canAlways: true,
};
// canAlways left true on purpose: once only wins even over a stray "Always".
const LOCAL: PendingApproval = { ...NAV, onceOnly: true };
const LINE = "This chat is local only, so this asks every time.";

beforeEach(() => {
  H.post.mockReset();
  H.post.mockResolvedValue({ ok: true });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const buttonNames = (root: HTMLElement) =>
  within(root)
    .getAllByRole("button")
    .map((b) => b.textContent?.trim());

describe("the stream keeps once_only", () => {
  it("the decoder keeps a real true and drops anything else", () => {
    const frame = { id: "a", call_id: "c", tool: "shell" };
    const yes = decodeSSE("approval", JSON.stringify({ ...frame, once_only: true })) as Record<string, unknown>;
    expect(yes.once_only).toBe(true);
    for (const junk of [1, "true", null, false]) {
      const no = decodeSSE("approval", JSON.stringify({ ...frame, once_only: junk })) as Record<string, unknown>;
      expect("once_only" in no).toBe(false);
    }
  });

  async function approvalAfter(frame: Record<string, unknown>) {
    let close: () => void = () => {};
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(`event: approval\ndata: ${JSON.stringify(frame)}\n\n`));
        close = () => controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { status: 200 })));
    const { result } = renderHook(() => useChatStream());
    let run: Promise<unknown> = Promise.resolve();
    act(() => {
      run = result.current.run({ messages: [] }).catch(() => null);
    });
    await vi.waitFor(() => expect(result.current.approval?.id).toBe("apr_9"));
    const seen = result.current.approval;
    await act(async () => {
      close();
      await run;
    });
    return seen!;
  }

  it("the real hook puts onceOnly on the pending approval, and only when sent", async () => {
    const frame = { id: "apr_9", call_id: "c9", tool: "shell", args: { command: "ls" } };
    expect((await approvalAfter({ ...frame, once_only: true })).onceOnly).toBe(true);
    expect("onceOnly" in (await approvalAfter(frame))).toBe(false); // control
  });
});

describe("a local-only card offers Allow once and Deny only", () => {
  it("shows two answers and the quiet line, and posts once", async () => {
    render(<ApprovalCard approval={LOCAL} />);
    const card = screen.getByTestId("chat-approval-card");
    expect(buttonNames(card)).toEqual(["Allow once", "Deny"]);
    expect(within(card).getByTestId("approval-local-only").textContent).toBe(LINE);
    expect(within(card).queryByTestId("approval-tab-note")).toBeNull();
    expect(within(card).queryByTestId("approval-always-note")).toBeNull();
    expect(card.textContent).not.toContain("this conversation");
    fireEvent.click(within(card).getByText("Allow once"));
    await vi.waitFor(() => expect(H.post).toHaveBeenCalledTimes(1));
    expect(H.post.mock.calls[0][1]).toEqual({ decision: "once" });
  });

  it("the line is calm: no box, whole pixels, no spaced dash", () => {
    render(<ApprovalCard approval={LOCAL} />);
    const line = screen.getByTestId("approval-local-only");
    expect(line.className).not.toMatch(/\b(border|rounded|bg-)/);
    expect(line.className).not.toMatch(/\.5px|\[\d+\.\d+px\]/);
    expect(line.textContent).not.toContain(" — ");
  });

  it("control: outside local only the same browser card keeps every answer", () => {
    render(<ApprovalCard approval={NAV} />);
    const card = screen.getByTestId("chat-approval-card");
    expect(buttonNames(card)).toEqual([
      "Allow for this tab",
      "Allow once",
      "Allow for this conversation",
      "Always allow exactly this",
      "Deny",
    ]);
    expect(within(card).queryByTestId("approval-local-only")).toBeNull();
  });
});

describe("the dock draws the same once-only card", () => {
  const item = (a: PendingApproval): DockAskItem => ({ kind: "approval", id: a.id, approval: a });
  const dock = (a: PendingApproval) =>
    render(
      <DockAsk
        asks={[item(a)]}
        onAnswerElicitation={vi.fn(async () => ({ ok: true }) as const)}
        onDecideSampling={vi.fn(async () => true)}
        onConversation={vi.fn()}
        armDelayMs={0}
      />,
    );

  it("in the composer's place it offers Allow once and Deny with the line", () => {
    dock(LOCAL);
    const card = screen.getByTestId("chat-approval-card");
    expect(buttonNames(card)).toEqual(["Allow once", "Deny"]);
    expect(screen.getByText(LINE)).toBeTruthy();
  });

  it("control: a card that is not local only keeps its wider answers in the dock", () => {
    dock(NAV);
    expect(screen.getByText("Allow for this conversation")).toBeTruthy();
    expect(screen.queryByText(LINE)).toBeNull();
  });
});
