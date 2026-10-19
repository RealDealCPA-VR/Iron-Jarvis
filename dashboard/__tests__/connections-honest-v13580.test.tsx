/**
 * v1.358.0 lane 8D — the Directory and Connections never claim more than is
 * true. Driven through the REAL components (DirectoryPage, SecretsPage,
 * LocalServerRows) and the REAL daemon rows: `fixtures/connections-honest-
 * v13580.json` is what GET /connectors sends (written by
 * tests/test_connections_honest_v13580.py: the three separate versions of
 * built-in tools, GitHub, a Pixio key refused on save, Microsoft 365 before
 * and after its sign-in). Only lib/api is a fake (the house pattern). Every
 * waitFor waits for the thing it asserts.
 *
 *  (1) the "Built in, nothing to connect" line never sits over a Connect
 *      card for one of those things: Files & folders / Fetch web pages /
 *      Knowledge graph live under their own fold, each saying how it differs;
 *  (2) "N connected" and its filter count WORKING cards; Off and Needs
 *      attention are said apart;
 *  (3) Microsoft 365 reads "Sign in to finish" until its own check says
 *      signed in — never green over 24 tools nobody can use yet;
 *  (4) a Pixio key refused on save says so, and its card says so;
 *  (5) Settings › Secrets reads "<App>: <field label>", the stored name a
 *      tooltip;
 *  (6) LM Studio / llama.cpp found on this PC are one press where Ollama is.
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
    posts: [] as { path: string; body: unknown }[],
    postAnswers: {} as Record<string, unknown>,
    connectors: [] as Record<string, unknown>[],
    categories: [] as string[],
    secrets: [] as Record<string, unknown>[],
    localServers: null as unknown,
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
    if (path === "/connectors") return { connectors: api.connectors, categories: api.categories };
    if (path === "/mcp/import/sources") return { sources: [] };
    if (path === "/mcp/servers") return { servers: [] };
    if (path === "/secrets") return { secrets: api.secrets };
    if (path === "/connections/local-servers") return api.localServers;
    return {};
  },
  post: async (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    return api.postAnswers[path] ?? { ok: true };
  },
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
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

const { default: DirectoryPage } = await import("@/components/settings/pages/DirectoryPage");
const { default: SecretsPage } = await import("@/components/settings/pages/SecretsPage");
const { LocalServerRows } = await import("@/components/connections/LocalServerRows");
const { cardHealth, healthCounts, keySaveNote } = await import("@/lib/directoryHealth");
const { secretWords, secretDescription, keyWords } = await import("@/lib/secretLabels");
const { answerCandidates, localServersDetected, LOCAL_SERVER_PROVIDERS } = await import("@/lib/onboarding");

/** Read a source the CI runner checked out with CRLF (the v1.232.1 rule). */
function src(rel: string): string {
  return readFileSync(join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");
}

type Row = Record<string, unknown> & {
  id: string;
  name: string;
  category: string;
  connect_via?: string;
  connected?: boolean;
  status?: string;
  enabled?: boolean | null;
};
const ROWS = JSON.parse(src("__tests__/fixtures/connections-honest-v13580.json")) as Record<string, Row>;

beforeEach(() => {
  api.posts = [];
  api.postAnswers = {};
  api.connectors = [];
  api.categories = ["Developer", "Productivity", "Data & Web", "AI & Media"];
  api.secrets = [];
  api.localServers = null;
});
afterEach(() => cleanup());

async function renderDirectory(rows: Row[]) {
  api.connectors = rows;
  render(<DirectoryPage />);
  return screen.findByTestId(`connector-card-${rows[0].id}`, {}, { timeout: 10_000 });
}

/* ------------------------------------------------------------------------ */
/* (1) one truth about what is built in                                       */
/* ------------------------------------------------------------------------ */
describe("(1) separate versions of built-in tools", () => {
  it("live under their own fold, never beside the apps under the built-in line", async () => {
    await renderDirectory([ROWS.github, ROWS.filesystem, ROWS.fetch, ROWS.memory]);
    // The line is what it always was.
    expect(screen.getByTestId("directory-built-in").textContent).toBe(
      "Built in, nothing to connect: Files & documents, Web, Images, Spreadsheets, Memory.",
    );
    const fold = await screen.findByTestId("directory-built-in-extras");
    expect(fold.tagName).toBe("DETAILS");
    expect((fold as HTMLDetailsElement).open).toBe(false);
    expect(within(fold).getByText("Separate versions of built-in tools (3)")).toBeTruthy();
    for (const id of ["filesystem", "fetch", "memory"]) {
      const card = screen.getByTestId(`connector-card-${id}`);
      expect(fold.contains(card), `${id} is inside the fold`).toBe(true);
      expect(within(card).getByTestId(`connector-built-in-note-${id}`).textContent).toMatch(
        /^Iron Jarvis already .* without this\. This is a separate /,
      );
    }
    // A real app stays among the apps.
    expect(fold.contains(screen.getByTestId("connector-card-github"))).toBe(false);
  });

  it("opens by itself for a search its cards answer, and when one of them is saved", async () => {
    await renderDirectory([ROWS.github, ROWS.filesystem, ROWS.fetch]);
    const fold = (await screen.findByTestId("directory-built-in-extras")) as HTMLDetailsElement;
    expect(fold.open).toBe(false);
    fireEvent.change(screen.getByLabelText("Search connections"), { target: { value: "folder" } });
    await waitFor(() => expect((screen.getByTestId("directory-built-in-extras") as HTMLDetailsElement).open).toBe(true));
    expect(screen.getByTestId("connector-card-filesystem")).toBeTruthy();
    cleanup();
    await renderDirectory([
      ROWS.github,
      { ...ROWS.filesystem, connected: true, status: "connected", tools_loaded: 14 },
    ]);
    await waitFor(() => expect((screen.getByTestId("directory-built-in-extras") as HTMLDetailsElement).open).toBe(true));
  });
});

/* ------------------------------------------------------------------------ */
/* (2) the count and the filter read each card's own health                    */
/* ------------------------------------------------------------------------ */
describe("(2) N connected counts what works", () => {
  const working = { ...ROWS.github, connected: true, status: "connected", tools_loaded: 3 };
  const off = { ...ROWS.github, id: "notion", name: "Notion", connected: true, status: "off", enabled: false, tools_loaded: 0 };
  const failed = { ...ROWS.github, id: "slack", name: "Slack", connected: true, status: "failed", tools_loaded: 0 };

  it("says off and needs-attention apart from the count, and the filter keeps only working cards", async () => {
    await renderDirectory([working, off, failed, ROWS.ms365_signin, ROWS.pixio_refused]);
    const line = await screen.findByTestId("directory-summary");
    const t = (line.textContent ?? "").replace(/\s+/g, " ");
    expect(t).toMatch(/\b1 connected\b/);
    expect(screen.getByTestId("directory-count-off").textContent).toBe("1 off");
    // Slack (didn't start), Microsoft 365 (sign-in to finish), Pixio (key refused).
    expect(screen.getByTestId("directory-count-attention").textContent).toBe("3 need attention");
    // "tools live" counts working cards only (Microsoft 365's 24 are not live).
    expect(t).toMatch(/\b3 tools live\b/);

    const filter = screen.getAllByRole("button", { name: /connected/i }).find((b) => b.hasAttribute("aria-pressed"))!;
    fireEvent.click(filter);
    await waitFor(() => expect(filter.getAttribute("aria-pressed")).toBe("true"));
    expect(screen.getByTestId("connector-card-github")).toBeTruthy();
    for (const id of ["notion", "slack", "microsoft_365", "pixio"]) {
      expect(screen.queryByTestId(`connector-card-${id}`), id).toBeNull();
    }
  });

  it("is one reading for the card and the page (lib/directoryHealth)", () => {
    expect(cardHealth(working)).toBe("working");
    expect(cardHealth(off)).toBe("off");
    expect(cardHealth(failed)).toBe("attention");
    expect(cardHealth(ROWS.ms365_signin)).toBe("signin");
    expect(cardHealth(ROWS.ms365_signed_in)).toBe("working");
    expect(cardHealth(ROWS.github)).toBe("none");
    expect(healthCounts([working, off, failed, ROWS.ms365_signin])).toEqual({ working: 1, off: 1, attention: 2 });
    const page = src("components/settings/pages/DirectoryPage.tsx");
    expect(page).toContain("const health = cardHealth(c);");
    expect(page).toContain("connectedOnly && cardHealth(c) !== \"working\"");
  });
});

/* ------------------------------------------------------------------------ */
/* (3) Microsoft 365 is green only once it is signed in                        */
/* ------------------------------------------------------------------------ */
describe("(3) Microsoft 365 before and after its sign-in", () => {
  it("reads Sign in to finish (never Connected) above the existing Sign in press", async () => {
    const card = await renderDirectory([ROWS.ms365_signin]);
    expect(card.getAttribute("data-health")).toBe("signin");
    expect(within(card).getByTestId("connector-signin-needed-microsoft_365").textContent).toBe("Sign in to finish");
    expect(within(card).queryByText("Connected")).toBeNull();
    expect(within(card).getByTestId("connector-signin-line-microsoft_365").textContent).toBe(
      "Sign in to Microsoft to finish.",
    );
    const press = within(card).getByTestId("connector-signin-press-microsoft_365");
    expect(press.textContent).toContain("Sign in to Microsoft");
    // No list of 24 tools that would all fail until then.
    expect(within(card).queryByText("get-calendar-view")).toBeNull();
    fireEvent.click(press);
    await within(card).findByTestId("connector-signin-hint-microsoft_365");
    expect(api.posts).toEqual([{ path: "/terminals/launch", body: { cli: "microsoft-365-signin-work" } }]);
  });

  it("is green and says who once the daemon's check says signed in", async () => {
    const card = await renderDirectory([ROWS.ms365_signed_in]);
    expect(card.getAttribute("data-health")).toBe("connected");
    expect(within(card).getByText("Connected")).toBeTruthy();
    expect(within(card).queryByTestId("connector-signin-needed-microsoft_365")).toBeNull();
    expect(within(card).getByTestId("connector-signin-state-microsoft_365").textContent).toBe(
      "Signed in as dana@contoso.com.",
    );
  });
});

/* ------------------------------------------------------------------------ */
/* (4) a key is checked on save                                                */
/* ------------------------------------------------------------------------ */
describe("(4) an API key card", () => {
  const unsaved = { ...ROWS.pixio_refused, connected: false, status: "disconnected", detail: undefined, technical: undefined, checked: undefined };

  it("says a refused key was refused on save — never 'Pixio connected.'", async () => {
    api.postAnswers["/connectors/pixio/connect"] = {
      ok: true,
      connector: "pixio",
      test: { ok: false, detail: ROWS.pixio_refused.detail },
    };
    const card = await renderDirectory([unsaved as Row]);
    fireEvent.click(within(card).getByRole("button", { name: /Connect/ }));
    fireEvent.change(within(card).getByLabelText("Pixio API key"), { target: { value: "pxio_typo" } });
    const submit = within(card).getAllByRole("button", { name: /Connect/ }).at(-1)!;
    await waitFor(() => expect((submit as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(submit);
    await within(card).findByText(
      "Saved. Pixio didn't accept this key. Remove it and connect again with a working key.",
    );
    expect(within(card).queryByText(/Pixio connected/)).toBeNull();
  });

  it("the card of a refused key needs attention and says why (the check's text on hover)", async () => {
    const card = await renderDirectory([ROWS.pixio_refused]);
    expect(card.getAttribute("data-health")).toBe("attention");
    expect(within(card).getByTestId("connector-attention-pixio")).toBeTruthy();
    const line = within(card).getByTestId("connector-key-failed-pixio");
    expect(line.textContent).toBe(ROWS.pixio_refused.detail);
    expect(line.getAttribute("title")).toBe(ROWS.pixio_refused.technical);
    expect(within(card).queryByText("Ready to use")).toBeNull();
  });

  it("keySaveNote: a passed check, a refused one, an older daemon", () => {
    expect(keySaveNote("Pixio", { ok: true, detail: "Pixio accepted the key." })).toEqual({
      ok: true,
      text: "Pixio connected. The key works.",
    });
    expect(keySaveNote("Pixio", { ok: false, detail: "Pixio didn't accept this key." }).ok).toBe(false);
    expect(keySaveNote("Pixio", undefined)).toEqual({ ok: true, text: "Pixio connected." });
  });
});

/* ------------------------------------------------------------------------ */
/* (5) Settings › Secrets in plain words                                       */
/* ------------------------------------------------------------------------ */
describe("(5) Secrets", () => {
  it("shows '<App>: <field label>' for an app's key; the stored name is only the tooltip", async () => {
    api.connectors = [ROWS.github, ROWS.filesystem];
    api.secrets = [
      { name: "mcp.github.auth.value", kind: "api_key", description: "MCP pack github: Authorization value", updated_at: null },
      { name: "mcp.notes_pack.NOTES_API_TOKEN", kind: "api_key", description: "MCP pack notes_pack: NOTES_API_TOKEN", updated_at: null },
      { name: "anthropic_api_key", kind: "api_key", description: "", updated_at: null },
    ];
    render(<SecretsPage />);
    const gh = await screen.findByTestId("secret-row-mcp.github.auth.value");
    await waitFor(() => expect(within(gh).getByText("GitHub: GitHub personal access token")).toBeTruthy());
    expect(within(gh).getByText("GitHub: GitHub personal access token").getAttribute("title")).toBe(
      "mcp.github.auth.value",
    );
    expect(within(gh).getByText("Used by GitHub")).toBeTruthy();
    expect(gh.textContent).not.toMatch(/MCP pack|mcp\.github/);
    const mine = screen.getByTestId("secret-row-mcp.notes_pack.NOTES_API_TOKEN");
    expect(within(mine).getByText("notes_pack: Notes API token")).toBeTruthy();
    // A key that is not an app's is shown as it was stored.
    expect(within(screen.getByTestId("secret-row-anthropic_api_key")).getByText("anthropic_api_key")).toBeTruthy();
  });

  it("reads every shape an add door writes, and the Directory's older names", () => {
    const apps = [ROWS.github as never, ROWS.filesystem as never];
    expect(secretWords("mcp.github.GITHUB_PERSONAL_ACCESS_TOKEN", apps)?.label).toBe(
      "GitHub: GitHub personal access token",
    );
    expect(secretWords("conn_github_GITHUB_PERSONAL_ACCESS_TOKEN", apps)?.label).toBe(
      "GitHub: GitHub personal access token",
    );
    expect(secretWords("mcp.filesystem.arg.0", apps)?.label).toBe("Files & folders: Folder path");
    expect(secretWords("mcp.x.header.X-Api-Key", [])?.label).toBe("x: X-Api-Key header");
    expect(secretWords("mcp.x.url", [])?.label).toBe("x: Server address");
    expect(secretWords("pixio_api_key", apps)).toBeNull();
    expect(keyWords("BRAVE_API_KEY")).toBe("Brave API key");
    expect(secretDescription("my own words", { app: "GitHub", label: "" })).toBe("my own words");
  });
});

/* ------------------------------------------------------------------------ */
/* (6) LM Studio / llama.cpp, one press where Ollama is                        */
/* ------------------------------------------------------------------------ */
describe("(6) model servers on this PC", () => {
  const health = (detected: unknown) => ({
    default_provider: "mock",
    providers: [
      { provider: "anthropic", available: true, class: "api" },
      { provider: "codex-cli", available: true, class: "cli" },
      { provider: "custom", available: false, detected_servers: detected },
    ],
  });

  it("the one-press list offers a detected LM Studio beside the local servers, in its own words", () => {
    const h = health([
      { key: "lmstudio", label: "LM Studio (on this PC)", base_url: "http://localhost:1234/v1" },
      { key: "weird", label: "nope" },
    ]) as never;
    expect(localServersDetected(h)).toEqual([{ provider: "lmstudio", label: "LM Studio (on this PC)" }]);
    expect(answerCandidates(h).map((c) => c.provider)).toEqual(["anthropic", "lmstudio", "codex-cli"]);
    expect(answerCandidates(h).find((c) => c.provider === "lmstudio")).toEqual({
      provider: "lmstudio",
      label: "LM Studio (on this PC)",
      where: "stays on this PC",
    });
    // An older daemon (no field) offers exactly what it offered before.
    expect(answerCandidates(health(undefined) as never).map((c) => c.provider)).toEqual(["anthropic", "codex-cli"]);
    expect(LOCAL_SERVER_PROVIDERS).toEqual(["lmstudio", "llamacpp"]);
  });

  it("the Accounts rows offer a running server in one press that connects (never chooses)", async () => {
    api.localServers = {
      servers: [
        { key: "lmstudio", name: "LM Studio", label: "LM Studio (on this PC)", base_url: "http://localhost:1234/v1", running: true, models: 1, slot: "free" },
        { key: "llamacpp", name: "llama.cpp", label: "llama.cpp (on this PC)", base_url: "http://localhost:8080/v1", running: false, models: 0, slot: "free" },
      ],
    };
    api.postAnswers["/connections/local-servers/lmstudio/connect"] = {
      ok: true,
      detail: "LM Studio is connected. Pick it in any model menu as your own model server.",
    };
    render(<LocalServerRows />);
    const row = await screen.findByTestId("local-server-row-lmstudio", {}, { timeout: 10_000 });
    expect(screen.queryByTestId("local-server-row-llamacpp")).toBeNull();
    const press = within(row).getByRole("button", { name: "Use LM Studio on this PC" });
    fireEvent.click(press);
    await within(row).findByText("LM Studio is connected. Pick it in any model menu as your own model server.");
    expect(api.posts).toEqual([{ path: "/connections/local-servers/lmstudio/connect", body: undefined }]);
  });

  it("a slot that holds another address is never replaced from the row", async () => {
    api.localServers = {
      servers: [
        { key: "lmstudio", name: "LM Studio", label: "LM Studio (on this PC)", base_url: "http://localhost:1234/v1", running: true, models: 1, slot: "taken" },
      ],
    };
    render(<LocalServerRows />);
    const row = await screen.findByTestId("local-server-row-lmstudio", {}, { timeout: 10_000 });
    expect(within(row).getByTestId("local-server-taken-lmstudio").textContent).toMatch(/Add an endpoint/);
    expect(within(row).queryByRole("button")).toBeNull();
  });

  it("the Connections page mounts the rows in the Accounts card, under Local Ollama", () => {
    const page = src("components/settings/pages/ConnectionsPage.tsx");
    const ollama = page.indexOf("{CLI_PROVIDERS.map((info) => (");
    const rows = page.indexOf("<LocalServerRows />");
    expect(ollama).toBeGreaterThan(0);
    expect(rows).toBeGreaterThan(ollama);
  });
});
