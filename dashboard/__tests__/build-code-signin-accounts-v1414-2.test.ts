/** v1.414.2 (a USER BUG): "in the build IDE, it states that I'm signed out of
 *  Claude, but I checked and I am signed in." This PC's own Claude Code login
 *  was signed out while the user's Iron-Proxy account (TSI) was signed in and
 *  running their sessions. The tool menu read only the PC's own login. */
import { describe, expect, it } from "vitest";
import { toolUnavailable, type CodingTool } from "@/lib/buildApp";

function tool(over: Partial<CodingTool>): CodingTool {
  return {
    id: "claude-code", label: "Claude Code", installed: true, signedIn: false, version: "",
    modes: [], hiddenModes: [], models: [], efforts: [], accounts: [], commands: [],
    ...over,
  } as CodingTool;
}

describe("a signed-out PC login with a ready Iron-Proxy account", () => {
  it("keeps the tool available when an Iron-Proxy account is ready", () => {
    expect(toolUnavailable(tool({ accounts: [{ id: "p1", name: "TSI", status: "ready" }] }))).toBeNull();
    expect(toolUnavailable(tool({ accounts: [{ id: "p1", name: "TSI", status: "usage" }] }))).toBeNull();
  });
  it("still says signed out when no account can run it", () => {
    for (const status of ["needs_sign_in", "limit", "off"]) {
      expect(toolUnavailable(tool({ accounts: [{ id: "p1", name: "TSI", status }] }))).toContain("is signed out");
    }
    expect(toolUnavailable(tool({ accounts: [] }))).toContain("is signed out");
  });
  it("a tool not installed is never available, accounts or not", () => {
    expect(toolUnavailable(tool({ installed: false, accounts: [{ id: "p1", name: "TSI", status: "ready" }] }))).toContain("not installed");
  });
});
