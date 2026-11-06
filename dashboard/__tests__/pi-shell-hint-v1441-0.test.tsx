/**
 * v1.441.0 lane g4 review — a Pi session runs a message that starts with `!`
 * as a command in Pi's own shell (as Pi's terminal does, where the box turns
 * another colour). The Code view says so in ONE quiet line while the words
 * would run, so "!important: …" is never run as a command unseen.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  patches: [] as { path: string; body: unknown }[],
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: FakeApiError,
    get: (path: string) => {
      const key = path.replace(/\?limit=1$/, "");
      const r = H.gets[key];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(r);
    },
    post: () => Promise.resolve({}),
    patch: (path: string, body: unknown) => {
      H.patches.push({ path, body });
      return Promise.resolve({});
    },
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* () {},
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => null,
}));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { LAST_TOOL_KEY } from "@/components/buildCode/StartComposer";
import { PI_SHELL_HINT, PI_SHELL_HINT_KEPT_OUT, piShellHint } from "@/lib/buildApp";

const sess = (over: Record<string, unknown> = {}) => ({
  id: "p1",
  harness: "pi",
  folder: "C:\work\shop",
  title: "Session p1",
  account: "",
  model: "",
  effort: "",
  mode: "own",
  harness_session_id: "01a1b2c3-1111-7222-8333-444444444444",
  created_at: "2026-11-04T09:00:00Z",
  updated_at: "2026-11-04T09:00:00Z",
  archived_at: null,
  last_error: "",
  ...over,
});

function seed(harness = "pi") {
  H.gets = {
    "/build/app/harnesses": { harnesses: [{ id: harness, label: harness, installed: true, models: [], modes: [] }] },
    "/build/app/sessions": { sessions: [sess({ harness })] },
    "/editor/options": { editors: [], preferred: "" },
    "/build/app/sessions/p1": { session: sess({ harness }), events: [], running: null, pending: [], commands: [] },
  };
}

beforeEach(() => {
  H.patches = [];
  window.localStorage.clear();
  window.localStorage.setItem(LAST_TOOL_KEY, "pi");
});

afterEach(() => cleanup());

describe("Pi's ! words", () => {
  it("are read the way Pi's own terminal reads them", () => {
    expect(piShellHint("pi", "!important: remember the meeting")).toBe(PI_SHELL_HINT);
    expect(piShellHint("pi", "  !! git status ")).toBe(PI_SHELL_HINT_KEPT_OUT);
    expect(piShellHint("pi", "!")).toBe("");
    expect(piShellHint("pi", "!!   ")).toBe("");
    expect(piShellHint("pi", "say !hello")).toBe("");
    expect(piShellHint("claude-code", "!ls")).toBe("");
    expect(piShellHint("grok", "!ls")).toBe("");
  });

  it("show one quiet line under a Pi session's box while they would run as a command", async () => {
    seed();
    render(<BuildCodeView initialSession="p1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box", {}, { timeout: 10_000 });
    expect(screen.queryByTestId("code-pi-shell")).toBeNull();
    fireEvent.change(box, { target: { value: "!important: remember the meeting" } });
    expect((await screen.findByTestId("code-pi-shell")).textContent).toBe(PI_SHELL_HINT);
    fireEvent.change(box, { target: { value: "!!git status" } });
    expect(screen.getByTestId("code-pi-shell").textContent).toBe(PI_SHELL_HINT_KEPT_OUT);
    fireEvent.change(box, { target: { value: "important: remember" } });
    await waitFor(() => expect(screen.queryByTestId("code-pi-shell")).toBeNull());
  });

  it("say nothing in another tool's session", async () => {
    seed("claude-code");
    render(<BuildCodeView initialSession="p1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box", {}, { timeout: 10_000 });
    fireEvent.change(box, { target: { value: "!ls" } });
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("code-pi-shell")).toBeNull();
  });
});
