/**
 * v1.381.0 REVIEW — Enter never allows a once-only SAFETY card.
 *
 * In the doer's live check the user typed `/status`, pressed Enter twice, and
 * the second Enter — meant for the composer — pressed the docked card's
 * "Allow once": the project's command ran a shell line the user had not read.
 * The dock's first-moment guard (DOCK_KEY_ARM_MS) cannot stop that: a model
 * can put the card up later than 400 ms after the key. So a card that asks
 * once, every time, for a safety reason (once_reason "command" — a project's
 * or a plugin's command's shell step; "git" — a commit or push on a session's
 * branch; "page" — a risky click in the agent's browser) is allowed by a click
 * only. Esc still declines; every other card keeps Enter exactly as before (a
 * local-only card, the plan door, an ordinary ask); the keys line under the
 * card says so. Driven through the real dock (chat and Build), the Build
 * pane's PaneAsk, and the real IJ harness session.
 */
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    cancelled = false;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown>,
    calls: [] as { method: string; path: string; body?: unknown }[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : undefined;
    if (hit instanceof Error) return Promise.reject(hit);
    if (hit === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(hit ?? {});
  };
  return {
    ApiError: api.ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => answer("GET", p),
    post: (p: string, b?: unknown) => answer("POST", p, b),
    put: (p: string, b?: unknown) => answer("PUT", p, b),
    patch: (p: string, b?: unknown) => answer("PATCH", p, b),
    del: (p: string) => answer("DELETE", p),
  };
});
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    checking: false,
    health: { default_provider: "mock", default_model: "mock-1", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <div data-testid="tree" />,
}));

import { DockAsk } from "@/components/chat/DockAsk";
import { PaneAsk } from "@/components/terminal/PaneAsk";
import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { CLICK_TO_ALLOW_HINT, dockAskKeyHint, enterAllows, type DockAskItem } from "@/lib/dockAsk";
import type { PendingApproval } from "@/lib/useChatStream";

const COMMAND_WORDS =
  "This turn runs the project's /deploy command, which has shell lines, so every command in it asks you first.";

const safe = (onceReason: "command" | "git" | "page", id = `ap_${onceReason}`): PendingApproval => ({
  id, callId: "c1", tool: "shell", args: { command: "echo hello" },
  onceOnly: true, onceReason, costNote: COMMAND_WORDS,
});
const ORDINARY: PendingApproval = { id: "ap_plain", callId: "c2", tool: "shell", args: { command: "git status" } };
const LOCAL_ONLY: PendingApproval = { id: "ap_local", callId: "c3", tool: "shell", args: { command: "ls" }, onceOnly: true };
const PLAN_DOOR: PendingApproval = { id: "ap_plan", callId: "c4", tool: "exit_plan_mode", args: {}, onceOnly: true };
const asItem = (a: PendingApproval): DockAskItem => ({ kind: "approval", id: a.id, approval: a });

const posted = (id: string) =>
  api.calls.filter((c) => c.method === "POST" && c.path === `/chat/approvals/${id}`).map((c) => c.body);

function renderDock(asks: DockAskItem[]) {
  return render(
    <DockAsk
      asks={asks}
      onAnswerElicitation={vi.fn(async () => ({ ok: true }) as const)}
      onDecideSampling={vi.fn(async () => true)}
      armDelayMs={0}
    />,
  );
}

const settle = () => new Promise((r) => setTimeout(r, 30));

beforeEach(() => {
  api.gets = {};
  api.calls = [];
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  (document.activeElement as HTMLElement | null)?.blur?.();
});

describe("lib/dockAsk — which cards Enter may allow", () => {
  it("never a once-only card with a safety reason; every other card as before", () => {
    for (const reason of ["command", "git", "page"] as const) expect(enterAllows(asItem(safe(reason)))).toBe(false);
    expect(enterAllows(asItem(ORDINARY))).toBe(true);
    expect(enterAllows(asItem(LOCAL_ONLY))).toBe(true);
    expect(enterAllows(asItem(PLAN_DOOR))).toBe(true);
    // A reason with no once-only flag is not a once-only card.
    expect(enterAllows(asItem({ ...ORDINARY, onceReason: "command" }))).toBe(true);
    expect(enterAllows(null)).toBe(true);
  });

  it("the keys line says a click allows it", () => {
    expect(dockAskKeyHint("approval", asItem(safe("command")))).toBe(CLICK_TO_ALLOW_HINT);
    expect(CLICK_TO_ALLOW_HINT).toBe("Click Allow once to run it · Esc to decline");
    expect(dockAskKeyHint("approval", asItem(ORDINARY))).toBe("Enter to allow · Esc to decline");
    expect(dockAskKeyHint("approval", asItem(LOCAL_ONLY))).toBe("Enter to allow · Esc to decline");
    expect(dockAskKeyHint("approval")).toBe("Enter to allow · Esc to decline");
  });
});

describe("DockAsk — a once-only safety card", () => {
  for (const reason of ["command", "git", "page"] as const) {
    it(`(${reason}) Enter never allows it — held, twice, from the page or the card; Esc declines`, async () => {
      const a = safe(reason);
      renderDock([asItem(a)]);
      (document.activeElement as HTMLElement | null)?.blur?.();
      fireEvent.keyDown(document.body, { key: "Enter" });
      fireEvent.keyDown(document.body, { key: "Enter", repeat: true });
      fireEvent.keyDown(document.body, { key: "Enter" });
      fireEvent.keyDown(screen.getByTestId("chat-approval-card"), { key: "Enter" });
      fireEvent.keyDown(screen.getByTestId("dock-ask"), { key: "Enter" });
      await settle();
      expect(posted(a.id)).toEqual([]);
      fireEvent.keyDown(screen.getByTestId("chat-approval-card"), { key: "Escape" });
      await waitFor(() => expect(posted(a.id)).toEqual([{ decision: "deny" }]));
    });
  }

  it("a click on Allow once allows it once", async () => {
    const a = safe("command");
    renderDock([asItem(a)]);
    fireEvent.click(screen.getByRole("button", { name: "Allow once" }));
    await waitFor(() => expect(posted(a.id)).toEqual([{ decision: "once" }]));
  });

  it("an ordinary card, a local-only card and the plan door still answer Enter", async () => {
    for (const a of [ORDINARY, LOCAL_ONLY, PLAN_DOOR]) {
      renderDock([asItem(a)]);
      (document.activeElement as HTMLElement | null)?.blur?.();
      fireEvent.keyDown(document.body, { key: "Enter" });
      await waitFor(() => expect(posted(a.id)).toEqual([{ decision: "once" }]));
      cleanup();
    }
  });
});

describe("PaneAsk (a Build pane's chat) — the same rule", () => {
  it("Enter never allows a safety card; Esc declines; its keys line says a click allows it", async () => {
    const a = safe("command", "ap_pane");
    render(<PaneAsk approval={a} armDelayMs={0} />);
    const card = screen.getByTestId("pane-ask");
    fireEvent.keyDown(card, { key: "Enter" });
    fireEvent.keyDown(card, { key: "Enter" });
    await settle();
    expect(posted(a.id)).toEqual([]);
    expect(screen.getByText(CLICK_TO_ALLOW_HINT)).toBeInTheDocument();
    fireEvent.keyDown(card, { key: "Escape" });
    await waitFor(() => expect(posted(a.id)).toEqual([{ decision: "deny" }]));
  });

  it("an ordinary card in a pane still answers Enter", async () => {
    render(<PaneAsk approval={ORDINARY} armDelayMs={0} />);
    fireEvent.keyDown(screen.getByTestId("pane-ask"), { key: "Enter" });
    await waitFor(() => expect(posted(ORDINARY.id)).toEqual([{ decision: "once" }]));
    expect(screen.getByText("Enter to allow · Esc to decline")).toBeInTheDocument();
  });
});

describe("the IJ harness session — the doer's live check, driven", () => {
  const terminal: SessionTerminalApi = {
    find: () => null,
    open: async () => null,
    render: () => <div data-testid="term-body" />,
    close: () => {},
  };

  it("/status, Enter twice: the second Enter never allows the project's command's card", async () => {
    let ctl!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({ start(c) { ctl = c; } });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } })));
    api.gets["/build/sessions/s1"] = {
      id: "s1", title: "Shop", updated_at: "2026-10-23T10:00:00", running: false, waiting: false, archived: false,
      folder: "C:\\work\\shop", folder_name: "shop", is_project: false, project_id: null, mode: "act", plan: null,
      setup: {}, messages: [], session_id: "build:s1", queued: [], turn_id: "",
    };
    api.gets["/build/accounts"] = new api.ApiError("no such route", 404);
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    await screen.findByTestId("harness-empty");
    const box = screen.getByTestId("harness-input") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "deploy the shop" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1));
    const frame = {
      id: "ap_live", call_id: "c1", tool: "shell", args: { command: "git status --short" },
      once_only: true, once_reason: "command", cost_note: COMMAND_WORDS,
    };
    await act(async () => {
      ctl.enqueue(new TextEncoder().encode(`event: approval\ndata: ${JSON.stringify(frame)}\n\n`));
    });
    const card = await screen.findByTestId("chat-approval-card");
    expect(within(card).getByTestId("approval-command-once")).toBeInTheDocument();
    expect(screen.getByText(CLICK_TO_ALLOW_HINT)).toBeInTheDocument();
    // Past the dock's first-moment guard: Enter is still not an answer.
    await new Promise((r) => setTimeout(r, 450));
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Enter" });
    fireEvent.keyDown(document.body, { key: "Enter" });
    fireEvent.keyDown(screen.getByTestId("dock-ask"), { key: "Enter" });
    await settle();
    expect(posted("ap_live")).toEqual([]);
    fireEvent.click(within(card).getByRole("button", { name: "Allow once" }));
    await waitFor(() => expect(posted("ap_live")).toEqual([{ decision: "once" }]));
    await act(async () => {
      ctl.close();
    });
  });
});
