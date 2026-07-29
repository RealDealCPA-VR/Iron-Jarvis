import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * One name per concept (v1.113.0).
 *
 * Before this pass a non-technical user met seven words for roughly three
 * ideas — and the SAME OBJECT changed name as they walked the app: a Notion
 * memory was a "base" where they created it, a "source" in the filter three
 * inches away, a "brain" in directory copy, and a "connector" in chat's "+"
 * menu. Nobody hit an error; they hit hesitation, which is worse, because
 * hesitation never files a bug report.
 *
 * The canon: MEMORY BASE (anything recall reads) · CONNECTION (anything the
 * app talks to) · PLUG-IN (an MCP server — freeing "Pack" for the future
 * staff-export bundle) · SKILL (anything it knows how to do) · NOTIFICATIONS
 * (where alerts go). The wire is untouched (/ltm/sources, ChatBody.connectors,
 * /mcp/servers, /channels) — this is what users READ, not what code calls
 * things. Old words live on as search aliases in lib/nav.ts so muscle memory
 * keeps working.
 *
 * These pins scan SOURCE FILES for the user-visible strings, the same
 * technique nav.test.ts uses: crude, but it catches the real failure mode —
 * a future feature reintroducing a retired word because nothing said no.
 */

const DASH = join(__dirname, "..");
const read = (p: string) => readFileSync(join(DASH, p), "utf-8");

describe("memory speaks 'base', never 'source' or 'brain'", () => {
  it("the long-term filter labels say Base", () => {
    const s = read("components/memory/LongTerm.tsx");
    // The v1.110.0 card said "base" while the filter three inches away said
    // "Source" — the exact adjacency this pass exists to kill.
    expect(s).not.toMatch(/^\s*Source$/m);
    expect(s).toMatch(/^\s*Base$/m);
    expect(s).not.toContain('aria-label="Source"');
  });
  it("copy stops calling the built-in base a 'brain'", () => {
    const s = read("components/memory/LongTerm.tsx");
    expect(s).not.toContain("built-in local brain");
    expect(s).not.toContain("Iron Jarvis&apos;s brain");
    // The identifier `brain` (the builtin source's API name) is allowed —
    // renaming an identity would break saved project bindings. Only prose is
    // policed here.
  });
  it("the surface blurb matches", () => {
    expect(read("components/memory/MemorySurface.tsx")).not.toContain("markdown brain");
  });
});

describe("chat speaks 'connections', never 'connectors' or 'integrations'", () => {
  it("the + flyout is Connections", () => {
    const s = read("app/chat/page.tsx");
    expect(s).not.toMatch(/^\s*Connectors$/m);
    expect(s).toMatch(/^\s*Connections$/m);
    expect(s).not.toContain("Turn off connector ");
  });
  it("the tool-category label retired 'Integrations'", () => {
    const s = read("app/chat/page.tsx");
    expect(s).not.toContain('"Integrations (MCP)"');
    expect(s).toContain('"Plug-ins (MCP)"');
  });
});

describe("MCP servers are 'plug-ins' — 'Pack' is reserved for the staff bundle", () => {
  it("the tools page presents plug-ins", () => {
    const s = read("app/tools/page.tsx");
    expect(s).toContain('title="Plug-ins (MCP)"');
    expect(s).not.toContain("Tool pack ");
    expect(s).not.toContain('`Tool pack "');
    expect(s).not.toContain("connected pack");
  });
  it("the palette deep link agrees", () => {
    expect(read("components/CommandPalette.tsx")).toContain('"Tools → Plug-ins"');
  });
  it("the Connections directory tile agrees", () => {
    // The directory the user explicitly asked to keep (v1.101.0) — its words
    // teach the taxonomy harder than any other card.
    const s = read("app/connections/page.tsx");
    expect(s).toContain('title: "Plug-ins (MCP)"');
    expect(s).not.toContain("Tool packs");
  });
});

describe("alerts live under 'Notifications'", () => {
  it("the page renamed", () => {
    const s = read("app/channels/page.tsx");
    expect(s).toContain('title="Notifications"');
    expect(s).not.toContain('title="Channels"');
    expect(s).not.toContain("Add a channel");
  });
  it("but Slack's own 'channel' concept is still allowed to be called one", () => {
    // "a bot token + a channel" is SLACK's channel — their word, correct
    // usage. The ban is on OUR concept wearing that name.
    expect(read("app/channels/page.tsx")).toContain("a bot token + a channel");
  });
  it("the nav label follows, and the old word stays searchable", () => {
    const s = read("lib/nav.ts");
    expect(s).toContain('label: "Notifications"');
    expect(s).toContain('"channels"'); // alias — muscle memory keeps working
  });
});

describe("the marketplace decision is finally reflected", () => {
  it("the page is a Directory (consume, don't compete — decided 2026-07-25)", () => {
    const s = read("app/marketplace/page.tsx");
    expect(s).toContain('title="Directory"');
    expect(s).not.toContain('title="Marketplace"');
  });
  it("chat's teaser tooltip follows", () => {
    expect(read("app/chat/page.tsx")).not.toContain("in the Marketplace");
  });
});

describe("the wire is untouched", () => {
  it("renames stayed in layer 1 — API paths and fields keep their names", () => {
    // Spot-checks that the pass never crossed into the contract layer: these
    // exact strings must still exist because the daemon still speaks them.
    expect(read("components/memory/LongTerm.tsx")).toContain("/ltm/sources");
    expect(read("app/chat/page.tsx")).toContain("connectors");
    expect(read("app/tools/page.tsx")).toContain("/mcp/servers");
    expect(read("app/channels/page.tsx")).toContain("/comm/channels");
  });
});

describe("the README stays true (v1.117.0)", () => {
  // The front door of the repo makes CLAIMS; these pins keep the ones that
  // went stale once from going stale silently again.
  const readme = () => readFileSync(join(DASH, "..", "README.md"), "utf-8");
  it("never sells the mode picker that was deleted in v1.108.0", () => {
    expect(readme()).not.toMatch(/Two modes|Agent mode/);
  });
  it("never promises the marketplace that was decided against", () => {
    expect(readme()).not.toMatch(/marketplace/i);
  });
  it("its screenshots are the current-chrome captures", () => {
    const s = readme();
    expect(s).toContain("readme-chat.png");
    expect(s).toContain("readme-search.png");
    expect(s).toContain("readme-memory-graph.png");
    expect(s).not.toMatch(/overview-v2\.png|feat-workflows-n8n\.png|kanban\.png/);
  });
  it("speaks the canon: bases, not custom sources", () => {
    expect(readme()).not.toContain("Add a custom source");
    expect(readme()).toContain("Add a memory base");
  });
});
