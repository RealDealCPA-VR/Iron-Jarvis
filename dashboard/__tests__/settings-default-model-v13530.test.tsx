/**
 * v1.353.0 integrate (lane 3A note): Settings › Models' default-model list
 * follows the provider chosen in THE SAME FORM, saved or not.
 *
 * Lane 3A made the default model a list (components/settings/
 * DefaultModelPicker) scoped to a provider. Without the form's own choice it
 * fell back to the SAVED default (/health), so changing Default provider and
 * then picking a model offered the OLD provider's models — and saving paired
 * provider X with provider Y's model id. SettingsHome now passes the form's
 * `default_provider` to the row. Rendered for real: SettingsHome, SettingRow,
 * DefaultModelPicker; only the transport is faked.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  }
  return { FakeApiError, gets: {} as Record<string, unknown> };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  get: async (path: string) => {
    const r = H.gets[path.split("?")[0]];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 500);
    return r;
  },
  put: async () => ({ updated: [], action_id: "" }),
  post: async () => ({}),
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    checking: false,
    health: {
      default_provider: "anthropic",
      providers: [
        { provider: "anthropic", available: true },
        { provider: "ollama", available: true },
      ],
    },
    refresh: vi.fn(),
  }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/settings",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock("@/components/settings/pages/LegacySettingsForm", () => ({
  default: () => <div data-testid="legacy-form">legacy</div>,
  LocalCapabilitiesCard: () => <div>caps</div>,
  ContextWindowsCard: () => <div>windows</div>,
}));

import { SettingsHome } from "@/components/settings/SettingsHome";

const GROUPS = [{ id: "models", label: "Models", description: "Which AI answers, and how." }];
const SETTINGS = [
  { key: "default_provider", label: "Default provider", group: "models", section: "", type: "string" },
  { key: "default_model", label: "Default model", group: "models", section: "", type: "string" },
];

beforeEach(() => {
  H.gets = {
    "/settings/schema": { groups: GROUPS, settings: SETTINGS, secrets: [] },
    "/settings/values": { values: { default_provider: "anthropic", default_model: "claude-opus-4-8" } },
    "/config/ledger": { changes: [] },
    "/models": {
      models: [
        { provider: "anthropic", model: "claude-opus-4-8", label: "Opus 4.8", available: true },
        { provider: "ollama", model: "qwen3:14b", available: true },
      ],
    },
  };
  window.history.replaceState({}, "", "/settings");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

function modelOptions(): string[] {
  const row = screen.getByTestId("setting-default_model");
  return within(row)
    .getAllByRole("option")
    .map((o) => (o as HTMLOptionElement).value);
}

describe("the default-model list follows the form's provider", () => {
  it("lists the saved provider's models first, then the NEW provider's after an unsaved change", async () => {
    render(<SettingsHome />);
    await screen.findByTestId("setting-default_model");
    await waitFor(() => expect(modelOptions()).toContain("claude-opus-4-8"));
    expect(modelOptions()).not.toContain("qwen3:14b");

    const provider = within(screen.getByTestId("setting-default_provider")).getByRole("combobox");
    fireEvent.change(provider, { target: { value: "ollama" } });

    await waitFor(() => expect(modelOptions()).toContain("qwen3:14b"));
    // The old provider's model is not offered as a pick for the new provider
    // (it stays visible only as the saved, unknown value).
    const picks = within(screen.getByTestId("setting-default_model"))
      .getAllByRole("option")
      .filter((o) => (o as HTMLOptionElement).title === "claude-opus-4-8");
    expect(picks).toHaveLength(0);
  });
});
