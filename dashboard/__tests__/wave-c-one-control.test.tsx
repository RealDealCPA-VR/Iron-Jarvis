/**
 * Wave C (readiness plan 0h.5, item 29): four settings had TWO controls on
 * the Settings page — a plain schema row and the better control inside a
 * section — and the two disagreed until a reload. Each keeps ONE: the
 * section's. The schema row still serves chat and the config tools, is found
 * by Settings search (which opens the section) and by `?focus=<key>`.
 *
 * The pin fails if a second control comes back: the key leaving
 * CONTROLLED_IN_SECTION, or the section's component no longer writing it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { readSrc } from "./helpers/dashGuard";

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
  useDaemon: () => ({ online: true, checking: false, health: { providers: [] }, refresh: vi.fn() }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/settings",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock("@/components/settings/pages/ConnectionsPage", () => ({
  default: () => <div data-testid="connections-page-stub">endpoint rows</div>,
}));
vi.mock("@/components/settings/pages/BrowserPage", () => ({
  default: () => <div data-testid="browser-page-stub">your browser card</div>,
}));
vi.mock("@/components/settings/MaintenanceCard", () => ({
  MaintenanceCard: () => <div data-testid="maintenance-card-stub">backup copy folder</div>,
}));
vi.mock("@/components/settings/pages/LegacySettingsForm", () => ({
  default: () => <div data-testid="legacy-form">legacy</div>,
  LocalCapabilitiesCard: () => null,
  ContextWindowsCard: () => null,
}));

import { CONTROLLED_IN_SECTION, SettingsHome } from "@/components/settings/SettingsHome";

/** The hidden rows (the media switch rides the same Maintenance card) and the
 *  section whose component is their ONE control. */
const ONE_CONTROL: Record<string, { section: string; file: string }> = {
  browser_access: { section: "connections-browser", file: "components/browser/YourBrowserCard.tsx" },
  backup_mirror_dir: { section: "system-maintenance", file: "components/settings/MaintenanceTools.tsx" },
  backup_mirror_media: { section: "system-maintenance", file: "components/settings/MaintenanceTools.tsx" },
};

/** Wave C review: the two endpoint ADDRESSES stay plain rows. The Connections
 *  page's endpoint rows Test, fetch models, take a key and Remove a seeded
 *  slot, and the Ollama press writes ONE fixed local address — nothing there
 *  lets the user type an address (the fleet registry keeps a seeded slot's
 *  address config-driven, v1.365.3), so hiding the row left the daily
 *  driver's own proxy address with no editor at all. */
const KEEP_ROWS = ["custom_base_url", "ollama_base_url"];

const GROUPS = [
  { id: "models", label: "Models", description: "Which AI answers." },
  { id: "connections", label: "Connections", description: "Accounts and apps." },
  { id: "system", label: "System", description: "Updates, backups." },
];
const row = (key: string, label: string, group: string, type = "string", extra: Record<string, unknown> = {}) => ({
  key, label, group, section: "", type, help: "", aliases: [], ...extra,
});
const SETTINGS = [
  row("default_model", "Default model", "models"),
  row("custom_base_url", "Custom endpoint URL", "models", "string", { section: "Local and custom models", aliases: ["lm studio"] }),
  row("custom_model", "Custom endpoint model", "models", "string", { section: "Local and custom models" }),
  row("ollama_base_url", "Ollama server URL", "models", "string", { section: "Local and custom models" }),
  row("browser_access", "Your browser", "connections", "enum", {
    options: [{ value: "off", label: "Off" }, { value: "read_only", label: "Read pages only" }],
  }),
  row("calendar_lead_minutes", "Calendar lead time (minutes)", "connections", "number", { section: "Calendar" }),
  row("backup_mirror_dir", "Backup copy folder", "system", "string", { section: "Backups" }),
  row("backup_mirror_media", "Copy media with backups", "system", "bool", { section: "Backups" }),
  row("keep_awake", "Keep this PC awake while work runs", "system", "bool", { section: "Power" }),
];

beforeEach(() => {
  H.gets = {
    "/settings/schema": { groups: GROUPS, settings: SETTINGS, secrets: [] },
    "/settings/values": {
      values: {
        default_model: "", custom_base_url: "http://x/v1", custom_model: "", ollama_base_url: "",
        browser_access: "off", calendar_lead_minutes: 5, backup_mirror_dir: "", backup_mirror_media: true, keep_awake: true,
      },
    },
    "/config/ledger": { changes: [] },
  };
  window.history.replaceState({}, "", "/settings");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

async function open(group: string, waitFor: string) {
  render(<SettingsHome />);
  const groups = await screen.findByTestId("settings-groups");
  fireEvent.click(await within(groups).findByRole("button", { name: group }, { timeout: 10_000 }));
  await screen.findByText(waitFor);
}

describe("ONE control per setting (Wave C, item 29)", () => {
  it("every key names its section, and that section's component is what writes it", () => {
    for (const [key, { section, file }] of Object.entries(ONE_CONTROL)) {
      expect(CONTROLLED_IN_SECTION[key], key).toBe(section);
      const src = readSrc(file);
      expect(src.includes(key), `${file} writes ${key}`).toBe(true);
      expect(src.includes('put("/settings"'), `${file} saves through PUT /settings`).toBe(true);
    }
  });

  it("the endpoint ADDRESS rows are not hidden: nothing else lets the user type one", async () => {
    for (const key of KEEP_ROWS) expect(CONTROLLED_IN_SECTION[key], key).toBeUndefined();
    // The Connections page writes custom_base_url only to CLEAR it (Remove)
    // and ollama_base_url only to one fixed address: no address box there.
    // (If an address editor is ever added there, this pin is what moves.)
    const conn = readSrc("components/settings/pages/ConnectionsPage.tsx");
    const customWrites = [...conn.matchAll(/custom_base_url:\s*([^,}\n]+)/g)].map((m) => m[1].trim());
    const ollamaWrites = [...conn.matchAll(/ollama_base_url:\s*([^,}\n]+)/g)].map((m) => m[1].trim());
    expect(customWrites.length).toBeGreaterThan(0);
    expect(customWrites.every((w) => w === '""')).toBe(true);
    expect(ollamaWrites.length).toBeGreaterThan(0);
    expect(ollamaWrites.every((w) => w === "OLLAMA_LOCAL_URL")).toBe(true);
    await open("Models", "Default model");
    expect(document.getElementById("setting-custom_base_url")).not.toBeNull();
    expect(document.getElementById("setting-ollama_base_url")).not.toBeNull();
    expect(screen.getByText("Custom endpoint URL")).toBeInTheDocument();
    expect(screen.getByText("Ollama server URL")).toBeInTheDocument();
    expect(document.getElementById("setting-custom_model")).not.toBeNull();
  });

  it("Connections draws no plain browser-access row (the Your browser card is the control)", async () => {
    await open("Connections", "Calendar lead time (minutes)");
    expect(document.getElementById("setting-browser_access")).toBeNull();
    expect(screen.getByTestId("settings-sub-connections-browser")).toBeInTheDocument();
  });

  it("System draws no plain backup copy rows (the Maintenance card is the control)", async () => {
    await open("System", "Keep this PC awake while work runs");
    expect(document.getElementById("setting-backup_mirror_dir")).toBeNull();
    expect(document.getElementById("setting-backup_mirror_media")).toBeNull();
    expect(screen.getByTestId("settings-sub-system-maintenance")).toBeInTheDocument();
  });

  it("a search for an endpoint's words finds the address ROW (its one editor), not a section", async () => {
    render(<SettingsHome />);
    await screen.findByTestId("settings-groups");
    fireEvent.change(screen.getByTestId("settings-search"), { target: { value: "lm studio" } });
    expect(await screen.findByText("Custom endpoint URL")).toBeInTheDocument();
    expect(screen.queryByTestId("settings-sub-connections-accounts")).toBeNull();
  });

  it("a search for the browser's words opens the Your browser section, never a row", async () => {
    render(<SettingsHome />);
    await screen.findByTestId("settings-groups");
    fireEvent.change(screen.getByTestId("settings-search"), { target: { value: "your browser" } });
    expect(await screen.findByTestId("settings-sub-connections-browser")).toBeInTheDocument();
    expect(document.getElementById("setting-browser_access")).toBeNull();
  });

  it("?focus=browser_access opens the Your browser section", async () => {
    window.history.replaceState({}, "", "/settings?focus=browser_access");
    render(<SettingsHome />);
    const sub = await screen.findByTestId("settings-sub-connections-browser");
    expect(await within(sub).findByTestId("browser-page-stub")).toBeInTheDocument();
    expect(document.getElementById("setting-browser_access")).toBeNull();
  });
});
