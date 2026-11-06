/**
 * The local page-open counters (v1.151.0; trimmed v1.439.0).
 *
 * The Overview's tile grid and its ordering left with `AppGrid` (removed in
 * v1.439.0, Wave 6 of the chat-first plan); what is left of `lib/appTiles` is
 * what the nav and the chat's doors still call, pinned here.
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import { recordDoorOpen, recordOpen, readUsage } from "@/lib/appTiles";

function fakeStorage() {
  const map = new Map<string, string>();
  return {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
    clear: () => map.clear(),
  };
}

beforeEach(() => {
  vi.stubGlobal("window", { localStorage: fakeStorage() });
});

describe("usage counting", () => {
  it("counts opens", () => {
    recordOpen("/memory");
    recordOpen("/memory");
    recordOpen("/creative");
    expect(readUsage()).toEqual({ "/memory": 2, "/creative": 1 });
  });

  it("does not count the overview or help", () => {
    recordOpen("/");
    recordOpen("/help");
    expect(readUsage()).toEqual({});
  });

  it("counts a chat door under its own key, never as a nav open", () => {
    recordDoorOpen("/schedules");
    recordDoorOpen("/schedules");
    expect(readUsage()).toEqual({});
    expect(JSON.parse(window.localStorage.getItem("ironjarvis.doors.usage") ?? "{}")).toEqual({
      "/schedules": 2,
    });
  });

  it("survives unreadable storage instead of throwing", () => {
    vi.stubGlobal("window", {
      localStorage: {
        getItem: () => "{not json",
        setItem: () => {
          throw new Error("quota");
        },
        removeItem: () => {},
      },
    });
    // A corrupt or full store must degrade to "nothing counted", never break
    // the nav press that records it.
    expect(() => recordOpen("/memory")).not.toThrow();
    expect(() => recordDoorOpen("/memory")).not.toThrow();
    expect(readUsage()).toEqual({});
  });
});
