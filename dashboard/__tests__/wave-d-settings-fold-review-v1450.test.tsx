/**
 * AN review of Wave D item 32 — what the builder's pins did not hold:
 * a closed fold must SAY a changed expert value (saved away from a fresh
 * install's default, or edited and not saved), a percent-encoded
 * `#settings-<key>` lands, and a link while a search is in the box still
 * shows the row.
 *
 * (Setup copied from wave-d-settings-fold-v1450.) Wave D item 32 (ease review 15; lanes I12 / J5 / M8): Settings folds the
 * schema's `advanced` rows and the JSON-typed boxes under a CLOSED "More
 * options (N)" per group. Search and a direct link (`?focus=<key>`,
 * `#settings-<key>`, lib/settingsOpen's event) still reach the row; "Account
 * groups" hides because the Several accounts card has its own Group picker
 * (CONTROLLED_IN_SECTION).
 *
 * Each guarantee here goes red when its code is reverted (checked by
 * mutation): the fold filter, the focus effect's open, the count on the
 * button, the account_groups entry and the hash reader.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    const key = path.split("?")[0];
    const r = H.gets[key];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 500);
    return r;
  },
  put: async () => ({ updated: [], action_id: "" }),
  post: async () => ({}),
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: { providers: [] }, refresh: vi.fn() }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/settings",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
// The relocated pages are whole pages; stand-ins keep the test on the fold.
vi.mock("@/components/settings/pages/ConnectionsPage", () => ({
  default: () => <div data-testid="connections-page-stub">accounts card</div>,
}));
vi.mock("@/components/settings/pages/LegacySettingsForm", () => ({
  default: () => <div data-testid="legacy-form">legacy</div>,
  LocalCapabilitiesCard: () => <div>caps</div>,
}));

import { SettingsHome, changedFromDefault, moreOptionsLabel } from "@/components/settings/SettingsHome";
import { SETTINGS_OPEN_EVENT } from "@/lib/settingsOpen";

const GROUPS = [
  { id: "models", label: "Models", description: "Which AI answers, and how." },
  { id: "connections", label: "Connections", description: "Accounts, keys, apps and alerts." },
];
const SETTINGS = [
  { key: "default_model", label: "Default model", group: "models", section: "", type: "string", help: "Which model" },
  { key: "model_roles", label: "Model roles", group: "models", section: "Routing", type: "dict", advanced: true, help: "JSON roles", default: { b: 1, a: 2 } },
  { key: "fleet_sample_s", label: "Fleet sampling", group: "models", section: "Fleet", type: "number", advanced: true, default: 30 },
  { key: "strict_model_pin", label: "Strict model pin", group: "models", section: "Routing", type: "bool", advanced: true, default: false },
  { key: "iron_proxy_enabled", label: "Several accounts", group: "connections", section: "Several accounts", type: "bool" },
  {
    key: "account_groups",
    label: "Account groups",
    group: "connections",
    section: "Several accounts",
    type: "dict",
    advanced: true,
    aliases: ["account group", "account groups"],
  },
];
const VALUES = {
  default_model: "",
  model_roles: { a: 2, b: 1 },
  fleet_sample_s: 30,
  strict_model_pin: false,
  iron_proxy_enabled: false,
  account_groups: { Work: ["a"] },
};

beforeEach(() => {
  H.gets = {
    "/settings/schema": { groups: GROUPS, settings: SETTINGS, secrets: [] },
    "/settings/values": { values: VALUES },
    "/config/ledger": { changes: [] },
  };
  window.history.replaceState({}, "", "/settings");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

const fold = () => screen.getByTestId("settings-more-options");
const foldButton = () => within(fold()).getByRole("button");

describe("the words", () => {
  it("say how many folded rows changed", () => {
    expect(moreOptionsLabel(3, 0)).toBe("More options (3)");
    expect(moreOptionsLabel(3, 1)).toBe("More options (3, 1 changed)");
  });
  it("compare with the default by value, key order and empties aside", () => {
    expect(changedFromDefault({ default: { b: 1, a: 2 } }, { a: 2, b: 1 })).toBe(false);
    expect(changedFromDefault({ default: null }, "")).toBe(false);
    expect(changedFromDefault({ default: false }, true)).toBe(true);
    expect(changedFromDefault({}, "anything")).toBe(false);
  });
});

describe("a closed fold never hides a changed expert value", () => {
  it("nothing changed: the plain count, no warning", async () => {
    render(<SettingsHome />);
    await screen.findByTestId("setting-default_model");
    expect(foldButton().textContent).toContain("More options (3)");
    expect(foldButton().textContent).not.toContain("changed");
    expect(screen.queryByTestId("settings-more-changed")).toBeNull();
  });

  it("a saved value away from the default is named on the closed fold", async () => {
    H.gets["/settings/values"] = { values: { ...VALUES, strict_model_pin: true } };
    render(<SettingsHome />);
    await screen.findByTestId("setting-default_model");
    expect(foldButton().getAttribute("aria-expanded")).toBe("false");
    expect(foldButton().textContent).toContain("More options (3, 1 changed)");
    expect(screen.getByTestId("settings-more-changed").textContent).toContain("Strict model pin");
  });

  it("an edit made inside the fold and folded away is still said, and the save bar stays", async () => {
    render(<SettingsHome />);
    await screen.findByTestId("setting-default_model");
    fireEvent.click(foldButton());
    const input = screen.getByTestId("setting-fleet_sample_s").querySelector("input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "45" } });
    fireEvent.click(foldButton());
    expect(screen.queryByTestId("setting-fleet_sample_s")).toBeNull();
    expect(foldButton().textContent).toContain("1 changed");
    const note = screen.getByTestId("settings-more-changed").textContent ?? "";
    expect(note).toContain("Fleet sampling");
    expect(note).toContain("1 not saved yet");
    expect(screen.getByTestId("settings-save-bar").textContent).toContain("1 unsaved change");
  });
});

describe("links", () => {
  it("a percent-encoded #settings-<key> lands on the folded row", async () => {
    window.history.replaceState({}, "", "/settings#settings-model%5Froles");
    render(<SettingsHome />);
    const row = await screen.findByTestId("setting-model_roles");
    expect(row.className).toContain("ring-2");
  });

  it("a link while a search is in the box clears it and shows the row", async () => {
    render(<SettingsHome />);
    await screen.findByTestId("setting-default_model");
    fireEvent.change(screen.getByTestId("settings-search"), { target: { value: "default model" } });
    await waitFor(() => expect(screen.queryByTestId("settings-more-options")).toBeNull());
    act(() => {
      window.dispatchEvent(new CustomEvent(SETTINGS_OPEN_EVENT, { detail: { section: "", focus: "strict_model_pin" } }));
    });
    const row = await screen.findByTestId("setting-strict_model_pin");
    expect(row.className).toContain("ring-2");
  });
});
