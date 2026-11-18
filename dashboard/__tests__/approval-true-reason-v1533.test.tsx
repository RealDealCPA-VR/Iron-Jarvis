/**
 * Fourth review PG-A / C-PG-2: a card that asks every time says the TRUE
 * reason. The daemon now names a reason on EVERY once-only card
 * (`once_reason`: "local_only", "locked", "night", "once", a check's own…).
 *
 *  - "local_only" (and no reason at all: an older daemon, a listing row)
 *    keeps today's line, "This chat is local only, so this asks every time."
 *  - "address" (a new place Iron Jarvis's things go) has its own line and is
 *    a SAFETY card: Enter never allows it, only a click.
 *  - "extension" (adding an extension or an app): its own line, a safety card.
 *  - any other reason with no line of its own ("locked", "batch", "once",
 *    "night") says "This asks you every time, whatever your approval
 *    setting." and NEVER the Local only line; Enter keeps allowing it, as
 *    before.
 *  - the real stream hook keeps each reason on the pending approval.
 */
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({ posts: [] as unknown[] }));

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return {
    ...real,
    API_BASE: "http://localhost",
    ijToken: () => "",
    post: vi.fn(async (_path: string, body: unknown) => {
      H.posts.push(body);
      return { ok: true };
    }),
  };
});

import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { DockAsk } from "@/components/chat/DockAsk";
import { ENTER_BLOCKED_NOTE, enterAllows, type DockAskItem } from "@/lib/dockAsk";
import { isSafetyOnceReason, onceReasonOf } from "@/lib/onceReason";
import { useChatStream, type PendingApproval } from "@/lib/useChatStream";

const LOCAL_LINE = "This chat is local only, so this asks every time.";
const ADDRESS_LINE =
  "Sending Iron Jarvis's things to a new address asks you every time, whatever your approval setting.";
const EXTENSION_LINE = "Adding an extension or an app asks you every time, whatever your approval setting.";
const EVERY_TIME_LINE = "This asks you every time, whatever your approval setting.";

const BASE: PendingApproval = {
  id: "apr_1",
  callId: "c1",
  tool: "webhook_add",
  args: { slug: "deploys", direction: "outbound", target_url: "https://hooks.example.test" },
  onceOnly: true,
};
const withReason = (reason: string | undefined, extra: Partial<PendingApproval> = {}): PendingApproval =>
  ({ ...BASE, ...(reason === undefined ? {} : { onceReason: reason }), ...extra }) as PendingApproval;
const item = (a: PendingApproval): DockAskItem => ({ kind: "approval", id: a.id, approval: a });

beforeEach(() => {
  H.posts.length = 0;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("each reason's line on the card", () => {
  it("local_only keeps today's Local only line", () => {
    render(<ApprovalCard approval={withReason("local_only")} scope="chat" />);
    expect(screen.getByTestId("approval-local-only").textContent).toBe(LOCAL_LINE);
    expect(screen.queryByTestId("approval-every-time")).toBeNull();
  });

  it("no reason at all (an older daemon, a listing row) keeps the Local only line", () => {
    render(<ApprovalCard approval={withReason(undefined)} scope="chat" />);
    expect(screen.getByTestId("approval-local-only").textContent).toBe(LOCAL_LINE);
  });

  it("address says a new address asks every time, never Local only", () => {
    render(<ApprovalCard approval={withReason("address")} scope="chat" />);
    expect(screen.getByTestId("approval-address-once").textContent).toBe(ADDRESS_LINE);
    expect(screen.queryByTestId("approval-local-only")).toBeNull();
    expect(screen.queryByText(LOCAL_LINE)).toBeNull();
  });

  it("extension says adding an extension or an app asks every time", () => {
    render(<ApprovalCard approval={withReason("extension", { tool: "mcp_server_add" })} scope="chat" />);
    expect(screen.getByTestId("approval-extension-once").textContent).toBe(EXTENSION_LINE);
    expect(screen.queryByText(LOCAL_LINE)).toBeNull();
  });

  for (const reason of ["locked", "batch", "once", "night"]) {
    it(`${reason}: the plain every-time line, never Local only`, () => {
      render(<ApprovalCard approval={withReason(reason)} scope="chat" />);
      expect(screen.getByTestId("approval-every-time").textContent).toBe(EVERY_TIME_LINE);
      expect(screen.queryByTestId("approval-local-only")).toBeNull();
      expect(screen.queryByText(LOCAL_LINE)).toBeNull();
    });
  }

  it("each card still offers Allow once and Deny only", () => {
    for (const reason of ["address", "extension", "locked", "local_only"]) {
      render(<ApprovalCard approval={withReason(reason, { canAlways: true })} scope="chat" />);
      const names = screen.getAllByRole("button").map((b) => b.textContent?.trim());
      expect(names).toEqual(["Allow once", "Deny"]);
      cleanup();
    }
  });
});

describe("Enter", () => {
  it("address and extension are safety cards; the others keep Enter as before", () => {
    for (const r of ["address", "extension"]) {
      expect(isSafetyOnceReason(r)).toBe(true);
      expect(enterAllows(item(withReason(r)))).toBe(false);
    }
    for (const r of ["local_only", "locked", "batch", "once", "night"]) {
      expect(isSafetyOnceReason(r)).toBe(false);
      expect(enterAllows(item(withReason(r)))).toBe(true);
    }
    // The older safety reasons are untouched.
    for (const r of ["git", "page", "command", "memory", "guarded", "privacy", "undo", "looked_up"]) {
      expect(isSafetyOnceReason(r)).toBe(true);
    }
  });

  it("in the dock, Enter on an address card does not allow it; a click does", async () => {
    render(
      <DockAsk
        asks={[item(withReason("address"))]}
        onAnswerElicitation={vi.fn(async () => ({ ok: true }) as const)}
        onDecideSampling={vi.fn(async () => true)}
        onConversation={vi.fn()}
        armDelayMs={0}
      />,
    );
    (document.activeElement as HTMLElement | null)?.blur?.();
    fireEvent.keyDown(document.body, { key: "Enter" });
    expect(screen.getByTestId("dock-ask-enter-blocked").textContent).toBe(ENTER_BLOCKED_NOTE);
    expect(H.posts).toEqual([]);
    fireEvent.click(screen.getByText("Allow once"));
    await waitFor(() => expect(H.posts).toEqual([{ decision: "once" }]));
  });

  it("control: in the dock, Enter on a locked app's card still allows once", async () => {
    render(
      <DockAsk
        asks={[item(withReason("locked"))]}
        onAnswerElicitation={vi.fn(async () => ({ ok: true }) as const)}
        onDecideSampling={vi.fn(async () => true)}
        onConversation={vi.fn()}
        armDelayMs={0}
      />,
    );
    (document.activeElement as HTMLElement | null)?.blur?.();
    fireEvent.keyDown(document.body, { key: "Enter" });
    await waitFor(() => expect(H.posts).toEqual([{ decision: "once" }]));
  });
});

describe("the stream keeps every reason", () => {
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

  for (const reason of ["local_only", "locked", "address", "extension", "batch", "once"]) {
    it(`keeps ${reason}`, async () => {
      expect(onceReasonOf(reason)).toBe(reason);
      const seen = await approvalAfter({
        id: "apr_9", call_id: "c9", tool: "webhook_add", args: {}, once_only: true, once_reason: reason,
      });
      expect(seen.onceReason).toBe(reason);
    });
  }

  it("still drops a reason it has no words for", () => {
    expect(onceReasonOf("commands")).toBeUndefined();
    expect(onceReasonOf(1)).toBeUndefined();
  });
});
