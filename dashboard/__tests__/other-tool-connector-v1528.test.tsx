import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * v1.528.0 — Claude Chat and the connector in the chat list's "Other tools".
 *
 *  - Claude Chat's chats (reported by Claude, and Cowork sessions) are not
 *    opened: a press shows Claude's summary and a web link back;
 *  - an app on this PC that is not connected is offered as "Connect …"; the
 *    words say what Connect does BEFORE the press that does it; nothing is
 *    sent until that second press;
 *  - a connected app with no chats yet still has its line, saying it waits;
 *  - Disconnect also says what it does first, and a refusal is said in the
 *    daemon's own words;
 *  - an app that is not on this PC is never offered.
 */

const calls: { get: string[]; post: string[] } = { get: [], post: [] };
let answer: unknown = {};
let postAnswer: (path: string) => unknown = () => ({});

vi.mock("@/lib/api", () => ({
  get: async (path: string) => {
    calls.get.push(path);
    return typeof answer === "function" ? (answer as () => unknown)() : answer;
  },
  post: async (path: string) => {
    calls.post.push(path);
    return postAnswer(path);
  },
}));

import OtherToolChats from "@/components/chat/OtherToolChats";
import { CONNECT_WORDS, FIRST_READ_MS, FOLDED_KEY, decodeOtherApps, decodeOtherTools } from "@/lib/otherTools";

const AT = "2026-11-16T10:00:00+00:00";

function app(over: Record<string, unknown>) {
  return { app: "claude-desktop", label: "Claude desktop", installed: true, connected: false, entry_missing: false, count: 0, last: "", ...over };
}

const CLAUDE_CHAT = {
  tool: "claude-chat",
  label: "Claude Chat",
  total: 2,
  openable: false,
  folders: [
    {
      folder: "C:\\Work\\Bosch",
      name: "Bosch",
      chats: [
        { id: "r:1", title: "Client tax planning questions", folder: "C:\\Work\\Bosch", started: AT, ended: AT,
          summary: "Drafted the reply.\nOpen: depreciation", link: "https://claude.ai/chat/abc" },
      ],
    },
    { folder: "", name: "No folder", chats: [{ id: "c:9", title: "Organise receipts", folder: "", started: AT, ended: AT, summary: "", link: "" }] },
  ],
};

async function shown() {
  render(<OtherToolChats go={() => {}} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(FIRST_READ_MS + 10);
  });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  calls.get = [];
  calls.post = [];
  postAnswer = () => ({});
  window.localStorage.clear();
  // v1.537.0: the chat list's part starts folded; these pins are about what
  // it shows once the owner has opened it (sidebar-folds-v1537 pins the fold).
  window.localStorage.setItem(FOLDED_KEY, JSON.stringify({ chat: false }));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the answer", () => {
  it("keeps a folderless Claude Chat group, and draws only a web link", () => {
    const got = decodeOtherTools({
      tools: [{ ...CLAUDE_CHAT, folders: [...CLAUDE_CHAT.folders, { folder: "", name: "", chats: [{ id: "r:2", title: "x", link: "javascript:alert(1)" }] }] }],
    });
    expect(got[0]?.openable).toBe(false);
    expect(got[0]?.folders.map((f) => f.name)).toEqual(["Bosch", "No folder", "No folder"]);
    expect(got[0]?.folders[2]?.chats[0]?.link).toBe("");
    // A coding tool's folderless group is not a group (it could not be opened).
    expect(decodeOtherTools({ tools: [{ tool: "codex", folders: [{ folder: "", chats: [{ id: "1", title: "t" }] }] }] })).toEqual([]);
  });

  it("reads which apps can be connected, and nothing it does not know", () => {
    expect(decodeOtherApps({ apps: [app({}), { app: "someone-else", installed: true }, "x"] })).toEqual([
      { app: "claude-desktop", label: "Claude desktop", installed: true, connected: false, entryMissing: false, count: 0, last: "" },
    ]);
  });
});

describe("Claude Chat rows", () => {
  it("show Claude's summary and a link back instead of opening anything", async () => {
    answer = { tools: [CLAUDE_CHAT], apps: [app({ connected: true, count: 1 })] };
    await shown();
    fireEvent.click(screen.getByTestId("other-tool-claude-chat"));
    expect(screen.getByTestId("other-tool-list-claude-chat")).toHaveTextContent("No folder");
    fireEvent.click(screen.getByTestId("other-chat-r:1"));
    const detail = screen.getByTestId("other-chat-detail-r:1");
    expect(detail).toHaveTextContent("Drafted the reply.");
    expect(detail).toHaveTextContent("Carry it on in Claude desktop.");
    expect(screen.getByTestId("other-chat-link-r:1")).toHaveAttribute("href", "https://claude.ai/chat/abc");
    expect(calls.post).toEqual([]);
    fireEvent.click(screen.getByTestId("other-chat-c:9"));
    expect(screen.getByTestId("other-chat-detail-c:9")).toHaveTextContent("A Cowork session in Claude desktop.");
    expect(screen.getByTestId("other-app-connected-claude-desktop")).toHaveTextContent("Connected · 1 recorded");
  });
});

describe("connecting", () => {
  it("offers an app on this PC, says what Connect does first, and sends only on the second press", async () => {
    let connected = false;
    answer = () => ({ tools: [], apps: [app({ connected }), app({ app: "codex", label: "Codex", installed: false })] });
    postAnswer = () => {
      connected = true;
      return { ...app({ connected: true }), words: "Claude desktop is connected. Restart Claude desktop to start using it." };
    };
    await shown();
    expect(screen.queryByTestId("other-app-connect-codex")).toBeNull(); // not on this PC
    fireEvent.click(screen.getByTestId("other-app-connect-claude-desktop"));
    expect(screen.getByTestId("other-app-confirm-claude-desktop")).toHaveTextContent(CONNECT_WORDS["claude-desktop"]!);
    expect(calls.post).toEqual([]);
    fireEvent.click(screen.getByTestId("other-app-connect-yes-claude-desktop"));
    await waitFor(() => expect(calls.post).toEqual(["/work-record/other-apps/claude-desktop/connect"]));
    await waitFor(() => expect(screen.getByTestId("other-tool-claude-chat")).toBeInTheDocument());
    expect(screen.queryByTestId("other-app-connect-claude-desktop")).toBeNull();
    fireEvent.click(screen.getByTestId("other-tool-claude-chat"));
    expect(screen.getByTestId("other-tool-waiting-claude-chat")).toHaveTextContent("Restart Claude desktop");
  });

  it("Not now sends nothing", async () => {
    answer = { tools: [], apps: [app({})] };
    await shown();
    fireEvent.click(screen.getByTestId("other-app-connect-claude-desktop"));
    fireEvent.click(screen.getByTestId("other-app-cancel-claude-desktop"));
    expect(screen.queryByTestId("other-app-confirm-claude-desktop")).toBeNull();
    expect(calls.post).toEqual([]);
  });

  it("a refusal is said in the daemon's words and changes nothing on screen", async () => {
    answer = { tools: [], apps: [app({})] };
    postAnswer = () => {
      throw Object.assign(new Error("Claude desktop already has a tool named \"iron-jarvis\" that Iron Jarvis did not add, so it was left as it is."), { status: 409 });
    };
    await shown();
    fireEvent.click(screen.getByTestId("other-app-connect-claude-desktop"));
    fireEvent.click(screen.getByTestId("other-app-connect-yes-claude-desktop"));
    await waitFor(() =>
      expect(screen.getByTestId("other-app-note-claude-desktop")).toHaveTextContent("did not add, so it was left as it is"),
    );
    expect(screen.getByTestId("other-app-connect-claude-desktop")).toBeInTheDocument();
  });

  it("Disconnect says what it does first, then sends", async () => {
    answer = { tools: [CLAUDE_CHAT], apps: [app({ connected: true, count: 1 })] };
    postAnswer = () => ({ ...app({}), words: "Claude desktop is no longer connected. What it recorded stays." });
    await shown();
    fireEvent.click(screen.getByTestId("other-tool-claude-chat"));
    fireEvent.click(screen.getByTestId("other-app-disconnect-claude-desktop"));
    expect(screen.getByTestId("other-app-confirm-claude-desktop")).toHaveTextContent("What it recorded stays.");
    expect(calls.post).toEqual([]);
    fireEvent.click(screen.getByTestId("other-app-disconnect-yes-claude-desktop"));
    await waitFor(() => expect(calls.post).toEqual(["/work-record/other-apps/claude-desktop/disconnect"]));
  });

  it("an app whose own list lost the entry is offered again", async () => {
    answer = { tools: [], apps: [app({ connected: false, entry_missing: true })] };
    await shown();
    expect(screen.getByTestId("other-app-connect-claude-desktop")).toHaveTextContent("Connect Claude desktop again");
  });

  it("draws nothing when there are no chats and no app to connect", async () => {
    answer = { tools: [], apps: [app({ installed: false }), app({ app: "codex", label: "Codex", installed: false })] };
    await shown();
    expect(screen.queryByTestId("other-tools")).toBeNull();
  });
});
