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
  join(__dirname, "..", "components", "settings", "pages", "ConnectionsPage.tsx"),
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
    // v1.329.0 (calm K2): the pill keeps both halves (what is wrong, what
    // fixes it) with the pills' middle dot instead of a dash aside.
    expect(pageSrc).toContain('label = "0 tools · restart"');
    // A warning, not a success. v1.329.0 (calm J3): the warning tone is the
    // theme token (tone-warn), not literal amber, so Daylight re-inks it.
    // v1.330.0 (calm L2): the pill is the calm Badge now, so the branch sets
    // the Badge's "amber" tone, which Badge draws with the tone-warn token
    // (confirm-badge-calm-v1330 pins the tone -> token mapping).
    expect(pageSrc).toMatch(/conn\.status === "no_tools"\) \{\s*tone = "amber";/);
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
    // v1.330.0 (calm L2): the pill is the calm Badge and its dot IS the tone
    // the branches pick, so the order of those branches is the dot's rule.
    const pill = pageSrc.slice(
      pageSrc.indexOf("function StatusPill"),
      pageSrc.indexOf("function StatusPill") + 2600,
    );
    const amberFirst = pill.indexOf('tone = "amber";');
    const greenBranch = pill.indexOf('tone = "green";');
    expect(amberFirst).toBeGreaterThan(-1);
    expect(greenBranch).toBeGreaterThan(-1);
    expect(amberFirst).toBeLessThan(greenBranch);
    expect(pill).toMatch(/<Badge[\s\S]{0,200}tone=\{tone\}/);
  });
});
