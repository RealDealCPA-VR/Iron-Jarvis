/**
 * v1.354.0 (product readiness, lane 4E): Accounts & keys tells a new user the
 * truth about model servers and about the REST hookups.
 *
 * G47: the user's own model server card read "Not connected" while that
 *   keyless endpoint answered every chat, and saving a mistyped address always
 *   showed a green "Endpoint saved" (the check's error was dropped). Now the
 *   card is "Model servers on your network", its pill reads "Connected · your
 *   endpoint · no key needed" from the daemon's own answer (`source`,
 *   connections/registry ENDPOINT_SOURCE), it KEEPS its endpoint list and add
 *   form, and a save whose check could not reach the server is a warning that
 *   names the address and the check's error.
 *   (The words separate with "·": Connections copy carries no dash asides,
 *   connections-calm-k2-v1329. The daemon's raw source rides the title.)
 * G48: "Direct REST hookups" promised to connect any API while no tool can call
 *   one; it sits in a closed "More ways to connect (advanced)" fold after every
 *   real door and says it is a connection check only. The extensions tile and
 *   the Add connection menu read "Add a tool".
 *
 * Real page, real RestHookups, real lib/modelServers; only the network seams
 * are mocked.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/* ------------------------------------------------------------------ mocks */

const hooks = vi.hoisted(() => {
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
    responses: {} as Record<string, unknown>,
    posts: [] as { path: string; body: unknown }[],
    /** Answers for POST …/verify, taken in order (an Error is thrown). */
    verify: [] as unknown[],
    /** A read's error by path (status 0 = the daemon is not answering). */
    errors: {} as Record<string, { status: number; message: string }>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: hooks.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  sseUrl: (p: string) => p,
  wsUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  get: vi.fn((p: string) => Promise.resolve(hooks.responses[p] ?? {})),
  post: vi.fn((p: string, body?: unknown) => {
    hooks.posts.push({ path: p, body });
    if (p === "/fleet/nodes") return Promise.resolve({ node: { id: "box", label: "box" } });
    if (p.endsWith("/verify")) {
      const next = hooks.verify.shift() ?? { tool_use: true };
      return next instanceof Error ? Promise.reject(next) : Promise.resolve(next);
    }
    return Promise.resolve({ ok: true });
  }),
  put: vi.fn(() => Promise.resolve({})),
  patch: vi.fn(() => Promise.resolve({})),
  del: vi.fn(() => Promise.resolve({})),
}));
vi.mock("@/lib/useApi", () => {
  const read = (p: string | null) => ({
    data: p ? (hooks.responses[p] ?? null) : null,
    error: p ? (hooks.errors[p] ?? null) : null,
    loading: false,
    reload: () => {},
  });
  return { useApi: read, usePolledApi: read };
});
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    health: { default_provider: "mock", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {}, prefetch: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const strip = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) {
      if (!["initial", "animate", "exit", "transition", "variants", "layout", "whileHover", "whileTap"].includes(k)) rest[k] = v;
    }
    return createElement(tag, rest);
  };
  const motion = new Proxy({} as Record<string, unknown>, { get: (_t, tag) => strip(String(tag)) });
  return {
    m: motion,
    motion,
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
  };
});
vi.mock("@/components/connections/IronProxyCard", () => ({ IronProxyCard: () => null }));
vi.mock("@/components/connections/EnvelopeCard", () => ({
  EnvelopeRowControls: () => null,
  MeasuredEndpoints: () => null,
}));
vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null, BrandGlyph: () => null }));

import ConnectionsPage from "@/components/settings/pages/ConnectionsPage";
import { RestHookups } from "@/components/connections/RestHookups";
import { OFFLINE_TITLE } from "@/lib/offlineWords";
import {
  MODEL_SERVERS_TITLE,
  OWN_ENDPOINT_LABEL,
  isOwnEndpoint,
  unreachableNote,
} from "@/lib/modelServers";

/* ---------------------------------------------------------------- helpers */

/** The daemon's ENDPOINT_SOURCE, read off the Python file (CRLF folded). */
const REGISTRY = readFileSync(
  path.join(__dirname, "..", "..", "src", "iron_jarvis", "connections", "registry.py"),
  "utf8",
).replace(/\r\n/g, "\n");
const ENDPOINT_SOURCE = /^ENDPOINT_SOURCE = "([^"]+)"$/m.exec(REGISTRY)?.[1] ?? "";

function customRow(over: Record<string, unknown> = {}) {
  return {
    provider: "custom",
    display_name: "Custom endpoint",
    method: "api_key",
    supports_oauth: false,
    supports_api_key: true,
    oauth_help: "",
    connected: false,
    status: "disconnected",
    account: "",
    source: "",
    scopes: [],
    ...over,
  };
}

function seed(row: Record<string, unknown>, nodes: unknown[] = []) {
  hooks.responses["/connections"] = { connections: [row] };
  hooks.responses["/routing/quality"] = { bar: 0.75, min_samples: 3, rows: [] };
  hooks.responses["/fleet"] = { nodes };
  hooks.responses["/integrations"] = { integrations: [] };
}

const SAVED_NODE = {
  node: {
    id: "lab",
    label: "LM Studio",
    base_url: "http://192.168.1.20:1234/v1",
    source: "user",
    routable: true,
    default_model: "qwen3",
    api_key_name: "",
    tool_use: null,
  },
};

async function card() {
  render(<ConnectionsPage />);
  return (await screen.findByText(MODEL_SERVERS_TITLE)).closest("#conn-card-custom") as HTMLElement;
}

async function saveEndpoint(c: HTMLElement, url: string) {
  fireEvent.click(within(c).getByRole("button", { name: /Add (an|another) endpoint/ }));
  fireEvent.change(within(c).getByPlaceholderText("http://localhost:1234/v1"), { target: { value: url } });
  fireEvent.click(within(c).getByRole("button", { name: /Save endpoint/ }));
}

beforeEach(() => {
  hooks.responses = {};
  hooks.posts = [];
  hooks.verify = [];
  hooks.errors = {};
});
afterEach(() => cleanup());

/* ===================================================== G47: the card's words */

describe("G47: the model-server card matches what answers", () => {
  it("the daemon's source is the acceptance wording and the page's key reads it", () => {
    expect(ENDPOINT_SOURCE).toBe("your endpoint — no key needed");
    expect(isOwnEndpoint({ connected: true, source: ENDPOINT_SOURCE })).toBe(true);
    // A stored key, an inherited login and a row that is not connected never match.
    expect(isOwnEndpoint({ connected: true, source: "vault" })).toBe(false);
    expect(isOwnEndpoint({ connected: true, source: "inherited from claude-cli" })).toBe(false);
    expect(isOwnEndpoint({ connected: false, source: ENDPOINT_SOURCE })).toBe(false);
  });

  it("reads 'Connected · your endpoint · no key needed' while its own server answers, and keeps its list and add form", async () => {
    seed(customRow({ connected: true, status: "connected", source: ENDPOINT_SOURCE }), [SAVED_NODE]);
    const c = await card();
    const pill = within(c).getByTestId("conn-status-pill");
    expect(pill.textContent?.replace(/\s+/g, " ").trim()).toBe(OWN_ENDPOINT_LABEL);
    expect(OWN_ENDPOINT_LABEL).toBe("Connected · your endpoint · no key needed");
    // The daemon's raw words ride the title, like the inherited source.
    expect(pill.getAttribute("title")).toBe(ENDPOINT_SOURCE);
    // The daemon's name for the slot stays reachable as the title's tooltip.
    expect(within(c).getByText(MODEL_SERVERS_TITLE).getAttribute("title")).toBe("Custom endpoint");
    // No "API key" method chip beside "no key needed" (the inherited rule).
    expect(within(c).queryByTestId("conn-card-auth")).toBeNull();
    // The connection IS the endpoint list: it stays, with its add door.
    expect(await within(c).findByTestId("endpoint-row")).toBeInTheDocument();
    expect(within(c).getByRole("button", { name: /Add another endpoint/ })).toBeInTheDocument();
    // No key to test or disconnect, and no second "default" control.
    expect(within(c).queryByRole("button", { name: /^Test$/ })).toBeNull();
    expect(within(c).queryByText("Disconnect")).toBeNull();
    expect(within(c).queryByText(/Make default/)).toBeNull();
  });

  it("the Add connection menu still offers the card by its new name", async () => {
    seed(customRow({ connected: true, status: "connected", source: ENDPOINT_SOURCE }));
    await card();
    fireEvent.click(screen.getByRole("button", { name: /Add connection/ }));
    const menu = screen.getByRole("menu");
    expect(within(menu).getByRole("menuitem", { name: MODEL_SERVERS_TITLE })).toBeInTheDocument();
  });

  it("nothing answering reads 'Not connected'; a stored key keeps 'Connected' and its key actions", async () => {
    seed(customRow());
    let c = await card();
    expect(within(c).getByTestId("conn-status-pill").textContent).toMatch(/^Not connected$/);
    expect(within(c).getByTestId("conn-card-auth").textContent).toMatch(/API key/);
    cleanup();

    seed(customRow({ connected: true, status: "connected", source: "vault" }));
    c = await card();
    expect(within(c).getByTestId("conn-status-pill").textContent).toMatch(/^Connected$/);
    expect(within(c).getByTestId("conn-card-auth").textContent).toMatch(/API key/);
    expect(within(c).getByRole("button", { name: /Test/ })).toBeInTheDocument();
    expect(within(c).getByText("Disconnect")).toBeInTheDocument();
  });
});

/* ================================================= G47: the save note */

describe("G47: a save whose check could not reach the server says so", () => {
  const SAID =
    "Saved, but Jarvis could not reach http://bxo:1234/v1: connect failed: ConnectError: " +
    "[Errno 11001] getaddrinfo failed. Start the server or check the address, then press Verify tools.";

  it("a mistyped address: a warning that names the address and the check's own error, never green", async () => {
    seed(customRow());
    hooks.verify = [
      { tool_use: null, error: "connect failed: ConnectError: [Errno 11001] getaddrinfo failed.", hint: "" },
    ];
    const c = await card();
    await saveEndpoint(c, "http://bxo:1234/v1");
    await waitFor(() => expect(within(c).getByText(SAID)).toBeInTheDocument());
    const note = within(c).getByText(SAID).closest("[data-tone]") as HTMLElement;
    expect(note.getAttribute("data-tone")).toBe("warn");
    expect(c.textContent).not.toMatch(/Endpoint saved/);
    // The endpoint itself WAS saved (its own provider), exactly as before.
    expect(hooks.posts.find((p) => p.path === "/fleet/nodes")?.body).toMatchObject({
      base_url: "http://bxo:1234/v1",
      routable: true,
    });
  });

  it("a check that itself fails says so too, with its message", async () => {
    seed(customRow());
    hooks.verify = [new hooks.FakeApiError("the check timed out", 502)];
    const c = await card();
    await saveEndpoint(c, "http://box:1234/v1");
    await waitFor(() =>
      expect(
        within(c).getByText(
          "Saved, but Jarvis could not reach http://box:1234/v1: the check timed out. Start the server or check the address, then press Verify tools.",
        ),
      ).toBeInTheDocument(),
    );
    expect(c.textContent).not.toMatch(/Endpoint saved/);
  });

  it("a server that answers keeps the green note, with no warning", async () => {
    seed(customRow());
    hooks.verify = [{ tool_use: true, vision: false, error: "" }];
    const c = await card();
    await saveEndpoint(c, "http://box:1234/v1");
    await waitFor(() =>
      expect(within(c).getByText(/Endpoint saved\. Pick "box" in any model picker\. It runs tools/)).toBeInTheDocument(),
    );
    expect(c.querySelector('[data-tone="warn"]')).toBeNull();
  });

  it("pressing Verify tools on a row replaces the save warning with its own answer", async () => {
    seed(customRow(), [SAVED_NODE]);
    hooks.verify = [
      { tool_use: null, error: "connect failed", hint: "" },
      { tool_use: true, vision: null, error: "" },
    ];
    const c = await card();
    await saveEndpoint(c, "http://bxo:1234/v1");
    await waitFor(() => expect(c.querySelector('[data-tone="warn"]')).not.toBeNull());
    fireEvent.click(await within(c).findByTestId("endpoint-row-verify"));
    await waitFor(() => expect(c.querySelector('[data-tone="warn"]')).toBeNull());
  });

  it("the note's words: the error's own full stop is not doubled; no error, no colon", () => {
    expect(unreachableNote(" http://x:1/v1 ", "refused. ")).toBe(
      "Saved, but Jarvis could not reach http://x:1/v1: refused. Start the server or check the address, then press Verify tools.",
    );
    expect(unreachableNote("http://x:1/v1", "")).toBe(
      "Saved, but Jarvis could not reach http://x:1/v1. Start the server or check the address, then press Verify tools.",
    );
  });
});

/* ======================================================= G48: Accounts & keys */

describe("G48: Accounts & keys hides the dead-end REST hookups and says Add a tool", () => {
  it("the hookups sit in a CLOSED 'More ways to connect (advanced)' fold, after every real door", async () => {
    seed(customRow());
    hooks.responses["/integrations"] = {
      integrations: [
        { id: "crm", kind: "rest", display_name: "Harbor CRM", enabled: true, configured: true, required_secrets: [] },
      ],
    };
    await card();
    const fold = screen.getByTestId("more-ways-to-connect") as HTMLDetailsElement;
    expect(fold.tagName).toBe("DETAILS");
    expect(fold.open).toBe(false);
    expect(fold.querySelector("summary")?.textContent?.trim()).toBe("More ways to connect (advanced)");
    // Everything the hookups render lives inside the closed fold.
    expect(fold.contains(screen.getByText("Connection checks"))).toBe(true);
    expect(fold.contains(screen.getByText("Harbor CRM"))).toBe(true);
    // The real doors come first.
    const where = screen.getByText("Where else things connect");
    expect(where.compareDocumentPosition(fold) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The promise is gone everywhere.
    expect(document.body.textContent).not.toMatch(/Direct REST hookups/);
    expect(document.body.textContent).not.toMatch(/Connect any service/);
  });

  it("the extensions tile and the Add connection menu read 'Add a tool' and open Tools", async () => {
    seed(customRow());
    await card();
    const where = screen.getByText("Where else things connect").closest("section") as HTMLElement;
    const tile = within(where).getByRole("link", { name: /Add a tool/ });
    expect(tile.getAttribute("href")).toBe("/tools");
    fireEvent.click(screen.getByRole("button", { name: /Add connection/ }));
    const item = within(screen.getByRole("menu")).getByRole("menuitem", { name: /Add a tool/ });
    expect(item.getAttribute("href")).toBe("/tools");
    expect(document.body.textContent).not.toMatch(/Extensions \(MCP\)/);
  });

  it("offline: no fold that opens onto nothing (RestHookups draws nothing then)", async () => {
    // Online, the same page draws the fold (anti-vacuity: the mock can draw it).
    seed(customRow());
    render(<ConnectionsPage />);
    expect(await screen.findByTestId("more-ways-to-connect")).toBeInTheDocument();
    cleanup();

    // Iron Jarvis not answering: every read fails with status 0.
    seed(customRow());
    hooks.errors["/connections"] = { status: 0, message: "offline" };
    hooks.errors["/integrations"] = { status: 0, message: "offline" };
    render(<ConnectionsPage />);
    // The page is in its offline state (its one hint) and still draws its doors...
    expect((await screen.findAllByText(OFFLINE_TITLE)).length).toBeGreaterThan(0);
    expect(screen.getByText("Where else things connect")).toBeInTheDocument();
    // ...but no "More ways to connect" line whose inside would be empty.
    expect(screen.queryByTestId("more-ways-to-connect")).toBeNull();
    expect(screen.queryByText("More ways to connect (advanced)")).toBeNull();
  });

  it("the hookups card says it is a check only and points at the real door", () => {
    hooks.responses["/integrations"] = { integrations: [] };
    render(<RestHookups />);
    const note = screen.getByTestId("rest-hookups-note");
    expect(note.textContent?.replace(/\s+/g, " ")).toContain(
      "Connection checks only. Jarvis can't call these yet.",
    );
    expect(within(note).getByRole("link", { name: "Add a tool" }).getAttribute("href")).toBe("/tools");
    fireEvent.click(screen.getByRole("button", { name: /Add a check/ }));
    expect(screen.getByText("New connection check")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add check/ })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/hookup/i);
  });
});
