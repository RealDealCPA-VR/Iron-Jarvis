/**
 * v1.354.0 (product-readiness Wave 4, lane 4C-imports): the Tools page's add
 * area explains itself (G49) and respects what the other apps switched off
 * (G50). Driven through the REAL Tools page (AddPack is mounted there) and
 * the real exported SourcesDoor the Directory uses; only the daemon seams
 * are mocked.
 *
 *  - ACCEPTANCE G49: the area opens on "Bring your tools from Claude, Codex,
 *    Cursor…"; each found tool has a one-line description (the catalog's
 *    blurb, else "A tool from <app>"), the command and key names folded
 *    under Details; after an import, the tool names (GET /mcp/servers read
 *    once) and "Use it in a chat: + › Connections";
 *  - "Open its row below to retry." is a BUTTON that scrolls to the pack's
 *    row, opens its last output and focuses its Retry (a link to the Tools
 *    page from the Directory); "Install Node.js" when the reason names it;
 *  - the HTTP door reads the transport off the address, the type, headers
 *    and token under More options;
 *  - ACCEPTANCE G50: a server switched off in Claude Desktop is listed
 *    unticked as "turned off in Claude Desktop"; one connected another way
 *    reads "already connected" / "already a memory source";
 *  - the empty places fold into one "Also looked in" line;
 *  - the pure helpers and the copy guards.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { asides, readSrc } from "./helpers/dashGuard";
import { uncalm } from "./helpers/calmVariant";

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
    serversAfterApply: null as Record<string, unknown>[] | null,
    applied: false,
    sources: [] as Record<string, unknown>[],
    apply: { added: [], skipped: [] } as Record<string, unknown>,
    preview: { servers: [] as Record<string, unknown>[], unrecognised: false } as Record<string, unknown>,
    addResult: { name: "", added: true, tools_loaded: 1, auto_approve: false, note: null } as Record<string, unknown>,
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
    if (path === "/mcp/servers") {
      return { servers: api.applied && api.serversAfterApply ? api.serversAfterApply : api.servers };
    }
    if (path === "/mcp/catalog") return { catalog: [] };
    if (path === "/tools/custom") return { tools: [] };
    if (path === "/mcp/import/sources") return { sources: api.sources };
    return {};
  },
  post: async (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    if (path === "/mcp/import/preview") return api.preview;
    if (path === "/mcp/import/apply") {
      api.applied = true;
      return api.apply;
    }
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
const { SourcesDoor } = await import("@/components/tools/AddPack");
const words = await import("@/lib/addPackWords");
const { addedButNotStarted } = await import("@/lib/packStartWords");

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
    source: "claude-desktop",
    origin: "C:\\AppData\\Claude\\claude_desktop_config.json",
    disabled: false,
    exists_as: "",
    blurb: "",
    ...over,
  };
}

const GITHUB = row("github", {
  args: ["-y", "@modelcontextprotocol/server-github"],
  env_keys: ["GITHUB_HOST", "GITHUB_PERSONAL_ACCESS_TOKEN"],
  secret_keys: ["GITHUB_PERSONAL_ACCESS_TOKEN"],
  blurb: "Repos, issues, and pull requests.",
});
const LEDGER = row("ledger-tool", { args: ["-y", "@acme/ledger-mcp"] });
const PARKED = row("parked", { disabled: true });
const DRIVE = row("google-drive", { exists: true, exists_as: "app" });
const BRAIN = row("basic-memory", { exists: true, exists_as: "memory" });

function desktop(servers: Record<string, unknown>[]) {
  return {
    source: "claude-desktop",
    label: "Claude Desktop",
    origin: "C:\\AppData\\Claude\\claude_desktop_config.json",
    readable: true,
    note: "",
    servers,
  };
}

const posted = (path: string) => api.posts.filter((p) => p.path === path);

beforeEach(() => {
  api.gets = [];
  api.posts = [];
  api.servers = [];
  api.serversAfterApply = null;
  api.applied = false;
  api.sources = [];
  api.apply = { added: [], skipped: [] };
  api.preview = { servers: [], unrecognised: false };
  api.addResult = { name: "", added: true, tools_loaded: 1, auto_approve: false, note: null };
});
afterEach(() => cleanup());

async function openTools() {
  render(<ToolsPage />);
  return await screen.findByTestId("add-pack");
}

/* ===================================================================== */
/*  G49: the area explains itself                                          */
/* ===================================================================== */

describe("G49: Bring your tools from Claude, Codex, Cursor…", () => {
  it("ACCEPTANCE: the area opens on the sources door, and each found tool says in one line what it does, its command folded under Details", async () => {
    api.sources = [desktop([GITHUB, LEDGER])];
    const area = await openTools();
    const door = within(area).getByTestId("add-pack-door-sources");
    expect(door.getAttribute("aria-selected")).toBe("true");
    expect(door.textContent).toBe("Bring your tools from Claude, Codex, Cursor…");
    // The first door in the strip.
    expect(within(area).getAllByRole("tab")[0]).toBe(door);

    const gh = await screen.findByTestId("import-row-github");
    expect(api.gets.filter((g) => g === "/mcp/import/sources")).toHaveLength(1);
    // The catalog's line when the daemon matched the package...
    expect(within(gh).getByTestId("import-about-github").textContent).toBe("Repos, issues, and pull requests.");
    // ...else where it came from.
    expect(screen.getByTestId("import-about-ledger-tool").textContent).toBe("A tool from Claude Desktop");
    // The launch command and the key names are folded, not on the row.
    const details = within(gh).getByTestId("import-details-github") as HTMLDetailsElement;
    expect(details.tagName).toBe("DETAILS");
    expect(details.open).toBe(false);
    expect(details.textContent).toContain("npx -y @modelcontextprotocol/server-github");
    expect(details.textContent).toContain("Uses GITHUB_HOST");
    expect(within(details).getByTestId("import-launcher-github")).toBeTruthy();
    expect(within(details).getByTestId("override-github-GITHUB_PERSONAL_ACCESS_TOKEN")).toBeTruthy();
    // Nothing of the command sits outside the fold.
    const outside = gh.cloneNode(true) as HTMLElement;
    outside.querySelector("details")?.remove();
    expect(outside.textContent).not.toContain("@modelcontextprotocol/server-github");
    expect(outside.textContent).not.toContain("GITHUB_HOST");
  });

  it("ACCEPTANCE: after an import the outcome names the tools (GET /mcp/servers read once) and says how to use them in a chat", async () => {
    api.sources = [desktop([GITHUB])];
    api.apply = { added: [{ name: "github", tools_loaded: 3, note: null, state: "ok" }], skipped: [] };
    api.serversAfterApply = [
      {
        name: "github",
        command: "npx",
        args: ["-y", "@modelcontextprotocol/server-github"],
        tools_loaded: 3,
        tool_names: ["search_repositories", "get_issue", "list_pull_requests"],
        state: "ok",
      },
    ];
    await openTools();
    const group = await screen.findByTestId("import-source-claude-desktop");
    const serversBefore = api.gets.filter((g) => g === "/mcp/servers").length;
    fireEvent.click(within(group).getByTestId("import-source-apply-claude-desktop"));

    const tools = await within(group).findByTestId("import-tools-github");
    await waitFor(() =>
      expect(tools.textContent).toBe("3 tools ready: search repositories, get issue and list pull requests"),
    );
    expect(within(group).getByTestId("import-use-in-chat").textContent).toBe("Use it in a chat: Tools › Connections");
    // Read once for the names (the page re-reads its own list too).
    expect(api.gets.filter((g) => g === "/mcp/servers").length).toBeGreaterThan(serversBefore);
  });

  it("a pack that did not start: no chat line, 'Open its row below to retry.' is a button to its row (scroll, open the last output, focus Retry), and Install Node.js when the reason names it", async () => {
    const scrolled: Element[] = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) {
      scrolled.push(this);
    };
    try {
      const note = "saved, but it did not start: npx isn't installed, or isn't on the PATH this app can see — see its row for the fix";
      api.servers = [
        {
          name: "slack",
          command: "npx",
          args: ["-y", "@modelcontextprotocol/server-slack"],
          tools_loaded: 0,
          tool_names: [],
          state: "failed",
          last_error: "FileNotFoundError: npx",
          reason: "npx isn't installed",
          stderr_tail: "npm ERR! not found",
        },
      ];
      api.sources = [desktop([row("slack", { args: ["-y", "@modelcontextprotocol/server-slack"] })])];
      api.apply = { added: [{ name: "slack", tools_loaded: 0, note, state: "failed" }], skipped: [] };
      await openTools();
      const group = await screen.findByTestId("import-source-claude-desktop");
      fireEvent.click(within(group).getByTestId("import-source-apply-claude-desktop"));
      const result = await within(group).findByTestId("import-result-slack");
      expect(result.getAttribute("data-outcome")).toBe("not-started");
      // The not-started sentence still reads whole (lib/packStartWords).
      expect(result.textContent).toContain(addedButNotStarted(note));
      expect(within(group).queryByTestId("import-use-in-chat")).toBeNull();
      // Install Node.js beside it.
      const node = within(result).getByTestId("import-install-node-slack") as HTMLAnchorElement;
      expect(node.textContent).toBe("Install Node.js");
      expect(node.getAttribute("href")).toBe("https://nodejs.org/en/download");
      // The tail is a BUTTON that goes to the row.
      const jump = within(result).getByTestId("import-open-row-slack");
      expect(jump.tagName).toBe("BUTTON");
      expect(jump.textContent).toBe("Open its row below to retry.");
      const retry = await screen.findByTestId("mcp-retry-slack");
      const output = screen.getByTestId("mcp-stderr-slack") as HTMLDetailsElement;
      expect(output.open).toBe(false);
      fireEvent.click(jump);
      // v1.354.0 integrate (on purpose): the Tools page now marks its rows
      // with data-pack-row, so the jump scrolls to the WHOLE row (which holds
      // the Retry) and still puts the focus on Retry.
      const packRow = retry.closest('[data-pack-row="slack"]') as HTMLElement;
      expect(packRow).not.toBeNull();
      expect(scrolled).toContain(packRow);
      expect(document.activeElement).toBe(retry);
      expect(output.open).toBe(true);
      expect(within(group).queryByTestId("import-row-missing-slack")).toBeNull();
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });

  it("the jump says where the row is when the page does not show it yet", async () => {
    api.sources = [desktop([row("ghost")])];
    api.apply = { added: [{ name: "ghost", tools_loaded: 0, note: "it did not answer", state: "failed" }], skipped: [] };
    await openTools();
    const group = await screen.findByTestId("import-source-claude-desktop");
    fireEvent.click(within(group).getByTestId("import-source-apply-claude-desktop"));
    fireEvent.click(await within(group).findByTestId("import-open-row-ghost"));
    expect((await within(group).findByTestId("import-row-missing-ghost")).textContent).toBe(
      "Its row is under Connected extensions below.",
    );
  });

  it("from the Directory (no pack rows on the page) the tail is a link to the Tools page's packs", async () => {
    const sources = [desktop([row("slack")])] as never;
    api.apply = { added: [{ name: "slack", tools_loaded: 0, note: "it did not answer", state: "failed" }], skipped: [] };
    render(<SourcesDoor sources={sources} onAdded={() => {}} />);
    const group = await screen.findByTestId("import-source-claude-desktop");
    fireEvent.click(within(group).getByTestId("import-source-apply-claude-desktop"));
    const jump = await within(group).findByTestId("import-open-row-slack");
    expect(jump.tagName).toBe("A");
    expect(jump.getAttribute("href")).toBe("/tools?focus=packs");
    expect(jump.textContent).toBe("Open its row in Tools to retry.");
  });

  it("the paste box is the advanced door, and there the Details are open (the user typed the config)", async () => {
    api.preview = { servers: [row("pasted", { source: "paste", origin: "paste" })], unrecognised: false };
    const area = await openTools();
    const pasteDoor = within(area).getByTestId("add-pack-door-paste");
    expect(pasteDoor.textContent).toBe("Paste from a website or README (advanced)");
    fireEvent.click(pasteDoor);
    const box = await within(area).findByTestId("add-pack-paste");
    fireEvent.change(box, { target: { value: "npx -y @example/pasted" } });
    fireEvent.blur(box);
    const pasted = await screen.findByTestId("import-row-pasted");
    expect((within(pasted).getByTestId("import-details-pasted") as HTMLDetailsElement).open).toBe(true);
    expect(within(pasted).getByTestId("import-about-pasted").textContent).toBe("A tool from the pasted text");
  });
});

/* ===================================================================== */
/*  G49: the HTTP door reads the address                                   */
/* ===================================================================== */

describe("G49: the HTTP door", () => {
  async function openHttp() {
    const area = await openTools();
    fireEvent.click(within(area).getByTestId("add-pack-door-http"));
    return await screen.findByTestId("add-pack-http-door");
  }

  it("an address ending in /sse is sent as SSE, anything else as HTTP, with the type, headers and token folded under More options", async () => {
    const door = await openHttp();
    const more = within(door).getByTestId("add-pack-http-more") as HTMLDetailsElement;
    expect(more.tagName).toBe("DETAILS");
    expect(more.open).toBe(false);
    expect(more.textContent).toContain("More options");
    for (const id of ["add-pack-http-transport", "add-pack-header-add", "add-pack-http-token"]) {
      expect(within(more).getByTestId(id)).toBeTruthy();
    }
    expect((within(door).getByTestId("add-pack-http-transport") as HTMLSelectElement).value).toBe("auto");
    fireEvent.change(within(door).getByTestId("add-pack-url"), { target: { value: "https://events.example.com/v1/sse/" } });
    fireEvent.click(within(door).getByTestId("add-pack-http-add"));
    await waitFor(() => expect(posted("/mcp/servers")).toHaveLength(1));
    expect((posted("/mcp/servers")[0].body as Record<string, unknown>).transport).toBe("sse");
    // The success note says where to switch it on.
    expect((await within(door).findByTestId("add-pack-http-ok")).textContent).toContain(
      "Use it in a chat: Tools › Connections",
    );

    fireEvent.change(within(door).getByTestId("add-pack-url"), { target: { value: "https://api.example.com/mcp" } });
    fireEvent.click(within(door).getByTestId("add-pack-http-add"));
    await waitFor(() => expect(posted("/mcp/servers")).toHaveLength(2));
    expect((posted("/mcp/servers")[1].body as Record<string, unknown>).transport).toBe("http");
  });

  it("a type picked under More options wins over the address", async () => {
    const door = await openHttp();
    fireEvent.change(within(door).getByTestId("add-pack-url"), { target: { value: "https://x.example.com/sse" } });
    fireEvent.change(within(door).getByTestId("add-pack-http-transport"), { target: { value: "http" } });
    fireEvent.click(within(door).getByTestId("add-pack-http-add"));
    await waitFor(() => expect(posted("/mcp/servers")).toHaveLength(1));
    expect((posted("/mcp/servers")[0].body as Record<string, unknown>).transport).toBe("http");
  });
});

/* ===================================================================== */
/*  G50: off and already here                                              */
/* ===================================================================== */

describe("G50: respects off, knows what is already connected", () => {
  it("ACCEPTANCE: a server switched off in Claude Desktop is listed unticked as 'turned off in Claude Desktop'; ticking it is the choice", async () => {
    api.sources = [desktop([LEDGER, PARKED])];
    api.apply = { added: [{ name: "ledger-tool", tools_loaded: 1, note: null, state: "ok" }], skipped: [] };
    await openTools();
    const parked = await screen.findByTestId("import-row-parked");
    expect(within(parked).getByTestId("import-off-parked").textContent).toBe("turned off in Claude Desktop");
    expect((within(parked).getByTestId("import-pick-parked") as HTMLInputElement).checked).toBe(false);
    expect((screen.getByTestId("import-pick-ledger-tool") as HTMLInputElement).checked).toBe(true);
    const apply = screen.getByTestId("import-source-apply-claude-desktop");
    expect(apply.textContent).toContain("Add selected (1)");
    // Ticking it adds it: the press is the choice.
    fireEvent.click(within(parked).getByTestId("import-pick-parked"));
    expect(apply.textContent).toContain("Add selected (2)");
    fireEvent.click(apply);
    await waitFor(() => expect(posted("/mcp/import/apply")).toHaveLength(1));
    expect((posted("/mcp/import/apply")[0].body as { names: string[] }).names).toEqual(["ledger-tool", "parked"]);
  });

  it("a tool connected another way reads 'already connected' / 'already a memory base' and starts unticked", async () => {
    api.sources = [desktop([DRIVE, BRAIN, LEDGER])];
    await openTools();
    const drive = await screen.findByTestId("import-row-google-drive");
    expect(within(drive).getByTestId("import-exists-google-drive").textContent).toBe("already connected");
    expect((within(drive).getByTestId("import-pick-google-drive") as HTMLInputElement).checked).toBe(false);
    const brain = screen.getByTestId("import-row-basic-memory");
    expect(within(brain).getByTestId("import-exists-basic-memory").textContent).toBe("already a memory base");
    expect((within(brain).getByTestId("import-pick-basic-memory") as HTMLInputElement).checked).toBe(false);
  });

  it("the places with nothing to bring fold into one line naming them; a file that could not be read stays in sight", async () => {
    api.sources = [
      desktop([LEDGER]),
      { source: "windsurf", label: "Windsurf", origin: "C:\\u\\.codeium\\windsurf\\mcp_config.json", readable: false, note: "", servers: [] },
      { source: "vscode-mcp", label: "VS Code", origin: "C:\\a\\Code\\User\\mcp.json", readable: true, note: "", servers: [] },
      { source: "gemini-cli", label: "Gemini CLI", origin: "C:\\u\\.gemini\\settings.json", readable: false, note: "", servers: [] },
      { source: "codex", label: "Codex", origin: "C:\\u\\.codex\\config.toml", readable: false, note: "config.toml could not be read", servers: [] },
    ];
    await openTools();
    const folded = await screen.findByTestId("import-looked-in");
    expect(folded.tagName).toBe("DETAILS");
    expect((folded as HTMLDetailsElement).open).toBe(false);
    expect(folded.querySelector("summary")?.textContent).toBe(
      "Also looked in Windsurf, VS Code and Gemini CLI: nothing to bring",
    );
    expect(within(folded).getByTestId("import-source-windsurf").textContent).toContain("Windsurf: not found");
    expect(within(folded).getByTestId("import-source-vscode-mcp").textContent).toContain("no tools in this file");
    // The broken file names what went wrong, outside the fold.
    const codex = screen.getByTestId("import-source-codex");
    expect(folded.contains(codex)).toBe(false);
    expect(codex.textContent).toContain("Codex: config.toml could not be read");
  });
});

/* ===================================================================== */
/*  the pure helpers                                                       */
/* ===================================================================== */

describe("lib/addPackWords", () => {
  it("transportFromUrl: an address ending in /sse is SSE (slashes and query aside), anything else HTTP", () => {
    expect(words.transportFromUrl("https://h.example/sse")).toBe("sse");
    expect(words.transportFromUrl("https://h.example/SSE/")).toBe("sse");
    expect(words.transportFromUrl("https://h.example/sse?key=1")).toBe("sse");
    expect(words.transportFromUrl("https://h.example/mcp")).toBe("http");
    expect(words.transportFromUrl("https://h.example/ssex")).toBe("http");
    expect(words.transportFromUrl("not a url/sse")).toBe("sse");
    expect(words.transportFromUrl("")).toBe("http");
  });

  it("toolNamesLine says names as words and counts the rest", () => {
    expect(words.toolNamesLine(["search_repositories"])).toBe("search repositories");
    expect(words.toolNamesLine(["a_b", "c-d"])).toBe("a b and c d");
    expect(words.toolNamesLine(["a", "b", "c", "d", "e", "f", "g", "h"])).toBe("a, b, c, d, e, f and 2 more");
    expect(words.toolNamesLine([])).toBe("");
    expect(words.toolNamesLine(null)).toBe("");
  });

  it("splitRowTail keeps the not-started sentence whole and hands the tail to the button", () => {
    const sentence = addedButNotStarted("saved, but it did not start: npx isn't installed — see its row for the fix");
    const { head, tail } = words.splitRowTail(sentence);
    expect(tail).toBe("Open its row below to retry.");
    expect(`${head} ${tail}`).toBe(sentence);
    expect(words.splitRowTail("Something else.")).toEqual({ head: "Something else.", tail: "" });
  });

  it("mentionsNode, rowAbout, existsWords, offWords, startsTicked", () => {
    expect(words.mentionsNode("needs npx (Node.js)")).toBe(true);
    expect(words.mentionsNode("npm ERR! 404")).toBe(true);
    expect(words.mentionsNode("uvx is not installed")).toBe(false);
    expect(words.mentionsNode(null)).toBe(false);
    expect(words.rowAbout({ ...LEDGER, blurb: "  " } as never, "Cursor")).toBe("A tool from Cursor");
    expect(words.rowAbout({ ...LEDGER, blurb: null } as never, "")).toBe("A tool from another app");
    expect(words.existsWords({ ...LEDGER, exists: true } as never)).toBe("already added");
    expect(words.offWords("Claude Desktop")).toBe("turned off in Claude Desktop");
    expect(words.startsTicked(LEDGER as never)).toBe(true);
    expect(words.startsTicked(PARKED as never)).toBe(false);
    expect(words.startsTicked(DRIVE as never)).toBe(false);
    // An older daemon sends no `disabled`: nothing is claimed.
    const { disabled: _d, ...older } = LEDGER;
    expect(words.startsTicked(older as never)).toBe(true);
  });
});

/* ===================================================================== */
/*  copy guards                                                            */
/* ===================================================================== */

const FILES = ["components/tools/AddPack.tsx", "lib/addPackWords.ts"];

describe("copy guards", () => {
  it("no dash asides, whole-pixel sizes, no literal hues", () => {
    for (const f of FILES) {
      expect(asides(f), f).toEqual([]);
      const src = readSrc(f);
      expect(src.match(/\btext-\[\d+\.5px\]/g) ?? [], f).toEqual([]);
      expect(src.match(/-\[#[0-9a-fA-F]+\]|\brgba?\(\s*\d/g) ?? [], f).toEqual([]);
      expect(
        src.match(/\b(?:text|bg|border|ring)-(?:amber|emerald|rose|red|green|blue|violet|sky|cyan|orange|yellow|lime|teal|indigo|purple|pink|fuchsia)-\d{3}\b/g) ?? [],
        f,
      ).toEqual([]);
    }
  });

  it("every Badge is calm, and the folds' chevron stops under reduced motion", () => {
    expect(uncalm("components/tools/AddPack.tsx")).toEqual([]);
    const src = readSrc("components/tools/AddPack.tsx");
    expect(src).toContain("motion-reduce:transition-none");
    expect(readSrc("lib/addPackWords.ts")).toContain("prefers-reduced-motion");
  });

  it("the paste box and the import words keep no engine room on the first door", () => {
    const src = readSrc("components/tools/AddPack.tsx");
    expect(src).not.toContain('"Paste a config"');
    expect(src).not.toContain("Bearer token (optional)");
    expect(src).not.toContain(">Streamable HTTP<");
  });
});
