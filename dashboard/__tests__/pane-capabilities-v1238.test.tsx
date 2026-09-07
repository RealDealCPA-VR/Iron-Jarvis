/**
 * Pane Capabilities, and the Launch menu that knows its recipes (v1.238.0).
 *
 * Ship 4 lets an EXTERNAL harness drive the same BrowserService through the
 * same gates. Two surfaces carry that on the dashboard, and both are about
 * telling the truth before anything runs:
 *
 *   - the rail's Capabilities popover (D20): five boxes, one of which is
 *     actually enforced, and a Browser box that reads UNAVAILABLE WITH A WORD
 *     when the whole install has Browser off — never merely unticked, and never
 *     colour alone;
 *   - the Launch menu (D18): the recipe state, shown BEFORE the launch, because
 *     "a harness that cannot be isolated must say so where the user is
 *     standing".
 *
 * jsdom cannot render an xterm pane, so the Launch half follows the house idiom
 * (v1.163.0, v1.190.0, v1.194.0): unit-test the seam, mount the small component
 * that renders it, and SOURCE-PIN the call site inside TerminalPane. The source
 * is read with line endings normalised — CI checks out with CRLF.
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/* ---- api ------------------------------------------------------------------- */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    responses: {} as Record<string, unknown>,
    patches: [] as [string, unknown][],
    patchFails: false,
    gets: [] as string[],
    /** The daemon's side of the pane store, keyed by pane id.
     *
     * A stub that just resolves `{}` cannot see this ship's S1: the defect was
     * a client body that OVERWROTE keys the server held, and a body that
     * overwrites is indistinguishable from one that merges unless something on
     * the other end actually merges and actually answers. So this fake does
     * what `TerminalSession.update_capabilities` does — a partial merge — and
     * answers with the whole map, exactly as `PATCH /terminals/{id}` returns
     * `session.info()`. */
    server: {} as Record<string, Record<string, boolean>>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: (path: string) => {
    api.gets.push(path);
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    }
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
  patch: (path: string, body: unknown) => {
    api.patches.push([path, body]);
    if (api.patchFails) return Promise.reject(new api.FakeApiError("refused", 400));
    const id = path.split("/").pop() || "";
    const sent = (body as { capabilities?: Record<string, boolean> })?.capabilities;
    const held = api.server[id] || {
      files: false,
      shell: false,
      browser: false,
      extensions: false,
      memory: false,
    };
    api.server[id] = { ...held, ...(sent || {}) };
    return Promise.resolve({ id, capabilities: { ...api.server[id] } });
  },
  del: () => Promise.resolve({}),
  wsUrl: (p: string) => `ws://test${p}`,
  sseUrl: (p: string) => `http://test${p}`,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
}));

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children?: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

import {
  PaneRail,
  PANE_CAPABILITIES,
  capabilityStatus,
  fullCapabilities,
  type RailPane,
} from "@/components/terminal/PaneRail";
import {
  LaunchRecipeNote,
  recipeMethodWord,
  recipeNote,
  type LaunchCli,
} from "@/components/terminal/TerminalPane";

/* ---- fixtures -------------------------------------------------------------- */

const pane = (over: Partial<RailPane> = {}): RailPane => ({
  id: "t1",
  label: "alpha",
  state: "idle",
  ...over,
});

function renderRail(panes: RailPane[], onFocus = vi.fn()) {
  render(
    <PaneRail
      panes={panes}
      focusedId={panes[0]?.id ?? null}
      onFocus={onFocus}
      onClose={vi.fn()}
      onRename={vi.fn()}
      onNew={vi.fn()}
    />,
  );
  return { onFocus };
}

/** Open the popover on a pane and wait for the install setting to land. */
async function openCaps(id = "t1") {
  fireEvent.click(screen.getByTestId(`rail-caps-${id}`));
  await screen.findByTestId(`rail-caps-panel-${id}`);
}

const word = (id: string, key: string) =>
  screen.getByTestId(`rail-cap-word-${id}-${key}`).textContent ?? "";

/** Seed the daemon's side: what `GET /terminals` and the merge start from. */
function serverHas(id: string, caps: Record<string, boolean>) {
  api.server[id] = {
    files: false,
    shell: false,
    browser: false,
    extensions: false,
    memory: false,
    ...caps,
  };
  api.responses["/terminals"] = {
    terminals: Object.entries(api.server).map(([tid, c]) => ({
      id: tid,
      capabilities: { ...c },
    })),
  };
}

beforeEach(() => {
  api.responses = {
    "/browser/status": { access: "interactive" },
    "/terminals": { terminals: [] },
  };
  api.patches = [];
  api.gets = [];
  api.patchFails = false;
  api.server = {};
});

afterEach(cleanup);

/* --------------------------------------------------------------------------- */

describe("the Capabilities popover", () => {
  it("offers all five boxes, in D20's order", async () => {
    renderRail([pane()]);
    await openCaps();
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(5);
    expect(PANE_CAPABILITIES.map((c) => c.label)).toEqual([
      "Files",
      "Shell",
      "Browser",
      "Extensions",
      "Memory",
    ]);
    for (const cap of PANE_CAPABILITIES) {
      expect(screen.getByTestId(`rail-cap-t1-${cap.key}`)).toBeInTheDocument();
    }
  });

  it("shows the pane's SERVER-side capabilities, not a fresh set of blanks", async () => {
    renderRail([pane({ capabilities: { browser: true, shell: true } })]);
    await openCaps();
    await waitFor(() =>
      expect(screen.getByTestId("rail-cap-t1-browser")).toHaveAttribute("aria-checked", "true"),
    );
    expect(screen.getByTestId("rail-cap-t1-shell")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("rail-cap-t1-files")).toHaveAttribute("aria-checked", "false");
  });

  it("a plain click on the row still SELECTS the pane, and opens nothing", async () => {
    const { onFocus } = renderRail([pane({ id: "t2", label: "beta" }), pane()]);
    fireEvent.click(screen.getByRole("button", { name: /^beta/ }));
    expect(onFocus).toHaveBeenCalledWith("t2");
    expect(screen.queryByTestId("rail-caps-panel-t2")).toBeNull();
  });

  it("toggling writes ONE PATCH, carrying ONLY the box that was clicked", async () => {
    serverHas("t1", { shell: true });
    renderRail([pane({ capabilities: { shell: true } })]);
    await openCaps();
    await waitFor(() =>
      expect(screen.getByTestId("rail-cap-t1-browser")).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByTestId("rail-cap-t1-browser"));
    await waitFor(() => expect(api.patches).toHaveLength(1));
    expect(api.patches[0][0]).toBe("/terminals/t1");
    // ONE key. The daemon merges; a whole-map body would make this client's
    // cached copy of the other four authoritative, which is precisely how a
    // stale popover revoked a live grant.
    expect(api.patches[0][1]).toEqual({ capabilities: { browser: true } });
    expect(Object.keys((api.patches[0][1] as { capabilities: object }).capabilities)).toEqual([
      "browser",
    ]);
    expect(api.patches).toHaveLength(1);
  });

  it("a REOPENED popover shows what the daemon holds, not what the page loaded", async () => {
    // The Build page fetches /terminals once, on mount. Without a re-read the
    // second open renders the mount-time value \u2014 telling the user a pane has no
    // Browser capability while its running harness holds a token that resolves
    // to browser:true.
    serverHas("t1", { browser: true, memory: true });
    renderRail([pane({ capabilities: {} })]);
    await openCaps();
    await waitFor(() =>
      expect(screen.getByTestId("rail-cap-t1-browser")).toHaveAttribute("aria-checked", "true"),
    );
    expect(screen.getByTestId("rail-cap-t1-memory")).toHaveAttribute("aria-checked", "true");
    expect(api.gets).toContain("/terminals");
  });

  it("a second toggle cannot revoke what the server granted meanwhile", async () => {
    // The deterministic repro: the props say nothing is granted, the daemon
    // says Browser is. Ticking Shell must not carry `browser: false` with it.
    serverHas("t1", { browser: true });
    renderRail([pane({ capabilities: {} })]);
    await openCaps();
    await waitFor(() =>
      expect(screen.getByTestId("rail-cap-t1-browser")).toHaveAttribute("aria-checked", "true"),
    );
    fireEvent.click(screen.getByTestId("rail-cap-t1-shell"));
    await waitFor(() => expect(api.patches).toHaveLength(1));
    expect(api.patches[0][1]).toEqual({ capabilities: { shell: true } });
    // The daemon's own record, after the write it actually received.
    expect(api.server.t1.browser).toBe(true);
    expect(api.server.t1.shell).toBe(true);
    await waitFor(() =>
      expect(screen.getByTestId("rail-cap-t1-browser")).toHaveAttribute("aria-checked", "true"),
    );
  });

  it("adopts the daemon's answer, so the boxes show the MERGED truth", async () => {
    // The one case an optimistic overlay cannot get right on its own: the
    // client's picture is wrong AND the re-read did not fix it (the list call
    // failed, or the pane was not in it). The PATCH response — the pane's whole
    // merged map, exactly what `PATCH /terminals/{id}` returns — is then the
    // only fresh truth on the wire, and the boxes have to take it.
    serverHas("t1", { files: true });
    api.responses["/terminals"] = { terminals: [] }; // the re-read finds nothing
    renderRail([pane({ capabilities: {} })]);
    await openCaps();
    // The stale picture: Files reads OFF while the daemon holds it ON.
    expect(screen.getByTestId("rail-cap-t1-files")).toHaveAttribute("aria-checked", "false");
    fireEvent.click(screen.getByTestId("rail-cap-t1-memory"));
    await waitFor(() =>
      expect(screen.getByTestId("rail-cap-t1-memory")).toHaveAttribute("aria-checked", "true"),
    );
    // Corrected by the response, not by a second round trip.
    await waitFor(() =>
      expect(screen.getByTestId("rail-cap-t1-files")).toHaveAttribute("aria-checked", "true"),
    );
    expect(api.gets.filter((g) => g === "/terminals")).toHaveLength(1);
  });

  it("puts the box back and SAYS SO when the daemon refuses the write", async () => {
    api.patchFails = true;
    renderRail([pane()]);
    await openCaps();
    await waitFor(() => expect(screen.getByTestId("rail-cap-t1-memory")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("rail-cap-t1-memory"));
    await screen.findByRole("alert");
    // Not left looking ticked: the pane has what it had.
    expect(screen.getByTestId("rail-cap-t1-memory")).toHaveAttribute("aria-checked", "false");
  });

  it("does not claim enforcement it does not have", async () => {
    renderRail([pane()]);
    await openCaps();
    expect(word("t1", "files")).toMatch(/not enforced yet/i);
    expect(word("t1", "shell")).toMatch(/not enforced yet/i);
    expect(word("t1", "extensions")).toMatch(/not enforced yet/i);
    expect(word("t1", "memory")).toMatch(/not enforced yet/i);
    expect(screen.getByTestId("rail-caps-panel-t1").textContent).toMatch(
      /nothing gates on them yet/i,
    );
  });

  it("THE WORD MATCHES THE GATE: an unticked Browser box says the pane gets nothing", async () => {
    // The v1.238.0 S1 in one assertion. The daemon's chat gate and its outward
    // MCP grant both refuse an unticked pane, so an unticked box that merely
    // says "enforced" describes the SETTING and leaves the pane's actual state
    // unsaid \u2014 which is how the UI came to read denied while the code allowed.
    renderRail([pane({ capabilities: { browser: false } })]);
    await openCaps();
    await waitFor(() => expect(word("t1", "browser")).toMatch(/no browser tools/i));
    expect(screen.getByTestId("rail-cap-t1-browser")).toHaveAttribute("aria-checked", "false");
  });

  it("THE WORD MATCHES THE GATE: a ticked Browser box says the pane may use it", async () => {
    renderRail([pane({ capabilities: { browser: true } })]);
    await openCaps();
    await waitFor(() => expect(word("t1", "browser")).toMatch(/may use your browser/i));
  });

  it("the footnote states the DEFAULT, because every existing pane is on it", async () => {
    renderRail([pane()]);
    await openCaps();
    const note = screen.getByTestId("rail-caps-footnote-t1").textContent ?? "";
    expect(note).toMatch(/starts unticked/i);
    expect(note).toMatch(/no browser tools/i);
  });
});

describe("Browser is unavailable WITH A WORD when the install has it off", () => {
  it("reads unavailable, is not clickable, and points at the Browser page", async () => {
    api.responses["/browser/status"] = { access: "off" };
    renderRail([pane()]);
    await openCaps();
    await waitFor(() => expect(word("t1", "browser")).toBe("off for this install"));
    expect(screen.getByTestId("rail-cap-t1-browser")).toBeDisabled();
    expect(screen.getByRole("link", { name: /Browser page/i })).toHaveAttribute(
      "href",
      "/computeruse",
    );
    fireEvent.click(screen.getByTestId("rail-cap-t1-browser"));
    expect(api.patches).toEqual([]);
  });

  it("D20's example: global off PLUS pane checked equals unavailable, said out loud", async () => {
    api.responses["/browser/status"] = { access: "off" };
    renderRail([pane({ capabilities: { browser: true } })]);
    await openCaps();
    const conflict = await screen.findByTestId("rail-cap-conflict-t1-browser");
    expect(conflict.textContent).toMatch(/unavailable/i);
    // The tick is still shown — it is the user's recorded choice — but the row
    // never lets that read as working.
    expect(screen.getByTestId("rail-cap-t1-browser")).toHaveAttribute("aria-checked", "true");
  });

  it("a grant can always be WITHDRAWN, even while the install setting is off", async () => {
    // Only the off->on direction waits on the install setting. Locking a ticked
    // box left the user having to re-enable Browser install-wide in order to
    // take it away from one pane \u2014 the opposite of where they were heading.
    api.responses["/browser/status"] = { access: "off" };
    serverHas("t1", { browser: true });
    renderRail([pane({ capabilities: { browser: true } })]);
    await openCaps();
    const box = screen.getByTestId("rail-cap-t1-browser");
    await waitFor(() => expect(box).not.toBeDisabled());
    fireEvent.click(box);
    await waitFor(() => expect(api.patches).toEqual([["/terminals/t1", { capabilities: { browser: false } }]]));
    expect(api.server.t1.browser).toBe(false);
    // And once it is off, the off->on direction is locked again.
    await waitFor(() => expect(screen.getByTestId("rail-cap-t1-browser")).toBeDisabled());
  });

  it("an install setting it could not read is unavailable too, and names that", async () => {
    delete api.responses["/browser/status"];
    renderRail([pane()]);
    await openCaps();
    await waitFor(() => expect(word("t1", "browser")).toBe("install setting unreadable"));
    expect(screen.getByTestId("rail-cap-t1-browser")).toBeDisabled();
  });

  it("asks the daemon for the install setting only when a popover opens", async () => {
    renderRail([pane()]);
    expect(api.gets).toEqual([]);
    await openCaps();
    await waitFor(() => expect(api.gets).toContain("/browser/status"));
    // Two reads, both on open, neither polled: the setting and the pane.
    expect(api.gets.sort()).toEqual(["/browser/status", "/terminals"]);
  });
});

describe("capabilityStatus — the pure rule the boxes read", () => {
  it("says what an unticked Browser box does to the pane, and what a ticked one does", () => {
    expect(capabilityStatus("browser", "interactive", false, false).word).toMatch(
      /no browser tools/i,
    );
    expect(capabilityStatus("browser", "interactive", false, true).word).toMatch(
      /may use your browser/i,
    );
    // Unavailable outranks the tick: the install setting still gets the word.
    expect(capabilityStatus("browser", "off", false, true).word).toBe("off for this install");
  });

  it("never calls Browser available on an unknown or off install", () => {
    expect(capabilityStatus("browser", "", false).available).toBe(false);
    expect(capabilityStatus("browser", "", true).available).toBe(false);
    expect(capabilityStatus("browser", "off", false).available).toBe(false);
    expect(capabilityStatus("browser", "off", false).offForInstall).toBe(true);
    expect(capabilityStatus("browser", "read_only", false).available).toBe(true);
    expect(capabilityStatus("browser", "interactive", false).available).toBe(true);
  });

  it("gives every state a word", () => {
    for (const access of ["", "off", "read_only", "interactive", "nonsense"]) {
      for (const failed of [true, false]) {
        for (const on of [true, false]) {
          expect(capabilityStatus("browser", access, failed, on).word.trim()).not.toBe("");
        }
      }
    }
  });

  it("says the other four are recorded, never enforced", () => {
    for (const cap of PANE_CAPABILITIES.filter((c) => c.key !== "browser")) {
      const st = capabilityStatus(cap.key, "interactive", false);
      expect(st.word).toMatch(/not enforced/i);
      expect(st.available).toBe(true);
    }
  });

  it("fullCapabilities fills every key and lets the later layer win", () => {
    expect(fullCapabilities(null)).toEqual({
      files: false,
      shell: false,
      browser: false,
      extensions: false,
      memory: false,
    });
    expect(fullCapabilities({ browser: true }, { browser: false, files: true })).toEqual({
      files: true,
      shell: false,
      browser: false,
      extensions: false,
      memory: false,
    });
  });
});

/* ---- the Launch menu ------------------------------------------------------- */

const cli = (over: Partial<LaunchCli> = {}): LaunchCli => ({
  id: "claude",
  label: "Claude Code",
  command: "claude",
  provider: "Anthropic",
  url: "",
  installed: true,
  ...over,
});

describe("recipeNote — what the Launch menu knows before the launch", () => {
  it("a CLI with no recipe launches as it always did, and says that", () => {
    const note = recipeNote(cli());
    expect(note.ready).toBe(false);
    expect(note.headline).toMatch(/no Jarvis capabilities/i);
    expect(note.limitations).toEqual([]);
  });

  it("a verified method names the method", () => {
    expect(recipeNote(cli({ recipe: { ok: true, method: "mcp_http" } })).headline).toBe(
      "Jarvis capabilities over HTTP",
    );
    expect(recipeNote(cli({ recipe: { ok: true, method: "mcp_stdio" } })).ready).toBe(true);
    expect(recipeMethodWord("mcp_stdio")).toBe("over stdio");
    // A method this build has never heard of prints verbatim rather than being
    // swallowed into silence.
    expect(recipeMethodWord("carrier_pigeon")).toBe("carrier_pigeon");
  });

  it("ok:true with method none is NOT ready", () => {
    const note = recipeNote(cli({ recipe: { ok: true, method: "none" } }));
    expect(note.ready).toBe(false);
    expect(note.headline).toMatch(/no Jarvis capabilities/i);
  });

  it("an unsupported version carries its limitation forward, verbatim", () => {
    const sentence =
      "This Claude Code build could not be told to disable its own web tools, so it may reach the web without Jarvis.";
    const note = recipeNote(cli({ recipe: { ok: false, limitations: [sentence] } }));
    expect(note.ready).toBe(false);
    expect(note.limitations).toEqual([sentence]);
  });

  it("never renders an EMPTY warning when the daemon gave no reason", () => {
    const note = recipeNote(cli({ recipe: { ok: false, limitations: [] } }));
    expect(note.limitations).toHaveLength(1);
    expect(note.limitations[0].trim()).not.toBe("");
  });

  it("keeps a limitation on a recipe that DID work — a caveat is not a failure", () => {
    const note = recipeNote(cli({ recipe: { ok: true, method: "mcp_http", limitations: ["Web search stays on."] } }));
    expect(note.ready).toBe(true);
    expect(note.limitations).toEqual(["Web search stays on."]);
  });
});

describe("LaunchRecipeNote — the rendered row", () => {
  it("shows the limitation sentence in the menu, before anything is typed", () => {
    render(
      <LaunchRecipeNote
        cli={cli({ recipe: { ok: false, limitations: ["Codex 0.1 cannot be pointed at a config."] } })}
      />,
    );
    expect(screen.getByTestId("launch-recipe-claude").textContent).toContain(
      "Codex 0.1 cannot be pointed at a config.",
    );
    expect(screen.getByText(/No Jarvis capabilities on this version/i)).toBeInTheDocument();
  });

  it("says what a working recipe configured", () => {
    render(<LaunchRecipeNote cli={cli({ recipe: { ok: true, method: "mcp_http" } })} />);
    expect(screen.getByText("Jarvis capabilities over HTTP")).toBeInTheDocument();
  });
});

describe("the Launch menu actually renders it", () => {
  const read = (p: string) => readFileSync(p, "utf8").replace(/\r\n/g, "\n");
  const src = read(join(process.cwd(), "components", "terminal", "TerminalPane.tsx"));

  it("mounts LaunchRecipeNote inside the installed-CLI row", () => {
    // The component being right is worth nothing if the menu never calls it —
    // the v1.163.0 trap, where a deleted call site left every test green.
    expect(src).toContain("<LaunchRecipeNote cli={c} />");
  });

  it("shows the detected version beside the CLI it belongs to", () => {
    expect(src).toContain("data-testid={`launch-version-${c.id}`}");
  });
});

describe("the Build page hands the rail the pane's capabilities", () => {
  const read = (p: string) => readFileSync(p, "utf8").replace(/\r\n/g, "\n");

  it("maps `capabilities` onto every RailPane", () => {
    // Without this line every popover renders five blank boxes and every write
    // is made against a map of falses \u2014 and no rail test can see it, because
    // the rail is handed its panes ready-made. Source-pinned in the house idiom
    // (v1.163.0), since the page cannot be mounted under jsdom.
    const src = read(join(process.cwd(), "app", "terminals", "page.tsx"));
    expect(src).toContain("capabilities: t.capabilities ?? null,");
  });
});
