/**
 * v1.352.0, wave 2 lane D (G27): what Jarvis can reach is written down.
 *
 * Nowhere said that chat and agents read any file on this PC (except the
 * folders holding the app's keys and records) WITHOUT ASKING, at every
 * approval level — "Ask first" included.
 *
 *  - lib/permissionLevels: every level's description carries READS_LINE (and
 *    keeps its old contract: one line, plain words, the no-ask level still
 *    warns); the level a new chat starts in is read the way the chat page
 *    reads it (one key, pinned equal to the page's).
 *  - the REAL SettingsHome: Settings › Permissions & ledger opens with "What
 *    Jarvis can reach" ABOVE its settings rows — reads anywhere except key
 *    folders (or the install's allowlist), saves only in the chat's or the
 *    project's folder, which apps are connected, the browser, the agent
 *    browser, the approval level and the standing grants; its presses open
 *    the group and section where each one is set.
 *
 * Only the network (lib/api), the router and the embedded Directory page
 * (a whole page with its own pins) are doubles.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
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
  return { FakeApiError, gets: {} as Record<string, unknown>, calls: [] as string[] };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  get: async (path: string) => {
    H.calls.push(path);
    const key = path.split("?")[0];
    const r = H.gets[key];
    if (r instanceof Error) throw r;
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return JSON.parse(JSON.stringify(r));
  },
  put: async () => ({}),
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
import { connectedApps, readsSentence, writesSentence } from "@/components/settings/AccessSummary";
import { __resetApiCache } from "@/lib/apiCache";
import {
  CHAT_LEVEL_KEY,
  PERMISSION_LEVELS,
  PERMISSION_MODES,
  READS_LINE,
  readChatPermissionMode,
} from "@/lib/permissionLevels";

/** Every async wait gets 5 s: below the 15 s test budget (so a miss reports
 *  what it waited for), above a loaded Release runner's hops. */
const W = { timeout: 5_000 };

/** A dashboard source, CRLF-normalised at the reader (CI checks out with
 *  autocrlf). */
function src(...rel: string[]): string {
  return readFileSync(join(process.cwd(), ...rel), "utf8").replace(/\r\n/g, "\n");
}

const GROUPS = [
  { id: "models", label: "Models", description: "Which AI answers, and how." },
  { id: "connections", label: "Connections", description: "Accounts, keys, apps and alerts." },
  { id: "permissions", label: "Permissions & ledger", description: "What may run without asking." },
];
const SETTINGS = [
  { key: "default_model", label: "Default model", group: "models", section: "", type: "string" },
  {
    key: "browser_access",
    label: "Your browser",
    group: "connections",
    section: "",
    type: "enum",
    options: [
      { value: "off", label: "Off" },
      { value: "read_only", label: "Read pages only" },
      { value: "interactive", label: "Read and act (each action asks)" },
    ],
  },
  {
    key: "agent_browser_enabled",
    label: "Agent browser",
    group: "connections",
    section: "Browser",
    type: "bool",
    help: "Let agents drive their own separate browser (never your logged-in one).",
  },
  { key: "mcp_auto_approve", label: "Run app tools without asking", group: "permissions", section: "", type: "bool" },
];
const CHAT_DIR = "C:\\Users\\me\\Documents\\Iron Jarvis";
const DEFAULT_ACCESS = { reads: { limited: false, folders: [] }, writes: { chat_files_dir: CHAT_DIR } };
const CONNECTORS = {
  connectors: [
    { id: "github", name: "GitHub", category: "Code", connected: true, status: "connected" },
    { id: "slack", name: "Slack", category: "Chat", connected: true, status: "failed" },
    { id: "notion", name: "Notion", category: "Docs", connected: false, status: "disconnected" },
    { id: "vault", name: "Work vault", category: "Memory", connected: true, status: "connected" },
  ],
};

function seed(values: Record<string, unknown> = {}) {
  H.gets = {
    "/settings/schema": { groups: GROUPS, settings: SETTINGS, secrets: [] },
    "/settings/values": {
      values: { default_model: "", browser_access: "read_only", agent_browser_enabled: false, mcp_auto_approve: false, ...values },
    },
    "/config/ledger": { changes: [] },
    "/settings/access": DEFAULT_ACCESS,
    "/connectors": CONNECTORS,
    "/grants": { grants: [{ id: "g1" }, { id: "g2" }] },
  };
}

function openPermissions() {
  window.history.replaceState({}, "", "/settings?section=permissions");
  render(<SettingsHome />);
}

beforeEach(() => {
  H.calls = [];
  seed();
  __resetApiCache();
  window.history.replaceState({}, "", "/settings");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

/* ========================================================================== */
/* the level descriptions                                                     */
/* ========================================================================== */

describe("every approval level says reading never asks", () => {
  it("each description carries the read line, and keeps the old contract", () => {
    expect(READS_LINE).toBe("Reads files anywhere on this PC without asking");
    for (const mode of PERMISSION_MODES) {
      const d = PERMISSION_LEVELS[mode].description;
      expect(d, mode).toContain(READS_LINE);
      expect(d).not.toMatch(/\n/);
      expect(d).not.toMatch(/—|yolo|approve_for_me|always_ask/i);
    }
    // "Ask first" included: it asks before edits and commands, never reads.
    expect(PERMISSION_LEVELS.always_ask.description).toMatch(/^Asks before it edits files/);
    // The no-ask level still says it runs without asking and still warns.
    expect(PERMISSION_LEVELS.yolo.description).toMatch(/without asking you first/);
    expect(PERMISSION_LEVELS.yolo.description).toMatch(/Use with care$/);
  });

  it("the level a new chat starts in is read from the chat page's own key", () => {
    // One key, two readers: the chat page keeps its constant; this pins it equal.
    expect(src("app", "chat", "page.tsx")).toContain(`const APPROVAL_MODE_KEY = "${CHAT_LEVEL_KEY}";`);
    expect(readChatPermissionMode()).toBe("approve_for_me");
    window.localStorage.setItem(CHAT_LEVEL_KEY, "always_ask");
    expect(readChatPermissionMode()).toBe("always_ask");
    window.localStorage.setItem(CHAT_LEVEL_KEY, "something-newer");
    expect(readChatPermissionMode()).toBe("approve_for_me"); // never the no-ask level
  });
});

/* ========================================================================== */
/* the words                                                                  */
/* ========================================================================== */

describe("AccessSummary's sentences", () => {
  it("reading: anywhere except the key folders; the install's folders when it limits them; unknown says its exception", () => {
    const anywhere = readsSentence(DEFAULT_ACCESS);
    expect(anywhere).toContain(READS_LINE);
    expect(anywhere).toMatch(/every approval level/);
    expect(anywhere).toMatch(/keys and its own records are never read/);
    const limited = readsSentence({ reads: { limited: true, folders: ["D:\\Shared", "E:\\Clients"] } });
    expect(limited).toContain("only in the folders this install allows");
    expect(limited).toContain("D:\\Shared, E:\\Clients");
    expect(limited).not.toContain("anywhere");
    expect(readsSentence({ reads: { limited: true, folders: [] } })).toMatch(/reads no files/);
    expect(readsSentence(null)).toMatch(/unless this install limits it to chosen folders/);
  });

  it("saving: only the chat's or the project's folder, where a chat keeps its folder, and why commands still ask", () => {
    const w = writesSentence(DEFAULT_ACCESS);
    expect(w).toContain("only in the folder it was given to work in: the chat's own folder or the project's folder");
    expect(w).toContain(`A chat outside a project keeps its folder in ${CHAT_DIR}.`);
    expect(w).toContain("unless the level is Don't ask");
    expect(writesSentence(null)).not.toContain("keeps its folder in");
  });

  it("apps: connected ones by name, a broken one said so, memory sources are not apps", () => {
    expect(connectedApps(CONNECTORS.connectors)).toEqual(["GitHub", "Slack (not working)"]);
    expect(connectedApps(null)).toEqual([]);
  });
});

/* ========================================================================== */
/* Settings › Permissions & ledger                                            */
/* ========================================================================== */

describe("Settings › Permissions & ledger opens with what Jarvis can reach", () => {
  it("a new user reads it all there, above the settings rows", async () => {
    openPermissions();
    const block = await screen.findByTestId("access-summary", {}, W);
    expect(within(block).getByRole("heading", { name: "What Jarvis can reach" })).toBeTruthy();
    const reads = within(block).getByTestId("access-reads");
    await waitFor(() => expect(reads.textContent).toContain(READS_LINE), W);
    expect(reads.textContent).toMatch(/keys and its own records are never read/);
    expect(within(block).getByTestId("access-writes").textContent).toContain(
      "only in the folder it was given to work in: the chat's own folder or the project's folder",
    );
    const apps = within(block).getByTestId("access-apps");
    await waitFor(() => expect(apps.textContent).toContain("GitHub, Slack (not working)."), W);
    expect(apps.textContent).not.toContain("Notion");
    expect(apps.textContent).not.toContain("Work vault");
    expect(within(block).getByTestId("access-browser").textContent).toMatch(
      /^Your browserRead pages only: it can read the pages open in your own browser\./,
    );
    expect(within(block).getByTestId("access-agent-browser").textContent).toContain("Off.");
    const level = within(block).getByTestId("access-level");
    expect(level.textContent).toContain("Ask first");
    expect(level.textContent).toContain(PERMISSION_LEVELS.approve_for_me.description);
    const grants = within(block).getByTestId("access-grants");
    await waitFor(() => expect(grants.textContent).toContain("2 approvals you gave stay in place"), W);
    // It sits at the TOP of the group: before the group's first settings row.
    const firstRow = await screen.findByTestId("setting-mcp_auto_approve", {}, W);
    expect(block.compareDocumentPosition(firstRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("follows the real state: the window's level, the app tools switch, an allowlist, the agent browser", async () => {
    window.localStorage.setItem(CHAT_LEVEL_KEY, "yolo");
    seed({ mcp_auto_approve: true, agent_browser_enabled: true, browser_access: "off" });
    H.gets["/settings/access"] = { reads: { limited: true, folders: ["D:\\Shared"] }, writes: { chat_files_dir: "" } };
    H.gets["/grants"] = { grants: [] };
    openPermissions();
    const block = await screen.findByTestId("access-summary", {}, W);
    const reads = within(block).getByTestId("access-reads");
    await waitFor(() => expect(reads.textContent).toContain("only in the folders this install allows, without asking: D:\\Shared."), W);
    expect(reads.textContent).not.toContain("anywhere");
    const level = within(block).getByTestId("access-level");
    expect(level.textContent).toContain("Don't ask");
    expect(level.querySelector("[data-mode]")?.className).toContain("text-tone-warn");
    await waitFor(() =>
      expect(within(block).getByTestId("access-apps").textContent).toContain(
        "Their tools run without asking (Run app tools without asking is on).",
      ),
      W,
    );
    expect(within(block).getByTestId("access-browser").textContent).toContain("Off. Jarvis can't see or use your own browser.");
    expect(within(block).getByTestId("access-agent-browser").textContent).toContain(
      "On. Let agents drive their own separate browser (never your logged-in one).",
    );
    await waitFor(() =>
      expect(within(block).getByTestId("access-grants").textContent).toContain("None. Every ask follows the approval level."),
      W,
    );
  });

  it("a fact that cannot be read says so; it never reads as nothing", async () => {
    H.gets["/settings/access"] = new H.FakeApiError("Not Found", 404);
    H.gets["/connectors"] = new H.FakeApiError("boom", 500);
    H.gets["/grants"] = new H.FakeApiError("standing grants are not available", 503);
    openPermissions();
    const block = await screen.findByTestId("access-summary", {}, W);
    await waitFor(() =>
      expect(within(block).getByTestId("access-reads").textContent).toMatch(/unless this install limits it to chosen folders/),
      W,
    );
    await waitFor(() => expect(within(block).getByTestId("access-apps").textContent).toContain("Couldn’t read the list of apps."), W);
    expect(within(block).getByTestId("access-apps").textContent).not.toContain("None yet");
    await waitFor(() => expect(within(block).getByTestId("access-grants").textContent).toContain("Not available"), W);
  });

  it("'Manage apps' opens Connections › Directory; 'Review' opens Standing grants", async () => {
    openPermissions();
    const block = await screen.findByTestId("access-summary", {}, W);
    await waitFor(() => expect(within(block).getByTestId("access-grants").textContent).toContain("2 approvals"), W);
    fireEvent.click(within(block).getByRole("button", { name: "Review" }));
    const grantsSub = await screen.findByTestId("settings-sub-permissions-grants", {}, W);
    expect(within(grantsSub).getAllByRole("button")[0].getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(within(block).getByRole("button", { name: "Manage apps" }));
    await waitFor(() => expect(screen.getByTestId("settings-group").getAttribute("data-group")).toBe("connections"), W);
    const appsSub = await screen.findByTestId("settings-sub-connections-apps", {}, W);
    expect(within(appsSub).getAllByRole("button")[0].getAttribute("aria-expanded")).toBe("true");
    await screen.findByTestId("directory-stand-in", {}, W);
    // Away from Permissions & ledger the block is not drawn.
    expect(screen.queryByTestId("access-summary")).toBeNull();
  });

  it("CONTROL: other groups and a search do not draw it, and it reads nothing there", async () => {
    window.history.replaceState({}, "", "/settings?section=models");
    render(<SettingsHome />);
    await screen.findByTestId("setting-default_model", {}, W);
    expect(screen.queryByTestId("access-summary")).toBeNull();
    expect(H.calls.some((c) => c.startsWith("/settings/access") || c.startsWith("/grants"))).toBe(false);
  });
});
