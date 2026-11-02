/**
 * v1.426.0 (Wave 6 lane B, INDEPENDENT REVIEW): folder defaults, the cases the
 * builder's pins did not hold (each red on the builder's code first).
 *
 *   1. A save or Clear made in the folder heading's panel reaches a start
 *      composer already on screen (they show TOGETHER: the empty view and the
 *      list beside it). The composer kept its own read and its own last save,
 *      so a Clear left it starting the next session with the cleared defaults.
 *   2. Build › Code: a folder's tool that cannot start here is not used AND
 *      the words say so (they said "start with: Codex" over a Claude Code
 *      chip); a folder's model the tool no longer offers likewise.
 *   3. Chat › Code: a folder's model that cannot answer is never picked (the
 *      builder's guarantee, unpinned: picking it anyway stayed green).
 *
 * (The builder's own header, whose mocks this file reuses:) a FOLDER carries
 * its own defaults for new coding sessions started in it.
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


const TOOLS_CODEX_OUT = decodeCodingTools({
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      modes: [{ id: "ask", label: "Ask first", dangerous: false }],
      hidden_modes: [],
      models: [
        { id: "", label: "Claude Code's default" },
        { id: "claude-opus-5-5", label: "Opus 5.5" },
      ],
      efforts: [],
      accounts: [],
      commands: [],
    },
    {
      id: "codex",
      label: "Codex",
      installed: true,
      signed_in: false,
      modes: [{ id: "ask", label: "Ask first", dangerous: false }],
      hidden_modes: [],
      models: [{ id: "", label: "Codex's default" }],
      efforts: [],
      accounts: [],
      commands: [],
    },
  ],
});

describe("a save made elsewhere in the window reaches the open start composer", () => {
  it("Chat › Code: a Clear in the folder heading's panel takes the defaults off the composer at once", async () => {
    api.gets[DEFAULTS_PATH] = {
      folder: FOLDER,
      defaults: { code: { provider: "ollama", model: "qwen3:8b", approval_mode: "accept_edits" } },
    };
    const got: StartRequest[] = [];
    render(
      <>
        <HarnessStartComposer initialFolder={FOLDER} onStart={async (req) => (got.push(req), null)} />
        <FolderDefaultsPanel
          folder={FOLDER}
          view="code"
          describe={(d) => codeDefaultsWords(d, () => "Qwen3 8B")}
          current={null}
          testId="p"
        />
      </>,
    );
    const select = screen.getByTestId("harness-new-model") as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("ollama::qwen3:8b"));
    api.putReply = { folder: FOLDER, defaults: {} };
    fireEvent.click(await screen.findByTestId("p-clear"));
    await waitFor(() => expect(screen.queryByTestId("harness-new-folder-defaults-words")).toBeNull());
    expect(select.value).toBe("");
    fireEvent.change(screen.getByTestId("harness-new-box"), { target: { value: "go" } });
    fireEvent.click(screen.getByTestId("harness-new-send"));
    await waitFor(() => expect(got).toHaveLength(1));
    expect(got[0].provider).toBeUndefined();
    expect(got[0].approval_mode).toBeUndefined();
  });

  it("Chat › Code: the composer's OWN save, then a Clear in the panel — the Clear wins", async () => {
    api.gets[DEFAULTS_PATH] = { folder: FOLDER, defaults: {} };
    render(
      <>
        <HarnessStartComposer initialFolder={FOLDER} onStart={async () => null} />
        <FolderDefaultsPanel folder={FOLDER} view="code" describe={(d) => codeDefaultsWords(d, () => "Opus 5.5")} current={null} testId="p" />
      </>,
    );
    fireEvent.change(screen.getByTestId("harness-new-model"), { target: { value: "claude-cli::opus" } });
    api.putReply = { folder: FOLDER, defaults: { code: { provider: "claude-cli", model: "opus" } } };
    fireEvent.click(await screen.findByTestId("harness-new-save-defaults"));
    await waitFor(() =>
      expect(screen.getByTestId("harness-new-folder-defaults-words").textContent).toBe("New sessions in shop start with: Opus 5.5."),
    );
    // The panel heard the composer's save too.
    await waitFor(() => expect(screen.getByTestId("p-words").textContent).toBe("New sessions in shop start with: Opus 5.5."));
    api.putReply = { folder: FOLDER, defaults: {} };
    fireEvent.click(screen.getByTestId("p-clear"));
    await waitFor(() => expect(screen.queryByTestId("harness-new-folder-defaults-words")).toBeNull());
  });

  it("Build › Code: a folder's tool saved in the panel moves an untouched composer onto it", async () => {
    window.localStorage.setItem("ij.code.lastTool", "claude-code");
    api.gets[DEFAULTS_PATH] = { folder: FOLDER, defaults: {} };
    render(
      <>
        <StartComposer tools={TOOLS} toolsError={null} startFolder={FOLDER} onStarted={() => {}} />
        <FolderDefaultsPanel folder={FOLDER} view="tool" describe={(d) => d.harness} current={{ harness: "codex", mode: "ask" }} testId="p" />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Claude Code"));
    api.putReply = { folder: FOLDER, defaults: { tool: { harness: "codex", mode: "ask" } } };
    fireEvent.click(await screen.findByTestId("p-use"));
    await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Codex"));
  });
});

describe("Build › Code: the words never claim a default the chips do not use", () => {
  it("a folder's tool that cannot start stays off the chip, and the words say so", async () => {
    window.localStorage.setItem("ij.code.lastTool", "claude-code");
    api.gets[DEFAULTS_PATH] = { folder: FOLDER, defaults: { tool: { harness: "codex", mode: "ask" } } };
    render(<StartComposer tools={TOOLS_CODEX_OUT} toolsError={null} startFolder={FOLDER} onStarted={() => {}} />);
    const words = await screen.findByTestId("code-start-folder-defaults-words");
    await act(() => new Promise((r) => setTimeout(r, 30)));
    expect(screen.getByTestId("code-start-tool").textContent).toContain("Claude Code");
    expect(words.textContent).toBe(
      "New sessions in shop start with: Codex · Ask first. Codex cannot start right now, so Claude Code does.",
    );
  });

  it("a folder's model the tool no longer offers is not used, and the words say so", async () => {
    window.localStorage.setItem("ij.code.lastTool", "claude-code");
    api.gets[DEFAULTS_PATH] = { folder: FOLDER, defaults: { tool: { harness: "claude-code", model: "claude-gone-1" } } };
    render(<StartComposer tools={TOOLS} toolsError={null} startFolder={FOLDER} onStarted={() => {}} />);
    const words = await screen.findByTestId("code-start-folder-defaults-words");
    expect((screen.getByTestId("code-start-model") as HTMLSelectElement).value).toBe("");
    expect(words.textContent).toBe(
      "New sessions in shop start with: Claude Code · claude-gone-1. claude-gone-1 is not offered by Claude Code right now, so its default model does.",
    );
  });
});

describe("Chat › Code: a folder's model that cannot answer is never picked", () => {
  it("the chip stays on the default model, the create carries none, and the words say why", async () => {
    api.gets[DEFAULTS_PATH] = {
      folder: FOLDER,
      defaults: { code: { provider: "codex-cli", model: "gpt-6-astra", approval_mode: "accept_edits" } },
    };
    const got: StartRequest[] = [];
    render(<HarnessStartComposer initialFolder={FOLDER} onStart={async (req) => (got.push(req), null)} />);
    const words = await screen.findByTestId("harness-new-folder-defaults-words");
    expect(words.textContent).toContain("cannot answer right now, so the default model does.");
    expect((screen.getByTestId("harness-new-model") as HTMLSelectElement).value).toBe("");
    fireEvent.change(screen.getByTestId("harness-new-box"), { target: { value: "go" } });
    fireEvent.click(screen.getByTestId("harness-new-send"));
    await waitFor(() => expect(got).toHaveLength(1));
    expect(got[0].provider).toBeUndefined();
    expect(got[0].model).toBeUndefined();
    // The folder's approval level still applies (it is not a model's).
    expect(got[0].approval_mode).toBe("accept_edits");
  });
});

describe("the one cache", () => {
  it("a read that began before a save never writes its older answer over the saved one", async () => {
    const { readFolderDefaults, saveFolderDefaults, cachedFolderDefaults } = await import("@/lib/folderDefaults");
    let answer: (v: unknown) => void = () => {};
    api.gets[DEFAULTS_PATH] = new Promise((res) => {
      answer = res;
    });
    const reading = readFolderDefaults(FOLDER);
    api.putReply = { folder: FOLDER, defaults: { code: { approval_mode: "always_ask" } } };
    await saveFolderDefaults(FOLDER, "code", { approval_mode: "always_ask" });
    answer({ folder: FOLDER, defaults: { code: { approval_mode: "auto" } } });
    expect(await reading).toEqual({ code: { approval_mode: "always_ask" }, tool: null });
    expect(cachedFolderDefaults(FOLDER)).toEqual({ code: { approval_mode: "always_ask" }, tool: null });
  });
});

describe("Chat › Code: Local only and a folder's model that leaves this PC", () => {
  it("an untouched chip does not take a cloud folder model while Local only is on, and the words say why", async () => {
    // The catalog's own answer to "does this model stay on your network".
    Object.assign(MODELS.usable[0], { on_this_network: false }); // claude-cli · Opus 5.5
    Object.assign(MODELS.usable[1], { on_this_network: true }); // ollama · Qwen3 8B
    try {
      api.gets[DEFAULTS_PATH] = {
        folder: FOLDER,
        defaults: { code: { provider: "claude-cli", model: "opus", reasoning: "high", approval_mode: "accept_edits" } },
      };
      const got: StartRequest[] = [];
      render(<HarnessStartComposer initialFolder={FOLDER} onStart={async (req) => (got.push(req), null)} />);
      const select = screen.getByTestId("harness-new-model") as HTMLSelectElement;
      await waitFor(() => expect(select.value).toBe("claude-cli::opus"));
      fireEvent.click(screen.getByTestId("harness-new-local-only"));
      await waitFor(() => expect(select.value).toBe(""));
      expect(screen.getByTestId("harness-new-folder-defaults-words").textContent).toContain(
        "Opus 5.5 leaves this PC, so it is not used while Local only is on.",
      );
      fireEvent.change(screen.getByTestId("harness-new-box"), { target: { value: "go" } });
      fireEvent.click(screen.getByTestId("harness-new-send"));
      await waitFor(() => expect(got).toHaveLength(1));
      expect(got[0]).toMatchObject({ local_only: true, approval_mode: "accept_edits" });
      expect(got[0].provider).toBeUndefined();
      expect(got[0].reasoning).toBeUndefined();
      // A folder model that stays on the network is used under Local only.
      cleanup();
      resetFolderDefaultsCache();
      api.gets[DEFAULTS_PATH] = { folder: FOLDER, defaults: { code: { provider: "ollama", model: "qwen3:8b" } } };
      render(<HarnessStartComposer initialFolder={FOLDER} onStart={async () => null} />);
      fireEvent.click(screen.getByTestId("harness-new-local-only"));
      await waitFor(() => expect((screen.getByTestId("harness-new-model") as HTMLSelectElement).value).toBe("ollama::qwen3:8b"));
    } finally {
      delete (MODELS.usable[0] as { on_this_network?: boolean }).on_this_network;
      delete (MODELS.usable[1] as { on_this_network?: boolean }).on_this_network;
    }
  });
});
