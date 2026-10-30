/**
 * v1.412.0 review — Build › Code's start composer, the defects the review
 * found (through the real BuildCodeView):
 *   - the DEFAULT tool with several logins never asked which account: now it
 *     asks when no account is remembered for it, and Send waits for the
 *     answer; with none usable, Send stays off and the chip says why;
 *   - the effort chip never hid: its levels are the chosen MODEL's when the
 *     daemon knows them (`models[].efforts`), it hides for a model that takes
 *     none, and an effort the new model does not offer is dropped;
 *   - every 400 landed on the folder chip: a refusal goes to the chip its
 *     `field` names;
 *   - a parked first message could be sent later, when the user came back to
 *     a session they had left before it was sent.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  holds: {} as Record<string, Promise<void>>,
  posts: [] as { path: string; body: unknown }[],
  postReply: {} as Record<string, unknown>,
  postFail: {} as Record<string, { status: number; message: string; body?: unknown }>,
  streams: [] as { path: string; body: unknown }[],
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    body?: unknown;
    constructor(message: string, status = 0, _c = false, body?: unknown) {
      super(message);
      this.status = status;
      this.name = "ApiError";
      if (body !== undefined) this.body = body;
    }
  }
  return {
    ApiError: FakeApiError,
    get: async (path: string) => {
      const key = path.replace(/\?limit=1$/, "");
      const holding = H.holds[key] as Promise<void> | undefined;
      if (holding) await holding;
      const r = H.gets[key];
      if (r === undefined) throw new FakeApiError(`unmocked GET ${path}`, 404);
      return r;
    },
    post: (path: string, body?: unknown) => {
      H.posts.push({ path, body });
      const fail = H.postFail[path];
      if (fail) return Promise.reject(new FakeApiError(fail.message, fail.status, false, fail.body));
      return Promise.resolve(H.postReply[path] ?? {});
    },
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* (path: string, body: unknown) {
    H.streams.push({ path, body });
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <div data-testid="stub-tree" />,
}));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { LAST_ACCOUNT_KEY, LAST_TOOL_KEY } from "@/components/buildCode/StartComposer";

const MODES = [{ id: "ask", label: "Ask first", dangerous: false }];
const CLAUDE_LEVELS = ["low", "medium", "high", "xhigh", "max"];

function claude(accounts: { id: string; name: string; status: string }[]) {
  return {
    id: "claude-code",
    label: "Claude Code",
    installed: true,
    signed_in: true,
    modes: MODES,
    hidden_modes: [],
    // What the daemon sends: each model's own levels when the picker said.
    models: [
      { id: "", label: "Claude Code's default", efforts: CLAUDE_LEVELS },
      { id: "claude-haiku-4-5", label: "Haiku 4.5", efforts: ["low", "medium", "high"] },
      { id: "claude-fable-5-1", label: "Fable 5.1", efforts: [] },
    ],
    efforts: CLAUDE_LEVELS,
    accounts,
  };
}

const TWO = [
  { id: "p_work", name: "Work", status: "ready" },
  { id: "p_home", name: "Home", status: "ready" },
];

const made = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  harness: "claude-code",
  folder: "",
  no_folder: true,
  work_root: "C:\\Users\\VR\\Documents\\Iron Jarvis",
  title: "",
  account: "",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-10-30T09:00:00Z",
  updated_at: "2026-10-30T09:00:00Z",
  archived_at: null,
  last_error: "",
  ...over,
});

function seed(accounts = TWO) {
  H.gets = {
    "/build/app/harnesses": { harnesses: [claude(accounts)] },
    "/build/app/sessions": { sessions: [], work_root: "C:\\Users\\VR\\Documents\\Iron Jarvis" },
    "/editor/options": { editors: [], preferred: "" },
  };
}

function willMake(id: string, over: Record<string, unknown> = {}) {
  H.postReply["/build/app/sessions"] = made(id, over);
  H.gets[`/build/app/sessions/${id}`] = { session: made(id, over), events: [], running: null, pending: [] };
}

beforeEach(() => {
  H.posts = [];
  H.postReply = {};
  H.postFail = {};
  H.streams = [];
  H.holds = {};
  seed();
  window.localStorage.clear();
});

afterEach(() => {
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
  cleanup();
});

async function ready() {
  render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
  await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Claude Code"));
}

function type(words: string) {
  fireEvent.change(screen.getByTestId("code-start-box"), { target: { value: words } });
}

const createBody = () => H.posts.find((p) => p.path === "/build/app/sessions")?.body as Record<string, unknown>;

describe("the default tool with several logins asks", () => {
  it("no account remembered: the question is open at once, and Send waits for the answer", async () => {
    willMake("n1", { account: "p_home" });
    await ready();
    const menu = await screen.findByTestId("code-start-account-menu");
    expect(menu.textContent).toContain("Which Claude Code account?");
    expect(screen.getByTestId("code-start-account").textContent).toContain("Choose an account");
    type("fix the login page");
    expect(screen.getByTestId("code-start-send")).toBeDisabled();
    fireEvent.keyDown(screen.getByTestId("code-start-box"), { key: "Enter" });
    expect(createBody()).toBeUndefined();
    fireEvent.click(within(menu).getByTestId("code-start-account-p_home"));
    expect(screen.getByTestId("code-start-send")).not.toBeDisabled();
    fireEvent.click(screen.getByTestId("code-start-send"));
    await screen.findByTestId("code-session");
    expect(createBody()).toMatchObject({ harness: "claude-code", account: "p_home" });
  });

  it("an account remembered for the tool is used, not asked", async () => {
    window.localStorage.setItem(LAST_ACCOUNT_KEY + "claude-code", "p_work");
    await ready();
    // The tool's defaults land in an effect after the tool's name is drawn.
    await waitFor(() => expect(screen.getByTestId("code-start-account").textContent).toContain("Work"));
    expect(screen.queryByTestId("code-start-account-menu")).toBeNull();
  });

  it("no account that can start it: Send stays off and the chip says why", async () => {
    seed([
      { id: "p_a", name: "A", status: "needs_sign_in" },
      { id: "p_b", name: "B", status: "limit" },
    ]);
    window.localStorage.setItem(LAST_TOOL_KEY, "claude-code");
    await ready();
    type("go");
    expect(screen.getByTestId("code-start-send")).toBeDisabled();
    const chip = screen.getByTestId("code-start-account");
    expect(chip.textContent).toMatch(/No account can start it/);
    expect(chip.getAttribute("title")).toMatch(/sign in|limit/);
    fireEvent.keyDown(screen.getByTestId("code-start-box"), { key: "Enter" });
    expect(createBody()).toBeUndefined();
  });
});

describe("the effort chip follows the model", () => {
  it("lists the model's own levels, hides for a model that takes none, and drops an effort the new model does not offer", async () => {
    window.localStorage.setItem(LAST_ACCOUNT_KEY + "claude-code", "p_work");
    willMake("n2");
    await ready();
    const levels = () =>
      Array.from((screen.getByTestId("code-start-effort") as HTMLSelectElement).options).map((o) => o.value);
    expect(levels()).toEqual(["", ...CLAUDE_LEVELS]);
    fireEvent.change(screen.getByTestId("code-start-effort"), { target: { value: "max" } });
    fireEvent.change(screen.getByTestId("code-start-model"), { target: { value: "claude-haiku-4-5" } });
    expect(levels()).toEqual(["", "low", "medium", "high"]);
    expect((screen.getByTestId("code-start-effort") as HTMLSelectElement).value).toBe("");
    // Dropped for good: back on a model that takes "max", it is not chosen.
    fireEvent.change(screen.getByTestId("code-start-model"), { target: { value: "" } });
    expect((screen.getByTestId("code-start-effort") as HTMLSelectElement).value).toBe("");
    fireEvent.change(screen.getByTestId("code-start-model"), { target: { value: "claude-fable-5-1" } });
    expect(screen.queryByTestId("code-start-effort")).toBeNull();
    type("go");
    fireEvent.click(screen.getByTestId("code-start-send"));
    await screen.findByTestId("code-session");
    expect(createBody()).toMatchObject({ model: "claude-fable-5-1" });
    expect("effort" in createBody()).toBe(false);
  });

  it("an open session on a model that takes no effort has no effort chip", async () => {
    const s = made("s1", { folder: "C:\\a", no_folder: false, model: "claude-fable-5-1", account: "p_work" });
    H.gets["/build/app/sessions"] = { sessions: [s] };
    H.gets["/build/app/sessions/s1"] = { session: s, events: [], running: null, pending: [] };
    render(<BuildCodeView initialSession="s1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    await screen.findByTestId("code-model-chip");
    expect(screen.queryByTestId("code-effort-chip")).toBeNull();
  });
});

describe("a refusal goes to the chip it is about", () => {
  it("an effort refusal is said on the effort chip, not the folder chip", async () => {
    window.localStorage.setItem(LAST_ACCOUNT_KEY + "claude-code", "p_work");
    const pickFolder = vi.fn(async () => ({ path: "D:\\projects\\shop" }));
    (window as unknown as { ironjarvis?: unknown }).ironjarvis = { isDesktop: true, dialog: { pickFolder } };
    H.postFail["/build/app/sessions"] = {
      status: 400,
      message: '"max" is not an effort Claude Code takes — use low, medium, high.',
      body: { detail: '"max" is not an effort Claude Code takes — use low, medium, high.', field: "effort" },
    };
    await ready();
    fireEvent.click(screen.getByTestId("code-start-folder-choose"));
    await waitFor(() => expect(screen.getByTestId("code-start-folder").textContent).toBe("shop"));
    type("go");
    fireEvent.click(screen.getByTestId("code-start-send"));
    expect(await screen.findByTestId("code-start-effort-problem")).toHaveTextContent("is not an effort");
    expect(screen.queryByTestId("code-start-folder-problem")).toBeNull();
  });

  it("an account refusal is said on the account chip", async () => {
    window.localStorage.setItem(LAST_ACCOUNT_KEY + "claude-code", "p_work");
    H.postFail["/build/app/sessions"] = {
      status: 400,
      message: "That account is not one Iron-Proxy has.",
      body: { detail: "That account is not one Iron-Proxy has.", field: "account" },
    };
    await ready();
    type("go");
    fireEvent.click(screen.getByTestId("code-start-send"));
    expect(await screen.findByTestId("code-start-account-problem")).toHaveTextContent("not one Iron-Proxy has");
    expect(screen.queryByTestId("code-start-folder-problem")).toBeNull();
  });
});

describe("a parked first message", () => {
  it("is never sent after the user opened a different session before it went", async () => {
    window.localStorage.setItem(LAST_ACCOUNT_KEY + "claude-code", "p_work");
    const other = made("s9", { folder: "C:\\other", no_folder: false, title: "Other work", account: "p_work" });
    H.gets["/build/app/sessions"] = { sessions: [other], work_root: "C:\\x" };
    H.gets["/build/app/sessions/s9"] = { session: other, events: [], running: null, pending: [] };
    willMake("n7");
    let release = () => {};
    H.holds["/build/app/sessions/n7"] = new Promise<void>((r) => {
      release = r;
    });
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Claude Code"));
    type("the first words");
    fireEvent.click(screen.getByTestId("code-start-send"));
    await waitFor(() => expect(createBody()).toBeTruthy());
    // The made session is still loading; the user opens another one.
    fireEvent.click(await screen.findByTestId("code-row-s9"));
    await waitFor(() => expect(screen.getByTestId("code-session").textContent).toContain("Other work"));
    release();
    // …and comes back to the made one later.
    H.gets["/build/app/sessions"] = { sessions: [made("n7"), other], work_root: "C:\\x" };
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    fireEvent.click(screen.getByTestId("code-row-n7"));
    await waitFor(() => expect(screen.getByTestId("code-session")).toBeInTheDocument());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(H.streams.filter((s) => s.path === "/build/app/sessions/n7/turn")).toHaveLength(0);
  });
});
