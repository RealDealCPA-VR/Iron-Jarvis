/**
 * v1.414.0 — the Build › Code session screen, driven through the real view:
 * the session being on screen (window focused) is LOOKED AT (the bell then
 * stays quiet when its long reply ends — lib/buildWatching, the same note
 * Chat › Code writes), the header carries the "Iron Jarvis tools" chip, and a
 * new session offers to share the profile with its tool once.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const H = vi.hoisted(() => ({ gets: {} as Record<string, unknown> }));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError: FakeApiError,
    get: (path: string) => {
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(r);
    },
    post: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* () {},
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { __resetLooking, lookingAt } from "@/lib/buildWatching";

const TOOLS = {
  harnesses: [
    {
      id: "pi",
      label: "Pi",
      installed: true,
      signed_in: null,
      own_sign_in: true,
      modes: [{ id: "own", label: "Pi's own rules", dangerous: false }],
      hidden_modes: [],
      models: [{ id: "", label: "Pi's default" }],
      efforts: [],
      accounts: [],
      commands: [],
    },
  ],
};

const SESSION = {
  id: "app_1",
  harness: "pi",
  folder: "C:\\work\\shop",
  title: "New Pi session",
  account: "",
  model: "",
  effort: "",
  mode: "own",
  harness_session_id: "",
  created_at: "2026-10-30T09:00:00Z",
  updated_at: "2026-10-30T09:00:00Z",
  archived_at: null,
  last_error: "",
  capabilities: { files: false, shell: false, browser: false, extensions: false, memory: false },
};

beforeEach(() => {
  window.localStorage.clear();
  __resetLooking();
  H.gets = {
    "/build/app/harnesses": TOOLS,
    "/build/app/sessions": { sessions: [SESSION] },
    "/editor/options": { editors: [], preferred: "" },
    "/build/app/sessions/app_1": { session: SESSION, events: [], running: null, pending: [] },
    "/profile/share": {
      clis: [{ cli: "pi", label: "Pi", vendor: "the model provider you chose in Pi", available: true, on: false, file_name: "AGENTS.md", files: [], targets: [] }],
      limit: 4000,
    },
  };
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("the Build › Code session screen", () => {
  it("is LOOKED AT while it is on screen and the window has focus, and not after", async () => {
    const view = render(<BuildCodeView initialSession="app_1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    expect(lookingAt("app_1")).toBe(true);
    expect(lookingAt("app_2")).toBe(false);
    view.unmount();
    expect(lookingAt("app_1")).toBe(false);
  });

  it("carries the Iron Jarvis tools chip (off) and offers the profile share once", async () => {
    render(<BuildCodeView initialSession="app_1" onOpenedTerminal={() => {}} />);
    const chip = await screen.findByTestId("code-jarvis-tools");
    expect(chip.textContent).toBe("Iron Jarvis tools: off");
    const offer = await screen.findByTestId("code-share-offer");
    expect(offer.textContent).toContain("Share your profile with Pi?");
  });
});
