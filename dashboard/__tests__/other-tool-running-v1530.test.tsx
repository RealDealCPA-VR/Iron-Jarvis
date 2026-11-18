import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * v1.530.0 — the Other tools part in its two places.
 *
 *  - the chat list ("chat") holds Claude Chat only, and offers Claude desktop;
 *  - Chat › Code's list ("code") holds Claude Code and Codex, and offers those;
 *  - a session working now has a dot, the word "working" and its newest step,
 *    and its tool's line says how many work; while one works the list is read
 *    again every 15 s, and never while none does;
 *  - a press opens the watch view (its newest steps, read again every 4 s
 *    while it works, and no more once closed);
 *  - Continue here is plain only for a session quiet for 10 minutes; for one
 *    written to since, only "It's closed in …, continue here", which sends the
 *    owner's word (elsewhere_closed) with the request.
 */

const calls: { get: string[]; post: { path: string; body: unknown }[] } = { get: [], post: [] };
let answer: unknown = {};
let steps: unknown = {};
let postAnswer: () => unknown = () => ({ id: "hs_made" });
const push = vi.fn();

vi.mock("@/lib/api", () => ({
  get: async (path: string) => {
    calls.get.push(path);
    return path.endsWith("/steps") ? steps : answer;
  },
  post: async (path: string, body: unknown) => {
    calls.post.push({ path, body });
    return postAnswer();
  },
}));

import OtherToolChats from "@/components/chat/OtherToolChats";
import { FIRST_READ_MS, WATCH_REREAD_MS, WORKING_REREAD_MS } from "@/lib/otherTools";

const AT = "2026-11-17T10:00:00+00:00";

function row(id: string, title: string, over: Record<string, unknown> = {}) {
  return { id, title, folder: "C:\\Work\\Parser", started: AT, ended: AT, working: false, quiet_s: 7200, doing: "", ...over };
}

function tool(name: string, label: string, chats: unknown[], openable = true) {
  return { tool: name, label, openable, folders: [{ folder: "C:\\Work\\Parser", name: "Parser", chats }] };
}

function app(name: string, label: string) {
  return { app: name, label, installed: true, connected: false, entry_missing: false, count: 0, last: "" };
}

const ALL = {
  tools: [
    tool("claude-code", "Claude Code", [
      row("live", "Speed up the import", { working: true, quiet_s: 12, doing: "Ran: pytest -q" }),
      row("old", "Fix the parser"),
    ]),
    tool("claude-chat", "Claude Chat", [row("r:1", "Client tax planning questions")], false),
    tool("codex", "Codex", [row("x1", "Tidy the tests", { quiet_s: 300 })]),
  ],
  apps: [app("claude-desktop", "Claude desktop"), app("claude-code", "Claude Code"), app("codex", "Codex")],
};

async function shown(mode: "chat" | "code") {
  render(<OtherToolChats mode={mode} go={push} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(FIRST_READ_MS + 10);
  });
}

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  calls.get = [];
  calls.post = [];
  answer = ALL;
  steps = { working: true, quiet_s: 12, steps: [{ at: AT, kind: "asked", words: "You: speed it up" }, { at: AT, kind: "ran", words: "Ran: pytest -q" }] };
  postAnswer = () => ({ id: "hs_made" });
  push.mockReset();
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("each place holds its own kind", () => {
  it("the chat list holds Claude Chat and offers Claude desktop only", async () => {
    await shown("chat");
    expect(screen.getByTestId("other-tool-claude-chat")).toBeInTheDocument();
    expect(screen.queryByTestId("other-tool-claude-code")).toBeNull();
    expect(screen.queryByTestId("other-tool-codex")).toBeNull();
    expect(screen.getByTestId("other-app-connect-claude-desktop")).toBeInTheDocument();
    expect(screen.queryByTestId("other-app-connect-claude-code")).toBeNull();
    expect(screen.queryByTestId("other-app-connect-codex")).toBeNull();
  });

  it("Chat › Code holds Claude Code and Codex and offers those", async () => {
    await shown("code");
    expect(screen.getByTestId("other-tools-code")).toBeInTheDocument();
    expect(screen.getByTestId("other-tool-claude-code")).toBeInTheDocument();
    expect(screen.getByTestId("other-tool-codex")).toBeInTheDocument();
    expect(screen.queryByTestId("other-tool-claude-chat")).toBeNull();
    expect(screen.getByTestId("other-app-connect-claude-code")).toBeInTheDocument();
    expect(screen.getByTestId("other-app-connect-codex")).toBeInTheDocument();
    expect(screen.queryByTestId("other-app-connect-claude-desktop")).toBeNull();
  });
});

describe("working now", () => {
  it("says it in words, with the newest step, and counts it on the tool's line", async () => {
    await shown("code");
    expect(screen.getByTestId("other-tool-working-claude-code")).toHaveTextContent("1 working");
    fireEvent.click(screen.getByTestId("other-tool-claude-code"));
    expect(screen.getByTestId("other-chat-live")).toHaveTextContent("working");
    expect(screen.getByTestId("other-chat-working-live")).toBeInTheDocument();
    expect(screen.getByTestId("other-chat-doing-live")).toHaveTextContent("Ran: pytest -q");
    expect(screen.queryByTestId("other-chat-working-old")).toBeNull();
    expect(screen.queryByTestId("other-chat-doing-old")).toBeNull();
  });

  it("reads the list again while a session works, and never while none does", async () => {
    await shown("code");
    const lists = () => calls.get.filter((g) => g === "/work-record/other-tools").length;
    expect(lists()).toBe(1);
    await tick(WORKING_REREAD_MS + 10);
    expect(lists()).toBe(2);
    answer = { ...ALL, tools: [tool("codex", "Codex", [row("x1", "Tidy the tests")])] };
    await tick(WORKING_REREAD_MS + 10);
    expect(lists()).toBe(3);
    await tick(WORKING_REREAD_MS * 3);
    expect(lists()).toBe(3);
  });

  it("the chat list never reads again on its own", async () => {
    await shown("chat");
    await tick(WORKING_REREAD_MS * 3);
    expect(calls.get).toEqual(["/work-record/other-tools"]);
  });
});

describe("the watch view", () => {
  it("shows the newest steps, reads them again while it works, and stops once closed", async () => {
    await shown("code");
    fireEvent.click(screen.getByTestId("other-tool-claude-code"));
    fireEvent.click(screen.getByTestId("other-chat-live"));
    const watch = await screen.findByTestId("other-chat-watch-live");
    await waitFor(() => expect(watch).toHaveTextContent("Ran: pytest -q"));
    expect(watch).toHaveTextContent("You: speed it up");
    const reads = () => calls.get.filter((g) => g === "/work-record/other-tools/claude-code/live/steps").length;
    expect(reads()).toBe(1);
    await tick(WATCH_REREAD_MS + 10);
    expect(reads()).toBe(2);
    fireEvent.click(screen.getByTestId("other-chat-live"));
    expect(screen.queryByTestId("other-chat-watch-live")).toBeNull();
    await tick(WATCH_REREAD_MS * 3);
    expect(reads()).toBe(2);
  });

  it("a session written to a moment ago is continued only on the owner's word", async () => {
    await shown("code");
    fireEvent.click(screen.getByTestId("other-tool-claude-code"));
    fireEvent.click(screen.getByTestId("other-chat-live"));
    expect(await screen.findByTestId("other-chat-in-use-live")).toHaveTextContent("Claude Code wrote to it 12 s ago");
    expect(screen.queryByTestId("other-chat-continue-live")).toBeNull();
    fireEvent.click(screen.getByTestId("other-chat-closed-there-live"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/terminals?view=code&code=hs_made"));
    expect(calls.post).toEqual([{ path: "/build/app/sessions", body: {
      harness: "claude-code", folder: "C:\\Work\\Parser", harness_session_id: "live", elsewhere_closed: true } }]);
  });

  it("a session quiet for ten minutes or more is continued with one press, without the word", async () => {
    steps = { working: false, quiet_s: 7200, steps: [] };
    await shown("code");
    fireEvent.click(screen.getByTestId("other-tool-claude-code"));
    fireEvent.click(screen.getByTestId("other-chat-old"));
    fireEvent.click(await screen.findByTestId("other-chat-continue-old"));
    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(calls.post[0]?.body).toEqual({ harness: "claude-code", folder: "C:\\Work\\Parser", harness_session_id: "old" });
  });

  it("a session quiet for five minutes still needs the word (the daemon's ten-minute rule)", async () => {
    steps = { working: false, quiet_s: 300, steps: [] };
    await shown("code");
    fireEvent.click(screen.getByTestId("other-tool-codex"));
    fireEvent.click(screen.getByTestId("other-chat-x1"));
    expect(await screen.findByTestId("other-chat-in-use-x1")).toHaveTextContent("Codex wrote to it 5 min ago");
    expect(screen.queryByTestId("other-chat-continue-x1")).toBeNull();
  });

  it("the daemon's refusal is said under the row", async () => {
    postAnswer = () => {
      throw Object.assign(new Error("Claude Code wrote to this conversation 40 seconds ago, so it may still be running there."), { status: 409 });
    };
    await shown("code");
    fireEvent.click(screen.getByTestId("other-tool-claude-code"));
    fireEvent.click(screen.getByTestId("other-chat-live"));
    fireEvent.click(await screen.findByTestId("other-chat-closed-there-live"));
    await waitFor(() => expect(screen.getByTestId("other-chat-note")).toHaveTextContent("may still be running there"));
    expect(push).not.toHaveBeenCalled();
  });
});
