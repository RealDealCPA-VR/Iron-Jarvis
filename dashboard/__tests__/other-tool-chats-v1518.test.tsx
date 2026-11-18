import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * v1.518.0 — "Other tools": the owner's chats in Claude Code and Codex at the
 * foot of the chat list.
 *
 *  - no such chats (or a list that could not be read): nothing is drawn;
 *  - closed it is one line per tool, with "N new" only for chats written
 *    since the owner last looked, and never on the first sight of a tool;
 *  - open it lists chats by folder, five and then "Show more";
 *  - a press opens the chat as a coding session on that tool and goes there;
 *    a refusal is said under the row and nothing navigates;
 *  - the first read waits (it is not part of a page's first paint).
 */

const calls: { get: string[]; post: { path: string; body: unknown }[] } = { get: [], post: [] };
let answer: unknown = {};
let postAnswer: () => unknown = () => ({ id: "hs_made" });
const push = vi.fn();

vi.mock("@/lib/api", () => ({
  get: async (path: string) => {
    calls.get.push(path);
    if (answer instanceof Error) throw answer;
    return answer;
  },
  post: async (path: string, body: unknown) => {
    calls.post.push({ path, body });
    return postAnswer();
  },
}));

import OtherToolChats, { OPEN_FAILED, codeSessionHref } from "@/components/chat/OtherToolChats";
import { buildCodeHref } from "@/lib/buildApp";
import {
  FIRST_READ_MS,
  SEEN_KEY,
  decodeOtherTools,
  newCount,
  withFirstSight,
  withoutChat,
  type OtherTool,
} from "@/lib/otherTools";

function chat(id: string, title: string, day: number) {
  const at = `2026-11-${String(day).padStart(2, "0")}T10:00:00+00:00`;
  return { id, title, started: at, ended: at };
}

const LIST = {
  tools: [
    {
      tool: "claude-code",
      label: "Claude Code",
      total: 7,
      folders: [
        {
          folder: "C:\\Work\\Hartleys 2026",
          name: "Hartleys 2026",
          chats: [7, 6, 5, 4, 3, 2].map((d) => chat(`c${d}`, `Claude chat ${d}`, d + 5)),
        },
        { folder: "C:\\Work\\Other", name: "Other", chats: [chat("c1", "Sort the receipts", 2)] },
      ],
    },
    {
      tool: "codex",
      label: "Codex",
      total: 1,
      folders: [{ folder: "C:\\Work\\Parser", name: "Parser", chats: [chat("x1", "Fix the import parser", 9)] }],
    },
  ],
  hidden: 3,
  whole: true,
};

async function shown() {
  render(<OtherToolChats mode="code" go={push} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(FIRST_READ_MS + 10);
  });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  calls.get = [];
  calls.post = [];
  answer = LIST;
  postAnswer = () => ({ id: "hs_made" });
  push.mockReset();
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the list's data", () => {
  it("keeps only rows that are rows, and drops a tool or folder left empty", () => {
    expect(decodeOtherTools({})).toEqual([]);
    expect(decodeOtherTools(null)).toEqual([]);
    const got = decodeOtherTools({
      tools: [
        { tool: "codex", label: "Codex", folders: [{ folder: "C:\\A", name: "A", chats: [{ id: "", title: "x" }] }] },
        { tool: "claude-code", folders: [{ folder: "C:\\B", chats: [{ id: "1", title: "Kept" }, { id: "2" }, "no"] }] },
      ],
    });
    expect(got).toEqual([
      {
        tool: "claude-code",
        label: "claude-code",
        total: 1,
        openable: true,
        folders: [
          {
            folder: "C:\\B",
            name: "C:\\B",
            chats: [{ id: "1", title: "Kept", folder: "C:\\B", started: "", ended: "", summary: "", link: "", working: false, quietS: -1, doing: "" }],
          },
        ],
      },
    ]);
  });

  it("counts as new only what was written after the tool was last looked at", () => {
    const tools = decodeOtherTools(LIST);
    const claude = tools[0] as OtherTool;
    expect(newCount(claude, {})).toBe(0);
    expect(newCount(claude, { "claude-code": "2026-11-10T10:00:00+00:00" })).toBe(2);
    const first = withFirstSight(tools, {});
    expect(first).toEqual({ "claude-code": "2026-11-12T10:00:00+00:00", codex: "2026-11-09T10:00:00+00:00" });
    expect(withFirstSight(tools, first)).toBe(first);
  });

  it("takes an opened chat out, and its folder and tool when they are left empty", () => {
    const tools = decodeOtherTools(LIST);
    expect(withoutChat(tools, "codex", "x1").map((t) => t.tool)).toEqual(["claude-code"]);
    const less = withoutChat(tools, "claude-code", "c1");
    expect(less[0]?.folders.map((f) => f.name)).toEqual(["Hartleys 2026"]);
    expect(less[0]?.total).toBe(6);
  });

  it("opens a session where Build › Code's own links do", () => {
    expect(codeSessionHref("hs 1")).toBe(buildCodeHref("hs 1"));
  });
});

describe("the row in the sidebar", () => {
  it("is not read with the page, and is one closed line per tool once read", async () => {
    render(<OtherToolChats mode="code" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(FIRST_READ_MS - 100);
    });
    expect(calls.get).toEqual([]);
    expect(screen.queryByTestId("other-tools")).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(110);
    });
    expect(calls.get).toEqual(["/work-record/other-tools"]);
    expect(screen.getByTestId("other-tool-claude-code")).toHaveTextContent("Claude Code");
    expect(screen.getByTestId("other-tool-codex")).toHaveTextContent("Codex");
    expect(screen.getByTestId("other-tool-claude-code")).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("other-tool-list-claude-code")).toBeNull();
    // First sight: nothing is "new".
    expect(screen.queryByTestId("other-tool-new-claude-code")).toBeNull();
  });

  it.each([
    ["no chats", { tools: [], hidden: 0, whole: true }],
    ["an answer that is not a list", {}],
    ["a read that failed", new Error("offline")],
  ])("draws nothing for %s", async (_name, got) => {
    answer = got;
    await shown();
    expect(calls.get).toEqual(["/work-record/other-tools"]);
    expect(screen.queryByTestId("other-tools")).toBeNull();
  });

  it("says how many are new since the last look, and stops saying it once opened", async () => {
    window.localStorage.setItem(SEEN_KEY, JSON.stringify({ "claude-code": "2026-11-10T10:00:00+00:00" }));
    await shown();
    expect(screen.getByTestId("other-tool-new-claude-code")).toHaveTextContent("2 new");
    expect(screen.queryByTestId("other-tool-new-codex")).toBeNull();
    fireEvent.click(screen.getByTestId("other-tool-claude-code"));
    expect(screen.queryByTestId("other-tool-new-claude-code")).toBeNull();
    fireEvent.click(screen.getByTestId("other-tool-claude-code"));
    expect(screen.queryByTestId("other-tool-new-claude-code")).toBeNull();
    expect(JSON.parse(window.localStorage.getItem(SEEN_KEY) || "{}")["claude-code"]).toBe("2026-11-12T10:00:00+00:00");
  });

  it("opens to chats by folder, five and then Show more; one tool at a time", async () => {
    await shown();
    fireEvent.click(screen.getByTestId("other-tool-claude-code"));
    const list = screen.getByTestId("other-tool-list-claude-code");
    expect(list).toHaveTextContent("Hartleys 2026");
    expect(list).toHaveTextContent("Other");
    expect(screen.getByTestId("other-chat-c7")).toHaveTextContent("Claude chat 7");
    expect(screen.getByTestId("other-chat-c3")).toBeInTheDocument();
    expect(screen.queryByTestId("other-chat-c2")).toBeNull();
    fireEvent.click(screen.getByTestId("other-folder-more"));
    expect(screen.getByTestId("other-chat-c2")).toBeInTheDocument();
    expect(screen.queryByTestId("other-folder-more")).toBeNull();
    fireEvent.click(screen.getByTestId("other-tool-codex"));
    expect(screen.queryByTestId("other-tool-list-claude-code")).toBeNull();
    expect(screen.getByTestId("other-chat-x1")).toHaveTextContent("Fix the import parser");
  });

  it("a press opens the chat as a coding session on its tool and goes there", async () => {
    const onNavigate = vi.fn();
    render(<OtherToolChats mode="code" onNavigate={onNavigate} go={push} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(FIRST_READ_MS + 10);
    });
    fireEvent.click(screen.getByTestId("other-tool-codex"));
    fireEvent.click(screen.getByTestId("other-chat-x1"));
    fireEvent.click(await screen.findByTestId("other-chat-continue-x1"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/terminals?view=code&code=hs_made"));
    expect(calls.post).toEqual([
      { path: "/build/app/sessions", body: { harness: "codex", folder: "C:\\Work\\Parser", harness_session_id: "x1" } },
    ]);
    // The watch view read its steps (the one GET besides the list).
    expect(calls.get.filter((g) => g.endsWith("/steps"))).toEqual(["/work-record/other-tools/codex/x1/steps"]);
    expect(onNavigate).toHaveBeenCalledTimes(1);
    // It is a coding session now: it left this list, and Codex with it.
    expect(screen.queryByTestId("other-tool-codex")).toBeNull();
    expect(screen.getByTestId("other-tool-claude-code")).toBeInTheDocument();
  });

  it("a chat that ran in a copy of the project is grouped under the project and opened in its copy", async () => {
    const copy = "C:\\Work\\Parser\\.claude\\worktrees\\calm-otter-1a2b3c";
    answer = {
      tools: [
        {
          tool: "claude-code",
          label: "Claude Code",
          folders: [
            {
              folder: "C:\\Work\\Parser",
              name: "Parser",
              chats: [{ ...chat("w1", "Try the parser in a copy", 9), folder: copy }, chat("w2", "Fix it for real", 8)],
            },
          ],
        },
      ],
    };
    await shown();
    fireEvent.click(screen.getByTestId("other-tool-claude-code"));
    expect(screen.getByTestId("other-tool-list-claude-code")).toHaveTextContent("Parser");
    fireEvent.click(screen.getByTestId("other-chat-w1"));
    fireEvent.click(await screen.findByTestId("other-chat-continue-w1"));
    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(calls.post[0]?.body).toEqual({ harness: "claude-code", folder: copy, harness_session_id: "w1" });
  });

  it("a refusal is said under the row, in the daemon's words, and nothing navigates", async () => {
    postAnswer = () => {
      throw Object.assign(new Error("Codex has no saved conversation with that id in this folder."), { status: 404 });
    };
    await shown();
    fireEvent.click(screen.getByTestId("other-tool-codex"));
    fireEvent.click(screen.getByTestId("other-chat-x1"));
    fireEvent.click(await screen.findByTestId("other-chat-continue-x1"));
    await waitFor(() =>
      expect(screen.getByTestId("other-chat-note")).toHaveTextContent(
        "Codex has no saved conversation with that id in this folder.",
      ),
    );
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByTestId("other-chat-x1")).not.toBeDisabled();
  });

  it("an answer with no session in it is a failure, not a blank page", async () => {
    postAnswer = () => ({});
    await shown();
    fireEvent.click(screen.getByTestId("other-tool-codex"));
    fireEvent.click(screen.getByTestId("other-chat-x1"));
    fireEvent.click(await screen.findByTestId("other-chat-continue-x1"));
    await waitFor(() => expect(screen.getByTestId("other-chat-note")).toHaveTextContent(OPEN_FAILED));
    expect(push).not.toHaveBeenCalled();
  });
});
