import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * v1.538.0 — fourth review, group 29 (OT4, MS2, MS5, C-OT-2): what a connected
 * app's row in "Other tools" says, from the daemon's own status fields.
 *
 *  - waiting_for_app: "Connected · <app> has not used it yet. Restart <app>
 *    if it was open." (until the app's first call);
 *  - entry_stale: "Connected, but <app> points at an older Iron Jarvis. Press
 *    Connect again." with the Connect press;
 *  - entry_left: "<app> still lists Iron Jarvis in its settings." with a
 *    Remove press that needs a second press and goes through the existing
 *    disconnect door; a refusal (409) is shown in the daemon's own words;
 *  - after Connect, the daemon's own sentence is shown as it is.
 */

const calls: { post: string[] } = { post: [] };
let answer: unknown = {};
let postAnswer: (path: string) => unknown = () => ({});

vi.mock("@/lib/api", () => ({
  get: async () => (typeof answer === "function" ? (answer as () => unknown)() : answer),
  post: async (path: string) => {
    calls.post.push(path);
    return postAnswer(path);
  },
}));

import OtherToolChats from "@/components/chat/OtherToolChats";
import { FIRST_READ_MS, FOLDED_KEY, decodeOtherApps, type OtherToolsMode } from "@/lib/otherTools";

function app(over: Record<string, unknown>) {
  return {
    app: "claude-desktop", label: "Claude desktop", installed: true, connected: false, entry_missing: false,
    entry_left: false, entry_stale: false, waiting_for_app: false, count: 0, last: "", ...over,
  };
}

async function shown(mode: OtherToolsMode = "chat") {
  render(<OtherToolChats go={() => {}} mode={mode} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(FIRST_READ_MS + 10);
  });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  calls.post = [];
  postAnswer = () => ({});
  window.localStorage.clear();
  // The part open in both places, whatever each place starts as.
  window.localStorage.setItem(FOLDED_KEY, JSON.stringify({ chat: false, code: false }));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the answer", () => {
  it("reads the three new fields, and an older daemon's row (none of them) as false", () => {
    const [older] = decodeOtherApps({ apps: [{ app: "codex", label: "Codex", installed: true, connected: true }] });
    expect(older).toMatchObject({ entryLeft: false, entryStale: false, waitingForApp: false });
    const [now] = decodeOtherApps({ apps: [app({ entry_left: true, entry_stale: true, waiting_for_app: true })] });
    expect(now).toMatchObject({ entryLeft: true, entryStale: true, waitingForApp: true });
  });
});

describe("a connected app the app has not used yet", () => {
  it("says so, naming the app, until its first call", async () => {
    answer = { tools: [], apps: [app({ connected: true, waiting_for_app: true })] };
    await shown();
    fireEvent.click(screen.getByTestId("other-tool-claude-chat"));
    expect(screen.getByTestId("other-app-connected-claude-desktop").textContent).toBe(
      "Connected · Claude desktop has not used it yet. Restart Claude desktop if it was open.",
    );
  });

  it("names Claude Code in Chat › Code", async () => {
    answer = { tools: [], apps: [app({ app: "claude-code", label: "Claude Code", connected: true, waiting_for_app: true })] };
    await shown("code");
    fireEvent.click(screen.getByTestId("other-tool-claude-code"));
    expect(screen.getByTestId("other-app-connected-claude-code").textContent).toBe(
      "Connected · Claude Code has not used it yet. Restart Claude Code if it was open.",
    );
  });

  it("once used, it is plain Connected again", async () => {
    answer = { tools: [], apps: [app({ connected: true, count: 2 })] };
    await shown();
    fireEvent.click(screen.getByTestId("other-tool-claude-chat"));
    expect(screen.getByTestId("other-app-connected-claude-desktop").textContent).toBe("Connected · 2 recorded");
  });
});

describe("an entry that names an older Iron Jarvis", () => {
  it("says so and offers Connect, which sends only on the second press and shows the daemon's sentence", async () => {
    answer = { tools: [], apps: [app({ connected: true, entry_stale: true })] };
    const said = "Claude desktop is connected. Restart Claude desktop to start using it.";
    postAnswer = () => ({ ...app({ connected: true }), words: said });
    await shown();
    expect(screen.getByTestId("other-app-stale-claude-desktop").textContent).toBe(
      "Connected, but Claude desktop points at an older Iron Jarvis. Press Connect again.",
    );
    fireEvent.click(screen.getByTestId("other-app-connect-claude-desktop"));
    expect(screen.getByTestId("other-app-connect-claude-desktop")).toHaveTextContent("Connect Claude desktop again");
    expect(calls.post).toEqual([]);
    fireEvent.click(screen.getByTestId("other-app-connect-yes-claude-desktop"));
    await waitFor(() => expect(calls.post).toEqual(["/work-record/other-apps/claude-desktop/connect"]));
    await waitFor(() => expect(screen.getByTestId("other-app-note-claude-desktop").textContent).toBe(said));
  });

  it("the open tool's own line says it too", async () => {
    answer = { tools: [], apps: [app({ connected: true, entry_stale: true, waiting_for_app: true })] };
    await shown();
    fireEvent.click(screen.getByTestId("other-tool-claude-chat"));
    expect(screen.getByTestId("other-app-connected-claude-desktop").textContent).toBe(
      "Connected, but Claude desktop points at an older Iron Jarvis. Press Connect again.",
    );
    // Said once on screen, not twice.
    expect(screen.queryByTestId("other-app-stale-claude-desktop")).toBeNull();
  });
});

describe("an entry left in the app's own list", () => {
  it("has its own line, and Remove sends only on the second press, through the disconnect door", async () => {
    let left = true;
    answer = () => ({ tools: [], apps: [app({ entry_left: left })] });
    postAnswer = () => {
      left = false;
      return { ...app({}), words: "Claude desktop is no longer connected. What it recorded stays." };
    };
    await shown();
    expect(screen.getByTestId("other-app-left-claude-desktop")).toHaveTextContent(
      "Claude desktop still lists Iron Jarvis in its settings.",
    );
    const remove = screen.getByTestId("other-app-remove-claude-desktop");
    expect(remove.textContent).toBe("Remove");
    fireEvent.click(remove);
    expect(screen.getByTestId("other-app-remove-claude-desktop").textContent).toBe("Remove for good? Press again");
    expect(calls.post).toEqual([]);
    fireEvent.click(screen.getByTestId("other-app-remove-claude-desktop"));
    await waitFor(() => expect(calls.post).toEqual(["/work-record/other-apps/claude-desktop/disconnect"]));
    await waitFor(() => expect(screen.queryByTestId("other-app-left-claude-desktop")).toBeNull());
    expect(screen.getByTestId("other-app-note-claude-desktop").textContent).toBe(
      "Claude desktop is no longer connected. What it recorded stays.",
    );
  });

  it("a refusal is shown in the daemon's own words, as they are", async () => {
    const refusal =
      "Claude desktop can no longer reach Iron Jarvis, but its settings still list it (the file is in use). Close Claude desktop, then press Remove.";
    answer = { tools: [], apps: [app({ entry_left: true })] };
    postAnswer = () => {
      throw Object.assign(new Error(refusal), { status: 409 });
    };
    await shown();
    fireEvent.click(screen.getByTestId("other-app-remove-claude-desktop"));
    fireEvent.click(screen.getByTestId("other-app-remove-claude-desktop"));
    await waitFor(() => expect(screen.getByTestId("other-app-note-claude-desktop").textContent).toBe(refusal));
    expect(screen.getByTestId("other-app-left-claude-desktop")).toBeInTheDocument();
  });

  it("is not drawn for an app that is not on this PC", async () => {
    answer = {
      tools: [],
      apps: [
        app({ app: "claude-code", label: "Claude Code", entry_left: true }),
        app({ app: "codex", label: "Codex", installed: false, entry_left: true }),
      ],
    };
    await shown("code");
    expect(screen.getByTestId("other-app-left-claude-code")).toHaveTextContent("Claude Code still lists Iron Jarvis in its settings.");
    expect(screen.queryByTestId("other-app-left-codex")).toBeNull();
  });
});
