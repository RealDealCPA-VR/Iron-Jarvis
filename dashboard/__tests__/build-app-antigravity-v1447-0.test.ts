/**
 * v1.447.0 — Antigravity's full capabilities in Build › Code (the dashboard's
 * half). The daemon now reads Antigravity's own MCP servers with `agy mcp
 * list` (names and on/off only: it says a server is TURNED ON, never whether
 * it connected), and "Share my profile with Antigravity" exists.
 */
import { describe, expect, it } from "vitest";

import { SHARE_CLIS, shareCliOf, toolVendor } from "@/lib/buildApp";
import { decodeCodeMcp, MCP_STATES } from "@/lib/codePanes";

describe("Antigravity's own MCP servers", () => {
  it("keeps the 'on' state the daemon sends (turned on, connection not reported)", () => {
    expect(MCP_STATES).toContain("on");
    const mcp = decodeCodeMcp({
      known: true,
      at: "2026-11-07T20:00:00Z",
      servers: [
        { name: "api", state: "on", words: "Turned on (whether it connected is not reported)" },
        { name: "gone", state: "off", words: "Turned off" },
      ],
    });
    expect(mcp?.servers.map((s) => [s.name, s.state])).toEqual([
      ["api", "on"],
      ["gone", "off"],
    ]);
    expect(mcp?.servers[0].words).toContain("not reported");
  });

  it("still reads an unknown state as failed", () => {
    const mcp = decodeCodeMcp({ known: true, servers: [{ name: "x", state: "weird", words: "?" }] });
    expect(mcp?.servers[0].state).toBe("failed");
  });
});

describe("Share my profile with Antigravity", () => {
  it("offers the share for Antigravity and names Google", () => {
    expect(SHARE_CLIS).toContain("antigravity");
    expect(shareCliOf("antigravity")).toBe("antigravity");
    expect(shareCliOf("grok")).toBe("grok");
    expect(shareCliOf("something-else")).toBe("");
    expect(toolVendor("antigravity")).toBe("Google");
  });
});
