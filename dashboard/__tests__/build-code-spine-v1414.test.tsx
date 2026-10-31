/**
 * v1.414.0 — Build › Code is part of the app.
 *
 * - Running now lists a Build › Code reply (`surface: "build_app"`) named by
 *   its tool, opened on that session in Build › Code; the orb counts it.
 * - The session's "Iron Jarvis tools" boxes (Memory, Browser): all off until
 *   ticked, each says what it gives and who sees it before the press, a
 *   press PATCHes only that box.
 * - The share offer: shown in a new session whose tool's "Share my profile"
 *   switch is off; Share turns the EXISTING setting on through the existing
 *   route; "No thanks" dismisses it for good; never shown when the switch is
 *   on, the tool is not found, or an older Iron Jarvis has no route.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  puts: [] as { path: string; body: unknown }[],
  patches: [] as { path: string; body: unknown }[],
  patchReply: {} as Record<string, unknown>,
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError: FakeApiError,
    get: (path: string) => {
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(r);
    },
    put: (path: string, body?: unknown) => {
      H.puts.push({ path, body });
      return Promise.resolve({});
    },
    patch: (path: string, body?: unknown) => {
      H.patches.push({ path, body });
      return Promise.resolve(H.patchReply[path] ?? {});
    },
  };
});

import { buildAppReplyItems, runningItems } from "@/lib/runningNow";
import { repliesRunning } from "@/components/MoodOrb";
import { JarvisToolsChip, ProfileShareOffer } from "@/components/buildCode/JarvisTools";
import { decodeCodeSession, jarvisToolsWords, shareOfferDismissKey, type CodeSession } from "@/lib/buildApp";

const NOW = Date.parse("2026-10-30T12:00:00Z");

function session(extra: Partial<CodeSession> = {}): CodeSession {
  return decodeCodeSession({
    id: "app_1",
    harness: "codex",
    folder: "C:\\work",
    title: "Fix it",
    capabilities: { files: false, shell: false, browser: false, extensions: false, memory: false },
    ...extra,
  })!;
}

beforeEach(() => {
  H.gets = {};
  H.puts = [];
  H.patches = [];
  H.patchReply = {};
  window.localStorage.clear();
});
afterEach(() => cleanup());

describe("running work is seen everywhere", () => {
  it("lists a Build › Code reply, named by its tool and opened in Build › Code", () => {
    const rows = [
      { turn_id: "t1", thread_id: "app_1", surface: "build_app", harness: "codex", started_at: "2026-10-30T11:58:00Z", waiting: false, title: "Fix it" },
      { turn_id: "t2", thread_id: "app_2", surface: "build_app", harness: "claude-code", started_at: "2026-10-30T11:50:00Z", waiting: true, title: "Edit it" },
    ];
    const items = runningItems(null, null, NOW, rows);
    expect(items.map((i) => i.kind)).toEqual(["build_app", "build_app"]);
    // Waiting first (the user is the next step).
    expect(items[0].href).toBe("/terminals?view=code&code=app_2");
    expect(items[0].line).toBe("Coding session · Claude Code · 10 min · Waiting on you");
    expect(items[0].waiting).toBe(true);
    expect(items[1].line).toBe("Coding session · Codex · 2 min");
    expect(items[1].detail).toBe("Fix it");
  });

  it("never lists a chat's or a Chat › Code reply as a Build › Code one", () => {
    expect(buildAppReplyItems([{ turn_id: "t", thread_id: "bs_1", surface: "build" }], NOW)).toEqual([]);
    expect(buildAppReplyItems([{ turn_id: "t", thread_id: "", surface: "build_app" }], NOW)).toEqual([]);
  });

  it("the orb counts a Build › Code reply as work running", () => {
    expect(repliesRunning({ replies: { chat: 0, build: 0, build_app: 2 } } as never)).toBe(2);
    expect(repliesRunning({ replies: { chat: 1, build: 1 } } as never)).toBe(2);
  });
});

describe("the Iron Jarvis tools boxes", () => {
  it("are off until ticked and say what they give and who sees it", () => {
    const s = session();
    expect(s.jarvisTools).toEqual({ memory: false, browser: false });
    render(<JarvisToolsChip session={s} toolName="Codex" onChanged={() => {}} onInfo={() => {}} />);
    const chip = screen.getByTestId("code-jarvis-tools");
    expect(chip.textContent).toBe("Iron Jarvis tools: off");
    fireEvent.click(chip);
    const panel = screen.getByTestId("code-jarvis-tools-panel");
    expect(panel.textContent).toContain("Off until you tick one");
    expect(panel.textContent).toContain("What it reads goes to OpenAI with the conversation");
    expect(panel.textContent).toContain("Every click, typing or page change asks you first");
    expect((screen.getByTestId("code-jarvis-tool-memory") as HTMLInputElement).checked).toBe(false);
  });

  it("a press PATCHes only that box and adopts the daemon's answer", async () => {
    const path = "/build/app/sessions/app_1";
    H.patchReply[path] = {
      session: { id: "app_1", harness: "codex", capabilities: { memory: true, browser: false } },
      applied: { capabilities: "next" },
    };
    const changed = vi.fn();
    const info = vi.fn();
    render(<JarvisToolsChip session={session()} toolName="Codex" onChanged={changed} onInfo={info} />);
    fireEvent.click(screen.getByTestId("code-jarvis-tools"));
    fireEvent.click(screen.getByTestId("code-jarvis-tool-memory"));
    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(H.patches).toEqual([{ path, body: { capabilities: { memory: true } } }]);
    expect(changed.mock.calls[0][0].jarvisTools).toEqual({ memory: true, browser: false });
    expect(info).toHaveBeenCalledWith("The change to Iron Jarvis's tools applies from your next message.");
  });

  it("names what is on", () => {
    expect(jarvisToolsWords({ memory: true, browser: true })).toBe("Iron Jarvis tools: Memory, Browser");
  });

  it("a conversation found on disk (no boxes yet) shows no chip", () => {
    const disk = decodeCodeSession({ id: "disk_1", harness: "claude-code", source: "disk" })!;
    expect(disk.jarvisTools).toBeUndefined();
    render(<JarvisToolsChip session={disk} toolName="Claude Code" onChanged={() => {}} onInfo={() => {}} />);
    expect(screen.queryByTestId("code-jarvis-tools")).toBeNull();
  });
});

describe("the share offer", () => {
  const row = (on: boolean, available = true) => ({
    clis: [
      { cli: "pi", label: "Pi", vendor: "the model provider you chose in Pi", available, on, file_name: "AGENTS.md", files: [], targets: [{ path: "C:\\Users\\me\\.pi\\agent\\AGENTS.md", account: null }] },
    ],
    limit: 4000,
  });

  it("offers one press that turns the existing setting on", async () => {
    H.gets["/profile/share"] = row(false);
    render(<ProfileShareOffer tool="pi" toolName="Pi" />);
    const offer = await screen.findByTestId("code-share-offer");
    expect(offer.textContent).toContain("Share your profile with Pi?");
    expect(offer.textContent).toContain("the model provider you chose in Pi sees it");
    fireEvent.click(screen.getByTestId("code-share-offer-yes"));
    await screen.findByTestId("code-share-offer-done");
    expect(H.puts).toEqual([{ path: "/profile/share", body: { cli: "pi", on: true } }]);
  });

  it("No thanks dismisses it for good", async () => {
    H.gets["/profile/share"] = row(false);
    const first = render(<ProfileShareOffer tool="pi" toolName="Pi" />);
    await screen.findByTestId("code-share-offer");
    fireEvent.click(screen.getByTestId("code-share-offer-no"));
    expect(screen.queryByTestId("code-share-offer")).toBeNull();
    expect(window.localStorage.getItem(shareOfferDismissKey("pi"))).toBe("1");
    first.unmount();
    render(<ProfileShareOffer tool="pi" toolName="Pi" />);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("code-share-offer")).toBeNull();
    expect(H.puts).toEqual([]);
  });

  it("is never shown when the switch is on, the tool is missing, or the route is not there", async () => {
    H.gets["/profile/share"] = row(true);
    const a = render(<ProfileShareOffer tool="pi" toolName="Pi" />);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("code-share-offer")).toBeNull();
    a.unmount();
    H.gets["/profile/share"] = row(false, false);
    const b = render(<ProfileShareOffer tool="pi" toolName="Pi" />);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("code-share-offer")).toBeNull();
    b.unmount();
    delete H.gets["/profile/share"];
    render(<ProfileShareOffer tool="pi" toolName="Pi" />);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("code-share-offer")).toBeNull();
  });
});
