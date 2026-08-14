/**
 * The Connections page cannot claim a capability that isn't there (v1.172.0).
 *
 * A user reported Iron Jarvis was "blind as a bat" with no access to their
 * wikis — while the Connections page showed the connector green. The badge
 * was computed from "a config entry exists", and MCP tools load ONCE at
 * daemon boot, so a server that failed to launch (or was added since startup)
 * delivered zero tools behind a confident "Connected".
 *
 * Source pins: the page file is too heavy to mount here, so these assert the
 * rendering rules directly — the same technique the v1.165.0 accountability
 * suite uses for chat/page.tsx.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const pageSrc = readFileSync(
  join(__dirname, "..", "app", "connections", "page.tsx"),
  "utf8",
);

describe("connector status honesty", () => {
  it("no_tools is checked BEFORE the green connected branch", () => {
    const statusPill = pageSrc.slice(
      pageSrc.indexOf("function StatusPill"),
      pageSrc.indexOf("function StatusPill") + 1400,
    );
    const noTools = statusPill.indexOf('conn.status === "no_tools"');
    const connected = statusPill.indexOf("} else if (conn.connected)");
    expect(noTools).toBeGreaterThan(-1);
    expect(connected).toBeGreaterThan(-1);
    expect(noTools).toBeLessThan(connected);
  });

  it("the no_tools badge says what is wrong AND what fixes it", () => {
    expect(pageSrc).toContain('label = "0 tools — restart"');
    // Amber, not green: the state is a warning, not a success.
    expect(pageSrc).toMatch(
      /conn\.status === "no_tools"[\s\S]{0,200}amber-500\/25/,
    );
  });

  it("the server's explanation is rendered, not swallowed", () => {
    expect(pageSrc).toMatch(
      /conn\.status === "no_tools" && conn\.detail[\s\S]{0,400}\{conn\.detail\}/,
    );
  });

  it("the dot is amber for no_tools even though connected stays true", () => {
    // connected===true keeps "your connect worked / it survived a restart"
    // honest (pinned server-side by test_connectors::test_restart_survival);
    // the DOT must still not read as a working connection.
    const dot = pageSrc.slice(
      pageSrc.indexOf("h-1.5 w-1.5 rounded-full"),
      pageSrc.indexOf("h-1.5 w-1.5 rounded-full") + 420,
    );
    const amberFirst = dot.indexOf('conn.status === "no_tools"');
    const greenBranch = dot.indexOf("conn.connected");
    expect(amberFirst).toBeGreaterThan(-1);
    expect(amberFirst).toBeLessThan(greenBranch);
  });
});
