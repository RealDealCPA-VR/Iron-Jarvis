/**
 * Wave D item 32 (ease review 15; lanes I12 / J5 / M8): Settings folds the
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

import { CONTROLLED_IN_SECTION, SettingsHome, foldedRow, moreOptionsLabel } from "@/components/settings/SettingsHome";
import { SETTINGS_HASH, SETTINGS_OPEN_EVENT, settingsTarget } from "@/lib/settingsOpen";

const GROUPS = [
  { id: "models", label: "Models", description: "Which AI answers, and how." },
  { id: "connections", label: "Connections", description: "Accounts, keys, apps and alerts." },
];
const SETTINGS = [
  { key: "default_model", label: "Default model", group: "models", section: "", type: "string", help: "Which model" },
  { key: "model_roles", label: "Model roles", group: "models", section: "Routing", type: "dict", advanced: true, help: "JSON roles" },
  { key: "fleet_sample_s", label: "Fleet sampling", group: "models", section: "Fleet", type: "number", advanced: true },
  { key: "strict_model_pin", label: "Strict model pin", group: "models", section: "Routing", type: "bool", advanced: true },
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
  model_roles: {},
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

describe("the rules", () => {
  it("an advanced row or a JSON box is folded; an everyday row is not", () => {
    expect(foldedRow({ advanced: true, type: "bool" })).toBe(true);
    expect(foldedRow({ type: "dict" })).toBe(true);
    expect(foldedRow({ advanced: false, type: "string" })).toBe(false);
    expect(foldedRow({ type: "enum" })).toBe(false);
  });

  it("the fold's words carry the count", () => {
    expect(moreOptionsLabel(12)).toBe("More options (12)");
    expect(moreOptionsLabel(1)).toBe("More options (1)");
  });

  it("Account groups is controlled by the Several accounts card's Group picker", () => {
    expect(CONTROLLED_IN_SECTION.account_groups).toBe("connections-accounts");
  });

  it("#settings-<key> names a row like ?focus=<key> (lib/settingsOpen)", () => {
    expect(SETTINGS_HASH).toBe("#settings-");
    expect(settingsTarget("/settings#settings-model_roles")).toEqual({ section: "", focus: "model_roles" });
    expect(settingsTarget("/settings#settings-device.theme")).toEqual({ section: "", focus: "device.theme" });
    // The older forms are unchanged.
    expect(settingsTarget("/settings?focus=default_model")).toEqual({ section: "", focus: "default_model" });
    expect(settingsTarget("/settings#appearance")).toEqual({ section: "appearance", focus: "" });
    expect(settingsTarget("/settings")).toBeNull();
  });
});

describe("the group's advanced rows wait under a closed fold", () => {
  it("they are not in the DOM until the fold opens, and the fold says how many", async () => {
    render(<SettingsHome />);
    expect(await screen.findByTestId("setting-default_model")).toBeTruthy();
    // Three folded rows in Models: two advanced + the dict (also advanced).
    expect(screen.queryByTestId("setting-model_roles")).toBeNull();
    expect(screen.queryByTestId("setting-fleet_sample_s")).toBeNull();
    expect(screen.queryByTestId("setting-strict_model_pin")).toBeNull();
    const button = foldButton();
    expect(button.textContent).toContain("More options (3)");
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(fold().getAttribute("data-open")).toBe("false");

    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("setting-model_roles")).toBeTruthy();
    expect(screen.getByTestId("setting-fleet_sample_s")).toBeTruthy();
    expect(screen.getByTestId("setting-strict_model_pin")).toBeTruthy();
    // The folded rows keep their section headings inside the fold.
    const headings = within(fold()).getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings).toEqual(["Routing", "Fleet"]);
    // The everyday row never moved into the fold.
    expect(within(fold()).queryByTestId("setting-default_model")).toBeNull();

    fireEvent.click(button);
    expect(screen.queryByTestId("setting-model_roles")).toBeNull();
  });

  it("a group with nothing advanced shows no fold", async () => {
    // Connections holds one plain row; its only folded row (account_groups)
    // is controlled by the Several accounts card, so nothing is left to fold.
    render(<SettingsHome />);
    const nav = await screen.findByTestId("settings-groups");
    fireEvent.click(await within(nav).findByRole("button", { name: "Connections" }, { timeout: 10_000 }));
    expect(await screen.findByTestId("setting-iron_proxy_enabled")).toBeTruthy();
    expect(screen.queryByTestId("settings-more-options")).toBeNull();
  });

  it("the fold is remembered per group while the page is open", async () => {
    render(<SettingsHome />);
    await screen.findByTestId("setting-default_model");
    fireEvent.click(foldButton());
    expect(screen.getByTestId("setting-model_roles")).toBeTruthy();
    const nav = screen.getByTestId("settings-groups");
    fireEvent.click(await within(nav).findByRole("button", { name: "Connections" }, { timeout: 10_000 }));
    await screen.findByTestId("setting-iron_proxy_enabled");
    fireEvent.click(within(nav).getByRole("button", { name: "Models" }));
    expect(await screen.findByTestId("setting-model_roles")).toBeTruthy();
  });
});

describe("a direct link still lands on a folded row", () => {
  it("?focus=<key> opens the fold and brings the row into view", async () => {
    window.history.replaceState({}, "", "/settings?focus=fleet_sample_s");
    render(<SettingsHome />);
    const row = await screen.findByTestId("setting-fleet_sample_s");
    expect(foldButton().getAttribute("aria-expanded")).toBe("true");
    expect(row.className).toContain("ring-2");
    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled());
  });

  it("#settings-<key> does the same", async () => {
    window.history.replaceState({}, "", "/settings#settings-model_roles");
    render(<SettingsHome />);
    const row = await screen.findByTestId("setting-model_roles");
    expect(row.className).toContain("ring-2");
    expect(foldButton().getAttribute("aria-expanded")).toBe("true");
  });

  it("the settings-open event (Ctrl K, a link while Settings is open) does the same", async () => {
    render(<SettingsHome />);
    await screen.findByTestId("setting-default_model");
    expect(screen.queryByTestId("setting-strict_model_pin")).toBeNull();
    act(() => {
      window.dispatchEvent(new CustomEvent(SETTINGS_OPEN_EVENT, { detail: { section: "", focus: "strict_model_pin" } }));
    });
    expect(await screen.findByTestId("setting-strict_model_pin")).toBeTruthy();
    expect(foldButton().getAttribute("aria-expanded")).toBe("true");
  });

  it("a link to an everyday row leaves the fold closed", async () => {
    window.history.replaceState({}, "", "/settings?focus=default_model");
    render(<SettingsHome />);
    await screen.findByTestId("setting-default_model");
    expect(foldButton().getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("setting-model_roles")).toBeNull();
  });
});

describe("search shows a folded row flat", () => {
  it("a match is drawn with no fold around it", async () => {
    render(<SettingsHome />);
    await screen.findByTestId("setting-default_model");
    fireEvent.change(screen.getByTestId("settings-search"), { target: { value: "fleet sampling" } });
    expect(await screen.findByTestId("setting-fleet_sample_s")).toBeTruthy();
    expect(screen.queryByTestId("settings-more-options")).toBeNull();
  });
});

describe("Account groups hides behind the Several accounts card", () => {
  it("is never a row, and a search for it offers the accounts section", async () => {
    render(<SettingsHome />);
    const nav = await screen.findByTestId("settings-groups");
    fireEvent.click(await within(nav).findByRole("button", { name: "Connections" }, { timeout: 10_000 }));
    await screen.findByTestId("setting-iron_proxy_enabled");
    expect(screen.queryByTestId("setting-account_groups")).toBeNull();
    fireEvent.change(screen.getByTestId("settings-search"), { target: { value: "account groups" } });
    expect(await screen.findByTestId("settings-sub-connections-accounts")).toBeTruthy();
    expect(screen.queryByTestId("setting-account_groups")).toBeNull();
  });

  it("?focus=account_groups opens that section", async () => {
    window.history.replaceState({}, "", "/settings?focus=account_groups");
    render(<SettingsHome />);
    const sub = await screen.findByTestId("settings-sub-connections-accounts");
    await waitFor(() => expect(within(sub).getByRole("button").getAttribute("aria-expanded")).toBe("true"));
    expect(screen.queryByTestId("setting-account_groups")).toBeNull();
  });
});
