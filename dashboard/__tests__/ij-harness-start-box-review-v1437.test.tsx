/**
 * v1.437.0 review — Chat › Code's new-session box, what the first pins left
 * open (each pin red on the reviewed commit, green after the fix):
 *
 *  - Plan picked IN THE PERMISSION CHIP reaches the ONE create as `mode:
 *    "plan"` (only `startCreateBody` was pinned: dropping it from the box's
 *    own request stayed green);
 *  - a first message the daemon refused before its turn started puts EVERY
 *    chip back in the box — the model and the account it was picked under,
 *    the effort, Plan, the team (named by its models), Local only — and a
 *    second Send carries them all again (only the words and the "Try on…"
 *    picks were pinned; losing the account, the effort or the team stayed
 *    green);
 *  - turning Local only on takes back a team member the box put on the team
 *    on a model that leaves this PC, in words (only the model and the "Try
 *    on…" picks were taken back);
 *  - a level picked on the default model (no model chosen) is not offered as
 *    the folder's default: the box applies a folder's level only beside its
 *    model, so it would be said and never used;
 *  - an account list that got no answer (Iron Jarvis still starting) is read
 *    again when the model menu opens — it used to stop asking for good.
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
    gets: {} as Record<string, unknown | ((path: string) => unknown)>,
    posts: {} as Record<string, unknown | ((path: string, body: unknown) => unknown)>,
    calls: [] as { method: string; path: string; body?: unknown }[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const table = method === "GET" ? api.gets : method === "POST" ? api.posts : {};
    const hit = table[path];
    const value = typeof hit === "function" ? (hit as (p: string, b?: unknown) => unknown)(path, body) : hit;
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
const DAEMON = {
  online: true,
  checking: false,
  health: { default_provider: "mock", default_model: "mock-1", providers: [] as unknown[] },
  refresh: () => {},
};
vi.mock("@/lib/daemon", () => ({ useDaemon: () => DAEMON }));
const ROWS = [
  {
    provider: "claude-cli",
    model: "opus",
    label: "Opus 5.5",
    available: true,
    kind: "cli",
    on_this_network: false,
    reasoning: ["low", "medium", "high", "xhigh"],
    reasoning_default: "medium",
  },
  {
    provider: "codex-cli",
    model: "gpt-6",
    label: "GPT-6",
    available: true,
    kind: "cli",
    on_this_network: false,
    reasoning: ["low", "medium"],
    reasoning_default: "medium",
  },
  { provider: "ollama", model: "glm-local", label: "GLM local", available: true, kind: "local", on_this_network: true },
];
const MODELS = { models: ROWS, usable: ROWS, loading: false };
vi.mock("@/lib/useModels", () => ({ useModels: () => MODELS }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="tree" /> }));

import { HarnessStartComposer, type StartRequest } from "@/components/harness/HarnessStartComposer";
import { IjHarnessView } from "@/components/harness/IjHarnessView";
import { resetTurnCache } from "@/lib/turnCache";
import { resetFolderDefaultsCache } from "@/lib/folderDefaults";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";

const REPO = "C:\\work\\shop";
const gitPath = (f: string) => `/build/git/folder?path=${encodeURIComponent(f)}`;
const teamPath = (f: string) => `/build/folders/team?folder=${encodeURIComponent(f)}`;
const GIT_OK = { git: true, repo: true, branch: "main", can_branch: true, note: "", worktrees_dir: "" };
const ACCOUNTS = {
  claude: [
    { id: "c-work", name: "Work", default: true, status: "ready", plan_label: "Max" },
    { id: "c-home", name: "Home", status: "ready", plan_label: "Pro" },
  ],
  codex: [],
  grok: [],
  iron_proxy: { enabled: true, running: true },
};
/** POST /team/members/from-model: the member named after the model asked for. */
const MEMBERS: Record<string, { name: string; label: string }> = {
  "ollama::glm-local": { name: "glm-w", label: "GLM local" },
  "codex-cli::gpt-6": { name: "gpt6-w", label: "GPT-6" },
};
const terminal: SessionTerminalApi = { find: () => null, open: async () => null, render: () => <div />, close: () => {} };

function view(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    title: "New session",
    updated_at: "2026-11-05T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "",
    folder_name: "",
    no_folder: true,
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: {},
    messages: [],
    session_id: `build:${id}`,
    queued: [],
    turn_id: "",
    ...over,
  };
}

function openStream() {
  const body = new ReadableStream<Uint8Array>({ start() {} });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

const turnBodies: Record<string, unknown>[] = [];
function stubTurn(answer: () => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (/\/build\/sessions\/new\d+\/turn/.test(String(url))) {
        turnBodies.push(JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>);
        return answer();
      }
      return new Response("{}", { status: 200 });
    }),
  );
}

let made = 0;
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetTurnCache();
  resetFolderDefaultsCache();
  window.history.replaceState(null, "", "/chat?view=code");
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
  api.calls = [];
  turnBodies.length = 0;
  made = 0;
  DAEMON.health = { default_provider: "mock", default_model: "mock-1", providers: [] };
  api.gets = {
    "/build/sessions": { folders: [], work_root: "C:\\Users\\VR\\Documents\\Iron Jarvis" },
    "/build/sessions?archived=1": { folders: [] },
    "/build/accounts": ACCOUNTS,
    [teamPath("")]: { folder: "", team: null },
    [teamPath(REPO)]: { folder: REPO, team: null },
    [gitPath(REPO)]: GIT_OK,
  };
  api.posts = {
    "/build/sessions": (_p: string, body: unknown) => {
      made += 1;
      const folder = (body as { folder?: string }).folder ?? "";
      return view(`new${made}`, { folder, no_folder: !folder });
    },
    "/team/members/from-model": (_p: string, body: unknown) => {
      const b = body as { provider: string; model: string };
      const m = MEMBERS[`${b.provider}::${b.model}`];
      return { name: m.name, provider: b.provider, model: b.model, label: m.label, roles: ["code", "test", "read"], created: true };
    },
  };
  stubTurn(openStream);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
});

const modelChip = () => within(screen.getByTestId("harness-new-model")).getByRole("button");
const startModel = () => screen.getByTestId("harness-new-model").getAttribute("data-choice");
const creates = () => api.calls.filter((c) => c.method === "POST" && c.path === "/build/sessions").map((c) => c.body);

function send(words = "Fix the cart") {
  fireEvent.change(screen.getByTestId("harness-new-box"), { target: { value: words } });
  fireEvent.click(screen.getByTestId("harness-new-send"));
}

/** Plan, from the permission chip's own menu (as a user picks it). */
function pickPlan() {
  fireEvent.click(screen.getByTestId("permission-chip"));
  const plan = within(screen.getByTestId("permission-menu"))
    .getAllByTestId("permission-item")
    .find((b) => b.getAttribute("data-mode") === "plan")!;
  fireEvent.click(plan);
}

/** Add a worker on `provider`/`model` through the Team chip's own panel. */
async function addWorker(provider: string, model: string) {
  fireEvent.click(await screen.findByTestId("harness-team"));
  fireEvent.click(await screen.findByTestId("harness-team-add-worker"));
  const picker = await screen.findByTestId("harness-team-picker");
  fireEvent.click(within(picker).getAllByTestId("harness-team-group").find((g) => g.getAttribute("data-group") === provider)!);
  fireEvent.click(within(picker).getAllByTestId("harness-team-model").find((b) => b.getAttribute("data-model") === model)!);
  await waitFor(() => expect(screen.getAllByTestId("harness-team-worker").some((w) => w.getAttribute("data-name") === MEMBERS[`${provider}::${model}`].name)).toBe(true));
  fireEvent.click(screen.getByTestId("harness-team-done"));
}

function renderView() {
  const pickFolder = vi.fn(async () => ({ path: REPO }));
  (window as unknown as { ironjarvis?: unknown }).ironjarvis = { isDesktop: true, dialog: { pickFolder } };
  return render(
    <IjHarnessView initialSession={null} terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />,
  );
}

describe("Plan from the permission chip rides the ONE create", () => {
  it("the create says mode: plan (and nothing about Plan rides the first message)", async () => {
    renderView();
    await screen.findByTestId("harness-new-box");
    fireEvent.click(screen.getByTestId("harness-new-folder-choose"));
    await waitFor(() => expect(screen.getByTestId("harness-new-folder")).toHaveTextContent("shop"));
    pickPlan();
    expect(screen.getByTestId("permission-chip")).toHaveAttribute("data-plan", "true");
    send("plan the cart fix");
    await waitFor(() => expect(creates()).toHaveLength(1));
    expect(creates()[0]).toMatchObject({ folder: REPO, mode: "plan" });
    await waitFor(() => expect(turnBodies).toHaveLength(1), { timeout: 10_000 });
    expect("mode" in turnBodies[0]).toBe(false);
  });
});

describe("a first message refused before its turn started puts EVERY chip back", () => {
  it("the model and its account, the effort, Plan and the team come back, and the next Send carries them all", async () => {
    stubTurn(
      () =>
        new Response(JSON.stringify({ detail: "That account cannot start a session right now." }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }),
    );
    renderView();
    await screen.findByTestId("harness-new-box");
    fireEvent.click(screen.getByTestId("harness-new-folder-choose"));
    await waitFor(() => expect(screen.getByTestId("harness-new-folder")).toHaveTextContent("shop"));
    await waitFor(() => expect(api.calls.some((c) => c.path === "/build/accounts")).toBe(true));
    // Opus under the Home account, a high effort, Plan, a worker on GLM.
    fireEvent.click(modelChip());
    fireEvent.click(await screen.findByTestId("model-account-c-home"));
    fireEvent.click(within(screen.getByTestId("model-sub")).getByText("Opus 5.5"));
    fireEvent.change(screen.getByTestId("reasoning-level"), { target: { value: "high" } });
    pickPlan();
    await addWorker("ollama", "glm-local");
    send("fix the cart");
    // v1.452.0 (readiness item 43): the branch switch is off by default.
    const first = { folder: REPO, branch: false, provider: "claude-cli", model: "opus", account: "c-home", reasoning: "high", mode: "plan", team: { workers: ["glm-w"], reviewer: "" } };
    await waitFor(() => expect(creates()).toEqual([first]));
    // The refusal: the empty session is taken back and the box reopens.
    await waitFor(
      () => expect(api.calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual(["/build/sessions/new1?only_if_empty=1"]),
      { timeout: 10_000 },
    );
    await waitFor(() => expect(screen.getByTestId("harness-new-box")).toHaveValue("fix the cart"), { timeout: 10_000 });
    expect(await screen.findByTestId("harness-new-problem")).toHaveTextContent("That account cannot start a session");
    // Every chip as it was.
    expect(startModel()).toBe("claude-cli::opus");
    expect(screen.getByTestId("harness-new-model")).toHaveAttribute("data-account", "c-home");
    await waitFor(() => expect(screen.getByTestId("model-chip-account")).toHaveTextContent("Home"));
    expect((screen.getByTestId("reasoning-level") as HTMLSelectElement).value).toBe("high");
    expect(screen.getByTestId("permission-chip")).toHaveAttribute("data-plan", "true");
    await waitFor(() => expect(screen.getByTestId("harness-team")).toHaveAttribute("data-count", "2"));
    // The worker is named by its model, not by the member's slug.
    fireEvent.click(screen.getByTestId("harness-team"));
    expect(await screen.findByTestId("harness-team-worker")).toHaveTextContent("GLM local");
    fireEvent.click(screen.getByTestId("harness-team-done"));
    // Send again: the same create, every choice in it.
    stubTurn(openStream);
    fireEvent.click(screen.getByTestId("harness-new-send"));
    await waitFor(() => expect(creates()).toHaveLength(2));
    expect(creates()[1]).toEqual(first);
  });

  it("Local only comes back too", async () => {
    stubTurn(
      () =>
        new Response(JSON.stringify({ detail: "No." }), { status: 400, headers: { "Content-Type": "application/json" } }),
    );
    renderView();
    await screen.findByTestId("harness-new-box");
    fireEvent.click(screen.getByTestId("harness-new-local-only"));
    send("x");
    await waitFor(() => expect(creates()).toHaveLength(1));
    await waitFor(() => expect(screen.getByTestId("harness-new-box")).toHaveValue("x"), { timeout: 10_000 });
    expect(screen.getByTestId("harness-new-local-only")).toHaveAttribute("aria-pressed", "true");
  });
});

describe("Local only takes back a cloud team member the box put on the team", () => {
  it("the member goes, in words, and the create says the session works alone", async () => {
    const got: StartRequest[] = [];
    render(<HarnessStartComposer initialFolder={REPO} onStart={async (req) => (got.push(req), null)} />);
    await addWorker("codex-cli", "gpt-6");
    expect(screen.getByTestId("harness-team")).toHaveAttribute("data-count", "2");
    fireEvent.click(screen.getByTestId("harness-new-local-only"));
    expect(screen.getByTestId("harness-new-local-note")).toHaveTextContent(
      "GPT-6 leaves this PC, so it is not used while Local only is on.",
    );
    await waitFor(() => expect(screen.getByTestId("harness-team")).toHaveAttribute("data-count", "0"));
    send();
    await waitFor(() => expect(got).toHaveLength(1));
    expect(got[0].team).toBeNull();
    expect(got[0].local_only).toBe(true);
  });

  it("a local model whose box is down for a moment is never said to leave this PC (member and Try on pick kept)", async () => {
    const got: StartRequest[] = [];
    render(<HarnessStartComposer initialFolder={REPO} onStart={async (req) => (got.push(req), null)} />);
    await addWorker("ollama", "glm-local");
    const chip = screen.getByTestId("harness-try-on");
    await waitFor(() => expect(chip).not.toHaveAttribute("aria-disabled"));
    fireEvent.click(chip);
    const group = screen.getAllByTestId("harness-try-on-group").find((g) => g.getAttribute("data-group") === "ollama")!;
    fireEvent.click(group);
    fireEvent.click(
      (screen.getAllByTestId("harness-try-on-row") as HTMLInputElement[]).find((r) => r.getAttribute("data-model") === "glm-local")!,
    );
    fireEvent.click(screen.getByTestId("harness-try-on-done"));
    const glm = ROWS[2] as { available: boolean };
    glm.available = false;
    try {
      fireEvent.click(screen.getByTestId("harness-new-local-only"));
      expect(screen.queryByTestId("harness-new-local-note")).toBeNull();
      expect(screen.getByTestId("harness-team")).toHaveAttribute("data-count", "2");
      expect(screen.getByTestId("harness-try-on")).toHaveTextContent("Try on GLM local");
    } finally {
      glm.available = true;
    }
  });

  it("a member on a model that stays here is kept", async () => {
    const got: StartRequest[] = [];
    render(<HarnessStartComposer initialFolder={REPO} onStart={async (req) => (got.push(req), null)} />);
    await addWorker("ollama", "glm-local");
    fireEvent.click(screen.getByTestId("harness-new-local-only"));
    expect(screen.queryByTestId("harness-new-local-note")).toBeNull();
    send();
    await waitFor(() => expect(got).toHaveLength(1));
    expect(got[0].team).toEqual({ workers: ["glm-w"], reviewer: "" });
  });
});

describe("a level is a model's: never saved as a folder's default with no model", () => {
  it("an effort picked on the default model is not offered as the folder's default", async () => {
    DAEMON.health = { default_provider: "claude-cli", default_model: "opus", providers: [] };
    render(<HarnessStartComposer initialFolder={REPO} onStart={async () => null} />);
    // The default model offers levels: the chip is there with no model picked.
    const level = (await screen.findByTestId("reasoning-level")) as HTMLSelectElement;
    expect(startModel()).toBe("");
    fireEvent.change(level, { target: { value: "high" } });
    expect(level.value).toBe("high");
    expect(screen.queryByTestId("harness-new-save-defaults")).toBeNull();
  });
});

describe("an account list that got no answer is read again", () => {
  it("opening the model menu asks again, and the accounts are offered", async () => {
    let reads = 0;
    api.gets["/build/accounts"] = () => {
      reads += 1;
      return reads === 1 ? new api.ApiError("Iron Jarvis isn't responding", 0) : ACCOUNTS;
    };
    render(<HarnessStartComposer onStart={async () => null} />);
    await waitFor(() => expect(reads).toBe(1));
    fireEvent.click(modelChip());
    await waitFor(() => expect(reads).toBe(2));
    expect(await screen.findByTestId("model-account-c-home")).toBeInTheDocument();
  });
});
