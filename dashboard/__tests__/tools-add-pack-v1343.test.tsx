/**
 * v1.343.0 ("add a pack in one paste"), lane K1/K3: the Tools page's
 * "Add a pack" area. Three doors, one preview list, values never rendered.
 *
 *  - the area sits on the Tools page with the three doors;
 *  - Paste a config: POST /mcp/import/preview {text}; rows render the
 *    launcher chip, the "Uses X" / "Needs X" chip from runtime_ready, the
 *    "already added" badge, and a password box per secret key; "Add
 *    selected" posts names + the typed overrides to POST /mcp/import/apply
 *    and the result rows show tools_loaded or the add note;
 *  - Import from your other apps: GET /mcp/import/sources, one group per
 *    readable source (label + origin), an unreadable one is one quiet line,
 *    apply posts {source, names};
 *  - HTTP server: POST /mcp/servers {transport, url, headers, auth};
 *  - the npm door offers a launcher (npx / uvx / command) and sanitises the
 *    name the way the daemon does (dots become "-");
 *  - the pure helpers (safeName, guessLauncher, buildLaunch, httpServerBody);
 *  - copy guards: no dash asides, whole-pixel sizes, no literal hues, calm
 *    Badges in the new files.
 *
 * Harness mirrors tools-pack-rows-v1342 (the page's data seams are mocked).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { asides, readSrc } from "./helpers/dashGuard";
import { uncalm } from "./helpers/calmVariant";

type Call = { path: string; body: unknown };

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
    posts: [] as { path: string; body: unknown }[],
    servers: [] as Record<string, unknown>[],
    catalog: [] as Record<string, unknown>[],
    preview: { servers: [] as Record<string, unknown>[], unrecognised: false } as Record<string, unknown>,
    previewError: null as Error | null,
    apply: { added: [], skipped: [] } as Record<string, unknown>,
    sources: [] as Record<string, unknown>[],
    addResult: { name: "", added: true, tools_loaded: 0, auto_approve: false, note: null } as Record<
      string,
      unknown
    >,
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
    if (path === "/mcp/servers") return { servers: api.servers };
    if (path === "/mcp/catalog") return { catalog: api.catalog };
    if (path === "/tools/custom") return { tools: [] };
    if (path === "/mcp/import/sources") return { sources: api.sources };
    return {};
  },
  post: async (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    if (path === "/mcp/import/preview") {
      if (api.previewError) throw api.previewError;
      return api.preview;
    }
    if (path === "/mcp/import/apply") return api.apply;
    if (path === "/mcp/servers") return { ...api.addResult, name: (body as { name: string }).name };
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

const ToolsPage = (await import("@/components/settings/pages/ToolsPage")).default;
const helpers = await import("@/lib/mcpImport");

const SECRET = "sk-SHOULD-NEVER-SHOW-1";

function previewRow(name: string, over: Record<string, unknown> = {}) {
  return {
    name,
    transport: "stdio",
    command: "npx",
    args: ["-y", `@example/${name}`],
    url: null,
    header_names: [],
    env_keys: [],
    secret_keys: [],
    exists: false,
    runtime_ready: true,
    launcher_note: null,
    source: "paste",
    origin: "paste",
    ...over,
  };
}

const BRAVE = previewRow("brave", { env_keys: ["BRAVE_API_KEY"], secret_keys: ["BRAVE_API_KEY"] });
const FILESYSTEM = previewRow("filesystem", { exists: true });
const PY = previewRow("py", {
  command: "uvx",
  args: ["mcp-server-time"],
  runtime_ready: false,
  launcher_note: "uvx is not installed on this PC",
});
const REMOTE = previewRow("remote", {
  transport: "http",
  command: null,
  args: [],
  url: "https://host/mcp",
  header_names: ["X-Api-Key"],
  runtime_ready: true,
});

const posted = (path: string): Call[] => api.posts.filter((p) => p.path === path);

beforeEach(() => {
  api.gets = [];
  api.posts = [];
  api.servers = [];
  api.catalog = [];
  api.preview = { servers: [], unrecognised: false };
  api.previewError = null;
  api.apply = { added: [], skipped: [] };
  api.sources = [];
  api.addResult = { name: "", added: true, tools_loaded: 0, auto_approve: false, note: null };
});
afterEach(() => cleanup());

async function openPage() {
  render(<ToolsPage />);
  return await screen.findByTestId("add-pack");
}

/** v1.354.0 (G49, updated on purpose): the area opens on "Bring your tools
 *  from Claude, Codex, Cursor…"; the paste box is the advanced door, one
 *  press away. */
async function openPaste() {
  const area = await openPage();
  fireEvent.click(within(area).getByTestId("add-pack-door-paste"));
  await within(area).findByTestId("add-pack-paste");
  return area;
}

/* ===================================================================== */
/*  the area                                                               */
/* ===================================================================== */

describe("the Add a pack area", () => {
  it("sits on the Tools page with three doors, Bring your tools from Claude, Codex, Cursor… open first (v1.354.0 G49, updated on purpose)", async () => {
    const area = await openPage();
    expect(within(area).getByTestId("add-pack-door-sources").getAttribute("aria-selected")).toBe("true");
    expect(within(area).getByTestId("add-pack-door-sources").textContent).toContain(
      "Bring your tools from Claude, Codex, Cursor…",
    );
    expect(within(area).getByTestId("add-pack-door-paste").textContent).toContain(
      "Paste from a website or README (advanced)",
    );
    expect(within(area).getByTestId("add-pack-door-http")).toBeTruthy();
    expect(await within(area).findByTestId("add-pack-sources-door")).toBeTruthy();
    expect(within(area).queryByTestId("add-pack-paste")).toBeNull();
    // The catalog cards and the npm door stay.
    expect(screen.getByTestId("npm-door-input")).toBeTruthy();
  });
});

/* ===================================================================== */
/*  paste a config                                                         */
/* ===================================================================== */

describe("paste a config", () => {
  it("previews on blur: POST /mcp/import/preview {text}; rows carry the launcher, the runtime chip, the already-added badge and a password box per secret key; no value", async () => {
    api.preview = { servers: [BRAVE, FILESYSTEM, PY, REMOTE], unrecognised: false };
    const area = await openPaste();
    const box = within(area).getByTestId("add-pack-paste") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: '{"mcpServers": {"brave": {}}}' } });
    fireEvent.blur(box);

    const brave = await screen.findByTestId("import-row-brave");
    expect(posted("/mcp/import/preview")).toEqual([
      { path: "/mcp/import/preview", body: { text: '{"mcpServers": {"brave": {}}}' } },
    ]);
    // launcher chip + "Uses Node" from runtime_ready: true
    expect(within(brave).getByTestId("import-launcher-brave").textContent).toContain("npx");
    expect(within(brave).getByTestId("status-ready").textContent).toContain("Uses Node");
    // a password box for the secret key, empty, and the vault sentence
    const override = within(brave).getByTestId("override-brave-BRAVE_API_KEY") as HTMLInputElement;
    expect(override.type).toBe("password");
    expect(override.value).toBe("");
    expect(brave.textContent).toContain("1 in vault");
    // already added: badge, unchecked by default
    const fs = screen.getByTestId("import-row-filesystem");
    expect(within(fs).getByTestId("import-exists-filesystem").textContent).toContain("already added");
    expect((within(fs).getByTestId("import-pick-filesystem") as HTMLInputElement).checked).toBe(false);
    expect(within(fs).queryByTestId("status-ready")).toBeNull();
    // runtime missing: amber "Needs Python (uv)" + the launcher note
    const py = screen.getByTestId("import-row-py");
    expect(within(py).getByTestId("status-blocked").textContent).toContain("Needs Python (uv)");
    expect(within(py).getByTestId("import-note-py").textContent).toContain("uvx is not installed");
    expect((within(py).getByTestId("import-pick-py") as HTMLInputElement).checked).toBe(true);
    // an HTTP row: the transport as the chip, the URL as the launch line, no runtime chip
    const remote = screen.getByTestId("import-row-remote");
    expect(within(remote).getByTestId("import-launcher-remote").textContent).toContain("HTTP");
    expect(remote.textContent).toContain("https://host/mcp");
    expect(remote.textContent).toContain("headers X-Api-Key");
    expect(within(remote).queryByTestId("status-ready")).toBeNull();
    expect(within(remote).queryByTestId("status-blocked")).toBeNull();
  });

  it("says when nothing in the text was recognised", async () => {
    api.preview = { servers: [], unrecognised: true };
    const area = await openPaste();
    const box = within(area).getByTestId("add-pack-paste");
    fireEvent.change(box, { target: { value: "hello there" } });
    fireEvent.click(within(area).getByTestId("add-pack-preview"));
    expect((await screen.findByTestId("add-pack-unrecognised")).textContent).toContain(
      "Nothing in that text looks like a server config",
    );
    expect(screen.queryByTestId("import-preview")).toBeNull();
  });

  it("Add selected posts the chosen names and the typed overrides to /mcp/import/apply and shows tools_loaded or the note; the typed value never renders", async () => {
    api.preview = { servers: [BRAVE, FILESYSTEM, PY], unrecognised: false };
    api.apply = {
      added: [{ name: "brave", tools_loaded: 3, note: null, state: "ok" }],
      skipped: [{ name: "py", why: "uvx is not installed" }],
    };
    const area = await openPaste();
    const box = within(area).getByTestId("add-pack-paste");
    const text = "npx -y @brave/brave-search-mcp-server";
    fireEvent.change(box, { target: { value: text } });
    fireEvent.blur(box);
    await screen.findByTestId("import-row-brave");
    const getsBefore = api.gets.filter((g) => g === "/mcp/servers").length;

    fireEvent.change(screen.getByTestId("override-brave-BRAVE_API_KEY"), { target: { value: SECRET } });
    const apply = screen.getByTestId("add-pack-apply");
    expect(apply.textContent).toContain("Add selected (2)");
    fireEvent.click(apply);

    const result = await screen.findByTestId("import-result-brave");
    expect(result.textContent).toContain("3 tools ready");
    expect(screen.getByTestId("import-skipped-py").textContent).toContain("skipped: uvx is not installed");
    // the apply body: the SAME text re-sent, the chosen names (the already
    // added one left out), the override for the one key, never auto-approve
    expect(posted("/mcp/import/apply")).toEqual([
      {
        path: "/mcp/import/apply",
        body: {
          text,
          names: ["brave", "py"],
          overrides: { brave: { BRAVE_API_KEY: SECRET } },
          auto_approve: false,
          replace: false,
        },
      },
    ]);
    // the value lives in a password box's property, never in the markup
    expect(document.body.innerHTML).not.toContain(SECRET);
    // the connected list re-reads after an add
    await waitFor(() =>
      expect(api.gets.filter((g) => g === "/mcp/servers").length).toBeGreaterThan(getsBefore),
    );
  });

  it("unticking a row keeps its name out of the apply body, and an added pack's note is shown in place of a count", async () => {
    api.preview = { servers: [BRAVE, PY], unrecognised: false };
    api.apply = {
      added: [{ name: "brave", tools_loaded: 0, note: "it needs BRAVE_API_KEY and none was given", state: "failed" }],
      skipped: [],
    };
    const area = await openPaste();
    const box = within(area).getByTestId("add-pack-paste");
    fireEvent.change(box, { target: { value: "npx -y x" } });
    fireEvent.blur(box);
    await screen.findByTestId("import-row-py");
    fireEvent.click(screen.getByTestId("import-pick-py"));
    expect(screen.getByTestId("add-pack-apply").textContent).toContain("Add selected (1)");
    fireEvent.click(screen.getByTestId("add-pack-apply"));
    const result = await screen.findByTestId("import-result-brave");
    expect(result.textContent).toContain("it needs BRAVE_API_KEY and none was given");
    expect(result.textContent).not.toContain("0 tools");
    const body = posted("/mcp/import/apply")[0].body as { names: string[]; overrides: unknown };
    expect(body.names).toEqual(["brave"]);
    expect(body.overrides).toEqual({});
  });

  it("an erased override and an unticked pack's override never ride the apply body, and every box is empty once the add has landed", async () => {
    // Two rows, each with its own secret key. The user types a value into
    // brave's box and erases it again; types a value into slack's box and
    // then unticks slack. Neither value may reach the route: an "" override
    // would replace the config's own value with "" (the pack then starts
    // without its key), and an unticked pack is not being added at all.
    const SLACK = previewRow("slack", { env_keys: ["SLACK_TOKEN"], secret_keys: ["SLACK_TOKEN"] });
    api.preview = { servers: [BRAVE, SLACK], unrecognised: false };
    api.apply = { added: [{ name: "brave", tools_loaded: 1, note: null, state: "ok" }], skipped: [] };
    const area = await openPaste();
    const box = within(area).getByTestId("add-pack-paste");
    fireEvent.change(box, { target: { value: '{"mcpServers": {"brave": {}, "slack": {}}}' } });
    fireEvent.blur(box);
    await screen.findByTestId("import-row-slack");

    const braveBox = screen.getByTestId("override-brave-BRAVE_API_KEY") as HTMLInputElement;
    const slackBox = screen.getByTestId("override-slack-SLACK_TOKEN") as HTMLInputElement;
    fireEvent.change(braveBox, { target: { value: SECRET } });
    fireEvent.change(braveBox, { target: { value: "" } });
    fireEvent.change(slackBox, { target: { value: "xoxb-SHOULD-NEVER-RIDE" } });
    expect(slackBox.value).toBe("xoxb-SHOULD-NEVER-RIDE");
    fireEvent.click(screen.getByTestId("import-pick-slack"));
    expect(screen.getByTestId("add-pack-apply").textContent).toContain("Add selected (1)");
    fireEvent.click(screen.getByTestId("add-pack-apply"));

    await screen.findByTestId("import-outcome");
    expect(screen.getByTestId("import-result-brave").textContent).toContain("1 tool ready");
    expect(posted("/mcp/import/apply")).toHaveLength(1);
    const body = posted("/mcp/import/apply")[0].body as { names: string[]; overrides: unknown };
    expect(body.names).toEqual(["brave"]);
    expect(body.overrides).toEqual({});
    // Once the add has landed, every typed value leaves the page: the
    // unticked row's box is emptied too (it was never sent, and it must not
    // linger in a password field for the next Add).
    await waitFor(() => expect(slackBox.value).toBe(""));
    expect(braveBox.value).toBe("");
    expect(document.body.innerHTML).not.toContain(SECRET);
    expect(document.body.innerHTML).not.toContain("xoxb-SHOULD-NEVER-RIDE");
  });

  it("the preview route refusing is one error line, nothing applied", async () => {
    const area = await openPaste();
    api.previewError = new api.FakeApiError("daemon too old", 404);
    const box = within(area).getByTestId("add-pack-paste");
    fireEvent.change(box, { target: { value: "npx -y x" } });
    fireEvent.click(within(area).getByTestId("add-pack-preview"));
    expect((await within(area).findByRole("alert")).textContent).toContain("daemon too old");
    expect(screen.queryByTestId("add-pack-apply")).toBeNull();
  });
});

/* ===================================================================== */
/*  import from your other apps                                            */
/* ===================================================================== */

describe("import from your other apps", () => {
  it("reads GET /mcp/import/sources as the area opens (the first door since v1.354.0): one group per app with tools, a broken file's quiet line, the empty places folded", async () => {
    api.sources = [
      {
        source: "claude-code",
        label: "Claude Code",
        origin: "C:\\Users\\me\\.claude.json",
        readable: true,
        note: "",
        servers: [BRAVE, FILESYSTEM],
      },
      {
        source: "codex",
        label: "Codex",
        origin: "C:\\Users\\me\\.codex\\config.toml",
        readable: false,
        note: "config.toml could not be read",
        servers: [],
      },
      { source: "cursor", label: "Cursor", origin: "C:\\Users\\me\\.cursor\\mcp.json", readable: false, note: "", servers: [] },
      { source: "vscode", label: "VS Code", origin: "C:\\x\\settings.json", readable: true, note: "", servers: [] },
    ];
    await openPage();

    const claude = await screen.findByTestId("import-source-claude-code");
    expect(api.gets.filter((g) => g === "/mcp/import/sources")).toHaveLength(1);
    expect(claude.textContent).toContain("Claude Code");
    expect(claude.textContent).toContain("C:\\Users\\me\\.claude.json");
    expect(within(claude).getByTestId("import-row-brave")).toBeTruthy();
    expect(within(claude).getByTestId("import-exists-filesystem")).toBeTruthy();
    // unreadable: the note names the file, never its contents; a missing one reads "not found"
    const codex = screen.getByTestId("import-source-codex");
    expect(codex.textContent).toContain("Codex: config.toml could not be read");
    expect(within(codex).queryByTestId("import-preview")).toBeNull();
    expect(screen.getByTestId("import-source-cursor").textContent).toContain("Cursor: not found");
    // readable but empty (v1.354.0 G50, updated on purpose: folded with the
    // missing ones into one "Also looked in" line, in tools words)
    expect(screen.getByTestId("import-source-vscode").textContent).toContain("no tools in this file");
    const folded = screen.getByTestId("import-looked-in");
    expect(within(folded).getByTestId("import-source-cursor")).toBeTruthy();
    expect(within(folded).getByTestId("import-source-vscode")).toBeTruthy();
    expect(within(folded).queryByTestId("import-source-codex")).toBeNull();
  });

  it("Add selected on a source posts {source, names, overrides} and no text", async () => {
    api.sources = [
      {
        source: "claude-desktop",
        label: "Claude Desktop",
        origin: "C:\\AppData\\Claude\\claude_desktop_config.json",
        readable: true,
        note: "",
        servers: [BRAVE, PY],
      },
    ];
    api.apply = { added: [{ name: "brave", tools_loaded: 2, note: null, state: "ok" }, { name: "py", tools_loaded: 1, note: null, state: "ok" }], skipped: [] };
    await openPage();
    const group = await screen.findByTestId("import-source-claude-desktop");
    fireEvent.change(within(group).getByTestId("override-brave-BRAVE_API_KEY"), { target: { value: SECRET } });
    fireEvent.click(within(group).getByTestId("import-source-apply-claude-desktop"));
    expect((await within(group).findByTestId("import-result-py")).textContent).toContain("1 tool ready");
    expect(posted("/mcp/import/apply")).toEqual([
      {
        path: "/mcp/import/apply",
        body: {
          source: "claude-desktop",
          names: ["brave", "py"],
          overrides: { brave: { BRAVE_API_KEY: SECRET } },
          auto_approve: false,
          replace: false,
        },
      },
    ]);
    expect(document.body.innerHTML).not.toContain(SECRET);
  });
});

/* ===================================================================== */
/*  HTTP server                                                            */
/* ===================================================================== */

describe("HTTP server door", () => {
  it("posts {name, transport, url, headers, auth} to /mcp/servers, names the pack from the host, says what loaded, never renders the token", async () => {
    api.addResult = { name: "", added: true, tools_loaded: 2, auto_approve: false, note: null };
    const area = await openPage();
    fireEvent.click(within(area).getByTestId("add-pack-door-http"));
    const door = await screen.findByTestId("add-pack-http-door");
    const add = within(door).getByTestId("add-pack-http-add") as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    fireEvent.change(within(door).getByTestId("add-pack-url"), { target: { value: "https://api.example.com/mcp" } });
    expect(door.textContent).toContain("as api-example-com");
    fireEvent.change(within(door).getByTestId("add-pack-http-transport"), { target: { value: "sse" } });
    fireEvent.click(within(door).getByTestId("add-pack-header-add"));
    fireEvent.change(within(door).getByTestId("add-pack-header-name-1"), { target: { value: "X-Api-Key" } });
    fireEvent.change(within(door).getByTestId("add-pack-header-value-1"), { target: { value: "hdr-SECRET" } });
    fireEvent.change(within(door).getByTestId("add-pack-http-token"), { target: { value: SECRET } });
    await waitFor(() => expect(add.disabled).toBe(false));
    fireEvent.click(add);

    expect((await within(door).findByTestId("add-pack-http-ok")).textContent).toContain(
      '"api-example-com" added with 2 tools ready',
    );
    expect(posted("/mcp/servers")).toEqual([
      {
        path: "/mcp/servers",
        body: {
          name: "api-example-com",
          transport: "sse",
          url: "https://api.example.com/mcp",
          headers: { "X-Api-Key": "hdr-SECRET" },
          auth: { header: "Authorization", format: "Bearer {value}", value: SECRET },
        },
      },
    ]);
    expect(document.body.innerHTML).not.toContain(SECRET);
    expect(document.body.innerHTML).not.toContain("hdr-SECRET");
  });

  it("a typed name is sanitised; no token means no auth key; the add note is shown", async () => {
    api.addResult = { name: "", added: true, tools_loaded: 0, auto_approve: false, note: "it did not answer" };
    const area = await openPage();
    fireEvent.click(within(area).getByTestId("add-pack-door-http"));
    const door = await screen.findByTestId("add-pack-http-door");
    fireEvent.change(within(door).getByTestId("add-pack-url"), { target: { value: "http://127.0.0.1:9000/mcp" } });
    fireEvent.change(within(door).getByTestId("add-pack-http-name"), { target: { value: "My Local.Server" } });
    fireEvent.click(within(door).getByTestId("add-pack-http-add"));
    // v1.351.0 (G08): a pack that loaded NO tools was saved but did not
    // start: the warn note with the daemon's reason, never the success box.
    expect((await within(door).findByTestId("add-pack-http-warn")).textContent).toContain(
      '"my-local-server" added, but it didn\'t start: it did not answer. Open its row below to retry.',
    );
    expect(within(door).queryByTestId("add-pack-http-ok")).toBeNull();
    const body = posted("/mcp/servers")[0].body as Record<string, unknown>;
    expect(body.name).toBe("my-local-server");
    expect(body.transport).toBe("http");
    expect("auth" in body).toBe(false);
    expect(body.headers).toEqual({});
  });
});

/* ===================================================================== */
/*  the npm door: launcher + name                                          */
/* ===================================================================== */

describe("the npm door", () => {
  it("offers a launcher (npx / uvx / command), builds the matching command and sanitises the name like the daemon", async () => {
    await openPage();
    const input = screen.getByTestId("npm-door-input");
    const launcher = screen.getByTestId("npm-door-launcher") as HTMLSelectElement;
    fireEvent.change(input, { target: { value: "@scope/my.pkg" } });
    expect((await screen.findByTestId("npm-door-name")).textContent).toBe("my-pkg");
    expect(launcher.value).toBe("npx");
    expect(screen.getByTestId("npm-door-argv").textContent).toBe(["npx", "-y", "@scope/my.pkg"].join(""));
    fireEvent.change(launcher, { target: { value: "uvx" } });
    await waitFor(() =>
      expect(screen.getByTestId("npm-door-argv").textContent).toBe(["uvx", "@scope/my.pkg"].join("")),
    );
    // a typed name goes through the same rule (dots -> "-", lowercase)
    fireEvent.change(screen.getByTestId("npm-door-custom-name"), { target: { value: "My.Own.Name" } });
    await waitFor(() => expect(screen.getByTestId("npm-door-name").textContent).toBe("my-own-name"));
    fireEvent.click(screen.getByText("Connect this pack"));
    await waitFor(() => expect(posted("/mcp/servers")).toHaveLength(1));
    const body = posted("/mcp/servers")[0].body as Record<string, unknown>;
    expect(body).toEqual({ name: "my-own-name", command: "uvx", args: ["@scope/my.pkg"], env: {}, launcher: "uvx" });
    // the pick is cleared by a Connect, so the guess runs again below
    // guess: a pip: spec reads as uvx without a pick
    fireEvent.change(input, { target: { value: "pip:mcp_server_time" } });
    await waitFor(() => expect((screen.getByTestId("npm-door-launcher") as HTMLSelectElement).value).toBe("uvx"));
    expect(screen.getByTestId("npm-door-argv").textContent).toBe(["uvx", "mcp_server_time"].join(""));
    expect(screen.getByTestId("npm-door-name").textContent).toBe("mcp_server_time");
  });

  it("a GitHub ref launches through npx whatever the select held: the select agrees, is held, and the body carries launcher npx beside command npx", async () => {
    await openPage();
    const input = screen.getByTestId("npm-door-input");
    const launcher = screen.getByTestId("npm-door-launcher") as HTMLSelectElement;
    // pick uvx for a pip package first
    fireEvent.change(input, { target: { value: "pip:mcp_server_time" } });
    fireEvent.change(launcher, { target: { value: "uvx" } });
    await waitFor(() => expect(launcher.value).toBe("uvx"));
    // then paste a GitHub repo into the same box
    fireEvent.change(input, { target: { value: "github.com/o/r" } });
    await waitFor(() =>
      expect(screen.getByTestId("npm-door-argv").textContent).toBe(["npx", "-y", "github:o/r"].join("")),
    );
    expect(launcher.value).toBe("npx");
    expect(launcher.disabled).toBe(true);
    fireEvent.click(screen.getByText("Connect this pack"));
    await waitFor(() => expect(posted("/mcp/servers")).toHaveLength(1));
    const body = posted("/mcp/servers")[0].body as Record<string, unknown>;
    expect(body.command).toBe("npx");
    expect(body.launcher).toBe("npx");
    expect(body.args).toEqual(["-y", "github:o/r"]);
    expect(body.name).toBe("r");
  });

  it("a catalog add still sends the old body shape (no launcher key)", async () => {
    // The npm door is the only caller that names a launcher; the shared
    // addMcpServer adds the key only when one is given.
    const src = readSrc("components/settings/pages/ToolsPage.tsx");
    expect(src).toContain("...(launcher ? { launcher } : {})");
  });
});

/* ===================================================================== */
/*  the pure helpers                                                       */
/* ===================================================================== */

describe("lib/mcpImport helpers", () => {
  it("safeName mirrors the daemon: lowercase, [^a-z0-9_-] -> '-', runs collapse, leading non-letters go, 40 max", () => {
    expect(helpers.safeName("Brave Search")).toBe("brave-search");
    expect(helpers.safeName("my.pkg")).toBe("my-pkg");
    expect(helpers.safeName("123abc")).toBe("abc");
    expect(helpers.safeName("--x--")).toBe("x");
    expect(helpers.safeName("a!!b")).toBe("a-b");
    expect(helpers.safeName("snake_case")).toBe("snake_case");
    expect(helpers.safeName("a".repeat(50))).toHaveLength(40);
    expect(helpers.safeName("")).toBe("");
    expect(helpers.nameFromUrl("https://api.example.com/mcp")).toBe("api-example-com");
    expect(helpers.nameFromUrl("not a url")).toBe("");
  });

  it("guessLauncher: pip:/pypi: or a PyPI-looking bare name reads as uvx, else npx", () => {
    expect(helpers.guessLauncher("pip:mcp-server-time")).toBe("uvx");
    expect(helpers.guessLauncher("PYPI:x")).toBe("uvx");
    expect(helpers.guessLauncher("mcp_server_time")).toBe("uvx");
    expect(helpers.guessLauncher("mcp-server-git")).toBe("uvx");
    expect(helpers.guessLauncher("@scope/mcp_x")).toBe("npx");
    expect(helpers.guessLauncher("npm:mcp_x")).toBe("npx");
    expect(helpers.guessLauncher("server.js")).toBe("npx");
    expect(helpers.guessLauncher("@modelcontextprotocol/server-slack")).toBe("npx");
    expect(helpers.guessLauncher("server-slack")).toBe("npx");
  });

  it("buildLaunch per launcher; a typed command splits on whitespace", () => {
    expect(helpers.buildLaunch("pkg", "npx")).toEqual({ command: "npx", args: ["-y", "pkg"] });
    expect(helpers.buildLaunch("pip:pkg", "uvx")).toEqual({ command: "uvx", args: ["pkg"] });
    expect(helpers.buildLaunch("pkg", "pipx")).toEqual({ command: "pipx", args: ["run", "pkg"] });
    expect(helpers.buildLaunch("server.js", "node")).toEqual({ command: "node", args: ["server.js"] });
    expect(helpers.buildLaunch("mod", "python")).toEqual({ command: "python", args: ["-m", "mod"] });
    expect(helpers.buildLaunch("docker run -i img", "command")).toEqual({ command: "docker", args: ["run", "-i", "img"] });
  });

  it("rowStatus: added wins, http needs nothing, runtime_ready decides ready vs blocked", () => {
    expect(helpers.rowStatus(FILESYSTEM)).toEqual({ status: "added" });
    expect(helpers.rowStatus(REMOTE)).toEqual({ status: "available" });
    expect(helpers.rowStatus(BRAVE)).toEqual({ status: "ready", needs: "Node" });
    expect(helpers.rowStatus(PY)).toEqual({ status: "blocked", needs: "Python (uv)" });
    expect(helpers.rowStatus({ ...PY, runtime_ready: null })).toEqual({ status: "blocked", needs: "Python (uv)" });
    expect(helpers.needsFor("docker")).toBe("Docker");
    expect(helpers.needsFor("node.exe")).toBe("Node");
  });

  it("httpServerBody: blank headers dropped, no token = no auth, a bad name falls back to the host", () => {
    expect(
      helpers.httpServerBody({
        name: "",
        url: " https://h.example/mcp ",
        transport: "http",
        headers: [
          { name: " ", value: "x" },
          { name: "A", value: "" },
        ],
        token: "  ",
      }),
    ).toEqual({ name: "h-example", transport: "http", url: "https://h.example/mcp", headers: { A: "" } });
  });
});

/* ===================================================================== */
/*  copy guards                                                            */
/* ===================================================================== */

const NEW_FILES = ["components/tools/AddPack.tsx", "components/tools/EditPack.tsx", "lib/mcpImport.ts"];

describe("copy guards for the new files", () => {
  it("no dash asides in user-visible copy", () => {
    for (const f of NEW_FILES) expect(asides(f), f).toEqual([]);
  });
  it("whole-pixel sizes only, no literal hues", () => {
    for (const f of NEW_FILES) {
      const src = readSrc(f);
      expect(src.match(/\btext-\[\d+\.5px\]/g) ?? [], f).toEqual([]);
      expect(
        src.match(/\b(?:text|bg|border|ring)-(?:amber|emerald|rose|red|green|blue|violet|sky|cyan|orange|yellow|lime|teal|indigo|purple|pink|fuchsia)-\d{3}\b/g) ?? [],
        f,
      ).toEqual([]);
    }
  });
  it("every Badge in the new components is calm", () => {
    expect(uncalm("components/tools/AddPack.tsx")).toEqual([]);
    expect(uncalm("components/tools/EditPack.tsx")).toEqual([]);
  });
  it("the Add a pack area is mounted on the Tools page and the row offers Edit", () => {
    const src = readSrc("components/settings/pages/ToolsPage.tsx");
    expect(src).toContain("<AddPack onAdded=");
    expect(src).toContain("<EditPack");
    expect(src).toContain("data-testid={`mcp-edit-${s.name}`}");
  });
});
