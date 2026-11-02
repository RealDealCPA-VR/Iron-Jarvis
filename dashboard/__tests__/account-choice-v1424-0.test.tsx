/**
 * v1.424.0 (wave 4, D6) — "Account" on a custom agent's job card and on a
 * night task (components/AccountChoice), and what the night task sends.
 *
 * The field is drawn only for a model on Claude Code / Codex / Grok (or a
 * keyless Anthropic / OpenAI the CLI serves) whose vendor has accounts —
 * Iron-Proxy off, an older Iron Jarvis (a 404) or a local model: no field,
 * the form as before. "No account of its own" = Iron-Proxy's pick.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    getResponses: {} as Record<string, unknown>,
    posts: [] as { path: string; body: Record<string, unknown> }[],
    patches: [] as { path: string; body: Record<string, unknown> }[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.posts.push({ path, body });
    return { id: "night_1", folder: body.folder, words: body.words, state: "queued", account: body.account ?? "" };
  },
  patch: async (path: string, body: Record<string, unknown>) => {
    H.patches.push({ path, body });
    return { id: "night_1", folder: body.folder, words: body.words, state: "queued", account: body.account ?? "" };
  },
  put: async () => ({}),
  del: async () => ({}),
}));

import { AccountChoice, NO_OWN_ACCOUNT, servedCli } from "@/components/AccountChoice";
import { addNight, decodeTask, editNight } from "@/lib/nightShift";
import type { ModelOption } from "@/lib/types";

const MODELS = [
  { provider: "claude-cli", model: "claude-opus-4-8", available: true, kind: "cli" },
  { provider: "openai", model: "gpt-6-sol", available: true, kind: "api", inherited_from: "codex-cli" },
  { provider: "grok-cli", model: "grok-build-0.1", available: true, kind: "cli" },
  { provider: "ollama", model: "qwen3:8b", available: true, kind: "local" },
] as unknown as ModelOption[];

const ACCOUNTS = {
  claude: [{ id: "c-work", name: "Work Max", vendor: "claude", status: "ready", plan_label: "Max", default: true }],
  codex: [{ id: "x-plus", name: "Codex Plus", vendor: "codex", status: "ready" }],
  grok: [{ id: "g-work", name: "Grok Work", vendor: "grok", status: "ready" }],
  iron_proxy: { enabled: true, running: true, needs_update: false, note: "" },
};

beforeEach(() => {
  H.getResponses = { "/build/accounts": ACCOUNTS };
  H.posts.length = 0;
  H.patches.length = 0;
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function draw(provider: string, value = "", onChange = vi.fn()) {
  render(
    <AccountChoice provider={provider} models={MODELS} value={value} onChange={onChange} testId="acct" ariaLabel="Account" />,
  );
  return onChange;
}

describe("the Account field", () => {
  it("knows which CLI serves a model, a keyless OpenAI's too", () => {
    expect(servedCli("claude-cli", MODELS)).toBe("claude-cli");
    expect(servedCli("openai", MODELS)).toBe("codex-cli");
    expect(servedCli("grok-cli", MODELS)).toBe("grok-cli");
    expect(servedCli("ollama", MODELS)).toBe("");
  });

  it("lists that vendor's accounts and No account of its own", async () => {
    const onChange = draw("claude-cli");
    const select = (await screen.findByTestId("acct")) as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual([NO_OWN_ACCOUNT, "Work Max · Max"]);
    fireEvent.change(select, { target: { value: "c-work" } });
    expect(onChange).toHaveBeenCalledWith("c-work");
  });

  it("a keyless OpenAI lists Codex's accounts; Grok lists Grok's", async () => {
    draw("openai");
    const select = (await screen.findByTestId("acct")) as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(["", "x-plus"]);
    cleanup();
    draw("grok-cli");
    const grok = (await screen.findByTestId("acct")) as HTMLSelectElement;
    expect([...grok.options].map((o) => o.value)).toEqual(["", "g-work"]);
  });

  it("a local model, the default model, or no list: no field at all", async () => {
    draw("ollama");
    draw("");
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("acct")).toBeNull();
    cleanup();
    delete H.getResponses["/build/accounts"];
    draw("claude-cli");
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("acct")).toBeNull();
  });

  it("an account already set stays selectable when the list no longer has it", async () => {
    draw("claude-cli", "c-gone");
    const select = (await screen.findByTestId("acct")) as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBe(3));
    expect(select.value).toBe("c-gone");
  });
});

describe("a night task's account (lib/nightShift)", () => {
  it("rides a new task only when chosen; an edit can clear it", async () => {
    await addNight({ folder: "C:/w", words: "fix it", provider: "claude-cli", model: "x", account: "c-work" });
    expect(H.posts[0].body.account).toBe("c-work");
    await addNight({ folder: "C:/w", words: "fix it", provider: "claude-cli", model: "x", account: "" });
    expect("account" in H.posts[1].body).toBe(false);
    await editNight("night_1", { folder: "C:/w", words: "fix it", provider: "claude-cli", model: "x", account: "" });
    expect(H.patches[0].body.account).toBe("");
    await editNight("night_1", { folder: "C:/w", words: "fix it" });
    expect("account" in H.patches[1].body).toBe(false);
  });

  it("a task says the account it runs on (an older Iron Jarvis: none)", () => {
    expect(decodeTask({ id: "n1", account: "c-work" })?.account).toBe("c-work");
    expect(decodeTask({ id: "n1" })?.account).toBe("");
  });
});
