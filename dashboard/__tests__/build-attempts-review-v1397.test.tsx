/**
 * v1.397.0 REVIEW — try one task on several models, on the screen
 * (components/harness/HarnessAttempts + HarnessSession, lib/buildAttempts,
 * lib/buildRetry). Each pin failed on the doer's code:
 *
 *  - a reply that tried the work on several models and was cut off by a
 *    restart offered Retry, which starts every attempt again (and a "Try on…"
 *    message's retry was a plain turn on the session's own model);
 *  - the attempts can be DISCARDED (two presses; their folders to the trash,
 *    their branches kept) — before, attempts nobody kept stayed beside the
 *    repository forever;
 *  - a card that CARRIED the session's work not committed yet says Keep lands
 *    the result in the session's folder, not committed;
 *  - a card a session got by Branch from here belongs to the other session:
 *    no Keep, no Discard all, and it says where they are kept.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    gets: {} as Record<string, unknown>,
    calls: [] as { method: string; path: string; body?: unknown }[],
    postAnswers: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const value = method === "GET" ? api.gets[path] : api.postAnswers[`${method} ${path}`];
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
    health: { default_provider: "mock", default_model: "mock-1", providers: [] },
    refresh: () => {},
  }),
}));

const MODELS = [
  { provider: "codex-cli", model: "gpt-5.5", label: "GPT-5.5 (Codex)", available: true, on_this_network: false },
  { provider: "ollama", model: "glm-local", label: "glm-local", available: true, on_this_network: true },
];
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: MODELS, usable: MODELS, loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { AttemptsCardView } from "@/components/harness/HarnessAttempts";
import { retryOffered } from "@/lib/buildRetry";
import type { BuildMessage } from "@/lib/buildSessions";

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

const GIT = (name: string) => ({
  repo: "C:\\work\\calc",
  main: "C:\\work\\calc",
  checkout: "C:\\work\\calc",
  worktree: `C:\\work\\calc.ij-worktrees\\${name}`,
  branch: `ij/${name}`,
  base: "ij/session-ab12",
  base_commit: "abc123",
  name,
});

const CARD = {
  task: "fix add",
  base: "ij/session-ab12",
  base_commit: "abc123",
  adopt: false,
  items: [
    {
      id: "try1", label: "GPT-5.5 (Codex)", provider: "codex-cli", model: "gpt-5.5", cost: "plan",
      branch: "ij/try-codex-1", state: "done", why: "finished", words: "Finished", steps: 3, ms: 4000,
      tests: "passed", files: ["calc.py"], usage: { input_tokens: 10, output_tokens: 2 }, git: GIT("try-codex-1"),
    },
    {
      id: "try2", label: "glm-local", provider: "ollama", model: "glm-local", cost: "free",
      branch: "ij/try-glm-2", state: "stopped", why: "cut_off", words: "Cut off when Iron Jarvis stopped",
      steps: 1, ms: 2000, usage: { input_tokens: 5, output_tokens: 1 }, git: GIT("try-glm-2"),
    },
  ],
};

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1", title: "Fix add", updated_at: "2026-10-26T10:00:00", running: false, waiting: false,
    archived: false, folder: "C:\\work\\calc", folder_name: "calc", is_project: false, project_id: null,
    mode: "act", plan: null, setup: {}, messages: [], session_id: "build:s1", queued: [], turn_id: "", ...over,
  };
}

beforeEach(() => {
  api.gets = { "/build/sessions/s1": view(), "/build/accounts": new api.ApiError("no such route", 404) };
  api.calls = [];
  api.postAnswers = {};
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderCard(raw: unknown, canKeep = true, onKept = () => {}) {
  return render(<AttemptsCardView raw={raw} threadId="s1" turnId="t1" canKeep={canKeep} onKept={onKept} />);
}
const column = (id: string) =>
  screen.getAllByTestId("attempt-column").find((el) => el.getAttribute("data-attempt") === id)!;

describe("a restart cut the attempts off: the card is the way out, never Retry", () => {
  const CUT = "Iron Jarvis stopped while the attempts worked (a restart or a crash), and nothing restarts on its own. The card shows what each attempt did; a finished one can still be kept.";

  it("retryOffered says no for a reply that carries an attempts card", () => {
    const m = { role: "assistant", content: "", error: CUT, turnId: "t1", attempts: CARD } as unknown as BuildMessage;
    expect(retryOffered(m, { newest: true, busy: false, archived: false })).toBe(false);
    const plain = { role: "assistant", content: "", error: "Iron Jarvis stopped while answering.", turnId: "t2" } as unknown as BuildMessage;
    expect(retryOffered(plain, { newest: true, busy: false, archived: false })).toBe(true);
  });

  it("the session shows the card (Keep on the finished attempt) and no Retry", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [
        { role: "user", content: "Try this with Codex and the local model: fix add", turnId: "t1" },
        { role: "assistant", content: "", error: CUT, turnId: "t1", attempts: CARD },
      ],
    });
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    await waitFor(() => expect(screen.getAllByTestId("attempt-column")).toHaveLength(2));
    expect(screen.getByTestId("harness-reply-error")).toHaveTextContent("nothing restarts on its own");
    expect(screen.queryByTestId("harness-retry")).toBeNull();
    expect(within(column("try1")).getByTestId("attempt-keep")).toBeEnabled();
  });
});

describe("Discard all", () => {
  it("two presses post the person's press and re-read; the branches stay", async () => {
    const kept = vi.fn();
    api.postAnswers["POST /build/sessions/s1/attempts/discard"] = {
      words: "Discarded the attempts on GPT-5.5 (Codex) and glm-local: their folders are in the trash, and their branches stay.",
    };
    renderCard(CARD, true, kept);
    const wrap = screen.getByTestId("attempts-discard");
    const press = within(wrap).getByRole("button");
    expect(press).toHaveAttribute("type", "button");
    expect(press).toHaveTextContent("Discard all");
    fireEvent.click(press);
    // The first press only arms it: nothing is posted yet.
    expect(api.calls.filter((c) => c.path.endsWith("/attempts/discard"))).toHaveLength(0);
    expect(press).toHaveTextContent("Move their folders to the trash");
    fireEvent.click(press);
    await waitFor(() => expect(kept).toHaveBeenCalledTimes(1));
    expect(api.calls).toContainEqual({ method: "POST", path: "/build/sessions/s1/attempts/discard", body: { turn_id: "t1" } });
    expect(screen.getByTestId("attempts-said")).toHaveTextContent("their branches stay");
  });

  it("is not offered while anything runs, after Keep, or once every folder is in the trash", () => {
    const { unmount } = renderCard(CARD, false);
    expect(screen.queryByTestId("attempts-discard")).toBeNull();
    unmount();
    const kept = { ...CARD, kept: "try1", items: CARD.items.map((i) => ({ ...i, trashed: true })) };
    const second = renderCard(kept);
    expect(screen.queryByTestId("attempts-discard")).toBeNull();
    second.unmount();
    const gone = { ...CARD, discarded_at: "2026-10-26T11:00:00", items: CARD.items.map((i) => ({ ...i, trashed: true })) };
    renderCard(gone);
    expect(screen.queryByTestId("attempts-discard")).toBeNull();
    expect(screen.queryAllByTestId("attempt-keep")).toHaveLength(0);
    expect(screen.getByTestId("attempts-lead")).toHaveTextContent("folders are in the trash, and their branches stay");
  });
});

describe("the session's work not committed yet, carried into the attempts", () => {
  it("says Keep lands the result in the session's folder, not committed — and says it after", () => {
    const { unmount } = renderCard({ ...CARD, carried: true });
    expect(screen.getByTestId("attempts-lead")).toHaveTextContent("its result goes into this session's folder, not committed yet");
    unmount();
    renderCard({ ...CARD, carried: true, landed: true, kept: "try1", items: CARD.items.map((i) => (i.id === "try1" ? { ...i, kept: true, trashed: true } : { ...i, trashed: true })) });
    expect(within(column("try1")).getByTestId("attempt-after")).toHaveTextContent("its result is in this session's folder, not committed yet");
  });
});

describe("a card copied by Branch from here", () => {
  it("offers neither Keep nor Discard all and says where the attempts are kept", () => {
    const copied = { ...CARD, from_session: "s0", items: CARD.items.map(({ git: _g, ...rest }) => rest) };
    renderCard(copied);
    expect(screen.queryAllByTestId("attempt-keep")).toHaveLength(0);
    expect(screen.queryByTestId("attempts-discard")).toBeNull();
    expect(screen.getByTestId("attempts-lead")).toHaveTextContent("belong to the session this one was branched from");
  });
});
