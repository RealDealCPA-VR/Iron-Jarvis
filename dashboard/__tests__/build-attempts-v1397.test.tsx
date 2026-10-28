/**
 * v1.397.0 — TRY ONE TASK ON SEVERAL MODELS on the screen
 * (components/harness/HarnessAttempts + HarnessTryOn + HarnessSession,
 * lib/buildAttempts), driven through the REAL chat stream against a fetch
 * double and a mocked lib/api:
 *
 *  - while the attempts run, each one is ONE row fed by the turn's own
 *    `attempt` frames (its model and account, "Working · N steps"), and an
 *    attempt's own approval card is drawn on its row and answered there;
 *  - when they end, the reply's `attempts` card shows them side by side —
 *    why it stopped, the finish check's tests, the files, the time, the
 *    usage (a plan's dollars at LIST PRICE, a local model free) — and Keep
 *    on a finished one, which posts the press and re-reads the session; a
 *    refusal (a merge conflict) is said in words; after Keep the card says
 *    which was kept and where the others went;
 *  - the box's "Try on…" control is opt-in for the NEXT message only: at
 *    most three models, Local only offers only local ones, the picks ride
 *    the send as `try_on` and leave the chip;
 *  - a saved card never waits on anyone and never reads as running.
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
    calls: [] as { method: string; path: string; body?: unknown }[],
    postAnswers: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : api.postAnswers[`${method} ${path}`];
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
    health: { default_provider: "mock", default_model: "mock-1", providers: [] },
    refresh: () => {},
  }),
}));

const MODELS = [
  { provider: "claude-cli", model: "claude-sonnet-4-5", label: "Claude Sonnet 4.5", available: true, on_this_network: false },
  { provider: "codex-cli", model: "gpt-5.5", label: "GPT-5.5 (Codex)", available: true, on_this_network: false },
  { provider: "ollama", model: "glm-local", label: "glm-local", available: true, on_this_network: true },
  { provider: "ollama", model: "qwen-local", label: "qwen-local", available: true, on_this_network: true },
  { provider: "google", model: "gemini-x", label: "Gemini X", available: false, on_this_network: false },
  { provider: "mock", model: "mock-1", available: true },
];
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: MODELS, usable: MODELS, loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { AttemptsCardView } from "@/components/harness/HarnessAttempts";
import { TryOnControl } from "@/components/harness/HarnessTryOn";
import {
  MAX_ATTEMPTS,
  applyAttemptFrame,
  decodeAttemptsCard,
  togglePick,
  tryOnChoices,
  tryOnMenuPosition,
  usageWords,
  type TryOnPick,
} from "@/lib/buildAttempts";
import { decodeMessage } from "@/lib/buildSessions";

/* ------------------------------------------------------------- fixtures */

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Fix add",
    updated_at: "2026-10-26T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "C:\\work\\calc",
    folder_name: "calc",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: {},
    messages: [],
    session_id: "build:s1",
    queued: [],
    turn_id: "",
    ...over,
  };
}

function controlledStream() {
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctl = c;
    },
  });
  const enc = new TextEncoder();
  return {
    response: new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    push: (event: string, data: unknown) => ctl.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    end: () => ctl.close(),
  };
}

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

/** The card the daemon keeps on the reply line (harness/attempts.saved_card). */
const CARD = {
  task: "fix the add function in calc.py",
  base: "ij/session-ab12",
  base_commit: "abc123",
  adopt: false,
  items: [
    {
      id: "try1", label: "Claude Sonnet 4.5", provider: "claude-cli", model: "claude-sonnet-4-5",
      account: "c-work", account_name: "Work Max", cost: "plan", branch: "ij/try-claude-1a2b",
      state: "limit", why: "limit", words: "Stopped at an account limit: The Claude account \"Work Max\" is at its request limit.",
      steps: 0, ms: 900, usage: { input_tokens: 0, output_tokens: 0 }, git: GIT("try-claude-1a2b"),
    },
    {
      id: "try2", label: "GPT-5.5 (Codex)", provider: "codex-cli", model: "gpt-5.5", account: "x-plus",
      account_name: "Codex Plus", cost: "plan", branch: "ij/try-codex-3c4d", state: "done", why: "finished",
      words: "Finished", steps: 3, ms: 42_000, tests: "passed", finish: { tests: "passed", command: "python -m unittest" },
      files: ["calc.py"], usage: { input_tokens: 1200, output_tokens: 300, cost_usd: 0.0123, list_price_equivalent: true },
      git: GIT("try-codex-3c4d"),
    },
    {
      id: "try3", label: "glm-local", provider: "ollama", model: "glm-local", cost: "free",
      branch: "ij/try-glm-local-5e6f", state: "done", why: "finished", words: "Finished", steps: 3, ms: 31_000,
      tests: "failed", files: ["calc.py"], usage: { input_tokens: 900, output_tokens: 210 }, git: GIT("try-glm-local-5e6f"),
    },
  ],
};

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = { "/build/sessions/s1": view(), "/build/accounts": new api.ApiError("no such route", 404) };
  api.calls = [];
  api.postAnswers = {};
  localStorage.clear();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
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

const box = () => screen.getByTestId("harness-input") as HTMLTextAreaElement;
async function send(text: string) {
  fireEvent.change(box(), { target: { value: text } });
  fireEvent.keyDown(box(), { key: "Enter" });
}
const attemptRow = (id: string) =>
  screen.getAllByTestId("attempt-row").find((el) => el.getAttribute("data-attempt") === id) ?? null;
const column = (id: string) =>
  screen.getAllByTestId("attempt-column").find((el) => el.getAttribute("data-attempt") === id)!;

/* ---------------------------------------------------------------- tests */

describe("while they run: one row per attempt, from the turn's own frames", () => {
  it("draws each attempt's model, account and progress, and its own approval card on its row", async () => {
    const s = controlledStream();
    fetchMock.mockImplementation(async () => s.response);
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("Try this with Codex and the local model: fix add");
    await act(async () => {
      s.push("attempt", { id: "try1", label: "GPT-5.5 (Codex)", provider: "codex-cli", model: "gpt-5.5",
        account: "x-plus", account_name: "Codex Plus", branch: "ij/try-codex-3c4d", state: "starting", steps: 0,
        since: 1, ask: null, git: GIT("try-codex-3c4d") });
      s.push("attempt", { id: "try2", label: "glm-local", provider: "ollama", model: "glm-local",
        branch: "ij/try-glm-local-5e6f", state: "running", steps: 2, since: 1, ask: null });
    });
    await waitFor(() => expect(attemptRow("try2")).not.toBeNull());
    expect(attemptRow("try1")).toHaveTextContent("GPT-5.5 (Codex) · Codex Plus — Starting");
    expect(attemptRow("try1")).toHaveTextContent("On ij/try-codex-3c4d");
    expect(attemptRow("try2")).toHaveTextContent("glm-local — Working · 2 steps");
    // An attempt's own card waits on ITS row and is answered there.
    await act(async () => {
      s.push("attempt", { id: "try2", state: "waiting", ask: { id: "apr_9", tool: "shell", args: { command: "pytest" } } });
    });
    const ask = await screen.findByTestId("attempt-ask");
    expect(attemptRow("try2")).toHaveTextContent("Waiting for your OK");
    expect(ask).toHaveTextContent("pytest");
    fireEvent.click(within(ask).getByRole("button", { name: /Allow once/ }));
    await waitFor(() =>
      expect(api.calls).toContainEqual({ method: "POST", path: "/chat/approvals/apr_9", body: { decision: "once" } }),
    );
    await act(async () => {
      s.push("attempt", { id: "try2", state: "running", ask: null, steps: 3 });
    });
    await waitFor(() => expect(screen.queryByTestId("attempt-ask")).toBeNull());
    s.end();
  });
});

describe("when they end: one card, side by side, and Keep", () => {
  function renderCard(raw: unknown = CARD, canKeep = true, onKept = () => {}) {
    return render(<AttemptsCardView raw={raw} threadId="s1" turnId="t1" canKeep={canKeep} onKept={onKept} />);
  }

  it("shows each attempt's model, why it stopped, tests, files, time and usage", () => {
    renderCard();
    expect(screen.getAllByTestId("attempt-column")).toHaveLength(3);
    const claude = column("try1");
    expect(claude).toHaveTextContent("Claude Sonnet 4.5 · Work Max");
    expect(within(claude).getByTestId("attempt-why")).toHaveTextContent("Stopped at an account limit");
    const codex = column("try2");
    expect(within(codex).getByTestId("attempt-tests")).toHaveTextContent("Tests passed");
    expect(within(codex).getByTestId("attempt-files")).toHaveTextContent("1 file changed");
    expect(within(codex).getByTestId("attempt-time")).toHaveTextContent("42 s");
    // A plan's dollars are a LIST-PRICE equivalent, never spend.
    expect(within(codex).getByTestId("attempt-usage")).toHaveTextContent("≈$0.01 at list price (your plan)");
    const local = column("try3");
    expect(within(local).getByTestId("attempt-tests")).toHaveTextContent("Tests failed");
    expect(within(local).getByTestId("attempt-usage")).toHaveTextContent("Free (on your own hardware)");
    // Keep is offered on the finished attempts only — not on the one the
    // account limit stopped before it changed anything.
    expect(within(codex).getByTestId("attempt-keep")).toBeInTheDocument();
    expect(within(local).getByTestId("attempt-keep")).toBeInTheDocument();
    expect(within(claude).queryByTestId("attempt-keep")).toBeNull();
    expect(screen.getByTestId("attempts-lead")).toHaveTextContent("only its work joins this session's branch");
    // Every button is typeless-safe (a form may contain the card).
    for (const b of screen.getAllByRole("button")) expect(b).toHaveAttribute("type", "button");
  });

  it("Keep posts the person's press and re-reads; a refusal is said in words", async () => {
    const kept = vi.fn();
    api.postAnswers["POST /build/sessions/s1/attempts/keep"] = new api.ApiError(
      "Merging would conflict in calc.py. Nothing was changed: bring ij/session-ab12's changes into this branch.", 409,
    );
    renderCard(CARD, true, kept);
    fireEvent.click(within(column("try2")).getByTestId("attempt-keep"));
    const said = await screen.findByTestId("attempts-said");
    expect(said).toHaveAttribute("role", "alert");
    expect(said).toHaveTextContent("would conflict in calc.py");
    expect(kept).not.toHaveBeenCalled();
    expect(api.calls).toContainEqual({
      method: "POST", path: "/build/sessions/s1/attempts/keep", body: { turn_id: "t1", attempt: "try2" },
    });
    api.postAnswers["POST /build/sessions/s1/attempts/keep"] = { words: "Kept the attempt on GPT-5.5 (Codex): merged into ij/session-ab12." };
    fireEvent.click(within(column("try2")).getByTestId("attempt-keep"));
    await waitFor(() => expect(kept).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("attempts-said")).toHaveAttribute("role", "status");
  });

  it("no Keep while anything runs; after Keep the card says what was kept and where the others went", () => {
    const { unmount } = renderCard(CARD, false);
    expect(screen.queryAllByTestId("attempt-keep")).toHaveLength(0);
    unmount();
    const after = {
      ...CARD,
      kept: "try2",
      merged_into: "ij/session-ab12",
      items: CARD.items.map((i) => (i.id === "try2" ? { ...i, kept: true, trashed: true } : { ...i, trashed: true })),
    };
    renderCard(after);
    expect(screen.queryAllByTestId("attempt-keep")).toHaveLength(0);
    expect(column("try2")).toHaveAttribute("data-kept", "true");
    expect(within(column("try2")).getByTestId("attempt-after")).toHaveTextContent("Kept: merged into ij/session-ab12");
    expect(within(column("try3")).getByTestId("attempt-after")).toHaveTextContent("Folder in the trash · branch kept");
    expect(screen.queryByTestId("attempts-lead")).toBeNull();
  });

  it("the session draws the card from the saved reply line (a reload, a return, a restart)", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [
        { role: "user", content: "Try this with Claude, Codex and the local model: fix add", turnId: "t1" },
        { role: "assistant", content: "Tried this on 3 models, each on its own branch:", turnId: "t1", attempts: CARD },
      ],
    });
    renderSession();
    await waitFor(() => expect(screen.getAllByTestId("attempt-column")).toHaveLength(3));
    expect(screen.getByTestId("attempts-card")).toBeInTheDocument();
    // The line decoder keeps `attempts` (the daemon's LINE_KEYS).
    const line = decodeMessage({ role: "assistant", content: "", attempts: CARD, junk: 1 }) as unknown as Record<string, unknown>;
    expect(line.attempts).toEqual(CARD);
    expect("junk" in line).toBe(false);
  });
});

describe("a saved card never waits on anyone and never reads as running", () => {
  it("drops a live ask and settles a running state", () => {
    const card = decodeAttemptsCard({
      items: [{ id: "try1", label: "x", state: "running", ask: { id: "a", tool: "shell", args: {} }, git: GIT("x") }],
    })!;
    expect(card.items[0].state).toBe("stopped");
    expect(card.items[0].ask).toBeNull();
    expect(decodeAttemptsCard({ items: [] })).toBeNull();
    expect(decodeAttemptsCard("junk")).toBeNull();
  });

  it("a live fold keeps at most three attempts, in order", () => {
    let rows = applyAttemptFrame([], { id: "try1", label: "a", state: "running" });
    rows = applyAttemptFrame(rows, { id: "try2", label: "b" });
    rows = applyAttemptFrame(rows, { id: "try3", label: "c" });
    rows = applyAttemptFrame(rows, { id: "try4", label: "d" });
    expect(rows.map((r) => r.id)).toEqual(["try1", "try2", "try3"]);
    rows = applyAttemptFrame(rows, { id: "try1", steps: 4 });
    expect(rows[0].steps).toBe(4);
    expect(rows[0].label).toBe("a");
  });

  it("money words: plan = list price, pay per use = metered, local = free", () => {
    const base = decodeAttemptsCard(CARD)!.items[1];
    expect(usageWords({ ...base, cost: "pay", usage: { inputTokens: 10, outputTokens: 2, costUsd: 0.5 } })).toMatch(/^\$0\.50 · /);
    expect(usageWords({ ...base, cost: "plan", usage: { inputTokens: 10, outputTokens: 2 } })).toMatch(/^Your plan · /);
    expect(usageWords({ ...base, cost: "free", usage: { inputTokens: 10, outputTokens: 2 } })).toMatch(/^Free/);
  });
});

/** v1.400.7: a provider's models open in its pop-out (lib/tryOnMenu). */
function openTryOnGroup(provider: string) {
  const row = screen.getAllByTestId("harness-try-on-group").find((g) => g.getAttribute("data-group") === provider)!;
  if (row.getAttribute("aria-expanded") !== "true") fireEvent.click(row);
}
function tryOnRow(model: string): HTMLInputElement {
  return (screen.getAllByTestId("harness-try-on-row") as HTMLInputElement[]).find((r) => r.getAttribute("data-model") === model)!;
}

describe("the \"Try on…\" control: opt-in, for the next message, at most three", () => {
  it("offers connected models (never the demo model), local only offers only local ones", () => {
    expect(tryOnChoices(MODELS, false).map((r) => r.model)).toEqual(["claude-sonnet-4-5", "gpt-5.5", "glm-local", "qwen-local"]);
    expect(tryOnChoices(MODELS, true).map((r) => r.model)).toEqual(["glm-local", "qwen-local"]);
    let picks: TryOnPick[] = [];
    for (const r of tryOnChoices(MODELS, false)) picks = togglePick(picks, { provider: r.provider, model: r.model, label: r.model });
    expect(picks).toHaveLength(MAX_ATTEMPTS);
    picks = togglePick(picks, picks[0]);
    expect(picks).toHaveLength(2);
  });

  it("its list is always inside the window: a phone, a chip mid-row, a chip at the edge", () => {
    for (const [vw, vh, left, top] of [
      [390, 844, 250, 760],
      [390, 844, 10, 760],
      [1280, 720, 1200, 650],
      [1024, 640, 300, 120],
    ] as const) {
      const p = tryOnMenuPosition({ left, top }, { width: vw, height: vh });
      expect(p.left).toBeGreaterThanOrEqual(16);
      expect(p.left + p.width).toBeLessThanOrEqual(vw - 16);
      expect(p.width).toBeLessThanOrEqual(320);
      // Hung just above the chip; never taller than the room above it.
      expect(vh - p.bottom).toBeLessThanOrEqual(top);
      expect(p.maxHeight).toBeGreaterThan(0);
    }
  });

  it("opens, ticks up to three, Escape closes and gives the focus back", () => {
    const onChange = vi.fn();
    function Harness() {
      const [picks, setPicks] = React.useState<TryOnPick[]>([]);
      return (
        <TryOnControl
          models={MODELS}
          localOnly={false}
          picks={picks}
          onChange={(p) => {
            onChange(p);
            setPicks(p);
          }}
        />
      );
    }
    render(<Harness />);
    const chip = screen.getByTestId("harness-try-on");
    expect(chip).toHaveTextContent("Try on…");
    fireEvent.click(chip);
    // v1.400.7: one row per provider, local first; a provider's models open
    // in its pop-out (lib/tryOnMenu).
    expect(screen.getAllByTestId("harness-try-on-group").map((g) => g.getAttribute("data-group"))).toEqual([
      "ollama",
      "claude-cli",
      "codex-cli",
    ]);
    openTryOnGroup("claude-cli");
    fireEvent.click(tryOnRow("claude-sonnet-4-5"));
    openTryOnGroup("codex-cli");
    fireEvent.click(tryOnRow("gpt-5.5"));
    openTryOnGroup("ollama");
    expect((screen.getAllByTestId("harness-try-on-row") as HTMLInputElement[]).map((r) => r.getAttribute("data-model"))).toEqual([
      "glm-local",
      "qwen-local",
    ]);
    fireEvent.click(tryOnRow("glm-local"));
    expect(tryOnRow("qwen-local")).toBeDisabled();
    expect(screen.getByTestId("harness-try-on")).toHaveTextContent("Try on 3 models");
    // Escape closes the pop-out, then the list.
    fireEvent.keyDown(screen.getByTestId("harness-try-on-menu"), { key: "Escape" });
    fireEvent.keyDown(screen.getByTestId("harness-try-on-menu"), { key: "Escape" });
    expect(screen.queryByTestId("harness-try-on-menu")).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId("harness-try-on"));
    for (const b of screen.getAllByRole("button")) expect(b).toHaveAttribute("type", "button");
  });

  it("the picks ride the NEXT message as try_on and leave the chip", async () => {
    const s = controlledStream();
    fetchMock.mockImplementation(async () => s.response);
    renderSession();
    await screen.findByTestId("harness-empty");
    fireEvent.click(screen.getByTestId("harness-try-on"));
    openTryOnGroup("codex-cli");
    fireEvent.click(tryOnRow("gpt-5.5"));
    openTryOnGroup("ollama");
    fireEvent.click(tryOnRow("glm-local"));
    fireEvent.click(screen.getByTestId("harness-try-on-done"));
    expect(screen.getByTestId("harness-try-on")).toHaveTextContent("Try on 2 models");
    await send("fix add in calc.py");
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/build/sessions/s1/turn");
    const body = JSON.parse(String(init.body));
    expect(body.try_on).toEqual([
      { provider: "codex-cli", model: "gpt-5.5" },
      { provider: "ollama", model: "glm-local" },
    ]);
    expect(body.text).toBe("fix add in calc.py");
    // One message only: the chip is back to asking.
    expect(screen.getByTestId("harness-try-on")).toHaveTextContent("Try on…");
    s.end();
  });

  it("an ordinary send carries no try_on", async () => {
    const s = controlledStream();
    fetchMock.mockImplementation(async () => s.response);
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("fix add");
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(String((fetchMock.mock.calls[0] as [string, RequestInit])[1].body));
    expect("try_on" in body).toBe(false);
    s.end();
  });
});
