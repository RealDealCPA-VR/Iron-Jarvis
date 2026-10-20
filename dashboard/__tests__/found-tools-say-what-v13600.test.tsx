/**
 * v1.360.0 lane 10C (2), G49: a found server outside the catalog said only
 * "A tool from <app>" — live, all six servers found on the user's PC read that
 * way, so nothing said what any of them was.
 *
 * Now each row says what it IS from what the import already knows (never a
 * capability it does not know): its name in words, then the program it runs
 * on this PC (the daemon's `program`, read off the masked command line) or the
 * host of its web address; the catalog's blurb still wins when the package is
 * known; once the row is added, the tools it loaded take over the line.
 *
 * Driven through the REAL SourcesDoor (the Tools page's and the Directory's
 * door) with only the network a double, plus the pure words, both ways.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => ({
  gets: [] as string[],
  posts: [] as { path: string; body: unknown }[],
  applied: false,
  apply: { added: [], skipped: [] } as Record<string, unknown>,
  servers: [] as Record<string, unknown>[],
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status = 0;
  }
  return {
    ApiError,
    API_BASE: "",
    get: async (path: string) => {
      api.gets.push(path);
      if (path === "/mcp/servers") return { servers: api.applied ? api.servers : [] };
      return {};
    },
    post: async (path: string, body?: unknown) => {
      api.posts.push({ path, body });
      if (path === "/mcp/import/apply") {
        api.applied = true;
        return api.apply;
      }
      return {};
    },
  };
});

import { SourcesDoor } from "@/components/tools/AddPack";
import { AT_ADDRESS, RUNS_HERE, rowAbout, serverLabel, type ImportRow } from "@/lib/addPackWords";

const W = { timeout: 4000 };

function row(name: string, over: Partial<ImportRow> = {}): ImportRow {
  return {
    name,
    transport: "stdio",
    command: "C:\\Tools\\quickbooks-desktop-mcp.exe",
    args: [],
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
  } as ImportRow;
}

const QB = row("quickbooks-desktop", { program: "quickbooks-desktop-mcp" });
const LINEAR = row("linear", { transport: "http", command: "", url: "https://mcp.linear.app/sse", host: "mcp.linear.app" });
const GITHUB = row("github", {
  command: "npx",
  args: ["-y", "@modelcontextprotocol/server-github"],
  program: "server-github",
  blurb: "Repos, issues, and pull requests.",
});

beforeEach(() => {
  api.gets = [];
  api.posts = [];
  api.applied = false;
  api.apply = { added: [], skipped: [] };
  api.servers = [];
});
afterEach(() => cleanup());

describe("the words", () => {
  it("a server's name in words: separators read as spaces, known casing, the mechanism words dropped", () => {
    expect(serverLabel("quickbooks-desktop")).toBe("QuickBooks Desktop");
    expect(serverLabel("brave_search")).toBe("Brave Search");
    expect(serverLabel("mcp-server-git")).toBe("Git");
    expect(serverLabel("MyLedger")).toBe("MyLedger");
    expect(serverLabel("github")).toBe("GitHub");
    // Nothing but mechanism words: kept, never an empty label.
    expect(serverLabel("mcp")).not.toBe("");
  });

  it("a row the catalog does not know says what it is, and nothing more", () => {
    expect(rowAbout(QB, "Claude Desktop")).toBe(`QuickBooks Desktop · ${RUNS_HERE} (quickbooks-desktop-mcp)`);
    expect(rowAbout(LINEAR, "Cursor")).toBe(`Linear · ${AT_ADDRESS} (mcp.linear.app)`);
    // An older daemon: no `host` -> read off the address; no `program` -> no parenthesis.
    expect(rowAbout({ ...LINEAR, host: undefined }, "Cursor")).toBe(`Linear · ${AT_ADDRESS} (mcp.linear.app)`);
    expect(rowAbout({ ...QB, program: undefined }, "Claude Desktop")).toBe(`QuickBooks Desktop · ${RUNS_HERE}`);
    for (const r of [QB, LINEAR]) {
      const line = rowAbout(r, "Claude Desktop");
      expect(line).not.toMatch(/A tool from/);
      // Never a dash aside, never the mechanism.
      expect(line).not.toMatch(/\s[—–]\s|\bMCP\b|stdio|\bpack\b/);
    }
  });

  it("CONTROL: the catalog's blurb still wins for a package it knows", () => {
    expect(rowAbout(GITHUB, "Claude Desktop")).toBe("Repos, issues, and pull requests.");
  });

  it("once added, the tools it loaded take over the line (blurb or not)", () => {
    expect(rowAbout(QB, "Claude Desktop", ["create_invoice", "list_customers", "get_report"])).toBe(
      "QuickBooks Desktop · 3 tools: create invoice, list customers and get report",
    );
    expect(rowAbout(GITHUB, "x", ["search_repositories"])).toBe("GitHub · 1 tool: search repositories");
    // No tools loaded (it did not start): the guess stays, never "0 tools".
    expect(rowAbout(QB, "x", [])).toBe(`QuickBooks Desktop · ${RUNS_HERE} (quickbooks-desktop-mcp)`);
  });
});

function desktop(servers: ImportRow[]) {
  return {
    source: "claude-desktop",
    label: "Claude Desktop",
    origin: "C:\\AppData\\Claude\\claude_desktop_config.json",
    readable: true,
    note: "",
    servers,
  };
}

describe("the real door a new user meets", () => {
  it("every found row says what it is; after Add, the added row's tools take over and the others keep theirs", async () => {
    render(<SourcesDoor onAdded={() => {}} sources={[desktop([QB, LINEAR, GITHUB])] as never} />);
    expect(screen.getByTestId("import-about-quickbooks-desktop").textContent).toBe(
      `QuickBooks Desktop · ${RUNS_HERE} (quickbooks-desktop-mcp)`,
    );
    expect(screen.getByTestId("import-about-linear").textContent).toBe(`Linear · ${AT_ADDRESS} (mcp.linear.app)`);
    expect(screen.getByTestId("import-about-github").textContent).toBe("Repos, issues, and pull requests.");
    // Nothing read before Add: the rows carry their own facts.
    expect(api.gets).toEqual([]);

    api.apply = { added: [{ name: "quickbooks-desktop", tools_loaded: 2, note: null, state: "ok" }], skipped: [] };
    api.servers = [{ name: "quickbooks-desktop", tool_names: ["create_invoice", "list_customers"] }];
    // Only QuickBooks: untick the other two.
    fireEvent.click(screen.getByTestId("import-pick-linear"));
    fireEvent.click(screen.getByTestId("import-pick-github"));
    fireEvent.click(screen.getByTestId("import-source-apply-claude-desktop"));

    await waitFor(
      () =>
        expect(screen.getByTestId("import-about-quickbooks-desktop").textContent).toBe(
          "QuickBooks Desktop · 2 tools: create invoice and list customers",
        ),
      W,
    );
    // The outcome still names them, from the SAME single read.
    expect(within(screen.getByTestId("import-outcome")).getByTestId("import-tools-quickbooks-desktop").textContent).toContain(
      "create invoice and list customers",
    );
    expect(api.gets.filter((g) => g === "/mcp/servers")).toHaveLength(1);
    // Rows that were not added keep what they are.
    expect(screen.getByTestId("import-about-linear").textContent).toBe(`Linear · ${AT_ADDRESS} (mcp.linear.app)`);
    expect(screen.getByTestId("import-about-github").textContent).toBe("Repos, issues, and pull requests.");
  });

  it("CONTROL: a row added but not started keeps its line (its tools are not claimed), beside one that started", async () => {
    render(<SourcesDoor onAdded={() => {}} sources={[desktop([QB, LINEAR])] as never} />);
    api.apply = {
      added: [
        { name: "quickbooks-desktop", tools_loaded: 0, note: "saved, but it did not start: boom", state: "failed" },
        { name: "linear", tools_loaded: 1, note: null, state: "ok" },
      ],
      skipped: [],
    };
    // Even if the list still names tools for it, a pack that did not start
    // never wears them.
    api.servers = [
      { name: "quickbooks-desktop", tool_names: ["create_invoice"] },
      { name: "linear", tool_names: ["list_issues"] },
    ];
    fireEvent.click(screen.getByTestId("import-source-apply-claude-desktop"));
    await waitFor(() => expect(screen.getByTestId("import-about-linear").textContent).toBe("Linear · 1 tool: list issues"), W);
    expect(screen.getByTestId("import-about-quickbooks-desktop").textContent).toBe(
      `QuickBooks Desktop · ${RUNS_HERE} (quickbooks-desktop-mcp)`,
    );
  });
});
