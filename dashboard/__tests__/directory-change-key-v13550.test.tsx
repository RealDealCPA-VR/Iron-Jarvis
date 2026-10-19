/**
 * v1.355.0 lane 5B (product-readiness wave 5): the Directory card of a
 * CONNECTED app.
 *
 * - G69: "Change key". A user whose GitHub token expired presses Change key
 *   on the card, pastes a new one, and the app works again without
 *   disconnecting: the press opens the Tools page's OWN editor
 *   (components/tools/EditPack, seeded from GET /mcp/servers) and Save sends
 *   PATCH /mcp/servers/github with the new token only — never a DELETE.
 * - G70: a connected card says what its tools can actually do — "Can read ·
 *   Can change (2 tools) · Asks before changing anything" — read off the
 *   daemon's row (can_change / asks_first / held_back), not the catalog's
 *   hand-written scopes, which stay for a card not connected yet.
 *
 * Driven through the REAL DirectoryPage, the real EditPack and the real
 * useApi; only lib/api is a fake (the house pattern). Every waitFor waits for
 * the thing it asserts.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    gets: [] as string[],
    patches: [] as { path: string; body: unknown }[],
    dels: [] as string[],
    connectors: [] as Record<string, unknown>[],
    servers: [] as Record<string, unknown>[],
    patchAnswer: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  sseUrl: (p: string) => p,
  wsUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  get: async (path: string) => {
    api.gets.push(path);
    if (path === "/connectors") return { connectors: api.connectors, categories: ["Developer"] };
    if (path === "/mcp/import/sources") return { sources: [] };
    if (path === "/mcp/servers") return { servers: api.servers };
    return {};
  },
  post: async () => ({}),
  put: async () => ({}),
  patch: async (path: string, body?: unknown) => {
    api.patches.push({ path, body });
    return api.patchAnswer;
  },
  del: async (path: string) => {
    api.dels.push(path);
    return {};
  },
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

const { __resetApiCache } = await import("@/lib/apiCache");
const DirectoryPage = (await import("@/components/settings/pages/DirectoryPage")).default;
const { packPowersLine } = await import("@/lib/packPowers");

const NEW_TOKEN = "ghp_LANE5B_dashboard_NEVERSHOWN";

function connector(id: string, name: string, over: Record<string, unknown> = {}) {
  return {
    id,
    name,
    category: "Developer",
    glyph: "",
    blurb: `${name} blurb.`,
    unlocks: `${name} unlocks.`,
    connect_via: "mcp",
    scopes: ["Repositories", "Issues & pull requests"],
    docs_url: "",
    fields: [{ name: "GITHUB_PERSONAL_ACCESS_TOKEN", label: "GitHub personal access token", help: "", kind: "secret", optional: false }],
    provider: "",
    connected: false,
    status: "disconnected",
    tools_loaded: 0,
    tool_names: [],
    needs: "",
    package_note: "",
    href: "",
    action: "",
    ...over,
  };
}

/** GitHub, connected to its own server: 5 tools, 2 of them change things. */
function connectedGitHub(over: Record<string, unknown> = {}) {
  return connector("github", "GitHub", {
    connected: true,
    status: "connected",
    tools_loaded: 5,
    tool_names: ["get_me", "search_repositories", "list_issues", "create_issue", "merge_pull_request"],
    can_change: 2,
    asks_first: true,
    held_back: 0,
    keyed: true,
    ...over,
  });
}

/** Its GET /mcp/servers row: names only, the token is `value_set`. */
const GITHUB_ROW = {
  name: "github",
  transport: "http",
  url: "https://api.githubcopilot.com/mcp/",
  args: [],
  env_keys: [],
  secret_keys: [],
  header_names: [],
  auth: { header: "Authorization", format: "Bearer {value}", value_set: true },
  auth_set: true,
  tools_loaded: 5,
};

const card = (id: string) => screen.getByTestId(`connector-card-${id}`);

function mount() {
  return render(<DirectoryPage />);
}

beforeEach(() => {
  localStorage.clear();
  __resetApiCache();
  api.gets = [];
  api.patches = [];
  api.dels = [];
  api.connectors = [connectedGitHub()];
  api.servers = [GITHUB_ROW];
  api.patchAnswer = { ...GITHUB_ROW, reloaded: true, note: null, tools_loaded: 5 };
});
afterEach(() => cleanup());

/* ------------------------------------------------------------- G70 words --- */

describe("packPowersLine", () => {
  it("names how many tools can change things and that they ask first", () => {
    expect(packPowersLine({ tools_loaded: 5, can_change: 2, asks_first: true })).toBe(
      "Can read · Can change (2 tools) · Asks before changing anything",
    );
    expect(packPowersLine({ tools_loaded: 5, can_change: 1, asks_first: true })).toBe(
      "Can read · Can change (1 tool) · Asks before changing anything",
    );
  });

  it("says when they change things without asking, and which still ask", () => {
    expect(packPowersLine({ tools_loaded: 5, can_change: 2, asks_first: false })).toBe(
      "Can read · Can change (2 tools) · Changes things without asking",
    );
    expect(packPowersLine({ tools_loaded: 5, can_change: 2, asks_first: false, held_back: 1 })).toBe(
      "Can read · Can change (2 tools) · Changes things without asking, except 1 tool added since, which ask first",
    );
  });

  it("a read-only app changes nothing; an all-writing app does not claim it only reads", () => {
    expect(packPowersLine({ tools_loaded: 3, can_change: 0, asks_first: true })).toBe("Can read · Changes nothing");
    expect(packPowersLine({ tools_loaded: 2, can_change: 2, asks_first: true })).toBe(
      "Can change (2 tools) · Asks before changing anything",
    );
  });

  it("says nothing without the daemon's counts or with no tools loaded", () => {
    expect(packPowersLine({ tools_loaded: 5 })).toBeNull();
    expect(packPowersLine({ tools_loaded: 0, can_change: 0, asks_first: true })).toBeNull();
    expect(packPowersLine(null)).toBeNull();
  });
});

/* ------------------------------------------------------------- G70 card ---- */

describe("a connected card says what the app can actually do (G70)", () => {
  it("GitHub's card says how many tools can change things and that they ask first", async () => {
    mount();
    const line = await screen.findByTestId("connector-powers-github");
    expect(line.textContent).toBe("Can read · Can change (2 tools) · Asks before changing anything");
    // The enforced picture replaces the hand-written scopes on a connected card.
    expect(within(card("github")).queryByText("Repositories")).toBeNull();
    // A quiet grey line: no box, no border, a theme token.
    expect(line.tagName).toBe("P");
    expect(line.className).toContain("text-zinc-400");
    expect(line.className).not.toMatch(/border|shadow|bg-/);
  });

  it("says when its tools run without asking", async () => {
    api.connectors = [connectedGitHub({ asks_first: false })];
    mount();
    await waitFor(() =>
      expect(screen.getByTestId("connector-powers-github").textContent).toContain("Changes things without asking"),
    );
  });

  it("a card not connected keeps its scopes and says nothing it cannot know", async () => {
    api.connectors = [connector("github", "GitHub")];
    mount();
    await within(await screen.findByTestId("connector-card-github")).findByText("Repositories");
    expect(screen.queryByTestId("connector-powers-github")).toBeNull();
    expect(screen.queryByTestId("connector-change-key-github")).toBeNull();
  });

  it("an older daemon's row (no counts) keeps the scopes", async () => {
    api.connectors = [connectedGitHub({ can_change: undefined, asks_first: undefined, held_back: undefined })];
    mount();
    await within(await screen.findByTestId("connector-card-github")).findByText("Repositories");
    expect(screen.queryByTestId("connector-powers-github")).toBeNull();
  });
});

/* ------------------------------------------------------------- G69 --------- */

describe("Change key on a connected card (G69)", () => {
  it("an expired token is changed in place: the editor, the new token, PATCH — never a disconnect", async () => {
    // The card a user sees when GitHub refused the old token.
    api.connectors = [
      connectedGitHub({
        status: "failed",
        tools_loaded: 0,
        tool_names: [],
        reason: "the pack refused our credentials",
        detail: "HTTPStatusError: 401",
      }),
    ];
    mount();
    const press = await screen.findByTestId("connector-change-key-github");
    expect(press.textContent).toContain("Change key");
    fireEvent.click(press);

    // The Tools page's OWN editor, seeded from the app's GET /mcp/servers row.
    const editor = await screen.findByTestId("edit-pack-github");
    expect(api.gets).toContain("/mcp/servers");
    // The token is shown as set, never as a value.
    expect(within(editor).getByTestId("edit-token-github-chip").textContent).toContain("set");
    fireEvent.click(within(editor).getByTestId("edit-token-github-replace"));
    const box = within(editor).getByTestId("edit-token-github") as HTMLInputElement;
    expect(box.type).toBe("password");
    fireEvent.change(box, { target: { value: NEW_TOKEN } });
    api.connectors = [connectedGitHub()];
    fireEvent.click(within(editor).getByTestId("edit-save-github"));

    await within(card("github")).findByText("Key changed. 5 tools ready to use.");
    expect(api.patches).toEqual([
      {
        path: "/mcp/servers/github",
        body: { auth: { header: "Authorization", format: "Bearer {value}", value: NEW_TOKEN } },
      },
    ]);
    expect(api.dels).toEqual([], "Change key never disconnects");
    expect(screen.queryByTestId("edit-pack-github")).toBeNull();
    // The card re-reads the gallery and is green again.
    await waitFor(() => expect(card("github").getAttribute("data-health")).toBe("connected"));
    expect(document.body.textContent ?? "").not.toContain(NEW_TOKEN);
  });

  it("a key saved over an app that still does not start says why, beside its Retry", async () => {
    api.patchAnswer = {
      ...GITHUB_ROW,
      reloaded: false,
      tools_loaded: 0,
      note: "saved, but it did not start: the pack refused our credentials; see its row for the fix",
    };
    mount();
    fireEvent.click(await screen.findByTestId("connector-change-key-github"));
    const editor = await screen.findByTestId("edit-pack-github");
    fireEvent.click(within(editor).getByTestId("edit-token-github-replace"));
    fireEvent.change(within(editor).getByTestId("edit-token-github"), { target: { value: NEW_TOKEN } });
    fireEvent.click(within(editor).getByTestId("edit-save-github"));
    await within(card("github")).findByText(
      "Key saved, but GitHub didn't start: the pack refused our credentials. Press Retry.",
    );
  });

  it("Cancel closes the editor and changes nothing", async () => {
    mount();
    fireEvent.click(await screen.findByTestId("connector-change-key-github"));
    const editor = await screen.findByTestId("edit-pack-github");
    fireEvent.click(within(editor).getByTestId("edit-cancel-github"));
    await waitFor(() => expect(screen.queryByTestId("edit-pack-github")).toBeNull());
    expect(api.patches).toEqual([]);
  });

  it("is offered only on an app that holds a key", async () => {
    api.connectors = [
      connectedGitHub(),
      connector("fetch", "Web Fetch", { connected: true, status: "connected", tools_loaded: 1, can_change: 0, asks_first: true, keyed: false, fields: [] }),
    ];
    mount();
    await screen.findByTestId("connector-change-key-github");
    await screen.findByTestId("connector-card-fetch");
    expect(screen.queryByTestId("connector-change-key-fetch")).toBeNull();
    // The keyless app still says what it can do.
    expect(screen.getByTestId("connector-powers-fetch").textContent).toBe("Can read · Changes nothing");
  });
});

/* ------------------------------------------------------------ reuse pins --- */

function src(rel: string): string {
  return readFileSync(join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");
}

describe("one editor, one set of words", () => {
  it("the Directory mounts the Tools page's EditPack, never a second editor", () => {
    const page = src("components/settings/pages/DirectoryPage.tsx");
    expect(page).toContain('import { EditPack, type EditSaved } from "@/components/tools/EditPack";');
    expect(page).toContain("<EditPack");
    expect(page).not.toMatch(/patch\(`\/mcp\/servers/);
    // The reason after a failed reload is the shared helper's, said once.
    expect(page).toContain('import { packStartReason } from "@/lib/packStartWords";');
    expect(page).toContain('import { packPowersLine } from "@/lib/packPowers";');
  });
});
