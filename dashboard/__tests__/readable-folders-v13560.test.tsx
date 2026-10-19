/**
 * v1.356.0, wave 6 lane 6C (G81): the folders Jarvis may read are a setting.
 *
 * A new user who wants Jarvis to read only two folders used to need an
 * environment variable. Now Settings › Permissions & ledger has ONE row,
 * "Folders Jarvis may read" (the schema's `readable_folders`, drawn by the
 * real SettingRow as one folder per line and saved through the one
 * `PUT /settings/values`), and the "What Jarvis can reach" block says which
 * folders were chosen, with a press that takes the user to that row.
 *
 * The real SettingsHome, SettingRow and AccessSummary; only the transport
 * (lib/api) and two whole pages with their own pins are doubles.
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
  return {
    FakeApiError,
    gets: {} as Record<string, unknown>,
    puts: [] as Array<{ path: string; body: unknown }>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  get: async (path: string) => {
    const r = H.gets[path.split("?")[0]];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return JSON.parse(JSON.stringify(r));
  },
  put: async (path: string, body: unknown) => {
    H.puts.push({ path, body });
    return { updated: ["readable_folders"], action_id: "act_1" };
  },
  post: async () => ({}),
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: { providers: [] }, refresh: vi.fn() }),
}));
vi.mock("next/navigation", () => ({
  redirect: () => {
    throw new Error("NEXT_REDIRECT");
  },
  usePathname: () => "/settings",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock("@/components/settings/pages/DirectoryPage", () => ({
  default: () => <div data-testid="directory-stand-in">directory</div>,
}));
vi.mock("@/components/settings/pages/LegacySettingsForm", () => ({
  default: () => <div data-testid="legacy-form">legacy</div>,
  LocalCapabilitiesCard: () => <div>caps</div>,
  ContextWindowsCard: () => <div>windows</div>,
}));

import { SettingsHome } from "@/components/settings/SettingsHome";
import { READ_FOLDERS_ROW, readsSentence } from "@/components/settings/AccessSummary";
import { __resetApiCache } from "@/lib/apiCache";

const W = { timeout: 5_000 };

const A = "C:\\Users\\me\\Documents\\Clients";
const B = "D:\\Tax 2025";

const GROUPS = [
  { id: "models", label: "Models", description: "Which AI answers, and how." },
  { id: "permissions", label: "Permissions & ledger", description: "What may run without asking." },
];
/** The row as the daemon's schema serves it (settings/schema.py). */
const READ_ROW = {
  key: "readable_folders",
  label: "Folders Jarvis may read",
  group: "permissions",
  section: "",
  type: "list",
  help:
    "Chat and agents read files only inside these folders, plus Jarvis's own working folders. One folder per line. " +
    "Leave it empty to let them read any file on this PC, except the folders that hold Jarvis's keys and records.",
  tier: "ask-floor",
  store: "config",
  placeholder: "One folder per line, for example C:\\Users\\you\\Documents\\Clients",
};
const SETTINGS = [{ key: "default_model", label: "Default model", group: "models", section: "", type: "string" }, READ_ROW];

function seed(chosen: string[]) {
  H.gets = {
    "/settings/schema": { groups: GROUPS, settings: SETTINGS, secrets: [] },
    "/settings/values": { values: { default_model: "", readable_folders: chosen } },
    "/config/ledger": { changes: [] },
    "/settings/access": {
      reads: { limited: chosen.length > 0, folders: chosen, chosen, install: false },
      writes: { chat_files_dir: "C:\\Users\\me\\Documents\\Iron Jarvis" },
    },
    "/connectors": { connectors: [] },
    "/grants": { grants: [] },
  };
}

function openPermissions() {
  window.history.replaceState({}, "", "/settings?section=permissions");
  render(<SettingsHome />);
}

beforeEach(() => {
  H.puts = [];
  seed([]);
  __resetApiCache();
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

describe("the words for what Jarvis may read", () => {
  it("chosen folders are named as typed, with Jarvis's own working folders said", () => {
    const s = readsSentence({ reads: { limited: true, folders: ["x"], chosen: [A, B], install: false } });
    expect(s).toBe(
      `Reads files only in the folders you chose, without asking: ${A}, ${B}, and in its own working folders. ` +
        "The folders that hold its keys and its own records are never read.",
    );
    expect(s).not.toMatch(/IRONJARVIS|allowlist|environment/i);
  });

  it("with the installation's own limit too, it says so without naming a variable", () => {
    const s = readsSentence({ reads: { limited: true, folders: [A], chosen: [A], install: true } });
    expect(s).toContain("folders you chose (where this install allows)");
    expect(s).not.toMatch(/IRONJARVIS|allowlist/i);
  });

  it("no chosen folders: exactly the words before this setting existed", () => {
    expect(readsSentence({ reads: { limited: false, folders: [], chosen: [], install: false } })).toBe(
      readsSentence({ reads: { limited: false, folders: [] } }),
    );
    expect(readsSentence({ reads: { limited: true, folders: ["D:\\Shared"], chosen: [], install: true } })).toContain(
      "only in the folders this install allows",
    );
  });
});

describe("Settings › Permissions: the row and the block", () => {
  it("a user limits reading to two folders: types them in the row and saves through the one writer", async () => {
    openPermissions();
    const row = await screen.findByTestId("setting-readable_folders", {}, W);
    expect(row.textContent).toContain("Folders Jarvis may read");
    const box = within(row).getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: `${A}\n${B}` } });
    const save = await screen.findByRole("button", { name: "Save changes" }, W);
    fireEvent.click(save);
    await waitFor(() => expect(H.puts).toHaveLength(1), W);
    expect(H.puts[0].path).toBe("/settings/values");
    expect((H.puts[0].body as { values: Record<string, unknown> }).values).toEqual({ readable_folders: [A, B] });
  });

  it("the block names the chosen folders and its press opens the row", async () => {
    seed([A, B]);
    openPermissions();
    const block = await screen.findByTestId("access-summary", {}, W);
    await waitFor(
      () => expect(within(block).getByTestId("access-reads").textContent).toContain(`without asking: ${A}, ${B}`),
      W,
    );
    const press = within(block).getByRole("button", { name: "Change folders" });
    const row = await screen.findByTestId(READ_FOLDERS_ROW, {}, W);
    const spy = vi.fn();
    row.scrollIntoView = spy;
    fireEvent.click(press);
    await waitFor(() => expect(spy).toHaveBeenCalled(), W);
  });

  it("with nothing chosen the press reads 'Choose folders'", async () => {
    openPermissions();
    const block = await screen.findByTestId("access-summary", {}, W);
    await waitFor(() => expect(within(block).getByRole("button", { name: "Choose folders" })).toBeTruthy(), W);
    expect(within(block).queryByRole("button", { name: "Change folders" })).toBeNull();
  });

  it("CONTROL: a daemon whose schema has no such row gets no press (nothing to open)", async () => {
    H.gets["/settings/schema"] = { groups: GROUPS, settings: [SETTINGS[0]], secrets: [] };
    openPermissions();
    const block = await screen.findByTestId("access-summary", {}, W);
    await waitFor(() => expect(within(block).getByTestId("access-reads").textContent).toContain("Reads"), W);
    expect(within(block).queryByRole("button", { name: /folders/ })).toBeNull();
  });
});
