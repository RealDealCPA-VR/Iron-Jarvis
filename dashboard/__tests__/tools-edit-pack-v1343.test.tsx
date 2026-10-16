/**
 * v1.343.0 ("add a pack in one paste"), lane K2/K3: every pack row gets
 * Edit, which opens an inline form prefilled from the row and sends a
 * PARTIAL PATCH /mcp/servers/{name}.
 *
 *  - Edit sits beside Test / Delete; the form prefills command, args as one
 *    line, the plain env keys as rows, the secret keys as "set" chips with
 *    Replace / Clear, the folder; an HTTP pack shows URL, header chips and
 *    the token chip;
 *  - Save sends ONLY the fields the user changed; nothing changed = no PATCH;
 *  - Clear sends clear_secrets (and clear_headers / auth: null for HTTP);
 *  - "" typed is a value, never a clear (the v1.164.0 rule);
 *  - no value is ever rendered, even when an older daemon's row carries one;
 *  - the name is not editable;
 *  - after a save the row is re-read and the form closes with a note.
 *
 * Harness mirrors tools-pack-rows-v1342 (the page's data seams are mocked).
 */
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
    gets: [] as string[],
    patches: [] as { path: string; body: unknown }[],
    patchResult: { reloaded: true, note: null } as Record<string, unknown>,
    patchError: null as Error | null,
    servers: [] as Record<string, unknown>[],
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
    if (path === "/mcp/catalog") return { catalog: [] };
    if (path === "/tools/custom") return { tools: [] };
    return {};
  },
  post: async () => ({}),
  put: async () => ({}),
  patch: async (path: string, body?: unknown) => {
    api.patches.push({ path, body });
    if (api.patchError) throw api.patchError;
    return api.patchResult;
  },
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

const NEVER = "SHOULD-NEVER-SHOW";

function row(name: string, over: Record<string, unknown> = {}) {
  return {
    name,
    command: "npx",
    args: ["-y", `@example/${name}`],
    tools_loaded: 2,
    tool_names: ["a", "b"],
    last_error: null,
    state: "ok",
    failures: 0,
    reason: null,
    fix: null,
    stderr_tail: "",
    env_keys: [],
    secret_keys: [],
    ...over,
  };
}

const BRAVE = row("brave", {
  args: ["-y", "@brave/x"],
  env_keys: ["BRAVE_API_KEY", "REGION"],
  secret_keys: ["BRAVE_API_KEY"],
  cwd: "",
});
// An older daemon's row that still carries values: they must not render.
const LEGACY = row("legacy", {
  env: { TOKEN: NEVER },
  env_keys: ["TOKEN"],
  secret_keys: ["TOKEN"],
  headers: { "X-Old": NEVER },
});
const REMOTE = row("remote", {
  transport: "http",
  command: "",
  args: [],
  url: "https://host/mcp",
  header_names: ["X-Api-Key"],
  auth: { header: "Authorization", format: "Bearer {value}", value_set: true },
});
const NOTOKEN = row("open", {
  transport: "sse",
  command: "",
  args: [],
  url: "https://open/sse",
  header_names: [],
});

beforeEach(() => {
  api.gets = [];
  api.patches = [];
  api.patchResult = { reloaded: true, note: null };
  api.patchError = null;
  api.servers = [BRAVE, LEGACY, REMOTE, NOTOKEN];
});
afterEach(() => cleanup());

async function openEditor(name: string) {
  render(<ToolsPage />);
  const edit = await screen.findByTestId(`mcp-edit-${name}`);
  fireEvent.click(edit);
  return await screen.findByTestId(`edit-pack-${name}`);
}

const patchBody = (i = 0) => api.patches[i]?.body as Record<string, unknown>;

/* ===================================================================== */
/*  the form                                                               */
/* ===================================================================== */

describe("Edit on a pack row", () => {
  it("sits beside Test and Delete and opens a form prefilled from the row: command, args as one line, plain env rows, secret chips, folder; the name is not editable", async () => {
    const form = await openEditor("brave");
    // beside Test / Delete, in the same action group
    const edit = screen.getByTestId("mcp-edit-brave");
    const group = edit.parentElement as HTMLElement;
    expect(within(group).getByText("Test")).toBeTruthy();
    expect(within(group).getByText("Delete")).toBeTruthy();
    expect(edit.getAttribute("aria-pressed")).toBe("true");
    // prefilled
    expect((within(form).getByTestId("edit-command-brave") as HTMLInputElement).value).toBe("npx");
    expect((within(form).getByTestId("edit-args-brave") as HTMLInputElement).value).toBe("-y @brave/x");
    expect((within(form).getByTestId("edit-cwd-brave") as HTMLInputElement).value).toBe("");
    const region = within(form).getByTestId("edit-env-brave-REGION") as HTMLInputElement;
    expect(region.value).toBe("");
    expect(region.placeholder).toContain("Unchanged");
    // the secret key is a chip, not a box: "set", Replace, Clear
    const chip = within(form).getByTestId("edit-secret-brave-BRAVE_API_KEY-chip");
    expect(chip.textContent).toContain("BRAVE_API_KEY");
    expect(chip.textContent).toContain("set");
    expect(within(chip).getByTestId("edit-secret-brave-BRAVE_API_KEY-replace")).toBeTruthy();
    expect(within(chip).getByTestId("edit-secret-brave-BRAVE_API_KEY-clear")).toBeTruthy();
    expect(within(form).queryByTestId("edit-env-brave-BRAVE_API_KEY")).toBeNull();
    // the name is shown, never as a box
    expect(form.textContent).toContain("Editing brave");
    expect(form.textContent).toContain("The name cannot change");
    const inputs = Array.from(form.querySelectorAll("input")) as HTMLInputElement[];
    expect(inputs.some((i) => i.value === "brave")).toBe(false);
    // Edit again closes it
    fireEvent.click(edit);
    await waitFor(() => expect(screen.queryByTestId("edit-pack-brave")).toBeNull());
  });

  it("an http pack's row says the transport and the URL in place of a command line; a stdio row keeps its argv", async () => {
    render(<ToolsPage />);
    const remote = await screen.findByTestId("mcp-launch-remote");
    expect(remote.textContent).toContain("HTTP https://host/mcp");
    expect(remote.textContent).not.toContain("npx");
    // an sse pack reads SSE; a stdio pack is still its command line
    expect(screen.getByTestId("mcp-launch-open").textContent).toBe("SSE https://open/sse");
    expect(screen.getByTestId("mcp-launch-brave").textContent).toBe("npx -y @brave/x");
  });

  it("never renders a value, even off an older daemon's row that carries one", async () => {
    const form = await openEditor("legacy");
    expect(form.textContent).not.toContain(NEVER);
    expect(document.body.innerHTML).not.toContain(NEVER);
    // the key is still known: a chip with Replace / Clear
    expect(within(form).getByTestId("edit-secret-legacy-TOKEN-chip").textContent).toContain("TOKEN");
    fireEvent.click(within(form).getByTestId("edit-secret-legacy-TOKEN-replace"));
    const box = within(form).getByTestId("edit-secret-legacy-TOKEN") as HTMLInputElement;
    expect(box.type).toBe("password");
    expect(box.value).toBe("");
    expect(document.body.innerHTML).not.toContain(NEVER);
  });
});

/* ===================================================================== */
/*  the partial PATCH                                                      */
/* ===================================================================== */

describe("Save sends a partial PATCH", () => {
  it("carries ONLY the changed fields, closes the form, says saved and reloaded, re-reads the rows", async () => {
    const form = await openEditor("brave");
    const getsBefore = api.gets.filter((g) => g === "/mcp/servers").length;
    fireEvent.change(within(form).getByTestId("edit-args-brave"), { target: { value: "-y  @brave/x --flag" } });
    fireEvent.change(within(form).getByTestId("edit-env-brave-REGION"), { target: { value: "us" } });
    fireEvent.click(within(form).getByTestId("edit-save-brave"));

    const note = await screen.findByText(/"brave" saved and reloaded/);
    expect(note).toBeTruthy();
    expect(api.patches).toEqual([
      { path: "/mcp/servers/brave", body: { args: ["-y", "@brave/x", "--flag"], env: { REGION: "us" } } },
    ]);
    await waitFor(() => expect(screen.queryByTestId("edit-pack-brave")).toBeNull());
    await waitFor(() =>
      expect(api.gets.filter((g) => g === "/mcp/servers").length).toBeGreaterThan(getsBefore),
    );
  });

  it("nothing changed: no PATCH, one quiet line", async () => {
    const form = await openEditor("brave");
    fireEvent.click(within(form).getByTestId("edit-save-brave"));
    expect((await within(form).findByTestId("edit-nothing-brave")).textContent).toContain("Nothing changed");
    expect(api.patches).toEqual([]);
    expect(screen.getByTestId("edit-pack-brave")).toBeTruthy();
  });

  it('"" typed into a field is a value, never a clear', async () => {
    const form = await openEditor("brave");
    fireEvent.change(within(form).getByTestId("edit-command-brave"), { target: { value: "" } });
    fireEvent.click(within(form).getByTestId("edit-save-brave"));
    await screen.findByText(/"brave" saved/);
    expect(patchBody()).toEqual({ command: "" });
  });

  it("Clear on a secret sends clear_secrets and no env for that key; Keep undoes it", async () => {
    const form = await openEditor("brave");
    fireEvent.click(within(form).getByTestId("edit-secret-brave-BRAVE_API_KEY-clear"));
    const chip = within(form).getByTestId("edit-secret-brave-BRAVE_API_KEY-chip");
    expect(chip.textContent).toContain("will be cleared");
    // Keep puts it back
    fireEvent.click(within(chip).getByTestId("edit-secret-brave-BRAVE_API_KEY-keep"));
    expect(chip.textContent).toContain("set");
    expect(chip.textContent).not.toContain("will be cleared");
    // Clear again, then save
    fireEvent.click(within(chip).getByTestId("edit-secret-brave-BRAVE_API_KEY-clear"));
    fireEvent.click(within(form).getByTestId("edit-save-brave"));
    await screen.findByText(/"brave" saved/);
    expect(patchBody()).toEqual({ clear_secrets: ["BRAVE_API_KEY"] });
  });

  it("Replace on a secret sends the new value under env and the value never renders", async () => {
    const form = await openEditor("brave");
    fireEvent.click(within(form).getByTestId("edit-secret-brave-BRAVE_API_KEY-replace"));
    fireEvent.change(within(form).getByTestId("edit-secret-brave-BRAVE_API_KEY"), { target: { value: "sk-NEW-9" } });
    fireEvent.click(within(form).getByTestId("edit-save-brave"));
    await screen.findByText(/"brave" saved/);
    expect(patchBody()).toEqual({ env: { BRAVE_API_KEY: "sk-NEW-9" } });
    // the form closed; the typed value is gone with it
    await waitFor(() => expect(screen.queryByTestId("edit-pack-brave")).toBeNull());
    expect(document.body.innerHTML).not.toContain("sk-NEW-9");
  });

  it("a new variable row sends env with its key; the save note carries the daemon's note", async () => {
    api.patchResult = { reloaded: false, note: "it did not start: npx was not found" };
    const form = await openEditor("brave");
    fireEvent.click(within(form).getByTestId("edit-env-add-brave"));
    fireEvent.change(within(form).getByTestId("edit-newenv-key-brave-1"), { target: { value: "NEW_KEY" } });
    fireEvent.change(within(form).getByTestId("edit-newenv-value-brave-1"), { target: { value: "v" } });
    fireEvent.click(within(form).getByTestId("edit-save-brave"));
    expect((await screen.findByText(/"brave" saved\. it did not start: npx was not found/)).textContent).toBeTruthy();
    expect(patchBody()).toEqual({ env: { NEW_KEY: "v" } });
  });

  it("a refused PATCH keeps the form open with the daemon's words", async () => {
    api.patchError = new api.FakeApiError("a rename is refused: the name is the pack's identity", 422);
    const form = await openEditor("brave");
    fireEvent.change(within(form).getByTestId("edit-cwd-brave"), { target: { value: "C:\\work" } });
    fireEvent.click(within(form).getByTestId("edit-save-brave"));
    expect((await within(form).findByRole("alert")).textContent).toContain("a rename is refused");
    expect(screen.getByTestId("edit-pack-brave")).toBeTruthy();
    expect(patchBody()).toEqual({ cwd: "C:\\work" });
  });
});

/* ===================================================================== */
/*  an HTTP pack                                                           */
/* ===================================================================== */

describe("an HTTP pack's editor", () => {
  it("shows the URL, header chips with Replace / Clear and the token chip; Clear sends clear_headers and auth: null", async () => {
    const form = await openEditor("remote");
    expect((within(form).getByTestId("edit-transport-remote") as HTMLSelectElement).value).toBe("http");
    expect((within(form).getByTestId("edit-url-remote") as HTMLInputElement).value).toBe("https://host/mcp");
    expect(within(form).queryByTestId("edit-command-remote")).toBeNull();
    const header = within(form).getByTestId("edit-header-remote-X-Api-Key-chip");
    expect(header.textContent).toContain("X-Api-Key");
    expect(header.textContent).toContain("set");
    const token = within(form).getByTestId("edit-token-remote-chip");
    expect(token.textContent).toContain("Authorization");
    expect(token.textContent).toContain("set");
    fireEvent.click(within(form).getByTestId("edit-header-remote-X-Api-Key-clear"));
    fireEvent.click(within(form).getByTestId("edit-token-remote-clear"));
    fireEvent.click(within(form).getByTestId("edit-save-remote"));
    await screen.findByText(/"remote" saved/);
    expect(patchBody()).toEqual({ clear_headers: ["X-Api-Key"], auth: null });
  });

  it("Replace on a header and on the token sends headers + the bearer auth shape; the URL change rides too", async () => {
    const form = await openEditor("remote");
    fireEvent.change(within(form).getByTestId("edit-url-remote"), { target: { value: "https://host/mcp2" } });
    fireEvent.click(within(form).getByTestId("edit-header-remote-X-Api-Key-replace"));
    fireEvent.change(within(form).getByTestId("edit-header-remote-X-Api-Key"), { target: { value: "h2" } });
    fireEvent.click(within(form).getByTestId("edit-token-remote-replace"));
    fireEvent.change(within(form).getByTestId("edit-token-remote"), { target: { value: "tok-2" } });
    fireEvent.click(within(form).getByTestId("edit-save-remote"));
    await screen.findByText(/"remote" saved/);
    expect(patchBody()).toEqual({
      url: "https://host/mcp2",
      headers: { "X-Api-Key": "h2" },
      auth: { header: "Authorization", format: "Bearer {value}", value: "tok-2" },
    });
    await waitFor(() => expect(screen.queryByTestId("edit-pack-remote")).toBeNull());
    expect(document.body.innerHTML).not.toContain("tok-2");
  });

  it("a pack with no token offers a box; typing one sends the auth shape; switching the type sends transport", async () => {
    const form = await openEditor("open");
    expect(within(form).queryByTestId("edit-token-open-chip")).toBeNull();
    const box = within(form).getByTestId("edit-token-open") as HTMLInputElement;
    expect(box.type).toBe("password");
    fireEvent.change(box, { target: { value: "t1" } });
    fireEvent.change(within(form).getByTestId("edit-transport-open"), { target: { value: "http" } });
    fireEvent.click(within(form).getByTestId("edit-save-open"));
    await screen.findByText(/"open" saved/);
    expect(patchBody()).toEqual({
      transport: "http",
      auth: { header: "Authorization", format: "Bearer {value}", value: "t1" },
    });
  });
});

/* ===================================================================== */
/*  the pure half                                                          */
/* ===================================================================== */

describe("lib/mcpImport editPatch + seedFromRow", () => {
  const seed = helpers.seedFromRow(BRAVE);

  it("seedFromRow reads names only and never a value", () => {
    expect(seed).toEqual({
      name: "brave",
      transport: "stdio",
      command: "npx",
      args: ["-y", "@brave/x"],
      cwd: "",
      url: "",
      envKeys: ["BRAVE_API_KEY", "REGION"],
      secretKeys: ["BRAVE_API_KEY"],
      headerNames: [],
      tokenSet: false,
    });
    const legacy = helpers.seedFromRow(LEGACY);
    expect(legacy.headerNames).toEqual(["X-Old"]);
    expect(JSON.stringify(legacy)).not.toContain(NEVER);
    const remote = helpers.seedFromRow(REMOTE);
    expect(remote.tokenSet).toBe(true);
    expect(remote.headerNames).toEqual(["X-Api-Key"]);
    expect(helpers.seedFromRow({ ...REMOTE, auth: { secret: "mcp.remote.auth.value" } }).tokenSet).toBe(true);
    expect(helpers.seedFromRow(NOTOKEN).tokenSet).toBe(false);
  });

  it("editPatch: untouched = {}, whitespace in args is not a change, '' is a value, a cleared key never rides env", () => {
    const st = helpers.seedState(seed);
    expect(helpers.editPatch(seed, st)).toEqual({});
    expect(helpers.editPatch(seed, { ...st, args: "  -y   @brave/x " })).toEqual({});
    expect(helpers.editPatch(seed, { ...st, cwd: "" })).toEqual({});
    expect(helpers.editPatch(seed, { ...st, command: "" })).toEqual({ command: "" });
    expect(helpers.editPatch(seed, { ...st, envValues: { REGION: "" } })).toEqual({ env: { REGION: "" } });
    expect(
      helpers.editPatch(seed, {
        ...st,
        secretValues: { BRAVE_API_KEY: "x" },
        clearSecrets: ["BRAVE_API_KEY"],
      }),
    ).toEqual({ clear_secrets: ["BRAVE_API_KEY"] });
    expect(helpers.editPatch(seed, { ...st, secretValues: { BRAVE_API_KEY: "" } })).toEqual({});
    expect(helpers.editPatch(seed, { ...st, token: "" })).toEqual({});
    expect(helpers.editPatch(seed, { ...st, clearToken: true, token: "zzz" })).toEqual({ auth: null });
  });
});
