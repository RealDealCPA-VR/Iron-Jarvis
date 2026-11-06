/**
 * v1.442.0 (Wave 7 group C) REVIEW: the two theme rows are ONE choice, so
 * the device-settings rules (lib/deviceSettings: "a save not yet answered
 * owns its key") must hold for the PAIR.
 *
 * Red on 19e2e474:
 *  - a built-in theme picked on screen while chat set colours: the read that
 *    answered before the press's save landed painted chat's colours (the
 *    colours row was not "pending"), and once the save landed and cleared
 *    the colours, the built-in theme was never painted back — the row said
 *    Daylight, the screen showed the chat theme;
 *  - a read with a built-in theme and no colours left a stale chat theme on
 *    screen when this window already knew that built-in theme;
 *  - another window picking one of the user's own themes made this window
 *    leave the chat theme for the theme shown BEFORE chat, and write that
 *    over the other window's pick in the shared storage.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "@testing-library/react";

const H = vi.hoisted(() => ({
  row: {} as Record<string, unknown>,
  puts: [] as { values: Record<string, unknown> }[],
  gate: null as Promise<void> | null,
}));

const LINKED: Record<string, string> = {
  "device.theme": "device.theme_colors",
  "device.theme_colors": "device.theme",
};

function daemonApply(values: Record<string, unknown>) {
  const next = { ...H.row, ...values };
  for (const [k, v] of Object.entries(values)) {
    const partner = LINKED[k];
    if (partner && v !== null && !(partner in values)) next[partner] = null;
  }
  H.row = next;
}

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  API_BASE: "",
  get: async (path: string) => {
    if (path.startsWith("/settings/device")) return { device_id: "dev_test", values: { ...H.row } };
    throw new Error(`unmocked GET ${path}`);
  },
  put: async (_path: string, body: { values: Record<string, unknown> }) => {
    if (H.gate) await H.gate;
    H.puts.push(body);
    daemonApply(body.values);
    return { values: { ...H.row } };
  },
  post: async () => ({}),
}));

import {
  THEME_BEFORE_CHAT_KEY,
  __resetDeviceSettings,
  applyDeviceValues,
  chooseTheme,
  refreshDeviceSettings,
} from "@/lib/deviceSettings";
import { CHAT_PALETTE_ID, loadPalettes, makePalette, upsertPalette } from "@/lib/themePalette";
import { applyTheme } from "@/lib/theme";
import { deviceSettingsState } from "@/lib/deviceSettingsState";

const UNSET = {
  "device.theme": null,
  "device.theme_colors": null,
  "device.approval_mode": null,
  "device.chat_persona": null,
  "device.auto_tools": null,
  "device.read_aloud": null,
};
const GREEN = { accent: "#2f855a", surface: "tinted", mode: "dark", name: "Green and calm" } as const;
const html = () => document.documentElement;

async function settle() {
  await act(async () => {
    for (let i = 0; i < 30; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  __resetDeviceSettings();
  H.row = { ...UNSET };
  H.puts.length = 0;
  H.gate = null;
  window.localStorage.clear();
  delete html().dataset.theme;
  delete html().dataset.scheme;
  html().removeAttribute("style");
});

describe("a screen pick racing chat's colours", () => {
  it("the pick's save owns BOTH theme rows until it is answered", async () => {
    H.row = { ...UNSET, "device.theme": "mark2" };
    await refreshDeviceSettings();
    await settle();
    expect(html().dataset.theme).toBe("mark2");

    let release!: () => void;
    H.gate = new Promise<void>((r) => (release = r));
    chooseTheme("mark1"); // the person picks Daylight; its save is out
    expect(html().dataset.theme).toBe("mark1");

    // Chat sets colours; this window's read answers BEFORE the pick lands.
    daemonApply({ "device.theme_colors": GREEN });
    await refreshDeviceSettings();
    await settle();
    // The pick is not answered yet: the read must not paint over it.
    expect(html().dataset.theme).toBe("mark1");

    // The pick lands LAST (the writer clears the colours): Daylight wins.
    release();
    H.gate = null;
    await settle();
    await refreshDeviceSettings();
    await settle();
    expect(H.row["device.theme"]).toBe("mark1");
    expect(H.row["device.theme_colors"]).toBe(null);
    expect(html().dataset.theme).toBe("mark1");
  });

  it("chat's colours set AFTER the pick landed still reach the screen", async () => {
    H.row = { ...UNSET, "device.theme": "mark2" };
    await refreshDeviceSettings();
    await settle();
    let release!: () => void;
    H.gate = new Promise<void>((r) => (release = r));
    chooseTheme("mark1");
    release();
    H.gate = null;
    await settle();
    // Chat's change lands after the pick: the colours are the last set.
    daemonApply({ "device.theme_colors": GREEN });
    await refreshDeviceSettings();
    await settle();
    expect(html().dataset.theme).toBe(CHAT_PALETTE_ID);
  });
});

describe("a stale chat theme on screen", () => {
  it("a built-in theme on the row with the colours gone paints that theme", async () => {
    // This window painted chat's colours, and already knew Daylight (a read
    // it took while a pick of Daylight was out).
    H.row = { ...UNSET, "device.theme_colors": GREEN };
    await refreshDeviceSettings();
    await settle();
    expect(html().dataset.theme).toBe(CHAT_PALETTE_ID);
    deviceSettingsState.known!["device.theme"] = "mark1";
    applyDeviceValues({ ...UNSET, "device.theme": "mark1", "device.theme_colors": null });
    expect(html().dataset.theme).toBe("mark1");
  });
});

describe("another window's own theme", () => {
  it("leaving the chat theme goes to the theme another window just picked", async () => {
    upsertPalette(makePalette({ id: "custom-mine", name: "Mine", accent: "#d97706", surface: "warm", mode: "dark" }));
    H.row = { ...UNSET, "device.theme": "mark2" };
    await refreshDeviceSettings();
    await settle();
    daemonApply({ "device.theme_colors": GREEN });
    await refreshDeviceSettings();
    await settle();
    expect(html().dataset.theme).toBe(CHAT_PALETTE_ID);
    expect(window.localStorage.getItem(THEME_BEFORE_CHAT_KEY)).toBe("mark2");

    // Another window of this device picks the user's own theme: it writes the
    // shared storage and clears both rows.
    window.localStorage.setItem("ij_theme", "custom-mine");
    daemonApply({ "device.theme": null, "device.theme_colors": null });
    await refreshDeviceSettings();
    await settle();
    expect(html().dataset.theme).toBe("custom-mine");
    expect(window.localStorage.getItem("ij_theme")).toBe("custom-mine");
  });

  it("an Undo in this window still goes back to the theme before chat", async () => {
    upsertPalette(makePalette({ id: "custom-mine", name: "Mine", accent: "#d97706", surface: "warm", mode: "dark" }));
    await refreshDeviceSettings();
    await settle();
    applyTheme("custom-mine"); // the user's own theme, a screen-only choice
    daemonApply({ "device.theme_colors": GREEN });
    await refreshDeviceSettings();
    await settle();
    daemonApply({ "device.theme": null, "device.theme_colors": null });
    await refreshDeviceSettings();
    await settle();
    expect(html().dataset.theme).toBe("custom-mine");
  });
});

describe("a background the person chose stays with the chat theme", () => {
  it("new colours from chat keep the chat theme's background", async () => {
    upsertPalette(
      makePalette({
        id: CHAT_PALETTE_ID, name: "Theme from chat", accent: "#2f855a", surface: "tinted", mode: "dark",
        background: { id: "aurora", motion: "still" },
      }),
    );
    daemonApply({ "device.theme_colors": { accent: "#7c3aed", surface: "cool", mode: "dark" } });
    await refreshDeviceSettings();
    await settle();
    const p = loadPalettes().find((x) => x.id === CHAT_PALETTE_ID)!;
    expect(p.accent).toBe("#7c3aed");
    expect(p.background).toEqual({ id: "aurora", motion: "still" });
  });
});
