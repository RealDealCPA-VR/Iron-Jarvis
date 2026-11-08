/**
 * v1.420.0 (C6.6 + C6.12) — the key door and Connections speak plainly; a
 * new user hears about Gemini's free tier, the fleet form takes a key, and
 * first run offers to import tools other apps already use.
 *
 *  1. A connection Test's answer shows the daemon's `title. fix`, the probe's
 *     own text folded under Details — never "Not connected — could not reach
 *     anthropic: ConnectError: [Errno 11001] …" as the line. An older daemon's
 *     raw `detail` keeps the old line.
 *  2. The retired engine-room words are gone from Connections, the email
 *     dialog and Notifications ("Subscription & local providers", "Rescan
 *     local CLIs", "Sessions use this provider by default", an always-shown
 *     "Server type", "No OAuth client configured", "Open Channels", "SMTP
 *     details").
 *  3. The Gemini door says "free tier" (lib/freeTiers: a documented constant,
 *     with the vendor page cited) and what it costs in privacy; the button's
 *     name stays "Google Gemini".
 *  4. Fleet's "Add a node" form takes a key and saves it the Connections
 *     endpoint form's way (vaulted, then wired to the node).
 *  5. The first-run checklist's connect-apps step offers "Import your tools"
 *     when GET /mcp/import/sources finds a tool not added yet, and opens the
 *     Tools page's own SourcesDoor in place.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    patches: [] as { path: string; body: unknown }[],
    test: null as unknown,
    sources: { sources: [] } as unknown,
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
  get: vi.fn((path: string) => {
    if (path === "/mcp/import/sources") return Promise.resolve(hooks.sources);
    return Promise.resolve(hooks.responses[path] ?? {});
  }),
  post: vi.fn((path: string, body?: unknown) => {
    hooks.posts.push({ path, body });
    if (path.endsWith("/test")) return Promise.resolve(hooks.test);
    if (path === "/fleet/nodes") return Promise.resolve({ node: { id: "spark", label: "Spark" } });
    if (path === "/fleet/probe") return Promise.resolve({ kind: "unknown", snapshot: null, error: null });
    return Promise.resolve({ ok: true });
  }),
  put: vi.fn(() => Promise.resolve({})),
  patch: vi.fn((path: string, body?: unknown) => {
    hooks.patches.push({ path, body });
    return Promise.resolve({});
  }),
  del: vi.fn(() => Promise.resolve({})),
}));
vi.mock("@/lib/useApi", () => {
  const read = (path: string | null) => ({
    data: path ? (hooks.responses[path] ?? null) : null,
    error: null,
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
    epoch: 0,
    provided: true,
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

import { ConnectDoors } from "@/components/onboarding/ConnectDoors";
import { ImportToolsOffer, IMPORT_TOOLS_LABEL } from "@/components/onboarding/ImportToolsOffer";
import { testWords } from "@/lib/connectionTestWords";
import { FREE_TIERS, freeTierWords } from "@/lib/freeTiers";
import FleetPage from "@/app/fleet/page";

const DASH = join(__dirname, "..");
const src = (p: string) => readFileSync(join(DASH, p), "utf-8").replace(/\r\n/g, "\n");

const RAW = "could not reach anthropic: ConnectError: [Errno 11001] getaddrinfo failed";
const WORDED = {
  ok: false,
  detail: RAW,
  kind: "unreachable",
  title: "Couldn't reach Anthropic to check the key",
  fix: "Check your internet connection, then press Test again.",
  technical: RAW,
};

beforeEach(() => {
  hooks.responses = { "/fleet": { nodes: [] } };
  hooks.posts.length = 0;
  hooks.patches.length = 0;
  hooks.test = WORDED;
  hooks.sources = { sources: [] };
});
afterEach(cleanup);

function openKeyDoor() {
  fireEvent.click(screen.getByRole("button", { name: /I have an API key/ }));
}

/* ------------------------------------------------------------ 1. Test words */

describe("a Test answer reads in plain words (C6.6)", () => {
  it("testWords joins title and fix, keeps the technical text apart, and leaves an older answer alone", () => {
    expect(testWords(WORDED as never)).toEqual({
      text: "Couldn't reach Anthropic to check the key. Check your internet connection, then press Test again.",
      technical: RAW,
      worded: true,
    });
    expect(testWords({ ok: true, detail: "anthropic reachable — credential accepted" })).toEqual({
      text: "anthropic reachable — credential accepted",
      technical: "",
      worded: false,
    });
  });

  it("the key door shows the words, the probe text only under Details", async () => {
    render(<ConnectDoors />);
    openKeyDoor();
    fireEvent.change(screen.getByPlaceholderText("sk-ant-…"), { target: { value: "sk-ant-x" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    const line = await screen.findByTestId("key-door-result");
    expect(line.textContent).toBe(
      "Couldn't reach Anthropic to check the key. Check your internet connection, then press Test again.",
    );
    expect(line.textContent).not.toMatch(/Not connected —|Errno|ConnectError/);
    const fold = screen.getByTestId("key-door-technical");
    expect(fold.tagName).toBe("DETAILS");
    expect(within(fold).getByText("Details")).toBeTruthy();
    expect(fold.textContent).toContain("Errno 11001");
  });

  it("an older daemon's raw detail keeps the old prefixed line", async () => {
    hooks.test = { ok: true, detail: "anthropic reachable — credential accepted" };
    render(<ConnectDoors />);
    openKeyDoor();
    fireEvent.change(screen.getByPlaceholderText("sk-ant-…"), { target: { value: "sk-ant-x" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect((await screen.findByTestId("key-door-result")).textContent).toMatch(
      /^Connected — anthropic reachable/,
    );
    expect(screen.queryByTestId("key-door-technical")).toBeNull();
  });
});

/* ------------------------------------------------------- 2. retired words */

describe("the retired words are gone (C6.6)", () => {
  it("Connections, the picker, the email dialog and Notifications speak plainly", () => {
    const conn = src("components/settings/pages/ConnectionsPage.tsx");
    for (const w of [
      'title="Subscription & local providers"',
      "Rescan local CLIs",
      "Sessions use this provider by default",
      "No OAuth client configured",
      "for new sessions",
    ])
      expect(conn, w).not.toContain(w);
    expect(conn).toContain("Your plans and your own model servers");
    expect(conn).toContain("Look again");
    // the server-type choice is shown only when Fetch could not tell
    expect(conn).toMatch(/\{protocolTold === false && \(\s*<EndpointProtocolChoice/);
    expect(src("components/connections/EndpointModelPicker.tsx")).not.toContain(">Server type<");
    const mail = src("components/chat/EmailComposeDialog.tsx");
    expect(mail).not.toMatch(/Open Channels|SMTP server|IMAP\s+server/);
    const notes = src("components/settings/pages/NotificationsPage.tsx");
    expect(notes).not.toContain('needs: "SMTP details');
    expect(notes).not.toContain("fill the SMTP host");
    expect(notes).toContain('label: "Email (sending)"');
    expect(notes).toContain('host: { label: "Mail server for sending"');
  });
});

/* ---------------------------------------------------------- 3. free tier */

describe("the Gemini door says free tier (C6.12)", () => {
  it("is a documented fact, never a guess", () => {
    expect(Object.keys(FREE_TIERS)).toEqual(["google"]);
    expect(FREE_TIERS.google.source).toMatch(/^https:\/\/ai\.google\.dev\//);
    expect(FREE_TIERS.google.checked).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(freeTierWords("anthropic")).toBe("");
  });

  it("the button keeps its name, shows the badge, and the line says it with its privacy note", () => {
    render(<ConnectDoors />);
    openKeyDoor();
    // the button's NAME is unchanged (the badge is hidden from it)
    const gemini = screen.getByRole("button", { name: "Google Gemini" });
    expect(within(gemini).getByTestId("key-door-free-google").textContent).toContain("free tier");
    // no badge on a provider with none on record
    expect(screen.queryByTestId("key-door-free-anthropic")).toBeNull();
    fireEvent.click(gemini);
    const line = screen.getByTestId("key-door-free-tier");
    expect(line.textContent).toMatch(/Google Gemini has a free tier\./);
    expect(line.textContent).toContain(FREE_TIERS.google.note);
  });
});

/* ---------------------------------------------------- 4. fleet form key */

// v1.455.0 (readiness Wave H item 31, moved on purpose): a model server is
// added in ONE place now — Settings › Connections, whose form has always taken
// a key (and now re-detects once it is on the server, as this page's form did).
// The Model servers page links there instead of holding a second form.
describe("Model servers links the one add form (Wave H item 31)", () => {
  it("no second form, a link to Connections' model-server form", async () => {
    render(<FleetPage />);
    const link = await screen.findByTestId("fleet-add-link");
    expect(link.querySelector("a")?.getAttribute("href")).toBe("/connections?focus=endpoints");
    expect(screen.queryByTestId("fleet-add-key")).toBeNull();
    expect(screen.queryByLabelText("Base URL")).toBeNull();
  });
});

/* -------------------------------------------------- 5. first-run import */

const DESKTOP = {
  source: "claude-desktop",
  label: "Claude Desktop",
  origin: "claude_desktop_config.json",
  readable: true,
  servers: [
    { name: "quickbooks", transport: "stdio", command: "npx", exists: false },
    { name: "github", transport: "http", exists: true },
  ],
};

describe("first run offers to import tools (C6.12)", () => {
  it("offers Import your tools when a tool not added yet is found, and opens the SAME door", async () => {
    hooks.sources = { sources: [DESKTOP] };
    render(<ImportToolsOffer />);
    expect((await screen.findByTestId("first-run-import-line")).textContent).toBe(
      "Found 1 tool in Claude Desktop.",
    );
    fireEvent.click(screen.getByRole("button", { name: IMPORT_TOOLS_LABEL }));
    expect(await screen.findByTestId("add-pack-sources-door", {}, { timeout: 10_000 })).toBeTruthy();
  });

  it("silent when everything found is already added, or nothing is found", async () => {
    hooks.sources = { sources: [{ ...DESKTOP, servers: [{ name: "github", exists: true }] }] };
    const { container } = render(<ImportToolsOffer />);
    // the read lands, and still nothing is offered
    await waitFor(() => expect(container.innerHTML).toBe(""));
    expect(screen.queryByTestId("first-run-import")).toBeNull();
  });

  it("the checklist mounts it on the open connect-apps step only", () => {
    const welcome = src("components/OnboardingWelcome.tsx");
    expect(welcome).toContain('import { ImportToolsOffer } from "@/components/onboarding/ImportToolsOffer";');
    expect(welcome).toContain('{step.key === "connect_apps" && !step.done && <ImportToolsOffer />}');
  });
});
