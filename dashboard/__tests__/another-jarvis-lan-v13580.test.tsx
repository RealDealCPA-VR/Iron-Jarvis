/**
 * v1.358.0 lane 8E (G61) — the task door's card points at the ONE setting
 * that lets another computer reach this one.
 *
 * Before: the card showed the daemon's sentence telling a desktop-app user to
 * run `ironjarvis serve --host` and set IRONJARVIS_HOST_ALLOWLIST, which the
 * app cannot do. Now the sentence names "Let other computers on my network
 * reach this Iron Jarvis" (Settings › Connections), and right under it the
 * card offers the one press that opens that row (Settings reads ?focus= on
 * mount). Nothing turns on from the card. When the address CAN be reached,
 * one quiet grey line says so.
 *
 * Drives the REAL card with only lib/api mocked; the calm look is pinned
 * (a ghost link, a grey line: no new boxed card, tone tokens only).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";

const api = vi.hoisted(() => ({ door: {} as Record<string, unknown>, posts: [] as string[] }));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    API_BASE: "http://test",
    get: (p: string) => Promise.resolve(p === "/agents/task-door" ? api.door : {}),
    post: (p: string) => {
      api.posts.push(p);
      return Promise.resolve({});
    },
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

import { AnotherJarvisCard } from "@/components/agents/AnotherJarvisCard";
import {
  LAN_SETTING_HREF,
  LAN_SETTING_KEY,
  LAN_SETTING_LINK,
  REACHABLE_LINE,
  TASK_DOOR_PATH,
} from "@/lib/anotherJarvis";

const DOOR_URL = `http://192.168.1.20:8787${TASK_DOOR_PATH}`;
// The daemon's own sentence (routes/agents.reachability, v1.358.0).
const NOTE =
  "Iron Jarvis on this computer accepts requests from this computer only, so the other computer " +
  "cannot reach it yet. To allow it, turn on “Let other computers on my network reach this Iron " +
  "Jarvis” in Settings › Connections and restart Iron Jarvis. Every request still needs its token.";

const ROOT = path.join(__dirname, "..");
const read = (rel: string) => readFileSync(path.join(ROOT, rel), "utf-8").replace(/\r\n/g, "\n");

beforeEach(() => {
  api.posts = [];
});
afterEach(() => cleanup());

describe("the task door's card points at the setting, not a command line (v1.358.0)", () => {
  it("not reachable yet: the daemon's sentence, then ONE quiet press that opens the setting's row", async () => {
    api.door = {
      enabled: true,
      caller: "laptop",
      url: DOOR_URL,
      reachable: { host_allowed: false, note: NOTE },
    };
    render(<AnotherJarvisCard />);
    const note = await screen.findByTestId("another-jarvis-reach-note");
    expect(note.textContent).toBe(NOTE);
    const link = await screen.findByTestId("another-jarvis-lan-setting");
    expect(link.textContent).toBe(LAN_SETTING_LINK);
    expect(link.getAttribute("href")).toBe(`/settings?focus=${LAN_SETTING_KEY}`);
    expect(LAN_SETTING_HREF).toBe("/settings?focus=lan_access");
    // Nothing turns on from here: no request was made by showing it.
    expect(api.posts).toEqual([]);
    expect(screen.queryByTestId("another-jarvis-reachable")).toBeNull();
  });

  it("reachable: no warning, no press — one grey line says another computer can reach it", async () => {
    api.door = {
      enabled: true,
      caller: "laptop",
      url: DOOR_URL,
      reachable: { host_allowed: true, note: "" },
    };
    render(<AnotherJarvisCard />);
    const line = await screen.findByTestId("another-jarvis-reachable");
    expect(line.textContent).toBe(REACHABLE_LINE);
    expect(line.className).toContain("text-zinc-500");
    expect(screen.queryByTestId("another-jarvis-reach-note")).toBeNull();
    expect(screen.queryByTestId("another-jarvis-lan-setting")).toBeNull();
  });

  it("off: neither line shows (the door must be on before reaching it matters)", async () => {
    api.door = { enabled: false, caller: "another computer", url: "", reachable: null };
    render(<AnotherJarvisCard />);
    await screen.findByTestId("another-jarvis-on-press");
    expect(screen.queryByTestId("another-jarvis-lan-setting")).toBeNull();
    expect(screen.queryByTestId("another-jarvis-reachable")).toBeNull();
  });

  it("the key is the daemon's and the look stays calm (a ghost link, no boxed card, no literal hue)", () => {
    const schema = read("../src/iron_jarvis/settings/schema.py");
    expect(schema).toContain(`S("${LAN_SETTING_KEY}", "Let other computers on my network reach this Iron Jarvis"`);
    const card = read("components/agents/AnotherJarvisCard.tsx");
    const block = card.slice(card.indexOf('data-testid="another-jarvis-lan-setting"') - 200, card.indexOf("{LAN_SETTING_LINK}"));
    expect(block).toContain("className={GHOST_BTN}");
    expect(block).not.toMatch(/border|shadow|#[0-9a-fA-F]{3,6}\b|rgb\(/);
    expect(card).not.toMatch(/ironjarvis serve|IRONJARVIS_HOST_ALLOWLIST/);
  });
});
