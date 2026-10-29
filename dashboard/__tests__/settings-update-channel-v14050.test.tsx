/**
 * v1.405.0 review: Settings › System shows the update channel ONCE — the
 * Updates section's Stable / Fresh card. The schema row (`update_channel`,
 * still there for chat and the config tools) is not drawn as a second, plain
 * select beside it: the two would disagree until a reload. A search for its
 * words, or `?focus=update_channel`, opens the Updates section instead.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

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
vi.mock("@/components/settings/pages/UpdatesPage", () => ({
  default: () => <div data-testid="updates-page-stub">Stable / Fresh card</div>,
}));
vi.mock("@/components/settings/pages/LegacySettingsForm", () => ({
  default: () => <div data-testid="legacy-form">legacy</div>,
  LocalCapabilitiesCard: () => null,
  ContextWindowsCard: () => null,
}));

import { CONTROLLED_IN_SECTION, SettingsHome } from "@/components/settings/SettingsHome";

const GROUPS = [
  { id: "models", label: "Models", description: "Which AI answers." },
  { id: "system", label: "System", description: "Updates, backups." },
];
const SETTINGS = [
  { key: "default_model", label: "Default model", group: "models", section: "", type: "string", help: "", aliases: [] },
  {
    key: "update_channel", label: "Update channel", group: "system", section: "Updates", type: "enum",
    help: "Stable gets a release once it has been out for a day.", tier: "allow",
    aliases: ["stable channel", "fresh channel"],
    options: [{ value: "fresh", label: "Fresh (every release)" }, { value: "stable", label: "Stable (out for a day first)" }],
  },
  { key: "keep_awake", label: "Keep this PC awake while work runs", group: "system", section: "Power", type: "bool", help: "", aliases: [] },
];

beforeEach(() => {
  H.gets = {
    "/settings/schema": { groups: GROUPS, settings: SETTINGS, secrets: [] },
    "/settings/values": { values: { default_model: "", update_channel: "fresh", keep_awake: true } },
    "/config/ledger": { changes: [] },
  };
  window.history.replaceState({}, "", "/settings");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

async function openSystem() {
  render(<SettingsHome />);
  const groups = await screen.findByTestId("settings-groups");
  fireEvent.click(within(groups).getByRole("button", { name: "System" }));
  // The group's own rows are drawn once the schema has arrived.
  await screen.findByText("Keep this PC awake while work runs");
}

describe("the update channel on Settings › System", () => {
  it("is drawn once: the Updates section, never a second plain row", async () => {
    expect(CONTROLLED_IN_SECTION.update_channel).toBe("system-updates");
    await openSystem();
    expect(screen.queryByText("Update channel")).toBeNull();
    expect(document.getElementById("setting-update_channel")).toBeNull();
    const updates = screen.getByTestId("settings-sub-system-updates");
    expect(updates.textContent).toContain("Stable or Fresh updates");
  });

  it("a search for its words opens the Updates section, not a row", async () => {
    render(<SettingsHome />);
    await screen.findByTestId("settings-groups");
    const box = screen.getByTestId("settings-search");
    fireEvent.change(box, { target: { value: "stable channel" } });
    expect(await screen.findByTestId("settings-sub-system-updates")).toBeInTheDocument();
    expect(screen.queryByText("Update channel")).toBeNull();
  });

  it("?focus=update_channel opens the Updates section", async () => {
    window.history.replaceState({}, "", "/settings?focus=update_channel");
    render(<SettingsHome />);
    const sub = await screen.findByTestId("settings-sub-system-updates");
    expect(await within(sub).findByTestId("updates-page-stub")).toBeInTheDocument();
  });
});
