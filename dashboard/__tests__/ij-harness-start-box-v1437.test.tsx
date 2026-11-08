/**
 * v1.437.0 — Chat › Code's NEW-SESSION BOX offers what a session's composer
 * offers.
 *
 * The user's report: an existing coding session's box has, in one row, the
 * permission chip, "Try on…", "Team · N", the effort chip and the model chip
 * with its account; a NEW session ("What should we build?") had only a folder
 * chip, the permission chip, a plain "Default model" select and Local only —
 * "Try on is not available at all in a new chat", nor Team, effort or the
 * account.
 *
 * Pinned here, on the REAL HarnessStartComposer (and, for the first message,
 * the real IjHarnessView + HarnessSession) against an api double:
 *
 *  - the row is the SESSION COMPOSER'S OWN: the same components (the model
 *    menu WITH the accounts — never a select), the same one-row fit group;
 *  - every choice reaches the session: the account and the model, the effort
 *    (only where the model offers levels; a touched level wins over the
 *    folder's, a level the next model does not offer is dropped), the team
 *    (set in the box with no session yet — no PATCH; untouched = the folder's
 *    saved team, which the create applies), Plan — all on the ONE create; the
 *    "Try on…" picks on the FIRST message, never the create;
 *  - Try on says WHY it cannot be used (no folder, a folder outside a git
 *    repository, a session that starts by planning) instead of hiding;
 *  - Local only never offers a cloud model, a cloud pick to try on, and takes
 *    back what was chosen before it, in words;
 *  - a first message the daemon refused before its turn started comes back
 *    with its picks, and a refusal about Try on is said under the box.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
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
  health: { default_provider: "mock", default_model: "mock-1", providers: [] },
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

import {
  HarnessStartComposer,
  START_LOCAL_ONLY_MENU_LINE,
  TRY_ON_CHECKING,
  TRY_ON_NEEDS_FOLDER,
  TRY_ON_NOT_A_REPO,
  TRY_ON_PLANNING,
  TRY_ON_WORDS_ONLY,
  isFolderProblem,
  localOnlyDroppedWords,
  startCreateBody,
  tryOnAvailability,
  type StartRequest,
} from "@/components/harness/HarnessStartComposer";
import { IjHarnessView } from "@/components/harness/IjHarnessView";
import { resetTurnCache } from "@/lib/turnCache";
import { resetFolderDefaultsCache } from "@/lib/folderDefaults";
import type { FolderGit } from "@/lib/buildGit";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";

const REPO = "C:\\work\\shop";
const NOTES = "C:\\notes";
const gitPath = (f: string) => `/build/git/folder?path=${encodeURIComponent(f)}`;
const teamPath = (f: string) => `/build/folders/team?folder=${encodeURIComponent(f)}`;
const defaultsPath = (f: string) => `/build/folders/defaults?folder=${encodeURIComponent(f)}`;
const GIT_OK = { git: true, repo: true, branch: "main", can_branch: true, note: "", worktrees_dir: "" };
const NOT_A_REPO = { git: true, repo: false, branch: "", can_branch: false, note: "", worktrees_dir: "" };
const ACCOUNTS = {
  claude: [
    { id: "c-work", name: "Work", default: true, status: "ready", plan_label: "Max" },
    { id: "c-home", name: "Home", status: "ready", plan_label: "Pro" },
  ],
  codex: [],
  grok: [],
  iron_proxy: { enabled: true, running: true },
};
/** The daemon's own refusal of picks on a folder outside git (harness/attempts NEEDS_GIT). */
const NEEDS_GIT =
  "Trying on several models needs a git folder: each attempt works on its own branch. " +
  "This session's folder is not in a git repository, so nothing started.";

const terminal: SessionTerminalApi = { find: () => null, open: async () => null, render: () => <div />, close: () => {} };

const read = (...parts: string[]) => readFileSync(join(process.cwd(), ...parts), "utf8").replace(/\r\n/g, "\n");

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

/** The turn's fetch: every call's body is kept. */
const turnBodies: Record<string, unknown>[] = [];
function stubTurn(answer: () => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (/\/build\/sessions\/new1\/turn/.test(String(url))) {
        turnBodies.push(JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>);
        return answer();
      }
      return new Response("{}", { status: 200 });
    }),
  );
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetTurnCache();
  resetFolderDefaultsCache();
  window.history.replaceState(null, "", "/chat?view=code");
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
  api.calls = [];
  turnBodies.length = 0;
  api.gets = {
    "/build/sessions": { folders: [], work_root: "C:\\Users\\VR\\Documents\\Iron Jarvis" },
    "/build/sessions?archived=1": { folders: [] },
    "/build/accounts": ACCOUNTS,
    [teamPath("")]: { folder: "", team: null },
    [teamPath(REPO)]: { folder: REPO, team: null },
    [teamPath(NOTES)]: { folder: NOTES, team: null },
    [gitPath(REPO)]: GIT_OK,
    [gitPath(NOTES)]: NOT_A_REPO,
  };
  api.posts = {
    "/build/sessions": (_p: string, body: unknown) =>
      view("new1", {
        folder: (body as { folder?: string }).folder ?? "",
        no_folder: !(body as { folder?: string }).folder,
      }),
  };
  stubTurn(openStream);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
});

function renderBox(props: Partial<Parameters<typeof HarnessStartComposer>[0]> = {}) {
  const got: StartRequest[] = [];
  render(<HarnessStartComposer onStart={async (req) => (got.push(req), null)} {...props} />);
  return got;
}

const modelChip = () => within(screen.getByTestId("harness-new-model")).getByRole("button");
const startModel = () => screen.getByTestId("harness-new-model").getAttribute("data-choice");

/** Pick a model by typing its name in the model menu and pressing Enter. */
function pickByName(name: string) {
  fireEvent.click(modelChip());
  const filter = screen.getByTestId("model-filter");
  fireEvent.change(filter, { target: { value: name } });
  fireEvent.keyDown(filter, { key: "Enter" });
}

function send(words = "Fix the cart") {
  fireEvent.change(screen.getByTestId("harness-new-box"), { target: { value: words } });
  fireEvent.click(screen.getByTestId("harness-new-send"));
}

/** Tick models in the open "Try on…" list (each provider's pop-out). */
function tickTryOn(...pairs: [string, string][]) {
  for (const [provider, model] of pairs) {
    const group = screen.getAllByTestId("harness-try-on-group").find((g) => g.getAttribute("data-group") === provider)!;
    if (group.getAttribute("aria-expanded") !== "true") fireEvent.click(group);
    const row = (screen.getAllByTestId("harness-try-on-row") as HTMLInputElement[]).find(
      (r) => r.getAttribute("data-provider") === provider && r.getAttribute("data-model") === model,
    )!;
    fireEvent.click(row);
  }
}

/* ------------------------------------------------------------ the row */

describe("the new-session box carries the session composer's own row", () => {
  it("permission chip, Try on, Team, the model menu with its chip and Send sit in ONE toolbar inside the box", async () => {
    renderBox({ initialFolder: REPO });
    const bar = screen.getByTestId("harness-new-toolbar");
    expect(within(bar).getByTestId("permission-chip")).toBeInTheDocument();
    expect(bar.querySelector("#harness-new-posture")).not.toBeNull();
    expect(within(bar).getByTestId("harness-try-on")).toBeInTheDocument();
    expect(await within(bar).findByTestId("harness-team")).toHaveTextContent("Team");
    expect(within(bar).getByTestId("model-chip-words")).toBeInTheDocument();
    expect(within(screen.getByTestId("harness-new-actions")).getByTestId("harness-new-send")).toBeInTheDocument();
    // Never a second model list: no select is the model chip.
    expect(document.querySelector('select[data-testid="harness-new-model"]')).toBeNull();
    // Under the box stay the folder chip and Local only.
    const chips = screen.getByTestId("harness-new-chips");
    expect(within(chips).getByTestId("harness-new-folder")).toBeInTheDocument();
    expect(within(chips).getByTestId("harness-new-local-only")).toBeInTheDocument();
    expect(within(chips).queryByTestId("permission-chip")).toBeNull();
  });

  it("is the session's own fit group in HARNESS_FIT_ORDER (the same classes and steps)", () => {
    const s = read("components", "harness", "HarnessStartComposer.tsx");
    expect(s).toContain("useToolbarFit(HARNESS_FIT_ORDER)");
    expect(s).toContain("ref={fitRef}");
    expect(s).toMatch(/group\/fit flex min-w-0 flex-wrap items-center[^"]*data-\[fit-row\]:flex-nowrap/);
    expect(s).toContain("group-data-[fit-row]/fit:justify-start");
    expect(s).toContain('iconOnly={fitOn("ask")}');
    expect(s).toContain('iconOnly={fitOn("tryon")}');
    expect(s).toContain('short={fitOn("short")}');
    // The SAME model menu as the session (with the accounts): companyMenu.
    expect(s).toContain("companyMenu(menuModels, accounts, startAccount, pick)");
    expect(s).toContain("companies={companies}");
  });
});

/* -------------------------------------------------- the model and account */

describe("the model menu WITH the accounts", () => {
  it("a model picked under an account names it on the chip and rides the create", async () => {
    const got = renderBox();
    await waitFor(() => expect(api.calls.some((c) => c.path === "/build/accounts")).toBe(true));
    fireEvent.click(modelChip());
    fireEvent.click(await screen.findByTestId("model-account-c-home"));
    fireEvent.click(within(screen.getByTestId("model-sub")).getByText("Opus 5.5"));
    expect(startModel()).toBe("claude-cli::opus");
    expect(screen.getByTestId("harness-new-model").getAttribute("data-account")).toBe("c-home");
    expect(screen.getByTestId("model-chip-words")).toHaveTextContent("Opus 5.5");
    expect(screen.getByTestId("model-chip-account")).toHaveTextContent("Home");
    send();
    await waitFor(() => expect(got).toHaveLength(1));
    expect(got[0]).toMatchObject({ provider: "claude-cli", model: "opus", account: "c-home" });
    expect(startCreateBody(got[0])).toMatchObject({ provider: "claude-cli", model: "opus", account: "c-home" });
  });

  it("another model with no account clears the account (it never rides a model of another vendor)", async () => {
    const got = renderBox();
    fireEvent.click(modelChip());
    fireEvent.click(await screen.findByTestId("model-account-c-home"));
    fireEvent.click(within(screen.getByTestId("model-sub")).getByText("Opus 5.5"));
    pickByName("GLM local");
    expect(startModel()).toBe("ollama::glm-local");
    expect(screen.queryByTestId("model-chip-account")).toBeNull();
    send();
    await waitFor(() => expect(got).toHaveLength(1));
    expect(got[0].account).toBeUndefined();
    expect(startCreateBody(got[0]).account).toBeUndefined();
  });

  it("an untouched folder model shows the account the daemon will start it on (its vendor's Default) and sends none", async () => {
    api.gets[defaultsPath(REPO)] = { folder: REPO, defaults: { code: { provider: "claude-cli", model: "opus" } } };
    const got = renderBox({ initialFolder: REPO });
    await waitFor(() => expect(startModel()).toBe("claude-cli::opus"));
    await waitFor(() => expect(screen.getByTestId("model-chip-account")).toHaveTextContent("Work"));
    send();
    await waitFor(() => expect(got).toHaveLength(1));
    expect(got[0].account).toBeUndefined();
  });
});

/* ------------------------------------------------------------ the effort */

describe("the effort chip", () => {
  it("shows only for a model that offers levels; a level picked rides the create", async () => {
    const got = renderBox();
    // The default model (the demo) offers none.
    expect(screen.queryByTestId("reasoning-level")).toBeNull();
    pickByName("GLM local");
    expect(screen.queryByTestId("reasoning-level")).toBeNull();
    pickByName("Opus 5.5");
    const level = within(screen.getByTestId("harness-new-actions")).getByTestId("reasoning-level") as HTMLSelectElement;
    expect(Array.from(level.options).map((o) => o.value)).toContain("xhigh");
    fireEvent.change(level, { target: { value: "high" } });
    send();
    await waitFor(() => expect(got).toHaveLength(1));
    expect(got[0].reasoning).toBe("high");
    expect(startCreateBody(got[0]).reasoning).toBe("high");
  });

  it("a level the next model does not offer is dropped (never sent to a model that cannot take it)", async () => {
    const got = renderBox();
    pickByName("Opus 5.5");
    fireEvent.change(screen.getByTestId("reasoning-level"), { target: { value: "xhigh" } });
    pickByName("GPT-6");
    const level = screen.getByTestId("reasoning-level") as HTMLSelectElement;
    expect(Array.from(level.options).map((o) => o.value)).not.toContain("xhigh");
    send();
    await waitFor(() => expect(got).toHaveLength(1));
    expect(got[0].reasoning).toBeUndefined();
  });

  it("a model that offers no levels takes none (its chip is gone, and nothing is sent unseen)", async () => {
    const got = renderBox();
    pickByName("Opus 5.5");
    fireEvent.change(screen.getByTestId("reasoning-level"), { target: { value: "high" } });
    pickByName("GLM local");
    expect(screen.queryByTestId("reasoning-level")).toBeNull();
    send();
    await waitFor(() => expect(got).toHaveLength(1));
    expect(got[0].reasoning).toBeUndefined();
  });

  it("untouched it follows the folder's level; a level the user picks wins", async () => {
    api.gets[defaultsPath(REPO)] = {
      folder: REPO,
      defaults: { code: { provider: "claude-cli", model: "opus", reasoning: "low" } },
    };
    const got = renderBox({ initialFolder: REPO });
    await waitFor(() => expect((screen.getByTestId("reasoning-level") as HTMLSelectElement).value).toBe("low"));
    fireEvent.change(screen.getByTestId("reasoning-level"), { target: { value: "high" } });
    send();
    await waitFor(() => expect(got).toHaveLength(1));
    expect(got[0].reasoning).toBe("high");
  });
});

/* -------------------------------------------------------------- the team */

describe("Team, before the session exists", () => {
  it("a worker added in the box rides the create — no session is patched", async () => {
    api.posts["/team/members/from-model"] = {
      name: "glm-w",
      provider: "ollama",
      model: "glm-local",
      label: "GLM local",
      roles: ["code", "test", "read"],
      created: true,
    };
    const got = renderBox({ initialFolder: REPO });
    fireEvent.click(await screen.findByTestId("harness-team"));
    fireEvent.click(await screen.findByTestId("harness-team-add-worker"));
    const picker = await screen.findByTestId("harness-team-picker");
    fireEvent.click(within(picker).getAllByTestId("harness-team-group").find((g) => g.getAttribute("data-group") === "ollama")!);
    fireEvent.click(within(picker).getAllByTestId("harness-team-model").find((b) => b.getAttribute("data-model") === "glm-local")!);
    // One worker and the lead (the chip counts everyone on the team).
    await waitFor(() => expect(screen.getByTestId("harness-team")).toHaveAttribute("data-count", "2"));
    // The member is named by its model, and has no "not known" state yet.
    const worker = screen.getByTestId("harness-team-worker");
    expect(worker).toHaveTextContent("GLM local");
    expect(within(worker).queryByTestId("harness-team-state")).toBeNull();
    fireEvent.click(screen.getByTestId("harness-team-done"));
    send();
    await waitFor(() => expect(got).toHaveLength(1));
    expect(got[0].team).toEqual({ workers: ["glm-w"], reviewer: "" });
    expect(startCreateBody(got[0]).team).toEqual({ workers: ["glm-w"], reviewer: "" });
    expect(api.calls.filter((c) => c.method === "PATCH")).toEqual([]);
  });

  it("untouched it shows the folder's saved team and sends none (the create applies it); No team sends null", async () => {
    api.gets[teamPath(REPO)] = { folder: REPO, team: { workers: ["qwen"], reviewer: "" } };
    const got = renderBox({ initialFolder: REPO });
    await waitFor(() => expect(screen.getByTestId("harness-team")).toHaveAttribute("data-count", "2"));
    send("first");
    await waitFor(() => expect(got).toHaveLength(1));
    expect("team" in got[0]).toBe(false);
    expect("team" in startCreateBody(got[0])).toBe(false);
    cleanup();
    const got2 = renderBox({ initialFolder: REPO });
    fireEvent.click(await screen.findByTestId("harness-team"));
    fireEvent.click(await screen.findByTestId("harness-team-none"));
    await waitFor(() => expect(screen.getByTestId("harness-team")).toHaveAttribute("data-count", "0"));
    fireEvent.click(screen.getByTestId("harness-team-done"));
    send("second");
    await waitFor(() => expect(got2).toHaveLength(1));
    expect(got2[0].team).toBeNull();
    expect(startCreateBody(got2[0]).team).toBeNull();
  });
});

/* ------------------------------------------------------------- Try on… */

describe("Try on in a new session", () => {
  it("on a git folder the picks ride the request — and never the create", async () => {
    const got = renderBox({ initialFolder: REPO });
    const chip = screen.getByTestId("harness-try-on");
    await waitFor(() => expect(chip).not.toHaveAttribute("aria-disabled"));
    fireEvent.click(chip);
    tickTryOn(["claude-cli", "opus"], ["ollama", "glm-local"]);
    fireEvent.click(screen.getByTestId("harness-try-on-done"));
    expect(screen.getByTestId("harness-try-on")).toHaveTextContent("Try on 2 models");
    send();
    await waitFor(() => expect(got).toHaveLength(1));
    expect(got[0].tryOn?.map((p) => `${p.provider}::${p.model}`)).toEqual(["claude-cli::opus", "ollama::glm-local"]);
    const body = startCreateBody(got[0]);
    expect("try_on" in body || "tryOn" in body).toBe(false);
  });

  it("with no folder it says why (never a chip that silently does nothing)", async () => {
    renderBox();
    const chip = screen.getByTestId("harness-try-on");
    expect(chip).toHaveAttribute("aria-disabled", "true");
    expect(chip).toHaveAttribute("title", TRY_ON_NEEDS_FOLDER);
    fireEvent.click(chip);
    expect(screen.getByTestId("harness-try-on-unavailable")).toHaveTextContent(TRY_ON_NEEDS_FOLDER);
    expect(screen.queryByTestId("harness-try-on-filter")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId("harness-try-on-done")));
  });

  it("a folder outside a git repository says so; picks made on a git folder go when the folder changes", async () => {
    renderBox({ initialFolder: REPO });
    const chip = screen.getByTestId("harness-try-on");
    await waitFor(() => expect(chip).not.toHaveAttribute("aria-disabled"));
    fireEvent.click(chip);
    tickTryOn(["ollama", "glm-local"]);
    fireEvent.click(screen.getByTestId("harness-try-on-done"));
    // The folder chip's own picker (a browser: the app's folder dialog) is
    // not driven here — Clear then a folder outside git, through the seed's
    // way in: a fresh box on NOTES.
    cleanup();
    renderBox({ initialFolder: NOTES });
    await waitFor(() => expect(screen.getByTestId("harness-try-on")).toHaveAttribute("title", TRY_ON_NOT_A_REPO));
    expect(screen.getByTestId("harness-try-on")).toHaveTextContent("Try on…");
  });

  it("a session that starts by planning says so, and its picks are not kept", async () => {
    renderBox({
      initialFolder: REPO,
      seed: {
        request: {
          folder: REPO,
          mode: "plan",
          text: "x",
          files: [],
          tryOn: [{ provider: "ollama", model: "glm-local", label: "GLM local" }],
        },
        problem: "",
      },
    });
    await waitFor(() => expect(screen.getByTestId("harness-try-on")).toHaveAttribute("title", TRY_ON_PLANNING));
    expect(screen.getByTestId("harness-try-on")).toHaveTextContent("Try on…");
  });

  it("files with picks are refused in words before anything is made", async () => {
    const got = renderBox({
      initialFolder: REPO,
      seed: {
        request: {
          folder: REPO,
          text: "x",
          files: [{ name: "a.png", path: "C:\\up\\a.png", bytes: 3, label: "a.png" }],
          tryOn: [{ provider: "ollama", model: "glm-local", label: "GLM local" }],
        },
        problem: "",
      },
    });
    await waitFor(() => expect(screen.getByTestId("harness-try-on")).toHaveTextContent("Try on GLM local"));
    send("look at this");
    expect(await screen.findByTestId("harness-new-problem")).toHaveTextContent(TRY_ON_WORDS_ONLY);
    expect(got).toEqual([]);
  });
});

/* ---------------------------------------------------------- Local only */

describe("Local only never offers a cloud model", () => {
  it("the model menu lists only models that stay here, and says so", async () => {
    renderBox();
    await waitFor(() => expect(api.calls.some((c) => c.path === "/build/accounts")).toBe(true));
    fireEvent.click(screen.getByTestId("harness-new-local-only"));
    fireEvent.click(modelChip());
    expect(screen.getByTestId("model-menu-local-only")).toHaveTextContent(START_LOCAL_ONLY_MENU_LINE);
    expect(screen.queryByTestId("model-account-c-work")).toBeNull();
    expect(screen.queryByTestId("model-way-claude-cli")).toBeNull();
    const filter = screen.getByTestId("model-filter");
    fireEvent.change(filter, { target: { value: "Opus" } });
    expect(screen.queryAllByTestId("model-match")).toEqual([]);
    fireEvent.change(filter, { target: { value: "GLM" } });
    expect(screen.getAllByTestId("model-match")).toHaveLength(1);
  });

  it("turning it on takes back a cloud model and cloud picks to try on, in words", async () => {
    const got = renderBox({ initialFolder: REPO });
    pickByName("Opus 5.5");
    const chip = screen.getByTestId("harness-try-on");
    await waitFor(() => expect(chip).not.toHaveAttribute("aria-disabled"));
    fireEvent.click(chip);
    tickTryOn(["codex-cli", "gpt-6"], ["ollama", "glm-local"]);
    fireEvent.click(screen.getByTestId("harness-try-on-done"));
    fireEvent.click(screen.getByTestId("harness-new-local-only"));
    expect(screen.getByTestId("harness-new-local-note")).toHaveTextContent(
      "Opus 5.5, GPT-6 leave this PC, so they are not used while Local only is on.",
    );
    expect(startModel()).toBe("");
    expect(screen.getByTestId("harness-try-on")).toHaveTextContent("Try on GLM local");
    // The "Try on…" list offers only what stays here.
    fireEvent.click(screen.getByTestId("harness-try-on"));
    expect(screen.getAllByTestId("harness-try-on-group").map((g) => g.getAttribute("data-group"))).toEqual(["ollama"]);
    fireEvent.click(screen.getByTestId("harness-try-on-done"));
    send();
    await waitFor(() => expect(got).toHaveLength(1));
    expect(got[0].provider).toBeUndefined();
    expect(got[0].local_only).toBe(true);
    expect(got[0].tryOn?.map((p) => p.provider)).toEqual(["ollama"]);
  });
});

/* --------------------------------------------------- the first message */

describe("the first message of the session the box makes", () => {
  function renderView() {
    return render(
      <IjHarnessView initialSession={null} terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />,
    );
  }

  async function chooseRepoAndPick() {
    const pickFolder = vi.fn(async () => ({ path: REPO }));
    (window as unknown as { ironjarvis?: unknown }).ironjarvis = { isDesktop: true, dialog: { pickFolder } };
    renderView();
    await screen.findByTestId("harness-new-box");
    fireEvent.click(screen.getByTestId("harness-new-folder-choose"));
    const chip = screen.getByTestId("harness-try-on");
    await waitFor(() => expect(chip).not.toHaveAttribute("aria-disabled"));
    fireEvent.click(chip);
    tickTryOn(["claude-cli", "opus"], ["ollama", "glm-local"]);
    fireEvent.click(screen.getByTestId("harness-try-on-done"));
  }

  it("carries the picks as try_on on the session's very first turn (and the create carries none)", async () => {
    await chooseRepoAndPick();
    send("fix add in calc.py");
    await waitFor(() => expect(turnBodies).toHaveLength(1), { timeout: 10_000 });
    expect(turnBodies[0].text).toBe("fix add in calc.py");
    expect(turnBodies[0].try_on).toEqual([
      { provider: "claude-cli", model: "opus" },
      { provider: "ollama", model: "glm-local" },
    ]);
    const created = api.calls.find((c) => c.method === "POST" && c.path === "/build/sessions");
    // v1.452.0 (readiness item 43): the branch switch is off by default.
    expect(created?.body).toEqual({ folder: REPO, branch: false });
  });

  it("refused before its turn started: back in the box WITH its picks, the refusal under the box", async () => {
    stubTurn(
      () =>
        new Response(JSON.stringify({ detail: NEEDS_GIT }), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        }),
    );
    await chooseRepoAndPick();
    send("fix add in calc.py");
    // The empty session the box made is taken back (the daemon's own guard)…
    await waitFor(
      () =>
        expect(api.calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual([
          "/build/sessions/new1?only_if_empty=1",
        ]),
      { timeout: 10_000 },
    );
    // … and the box comes back with the words.
    await waitFor(() => expect(screen.getByTestId("harness-new-box")).toHaveValue("fix add in calc.py"), {
      timeout: 10_000,
    });
    expect(await screen.findByTestId("harness-new-problem")).toHaveTextContent("Trying on several models");
    expect(screen.getByTestId("harness-new-folder")).not.toHaveAttribute("data-problem");
    await waitFor(() => expect(screen.getByTestId("harness-try-on")).toHaveTextContent("Try on 2 models"));
  });
});

/* ------------------------------------------------------------ the rules */

describe("the rules, pure", () => {
  it("startCreateBody: the account only with a model, Plan, the team present or not — never the picks", () => {
    const base: StartRequest = { folder: "", text: "x", files: [] };
    expect(startCreateBody({ ...base, account: "c-home" })).toEqual({});
    expect(startCreateBody({ ...base, provider: "claude-cli", model: "opus", account: "c-home" })).toEqual({
      provider: "claude-cli",
      model: "opus",
      account: "c-home",
    });
    expect(startCreateBody({ ...base, mode: "plan" })).toEqual({ mode: "plan" });
    expect(startCreateBody({ ...base, team: null })).toEqual({ team: null });
    expect(startCreateBody({ ...base, team: { workers: [], reviewer: "x" } })).toEqual({ team: null });
    expect(startCreateBody({ ...base, team: { workers: ["w"], reviewer: "" } })).toEqual({
      team: { workers: ["w"], reviewer: "" },
    });
    expect(startCreateBody({ ...base, tryOn: [{ provider: "ollama", model: "m", label: "m" }] })).toEqual({});
  });

  it("tryOnAvailability: a folder in a git repository that can branch, not planning", () => {
    const git = (over: Partial<FolderGit> = {}): FolderGit => ({
      git: true,
      repo: true,
      branch: "main",
      canBranch: true,
      note: "",
      worktreesDir: "",
      ...over,
    });
    expect(tryOnAvailability({ folder: null, git: null, gitKnown: false, plan: false })).toEqual({
      why: TRY_ON_NEEDS_FOLDER,
      settled: true,
    });
    expect(tryOnAvailability({ folder: REPO, git: null, gitKnown: false, plan: false })).toEqual({
      why: TRY_ON_CHECKING,
      settled: false,
    });
    expect(tryOnAvailability({ folder: REPO, git: git({ repo: false }), gitKnown: true, plan: false }).why).toBe(
      TRY_ON_NOT_A_REPO,
    );
    expect(
      tryOnAvailability({ folder: REPO, git: git({ canBranch: false, note: "HEAD is detached." }), gitKnown: true, plan: false })
        .why,
    ).toMatch(/^HEAD is detached\. Try on needs one/);
    expect(tryOnAvailability({ folder: REPO, git: git(), gitKnown: true, plan: true }).why).toBe(TRY_ON_PLANNING);
    expect(tryOnAvailability({ folder: REPO, git: git(), gitKnown: true, plan: false })).toEqual({ why: "", settled: true });
  });

  it("a refusal about Try on is not the folder's; the folder's own refusals still are", () => {
    expect(isFolderProblem(NEEDS_GIT)).toBe(false);
    expect(isFolderProblem("This folder cannot be used: it is not writable")).toBe(true);
    expect(isFolderProblem("This repository cannot start a branch here.")).toBe(true);
  });

  it("what Local only took back, in words", () => {
    expect(localOnlyDroppedWords([])).toBe("");
    expect(localOnlyDroppedWords(["Opus 5.5"])).toBe("Opus 5.5 leaves this PC, so it is not used while Local only is on.");
  });
});
