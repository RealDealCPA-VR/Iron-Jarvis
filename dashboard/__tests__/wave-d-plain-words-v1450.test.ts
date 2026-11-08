/**
 * Wave D item 33: engine-room words leave the primary paths, and the
 * vocabulary scanner learns (1) HTML entities in JSX and (2) a row for
 * "Iron-Proxy" scoped to the chat and coding surfaces (VOCABULARY.md row 29:
 * the program keeps its own name on its Connections card).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  ALL_RETIRED,
  decodeEntities,
  isChatOrCodingSurface,
  retiredHits,
} from "./helpers/retiredWords";
import { ANY_ACCOUNT_STATE } from "@/components/chat/ModelMenu";
import { TRY_ACCOUNTS_LEAD } from "@/lib/buildApp";
import { CHAT_ACCOUNT_OFF_NOTE, CHAT_ACCOUNT_UPDATE_NOTE } from "@/lib/chatAccount";
import { MCP_RELOAD_WORDS, mcpReloadTitle } from "@/lib/codePanes";

const DASH = join(__dirname, "..");
const read = (p: string) => readFileSync(join(DASH, p), "utf-8").replace(/\r\n/g, "\n");

describe("the scanner reads HTML entities as the browser shows them", () => {
  it("decodes named and numeric entities", () => {
    expect(decodeEntities("See subscription &amp; local providers")).toBe("See subscription & local providers");
    expect(decodeEntities("Jarvis&rsquo;s")).toBe("Jarvis’s");
    expect(decodeEntities("it&#39;s &#x27;x&#x27;")).toBe("it's 'x'");
    expect(decodeEntities("&notanentity; a & b")).toBe("&notanentity; a & b");
  });

  it("an entity-spelled retired phrase in JSX text is a hit", () => {
    const probe = `export const A = () => <a>See subscription &amp; local providers</a>;`;
    expect(retiredHits("components/probe.tsx", probe).map((h) => h.word)).toContain("Subscription & local providers");
  });
});

describe("Iron-Proxy is retired on the chat and coding surfaces only", () => {
  const row = ALL_RETIRED.find((r) => r.word === "Iron-Proxy");

  it("has a scoped row", () => {
    expect(row?.scope).toBeTypeOf("function");
  });

  it("the scope is the chat and coding surfaces", () => {
    for (const f of [
      "components/chat/ModelMenu.tsx",
      "components/harness/HarnessSession.tsx",
      "components/buildCode/CodePanes.tsx",
      "lib/buildApp.ts",
      "lib/buildAppHistory.ts",
      "lib/buildWords.ts",
      "lib/chatAccount.ts",
      "lib/paneAccounts.ts",
    ])
      expect(isChatOrCodingSurface(f), f).toBe(true);
    for (const f of ["components/connections/IronProxyCard.tsx", "lib/ironProxy.ts", "components/settings/SettingsHome.tsx"])
      expect(isChatOrCodingSurface(f), f).toBe(false);
  });

  it("a coding surface saying Iron-Proxy is a hit; the Connections card is not", () => {
    const probe = `export const W = "Iron-Proxy picks the first free one";`;
    expect(retiredHits("lib/buildApp.ts", probe).map((h) => h.word)).toContain("Iron-Proxy");
    expect(retiredHits("components/chat/X.tsx", probe).map((h) => h.word)).toContain("Iron-Proxy");
    expect(retiredHits("components/connections/IronProxyCard.tsx", probe).map((h) => h.word)).not.toContain("Iron-Proxy");
  });

  it("the Connections card still names the program (the canon)", () => {
    expect(read("components/connections/IronProxyCard.tsx")).toContain("Several accounts (Iron-Proxy)");
  });
});

describe("the six account lines say 'your accounts' / 'the account manager'", () => {
  it("in the words themselves", () => {
    for (const w of [ANY_ACCOUNT_STATE, TRY_ACCOUNTS_LEAD, CHAT_ACCOUNT_OFF_NOTE, CHAT_ACCOUNT_UPDATE_NOTE])
      expect(w).not.toMatch(/iron[- ]?proxy/i);
    expect(retiredHits("lib/paneAccounts.ts").filter((h) => h.word === "Iron-Proxy")).toEqual([]);
    expect(read("components/settings/ModelTeam.tsx")).toContain("the account manager picks one");
  });
});

describe("plain words on the primary paths", () => {
  it("Settings home's Connections card", () => {
    const s = read("components/settings/SettingsHome.tsx");
    expect(s).toContain("Your model plans and sign-ins, API keys, your own model servers, and several accounts.");
    expect(s).not.toContain("Model providers, sign-ins, API keys, endpoints and Iron-Proxy accounts.");
  });

  it("Connections page", () => {
    const s = read("components/settings/pages/ConnectionsPage.tsx");
    for (const old of [
      "All providers connected",
      "See subscription &amp; local providers",
      "Add an endpoint",
      "Add another endpoint",
      "Endpoint base URL",
      "Saved endpoints\n",
      "Each endpoint is its own provider",
      "Its provider disappears",
    ])
      expect(s, old).not.toContain(old);
    for (const now of ["Everything is connected", "See your plans and model servers", "Add a model server", "Server address"])
      expect(s, now).toContain(now);
  });

  // v1.455.0 (Wave E, moved on purpose): an MCP server is an "extension"
  // (VOCABULARY.md row 22); "tool server" was a second name for it.
  it("Build › Code's extensions", () => {
    expect(read("components/buildCode/CodePanes.tsx")).toContain('title="Its own extensions"');
    expect(MCP_RELOAD_WORDS).toBe("Reload its extensions");
    expect(mcpReloadTitle("Codex")).not.toMatch(/\bMCP\b/);
  });

  it("first-run door, goal card, Launch menu", () => {
    expect(read("components/onboarding/ConnectDoors.tsx")).toContain('fleet: "Your model servers"');
    const goal = read("components/chat/GoalContractCard.tsx");
    expect(goal).not.toContain("Schedule (cron)");
    expect(goal).not.toMatch(/a cron fires/);
    expect(goal).toContain("on a schedule");
    const pane = read("components/terminal/TerminalPane.tsx");
    expect(pane).not.toContain("on your PATH");
    expect(pane).toContain("isn't installed where this PC can find it");
  });
});
