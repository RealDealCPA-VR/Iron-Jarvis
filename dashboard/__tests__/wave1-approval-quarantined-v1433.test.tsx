/**
 * v1.433.0 (Wave 1 B, A16): the "new in its app" warning reaches the card.
 *
 * Since v1.299.0 the stream lane sends `quarantined: true` on an approval
 * frame for an app tool that is new (or changed) since the user added the
 * app (daemon/routes/chat.py), and never offers "Always" for it. The
 * dashboard's decoder dropped the key, so the card never said why this tool
 * is different. Now BOTH halves keep it — the SSE decoder's whitelist and the
 * hook's PendingApproval — and the card shows ONE plain line. A card without
 * the key is unchanged byte for byte (approval-card-why-v1417 holds the
 * fixture `approval-card-old-dom-v1417.json`).
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, post: async () => ({ ok: true }) };
});

import { ApprovalCard, type ApprovalAsk } from "@/components/chat/ApprovalCard";
import { QUARANTINED_LINE } from "@/lib/approvalWords";
import { decodeSSE, useChatStream } from "@/lib/useChatStream";

afterEach(() => cleanup());

const FRAME = { id: "apr_q1", call_id: "c1", tool: "mcp__notion__delete_page", args: { page_id: "p1" } };

describe("the stream keeps `quarantined`", () => {
  it("the decoder keeps a real true and nothing else", () => {
    const yes = decodeSSE("approval", JSON.stringify({ ...FRAME, quarantined: true })) as Record<string, unknown>;
    expect(yes.quarantined).toBe(true);
    for (const junk of [false, "true", 1, null, {}]) {
      const no = decodeSSE("approval", JSON.stringify({ ...FRAME, quarantined: junk })) as Record<string, unknown>;
      expect("quarantined" in no, JSON.stringify(junk)).toBe(false);
    }
    const plain = decodeSSE("approval", JSON.stringify(FRAME)) as Record<string, unknown>;
    expect("quarantined" in plain).toBe(false);
  });

  async function approvalAfter(f: Record<string, unknown>) {
    let close: () => void = () => {};
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(`event: approval\ndata: ${JSON.stringify(f)}\n\n`));
        close = () => controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { status: 200 })));
    const { result } = renderHook(() => useChatStream());
    let run: Promise<unknown> = Promise.resolve();
    act(() => {
      run = result.current.run({ messages: [] }).catch(() => null);
    });
    await vi.waitFor(() => expect(result.current.approval?.id).toBe("apr_q1"));
    const seen = result.current.approval;
    await act(async () => {
      close();
      await run;
    });
    vi.unstubAllGlobals();
    return seen!;
  }

  it("the real hook puts it on the pending approval, and only when sent", async () => {
    const got = await approvalAfter({ ...FRAME, quarantined: true });
    expect(got.quarantined).toBe(true);
    const plain = await approvalAfter(FRAME);
    expect("quarantined" in plain).toBe(false);
  });
});

const ASK: ApprovalAsk = {
  id: "apr_q1",
  callId: "c1",
  tool: "mcp__notion__delete_page",
  args: { page_id: "p1" },
  timeoutS: 0,
};

describe("the card says the tool is new in its app", () => {
  it("shows one plain line when quarantined", () => {
    render(<ApprovalCard approval={{ ...ASK, quarantined: true }} scope="chat" />);
    const line = screen.getByTestId("approval-quarantined");
    expect(line.textContent).toBe(QUARANTINED_LINE);
    // v1.433.0 review: the daemon also holds a tool whose shape CHANGED
    // (mcp/manifest.py), and the person added the app, never the tool — the
    // first cut said "new … since you added it" (the tool) for both.
    expect(QUARANTINED_LINE).toBe(
      "This tool is new or changed in its app since you added the app. Check it before you allow it.",
    );
    expect(screen.getAllByText(QUARANTINED_LINE)).toHaveLength(1);
  });

  it("shows it on the compact card too (the daemon said why it asks)", () => {
    render(
      <ApprovalCard approval={{ ...ASK, quarantined: true, why: "It is new in its app." }} scope="chat" docked />,
    );
    expect(screen.getByTestId("approval-quarantined").textContent).toBe(QUARANTINED_LINE);
  });

  it("says nothing when not quarantined", () => {
    render(<ApprovalCard approval={{ ...ASK, quarantined: false }} scope="chat" />);
    expect(screen.queryByTestId("approval-quarantined")).toBeNull();
    cleanup();
    render(<ApprovalCard approval={ASK} scope="chat" />);
    expect(screen.queryByTestId("approval-quarantined")).toBeNull();
  });

  it("the old cards' fixture holds no such line (cards without the key are unchanged)", () => {
    const fixture = readFileSync(path.join(__dirname, "fixtures", "approval-card-old-dom-v1417.json"), "utf8");
    expect(fixture).not.toContain("approval-quarantined");
  });
});
