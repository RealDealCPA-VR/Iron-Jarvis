/**
 * Wave C (readiness plan 0h.5, item 30): the Tools page's import door lists
 * OpenCode, Grok, Antigravity and Pi by the TOOL's name — the row's own
 * `label` as the daemon sends it (the dashboard hard-codes no source keys),
 * a found server under the tool's heading, a missing file as "<Tool>: not
 * found", and the discovery line names the tool. The daemon half is
 * tests/test_wave_c_import_more_tools.py.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  API_BASE: "",
  get: async () => ({}),
  post: async () => ({ added: [], skipped: [] }),
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children?: React.ReactNode; href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { SourcesDoor } from "@/components/tools/AddPack";
import { appName, discover, discoveryLine } from "@/lib/importDiscovery";
import type { ImportSource, PreviewRow } from "@/lib/mcpImport";

const server = (name: string, source: string, over: Partial<PreviewRow> = {}): PreviewRow =>
  ({
    name, source, transport: "stdio", command: "npx", args: ["-y", name], env_keys: [], secret_keys: [],
    header_names: [], auth_set: false, exists: false, exists_as: "", disabled: false, runtime_ready: true,
    blurb: "", ...over,
  }) as PreviewRow;

const SOURCES: ImportSource[] = [
  { source: "opencode", label: "OpenCode", origin: "C:\\u\\.config\\opencode\\opencode.jsonc", readable: true, note: "",
    servers: [server("playwright", "opencode")] },
  { source: "grok", label: "Grok", origin: "C:\\u\\.grok\\config.toml", readable: true, note: "",
    servers: [server("linear", "grok", { disabled: true })] },
  { source: "antigravity", label: "Antigravity", origin: "C:\\u\\.gemini\\config\\mcp_config.json", readable: false, note: "", servers: [] },
  { source: "pi", label: "Pi", origin: "C:\\u\\.pi\\agent\\mcp.json", readable: false, note: "mcp.json could not be read as a config file", servers: [] },
  { source: "pi-project", label: "Pi (this project)", origin: "C:\\repo\\.pi\\mcp.json", readable: true, note: "",
    servers: [server("proj-pi", "pi")] },
];

afterEach(() => cleanup());

describe("the import door and the coding tools (Wave C, item 30)", () => {
  it("lists each tool by its name, found servers under the tool's heading", async () => {
    render(<SourcesDoor sources={SOURCES} onAdded={() => {}} rowsHere={false} />);
    const oc = await screen.findByTestId("import-source-opencode");
    expect(oc.textContent).toContain("OpenCode");
    expect(within(oc).getByTestId("import-row-playwright")).toBeInTheDocument();
    const grok = screen.getByTestId("import-source-grok");
    expect(grok.textContent).toContain("Grok");
    expect(within(grok).getByTestId("import-row-linear")).toBeInTheDocument();
    // A server its tool switched off is shown, unticked, saying so.
    expect((within(grok).getByTestId("import-pick-linear") as HTMLInputElement).checked).toBe(false);
    expect(within(grok).getByTestId("import-off-linear").textContent).toMatch(/turned off in Grok/i);
    const pp = screen.getByTestId("import-source-pi-project");
    expect(pp.textContent).toContain("Pi (this project)");
  });

  it("a missing file is '<Tool>: not found' and a broken one names the file, each by the tool's name", async () => {
    render(<SourcesDoor sources={SOURCES} onAdded={() => {}} rowsHere={false} />);
    const folded = await screen.findByTestId("import-looked-in");
    expect(within(folded).getByTestId("import-source-antigravity").textContent).toContain("Antigravity: not found");
    const pi = screen.getByTestId("import-source-pi");
    expect(pi.textContent).toContain("Pi: mcp.json could not be read as a config file");
    expect(folded.contains(pi)).toBe(false);
  });

  it("the discovery line and the app names come from the rows' own labels", () => {
    expect(appName({ source: "opencode", label: "OpenCode" })).toBe("OpenCode");
    expect(appName({ source: "pi-project", label: "Pi (this project)" })).toBe("Pi");
    expect(appName({ source: "grok", label: "Grok" })).toBe("Grok");
    expect(appName({ source: "antigravity", label: "Antigravity" })).toBe("Antigravity");
    const d = discover(SOURCES);
    expect(d.checkedApps).toEqual(["OpenCode", "Grok", "Antigravity", "Pi"]);
    expect(discoveryLine(d)).toBe("Found 3 tools in OpenCode, Grok and Pi.");
  });
});
