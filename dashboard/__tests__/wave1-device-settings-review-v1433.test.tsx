/**
 * v1.433.0 review (Wave 1 B, decision D1): the device rows never undo what
 * the person just did, and never undo what they chose before this release.
 *
 * Three holes the first cut left, each red on it:
 *
 *  1. A STALE READ. The window opens and reads this device's row; the person
 *     picks a theme while that read is still out; the read answers with the
 *     row as it was BEFORE the pick, and the first cut took it for someone
 *     else's change and put the old theme back. Same when a second press
 *     lands while the first is still being saved: the first save's
 *     `config.changed` read answers the first value, and undid the second.
 *  2. WRITES OUT OF ORDER. Two quick presses sent two PUTs at once; the
 *     daemon may finish them in either order, so the row could end on the
 *     FIRST press. Saves now go one at a time, in press order.
 *  3. UPGRADE DAY. Until this release the four rows did nothing on screen,
 *     so a row set back then (from chat or the old Settings row) is older
 *     than what the person sees. The first look at a key took the row, so a
 *     theme of the user's own (or a read-aloud switch they had turned off)
 *     was replaced the day they updated. Now, the first time this browser
 *     sees a key, a value the screen already keeps wins, and the row is set
 *     to it (a stale row would otherwise swallow a later chat change to the
 *     same value: the daemon raises no event for a value it already holds).
 *     A screen that keeps nothing for the key still takes the row.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  type Gate = { release: () => void; promise: Promise<void> };
  const gate = (): Gate => {
    let release = () => {};
    const promise = new Promise<void>((r) => {
      release = r;
    });
    return { release, promise };
  };
  return {
    FakeApiError,
    gate,
    /** What the daemon holds now. */
    row: {} as Record<string, unknown>,
    gets: 0,
    /** When set, the next GET reads the row NOW but answers when released. */
    holdGet: null as Gate | null,
    puts: [] as { values: Record<string, unknown>; gate: Gate | null; done: boolean }[],
    /** When true, each PUT waits for its gate before landing. */
    holdPuts: false,
    /** When true, a PUT is refused (after its gate, if held). */
    failPuts: false,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  get: async (path: string) => {
    if (!path.startsWith("/settings/device")) throw new H.FakeApiError(`unmocked GET ${path}`, 500);
    H.gets += 1;
    const snapshot = { ...H.row };
    const held = H.holdGet;
    H.holdGet = null;
    if (held) await held.promise;
    return { device_id: "dev_test", values: snapshot };
  },
  put: async (_path: string, body: { values?: Record<string, unknown> }) => {
    const values = { ...(body.values ?? {}) };
    const entry = { values, gate: H.holdPuts ? H.gate() : null, done: false };
    H.puts.push(entry);
    if (entry.gate) await entry.gate.promise;
    if (H.failPuts) throw new H.FakeApiError("Not Found", 404);
    Object.assign(H.row, values);
    entry.done = true;
    return { values: { ...H.row } };
  },
  post: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));

import { DeviceSettingsBridge } from "@/components/DeviceSettingsBridge";
import { chooseTheme, refreshDeviceSettings, writeDeviceSetting } from "@/lib/deviceSettings";
import { makePalette, upsertPalette } from "@/lib/themePalette";

const UNSET = {
  "device.theme": null,
  "device.approval_mode": null,
  "device.chat_persona": null,
  "device.auto_tools": null,
  "device.read_aloud": null,
};

const html = () => document.documentElement;

/** Every theme the page shows from now on (a flicker back counts). */
function themesShown(): string[] {
  const seen: string[] = [];
  const mo = new MutationObserver(() => seen.push(html().dataset.theme ?? ""));
  mo.observe(html(), { attributes: true, attributeFilter: ["data-theme"] });
  observers.push(mo);
  return seen;
}
const observers: MutationObserver[] = [];

async function settle(rounds = 10) {
  await act(async () => {
    for (let i = 0; i < rounds; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  H.row = { ...UNSET };
  H.gets = 0;
  H.holdGet = null;
  H.puts = [];
  H.holdPuts = false;
  H.failPuts = false;
  window.localStorage.clear();
  delete html().dataset.theme;
  delete html().dataset.scheme;
  html().removeAttribute("style");
});
afterEach(() => {
  cleanup();
  for (const mo of observers.splice(0)) mo.disconnect();
});

describe("a read that was out when the person pressed never undoes the press", () => {
  it("the window's first read answers the old row after a theme pick", async () => {
    // This browser already knows the row (not upgrade day).
    window.localStorage.setItem("ij_device_known", JSON.stringify({ "device.theme": "mark2" }));
    window.localStorage.setItem("ij_theme", "mark2");
    html().dataset.theme = "mark2";
    H.row = { ...UNSET, "device.theme": "mark2" };
    const read = H.gate();
    H.holdGet = read;
    render(<DeviceSettingsBridge />);
    await settle();
    expect(H.gets).toBe(1); // the read is out, holding the OLD row
    const shown = themesShown();
    chooseTheme("mark1");
    expect(html().dataset.theme).toBe("mark1");
    await settle();
    read.release();
    await settle(20);
    expect(html().dataset.theme).toBe("mark1");
    // Never put back, not even for a moment.
    expect(shown.filter((t) => t !== "mark1")).toEqual([]);
    expect(H.row["device.theme"]).toBe("mark1");
  });

  it("the first save's read does not undo a second press still being saved", async () => {
    window.localStorage.setItem("ij_device_known", JSON.stringify({ "device.theme": "mark2" }));
    html().dataset.theme = "mark2";
    H.row = { ...UNSET, "device.theme": "mark2" };
    H.holdPuts = true;
    const shown = themesShown();
    chooseTheme("mark1");
    chooseTheme("mark23");
    await settle();
    // The first save lands; its config.changed reads the row (mark1) while
    // the second press is still being saved.
    H.puts[0].gate?.release();
    await settle();
    await act(async () => {
      await refreshDeviceSettings();
    });
    expect(html().dataset.theme).toBe("mark23");
    for (const p of H.puts) p.gate?.release();
    await settle(20);
    expect(html().dataset.theme).toBe("mark23");
    expect(shown.filter((t) => t !== "mark1" && t !== "mark23")).toEqual([]);
    // Once the second press shows, it is never put back to the first.
    expect(shown.slice(shown.indexOf("mark23"))).toEqual(shown.slice(shown.indexOf("mark23")).map(() => "mark23"));
    expect(H.row["device.theme"]).toBe("mark23");
  });

  it("read aloud: a read answering before the toggle's save lands keeps the toggle", async () => {
    window.localStorage.setItem("ij_device_known", JSON.stringify({ "device.read_aloud": false }));
    window.localStorage.setItem("ironjarvis.tts.enabled", "1");
    H.row = { ...UNSET, "device.read_aloud": false };
    H.holdPuts = true;
    void writeDeviceSetting("device.read_aloud", true);
    await act(async () => {
      await refreshDeviceSettings();
    });
    expect(window.localStorage.getItem("ironjarvis.tts.enabled")).toBe("1");
  });
});

describe("a change set aside for a save is read again once the save is answered", () => {
  it("the save is refused: the change made meanwhile (from chat) reaches the screen", async () => {
    window.localStorage.setItem("ij_device_known", JSON.stringify({ "device.theme": "mark2" }));
    html().dataset.theme = "mark2";
    H.row = { ...UNSET, "device.theme": "mark2" };
    H.holdPuts = true;
    H.failPuts = true;
    chooseTheme("mark1");
    await settle();
    // Chat sets Gold & Red while the press is still being saved; its read is
    // set aside (the press owns the key until its save is answered).
    H.row["device.theme"] = "mark23";
    await act(async () => {
      await refreshDeviceSettings();
    });
    expect(html().dataset.theme).toBe("mark1");
    // The save is refused (an older daemon, offline): the row's own value
    // is read again and shown.
    H.puts[0].gate?.release();
    await settle(30);
    expect(html().dataset.theme).toBe("mark23");
  });
});

describe("saves go one at a time, in press order", () => {
  it("a second press waits for the first save, so the row ends on the last press", async () => {
    window.localStorage.setItem("ij_device_known", JSON.stringify({ "device.theme": "mark2" }));
    H.row = { ...UNSET, "device.theme": "mark2" };
    H.holdPuts = true;
    chooseTheme("mark1");
    chooseTheme("mark23");
    await settle();
    expect(H.puts.map((p) => p.values)).toEqual([{ "device.theme": "mark1" }]);
    H.puts[0].gate?.release();
    await settle();
    expect(H.puts.map((p) => p.values)).toEqual([{ "device.theme": "mark1" }, { "device.theme": "mark23" }]);
    H.puts[1].gate?.release();
    await settle();
    expect(H.row["device.theme"]).toBe("mark23");
  });
});

describe("upgrade day: what the screen already keeps wins over an older row", () => {
  it("a theme of your own survives the first look, and the row is cleared to match", async () => {
    const p = makePalette({ name: "Grape", accent: "#7c3aed", second: null, surface: "tinted", mode: "dark" });
    expect(upsertPalette(p)).toBe(true);
    window.localStorage.setItem("ij_theme", p.id);
    html().dataset.theme = p.id;
    H.row = { ...UNSET, "device.theme": "mark2" }; // set before this release
    render(<DeviceSettingsBridge />);
    await settle(20);
    expect(html().dataset.theme).toBe(p.id);
    expect(H.puts.map((x) => x.values)).toEqual([{ "device.theme": null }]);
    expect(H.row["device.theme"]).toBeNull();
    // A later chat change to Arc Cyan is now a real change and is applied.
    H.row["device.theme"] = "mark2";
    await act(async () => {
      await refreshDeviceSettings();
    });
    expect(html().dataset.theme).toBe("mark2");
  });

  it("read aloud the person turned off stays off; the row follows the screen", async () => {
    window.localStorage.setItem("ironjarvis.tts.enabled", "0");
    window.localStorage.setItem("ij_chat_auto_tools", "1");
    H.row = { ...UNSET, "device.read_aloud": true, "device.auto_tools": false };
    render(<DeviceSettingsBridge />);
    await settle(20);
    expect(window.localStorage.getItem("ironjarvis.tts.enabled")).toBe("0");
    expect(window.localStorage.getItem("ij_chat_auto_tools")).toBe("1");
    expect(H.puts.map((x) => x.values)).toEqual([{ "device.auto_tools": true, "device.read_aloud": false }]);
  });

  it("a screen that keeps nothing for a key takes the row; a row not set writes nothing", async () => {
    window.localStorage.setItem("ij_theme", "mark8");
    H.row = { ...UNSET, "device.read_aloud": true };
    render(<DeviceSettingsBridge />);
    await settle(20);
    expect(window.localStorage.getItem("ironjarvis.tts.enabled")).toBe("1");
    // The theme row is not set: the screen's own theme is left alone and
    // nothing is written for it.
    expect(window.localStorage.getItem("ij_theme")).toBe("mark8");
    expect(H.puts).toEqual([]);
  });

  it("the second look is an ordinary one: a new row value is applied", async () => {
    window.localStorage.setItem("ij_theme", "mark8");
    html().dataset.theme = "mark8";
    H.row = { ...UNSET, "device.theme": "mark8" };
    render(<DeviceSettingsBridge />);
    await settle(20);
    expect(H.puts).toEqual([]);
    H.row["device.theme"] = "mark1";
    await act(async () => {
      await refreshDeviceSettings();
    });
    expect(html().dataset.theme).toBe("mark1");
  });
});
