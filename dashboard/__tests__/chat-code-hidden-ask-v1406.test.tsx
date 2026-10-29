/**
 * v1.406.0 part 1 — a chat kept behind the Code view never answers a key.
 *
 * The chat page stays MOUNTED behind Chat | Code's Code view (hidden +
 * `inert`, components/chat/ChatCodeSurface). Its question dock (DockAsk)
 * answers Enter / Esc pressed with nothing focused — so, without a guard,
 * Enter in the Code view would press a hidden chat's Allow. A card under an
 * `[inert]` ancestor ignores the page's keys; the same card on screen still
 * answers them (the control).
 *
 * Also pinned (source): the hidden chat gives the sidebar's list slot up.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, API_BASE: "http://localhost", ijToken: () => "", post: H.post };
});

import { DockAsk } from "@/components/chat/DockAsk";
import type { DockAskItem } from "@/lib/dockAsk";
import type { PendingApproval } from "@/lib/useChatStream";

const APPROVAL: PendingApproval = { id: "apr_1", callId: "c1", tool: "shell", args: { command: "git status" } };
const ASK: DockAskItem = { kind: "approval", id: APPROVAL.id, approval: APPROVAL };

function renderDock(inert: boolean) {
  return render(
    <div data-testid="panel" inert={inert}>
      <DockAsk
        asks={[ASK]}
        onAnswerElicitation={vi.fn(async () => ({ ok: true }) as const)}
        onDecideSampling={vi.fn(async () => true)}
        armDelayMs={0}
      />
    </div>,
  );
}

const posted = () => H.post.mock.calls.filter((c) => c[0] === "/chat/approvals/apr_1");

beforeEach(() => {
  H.post.mockReset();
  H.post.mockResolvedValue({});
});
afterEach(() => cleanup());

describe("a hidden chat's question never answers the page's keys", () => {
  it("under an inert panel, Enter and Esc with nothing focused press nothing", async () => {
    renderDock(true);
    (document.activeElement as HTMLElement | null)?.blur?.();
    fireEvent.keyDown(document.body, { key: "Enter" });
    fireEvent.keyDown(document.body, { key: "Escape" });
    await new Promise((r) => setTimeout(r, 30));
    expect(posted()).toEqual([]);
  });

  it("CONTROL: the same card on screen still answers Enter", async () => {
    renderDock(false);
    (document.activeElement as HTMLElement | null)?.blur?.();
    fireEvent.keyDown(document.body, { key: "Enter" });
    await waitFor(() => expect(posted().map((c) => c[1])).toEqual([{ decision: "once" }]));
  });
});

describe("the hidden chat gives the sidebar's list slot to the Code view", () => {
  it("the chat page reads the slot only while it is shown", () => {
    const page = readFileSync(join(process.cwd(), "app", "chat", "page.tsx"), "utf8").replace(/\r\n/g, "\n");
    expect(page).toContain("const chatSlot = surfaceHidden ? null : chatSlotLive;");
    expect(page).toMatch(/export default function ChatPage\(\) \{\n\s+return <ChatCodeSurface renderChat=\{\(hidden\) => <ChatConversation surfaceHidden=\{hidden\} \/>\} \/>;/);
  });
});
