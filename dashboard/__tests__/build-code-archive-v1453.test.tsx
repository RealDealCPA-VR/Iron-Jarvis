/**
 * v1.453.0 — Build › Code: Archive from the session row's ⋯ menu (and the
 * header's), and the short Markdown summary the daemon writes when a session
 * is archived, shown when the archived session is opened from "Archived
 * sessions" (Copy, Open file, Bring back) and kept, folded, after it is
 * brought back.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  getLog: [] as string[],
  posts: [] as { path: string; body: unknown }[],
  postReply: {} as Record<string, unknown>,
  postFail: {} as Record<string, { status: number; message: string }>,
  copies: [] as string[],
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
    post: (path: string, body?: unknown) => {
      H.posts.push({ path, body });
      const fail = H.postFail[path];
      if (fail) return Promise.reject(new FakeApiError(fail.message, fail.status));
      return Promise.resolve(H.postReply[path] ?? {});
    },
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({ ok: true }),
  };
});
vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* () {},
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <span /> }));
vi.mock("@/components/settings/MaintenanceTools", () => ({
  copyPlain: (text: string) => {
    H.copies.push(text);
    return Promise.resolve("copied");
  },
}));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { ARCHIVE_ROW, SUMMARY_HEADING } from "@/lib/buildArchive";

const TOOLS = {
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      version: "2.1.311",
      modes: [{ id: "ask", label: "Ask first", dangerous: false }],
      hidden_modes: [],
      models: [],
      efforts: [],
      accounts: [],
      commands: [],
    },
  ],
};

const sess = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  harness: "claude-code",
  folder: "C:\\work\\shop",
  title: `Session ${id}`,
  account: "",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-11-01T09:00:00Z",
  updated_at: "2026-11-01T09:00:00Z",
  archived_at: null,
  last_error: "",
  ...over,
});

const SESSIONS = "/build/app/sessions";
const SUMMARY_TEXT = "# Fix the cart\n\n## Before\n- Last commit before the session started: `abc1234`\n\n## After\n- Left uncommitted: 1 file\n";
const FACTS = { path: "C:\\home\\hosted\\z1\\summary.md", at: "2026-11-08T10:00:00Z" };

beforeEach(() => {
  H.getLog = [];
  H.posts = [];
  H.postReply = {};
  H.postFail = {};
  H.copies = [];
  H.gets = {
    "/build/app/harnesses": TOOLS,
    [SESSIONS]: { sessions: [sess("a1", { title: "Fix the cart" }), sess("a2", { title: "Login page" })] },
    "/editor/options": { editors: [], preferred: "" },
  };
});

afterEach(() => cleanup());

function renderView(initial: string | null = null) {
  return render(<BuildCodeView initialSession={initial} onOpenedTerminal={() => {}} />);
}

describe("Archive from the session row", () => {
  it("is in the row's ⋯ menu; one press archives it and it leaves the list", async () => {
    H.postReply[`${SESSIONS}/a1/archive`] = { ...sess("a1"), archived: true, archive_summary: FACTS };
    renderView();
    fireEvent.click(await screen.findByTestId("code-row-more-a1"));
    const item = screen.getByTestId("code-row-archive-a1");
    expect(item.textContent).toBe(ARCHIVE_ROW);
    expect(item.getAttribute("role")).toBe("menuitem");
    fireEvent.click(item);
    await waitFor(() => expect(H.posts.map((p) => p.path)).toEqual([`${SESSIONS}/a1/archive`]));
    await waitFor(() => expect(screen.queryByTestId("code-row-a1")).toBeNull());
    expect(screen.getByTestId("code-row-a2")).toBeInTheDocument();
    expect(screen.queryByTestId("code-row-note")).toBeNull();
  });

  it("a refusal (background work still running) is said in the daemon's words; the row stays", async () => {
    const REFUSAL =
      "Claude Code is still working in the background in this session (helpers, commands, or a reply of its own). Archiving stops it, so wait for that to finish, or ask it to stop, then archive.";
    H.postFail[`${SESSIONS}/a1/archive`] = { status: 409, message: REFUSAL };
    renderView();
    fireEvent.click(await screen.findByTestId("code-row-more-a1"));
    fireEvent.click(screen.getByTestId("code-row-archive-a1"));
    expect(await screen.findByTestId("code-row-note")).toHaveTextContent(REFUSAL);
    expect(screen.getByTestId("code-row-a1")).toBeInTheDocument();
  });

  it("a running reply makes it unpressable, and says why", async () => {
    H.gets[SESSIONS] = { sessions: [sess("a1", { running: true })] };
    renderView();
    fireEvent.click(await screen.findByTestId("code-row-more-a1"));
    const item = screen.getByTestId("code-row-archive-a1") as HTMLButtonElement;
    expect(item.disabled).toBe(true);
    expect(item.title).toMatch(/A reply is running/);
  });

  it("a summary that could not be written is said (the session WAS archived)", async () => {
    const WORDS = "The archive summary could not be saved: disk full";
    H.postReply[`${SESSIONS}/a1/archive`] = { ...sess("a1"), archived: true, archive_summary_error: WORDS };
    renderView();
    fireEvent.click(await screen.findByTestId("code-row-more-a1"));
    fireEvent.click(screen.getByTestId("code-row-archive-a1"));
    expect(await screen.findByTestId("code-row-note")).toHaveTextContent(WORDS);
    expect(screen.queryByTestId("code-row-a1")).toBeNull();
  });

  it("an archived row offers Bring back, never Archive", async () => {
    H.gets[`${SESSIONS}?archived=1`] = { sessions: [sess("z1", { archived_at: "2026-11-08T10:00:00Z" })] };
    renderView();
    fireEvent.click(await screen.findByTestId("code-archived-toggle"));
    fireEvent.click(await screen.findByTestId("code-row-more-z1"));
    expect(screen.getByTestId("code-row-unarchive-z1")).toBeInTheDocument();
    expect(screen.queryByTestId("code-row-archive-z1")).toBeNull();
  });
});

describe("the archive summary", () => {
  const archivedView = () => {
    const z1 = sess("z1", { title: "Fix the cart", archived_at: "2026-11-08T10:00:00Z" });
    H.gets[`${SESSIONS}?archived=1`] = { sessions: [z1] };
    H.gets[`${SESSIONS}/z1`] = { session: z1, events: [], running: null, pending: [], archive_summary: FACTS };
    H.gets[`${SESSIONS}/z1/summary`] = { text: SUMMARY_TEXT, ...FACTS };
  };

  it("opening an archived session shows its summary, read from the daemon, with Copy and Open file", async () => {
    archivedView();
    renderView();
    fireEvent.click(await screen.findByTestId("code-archived-toggle"));
    fireEvent.click(await screen.findByTestId("code-row-z1"));
    const card = await screen.findByTestId("code-archive-summary");
    expect(card).toHaveTextContent(SUMMARY_HEADING);
    const body = await screen.findByTestId("code-archive-summary-text");
    expect(body).toHaveTextContent("Before");
    expect(body).toHaveTextContent("Left uncommitted: 1 file");
    expect(H.getLog).toContain(`${SESSIONS}/z1/summary`);
    fireEvent.click(screen.getByTestId("code-archive-summary-copy"));
    await waitFor(() => expect(H.copies).toEqual([SUMMARY_TEXT]));
    fireEvent.click(screen.getByTestId("code-archive-summary-open"));
    await waitFor(() => expect(H.posts.map((p) => p.path)).toContain(`${SESSIONS}/z1/summary/open`));
  });

  it("Bring back un-archives it; the summary stays, folded under one press", async () => {
    archivedView();
    H.postReply[`${SESSIONS}/z1/unarchive`] = sess("z1", { title: "Fix the cart" });
    renderView("z1");
    await screen.findByTestId("code-archive-summary-text");
    // Brought back: the next read says it is not archived any more.
    H.gets[`${SESSIONS}/z1`] = {
      session: sess("z1", { title: "Fix the cart" }),
      events: [],
      running: null,
      pending: [],
      archive_summary: FACTS,
    };
    fireEvent.click(screen.getByTestId("code-archive-bring-back"));
    await waitFor(() => expect(H.posts.map((p) => p.path)).toContain(`${SESSIONS}/z1/unarchive`));
    const toggle = await screen.findByTestId("code-archive-summary-toggle");
    await waitFor(() => expect(toggle.getAttribute("aria-expanded")).toBe("false"));
    expect(screen.queryByTestId("code-archive-summary-text")).toBeNull();
    fireEvent.click(toggle);
    expect(await screen.findByTestId("code-archive-summary-text")).toHaveTextContent("Before");
  });

  it("a session with no summary and not archived shows nothing", async () => {
    H.gets[`${SESSIONS}/a1`] = { session: sess("a1"), events: [], running: null, pending: [] };
    renderView("a1");
    await screen.findByTestId("code-empty");
    expect(screen.queryByTestId("code-archive-summary")).toBeNull();
    expect(screen.queryByTestId("code-archived-note")).toBeNull();
    expect(H.getLog).not.toContain(`${SESSIONS}/a1/summary`);
  });
});
