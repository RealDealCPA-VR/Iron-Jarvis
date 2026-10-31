/**
 * v1.415.0 REVIEW — the Context drop-down's words, pinned red first.
 *
 * 1. Claude Code's DEFERRED tools ("loaded when needed") are, by definition,
 *    NOT sent with each message, yet the list above them said "Sent with
 *    each message, not counted above:". Only Iron Jarvis's own tool
 *    descriptions (Chat › Code) are sent with each message.
 * 2. In Chat › Code (Iron Jarvis's own harness) the same press the box's
 *    meter calls "Summarize" (v1.356.0 G75 retired "Compact now"; v1.389.0:
 *    "Summarize" replaced "Compact", `/compact` keeps Claude Code's name) was
 *    "Compact session" here. Build › Code keeps Claude Code's own word.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import fixture from "./fixtures/context-breakdown-v1415-0.json";

const api = vi.hoisted(() => ({ gets: {} as Record<string, unknown> }));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status = 0;
  }
  return {
    ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    get: (p: string) => (p in api.gets ? Promise.resolve(api.gets[p]) : Promise.reject(new ApiError(`unmocked ${p}`))),
    post: (p: string) => Promise.resolve(p === "/chat/changes" ? { changes: [] } : {}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div /> }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));

import { HarnessPanes, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { CodePanes } from "@/components/buildCode/CodePanes";
import { decodeSessionView } from "@/lib/buildSessions";
import { decodeCodeSession, decodeCodingTools } from "@/lib/buildApp";
import { COMPACTION_OFFER_BUTTON } from "@/lib/chatControlWords";

const LINE_CTX = fixture.harness_line_context as Record<string, unknown>;
const CLAUDE_CTX = fixture.claude_context as Record<string, unknown>;
const terminal: SessionTerminalApi = { find: () => null, open: async () => null, render: () => null, close: () => {} };

beforeEach(() => {
  api.gets = { "/settings/values": { values: { context_compaction: {} } } };
  window.sessionStorage.clear();
  window.localStorage.clear();
});
afterEach(() => cleanup());

function renderRail(extra: Partial<React.ComponentProps<typeof HarnessPanes>> = {}) {
  const v = decodeSessionView({
    id: "s1",
    title: "Cart",
    folder: "C:\\work\\shop",
    folder_name: "shop",
    session_id: "build:s1",
    messages: [
      { role: "user", content: "Add a cart", at: "2026-10-31T10:00:00" },
      { role: "assistant", content: "Done.", at: "2026-10-31T10:01:00", context: LINE_CTX },
    ],
  })!;
  render(
    <HarnessPanes view={v} livePlan={null} terminal={terminal} refreshKey={0} undoFor={() => null}
      onUndo={async () => {}} modelWords="Opus 5.5" {...extra} />,
  );
  fireEvent.click(screen.getByTestId("harness-pane-tab-session"));
}

describe("Claude Code's deferred tools are not said to be sent with each message", () => {
  it("Build › Code", async () => {
    api.gets["/build/app/sessions/app_1/context"] = CLAUDE_CTX;
    const session = decodeCodeSession({
      id: "app_1", harness: "claude-code", folder: "C:\\work\\shop", title: "t", account: "", model: "",
      effort: "", mode: "ask", harness_session_id: "", created_at: "2026-10-30T09:00:00Z",
      updated_at: "2026-10-30T09:00:00Z", archived_at: null, last_error: "",
      capabilities: { files: false, shell: false, browser: false, extensions: false, memory: true },
    })!;
    const tools = decodeCodingTools({
      harnesses: [{ id: "claude-code", label: "Claude Code", installed: true, signed_in: true, own_sign_in: false,
        modes: [{ id: "ask", label: "Ask first", dangerous: false, about: "" }], hidden_modes: [], models: [] }],
    });
    render(<CodePanes session={session} tools={tools} events={[]} running={false} onSend={vi.fn()} />);
    fireEvent.click(screen.getByTestId("code-pane-tab-session"));
    const extra = await screen.findByTestId("code-session-context-extra");
    expect(extra.textContent).toContain("loaded when needed");
    expect(extra.textContent).not.toMatch(/sent with each message/i);
    // Build › Code keeps Claude Code's own word for its own /compact.
    expect(screen.getByTestId("code-session-context-compact").textContent).toContain("Compact");
  });

  it("Chat › Code still says its own tool descriptions go with each message", () => {
    renderRail();
    expect(screen.getByTestId("session-rail-context-extra").textContent).toMatch(/sent with each message/i);
  });
});

describe("Chat › Code's press is the app's own word", () => {
  it("Summarize, never Compact — and the wait says the same word", () => {
    const onCompact = vi.fn();
    renderRail({ onCompact });
    const press = screen.getByTestId("session-rail-context-compact");
    expect(press.textContent).toBe(COMPACTION_OFFER_BUTTON);
    expect(press.textContent).not.toMatch(/compact/i);
    fireEvent.click(press);
    expect(onCompact).toHaveBeenCalledTimes(1);
    cleanup();
    renderRail({ onCompact, busy: true });
    expect(screen.getByTestId("session-rail-context-compact-why").textContent).toBe(
      "Wait for the reply to finish, then summarize the earlier turns.",
    );
  });
});
