/**
 * v1.455.0 — readiness Wave H (docs/PRODUCT-READINESS-RESULTS.md 0j.5):
 *
 *  - 27  the drive cards' sign-in form links Help's step-by-step guide, and
 *        Google's sentence no longer asks to "allow" a redirect address on a
 *        Desktop client that has no box for one;
 *  - 29  the chat home's apps line carries the import offer;
 *  - 31  a web service's key can travel in a header or as a basic sign-in;
 *  - 33  a connected app's card says naming it in a message is enough
 *        (never for Microsoft 365, whose tools answer only in Local only);
 *  - 35  ONE connections front door: Discover / Connected / Import.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  responses: {} as Record<string, unknown>,
  gets: [] as string[],
  posts: [] as { path: string; body: unknown }[],
  failGet: "" as string,
  health: null as unknown,
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(m: string, s = 0) {
      super(m);
      this.status = s;
    }
  }
  return {
    ApiError,
    get: vi.fn((path: string) => {
      H.gets.push(path);
      if (H.failGet && path === H.failGet) return Promise.reject(new ApiError("boom", 500));
      return Promise.resolve(H.responses[path] ?? {});
    }),
    post: vi.fn((path: string, body?: unknown) => {
      H.posts.push({ path, body });
      return Promise.resolve({ id: "x", added: true });
    }),
    put: vi.fn(() => Promise.resolve({})),
    patch: vi.fn(() => Promise.resolve({})),
    del: vi.fn(() => Promise.resolve({})),
  };
});
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, health: H.health, refresh: () => {} }),
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/tools/AddPack", () => ({
  SourcesDoor: ({ sources }: { sources?: unknown[] }) => (
    <div data-testid="sources-door">{(sources ?? []).length} sources</div>
  ),
}));
vi.mock("next/dynamic", () => ({
  default: (load: () => Promise<unknown>) => {
    // Resolve the (mocked) module synchronously enough for a test: render
    // the SourcesDoor stub directly.
    void load;
    return ({ sources }: { sources?: unknown[] }) => (
      <div data-testid="sources-door">{(sources ?? []).length} sources</div>
    );
  },
}));

import { OAuthAppForm } from "@/components/connections/OAuthAppForm";
import { vendorConsoleSentence } from "@/lib/oauthApp";
import { OWN_APP_SECTIONS, SETUP_GUIDES, ownAppGuideHref } from "@/lib/directoryLinks";
import { AppsLine } from "@/components/chat/HomeReadiness";
import { CHAT_HINT_NAMED, CHAT_HINT_SWITCH_ONLY, chatHintWords } from "@/lib/extensionWords";
import { RestHookups } from "@/components/connections/RestHookups";
import {
  CONNECTIONS_HOME_HREF,
  ConnectionsHome,
  DISCOVER_DOORS,
  connectedRows,
  tabFromSearch,
} from "@/components/settings/ConnectionsHome";

beforeEach(() => {
  H.responses = {};
  H.gets.length = 0;
  H.posts.length = 0;
  H.failGet = "";
  H.health = null;
});
afterEach(() => cleanup());

/* ------------------------------------------------------------------ 27 */
describe("27 — the own-app guide is one press from the boxes", () => {
  it("each drive form links its own Help section", () => {
    for (const p of ["google_drive", "onedrive", "dropbox"]) {
      render(<OAuthAppForm provider={p} appName={p} />);
      const link = screen.getByTestId(`oauth-app-form-${p}-guide`);
      expect(link.getAttribute("href")).toBe(`/help?guide=own-apps&section=${OWN_APP_SECTIONS[p]}`);
      expect(link.textContent).toMatch(/Step-by-step setup/);
      cleanup();
    }
  });

  it("Gmail and the drives carry the same door on their Directory card; Microsoft 365 keeps its admin steps", () => {
    expect(SETUP_GUIDES.gmail.href).toBe(ownAppGuideHref("gmail"));
    expect(SETUP_GUIDES.microsoft_365.label).toBe("How your admin sets this up");
    expect(ownAppGuideHref("notion")).toBe("");
  });

  it("Google's Desktop client has no redirect box, so its sentence never asks to allow one", () => {
    expect(vendorConsoleSentence("google_drive", "Google Drive")).not.toMatch(/allow the redirect/);
    expect(vendorConsoleSentence("google_drive", "Google Drive")).toMatch(/nothing to allow/);
    expect(vendorConsoleSentence("dropbox", "Dropbox")).toMatch(/allow the redirect address below/);
  });
});

/* ------------------------------------------------------------------ 29 */
describe("29 — the chat home's apps line offers the tools found elsewhere", () => {
  const SOURCES = {
    sources: [
      {
        source: "cursor-project",
        label: "Cursor (folder ClosePilot)",
        readable: true,
        servers: [{ name: "repo-tool", exists: false }],
      },
    ],
  };

  it("with importOffer it reads the sources once and offers Import", async () => {
    H.responses["/mcp/import/sources"] = SOURCES;
    render(<AppsLine names={["GitHub"]} importOffer />);
    expect((await screen.findByTestId("home-apps-import-line")).textContent).toBe("Found 1 tool in Cursor.");
    fireEvent.click(screen.getByTestId("home-apps-import-open"));
    expect(screen.getByTestId("sources-door")).toBeTruthy();
    expect(H.gets.filter((g) => g === "/mcp/import/sources")).toHaveLength(1);
  });

  it("CONTROL: without it, the line reads nothing", async () => {
    render(<AppsLine names={["GitHub"]} />);
    expect(screen.getByTestId("home-apps")).toBeTruthy();
    await new Promise((r) => setTimeout(r, 0));
    expect(H.gets).toEqual([]);
  });
});

/* ------------------------------------------------------------------ 31 */
describe("31 — a web service's key can travel in a header or as a basic sign-in", () => {
  async function openForm() {
    H.responses["/integrations"] = { integrations: [] };
    render(<RestHookups />);
    fireEvent.click(await screen.findByRole("button", { name: /Add a service/ }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Pricing" } });
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://pricing.example.com" } });
  }

  it("a header key posts the header's name; the key stays the key", async () => {
    await openForm();
    expect(screen.queryByTestId("add-integ-key-in")).toBeNull(); // no key, nothing to choose
    fireEvent.change(screen.getByLabelText(/API key/), { target: { value: "k-1455" } });
    fireEvent.change(screen.getByLabelText("How the key is sent"), { target: { value: "header" } });
    fireEvent.change(screen.getByLabelText("Header name"), { target: { value: "X-API-Key" } });
    fireEvent.click(screen.getByRole("button", { name: /Add service/ }));
    await waitFor(() => expect(H.posts.map((p) => p.path)).toContain("/integrations"));
    expect(H.posts.find((p) => p.path === "/integrations")!.body).toEqual({
      name: "Pricing",
      base_url: "https://pricing.example.com",
      auth_token: "k-1455",
      auth_kind: "header",
      auth_header: "X-API-Key",
    });
  });

  it("CONTROL: a plain key posts exactly what it always did (bearer is the daemon's default)", async () => {
    await openForm();
    fireEvent.change(screen.getByLabelText(/API key/), { target: { value: "k-1455" } });
    fireEvent.click(screen.getByRole("button", { name: /Add service/ }));
    await waitFor(() => expect(H.posts.map((p) => p.path)).toContain("/integrations"));
    expect(H.posts.find((p) => p.path === "/integrations")!.body).toEqual({
      name: "Pricing",
      base_url: "https://pricing.example.com",
      auth_token: "k-1455",
    });
  });
});

/* ------------------------------------------------------------------ 33 */
describe("33 — naming a connected app in a message is enough (and said so)", () => {
  it("every app but Microsoft 365", () => {
    expect(chatHintWords("notion")).toBe(CHAT_HINT_NAMED);
    expect(CHAT_HINT_NAMED).toMatch(/off in chats until you turn it on in Tools › Connections/);
    // v1.455.0 review: true on the Chat page only (the phone's lane never arms
    // an app by name) and never in a Local only chat — the words say both.
    expect(CHAT_HINT_NAMED).toMatch(/on the Chat page you can also name it in your message/);
    expect(CHAT_HINT_NAMED).toMatch(/not in a Local only chat/);
    expect(chatHintWords("microsoft_365")).toBe(CHAT_HINT_SWITCH_ONLY);
  });
});

/* ------------------------------------------------------------------ 35 */
describe("35 — one connections front door", () => {
  it("opens on Discover; each door opens the page that owns it", () => {
    const onGo = vi.fn();
    render(<ConnectionsHome onGo={onGo} />);
    expect(screen.getByTestId("connections-tab-discover").getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByTestId("connections-door-apps"));
    expect(onGo).toHaveBeenCalledWith("connections", "connections-apps");
    fireEvent.click(screen.getByTestId("connections-door-extensions"));
    expect(onGo).toHaveBeenCalledWith("permissions", "permissions-tools");
    expect(screen.getByTestId("connections-door-model-servers").getAttribute("href")).toBe(
      "/connections?focus=endpoints",
    );
    expect(DISCOVER_DOORS.map((d) => d.id)).toEqual([
      "apps",
      "models",
      "model-servers",
      "extensions",
      "destinations",
      "browser",
      "remote",
    ]);
    // Nothing is read until a tab needs it.
    expect(H.gets).toEqual([]);
  });

  it("Connected lists models, apps and destinations in one place, read when opened", async () => {
    H.health = { providers: [{ provider: "claude-cli", available: true }, { provider: "mock", available: true }] };
    H.responses["/connectors"] = {
      connectors: [
        { id: "notion", name: "Notion", connect_via: "mcp", connected: true, status: "connected" },
        { id: "github", name: "GitHub", connect_via: "mcp", connected: true, status: "failed" },
        { id: "slack", name: "Slack", connect_via: "mcp", connected: false, status: "" },
      ],
    };
    H.responses["/comm/channels"] = { channels: [{ name: "phone", type: "telegram" }, { name: "this-pc", builtin: true }] };
    const onGo = vi.fn();
    render(<ConnectionsHome onGo={onGo} />);
    fireEvent.click(screen.getByTestId("connections-tab-connected"));
    const notion = await screen.findByTestId("connections-row-app:notion");
    expect(notion.textContent).toMatch(/Working/);
    expect(screen.getByTestId("connections-row-app:github").textContent).toMatch(/Needs attention/);
    expect(screen.queryByTestId("connections-row-app:slack")).toBeNull();
    expect(screen.getByTestId("connections-row-model:claude-cli")).toBeTruthy();
    expect(screen.queryByTestId("connections-row-model:mock")).toBeNull();
    expect(screen.getByTestId("connections-row-dest:phone")).toBeTruthy();
    expect(screen.queryByTestId("connections-row-dest:this-pc")).toBeNull();
    fireEvent.click(within(screen.getByTestId("connections-row-dest:phone")).getByRole("button", { name: "Manage" }));
    expect(onGo).toHaveBeenCalledWith("connections", "connections-notifications");
  });

  it("a Connected read that fails says so in one line, never an empty list", async () => {
    H.failGet = "/connectors";
    render(<ConnectionsHome onGo={() => {}} />);
    fireEvent.click(screen.getByTestId("connections-tab-connected"));
    expect((await screen.findByText(/Your connections couldn.t load/)).textContent).toBeTruthy();
    expect(screen.queryByTestId("connections-none")).toBeNull();
  });

  it("Import reads the other programs' tools and opens the Tools page's own door", async () => {
    H.responses["/mcp/import/sources"] = {
      sources: [{ source: "codex", label: "Codex", readable: true, servers: [{ name: "fs", exists: false }] }],
    };
    render(<ConnectionsHome onGo={() => {}} />);
    fireEvent.click(screen.getByTestId("connections-tab-import"));
    expect((await screen.findByTestId("connections-import-line")).textContent).toMatch(/Found 1 tool in Codex/);
    expect(screen.getByTestId("sources-door").textContent).toBe("1 sources");
  });

  it("the pure pieces: an address opens a tab, and the home's address is Settings › Connections", () => {
    expect(tabFromSearch("?section=connections&tab=import")).toBe("import");
    expect(tabFromSearch("?tab=nonsense")).toBe("discover");
    expect(CONNECTIONS_HOME_HREF).toBe("/connections?tab=discover");
    expect(connectedRows([], [], [])).toEqual([]);
  });
});
