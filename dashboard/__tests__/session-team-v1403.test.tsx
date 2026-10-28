/**
 * v1.403.0 — THE CODING-SESSION TEAM on the screen
 * (components/harness/HarnessTeam + HarnessSession, lib/sessionTeam).
 *
 * The user's decision: "Build the coding-session team: Lead plans and checks
 * but never edits files, Workers do the code, Reviewer checks every change.
 * If no worker can take a piece, the lead may do it and must say so."
 *
 * Driven through the REAL session screen against a mocked lib/api answering
 * in the contract's exact shapes:
 *
 *  - the box's Team chip opens a panel: the TEAM LEAD is the session's own
 *    model in the menu's words; picking a worker or the reviewer makes (or
 *    reuses) its member (`POST /team/members/from-model`) THEN sets the
 *    session's team (`PATCH /build/sessions/{id} {team}`); Remove, "No team"
 *    and the folder's default (`PUT /build/folders/team`) send exactly their
 *    shapes; a refusal is one sentence in the panel; a 404 (an Iron Jarvis
 *    without teams) hides the control entirely;
 *  - while a team is set the Try on chip says it runs each model alone;
 *  - a team reply says who led, worked and reviewed, and — in the warn tone,
 *    never folded away — every time the lead did a piece itself;
 *  - "reviewed twice" when the reviewer checked again;
 *  - a helper row in a team turn reads "Worker <model>" / "Reviewer <model>".
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
    answers: {} as Record<string, unknown | ((body: unknown) => unknown)>,
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : api.answers[`${method} ${path}`];
    const value = typeof hit === "function" ? (hit as (x: unknown) => unknown)(method === "GET" ? path : body) : hit;
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
    health: { default_provider: "claude-cli", default_model: "claude-opus-5-5", providers: [] },
    refresh: () => {},
  }),
}));

const MODELS = [
  { provider: "claude-cli", model: "claude-opus-5-5", label: "Opus 5.5", available: true, on_this_network: false },
  { provider: "codex-cli", model: "gpt-6-astra", label: "Codex", available: true, on_this_network: false },
  { provider: "ollama", model: "glm-5.3-flash", label: "glm-5.3-flash", available: true, on_this_network: true },
  { provider: "ollama", model: "qwen3.8-27b", label: "Qwen3.8-27B", available: true, on_this_network: true },
  { provider: "mock", model: "mock-1", available: true },
];
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: MODELS, usable: MODELS, loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { TeamReplyLine } from "@/components/harness/HarnessTeam";
import { SavedHelperRows, LiveHelperRows } from "@/components/harness/HarnessHelpers";
import { applyHelperFrame, type HelperView } from "@/lib/buildHelpers";
import { decodeFinish, decodeFinishReview, decodeMessage, decodeSessionView } from "@/lib/buildSessions";
import { finishTitle, finishWords, reviewWords } from "@/lib/stepWords";
import { CHAT_FIT_ORDER, FIT_STEPS, HARNESS_FIT_ORDER, applyFit } from "@/lib/toolbarFit";
import {
  EMPTY_ROSTER,
  decodeReplyTeam,
  decodeSessionTeam,
  decodeTeamNames,
  helperPart,
  helperTeamName,
  leadDidLines,
  sameTeam,
  teamChipWords,
  teamCount,
  teamLineWords,
  teamPatch,
  TRY_ON_ALONE,
} from "@/lib/sessionTeam";

/* ------------------------------------------------------------- fixtures */

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

const FOLDER = "C:\\work\\calc";
const FOLDER_PATH = `/build/folders/team?folder=${encodeURIComponent(FOLDER)}`;

const GLM = { name: "glm-5-3-flash", provider: "ollama", model: "glm-5.3-flash", label: "glm-5.3-flash", state: "ready" };
const QWEN = { name: "qwen3-8-27b", provider: "ollama", model: "qwen3.8-27b", label: "Qwen3.8-27B", state: "busy" };
const CODEX = { name: "codex", provider: "codex-cli", model: "gpt-6-astra", label: "Codex", state: "limit" };
const LEAD = { provider: "claude-cli", model: "claude-opus-5-5", label: "Opus 5.5" };

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Fix add",
    updated_at: "2026-10-28T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: FOLDER,
    folder_name: "calc",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: { provider: "claude-cli", model: "claude-opus-5-5" },
    messages: [],
    session_id: "build:s1",
    queued: [],
    turn_id: "",
    ...over,
  };
}

/** A view with a team set (the contract's `team` on the view, and the setup's
 *  names beside it). */
function teamView(workers: unknown[], reviewer: unknown | null, over: Record<string, unknown> = {}) {
  const names = {
    workers: workers.map((w) => (w as { name: string }).name),
    reviewer: reviewer ? (reviewer as { name: string }).name : "",
  };
  return view({
    setup: { provider: "claude-cli", model: "claude-opus-5-5", team: names },
    team: { lead: LEAD, workers, reviewer },
    ...over,
  });
}

beforeEach(() => {
  api.gets = {
    "/build/sessions/s1": view(),
    "/build/accounts": new api.ApiError("no such route", 404),
    [FOLDER_PATH]: { folder: FOLDER, team: null },
  };
  api.calls = [];
  api.answers = {};
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn());
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

const callsOf = (method: string, path: string) => api.calls.filter((c) => c.method === method && c.path === path);
const panel = () => screen.getByTestId("harness-team-panel");

async function openPanel() {
  const chip = await screen.findByTestId("harness-team");
  fireEvent.click(chip);
  return screen.findByTestId("harness-team-panel");
}

/** Pick a model in the open picker: its provider row, then the model. */
async function pickModel(group: string, model: string) {
  const picker = await screen.findByTestId("harness-team-picker");
  const row = within(picker)
    .getAllByTestId("harness-team-group")
    .find((el) => el.getAttribute("data-group") === group)!;
  fireEvent.click(row);
  const btn = within(picker)
    .getAllByTestId("harness-team-model")
    .find((el) => el.getAttribute("data-model") === model)!;
  fireEvent.click(btn);
}

/* ---------------------------------------------------------------- tests */

describe("an Iron Jarvis without teams", () => {
  it("a 404 from the folder's team read hides the Team control entirely", async () => {
    api.gets[FOLDER_PATH] = new api.ApiError("Not Found", 404);
    renderSession();
    await screen.findByTestId("harness-try-on");
    await waitFor(() => expect(callsOf("GET", FOLDER_PATH)).toHaveLength(1));
    await act(async () => {});
    expect(screen.queryByTestId("harness-team")).toBeNull();
  });

  it("(control) the same session with teams draws the chip", async () => {
    renderSession();
    const chip = await screen.findByTestId("harness-team");
    expect(chip).toHaveTextContent("Team");
    expect(chip.getAttribute("data-count")).toBe("0");
  });

  it("another refusal of the read still shows the control (only a 404 says no teams)", async () => {
    api.gets[FOLDER_PATH] = new api.ApiError("The folder is not readable.", 400);
    renderSession();
    expect(await screen.findByTestId("harness-team")).toBeTruthy();
  });

  it("a 404 from a change hides the control too (the panel closes)", async () => {
    api.answers["POST /team/members/from-model"] = new api.ApiError("Not Found", 404);
    renderSession();
    await openPanel();
    fireEvent.click(screen.getByTestId("harness-team-add-worker"));
    await pickModel("ollama", "glm-5.3-flash");
    await waitFor(() => expect(screen.queryByTestId("harness-team")).toBeNull());
    expect(screen.queryByTestId("harness-team-panel")).toBeNull();
    expect(callsOf("PATCH", "/build/sessions/s1")).toHaveLength(0);
  });
});

describe("the Team panel", () => {
  it("opens from the chip; the lead is the session's own model in the menu's words, and never edits files", async () => {
    renderSession();
    const chip = await screen.findByTestId("harness-team");
    expect(chip.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(chip);
    const p = await screen.findByTestId("harness-team-panel");
    expect(chip.getAttribute("aria-expanded")).toBe("true");
    const lead = within(p).getByTestId("harness-team-lead");
    expect(lead).toHaveTextContent("Team lead");
    expect(within(lead).getByTestId("harness-team-lead-model").textContent).toBe("Opus 5.5");
    expect(lead).toHaveTextContent("never edits files");
    // Never the raw id.
    expect(lead.textContent).not.toContain("claude-opus-5-5");
    expect(within(p).getByTestId("harness-team-workers")).toHaveTextContent("Workers");
    expect(within(p).getByTestId("harness-team-reviewer-part")).toHaveTextContent("Reviewer");
    // No worker yet: the reviewer waits for one; there is no "No team".
    expect((within(p).getByTestId("harness-team-add-reviewer") as HTMLButtonElement).disabled).toBe(true);
    expect(within(p).getByTestId("harness-team-reviewer-needs")).toBeTruthy();
    expect(within(p).queryByTestId("harness-team-none")).toBeNull();
  });

  it("is portaled, fixed and inside the window at a phone's width", async () => {
    const w = window.innerWidth;
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    try {
      renderSession();
      const p = await openPanel();
      expect(p.parentElement).toBe(document.body);
      expect(p.style.position).toBe("fixed");
      const left = Number.parseFloat(p.style.left);
      const width = Number.parseFloat(p.style.width);
      expect(left).toBeGreaterThanOrEqual(16);
      expect(left + width).toBeLessThanOrEqual(390 - 16);
    } finally {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: w });
    }
  });

  it("picking a worker makes its member, THEN sets the session's team", async () => {
    api.answers["POST /team/members/from-model"] = {
      name: "glm-5-3-flash", provider: "ollama", model: "glm-5.3-flash", label: "glm-5.3-flash",
      roles: ["read", "code", "test"], created: true,
    };
    api.answers["PATCH /build/sessions/s1"] = teamView([GLM], null);
    renderSession();
    await openPanel();
    fireEvent.click(screen.getByTestId("harness-team-add-worker"));
    // The picker lists the providers the Try on list does (never the demo).
    const picker = await screen.findByTestId("harness-team-picker");
    const groups = within(picker).getAllByTestId("harness-team-group").map((g) => g.getAttribute("data-group"));
    expect(groups).toContain("ollama");
    expect(groups).not.toContain("mock");
    await pickModel("ollama", "glm-5.3-flash");
    await waitFor(() => expect(callsOf("PATCH", "/build/sessions/s1")).toHaveLength(1));
    const posts = callsOf("POST", "/team/members/from-model");
    expect(posts).toEqual([
      { method: "POST", path: "/team/members/from-model", body: { provider: "ollama", model: "glm-5.3-flash", role: "worker" } },
    ]);
    // The member's NAME the route answered goes in the team.
    expect(callsOf("PATCH", "/build/sessions/s1")[0].body).toEqual({ team: { workers: ["glm-5-3-flash"], reviewer: "" } });
    const postAt = api.calls.findIndex((c) => c.method === "POST" && c.path === "/team/members/from-model");
    const patchAt = api.calls.findIndex((c) => c.method === "PATCH" && c.path === "/build/sessions/s1");
    expect(postAt).toBeLessThan(patchAt);
    // The view the PATCH answered is what the screen shows: the worker and
    // its state in words; the chip counts the lead and the worker.
    const row = await screen.findByTestId("harness-team-worker");
    expect(row).toHaveTextContent("glm-5.3-flash");
    expect(within(row).getByTestId("harness-team-state").textContent).toBe("ready");
    await waitFor(() => expect(screen.getByTestId("harness-team")).toHaveTextContent("Team · 2"));
    expect(screen.queryByTestId("harness-team-picker")).toBeNull();
    // The picked model's row is gone: the focus goes back to Add a worker,
    // never to the page.
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId("harness-team-add-worker")));
  });

  it("picking the reviewer sends role reviewer, and keeps the workers", async () => {
    api.gets["/build/sessions/s1"] = teamView([GLM], null);
    api.answers["POST /team/members/from-model"] = {
      name: "codex", provider: "codex-cli", model: "gpt-6-astra", label: "Codex", roles: ["review"], created: false,
    };
    api.answers["PATCH /build/sessions/s1"] = teamView([GLM], CODEX);
    renderSession();
    await openPanel();
    const add = screen.getByTestId("harness-team-add-reviewer") as HTMLButtonElement;
    expect(add.disabled).toBe(false);
    fireEvent.click(add);
    await pickModel("codex-cli", "gpt-6-astra");
    await waitFor(() => expect(callsOf("PATCH", "/build/sessions/s1")).toHaveLength(1));
    expect(callsOf("POST", "/team/members/from-model")[0].body).toEqual({
      provider: "codex-cli", model: "gpt-6-astra", role: "reviewer",
    });
    expect(callsOf("PATCH", "/build/sessions/s1")[0].body).toEqual({
      team: { workers: ["glm-5-3-flash"], reviewer: "codex" },
    });
    const rev = await screen.findByTestId("harness-team-reviewer");
    // A state that keeps it from working says so in words, in the warn tone.
    const state = within(rev).getByTestId("harness-team-state");
    expect(state.textContent).toBe("at its limit");
    expect(state.className).toContain("text-tone-warn");
    await waitFor(() => expect(screen.getByTestId("harness-team")).toHaveTextContent("Team · 3"));
  });

  it("Remove sends the team without it; the last worker removed is no team at all", async () => {
    api.gets["/build/sessions/s1"] = teamView([GLM, QWEN], CODEX);
    api.answers["PATCH /build/sessions/s1"] = (body: unknown) => {
      const t = (body as { team: { workers: string[]; reviewer: string } | null }).team;
      if (!t) return view();
      const all = [GLM, QWEN, CODEX];
      return teamView(
        t.workers.map((n) => all.find((m) => m.name === n)),
        t.reviewer ? all.find((m) => m.name === t.reviewer) : null,
      );
    };
    renderSession();
    await openPanel();
    expect(screen.getAllByTestId("harness-team-worker")).toHaveLength(2);
    // Two workers: the most there can be.
    expect(screen.queryByTestId("harness-team-add-worker")).toBeNull();
    const qwen = screen.getAllByTestId("harness-team-worker").find((r) => r.getAttribute("data-name") === "qwen3-8-27b")!;
    expect(within(qwen).getByTestId("harness-team-state").textContent).toBe("busy");
    fireEvent.click(within(qwen).getByTestId("harness-team-remove-worker"));
    await waitFor(() => expect(callsOf("PATCH", "/build/sessions/s1")).toHaveLength(1));
    expect(callsOf("PATCH", "/build/sessions/s1")[0].body).toEqual({ team: { workers: ["glm-5-3-flash"], reviewer: "codex" } });
    await waitFor(() => expect(screen.getAllByTestId("harness-team-worker")).toHaveLength(1));
    // The reviewer.
    fireEvent.click(screen.getByTestId("harness-team-remove-reviewer"));
    await waitFor(() => expect(callsOf("PATCH", "/build/sessions/s1")).toHaveLength(2));
    expect(callsOf("PATCH", "/build/sessions/s1")[1].body).toEqual({ team: { workers: ["glm-5-3-flash"], reviewer: "" } });
    await waitFor(() => expect(screen.queryByTestId("harness-team-reviewer")).toBeNull());
    // The last worker: the session works alone again.
    fireEvent.click(screen.getByTestId("harness-team-remove-worker"));
    await waitFor(() => expect(callsOf("PATCH", "/build/sessions/s1")).toHaveLength(3));
    expect(callsOf("PATCH", "/build/sessions/s1")[2].body).toEqual({ team: null });
    await waitFor(() => expect(screen.getByTestId("harness-team").getAttribute("data-count")).toBe("0"));
  });

  it("No team sets it back to working alone", async () => {
    api.gets["/build/sessions/s1"] = teamView([GLM], CODEX);
    api.answers["PATCH /build/sessions/s1"] = view();
    renderSession();
    await openPanel();
    fireEvent.click(screen.getByTestId("harness-team-none"));
    await waitFor(() => expect(callsOf("PATCH", "/build/sessions/s1")).toHaveLength(1));
    expect(callsOf("PATCH", "/build/sessions/s1")[0].body).toEqual({ team: null });
    await waitFor(() => expect(screen.getByTestId("harness-team")).toHaveTextContent(/^Team$/));
  });

  it("the folder's default: saved with PUT, said while in use, and cleared", async () => {
    api.gets["/build/sessions/s1"] = teamView([GLM], CODEX);
    api.answers["PUT /build/folders/team"] = (body: unknown) => body;
    renderSession();
    await openPanel();
    const use = await screen.findByTestId("harness-team-folder-default");
    expect(use).toHaveTextContent("Use this team for new sessions in this folder");
    fireEvent.click(use);
    await waitFor(() => expect(callsOf("PUT", "/build/folders/team")).toHaveLength(1));
    expect(callsOf("PUT", "/build/folders/team")[0].body).toEqual({
      folder: FOLDER,
      team: { workers: ["glm-5-3-flash"], reviewer: "codex" },
    });
    const note = await screen.findByTestId("harness-team-folder-note");
    expect(note.textContent).toBe("New sessions in this folder start with this team.");
    expect(screen.queryByTestId("harness-team-folder-default")).toBeNull();
    fireEvent.click(screen.getByTestId("harness-team-folder-clear"));
    await waitFor(() => expect(callsOf("PUT", "/build/folders/team")).toHaveLength(2));
    expect(callsOf("PUT", "/build/folders/team")[1].body).toEqual({ folder: FOLDER, team: null });
    await screen.findByTestId("harness-team-folder-default");
  });

  it("a folder whose saved team is in use says so when the panel opens", async () => {
    api.gets["/build/sessions/s1"] = teamView([GLM], null);
    api.gets[FOLDER_PATH] = { folder: FOLDER, team: { workers: ["glm-5-3-flash"], reviewer: "" } };
    renderSession();
    await openPanel();
    expect((await screen.findByTestId("harness-team-folder-note")).textContent).toBe(
      "New sessions in this folder start with this team.",
    );
  });

  it("a session on its own branch keys the default on the folder the user picked, not its worktree", async () => {
    const worktree = "C:\\work\\calc.ij-worktrees\\fix-add-1a2b";
    api.gets["/build/sessions/s1"] = teamView([GLM], null, {
      folder: worktree,
      git: { repo: FOLDER, checkout: FOLDER, worktree, branch: "ij/fix-add-1a2b", base: "main", base_commit: "abc", name: "fix-add-1a2b" },
    });
    api.answers["PUT /build/folders/team"] = (body: unknown) => body;
    renderSession();
    await openPanel();
    expect(callsOf("GET", FOLDER_PATH)).toHaveLength(1);
    expect(api.calls.some((c) => c.path.includes(encodeURIComponent(worktree)))).toBe(false);
    fireEvent.click(await screen.findByTestId("harness-team-folder-default"));
    await waitFor(() => expect(callsOf("PUT", "/build/folders/team")).toHaveLength(1));
    expect((callsOf("PUT", "/build/folders/team")[0].body as { folder: string }).folder).toBe(FOLDER);
  });

  it("a session with no folder offers no folder default", async () => {
    api.gets["/build/sessions/s1"] = teamView([GLM], null, { folder: "", no_folder: true, folder_name: "" });
    api.gets[`/build/folders/team?folder=`] = { folder: "", team: null };
    renderSession();
    await openPanel();
    expect(screen.queryByTestId("harness-team-folder-default")).toBeNull();
    expect(screen.queryByTestId("harness-team-folder-note")).toBeNull();
  });

  it("a refusal is one plain sentence in the panel, and nothing is set", async () => {
    const sentence = "Local only is on, and Codex runs in the cloud.";
    api.answers["POST /team/members/from-model"] = new api.ApiError(sentence, 409);
    renderSession();
    await openPanel();
    fireEvent.click(screen.getByTestId("harness-team-add-worker"));
    await pickModel("codex-cli", "gpt-6-astra");
    const err = await screen.findByTestId("harness-team-error");
    expect(err.textContent).toBe(sentence);
    expect(err.getAttribute("role")).toBe("alert");
    expect(callsOf("PATCH", "/build/sessions/s1")).toHaveLength(0);
    // The control stays (a refusal is not an older Iron Jarvis).
    expect(screen.getByTestId("harness-team")).toBeTruthy();
  });

  it("a PATCH refusal (400) is said the same way", async () => {
    api.answers["POST /team/members/from-model"] = { name: "codex", provider: "codex-cli", model: "gpt-6-astra", label: "Codex", roles: ["code"], created: false };
    api.answers["PATCH /build/sessions/s1"] = new api.ApiError("codex cannot write code: it has no Writing code role.", 400);
    renderSession();
    await openPanel();
    fireEvent.click(screen.getByTestId("harness-team-add-worker"));
    await pickModel("codex-cli", "gpt-6-astra");
    expect((await screen.findByTestId("harness-team-error")).textContent).toBe(
      "codex cannot write code: it has no Writing code role.",
    );
  });

  it("a model already on the team is not added twice (no call is made)", async () => {
    api.gets["/build/sessions/s1"] = teamView([GLM], null);
    renderSession();
    await openPanel();
    fireEvent.click(screen.getByTestId("harness-team-add-worker"));
    await pickModel("ollama", "glm-5.3-flash");
    expect((await screen.findByTestId("harness-team-error")).textContent).toBe("glm-5.3-flash is already a worker.");
    expect(callsOf("POST", "/team/members/from-model")).toHaveLength(0);
  });

  it("Escape closes the model list first, then the panel — and the focus returns to the chip", async () => {
    renderSession();
    await openPanel();
    fireEvent.click(screen.getByTestId("harness-team-add-worker"));
    const filter = await screen.findByTestId("harness-team-filter");
    await waitFor(() => expect(document.activeElement).toBe(filter));
    fireEvent.keyDown(filter, { key: "Escape" });
    expect(screen.queryByTestId("harness-team-picker")).toBeNull();
    expect(panel()).toBeTruthy();
    fireEvent.keyDown(panel(), { key: "Escape" });
    expect(screen.queryByTestId("harness-team-panel")).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId("harness-team"));
  });

  it("under Local only the picker offers only models that stay on this network", async () => {
    api.gets["/build/sessions/s1"] = view({ setup: { provider: "ollama", model: "qwen3.8-27b", local_only: true } });
    renderSession();
    await openPanel();
    fireEvent.click(screen.getByTestId("harness-team-add-worker"));
    const picker = await screen.findByTestId("harness-team-picker");
    const groups = within(picker).getAllByTestId("harness-team-group").map((g) => g.getAttribute("data-group"));
    expect(groups).toEqual(["ollama"]);
  });

  it("an archived session's chip cannot be pressed", async () => {
    api.gets["/build/sessions/s1"] = view({ archived: true });
    renderSession();
    expect(((await screen.findByTestId("harness-team")) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("the chip names the team; Try on says it is not the team", () => {
  it("the chip's tooltip names the lead, the workers and the reviewer", async () => {
    api.gets["/build/sessions/s1"] = teamView([GLM, QWEN], CODEX);
    renderSession();
    const chip = await screen.findByTestId("harness-team");
    expect(chip).toHaveTextContent("Team · 4");
    expect(chip.getAttribute("title")).toBe("Team: lead Opus 5.5 · workers glm-5.3-flash, Qwen3.8-27B · reviewer Codex");
    // A team set keeps its count when the toolbar is tight (the words go,
    // the count stays): it is not the icon-alone chip.
    expect(chip.className).not.toContain("group-data-[fit-team]/fit:w-[30px]");
    expect(within(chip).getByText("4").className).toContain("group-data-[fit-team]/fit:inline");
  });

  it("alone, the chip is quiet, and gives up its word at its toolbar step", async () => {
    renderSession();
    const chip = await screen.findByTestId("harness-team");
    expect(chip.getAttribute("title")).toMatch(/^Team: /);
    expect(chip.className).toContain("group-data-[fit-team]/fit:w-[30px]");
    expect(chip.className).toContain("text-zinc-400");
  });

  it("while a team is set the Try on chip says each model works alone", async () => {
    api.gets["/build/sessions/s1"] = teamView([GLM], null);
    renderSession();
    const tryOn = await screen.findByTestId("harness-try-on");
    await waitFor(() => expect(tryOn.getAttribute("title")).toContain(TRY_ON_ALONE));
  });

  it("(control) alone, the Try on chip's tooltip is as before", async () => {
    renderSession();
    const tryOn = await screen.findByTestId("harness-try-on");
    expect(tryOn.getAttribute("title")).not.toContain(TRY_ON_ALONE);
  });
});

describe("a team reply's line", () => {
  const RECORD = {
    lead: "Opus 5.5",
    workers: ["glm-5.3-flash", "Qwen3.8-27B"],
    reviewer: "Codex",
    pieces: 3,
    lead_did: [{ reason: "No worker could take a piece that needs the internet." }],
  };

  it("decodes only the contract's shape (a whitelist)", () => {
    expect(decodeReplyTeam(RECORD)).toEqual({
      lead: "Opus 5.5",
      workers: ["glm-5.3-flash", "Qwen3.8-27B"],
      reviewer: "Codex",
      pieces: 3,
      leadDid: [{ reason: "No worker could take a piece that needs the internet." }],
    });
    for (const junk of [null, "team", 7, [], {}, { workers: [] }, { workers: [1, null] }]) {
      expect(decodeReplyTeam(junk)).toBeNull();
    }
    const odd = decodeReplyTeam({ workers: ["w", 3], lead: { x: 1 }, reviewer: 9, pieces: -2, lead_did: ["x", { reason: 4 }, { reason: "busy" }], extra: "drop" })!;
    expect(odd).toEqual({ lead: "", workers: ["w"], reviewer: "", pieces: 0, leadDid: [{ reason: "" }, { reason: "busy" }] });
    expect(Object.keys(odd)).not.toContain("extra");
  });

  it("the reply line keeps `team` (LINE_KEYS) and the view decodes its team", () => {
    const m = decodeMessage({ role: "assistant", content: "Done.", team: RECORD, junk: 1 })!;
    expect(m.team).toEqual(RECORD);
    expect((m as unknown as Record<string, unknown>).junk).toBeUndefined();
    const v = decodeSessionView(teamView([GLM], CODEX))!;
    expect(v.team?.workers.map((w) => w.name)).toEqual(["glm-5-3-flash"]);
    expect(v.team?.reviewer?.state).toBe("limit");
    expect(v.setup.team).toEqual({ workers: ["glm-5-3-flash"], reviewer: "codex" });
    // No team: none.
    const solo = decodeSessionView(view())!;
    expect(solo.team).toBeNull();
    expect(solo.setup.team).toBeNull();
  });

  it("says who led, worked and reviewed; the lead's own work in the warn tone", () => {
    render(<TeamReplyLine raw={RECORD} />);
    expect(screen.getByTestId("harness-team-members").textContent).toBe(
      "Team: lead Opus 5.5 · workers glm-5.3-flash, Qwen3.8-27B · reviewer Codex",
    );
    expect(screen.getByTestId("harness-team-members").getAttribute("title")).toContain("handed out 3 pieces");
    const did = screen.getByTestId("harness-team-lead-did");
    expect(did.textContent).toBe("The team lead did this itself: No worker could take a piece that needs the internet.");
    expect(did.className).toContain("text-tone-warn");
  });

  it("nothing for a reply of a session that worked alone", () => {
    const { container } = render(<TeamReplyLine raw={undefined} />);
    expect(container.textContent).toBe("");
  });

  it("in the session, both lines are in sight on the collapsed reply (never inside a fold)", async () => {
    api.gets["/build/sessions/s1"] = teamView([GLM], CODEX, {
      messages: [
        { role: "user", content: "Fix add", turnId: "t1" },
        {
          role: "assistant",
          content: "Fixed.",
          turnId: "t1",
          steps: [{ name: "read_file", ok: true, ms: 5 }],
          timing: { startedAt: 1, firstTokenAt: 2, endedAt: 3000 },
          team: RECORD,
        },
      ],
    });
    renderSession();
    const did = await screen.findByTestId("harness-team-lead-did");
    expect(did).toHaveTextContent("The team lead did this itself");
    // The work line is closed: the warn line is not one of its rows.
    const work = screen.getByTestId("work-line");
    expect(within(work).getByRole("button").getAttribute("aria-expanded")).toBe("false");
    expect(work.contains(did)).toBe(false);
    expect(screen.getByTestId("harness-team-members")).toHaveTextContent("Team: lead Opus 5.5");
  });

  it("repeated reasons are said once, with how many times", () => {
    const t = decodeReplyTeam({ workers: ["w"], lead_did: [{ reason: "busy." }, { reason: "busy." }, { reason: "" }] })!;
    expect(leadDidLines(t)).toEqual(["The team lead did this itself: busy (2 times).", "The team lead did this itself."]);
  });
});

describe("reviewed twice", () => {
  it("the finish line says so when the reviewer checked again", () => {
    const f = decodeFinish({
      tests: "passed",
      command: "pytest",
      review: { state: "done", member: "codex", model: "gpt-6-astra", verdict: "approved", rounds: 2 },
    })!;
    expect(f.review?.rounds).toBe(2);
    expect(finishWords(f)).toBe("Checked: tests passed, reviewed by codex twice: approved");
    expect(finishTitle(f)).toContain("Reviewed by codex on gpt-6-astra, twice");
    expect(reviewWords({ state: "done", member: "", model: "", verdict: "", rounds: 2 })).toBe("reviewed twice");
    expect(reviewWords({ state: "done", member: "", model: "", verdict: "", rounds: 1 })).toBe("reviewed by your team");
  });

  it("a count beside the review reads too; junk does not", () => {
    const f = decodeFinish({ tests: "passed", review: { state: "done", member: "codex", verdict: "" }, rounds: 2 })!;
    expect(finishWords(f)).toBe("Checked: tests passed, reviewed by codex twice");
    for (const junk of [3, "2", 0, 1.5, null]) {
      expect(decodeFinishReview({ state: "done", member: "x", rounds: junk })?.rounds).toBeUndefined();
    }
    // Once is the old words.
    expect(finishWords(decodeFinish({ tests: "passed", review: { state: "done", member: "codex", rounds: 1 } })!)).toBe(
      "Checked: tests passed, reviewed by codex",
    );
  });
});

describe("a helper row in a team turn reads as its part", () => {
  const saved = (agent: string, role?: string) => ({
    id: `h-${agent}`, session: `sess-${agent}`, agent, task: "Write util.py.", state: "done", steps: 2,
    startedAt: 1, endedAt: 2, result: "Done.", work: [], provider: "ollama", model: "glm-5.3-flash",
    ...(role ? { role } : {}),
  });

  it("code, test and read are a worker's; review is the reviewer's (ONE helper)", () => {
    expect(["code", "test", "read", "review", "design", ""].map(helperPart)).toEqual([
      "worker", "worker", "worker", "reviewer", null, null,
    ]);
    const roster = { workers: [{ name: "glm-5-3-flash", label: "glm-5.3-flash" }], reviewer: { name: "codex", label: "Codex" } };
    expect(helperTeamName("custom:glm-5-3-flash", undefined, roster)).toBe("Worker glm-5.3-flash");
    expect(helperTeamName("custom:codex", undefined, roster)).toBe("Reviewer Codex");
    expect(helperTeamName("custom:other", "test", roster)).toBe("Worker other");
    expect(helperTeamName("custom:other", "review", EMPTY_ROSTER)).toBe("Reviewer other");
    // Not yet given a member, a remote, no role outside the team, no team.
    expect(helperTeamName("role:code", "code", roster)).toBeNull();
    expect(helperTeamName("remote:mac", "code", roster)).toBeNull();
    expect(helperTeamName("custom:other", undefined, roster)).toBeNull();
    expect(helperTeamName("custom:glm-5-3-flash", "code", null)).toBeNull();
  });

  it("saved rows: 'Worker …' and 'Reviewer …' in a team reply, the old words outside one", () => {
    const raw = [saved("custom:glm-5-3-flash", "code"), saved("custom:codex", "review")];
    const { unmount } = render(
      <SavedHelperRows raw={raw} team={{ workers: [{ name: "glm-5-3-flash", label: "glm-5.3-flash" }], reviewer: null }} />,
    );
    const rows = screen.getAllByTestId("helper-row");
    expect(rows[0]).toHaveTextContent("Worker glm-5.3-flash");
    expect(rows[1]).toHaveTextContent("Reviewer codex");
    unmount();
    render(<SavedHelperRows raw={raw} />);
    expect(screen.getAllByTestId("helper-row")[0]).toHaveTextContent("Your glm-5-3-flash agent");
    expect(screen.queryByText(/Worker/)).toBeNull();
  });

  it("a live row in a team turn too, once the router gave it a member", () => {
    let helpers: HelperView[] = [];
    helpers = applyHelperFrame(helpers, { id: "c1", agent: "role:code", task: "Write util.py.", state: "starting" });
    const roster = { workers: [{ name: "glm-5-3-flash", label: "glm-5.3-flash" }], reviewer: null };
    const { rerender } = render(<LiveHelperRows helpers={helpers} team={roster} />);
    // Not yet a member: named by the role it asked for.
    expect(screen.getByTestId("helper-row")).toHaveTextContent("A team member for coding");
    helpers = applyHelperFrame(helpers, { id: "c1", session: "sess_1", agent: "custom:glm-5-3-flash", role: "code", state: "running" });
    rerender(<LiveHelperRows helpers={helpers} team={roster} />);
    expect(screen.getByTestId("helper-row")).toHaveTextContent("Worker glm-5.3-flash");
  });

  it("in the session, a team reply's saved helper reads as a worker", async () => {
    api.gets["/build/sessions/s1"] = teamView([GLM], null, {
      messages: [
        { role: "user", content: "Fix add", turnId: "t1" },
        {
          role: "assistant",
          content: "Fixed.",
          turnId: "t1",
          helpers: [saved("custom:glm-5-3-flash", "code")],
          team: { lead: "Opus 5.5", workers: ["glm-5.3-flash"], reviewer: "", pieces: 1, lead_did: [] },
        },
      ],
    });
    renderSession();
    const row = await screen.findByTestId("helper-row");
    expect(row).toHaveTextContent("Worker glm-5.3-flash");
    expect(screen.queryByTestId("harness-team-lead-did")).toBeNull();
  });
});

describe("the one-row toolbar (lib/toolbarFit)", () => {
  it("'Team' gives up its word right after 'Try on…', before the model's name", () => {
    expect(HARNESS_FIT_ORDER.indexOf("team")).toBe(HARNESS_FIT_ORDER.indexOf("tryon") + 1);
    expect(HARNESS_FIT_ORDER.indexOf("team")).toBeLessThan(HARNESS_FIT_ORDER.indexOf("name"));
    expect(FIT_STEPS).toContain("team");
    const bar = document.createElement("div");
    applyFit(bar, HARNESS_FIT_ORDER, 2);
    expect(FIT_STEPS.filter((s) => bar.hasAttribute(`data-fit-${s}`))).toEqual(["tryon", "team"]);
    // The chat never takes it.
    expect(CHAT_FIT_ORDER).not.toContain("team");
  });

  it("the chip is in the session's toolbar, beside Try on", async () => {
    renderSession();
    const chip = await screen.findByTestId("harness-team");
    const bar = screen.getByTestId("harness-composer-toolbar");
    expect(bar.contains(chip)).toBe(true);
    const buttons = Array.from(bar.querySelectorAll("button"));
    expect(buttons.indexOf(chip as HTMLButtonElement)).toBe(buttons.indexOf(screen.getByTestId("harness-try-on") as HTMLButtonElement) + 1);
  });
});

describe("the pure rules", () => {
  it("names, counts and the PATCH body", () => {
    expect(decodeTeamNames({ workers: ["a", "a", "b", "c"], reviewer: "a" })).toEqual({ workers: ["a", "b"], reviewer: "" });
    expect(decodeTeamNames({ workers: [] , reviewer: "r"})).toBeNull();
    expect(decodeTeamNames(null)).toBeNull();
    expect(teamCount(null)).toBe(0);
    expect(teamCount({ workers: ["a"], reviewer: "" })).toBe(2);
    expect(teamCount({ workers: ["a", "b"], reviewer: "r" })).toBe(4);
    expect(teamChipWords(0)).toBe("Team");
    expect(teamChipWords(3)).toBe("Team · 3");
    expect(teamPatch(null)).toEqual({ team: null });
    expect(teamPatch({ workers: [], reviewer: "r" })).toEqual({ team: null });
    expect(teamPatch({ workers: ["a", "b", "c"], reviewer: "r" })).toEqual({ team: { workers: ["a", "b"], reviewer: "r" } });
    expect(sameTeam({ workers: ["a", "b"], reviewer: "" }, { workers: ["b", "a"], reviewer: "" })).toBe(true);
    expect(sameTeam({ workers: ["a"], reviewer: "r" }, { workers: ["a"], reviewer: "" })).toBe(false);
    expect(sameTeam(null, null)).toBe(true);
    expect(teamLineWords({ lead: "Opus 5.5", workers: ["glm"], reviewer: "" })).toBe("Team: lead Opus 5.5 · worker glm");
  });

  it("the session view's team: whitelisted, no team without a worker, unknown states read 'not known'", () => {
    expect(decodeSessionTeam({ lead: LEAD, workers: [], reviewer: CODEX })).toBeNull();
    const t = decodeSessionTeam({ lead: LEAD, workers: [{ ...GLM, state: "weird" }, { bad: 1 }], reviewer: { name: "glm-5-3-flash" } })!;
    expect(t.workers).toHaveLength(1);
    expect(t.workers[0].state).toBe("unknown");
    // The same member cannot be a worker and the reviewer.
    expect(t.reviewer).toBeNull();
  });
});
