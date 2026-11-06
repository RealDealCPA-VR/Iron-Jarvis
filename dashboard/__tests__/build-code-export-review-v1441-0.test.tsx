/**
 * v1.441.0 lane fix2, REVIEW of "Export as Markdown" reading every page:
 *
 *   - while it reads the earlier pages (up to 200 of them, one after
 *     another) the screen SAYS so — the menu closed at once and nothing was
 *     said, so a slow export looked like a press that did nothing;
 *   - a second press while one reads starts nothing more (it made a second
 *     file, reading every page again);
 *   - leaving the view while it reads stops it: no further page is read and
 *     no file appears after the user left.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  getLog: [] as string[],
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
      H.getLog.push(path);
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(r);
    },
    post: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* () {
    /* nothing streams */
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <span /> }));
vi.mock("@/components/settings/MaintenanceTools", () => ({ copyPlain: () => Promise.resolve("copied") }));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { EXPORT_READING } from "@/lib/buildApp";
import { eventsPagePath } from "@/lib/buildAppHistory";

const SESSIONS = "/build/app/sessions";
const TOOLS = {
  harnesses: [
    {
      id: "claude-code", label: "Claude Code", installed: true, signed_in: true,
      modes: [{ id: "ask", label: "Ask first", dangerous: false }],
      models: [{ id: "", label: "Claude Code's default" }], efforts: [], accounts: [], commands: [],
    },
  ],
};
const sess = (id: string) => ({
  id, harness: "claude-code", folder: "C:\work\shop", title: `Session ${id}`, account: "", model: "",
  effort: "", mode: "ask", harness_session_id: "", created_at: "2026-11-06T09:00:00Z",
  updated_at: "2026-11-06T09:00:00Z", archived_at: null, last_error: "",
});
const LATE = [
  { type: "user", at: "2026-11-06T09:00:00Z", text: "a later question" },
  { type: "text_done", at: "2026-11-06T09:00:05Z", item: "m2:0", text: "a later answer" },
];
const PAGE1 = eventsPagePath(SESSIONS, "a1", "s777", 2000);
const PAGE2 = eventsPagePath(SESSIONS, "a1", "s555", 2000);

function deferred() {
  let resolve!: (v: unknown) => void;
  const promise = new Promise((r) => (resolve = r));
  return { promise, resolve };
}

function catchDownload() {
  const created = vi.fn(() => "blob:x");
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: created });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: () => {} });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  return created;
}

beforeEach(() => {
  H.getLog = [];
  H.gets = {
    "/build/app/harnesses": TOOLS,
    [SESSIONS]: { sessions: [sess("a1")] },
    "/editor/options": { editors: [], preferred: "" },
    [`${SESSIONS}/a1`]: { session: sess("a1"), running: null, pending: [], events: LATE, older: "s777", events_total: 9 },
  };
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function pressExport() {
  await screen.findAllByTestId("code-text", {}, { timeout: 10_000 });
  fireEvent.click(await screen.findByTestId("code-row-more-a1"));
  fireEvent.click(await screen.findByTestId("code-row-export-a1"));
}

describe("export review: a slow export is said, pressed once, and stopped when you leave", () => {
  it("says it is reading while the earlier pages come, and a second press reads nothing more", async () => {
    const created = catchDownload();
    const held = deferred();
    H.gets[PAGE1] = held.promise;
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await pressExport();
    expect((await screen.findByTestId("code-export-status")).textContent).toBe(EXPORT_READING);
    // a second press while it reads
    fireEvent.click(await screen.findByTestId("code-row-more-a1"));
    fireEvent.click(await screen.findByTestId("code-row-export-a1"));
    held.resolve({ events: [], older: null, earlier: 0 });
    await waitFor(() => expect(created).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByTestId("code-export-status")).toBeNull());
    expect(H.getLog.filter((p) => p === PAGE1)).toHaveLength(1);
    expect(created).toHaveBeenCalledTimes(1);
  });

  it("leaving the view while it reads stops it: no more pages, and no file", async () => {
    const created = catchDownload();
    const held = deferred();
    H.gets[PAGE1] = held.promise;
    H.gets[PAGE2] = { events: [], older: null, earlier: 0 };
    const view = render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await pressExport();
    await waitFor(() => expect(H.getLog).toContain(PAGE1));
    view.unmount();
    held.resolve({ events: LATE.slice(0, 1), older: "s555", earlier: 1 });
    await new Promise((r) => setTimeout(r, 50));
    expect(H.getLog).not.toContain(PAGE2);
    expect(created).not.toHaveBeenCalled();
  });
});
