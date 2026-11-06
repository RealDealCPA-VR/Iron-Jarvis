/**
 * v1.441.0 (review A5) — a coding tool that says it takes no Iron Jarvis
 * tools (Antigravity 1.5.2: no `mcpCapabilities`) shows no "Iron Jarvis
 * tools" chip: the daemon's view says `jarvis_tools: false`. A session whose
 * view does not say so keeps the chip (and an older daemon never says).
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
import { __resetLooking } from "@/lib/buildWatching";
import { decodeCodeSession } from "@/lib/buildApp";

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

function setUp(extra: Record<string, unknown>) {
  const session = { ...SESSION, ...extra };
  H.gets = {
    "/build/app/harnesses": TOOLS,
    "/build/app/sessions": { sessions: [session] },
    "/editor/options": { editors: [], preferred: "" },
    "/build/app/sessions/app_1": { session, events: [], running: null, pending: [] },
    "/profile/share": { clis: [], limit: 4000 },
  };
}

beforeEach(() => {
  window.localStorage.clear();
  __resetLooking();
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("the Iron Jarvis tools chip", () => {
  it("is hidden for a tool that takes no Iron Jarvis tools", async () => {
    setUp({ jarvis_tools: false });
    render(<BuildCodeView initialSession="app_1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session-title");
    expect(screen.queryByTestId("code-jarvis-tools")).toBeNull();
  });

  it("stays for a session whose view does not say so", async () => {
    setUp({});
    render(<BuildCodeView initialSession="app_1" onOpenedTerminal={() => {}} />);
    expect((await screen.findByTestId("code-jarvis-tools")).textContent).toBe("Iron Jarvis tools: off");
  });

  it("decodes only an explicit false", () => {
    expect(decodeCodeSession({ ...SESSION, jarvis_tools: false })?.jarvisToolsTaken).toBe(false);
    expect(decodeCodeSession({ ...SESSION })).not.toHaveProperty("jarvisToolsTaken");
    expect(decodeCodeSession({ ...SESSION, jarvis_tools: true })).not.toHaveProperty("jarvisToolsTaken");
  });
});
