/**
 * v1.423.0 (Wave 3 lane B) — "Copy as a fix request": the problems Iron
 * Jarvis's coding sessions and model team ran into, copied as a report the
 * owner can paste to the coding agent that fixes Iron Jarvis
 * (components/team/FixRequestButton; the daemon's GET /team/fix-request).
 *
 * Pinned: a press reads the route (the last 7 days; one session's id when it
 * sits in that session's Session tab) and copies the TEXT through the app's
 * own copy path (the desktop bridge's `clipboardWriteText`, then the
 * browser's); the status line says "Copied: N problems from the last 7 days";
 * nothing to report copies NOTHING and says so; a clipboard that cannot be
 * reached is never reported as a copy (the report is shown to select by
 * hand); FastAPI's own 404 ("Not Found", an older Iron Jarvis) hides the
 * button while the route's own 404 is said in its words; any other failure in
 * words; and it is mounted in Settings › Model team and a coding session's
 * Session tab.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown>,
    calls: [] as string[],
    hooks: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) => {
    api.calls.push(p);
    if (!(p in api.gets)) return Promise.reject(new api.ApiError("Not Found", 404));
    const v = api.gets[p];
    return v instanceof api.ApiError ? Promise.reject(v) : Promise.resolve(v);
  },
  post: (p: string) => {
    if (p === "/chat/changes") return Promise.resolve({ changes: [] });
    return Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (api.hooks[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (api.hooks[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

import {
  CLIPBOARD_UNAVAILABLE,
  FIX_REQUEST_DAYS,
  FixRequestButton,
  copiedWords,
  fixRequestPath,
  fixRequestTitle,
  nothingWords,
} from "@/components/team/FixRequestButton";
import { ModelTeam } from "@/components/settings/ModelTeam";
import { SessionRail } from "@/components/harness/HarnessSessionRail";
import { decodeSessionView } from "@/lib/buildSessions";

const REPORT = "# Fix request: Iron Jarvis coding sessions and model team\n\n## 1. A command the shell could not find";
const ALL = "/team/fix-request?days=7";
const ONE = "/team/fix-request?days=7&session=chat_s1";

let written: string[] = [];

function bridge(on: boolean, fail = false) {
  const w = window as unknown as { ironjarvis?: unknown };
  if (!on) {
    delete w.ironjarvis;
    return;
  }
  w.ironjarvis = {
    clipboardWriteText: (text: string) => {
      if (fail) return Promise.reject(new Error("no clipboard"));
      written.push(text);
      return Promise.resolve();
    },
  };
}

beforeEach(() => {
  api.gets = {};
  api.calls = [];
  api.hooks = {};
  written = [];
  bridge(true);
  // jsdom has no clipboard: with the bridge gone, nothing can take the text.
  Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
});

afterEach(() => {
  cleanup();
  bridge(false);
});

async function press() {
  fireEvent.click(screen.getByTestId("fix-request-copy"));
  await waitFor(() => expect(screen.getByTestId("fix-request-copy").hasAttribute("disabled")).toBe(false));
}

describe("the words", () => {
  it("reads the last 7 days, and one session when it is given one", () => {
    expect(FIX_REQUEST_DAYS).toBe(7);
    expect(fixRequestPath()).toBe(ALL);
    expect(fixRequestPath("chat_s1")).toBe(ONE);
    expect(fixRequestPath("  ")).toBe(ALL);
  });

  it("says how many problems it copied, from which window", () => {
    expect(copiedWords(3)).toBe("Copied: 3 problems from the last 7 days");
    expect(copiedWords(1)).toBe("Copied: 1 problem from the last 7 days");
    expect(copiedWords(2, 1)).toBe("Copied: 2 problems from the last day");
    expect(nothingWords()).toBe("Nothing to report from the last 7 days");
  });

  it("its title says what it copies and to check it before pasting it anywhere", () => {
    const t = fixRequestTitle();
    expect(t).toContain("problems Iron Jarvis's coding sessions and model team ran into in the last 7 days");
    expect(t).toContain("Check it before you paste it anywhere");
    expect(fixRequestTitle("chat_s1")).toContain("in this coding session over the last 7 days");
  });
});

describe("the button", () => {
  it("copies the report through the desktop bridge and says how many problems", async () => {
    api.gets[ALL] = { text: REPORT, problems: 3, since: "a", until: "b", sessions: 2 };
    render(<FixRequestButton />);
    // Nothing is read until the press.
    expect(api.calls).toEqual([]);
    expect(screen.getByTestId("fix-request-copy").getAttribute("title")).toBe(fixRequestTitle());
    await press();
    expect(api.calls).toEqual([ALL]);
    expect(written).toEqual([REPORT]);
    await waitFor(() =>
      expect(screen.getByTestId("fix-request-status").textContent).toBe("Copied: 3 problems from the last 7 days"),
    );
    // The status line is a live region that was there before the words.
    expect(screen.getByTestId("fix-request-status").getAttribute("role")).toBe("status");
  });

  it("copies nothing when there is nothing to report, and says so", async () => {
    api.gets[ALL] = { text: "Nothing to report: Iron Jarvis ran into no problems …", problems: 0 };
    render(<FixRequestButton />);
    await press();
    expect(written).toEqual([]);
    await waitFor(() =>
      expect(screen.getByTestId("fix-request-status").textContent).toBe("Nothing to report from the last 7 days"),
    );
  });

  it("never reports a copy that did not happen: the report is shown to copy by hand", async () => {
    api.gets[ALL] = { text: REPORT, problems: 2 };
    bridge(true, true);
    render(<FixRequestButton />);
    await press();
    await waitFor(() => expect(screen.getByTestId("fix-request-status").textContent).toBe(CLIPBOARD_UNAVAILABLE));
    expect((screen.getByTestId("fix-request-text") as HTMLTextAreaElement).value).toBe(REPORT);
    expect(screen.getByTestId("fix-request-status").textContent).not.toContain("Copied");
  });

  it("hides itself on an older Iron Jarvis (FastAPI's own 404)", async () => {
    render(<FixRequestButton />);
    fireEvent.click(screen.getByTestId("fix-request-copy"));
    await waitFor(() => expect(screen.queryByTestId("fix-request")).toBeNull());
    expect(written).toEqual([]);
  });

  it("says the route's own 404 (a session that is gone) in its words", async () => {
    api.gets[ONE] = new api.ApiError("That coding session was not found. It may have been deleted.", 404);
    render(<FixRequestButton session="chat_s1" />);
    await press();
    await waitFor(() =>
      expect(screen.getByTestId("fix-request-status").textContent).toBe(
        "The report could not be made: That coding session was not found. It may have been deleted.",
      ),
    );
    expect(screen.getByTestId("fix-request")).toBeTruthy();
  });

  it("says any other failure in words", async () => {
    api.gets[ALL] = new api.ApiError("Iron Jarvis isn't responding", 0);
    render(<FixRequestButton />);
    await press();
    await waitFor(() =>
      expect(screen.getByTestId("fix-request-status").textContent).toBe(
        "The report could not be made: Iron Jarvis isn't responding",
      ),
    );
    expect(written).toEqual([]);
  });
});

describe("where it is mounted", () => {
  it("Settings › Model team: the last 7 days, every coding session", async () => {
    api.hooks["/team"] = { roles: [], members: [] };
    api.gets[ALL] = { text: REPORT, problems: 1 };
    render(<ModelTeam />);
    const team = screen.getByTestId("model-team");
    expect(team.querySelector('[data-testid="fix-request-copy"]')).not.toBeNull();
    await press();
    expect(api.calls).toContain(ALL);
    await waitFor(() => expect(written).toEqual([REPORT]));
  });

  it("a coding session's Session tab: that session only", async () => {
    const view = decodeSessionView({
      id: "chat_s1",
      title: "Cart",
      folder: "C:\\work\\shop",
      folder_name: "shop",
      session_id: "build:chat_s1",
      messages: [{ role: "user", content: "Add a cart", at: "2026-10-22T10:00:00" }],
    });
    if (!view) throw new Error("bad fixture");
    api.gets["/build/sessions/chat_s1/programs"] = { programs: [] };
    api.gets[ONE] = { text: REPORT, problems: 4 };
    render(
      <SessionRail view={view} messages={view.messages} modelWords="Opus 5.5" refreshKey={0} onOpenChanges={() => {}} />,
    );
    const rail = screen.getByTestId("pane-session");
    expect(rail.querySelector('[data-testid="fix-request-copy"]')).not.toBeNull();
    await press();
    expect(api.calls).toContain(ONE);
    expect(api.calls).not.toContain(ALL);
    await waitFor(() =>
      expect(screen.getByTestId("fix-request-status").textContent).toBe("Copied: 4 problems from the last 7 days"),
    );
  });
});
