/**
 * v1.230.0 (audit Wave 4, U3) — the model switcher shows the ACTIVE model
 * first.
 *
 * The audit opened the switcher on the daily driver: heading "Active model",
 * then ~55 flat catalog rows starting with claude-opus… — the active brain (a
 * local RTX endpoint) sat 2,187 px down a 320 px scroller, with six offline
 * entries in the way. Now:
 *  - the active entry is PINNED as the first row of the list, with its check;
 *  - the rest is titled "All models", grouped by provider, local / included
 *    (flat-rate CLI) first, metered APIs after;
 *  - offline providers fold under one "Show offline (N)" line;
 *  - the pinned entry is not listed twice.
 *
 * Same harness as provider-health.test.tsx: real DaemonProvider, `get` mocked
 * like the daemon.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const { getMock, putMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  putMock: vi.fn(async () => ({})),
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  get: getMock,
  put: putMock,
  post: vi.fn(async () => ({})),
  patch: vi.fn(async () => ({})),
  del: vi.fn(async () => ({})),
  API_BASE: "",
  ijToken: () => "",
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
}));

import { DaemonProvider } from "@/lib/daemon";
import { ModelSwitcher } from "@/components/ModelSwitcher";

const switcher = () => (
  <DaemonProvider>
    <ModelSwitcher />
  </DaemonProvider>
);

/** The daily driver's shape: a local brain active, a metered API with many
 *  rows listed first by the catalog, an inherited CLI login, two dead boxes. */
const HEALTH = {
  status: "ok",
  version: "1.230.0",
  default_provider: "fleet-rtx",
  default_model: "qwen3-30b",
  providers: [
    { provider: "anthropic", available: true, class: "api", inherited_from: "claude-cli" },
    { provider: "openai", available: true, class: "api" },
    { provider: "fleet-rtx", available: true, class: "local" },
    { provider: "ollama", available: false, class: "local" },
    { provider: "fleet-dead", available: false, class: "local" },
  ],
};

const MODELS = {
  models: [
    { provider: "anthropic", model: "claude-opus-4-8", name: "Anthropic", kind: "api", inherited_from: "claude-cli" },
    { provider: "anthropic", model: "claude-sonnet-4-6", name: "Anthropic", kind: "api", inherited_from: "claude-cli" },
    { provider: "openai", model: "gpt-5.5", name: "OpenAI", kind: "api" },
    { provider: "ollama", model: "llama3.1", name: "Ollama", kind: "local", size_b: 8 },
    { provider: "fleet-dead", model: "mistral-7b", name: "Dead box", kind: "local", size_b: 7 },
    { provider: "fleet-rtx", model: "qwen3-30b", name: "brain (RTX)", kind: "local", size_b: 30 },
    { provider: "fleet-rtx", model: "qwen3-8b", name: "brain (RTX)", kind: "local", size_b: 8 },
  ],
};

const ROUTING = { enabled: false, routing_model: "", connected: [], suggested: null, tiers: {} };

function mockDaemon(health: typeof HEALTH = HEALTH) {
  getMock.mockImplementation(async (path: unknown) => {
    if (path === "/health") return health;
    if (path === "/models") return MODELS;
    if (path === "/routing") return ROUTING;
    return {};
  });
}

async function openSwitcher() {
  render(switcher());
  fireEvent.click(await screen.findByRole("button", { name: /switch the active model/i }));
  await screen.findByText("Active model");
  return await screen.findByTestId("ij-model-list");
}

beforeEach(() => {
  getMock.mockReset();
  putMock.mockClear();
});
afterEach(() => cleanup());

describe("ModelSwitcher — the active model comes first", () => {
  it("pins the active entry as the FIRST child of the list, with its check", async () => {
    mockDaemon();
    const list = await openSwitcher();
    const first = list.firstElementChild as HTMLElement;
    expect(first.getAttribute("data-testid")).toBe("ij-active-model-row");
    expect(first.textContent).toContain("qwen3-30b");
    expect(first.textContent).toContain("brain (RTX)");
    // Its check (the only one: nothing else is active).
    expect(first.querySelector("svg.text-accent-soft")).not.toBeNull();
    // The catalog's first row (claude-opus) is NOT what the user sees first.
    expect(first.textContent).not.toContain("claude-opus-4-8");
  });

  it("titles the rest 'All models', grouped by provider with local and included first", async () => {
    mockDaemon();
    const list = await openSwitcher();
    expect(within(list).getByText("All models")).toBeInTheDocument();
    const groups = Array.from(list.querySelectorAll("[data-testid^='ij-model-group-']")).map(
      (g) => g.getAttribute("data-testid")!.replace("ij-model-group-", ""),
    );
    // Online only (offline are folded): local brain first, then the inherited
    // CLI login (flat-rate), then the metered API.
    expect(groups).toEqual(["fleet-rtx", "anthropic", "openai"]);
    const rtx = within(list).getByTestId("ij-model-group-fleet-rtx");
    expect(rtx.textContent).toContain("local");
    expect(within(list).getByTestId("ij-model-group-anthropic").textContent).toContain("included");
    expect(within(list).getByTestId("ij-model-group-openai").textContent).toContain("metered");
    // The active row is pinned, not listed twice: the RTX group holds only
    // the OTHER model.
    expect(rtx.textContent).toContain("qwen3-8b");
    expect(rtx.textContent).not.toContain("qwen3-30b");
    expect(within(list).getAllByText("qwen3-30b")).toHaveLength(1);
  });

  it("folds offline providers under 'Show offline (N)' and expands them on click", async () => {
    mockDaemon();
    const list = await openSwitcher();
    // Two offline providers, one model each → N = 2; their rows are absent.
    const fold = within(list).getByRole("button", { name: /Show offline \(2\)/ });
    expect(within(list).queryByText("llama3.1")).toBeNull();
    expect(within(list).queryByText("mistral-7b")).toBeNull();
    fireEvent.click(fold);
    expect(within(list).getByText("llama3.1")).toBeInTheDocument();
    expect(within(list).getByText("mistral-7b")).toBeInTheDocument();
    expect(within(list).queryByRole("button", { name: /Show offline/ })).toBeNull();
    // Still marked, still selectable (the v1.164.0 rule stands).
    const dead = within(list).getByText("mistral-7b").closest("button")!;
    expect(dead.textContent).toContain("(offline)");
    expect(dead).not.toBeDisabled();
  });

  it("an active model the catalog does not list is still pinned first", async () => {
    mockDaemon({ ...HEALTH, default_provider: "fleet-rtx", default_model: "typed-by-hand" });
    const list = await openSwitcher();
    const first = list.firstElementChild as HTMLElement;
    expect(first.getAttribute("data-testid")).toBe("ij-active-model-row");
    expect(first.textContent).toContain("typed-by-hand");
  });

  it("picking a listed row still writes the default through PUT /settings", async () => {
    mockDaemon();
    const list = await openSwitcher();
    fireEvent.click(within(list).getByText("gpt-5.5").closest("button")!);
    await vi.waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/settings", {
        values: { default_provider: "openai", default_model: "gpt-5.5" },
      }),
    );
  });
});
