/**
 * A2 review of v1.448.0 (Wave A item 4): the secure card DRAWS what a tool
 * server's key did.
 *
 * Before: `lib/appConnect.connectedFromSave` read `{app, tools_loaded, …}`
 * only off an `app.*` card, so the daemon's answer to a `tool_server.*` save
 * ("added · 1 tool", "still needs …", "did not start") was returned and never
 * shown — the card said "saved securely" and stopped. Now the card knows the
 * kind: its words name the tool server, its not-loaded door opens Settings ›
 * Tools (the pack's row holds the load record and Retry), and the page turns
 * the pack on for the chat as it does an app's. A web service's save answers
 * nothing to draw, and the Continue-after-connect reading
 * (lib/connectedElsewhere) still counts only an app's card.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("@/lib/api", () => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { ConfigCards } from "@/components/chat/ConfigCards";
import { connectedFromSave, connectedWords, connectorIdOf, notLoadedDoor, toolServerOf, waitingWords } from "@/lib/appConnect";
import type { ConfigCard } from "@/lib/configCards";
import { offeredApps } from "@/lib/connectedElsewhere";

afterEach(cleanup);

const SAVED = { secret: "tool_server.keyed__env__API_KEY", label: "A tool server's key", status: "stored", action_id: "a1" };

describe("a tool server's secure card (A2 review)", () => {
  it("reads the pack off every slot shape, and nothing off any other card", () => {
    expect(toolServerOf("tool_server.keyed__env__API_KEY")).toBe("keyed");
    expect(toolServerOf("tool_server.my__pack__env__API_KEY")).toBe("my__pack");
    expect(toolServerOf("tool_server.hp__header__X-Api-Key")).toBe("hp");
    expect(toolServerOf("tool_server.hp__auth")).toBe("hp");
    expect(toolServerOf("tool_server.argtok__arg__2")).toBe("argtok");
    expect(toolServerOf("tool_server.live")).toBe("live");
    expect(toolServerOf("app.github")).toBe("");
    expect(toolServerOf("web_service.pricing")).toBe("");
    expect(connectorIdOf("app.github")).toBe("github");
    expect(connectorIdOf("tool_server.keyed__env__API_KEY")).toBe("keyed");
    expect(connectorIdOf("web_service.pricing")).toBe("");
    expect(connectorIdOf("connection.openai")).toBe("");
  });

  it("decodes the daemon's answer to a tool server's save, and nothing to a web service's", () => {
    const added = connectedFromSave("tool_server.keyed__env__API_KEY", { ...SAVED, app: "keyed", app_name: "keyed", tools_loaded: 1 });
    expect(added).toEqual({ app: "keyed", name: "keyed", tools: 1, kind: "tool_server" });
    expect(connectedWords(added!)).toBe("Extension “keyed” added · 1 tool");
    const waiting = connectedFromSave("tool_server.two__env__API_KEY", { app: "two", app_name: "two", tools_loaded: 0, waiting_for: ["the X-Api-Key header"] });
    expect(waitingWords(waiting!)).toBe("The extension “two” still needs its the X-Api-Key header");
    const dead = connectedFromSave("tool_server.hp__auth", { app: "hp", app_name: "hp", tools_loaded: 0, load_reason: "saved, but it did not start" });
    expect(notLoadedDoor(dead!)).toEqual({ href: "/tools", words: "open Settings › Tools" });
    // An app's card is byte-for-byte what it was.
    const app = connectedFromSave("app.github", { app: "github", app_name: "GitHub", tools_loaded: 26 });
    expect(app).toEqual({ app: "github", name: "GitHub", tools: 26 });
    expect(connectedWords(app!)).toBe("GitHub connected · 26 tools");
    expect(notLoadedDoor(app!)).toEqual({ href: "/marketplace", words: "open the Directory" });
    // A web service's save says only stored/replaced: nothing to draw.
    expect(connectedFromSave("web_service.pricing", { secret: "web_service.pricing", status: "stored" })).toBeUndefined();
    expect(connectedFromSave("web_service.pricing", { status: "stored", tools_loaded: 0 })).toBeUndefined();
  });

  it("draws the words and the Settings › Tools door on the card", () => {
    const added: ConfigCard = {
      kind: "secret", name: "tool_server.keyed__env__API_KEY", label: "keyed: API_KEY", request_id: "r1",
      saved: "stored", connected: { app: "keyed", name: "keyed", tools: 1, kind: "tool_server" },
    } as ConfigCard;
    const dead: ConfigCard = {
      kind: "secret", name: "tool_server.hp__auth", label: "hp: its access token", request_id: "r2",
      saved: "stored", connected: { app: "hp", name: "hp", tools: 0, kind: "tool_server", reason: "saved, but it did not start" },
    } as ConfigCard;
    render(<ConfigCards cards={[added, dead]} onSettle={() => {}} onContinue={() => {}} />);
    expect(screen.getByTestId("credential-card-connected").textContent).toContain("Extension “keyed” added · 1 tool");
    expect(screen.getByTestId("credential-card-continue")).toBeTruthy();
    const notLoaded = screen.getByTestId("credential-card-not-loaded");
    expect(notLoaded.textContent).toContain("did not load");
    const link = notLoaded.querySelector("a");
    expect(link?.getAttribute("href")).toBe("/tools");
    expect(link?.textContent).toBe("open Settings › Tools");
  });

  it("is never read as an app connected elsewhere", () => {
    const m = {
      role: "assistant",
      toolsUsed: ["mcp_server_add", "web_service_add"],
      doors: [],
      configCards: [
        { kind: "secret", name: "tool_server.keyed__env__API_KEY", request_id: "r1" },
        { kind: "secret", name: "web_service.pricing", request_id: "r2" },
      ],
    };
    expect(offeredApps(m as never)).toEqual([]);
  });
});
