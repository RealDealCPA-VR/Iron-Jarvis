import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { RETIRED, readableCopy, retiredHits, scannedFiles } from "./helpers/retiredWords";

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
    // …and moved on again in v1.216.0: plug-in → extension. The retired word
    // this test was written to bury stays buried either way.
    expect(s).toContain('"Extensions (MCP)"');
  });
});

describe("MCP servers are 'plug-ins' — 'Pack' is reserved for the staff bundle", () => {
  // RENAMED plug-in → extension (v1.216.0). From a UX review of the Tools
  // page: "'Plug-ins (MCP)' is insider jargon on a first-run screen." The word
  // was picked to free up "pack" and did that job, but it never told a
  // first-time reader what the thing IS. "Extension" is what browsers, editors
  // and IDEs already call a separate program that adds abilities, so it lands
  // pre-understood; MCP stays in a parenthetical because it is the wire, not
  // the noun. See VOCABULARY.md for the canon entry.
  it("the tools page presents extensions", () => {
    const s = read("components/settings/pages/ToolsPage.tsx");
    expect(s).toContain("title={`Extensions${");
    expect(s).not.toContain('title="Plug-ins (MCP)"');
    expect(s).not.toContain("Tool pack ");
    expect(s).not.toContain('`Tool pack "');
    expect(s).not.toContain("connected pack");
  });
  it("the palette deep link agrees", () => {
    const s = read("components/CommandPalette.tsx");
    expect(s).toContain('"Tools → Extensions"');
    // The retired word stays as a SEARCH ALIAS — someone who learned the old
    // name must still find the page (the canon's own rule).
    expect(s).toContain('"connect a plug-in"');
  });
  it("the Connections directory tile agrees", () => {
    // The directory the user explicitly asked to keep (v1.101.0) — its words
    // teach the taxonomy harder than any other card.
    // v1.354.0 (G48, product readiness): the tile and the Add connection
    // menu say what the door DOES, "Add a tool", never the protocol's name
    // ("Extensions (MCP)" was an engine word on a first-run screen). The
    // Tools page it opens still calls them extensions.
    const s = read("components/settings/pages/ConnectionsPage.tsx");
    expect(s).toContain('title: "Add a tool"');
    expect(s).not.toContain("Extensions (MCP)");
    expect(s).toContain("Ready-made extensions that give Jarvis new abilities.");
    expect(s).not.toContain("Tool packs");
  });
  it("no user-visible surface still says plug-in", () => {
    // A half-done rename is worse than either name: the app would say two
    // words for one thing, which is the exact failure this file exists to
    // prevent. Comments and aliases are allowed; rendered strings are not.
    for (const f of [
      "components/settings/pages/ToolsPage.tsx",
      "components/settings/pages/ConnectionsPage.tsx",
      "app/chat/page.tsx",
      "lib/nav.ts",
      // v1.314.0 (UX wave 2): the automation pages are scanned too.
      "app/templates/page.tsx",
      "app/reflex/page.tsx",
      "app/autonomy/page.tsx",
      "components/settings/pages/NotificationsPage.tsx",
      // v1.316.0 (UX wave 4): the rest of the automation pages.
      "app/webhooks/page.tsx",
      "app/sentinels/page.tsx",
      "app/schedules/page.tsx",
    ]) {
      const code = read(f)
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/^\s*\/\/.*$/gm, "");
      expect(code).not.toMatch(/Plug-in|Plug-ins/);
    }
  });
});

describe("alerts live under 'Notifications'", () => {
  it("the page renamed", () => {
    const s = read("components/settings/pages/NotificationsPage.tsx");
    expect(s).toContain('title="Notifications"');
    expect(s).not.toContain('title="Channels"');
    expect(s).not.toContain("Add a channel");
  });
  it("the list card says Destinations, not channels (v1.118.0)", () => {
    const s = read("components/settings/pages/NotificationsPage.tsx");
    expect(s).toContain("`Destinations${");
    expect(s).not.toContain("Configured channels");
  });
  it("but Slack's own 'channel' concept is still allowed to be called one", () => {
    // "a bot token + a channel" is SLACK's channel — their word, correct
    // usage. The ban is on OUR concept wearing that name.
    expect(read("components/settings/pages/NotificationsPage.tsx")).toContain("a bot token + a channel");
  });
  it("our concept is a destination on every automation page (v1.314.0)", () => {
    // "Channels" was OUR word for a destination; it is retired on the pages
    // that link to Notifications. Slack's own "channel" stays allowed — the
    // exemption above — so only OUR phrasings are banned here.
    const code = (f: string) =>
      read(f)
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/^\s*\/\/.*$/gm, "");
    const reflex = code("app/reflex/page.tsx");
    expect(reflex).not.toMatch(/^\s*Channels\b/m);
    expect(reflex).not.toMatch(/Channels page|\bEmail channel\b/);
    const autonomy = code("app/autonomy/page.tsx");
    expect(autonomy).not.toMatch(/to channels|connected channels|comm channel/i);
    const channels = code("components/settings/pages/NotificationsPage.tsx");
    expect(channels).not.toMatch(/All channels|No channel responses|aria-label="Channel"/);
    expect(channels).not.toMatch(/^\s*Channel\s*$/m);
    for (const f of [
      "app/templates/page.tsx",
      "app/reflex/page.tsx",
      "app/autonomy/page.tsx",
      "components/settings/pages/NotificationsPage.tsx",
      // v1.316.0 (UX wave 4): the rest of the automation pages.
      "app/webhooks/page.tsx",
      "app/sentinels/page.tsx",
      "app/schedules/page.tsx",
    ]) {
      // Our concept, any page: a "channel" that is not Slack's/Discord's.
      expect(code(f), f).not.toMatch(/\b(?:Email|Telegram|inbound|comm) channels?\b/i);
    }
    // v1.316.0: these three name the Notifications page by its nav word too.
    for (const f of ["app/webhooks/page.tsx", "app/sentinels/page.tsx", "app/schedules/page.tsx"]) {
      expect(code(f), f).not.toMatch(/Channels page|^\s*Channels\b/m);
    }
  });
  it("the nav label follows, and the old word stays searchable", () => {
    const s = read("lib/nav.ts");
    expect(s).toContain('label: "Notifications"');
    expect(s).toContain('"channels"'); // alias — muscle memory keeps working
  });
});

describe("the marketplace decision is finally reflected", () => {
  it("the page is a Directory (consume, don't compete — decided 2026-07-25)", () => {
    const s = read("components/settings/pages/DirectoryPage.tsx");
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
    expect(read("components/settings/pages/ToolsPage.tsx")).toContain("/mcp/servers");
    expect(read("components/settings/pages/NotificationsPage.tsx")).toContain("/comm/channels");
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

/**
 * THE SCAN (v1.357.0, product readiness G76). The pins above each guard one
 * exact string, so a variant slipped past all of them ("Connected packs",
 * "Open the pane", "the offline mock", "the daemon did not open…"). This
 * reads EVERY string literal and JSX text node in app/, components/ and lib/
 * through the TypeScript parser (helpers/retiredWords.ts says what it skips
 * and why) and fails on a retired word in anything a user can read.
 *
 * ALLOW is the short list of DELIBERATE uses — each names its file, a piece
 * of its text and why. An entry that no longer matches anything fails too,
 * so a reworded line takes its entry with it.
 */
const ALLOW: ReadonlyArray<{ file: string; text: string; why: string }> = [
  {
    file: "components/settings/MaintenanceTools.tsx",
    text: "daemon.log",
    why: "the real log file's name in the folder this button opens; a file name is a name, not prose",
  },
  {
    file: "components/sessions/runPageWords.ts",
    text: "interrupted by a daemon restart",
    why: "OLD_RESTART_SUMMARY: matches what versions before v1.354.0 STORED on old runs; read by isRestartSummary, never shown",
  },
  {
    file: "components/agents/SetupCard.tsx",
    text: "Job card",
    why: "an agent's job description (its role, v1.295.0 'a custom agent is an employee'), not a unit of work",
  },
];

const allowed = (h: { file: string; text: string }) => ALLOW.some((a) => a.file === h.file && h.text.includes(a.text));

describe("the canon is a scan, not a list of strings (v1.357.0)", () => {
  const hits = scannedFiles().flatMap((f) => retiredHits(f));

  it("no retired word in anything a user reads", () => {
    const left = hits
      .filter((h) => !allowed(h))
      .map((h) => `${h.file}:${h.line} [${h.word}] ${h.text.slice(0, 110)}`);
    expect(left).toEqual([]);
  });

  it("every allowlist entry still matches a line, and says why", () => {
    for (const a of ALLOW) {
      expect(a.why.trim().length, `${a.file}: ${a.text}`).toBeGreaterThan(10);
      expect(
        hits.some((h) => h.file === a.file && h.text.includes(a.text)),
        `stale allowlist entry ${a.file}: "${a.text}"`,
      ).toBe(true);
    }
  });

  it("scans every folder a user reads, and a lot of files", () => {
    const files = scannedFiles();
    for (const dir of ["app/", "components/", "lib/"]) expect(files.some((f) => f.startsWith(dir))).toBe(true);
    expect(files.length).toBeGreaterThan(300);
    expect(files).toContain("app/chat/page.tsx");
  });

  it("reads prose and skips code (the probe that keeps the scan honest)", () => {
    // Anti-vacuity: a scanner that reads nothing passes every file. Each
    // shape below is one a real file has; the expectation says which a user
    // can read.
    const probe = [
      `import x from "@/components/agents/mission/TeamScreen";`,
      `// the daemon restarts here (a comment)`,
      `/* a pack in a block comment */`,
      `const KEYS = { "job:mission": "Kept as a key" };`,
      `const isMock = provider === "mock";`,
      `if (s.includes("Mock answer")) {}`,
      `console.warn("daemon unreachable");`,
      `const id = "pane-rail";`,
      `const route = "/missions";`,
      `const aliases = { aliases: ["connect a pack", "pane"] };`,
      `const t = { title: "Output came from the mock model." };`,
      `export function A({ n }: { n: number }) {`,
      `  return (`,
      `    <div data-testid="pane-rail" className="pane" title="Close this pane" aria-label="Rename pane">`,
      `      Open the Build pane`,
      `      <span>{n === 1 ? "pane" : "panes"}</span>`,
      `      <p>{\`\${n} queued \${n === 1 ? "job" : "jobs"}\`}</p>`,
      `      <b>{"The daemon did not answer."}</b>`,
      `    </div>`,
      `  );`,
      `}`,
    ].join("\n");
    const prose = readableCopy("probe.tsx", probe).map((p) => p.text.trim());
    // What a user reads:
    expect(prose).toContain("Output came from the mock model.");
    expect(prose).toContain("Close this pane");
    expect(prose).toContain("Rename pane");
    expect(prose).toContain("Open the Build pane");
    expect(prose).toContain("pane");
    expect(prose).toContain("panes");
    expect(prose).toContain("job");
    expect(prose).toContain("jobs");
    expect(prose).toContain("The daemon did not answer.");
    expect(prose).toContain("Kept as a key");
    // What it does not:
    for (const code of [
      "@/components/agents/mission/TeamScreen",
      "job:mission",
      "mock",
      "Mock answer",
      "daemon unreachable",
      "pane-rail",
      "/missions",
      "connect a pack",
    ]) {
      expect(prose, code).not.toContain(code);
    }
    // And the words it reads become hits, one per retired word.
    const words = new Set(retiredHits("probe.tsx", probe).map((h) => h.word));
    for (const w of ["mock", "pane", "job", "daemon"]) expect(words.has(w), w).toBe(true);
    expect(words.has("pack")).toBe(false); // only in a comment and an alias
  });

  it("an attribute is judged by its NAME whatever shape its value takes, and a plural choice is words (v1.357.0 review)", () => {
    // Read: a one-word title / aria-label / placeholder behind a ternary or a
    // fallback, and a singular/plural choice kept in a variable first.
    const read = [
      `export const A = ({ x }: { x: boolean }) => <b title={x ? "daemon" : "app"} />;`,
      `export const B = ({ x }: { x: boolean }) => <i aria-label={x ? "pack" : "tool"} />;`,
      `export const C = ({ v }: { v?: string }) => <input placeholder={v || "mock"} />;`,
      `export const D = ({ n }: { n: number }) => { const noun = n === 1 ? "pane" : "panes"; return <span>{n} {noun}</span>; };`,
    ];
    for (const src of read) expect(retiredHits("probe.tsx", src).length, src).toBeGreaterThan(0);
    // Skipped: a hidden attribute stays hidden behind a template or a ternary.
    const skipped = [
      `export const E = ({ x }: { x: string }) => <div className={\`pane-rail \${x}\`} />;`,
      `export const F = ({ x }: { x: boolean }) => <div className={x ? "pane on" : "pane"} />;`,
      `export const G = ({ x }: { x: string }) => <div data-testid={\`pane \${x}\`} />;`,
      `export const H = ({ id }: { id: string }) => <a href={\`/agents?mission=\${id}\`}>Open</a>;`,
    ];
    for (const src of skipped) expect(retiredHits("probe.tsx", src), src).toEqual([]);
  });

  it("covers every retired word the canon names", () => {
    const words = RETIRED.map((r) => r.word);
    for (const w of ["pack", "connector", "memory source", "brain", "mission", "daemon", "mock", "pane", "job", "harness"]) {
      expect(words).toContain(w);
    }
    const canon = readFileSync(join(DASH, "..", "VOCABULARY.md"), "utf-8").replace(/\r\n/g, "\n");
    expect(canon).toContain("| One piece of work Jarvis does for you");
    expect(canon).toMatch(/\*\*task\*\*; one attempt at it is a \*\*run\*\*/);
    expect(canon).toContain("| **terminal** | pane |");
    expect(canon).toContain("| **Iron Jarvis** | daemon |");
    // Harness gaps (fix 4): Build's coding-agent view.
    expect(canon).toContain("| **Coding sessions**; one of them is a **coding session** | IJ harness, harness |");
  });
});
