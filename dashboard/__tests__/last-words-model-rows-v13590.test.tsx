/**
 * v1.359.0 (wave 9, lane 9B) — the model menu names every row in words.
 *
 * The re-audit read the live /models (v1.354.0) and found rows with no
 * label printing their raw id: "subscription" under Claude Code and under
 * Codex (the CLI's own default, no --model), "fleet" under Your model server
 * and "spark/fleet" under OpenCode (the alias docs/LOCAL-MODELS.md tells a
 * model-server owner to keep), and an unlabelled Claude id such as
 * "claude-opus-4-8" (the composer's chip already said "Opus 4.8").
 *
 * The rule lives in ONE place, lib/onboarding.modelRowName (the provider
 * words file), read by ModelRowBits.modelText, which every model menu uses;
 * the raw id stays as the row's title. The chip above the composer says the
 * same words as the row the user picked, and a reply answered by a CLI's
 * default reads "answered by Claude Code", never "answered by subscription".
 *
 * Harness: the claude-native-v1300 model-menu harness (the REAL ChatPage,
 * only the transport, the stream hook and the daemon context mocked), fed
 * the live /models rows' shape (no `name`, no `label`).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      getResponses: {} as Record<string, unknown>,
      posts: [] as { path: string; body: Record<string, unknown> }[],
    },
    health: {
      default_provider: "claude-cli",
      default_model: "subscription",
    } as { default_provider: string; default_model: string },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    return {};
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async () => ({ reply: "ok" }),
    abort: () => {},
  }),
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    refresh: () => {},
    health: {
      status: "ok",
      version: "test",
      default_provider: H.health.default_provider,
      default_model: H.health.default_model,
      providers: [
        { provider: "claude-cli", available: true, class: "cli" },
        { provider: "codex-cli", available: true, class: "cli" },
        { provider: "custom", available: true, class: "local" },
        { provider: "opencode-cli", available: true, class: "local" },
        { provider: "anthropic", available: true, class: "api" },
      ],
    },
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false, reason: null, engine: null, listening: false, processing: false,
    transcript: "", interim: "", error: null, start: () => {}, stop: () => {}, reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { modelText, modelTitle } from "@/components/ModelRowBits";
import { answeredModelName } from "@/lib/answeredModel";
import { modelRowName, PROVIDER_DEFAULT_MODEL_IDS, SUBSCRIPTION_CLIS } from "@/lib/onboarding";

/** The live /models rows' shape (read off the user's install, 2026-10-19):
 *  no `name` and no `label` on any of these. */
const LIVE = {
  models: [
    { provider: "claude-cli", model: "subscription", kind: "cli", available: true },
    { provider: "claude-cli", model: "claude-opus-5-5", label: "Opus 5.5", kind: "cli", available: true },
    { provider: "codex-cli", model: "subscription", kind: "cli", available: true },
    { provider: "anthropic", model: "claude-opus-4-8", kind: "api", available: true },
    { provider: "custom", model: "fleet", kind: "local", available: true },
    { provider: "custom", model: "glm", kind: "local", available: true },
    { provider: "opencode-cli", model: "spark/fleet", kind: "local", available: true },
  ],
};

beforeEach(() => {
  H.api.posts.length = 0;
  H.health.default_provider = "claude-cli";
  H.health.default_model = "subscription";
  H.api.getResponses = {
    "/models": LIVE,
    "/routing": { enabled: false, routing_model: "", connected: [], suggested: null, tiers: {} },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/* ---------------------------------------------------------- the one rule */

describe("modelRowName: every live row reads in words, from the words lib/onboarding already has", () => {
  const row = (provider: string, model: string, label?: string) => ({ provider, model, ...(label ? { label } : {}) });

  it("a CLI's default row is the plan it runs on", () => {
    expect(modelRowName(row("claude-cli", "subscription"))).toBe("Your Claude plan");
    expect(modelRowName(row("codex-cli", "subscription"))).toBe("Your ChatGPT plan");
    // The words are the subscription table's own, never a second table.
    expect(SUBSCRIPTION_CLIS.map((c) => c.planName)).toEqual(["Your Claude plan", "Your ChatGPT plan"]);
    // Every placeholder the daemon treats as "no --model" reads the same.
    for (const id of PROVIDER_DEFAULT_MODEL_IDS) {
      expect(modelRowName(row("claude-cli", id))).toBe("Your Claude plan");
    }
    // A provider-default row on the user's own server reads its heading.
    expect(modelRowName(row("custom", "default"))).toBe("Your model server");
  });

  it("the 'fleet' alias reads 'Your model server', under any provider, any case", () => {
    expect(modelRowName(row("custom", "fleet"))).toBe("Your model server");
    expect(modelRowName(row("opencode-cli", "spark/fleet"))).toBe("Your model server");
    expect(modelRowName(row("fleet-custom", "Fleet"))).toBe("Your model server");
  });

  it("an unlabelled Claude id reads the way the chip says it; every other id stays as it is", () => {
    expect(modelRowName(row("anthropic", "claude-opus-4-8"))).toBe("Opus 4.8");
    // The user's own server names are theirs: nothing invented.
    expect(modelRowName(row("custom", "glm"))).toBe("glm");
    expect(modelRowName(row("custom", "fleet-think"))).toBe("fleet-think");
    expect(modelRowName(row("openai", "gpt-5.5"))).toBe("gpt-5.5");
  });

  it("the picker's own label always wins", () => {
    expect(modelRowName(row("claude-cli", "subscription", "Opus 5.5"))).toBe("Opus 5.5");
    expect(modelRowName(row("custom", "fleet", "Spark fleet"))).toBe("Spark fleet");
  });

  it("modelText is that rule, and modelTitle keeps the raw id only when the words differ", () => {
    const sub = row("claude-cli", "subscription");
    expect(modelText(sub)).toBe("Your Claude plan");
    expect(modelTitle(sub)).toBe("subscription");
    expect(modelTitle(row("custom", "glm"))).toBeUndefined();
  });

  it("a reply answered by a CLI's default says the CLI, never 'subscription'", () => {
    expect(answeredModelName({ provider: "claude-cli", model: "subscription" }, [])).toBe("Claude Code");
    expect(answeredModelName({ provider: "codex-cli", model: "subscription" }, [])).toBe("Codex");
    // A catalog label still wins; a real id still reads its own name.
    expect(
      answeredModelName({ provider: "claude-cli", model: "subscription" }, [
        { provider: "claude-cli", model: "subscription", label: "Default (Opus 5.5)" },
      ]),
    ).toBe("Default (Opus 5.5)");
    expect(answeredModelName({ provider: "claude-cli", model: "claude-opus-4-8" }, [])).toBe("Opus 4.8");
    // A provider no table knows keeps the old answer (nothing invented).
    expect(answeredModelName({ provider: "fleet-x7f2", model: "default" }, [])).toBe("default");
  });
});

/* ------------------------------------------------------ the real menu */

async function openComposerMenu() {
  await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.click(await screen.findByTitle("Switch model"));
  return (await screen.findByTestId("model-filter")) as HTMLInputElement;
}

/** Open one provider's submenu by its group name and hand back the panel. */
async function openGroup(name: string): Promise<HTMLElement> {
  const menu = screen.getByTestId("model-menu");
  const group = within(menu)
    .getAllByText(name)
    .map((el) => el.closest("button"))
    .find((b): b is HTMLButtonElement => !!b && b.getAttribute("aria-expanded") !== null);
  expect(group, `no group button named ${name}`).toBeTruthy();
  fireEvent.click(group as HTMLButtonElement);
  return await screen.findByTestId("model-sub");
}

describe("the composer's model menu on the live rows: words, the raw id only on hover", () => {
  it("Claude Code and Codex list their plan, never 'subscription'", async () => {
    render(<ChatPage />);
    await openComposerMenu();
    let sub = await openGroup("Claude Code");
    const plan = within(sub).getByText("Your Claude plan");
    expect(plan).toHaveAttribute("title", "subscription");
    expect(within(sub).getByText("Opus 5.5")).toBeInTheDocument();
    expect(sub.textContent).not.toMatch(/subscription/i);

    sub = await openGroup("Codex");
    await waitFor(() => expect(within(screen.getByTestId("model-sub")).getByText("Your ChatGPT plan")).toBeInTheDocument());
    sub = screen.getByTestId("model-sub");
    expect(within(sub).getByText("Your ChatGPT plan")).toHaveAttribute("title", "subscription");
    expect(sub.textContent).not.toMatch(/subscription/i);
  });

  it("the user's own server and OpenCode say 'Your model server' for the fleet alias; other names stay", async () => {
    render(<ChatPage />);
    await openComposerMenu();
    let sub = await openGroup("Your model server");
    await waitFor(() => expect(within(screen.getByTestId("model-sub")).getByText("glm")).toBeInTheDocument());
    sub = screen.getByTestId("model-sub");
    expect(within(sub).getByText("Your model server")).toHaveAttribute("title", "fleet");
    // A name the server owner chose is theirs, and has nothing to add on hover.
    expect(within(sub).getByText("glm")).not.toHaveAttribute("title");
    expect(sub.textContent).not.toMatch(/\bfleet\b/);

    sub = await openGroup("OpenCode");
    await waitFor(() =>
      expect(within(screen.getByTestId("model-sub")).getByText("Your model server")).toHaveAttribute("title", "spark/fleet"),
    );
    expect(screen.getByTestId("model-sub").textContent).not.toMatch(/fleet/);
  });

  it("an unlabelled Claude id under Anthropic reads 'Opus 4.8'", async () => {
    render(<ChatPage />);
    await openComposerMenu();
    const sub = await openGroup("Anthropic");
    expect(within(sub).getByText("Opus 4.8")).toHaveAttribute("title", "claude-opus-4-8");
    expect(sub.textContent).not.toContain("claude-opus-4-8");
  });

  it("the filter still finds a row by its id, and the match reads in words", async () => {
    render(<ChatPage />);
    const box = await openComposerMenu();
    fireEvent.change(box, { target: { value: "subscription" } });
    await waitFor(() => expect(screen.getAllByTestId("model-match")).toHaveLength(2));
    const rows = screen.getAllByTestId("model-match");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("Your Claude plan"),
      expect.stringContaining("Your ChatGPT plan"),
    ]);
    for (const r of rows) expect(r.textContent).not.toMatch(/subscription/i);
  });

  it("the chip names the default and the pick in the row's own words", async () => {
    render(<ChatPage />);
    // The saved default is Claude Code's own default: the chip says the plan.
    const trigger = await screen.findByTitle("Switch model");
    await waitFor(() => expect(trigger.textContent).toContain("Your Claude plan"));
    expect(trigger.textContent).not.toMatch(/subscription/i);
    // Picking the fleet alias: the chip follows the row's words.
    fireEvent.click(trigger);
    const box = (await screen.findByTestId("model-filter")) as HTMLInputElement;
    fireEvent.change(box, { target: { value: "spark/fleet" } });
    await waitFor(() => expect(screen.getAllByTestId("model-match")).toHaveLength(1));
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.queryByTestId("model-filter")).toBeNull());
    await waitFor(() => expect(screen.getByTitle("Switch model").textContent).toContain("Your model server"));
    expect(screen.getByTitle("Switch model").textContent).not.toMatch(/fleet/);
  });
});
