/**
 * v1.355.0 integrate — the wave-4 follow-ups, on the REAL components:
 *
 *  (1) a placeholder the imported config left (`${input:token}`, `Bearer
 *      ${TOKEN}`) is ASKED for on the row: the row's Details open on it, a
 *      secret-looking name gets a hidden input, a folder a plain one, and the
 *      typed value rides the apply's `overrides`;
 *  (2) the import door is honest: a Drive sign-in says so (it is not "already
 *      connected"), and a pack added under another spelling says that name;
 *  (3) the memory graph's link removal is the two-press ConfirmButton (no
 *      native dialog); the Creative studio's End session is too (source pin);
 *  (4) the first-run door says the Connections card's words for a model
 *      server ("Model servers on your network");
 *  (5) a check that reached the server and got an error never says "could
 *      not reach" (lib/modelServers.answeredWithErrorNote).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    sources: [] as Record<string, unknown>[],
    graph: { nodes: [], edges: [], embedder: "" } as Record<string, unknown>,
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
    if (path === "/mcp/servers") return { servers: [] };
    if (path === "/mcp/catalog") return { catalog: [] };
    if (path === "/tools/custom") return { tools: [] };
    if (path === "/mcp/import/sources") return { sources: api.sources };
    if (path.startsWith("/memory/graph")) return api.graph;
    return {};
  },
  post: async (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    if (path === "/mcp/import/apply") return { added: [], skipped: [] };
    return {};
  },
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: () => {},
  }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
// The 3D canvas needs WebGL: a stand-in that offers its one link to click.
vi.mock("next/dynamic", () => ({
  default: () =>
    function FakeCanvas(props: { onLinkClick: (l: unknown) => void }) {
      return (
        <button
          type="button"
          data-testid="fake-graph-link"
          onClick={() => props.onLinkClick({ source: "lesson:1", target: "mem:2", kind: "manual" })}
        >
          link
        </button>
      );
    },
}));

const ToolsPage = (await import("@/components/settings/pages/ToolsPage")).default;
const MemoryGraph = (await import("@/components/memory/MemoryGraph")).default;
const words = await import("@/lib/addPackWords");
const { answeredWithErrorNote, unreachableNote, MODEL_SERVERS_TITLE } = await import("@/lib/modelServers");

const DASH = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(DASH, rel), "utf-8").replace(/\r\n/g, "\n");

function row(name: string, over: Record<string, unknown> = {}) {
  return {
    name,
    transport: "stdio",
    command: "npx",
    args: ["-y", `@example/${name}`],
    url: "",
    header_names: [],
    env_keys: [],
    secret_keys: [],
    exists: false,
    runtime_ready: true,
    launcher_note: "",
    source: "vscode",
    origin: "C:\\AppData\\Code\\User\\mcp.json",
    disabled: false,
    exists_as: "",
    blurb: "",
    ...over,
  };
}

function vscode(servers: Record<string, unknown>[]) {
  return { source: "vscode", label: "VS Code", origin: "C:\\AppData\\Code\\User\\mcp.json", readable: true, note: "", servers };
}

beforeEach(() => {
  api.posts = [];
  api.sources = [];
  api.graph = { nodes: [], edges: [], embedder: "" };
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/* ------------------------------------------------------------- (1) asks */
describe("(1) a placeholder the config left is asked for", () => {
  const GH = row("gh", {
    env_keys: ["GITHUB_TOKEN"],
    secret_keys: ["GITHUB_TOKEN"],
    asks: ["GITHUB_TOKEN", "root_dir"],
  });

  it("the row opens on the asks: a hidden input for a secret, a plain one for a folder, and they ride the apply", async () => {
    api.sources = [vscode([GH])];
    render(<ToolsPage />);
    const gh = await screen.findByTestId("import-row-gh");
    expect((within(gh).getByTestId("import-details-gh") as HTMLDetailsElement).open).toBe(true);
    const token = within(gh).getByTestId("override-gh-GITHUB_TOKEN") as HTMLInputElement;
    const root = within(gh).getByTestId("override-gh-root_dir") as HTMLInputElement;
    expect(token.type).toBe("password");
    expect(root.type).toBe("text");
    expect(root.placeholder).toBe("Type its value (the config left a placeholder)");
    fireEvent.change(token, { target: { value: "ghp_typed" } });
    fireEvent.change(root, { target: { value: "C:/work" } });
    fireEvent.click(within(screen.getByTestId("import-source-vscode")).getByTestId("import-source-apply-vscode"));
    await waitFor(() => expect(api.posts.find((p) => p.path === "/mcp/import/apply")).toBeTruthy());
    const body = api.posts.find((p) => p.path === "/mcp/import/apply")!.body as {
      overrides: Record<string, Record<string, string>>;
    };
    expect(body.overrides.gh).toEqual({ GITHUB_TOKEN: "ghp_typed", root_dir: "C:/work" });
  });

  it("the words: each name once; a secret key the config may hold stays optional", () => {
    expect(words.askedNames(GH as never)).toEqual(["GITHUB_TOKEN", "root_dir"]);
    expect(words.askHint(row("x", { secret_keys: ["API_KEY"] }) as never, "API_KEY")).toBe(
      "Type a value if the config has none",
    );
    for (const n of ["TOKEN", "github_token", "API_KEY", "Authorization", "DATABASE_URL", "PASSWORD"]) {
      expect(words.looksSecretName(n)).toBe(true);
    }
    for (const n of ["root_dir", "workspaceFolder", "MODE", "PATH"]) {
      expect(words.looksSecretName(n)).toBe(false);
    }
  });
});

/* ----------------------------------------------------------- (2) honest */
describe("(2) the import door is honest about sign-ins and spellings", () => {
  it("a Drive sign-in is said, the row is not 'already connected', and it starts ticked", async () => {
    api.sources = [vscode([row("google-drive", { signed_in_app: "Google Drive" })])];
    render(<ToolsPage />);
    const r = await screen.findByTestId("import-row-google-drive");
    expect(within(r).getByTestId("import-signed-in-google-drive").textContent).toBe(
      "You're signed in to Google Drive here for memory search; this adds the server's own tools.",
    );
    expect(within(r).queryByTestId("import-exists-google-drive")).toBeNull();
    expect((within(r).getByTestId("import-pick-google-drive") as HTMLInputElement).checked).toBe(true);
    expect(r.textContent).not.toMatch(/already connected/);
  });

  it("a pack added under another spelling says its name", () => {
    expect(words.existsWords(row("brave-search", { exists: true, exists_as: "pack", exists_name: "brave_search" }) as never)).toBe(
      "already added as brave_search",
    );
    expect(words.existsWords(row("brave_search", { exists: true, exists_as: "pack" }) as never)).toBe("already added");
    expect(words.signedInWords(row("x", { exists: true, signed_in_app: "Google Drive" }) as never)).toBe("");
  });
});

/* ---------------------------------------------------------- (3) confirm */
describe("(3) a memory-graph link is removed with two presses, never a native dialog", () => {
  it("clicking a link picks it; Remove link arms, the second press removes", async () => {
    api.graph = {
      nodes: [
        { id: "lesson:1", label: "Keep answers short", group: "lesson", snippet: "" },
        { id: "mem:2", label: "Prefers bullet lists", group: "memory", snippet: "" },
      ],
      edges: [{ a: "lesson:1", b: "mem:2", kind: "manual", weight: 1 }],
      embedder: "",
    };
    const native = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<MemoryGraph />);
    fireEvent.click(await screen.findByTestId("fake-graph-link"));
    const picked = await screen.findByTestId("memory-link-picked");
    expect(picked.textContent).toContain("Keep answers short");
    expect(picked.textContent).toContain("Prefers bullet lists");
    const press = within(picked).getByRole("button", { name: "Remove link" });
    fireEvent.click(press);
    expect(api.posts.find((p) => p.path === "/memory/graph/unlink")).toBeUndefined();
    fireEvent.click(within(picked).getByRole("button", { name: "Really remove?" }));
    await waitFor(() =>
      expect(api.posts.find((p) => p.path === "/memory/graph/unlink")?.body).toEqual({ a: "lesson:1", b: "mem:2" }),
    );
    expect(native).not.toHaveBeenCalled();
  });

  it("Keep it un-picks the link and removes nothing", async () => {
    api.graph = {
      nodes: [
        { id: "lesson:1", label: "A", group: "lesson", snippet: "" },
        { id: "mem:2", label: "B", group: "memory", snippet: "" },
      ],
      edges: [{ a: "lesson:1", b: "mem:2", kind: "similar", weight: 0.6 }],
      embedder: "",
    };
    render(<MemoryGraph />);
    fireEvent.click(await screen.findByTestId("fake-graph-link"));
    fireEvent.click(within(await screen.findByTestId("memory-link-picked")).getByRole("button", { name: "Keep it" }));
    expect(screen.queryByTestId("memory-link-picked")).toBeNull();
    expect(api.posts.find((p) => p.path === "/memory/graph/unlink")).toBeUndefined();
  });

  it("the Creative studio's End session is the two-press ConfirmButton (no native dialog)", () => {
    const src = read("app/creative/page.tsx");
    expect(src).not.toMatch(/confirm\(\s*"End this session\?/);
    expect(src).toMatch(/<ConfirmButton\s+onConfirm=\{endSession\}\s+label="End session"/);
  });
});

/* ------------------------------------------------------------ (4) label */
describe("(4) the first-run door names a model server as the Connections card does", () => {
  it("ConnectDoors' key door uses MODEL_SERVERS_TITLE, never 'Custom endpoint'", () => {
    const src = read("components/onboarding/ConnectDoors.tsx");
    expect(MODEL_SERVERS_TITLE).toBe("Model servers on your network");
    expect(src).toMatch(/\{ id: "custom", label: MODEL_SERVERS_TITLE,/);
    expect(src).not.toMatch(/label: "Custom endpoint"/);
  });
});

/* ------------------------------------------------------------- (5) verify */
describe("(5) a check that got an answer never says it could not reach the server", () => {
  it("answeredWithErrorNote says it answered; unreachableNote is for a server never reached", () => {
    const answered = answeredWithErrorNote("http://box:1234/v1", "NotFoundError: model 'm' not found.");
    expect(answered).toBe(
      "Saved. http://box:1234/v1 answered, but with an error: NotFoundError: model 'm' not found. Check the model name and the key, then press Verify tools.",
    );
    expect(answered).not.toMatch(/could not reach/);
    expect(unreachableNote("http://box:1234/v1", "connect failed")).toMatch(/could not reach/);
    expect(answeredWithErrorNote("", "")).toBe(
      "Saved. The server answered, but with an error. Check the model name and the key, then press Verify tools.",
    );
  });
});
