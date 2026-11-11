/**
 * v1.426.0 (Wave 6 lane B, E5): a FOLDER carries its own defaults for new
 * coding sessions started in it.
 *
 *   - Chat › Code's start composer (HarnessStartComposer): an untouched model
 *     and approval chip show the folder's model and level, the create carries
 *     them (and the folder's reasoning level while the model is the folder's
 *     own), the words say so; a chip the user picks wins.
 *   - Build › Code's start composer (StartComposer): an untouched tool, mode,
 *     model and effort show the folder's; a pick wins; "Use for new sessions
 *     in this folder" saves what the chips say.
 *   - Each view's folder heading offers the defaults: the words, "Use for new
 *     sessions in this folder" (the session open in front, when it is one of
 *     that folder's — Full access never), and Clear.
 *
 * The daemon's answers are the shapes tests/test_folder_defaults_v14260.py
 * pins on the Python side.
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
    gets: {} as Record<string, unknown>,
    puts: [] as { path: string; body: unknown }[],
    posts: [] as { path: string; body: unknown }[],
    putReply: null as unknown,
    putFail: null as string | null,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) => {
    const hit = api.gets[p];
    return hit === undefined ? Promise.reject(new api.ApiError(`unmocked ${p}`, 404)) : Promise.resolve(hit);
  },
  post: (p: string, b?: unknown) => {
    api.posts.push({ path: p, body: b });
    return Promise.resolve({
      id: "app_new",
      harness: (b as { harness?: string })?.harness ?? "claude-code",
      folder: (b as { folder?: string })?.folder ?? "",
      title: "New session",
      account: "",
      model: "",
      effort: "",
      mode: "ask",
      harness_session_id: "",
      created_at: "2026-11-02T10:00:00Z",
      updated_at: "2026-11-02T10:00:00Z",
      archived_at: null,
      last_error: "",
    });
  },
  put: (p: string, b?: unknown) => {
    api.puts.push({ path: p, body: b });
    if (api.putFail) return Promise.reject(new api.ApiError(api.putFail, 400));
    return Promise.resolve(api.putReply ?? { folder: "", defaults: {} });
  },
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

const MODELS = {
  models: [
    { provider: "claude-cli", model: "opus", label: "Opus 5.5", available: true },
    { provider: "ollama", model: "qwen3:8b", label: "Qwen3 8B", available: true },
  ],
  usable: [
    { provider: "claude-cli", model: "opus", label: "Opus 5.5", available: true },
    { provider: "ollama", model: "qwen3:8b", label: "Qwen3 8B", available: true },
  ],
  loading: false,
};
vi.mock("@/lib/useModels", () => ({ useModels: () => MODELS }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));

/** v1.437.0 (moved on purpose): the start box's model chip is the SESSION's
 *  model menu (components/chat/ModelMenu with the accounts), never a select —
 *  a row is picked by typing its name and pressing Enter; the pick is read off
 *  the chip's wrapper (`data-choice`). */
function pickStartModel(name: string) {
  fireEvent.click(within(screen.getByTestId("harness-new-model")).getByRole("button"));
  const filter = screen.getByTestId("model-filter");
  fireEvent.change(filter, { target: { value: name } });
  fireEvent.keyDown(filter, { key: "Enter" });
}
const startModel = () => screen.getByTestId("harness-new-model").getAttribute("data-choice");

import { HarnessStartComposer, startCreateBody, type StartRequest } from "@/components/harness/HarnessStartComposer";
import { StartComposer } from "@/components/buildCode/StartComposer";
import { FolderDefaultsPanel } from "@/components/folders/FolderDefaultsPanel";
import { decodeCodingTools } from "@/lib/buildApp";
import {
  decodeFolderDefaults,
  resetFolderDefaultsCache,
  toolDefaultsWords,
  codeDefaultsWords,
} from "@/lib/folderDefaults";
import { START, chooseEffort, chooseModel, chosen } from "./helpers/modelPicker";

const FOLDER = "C:\\work\\shop";
const DEFAULTS_PATH = `/build/folders/defaults?folder=${encodeURIComponent(FOLDER)}`;

const TOOLS = decodeCodingTools({
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      modes: [
        { id: "ask", label: "Ask first", dangerous: false },
        { id: "edits", label: "Auto edits", dangerous: false },
        { id: "full", label: "Full access", dangerous: true },
      ],
      hidden_modes: [],
      models: [
        { id: "", label: "Claude Code's default" },
        { id: "claude-opus-5-5", label: "Opus 5.5" },
      ],
      efforts: ["low", "medium", "high"],
      accounts: [],
      commands: [],
    },
    {
      id: "codex",
      label: "Codex",
      installed: true,
      signed_in: true,
      modes: [
        { id: "ask", label: "Ask first", dangerous: false },
        { id: "full", label: "Full access", dangerous: true },
      ],
      hidden_modes: [],
      models: [
        { id: "", label: "Codex's default" },
        { id: "gpt-6-astra", label: "GPT-6 Astra" },
      ],
      efforts: ["low", "medium", "high"],
      accounts: [],
      commands: [],
    },
  ],
});

beforeEach(() => {
  resetFolderDefaultsCache();
  window.localStorage.clear();
  api.gets = {};
  api.puts = [];
  api.posts = [];
  api.putReply = null;
  api.putFail = null;
});

afterEach(() => cleanup());

describe("the words and the decode", () => {
  it("never decodes Don't ask or Full access as a folder default", () => {
    const d = decodeFolderDefaults({
      defaults: { code: { approval_mode: "yolo", provider: "x", model: "m" }, tool: { harness: "codex", mode: "full" } },
    });
    expect(d.code).toEqual({ provider: "x", model: "m" });
    expect(d.tool).toBeNull();
    expect(decodeFolderDefaults(null)).toEqual({ code: null, tool: null });
  });

  it("says the defaults in words", () => {
    expect(
      codeDefaultsWords({ provider: "claude-cli", model: "opus", reasoning: "high", approval_mode: "accept_edits" }, () => "Opus 5.5"),
    ).toBe("Opus 5.5 · reasoning high · Accept edits");
    expect(
      toolDefaultsWords({ harness: "codex", model: "gpt-6-astra", effort: "medium", mode: "ask" }, {
        tool: () => "Codex",
        model: () => "GPT-6 Astra",
        mode: () => "Ask first",
      }),
    ).toBe("Codex · GPT-6 Astra · medium effort · Ask first");
  });
});

describe("Chat › Code's start composer", () => {
  it("starts with the folder's model, level and reasoning, and says so", async () => {
    api.gets[DEFAULTS_PATH] = {
      folder: FOLDER,
      defaults: { code: { provider: "ollama", model: "qwen3:8b", reasoning: "high", approval_mode: "accept_edits" } },
    };
    const got: StartRequest[] = [];
    render(
      <HarnessStartComposer
        initialFolder={FOLDER}
        onStart={async (req) => {
          got.push(req);
          return null;
        }}
      />,
    );
    await waitFor(() => expect(startModel()).toBe("ollama::qwen3:8b"));
    expect(screen.getByTestId("harness-new-folder-defaults-words").textContent).toBe(
      "New sessions in shop start with: Qwen3 8B · reasoning high · Accept edits.",
    );
    fireEvent.change(screen.getByTestId("harness-new-box"), { target: { value: "fix the cart" } });
    fireEvent.click(screen.getByTestId("harness-new-send"));
    await waitFor(() => expect(got).toHaveLength(1));
    expect(got[0]).toMatchObject({ provider: "ollama", model: "qwen3:8b", reasoning: "high", approval_mode: "accept_edits" });
    // v1.452.0 (readiness item 43; moved on purpose): a Send that lands
    // before the folder's git read says the separate copy is off out loud
    // (`branch: false`); after it, nothing. Never a branch the user did not ask for.
    const { branch, ...body } = startCreateBody(got[0]);
    expect(branch === undefined || branch === false).toBe(true);
    expect(body).toEqual({
      provider: "ollama",
      model: "qwen3:8b",
      reasoning: "high",
      approval_mode: "accept_edits",
    });
  });

  it("a model the user picks wins — and the folder's reasoning (that model's) is dropped", async () => {
    api.gets[DEFAULTS_PATH] = {
      folder: FOLDER,
      defaults: { code: { provider: "ollama", model: "qwen3:8b", reasoning: "high", approval_mode: "accept_edits" } },
    };
    const got: StartRequest[] = [];
    render(<HarnessStartComposer initialFolder={FOLDER} onStart={async (req) => (got.push(req), null)} />);
    await waitFor(() => expect(startModel()).toBe("ollama::qwen3:8b"));
    pickStartModel("Opus 5.5");
    fireEvent.change(screen.getByTestId("harness-new-box"), { target: { value: "go" } });
    fireEvent.click(screen.getByTestId("harness-new-send"));
    await waitFor(() => expect(got).toHaveLength(1));
    expect(got[0].provider).toBe("claude-cli");
    expect(got[0].reasoning).toBeUndefined();
    expect(got[0].approval_mode).toBe("accept_edits");
  });

  it("a pick made BEFORE the folder's defaults arrive still wins (a slow read never overwrites it)", async () => {
    let answer: (v: unknown) => void = () => {};
    api.gets[DEFAULTS_PATH] = new Promise((res) => {
      answer = res;
    });
    const got: StartRequest[] = [];
    render(<HarnessStartComposer initialFolder={FOLDER} onStart={async (req) => (got.push(req), null)} />);
    pickStartModel("Opus 5.5");
    await act(async () => {
      answer({ folder: FOLDER, defaults: { code: { provider: "ollama", model: "qwen3:8b", approval_mode: "auto" } } });
    });
    await waitFor(() => expect(screen.getByTestId("harness-new-folder-defaults-words")).toBeTruthy());
    expect(startModel()).toBe("claude-cli::opus");
    fireEvent.change(screen.getByTestId("harness-new-box"), { target: { value: "go" } });
    fireEvent.click(screen.getByTestId("harness-new-send"));
    await waitFor(() => expect(got).toHaveLength(1));
    expect(got[0]).toMatchObject({ provider: "claude-cli", model: "opus", approval_mode: "auto" });
  });

  it("saves the chips as the folder's defaults with one press", async () => {
    api.gets[DEFAULTS_PATH] = { folder: FOLDER, defaults: {} };
    api.putReply = { folder: FOLDER, defaults: { code: { provider: "claude-cli", model: "opus" } } };
    render(<HarnessStartComposer initialFolder={FOLDER} onStart={async () => null} />);
    pickStartModel("Opus 5.5");
    fireEvent.click(await screen.findByTestId("harness-new-save-defaults"));
    await waitFor(() => expect(api.puts).toHaveLength(1));
    expect(api.puts[0]).toEqual({
      path: "/build/folders/defaults",
      body: { folder: FOLDER, view: "code", defaults: { provider: "claude-cli", model: "opus" } },
    });
    await waitFor(() =>
      expect(screen.getByTestId("harness-new-folder-defaults-words").textContent).toBe("New sessions in shop start with: Opus 5.5."),
    );
    expect(screen.queryByTestId("harness-new-save-defaults")).toBeNull();
  });
});

describe("Build › Code's start composer", () => {
  it("starts on the folder's tool, mode, model and effort, and says so", async () => {
    window.localStorage.setItem("ij.code.lastTool", "claude-code");
    api.gets[DEFAULTS_PATH] = {
      folder: FOLDER,
      defaults: { tool: { harness: "codex", model: "gpt-6-astra", effort: "medium", mode: "ask" } },
    };
    const started: string[] = [];
    render(<StartComposer tools={TOOLS} toolsError={null} startFolder={FOLDER} onStarted={(s) => started.push(s.id)} />);
    await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Codex"));
    await waitFor(() => expect(chosen(START).model).toBe("gpt-6-astra"));
    expect(chosen(START).effort).toBe("medium");
    expect(screen.getByTestId("code-start-folder-defaults-words").textContent).toBe(
      "New sessions in shop start with: Codex · GPT-6 Astra · medium effort · Ask first.",
    );
    fireEvent.change(screen.getByTestId("code-start-box"), { target: { value: "fix it" } });
    fireEvent.click(screen.getByTestId("code-start-send"));
    await waitFor(() => expect(api.posts).toHaveLength(1));
    expect(api.posts[0].body).toEqual({
      harness: "codex",
      folder: FOLDER,
      mode: "ask",
      model: "gpt-6-astra",
      effort: "medium",
    });
  });

  it("a tool the user picks wins", async () => {
    api.gets[DEFAULTS_PATH] = { folder: FOLDER, defaults: { tool: { harness: "codex", model: "gpt-6-astra" } } };
    render(<StartComposer tools={TOOLS} toolsError={null} startFolder={FOLDER} onStarted={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Codex"));
    fireEvent.click(screen.getByTestId("code-start-tool"));
    fireEvent.click(screen.getByTestId("code-start-tool-claude-code"));
    await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Claude Code"));
    await act(() => new Promise((r) => setTimeout(r, 30)));
    expect(screen.getByTestId("code-start-tool").textContent).toContain("Claude Code");
    expect(chosen(START).model).toBe("");
  });

  it("a model or effort picked before the folder's defaults arrive still wins; an untouched mode follows", async () => {
    window.localStorage.setItem("ij.code.lastTool", "claude-code");
    let answer: (v: unknown) => void = () => {};
    api.gets[DEFAULTS_PATH] = new Promise((res) => {
      answer = res;
    });
    render(<StartComposer tools={TOOLS} toolsError={null} startFolder={FOLDER} onStarted={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Claude Code"));
    await act(async () => {}); // v1.431.0: the tool's reset step runs first (see build-code-start-v1412)
    await chooseModel(START, "claude-opus-5-5");
    chooseEffort(START, "low");
    await act(async () => {
      answer({
        folder: FOLDER,
        defaults: { tool: { harness: "claude-code", model: "", effort: "high", mode: "edits" } },
      });
    });
    await screen.findByTestId("code-start-folder-defaults-words");
    expect(chosen(START).model).toBe("claude-opus-5-5");
    expect(chosen(START).effort).toBe("low");
    fireEvent.change(screen.getByTestId("code-start-box"), { target: { value: "go" } });
    fireEvent.click(screen.getByTestId("code-start-send"));
    await waitFor(() => expect(api.posts).toHaveLength(1));
    expect(api.posts[0].body).toMatchObject({ harness: "claude-code", model: "claude-opus-5-5", effort: "low", mode: "edits" });
  });

  it("saves what the chips say for new sessions here, never Full access", async () => {
    api.gets[DEFAULTS_PATH] = { folder: FOLDER, defaults: {} };
    api.putReply = { folder: FOLDER, defaults: { tool: { harness: "claude-code", model: "claude-opus-5-5", mode: "ask" } } };
    window.localStorage.setItem("ij.code.lastTool", "claude-code");
    render(<StartComposer tools={TOOLS} toolsError={null} startFolder={FOLDER} onStarted={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Claude Code"));
    await chooseModel(START, "claude-opus-5-5");
    fireEvent.click(await screen.findByTestId("code-start-save-defaults"));
    await waitFor(() => expect(api.puts).toHaveLength(1));
    expect(api.puts[0].body).toEqual({
      folder: FOLDER,
      view: "tool",
      defaults: { harness: "claude-code", model: "claude-opus-5-5", mode: "ask" },
    });
    await waitFor(() => expect(screen.queryByTestId("code-start-save-defaults")).toBeNull());
  });
});

describe("the folder heading's defaults panel", () => {
  it("offers the open session's settings, saves them, and clears", async () => {
    api.gets[DEFAULTS_PATH] = { folder: FOLDER, defaults: {} };
    api.putReply = { folder: FOLDER, defaults: { code: { provider: "claude-cli", model: "opus", approval_mode: "auto" } } };
    render(
      <FolderDefaultsPanel
        folder={FOLDER}
        view="code"
        describe={(d) => codeDefaultsWords(d, () => "Opus 5.5")}
        current={{ provider: "claude-cli", model: "opus", approval_mode: "auto" }}
        testId="p"
      />,
    );
    expect((await screen.findByTestId("p-words")).textContent).toBe("New sessions here start with the app's own choices.");
    fireEvent.click(screen.getByTestId("p-use"));
    await waitFor(() =>
      expect(screen.getByTestId("p-words").textContent).toBe("New sessions in shop start with: Opus 5.5 · Auto." /* v1.430.0: the level is named "Auto" */),
    );
    expect(api.puts[0].body).toEqual({
      folder: FOLDER,
      view: "code",
      defaults: { provider: "claude-cli", model: "opus", approval_mode: "auto" },
    });
    expect(screen.queryByTestId("p-use")).toBeNull(); // already the defaults
    api.putReply = { folder: FOLDER, defaults: {} };
    fireEvent.click(screen.getByTestId("p-clear"));
    await waitFor(() => expect(screen.getByTestId("p-words").textContent).toBe("New sessions here start with the app's own choices."));
    expect(api.puts[1].body).toEqual({ folder: FOLDER, view: "code", defaults: null });
  });

  it("says the daemon's refusal in words", async () => {
    api.gets[DEFAULTS_PATH] = { folder: FOLDER, defaults: {} };
    api.putFail = "Full access is chosen per session (two presses), never as a folder's default.";
    render(
      <FolderDefaultsPanel
        folder={FOLDER}
        view="tool"
        describe={(d) => d.harness}
        current={{ harness: "codex", mode: "full" }}
        testId="p"
      />,
    );
    fireEvent.click(await screen.findByTestId("p-use"));
    expect((await screen.findByTestId("p-problem")).textContent).toContain("Full access is chosen per session");
  });

  it("Chat › Code's heading opens it with the session in front of that folder only", async () => {
    const { HarnessSidebar } = await import("@/components/harness/HarnessSidebar");
    api.gets[DEFAULTS_PATH] = { folder: FOLDER, defaults: {} };
    api.putReply = { folder: FOLDER, defaults: { code: { provider: "claude-cli", model: "opus", reasoning: "high" } } };
    const asked: string[] = [];
    const folders = [
      {
        path: FOLDER,
        name: "shop",
        isProject: false,
        projectId: null,
        sessions: [{ id: "b1", title: "Fix", updatedAt: null, running: false, waiting: false, archived: false, branch: "" }],
      },
      {
        path: "C:\\work\\other",
        name: "other",
        isProject: false,
        projectId: null,
        sessions: [{ id: "b2", title: "Other", updatedAt: null, running: false, waiting: false, archived: false, branch: "" }],
      },
    ];
    render(
      <HarnessSidebar
        folders={folders}
        listError={null}
        selectedId="b1"
        onSelect={() => {}}
        onStart={async () => null}
        onRename={async () => null}
        terminals={[]}
        onOpenTerminal={() => {}}
        settingsOf={(id) => (asked.push(id), { provider: "claude-cli", model: "opus", reasoning: "high" })}
        describeDefaults={(d) => codeDefaultsWords(d, () => "Opus 5.5")}
      />,
    );
    const groups = screen.getAllByTestId("harness-folder-group");
    // The other folder's panel offers nothing to use: its session is not in front.
    fireEvent.click(within(groups[1]).getByTestId("harness-folder-defaults"));
    const other = await within(groups[1]).findByTestId("harness-folder-defaults-panel");
    expect(within(other).queryByTestId("harness-folder-defaults-panel-use")).toBeNull();
    fireEvent.click(within(groups[0]).getByTestId("harness-folder-defaults"));
    const panel = await within(groups[0]).findByTestId("harness-folder-defaults-panel");
    const use = await within(panel).findByTestId("harness-folder-defaults-panel-use");
    expect(use.textContent).toBe("Use for new sessions in this folder: Opus 5.5 · reasoning high");
    expect(asked).toContain("b1");
    expect(asked).not.toContain("b2");
    fireEvent.click(use);
    await waitFor(() => expect(api.puts).toHaveLength(1));
    expect(api.puts[0].body).toEqual({
      folder: FOLDER,
      view: "code",
      defaults: { provider: "claude-cli", model: "opus", reasoning: "high" },
    });
  });

  it("Build › Code's heading opens it with the session in front — Full access left out", async () => {
    const { BuildCodeView } = await import("@/components/buildCode/BuildCodeView");
    const sess = {
      id: "a1",
      harness: "claude-code",
      folder: FOLDER,
      title: "Fix the cart",
      account: "",
      model: "claude-opus-5-5",
      effort: "high",
      mode: "full",
      harness_session_id: "",
      created_at: "2026-10-30T09:00:00Z",
      updated_at: "2026-10-30T09:00:00Z",
      archived_at: null,
      last_error: "",
    };
    api.gets = {
      "/build/app/harnesses": {
        harnesses: [
          {
            id: "claude-code",
            label: "Claude Code",
            installed: true,
            signed_in: true,
            modes: [
              { id: "ask", label: "Ask first", dangerous: false },
              { id: "full", label: "Full access", dangerous: true },
            ],
            hidden_modes: [],
            models: [{ id: "", label: "Claude Code's default" }, { id: "claude-opus-5-5", label: "Opus 5.5" }],
            efforts: ["low", "medium", "high"],
            accounts: [],
            commands: [],
          },
        ],
      },
      "/build/app/sessions": { sessions: [sess] },
      "/build/app/sessions/a1": { session: sess, events: [], running: false, pending: [] },
      "/editor/options": { editors: [], preferred: "" },
      [DEFAULTS_PATH]: { folder: FOLDER, defaults: {} },
    };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const group = (await screen.findAllByTestId("code-folder-group"))[0];
    fireEvent.click(within(group).getByTestId(/^code-folder-defaults-/));
    const panel = await screen.findByTestId("code-folder-defaults-panel");
    const use = await within(panel).findByTestId("code-folder-defaults-panel-use");
    expect(use.textContent).toBe("Use for new sessions in this folder: Claude Code · Opus 5.5 · high effort");
    fireEvent.click(use);
    await waitFor(() => expect(api.puts).toHaveLength(1));
    expect(api.puts[0].body).toEqual({
      folder: FOLDER,
      view: "tool",
      defaults: { harness: "claude-code", model: "claude-opus-5-5", effort: "high" },
    });
  });
});

/* v1.475.0: with no folder default for the tool, a new session opens on what
 * that tool last started with on this PC; a folder's own default still wins. */
describe("Build › Code's start box remembers each tool's last pick", () => {
  it("opens on the tool's last model and effort, and a start remembers the new pick", async () => {
    window.localStorage.setItem("ij.code.lastTool", "claude-code");
    window.localStorage.setItem("ij.code.lastPick.claude-code", JSON.stringify({ model: "claude-opus-5-5", effort: "high" }));
    render(<StartComposer tools={TOOLS} toolsError={null} startFolder={null} onStarted={() => {}} />);
    await waitFor(() => expect(chosen(START).model).toBe("claude-opus-5-5"));
    expect(chosen(START).effort).toBe("high");
    expect(screen.getByTestId("code-start-tool").textContent).toBe("Claude Code · Opus 5.5 · High");
    chooseEffort(START, "low");
    fireEvent.change(screen.getByTestId("code-start-box"), { target: { value: "go" } });
    fireEvent.click(screen.getByTestId("code-start-send"));
    await waitFor(() => expect(api.posts).toHaveLength(1));
    expect(api.posts[0].body).toMatchObject({ harness: "claude-code", model: "claude-opus-5-5", effort: "low" });
    expect(JSON.parse(window.localStorage.getItem("ij.code.lastPick.claude-code") || "{}")).toEqual({
      model: "claude-opus-5-5",
      effort: "low",
    });
  });

  it("a folder's own default for the tool wins over the last pick", async () => {
    window.localStorage.setItem("ij.code.lastTool", "codex");
    window.localStorage.setItem("ij.code.lastPick.codex", JSON.stringify({ model: "gpt-6-astra", effort: "high" }));
    api.gets[DEFAULTS_PATH] = { folder: FOLDER, defaults: { tool: { harness: "codex", model: "", effort: "low" } } };
    render(<StartComposer tools={TOOLS} toolsError={null} startFolder={FOLDER} onStarted={() => {}} />);
    await screen.findByTestId("code-start-folder-defaults-words");
    await waitFor(() => expect(chosen(START).effort).toBe("low"));
    expect(chosen(START).model).toBe("");
  });

  it("another tool's last pick is not used for this one", async () => {
    window.localStorage.setItem("ij.code.lastTool", "claude-code");
    window.localStorage.setItem("ij.code.lastPick.codex", JSON.stringify({ model: "gpt-6-astra", effort: "high" }));
    render(<StartComposer tools={TOOLS} toolsError={null} startFolder={null} onStarted={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Claude Code"));
    await act(async () => {});
    expect(chosen(START)).toMatchObject({ model: "", effort: "" });
  });
});

describe("Build › Code's start box: a model picked with its tool", () => {
  it("picking another tool's model straight from the panel keeps the model (the tool's reset does not wipe it)", async () => {
    window.localStorage.setItem("ij.code.lastTool", "claude-code");
    render(<StartComposer tools={TOOLS} toolsError={null} startFolder={null} onStarted={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Claude Code"));
    await act(async () => {});
    fireEvent.click(screen.getByTestId("code-start-tool"));
    // Browse Codex in the panel without leaving Claude Code yet: press Codex,
    // then its model, as one trip.
    fireEvent.click(screen.getByTestId("code-start-tool-codex"));
    fireEvent.click(screen.getByTestId("code-start-model-gpt-6-astra"));
    await act(async () => {});
    expect(chosen(START)).toMatchObject({ tool: "codex", model: "gpt-6-astra" });
    fireEvent.change(screen.getByTestId("code-start-box"), { target: { value: "go" } });
    fireEvent.click(screen.getByTestId("code-start-send"));
    await waitFor(() => expect(api.posts).toHaveLength(1));
    expect(api.posts[0].body).toMatchObject({ harness: "codex", model: "gpt-6-astra" });
  });
});

/* v1.475.0 review: opening the picker reads the tools' list again (new
 * objects for the same tools); that must not wipe what the box holds. */
describe("Build › Code's start box: the tools' list read again", () => {
  it("keeps the mode, model and effort chosen so far", async () => {
    window.localStorage.setItem("ij.code.lastTool", "claude-code");
    const { rerender } = render(<StartComposer tools={TOOLS} toolsError={null} startFolder={null} onStarted={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Claude Code"));
    await act(async () => {});
    await chooseModel(START, "claude-opus-5-5");
    chooseEffort(START, "low");
    fireEvent.click(screen.getByTestId("code-start-mode-button"));
    fireEvent.click(screen.getByTestId("code-start-mode-option-edits"));
    expect(screen.getByTestId("code-start-mode-button").textContent).toContain("Auto edits");
    const again = TOOLS.map((t) => ({ ...t, models: t.models.map((m) => ({ ...m })) }));
    rerender(<StartComposer tools={again} toolsError={null} startFolder={null} onStarted={() => {}} />);
    await act(async () => {});
    expect(chosen(START)).toMatchObject({ tool: "claude-code", model: "claude-opus-5-5", effort: "low" });
    expect(screen.getByTestId("code-start-mode-button").textContent).toContain("Auto edits");
  });
});
