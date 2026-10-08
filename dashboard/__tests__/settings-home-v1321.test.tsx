/**
 * Calm UI redesign S10 — Settings in seven groups, drawn from the ONE schema
 * (brief non-negotiable 3, AUDIT §4.4), the relocated pages as its sections
 * (Q4/Q8), and the "Changed here or in chat" ledger.
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
  return {
    FakeApiError,
    gets: {} as Record<string, unknown>,
    puts: [] as { path: string; body: unknown }[],
    posts: [] as { path: string; body: unknown }[],
    redirects: [] as string[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  get: async (path: string) => {
    const key = path.split("?")[0];
    const r = H.gets[key];
    if (r instanceof Error) throw r;
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 500);
    return r;
  },
  put: async (path: string, body: unknown) => {
    H.puts.push({ path, body });
    return { updated: Object.keys((body as { values: object }).values), action_id: "tool_77" };
  },
  post: async (path: string, body: unknown) => {
    H.posts.push({ path, body });
    return {};
  },
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: { providers: [] }, refresh: vi.fn() }),
}));
vi.mock("next/navigation", () => ({
  redirect: (to: string) => {
    H.redirects.push(to);
    throw new Error("NEXT_REDIRECT");
  },
  usePathname: () => "/settings",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
// The relocated pages are whole pages; here they are stand-ins that render a
// PageHeader, so what is pinned is the EMBEDDING (heading level, mounting on
// open), not each page's own behaviour (their own tests cover that).
vi.mock("@/components/settings/pages/NotificationsPage", async () => {
  const { PageHeader } = await import("@/components/PageHeader");
  return { default: () => <PageHeader title="Notifications" /> };
});
vi.mock("@/components/settings/pages/LegacySettingsForm", () => ({
  default: () => <div data-testid="legacy-form">legacy</div>,
  LocalCapabilitiesCard: () => <div>caps</div>,
  ContextWindowsCard: () => <div>windows</div>,
}));

import { SettingsHome } from "@/components/settings/SettingsHome";

const GROUPS = [
  { id: "models", label: "Models", description: "Which AI answers, and how." },
  { id: "connections", label: "Connections", description: "Accounts, keys, apps and alerts." },
  { id: "automation", label: "Agents & automation", description: "Limits." },
  { id: "memory", label: "Memory & you", description: "Who you are." },
  { id: "permissions", label: "Permissions & ledger", description: "What may run." },
  { id: "appearance", label: "Appearance", description: "Look." },
  { id: "system", label: "System", description: "Updates." },
];
const SETTINGS = [
  { key: "default_model", label: "Default model", group: "models", section: "", type: "string", help: "Which model", aliases: [] },
  {
    key: "local_primary_policy",
    label: "If the local model fails",
    group: "models",
    section: "Routing",
    type: "enum",
    tier: "ask-floor",
    options: [
      { value: "refuse", label: "Refuse" },
      { value: "failover", label: "Fail over" },
    ],
  },
  { key: "autonomy_kill_switch", label: "Kill switch", group: "automation", section: "Autonomy", type: "bool", aliases: ["emergency stop"] },
  { key: "max_agent_steps", label: "Max agent steps", group: "automation", section: "", type: "number" },
  { key: "permissions.{tool}", label: "Tool permission", group: "permissions", section: "", type: "enum", pattern: true },
];
const VALUES = { default_model: "", local_primary_policy: "refuse", autonomy_kill_switch: false, max_agent_steps: 20 };

beforeEach(() => {
  H.gets = {
    "/settings/schema": { groups: GROUPS, settings: SETTINGS, secrets: [] },
    "/settings/values": { values: VALUES },
    "/config/ledger": {
      changes: [
        { action_id: "a1", tool: "config_set", summary: "Changed Dry run", where: "chat", at: null, undoable: true, undone: false },
      ],
    },
  };
  H.puts = [];
  H.posts = [];
  H.redirects = [];
  window.history.replaceState({}, "", "/settings");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

describe("seven groups from the schema", () => {
  it("lists the schema's groups and draws each row from its definition", async () => {
    render(<SettingsHome />);
    const nav = await screen.findByTestId("settings-groups");
    expect(within(nav).getAllByRole("button").map((b) => b.textContent)).toEqual(GROUPS.map((g) => g.label));
    // Models is open first: its rows, typed by the schema.
    expect(await screen.findByTestId("setting-default_model")).toBeTruthy();
    const enumRow = screen.getByTestId("setting-local_primary_policy");
    expect(within(enumRow).getByRole("combobox")).toBeTruthy();
    expect(enumRow.textContent).toContain("protected");
    // A family key (per-tool permissions) is not a row.
    expect(screen.queryByTestId("setting-permissions.{tool}")).toBeNull();
    fireEvent.click(within(nav).getByRole("button", { name: "Agents & automation" }));
    expect(within(screen.getByTestId("setting-autonomy_kill_switch")).getByRole("switch")).toBeTruthy();
    // Nor in its own group.
    fireEvent.click(within(nav).getByRole("button", { name: "Permissions & ledger" }));
    expect(screen.queryByTestId("setting-permissions.{tool}")).toBeNull();
  });

  it("one save sends every change through the writer, with the device, and offers Undo", async () => {
    render(<SettingsHome />);
    const nav = await screen.findByTestId("settings-groups");
    fireEvent.change(within(await screen.findByTestId("setting-local_primary_policy")).getByRole("combobox"), {
      target: { value: "failover" },
    });
    fireEvent.click(within(nav).getByRole("button", { name: "Agents & automation" }));
    fireEvent.click(within(screen.getByTestId("setting-autonomy_kill_switch")).getByRole("switch"));
    const bar = screen.getByTestId("settings-save-bar");
    expect(bar.textContent).toContain("2 unsaved changes");
    fireEvent.click(within(bar).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(H.puts).toHaveLength(1));
    const body = H.puts[0].body as { values: Record<string, unknown>; device_id: string };
    expect(H.puts[0].path).toBe("/settings/values");
    expect(body.values).toEqual({ local_primary_policy: "failover", autonomy_kill_switch: true });
    expect(body.device_id).toMatch(/^dev_/);
    await screen.findByText(/Saved 2 settings/);
    fireEvent.click(within(screen.getByTestId("settings-save-bar")).getByRole("button", { name: /Undo/ }));
    await waitFor(() => expect(H.posts.map((p) => p.path)).toContain("/undo/tool_77"));
  });

  it("search finds a setting by its words across every group, and the pages by name", async () => {
    render(<SettingsHome />);
    await screen.findByTestId("settings-groups");
    fireEvent.change(screen.getByTestId("settings-search"), { target: { value: "emergency stop" } });
    expect(screen.getByTestId("setting-autonomy_kill_switch")).toBeTruthy();
    expect(screen.queryByTestId("setting-default_model")).toBeNull();
    fireEvent.change(screen.getByTestId("settings-search"), { target: { value: "notifications" } });
    expect(screen.getByTestId("settings-sub-connections-notifications")).toBeTruthy();
  });

  it("?focus=<key> opens that setting's group and highlights the row", async () => {
    window.history.replaceState({}, "", "/settings?focus=max_agent_steps");
    render(<SettingsHome />);
    const row = await screen.findByTestId("setting-max_agent_steps");
    await waitFor(() => expect(row.className).toContain("ring-2"));
    expect(screen.getByTestId("settings-group").getAttribute("data-group")).toBe("automation");
  });
});

describe("the relocated pages are sections (Q4/Q8)", () => {
  it("?section=… opens that page inside its group, titled as a section (one h1 on the page)", async () => {
    window.history.replaceState({}, "", "/settings?section=connections-notifications&focus=add");
    render(<SettingsHome />);
    const sub = await screen.findByTestId("settings-sub-connections-notifications");
    expect(within(sub).getByRole("button", { expanded: true })).toBeTruthy();
    const heading = await within(sub).findByRole("heading", { name: "Notifications" });
    expect(heading.tagName).toBe("H2");
    expect(document.querySelectorAll("h1")).toHaveLength(1);
    // Closed sections mount nothing.
    const accounts = screen.getByTestId("settings-sub-connections-accounts");
    expect(within(accounts).getByRole("button", { expanded: false })).toBeTruthy();
  });

  it("every old address redirects to its new home and keeps its query", async () => {
    const { RELOCATED, relocatedHref } = await import("@/lib/relocated");
    expect(relocatedHref("/settings", { focus: "endpoints" }, { section: "connections-accounts" })).toBe(
      "/settings?focus=endpoints&section=connections-accounts",
    );
    for (const route of Object.keys(RELOCATED)) {
      const mod = await import(`@/app${route}/page`);
      await expect(mod.default({ searchParams: Promise.resolve({ focus: "x" }) })).rejects.toThrow("NEXT_REDIRECT");
      const to = H.redirects.at(-1)!;
      expect(to.startsWith(RELOCATED[route].base)).toBe(true);
      expect(to).toContain("focus=x");
    }
    expect(Object.keys(RELOCATED).sort()).toEqual(
      ["/channels", "/computeruse", "/connections", "/kanban", "/marketplace", "/secrets", "/tools", "/train", "/updates", "/you"].sort(),
    );
  });
});

describe("the ledger and the older-daemon fallback", () => {
  it("Permissions & ledger lists changes made here or in chat, each with its Undo", async () => {
    window.history.replaceState({}, "", "/settings?section=permissions");
    render(<SettingsHome />);
    const row = await screen.findByTestId("settings-ledger-row");
    expect(row.textContent).toContain("Changed Dry run");
    expect(row.textContent).toContain("In chat");
    fireEvent.click(within(row).getByRole("button", { name: /Undo/ }));
    await waitFor(() => expect(H.posts.map((p) => p.path)).toContain("/undo/a1"));
  });

  it("a daemon with no schema (404) gets the previous form", async () => {
    H.gets["/settings/schema"] = new H.FakeApiError("not found", 404);
    render(<SettingsHome />);
    expect(await screen.findByTestId("legacy-form")).toBeTruthy();
  });
});

describe("Sessions: list or board (Q4a)", () => {
  it("?view=board shows the Session board inside Sessions", async () => {
    vi.doMock("@/components/sessions/SessionBoard", () => ({ default: () => <div data-testid="board">board</div> }));
    vi.resetModules();
    const { default: SessionsPage } = await import("@/app/sessions/page");
    window.history.replaceState({}, "", "/sessions?view=board");
    render(<SessionsPage />);
    expect(await screen.findByTestId("board")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Board" }).getAttribute("aria-selected")).toBe("true");
    await act(async () => {});
    vi.doUnmock("@/components/sessions/SessionBoard");
  });
});
