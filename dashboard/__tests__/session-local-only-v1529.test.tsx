/**
 * Owner decision 2 after the third fix plan: an OPEN coding session (Chat ›
 * Code) has its own Local only switch, in the session's ⋯ menu beside the
 * reply limit — on the REAL HarnessSession (the mocks are
 * third-plan-g20-session-settings'):
 *  - it changes the session through the PATCH the daemon already takes
 *    (`local_only`), and shows what the daemon's answer says;
 *  - while a reply runs, the daemon's `applies_local_only` is said beside the
 *    level picker in the words that already exist (LOCAL_ONLY_APPLIES_ON);
 *  - a session an app holds Local only (`local_only_holds`) shows the switch
 *    on, never sends "off", and says why; a hold the page had not read yet
 *    (the PATCH comes back still on) says why too.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown | ((path: string) => unknown)>,
    patches: [] as { path: string; body: unknown }[],
    patchAnswer: (_body: unknown): unknown => ({}),
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    if (method === "PATCH") {
      api.patches.push({ path, body });
      return Promise.resolve(api.patchAnswer(body));
    }
    const hit = method === "GET" ? api.gets[path] : undefined;
    const value = typeof hit === "function" ? (hit as (p: string) => unknown)(path) : hit;
    if (value instanceof Error) return Promise.reject(value);
    if (value === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(value ?? {});
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
    health: { default_provider: "claude-cli", default_model: "opus", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { LOCAL_ONLY_APPLIES_ON, sessionLocalOnlyHeldWords } from "@/lib/buildSessions";

const NET = { timeout: 5000 };
const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(setup: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Write the notes",
    updated_at: "2026-11-17T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "C:\\work\\notes",
    folder_name: "notes",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: { provider: "claude-cli", model: "opus", approval_mode: "yolo", ...setup },
    messages: [],
    session_id: "build:s1",
    queued: [],
    turn_id: "",
  };
}

class Feed {
  private ctl: ReadableStreamDefaultController<Uint8Array> | null = null;
  private enc = new TextEncoder();
  response(): Response {
    const body = new ReadableStream<Uint8Array>({
      start: (c) => {
        this.ctl = c;
      },
    });
    return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }
  frame(event: string, data: unknown): void {
    this.ctl?.enqueue(this.enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
  }
}

let feed: Feed;

beforeEach(() => {
  api.gets = {
    "/build/accounts": new api.ApiError("no such route", 404),
    "/skills": { skills: [] },
    "/build/sessions/s1/skills": { skills: [], note: "" },
    "/build/sessions/s1": view(),
  };
  api.patches = [];
  api.patchAnswer = () => view();
  localStorage.clear();
  feed = new Feed();
  vi.stubGlobal("fetch", vi.fn(async () => feed.response()));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderSession() {
  return render(
    <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
  );
}

function openMenu(): void {
  if (!screen.queryByTestId("harness-session-menu")) {
    fireEvent.click(screen.getByRole("button", { name: "Session actions" }));
  }
}

/** The ⋯ menu's Local only row (the menu opened first). */
async function localOnlyRow(): Promise<HTMLElement> {
  await screen.findByTestId("harness-empty", {}, NET);
  openMenu();
  const menu = await screen.findByTestId("harness-session-menu");
  return within(menu).getByTestId("harness-local-only");
}

const pressed = () => screen.getByTestId("harness-local-only").getAttribute("aria-pressed");

describe("an open coding session's own Local only switch", () => {
  it("sits in the session's ⋯ menu with the reply limit, and turns on and off through the PATCH", async () => {
    renderSession();
    const row = await localOnlyRow();
    expect(row.textContent).toContain("Local only");
    expect(row.getAttribute("aria-pressed")).toBe("false");
    // Beside the reply limit: one menu holds the session's own settings.
    expect(within(screen.getByTestId("harness-session-menu")).getByTestId("harness-cap-open")).toBeTruthy();

    api.patchAnswer = () => view({ local_only: true });
    await act(async () => {
      fireEvent.click(row);
    });
    expect(api.patches.at(-1)).toEqual({ path: "/build/sessions/s1", body: { local_only: true } });
    openMenu();
    await waitFor(() => expect(pressed()).toBe("true"), NET);
    // No reply runs: the daemon says nothing about when, and neither does the page.
    expect(screen.queryByTestId("harness-local-only-applies")).toBeNull();

    api.patchAnswer = () => view();
    await act(async () => {
      fireEvent.click(screen.getByTestId("harness-local-only"));
    });
    expect(api.patches.at(-1)).toEqual({ path: "/build/sessions/s1", body: { local_only: false } });
    openMenu();
    await waitFor(() => expect(pressed()).toBe("false"), NET);
  });

  it("while a reply runs, says the change starts with the next message (the daemon's answer)", async () => {
    renderSession();
    await screen.findByTestId("harness-empty", {}, NET);
    fireEvent.change(screen.getByTestId("harness-input"), { target: { value: "tidy the notes" } });
    await act(async () => {
      fireEvent.keyDown(screen.getByTestId("harness-input"), { key: "Enter" });
    });
    feed.frame("round", { round: 1 });
    openMenu();
    const row = within(await screen.findByTestId("harness-session-menu")).getByTestId("harness-local-only");
    api.patchAnswer = () => ({ ...view({ local_only: true }), applies_local_only: "next_reply" });
    await act(async () => {
      fireEvent.click(row);
    });
    const line = await screen.findByTestId("harness-local-only-applies", {}, NET);
    expect(line.textContent).toBe(LOCAL_ONLY_APPLIES_ON);
  });

  it("a session an app holds Local only shows it on, never sends off, and says why", async () => {
    api.gets["/build/sessions/s1"] = view({ local_only: true, local_only_holds: ["microsoft_365"] });
    renderSession();
    const row = await localOnlyRow();
    await waitFor(() => expect(pressed()).toBe("true"), NET);
    expect(row.getAttribute("aria-disabled")).toBe("true");
    const why = within(screen.getByTestId("harness-session-menu")).getByTestId("harness-local-only-held");
    expect(why.textContent).toBe(
      "Local only stays on: this session used Microsoft 365, which stays on your own models.",
    );
    expect(why.textContent).toBe(sessionLocalOnlyHeldWords(["microsoft_365"]));
    await act(async () => {
      fireEvent.click(screen.getByTestId("harness-local-only"));
    });
    expect(api.patches).toHaveLength(0);
    expect(pressed()).toBe("true");
  });

  it("a hold the page had not read yet: the PATCH comes back still on, and the switch says why", async () => {
    api.gets["/build/sessions/s1"] = view({ local_only: true });
    renderSession();
    const row = await localOnlyRow();
    await waitFor(() => expect(pressed()).toBe("true"), NET);
    expect(screen.queryByTestId("harness-local-only-held")).toBeNull();
    api.patchAnswer = () => view({ local_only: true, local_only_holds: ["microsoft_365"] });
    await act(async () => {
      fireEvent.click(row);
    });
    expect(api.patches.at(-1)?.body).toEqual({ local_only: false });
    openMenu();
    const why = await screen.findByTestId("harness-local-only-held", {}, NET);
    expect(why.textContent).toBe(sessionLocalOnlyHeldWords(["microsoft_365"]));
    expect(pressed()).toBe("true");
  });
});
