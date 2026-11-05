/**
 * v1.433.0 (Wave 1 B, decision D1): Settings › Appearance has ONE theme
 * control.
 *
 * The Theme card's switcher IS the `device.theme` row now (it saves it,
 * lib/deviceSettings), so the row is not drawn a second time as a plain
 * field beside it — the two disagreed (the field held a word nothing read,
 * the switcher the theme on screen). The row stays in the schema for chat
 * and the config tools; a search for its words, or `?focus=device.theme`,
 * shows the Theme card instead (CONTROLLED_IN_SECTION).
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
  return { FakeApiError, gets: {} as Record<string, unknown>, puts: [] as { path: string; body: unknown }[] };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  get: async (path: string) => {
    const r = H.gets[path.split("?")[0]];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 500);
    return r;
  },
  put: async (path: string, body: unknown) => {
    H.puts.push({ path, body });
    return { updated: [], action_id: "" };
  },
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
vi.mock("@/components/settings/pages/LegacySettingsForm", () => ({
  default: () => <div data-testid="legacy-form">legacy</div>,
  LocalCapabilitiesCard: () => null,
  ContextWindowsCard: () => null,
}));

import { CONTROLLED_IN_SECTION, SettingsHome } from "@/components/settings/SettingsHome";

const GROUPS = [
  { id: "models", label: "Models", description: "Which AI answers." },
  { id: "appearance", label: "Appearance", description: "How it looks on this device." },
];
const THEME_OPTIONS = [
  { value: "mark1", label: "Daylight" },
  { value: "mark2", label: "Arc Cyan" },
  { value: "mark8", label: "Liquid Glass" },
  { value: "mark23", label: "Gold & Red" },
  { value: "mark29", label: "Silver & Red" },
];
const SETTINGS = [
  { key: "default_model", label: "Default model", group: "models", section: "", type: "string", help: "", aliases: [] },
  {
    key: "device.theme", label: "Theme", group: "appearance", section: "", type: "enum",
    help: "The theme on this device.", tier: "allow", store: "device",
    aliases: ["theme", "colors", "dark mode", "light mode"], options: THEME_OPTIONS,
  },
  {
    key: "device.read_aloud", label: "Read replies aloud", group: "appearance", section: "", type: "bool",
    help: "Speak each reply on this device.", tier: "allow", store: "device", aliases: ["text to speech"],
  },
];

beforeEach(() => {
  H.gets = {
    "/settings/schema": { groups: GROUPS, settings: SETTINGS, secrets: [] },
    "/settings/values": { values: { default_model: "", "device.theme": "mark8", "device.read_aloud": null } },
    "/config/ledger": { changes: [] },
  };
  H.puts.length = 0;
  window.history.replaceState({}, "", "/settings");
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

async function openAppearance() {
  render(<SettingsHome />);
  const groups = await screen.findByTestId("settings-groups");
  fireEvent.click(within(groups).getByRole("button", { name: "Appearance" }));
  // The group's own rows are drawn once the schema has arrived.
  await screen.findByText("Read replies aloud");
}

describe("Settings › Appearance", () => {
  it("shows ONE theme control: the Theme card, never a second plain row", async () => {
    expect(CONTROLLED_IN_SECTION["device.theme"]).toBe("appearance");
    await openAppearance();
    expect(document.getElementById("setting-device.theme")).toBeNull();
    const card = screen.getByTestId("settings-appearance");
    expect(within(card).getByRole("button", { name: /Daylight/ })).toBeInTheDocument();
    // The other device rows are still drawn as rows.
    expect(document.getElementById("setting-device.read_aloud")).not.toBeNull();
    // Exactly one control anywhere on the page names the Daylight theme.
    expect(screen.queryAllByRole("combobox").filter((s) => /Daylight/.test(s.textContent ?? ""))).toEqual([]);
    expect(screen.queryAllByRole("textbox").filter((t) => (t as HTMLInputElement).value === "mark8")).toEqual([]);
  });

  it("the card's switcher saves this device's theme row", async () => {
    await openAppearance();
    const card = screen.getByTestId("settings-appearance");
    fireEvent.click(within(card).getByRole("button", { name: /Gold & Red/ }));
    expect(document.documentElement.dataset.theme).toBe("mark23");
    await waitFor(() =>
      expect(H.puts.filter((p) => p.path === "/settings/device").map((p) => (p.body as { values: unknown }).values)).toEqual([
        { "device.theme": "mark23" },
      ]),
    );
  });

  it("a search for the theme's words shows the Theme card, not a row", async () => {
    render(<SettingsHome />);
    await screen.findByTestId("settings-groups");
    fireEvent.change(screen.getByTestId("settings-search"), { target: { value: "dark mode" } });
    expect(await screen.findByTestId("settings-appearance")).toBeInTheDocument();
    expect(document.getElementById("setting-device.theme")).toBeNull();
    expect(screen.queryByText(/Nothing matches/)).toBeNull();
  });

  it("?focus=device.theme opens Appearance at the Theme card", async () => {
    window.history.replaceState({}, "", "/settings?focus=device.theme");
    render(<SettingsHome />);
    expect(await screen.findByTestId("settings-appearance")).toBeInTheDocument();
    expect(document.getElementById("setting-device.theme")).toBeNull();
  });
});
