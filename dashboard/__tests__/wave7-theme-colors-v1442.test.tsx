/**
 * v1.442.0 (chat-first plan, Wave 7 group C): a theme of your own colours,
 * set from chat ("Change the theme to something green and calm").
 *
 * `device.theme_colors` holds the theme maker's picks; the bridge
 * (components/DeviceSettingsBridge → lib/deviceSettings) builds the theme with
 * the theme maker's OWN generator (lib/themePalette makePalette →
 * generatePalette), keeps it among this device's themes under one id
 * (CHAT_PALETTE_ID: replaced, never piled up, never taking one of the user's
 * own places), and paints it. The daemon's writer clears `device.theme` when
 * colours are set and the colours when a built-in theme is set
 * (settings/theme_colors.LINKED), so the one set LAST is what the screen
 * shows; this file's fake daemon applies the same rule.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  row: {} as Record<string, unknown>,
  puts: [] as { device_id?: string; values: Record<string, unknown> }[],
  events: [] as { id: string; type: string; session_id: null; ts: string; payload: Record<string, unknown> }[],
}));

const LINKED: Record<string, string> = {
  "device.theme": "device.theme_colors",
  "device.theme_colors": "device.theme",
};

/** The daemon's writer, for these two rows: a value for one clears the other. */
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
  put: async (_path: string, body: { device_id?: string; values: Record<string, unknown> }) => {
    H.puts.push(body);
    daemonApply(body.values);
    return { values: { ...H.row } };
  },
  post: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: H.events, connected: true }),
}));

import { DeviceSettingsBridge } from "@/components/DeviceSettingsBridge";
import {
  CHAT_THEME_NAME,
  THEME_BEFORE_CHAT_KEY,
  applyDeviceValues,
  chooseTheme,
  themeColorsFrom,
} from "@/lib/deviceSettings";
import { deviceSettingsState } from "@/lib/deviceSettingsState";
import { PRE_PAINT_SCRIPT, applyTheme } from "@/lib/theme";
import {
  CHAT_PALETTE_ID,
  MAX_PALETTES,
  PALETTES_EVENT,
  generatePalette,
  loadPalettes,
  makePalette,
  ownPaletteCount,
  readabilityProblems,
  upsertPalette,
} from "@/lib/themePalette";

const UNSET = {
  "device.theme": null,
  "device.theme_colors": null,
  "device.approval_mode": null,
  "device.chat_persona": null,
  "device.auto_tools": null,
  "device.read_aloud": null,
};
const GREEN = { accent: "#2f855a", surface: "tinted", mode: "dark", name: "Green and calm" } as const;
const PURPLE = { accent: "#7c3aed", second: "#1e1b4b", surface: "cool", mode: "light" } as const;

let seq = 0;
function changed(keys: string[]) {
  seq += 1;
  return { id: `ev_w7_${seq}`, type: "config.changed", session_id: null, ts: "2026-11-06T10:00:00Z", payload: { keys } };
}

/** Chat changes rows through the daemon, then the bridge hears it. */
async function fromChat(view: ReturnType<typeof render>, values: Record<string, unknown>) {
  daemonApply(values);
  H.events = [changed(Object.keys(values))];
  view.rerender(<DeviceSettingsBridge />);
  await settle();
}

async function settle() {
  await act(async () => {
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  });
}

const html = () => document.documentElement;
const chatPalette = () => loadPalettes().find((p) => p.id === CHAT_PALETTE_ID);

beforeEach(() => {
  H.row = { ...UNSET };
  H.puts.length = 0;
  H.events = [];
  window.localStorage.clear();
  delete html().dataset.theme;
  delete html().dataset.scheme;
  html().removeAttribute("style");
});
afterEach(() => cleanup());

describe("chat's colours reach the screen", () => {
  it("builds the theme with the theme maker's own generator and paints it", async () => {
    H.row = { ...UNSET, "device.theme": "mark2" };
    const view = render(<DeviceSettingsBridge />);
    await waitFor(() => expect(html().dataset.theme).toBe("mark2"));
    await fromChat(view, { "device.theme_colors": GREEN });
    await waitFor(() => expect(html().dataset.theme).toBe(CHAT_PALETTE_ID));
    const p = chatPalette()!;
    expect(p.name).toBe("Green and calm");
    expect(p.accent).toBe("#2f855a");
    // The very same vars the theme maker would make from these picks.
    expect(p.vars).toEqual(generatePalette({ accent: "#2f855a", second: null, surface: "tinted" }).vars);
    expect(html().dataset.scheme).toBe("dark");
    expect(html().style.getPropertyValue("--accent-rgb")).toBe(p.vars.dark["accent-rgb"]);
    expect(window.localStorage.getItem("ij_theme")).toBe(CHAT_PALETTE_ID);
    expect(window.localStorage.getItem(THEME_BEFORE_CHAT_KEY)).toBe("mark2");
    // Nothing is written back: the change came FROM the row.
    expect(H.puts).toEqual([]);
  });

  it("a second set of colours REPLACES the chat theme (one palette, never piled up)", async () => {
    const view = render(<DeviceSettingsBridge />);
    await settle();
    await fromChat(view, { "device.theme_colors": GREEN });
    await waitFor(() => expect(chatPalette()?.accent).toBe("#2f855a"));
    await fromChat(view, { "device.theme_colors": PURPLE });
    await waitFor(() => expect(chatPalette()?.accent).toBe("#7c3aed"));
    expect(loadPalettes()).toHaveLength(1);
    expect(chatPalette()!.name).toBe(CHAT_THEME_NAME); // no name given
    expect(chatPalette()!.second).toBe("#1e1b4b");
    expect(html().dataset.scheme).toBe("light");
    expect(html().style.getPropertyValue("--accent-rgb")).toBe(chatPalette()!.vars.light["accent-rgb"]);
  });

  it("a value that is no set of colours changes nothing", async () => {
    H.row = { ...UNSET, "device.theme": "mark8" };
    const view = render(<DeviceSettingsBridge />);
    await waitFor(() => expect(html().dataset.theme).toBe("mark8"));
    await fromChat(view, { "device.theme_colors": { accent: "green" } });
    expect(html().dataset.theme).toBe("mark8");
    expect(chatPalette()).toBeUndefined();
  });

  it("the same colours read back in another key order are not applied again", async () => {
    const view = render(<DeviceSettingsBridge />);
    await settle();
    await fromChat(view, { "device.theme_colors": GREEN });
    await waitFor(() => expect(html().dataset.theme).toBe(CHAT_PALETTE_ID));
    let writes = 0;
    const count = () => (writes += 1);
    window.addEventListener(PALETTES_EVENT, count);
    try {
      H.row = { ...H.row, "device.theme_colors": { name: "Green and calm", mode: "dark", surface: "tinted", accent: "#2f855a" } };
      H.events = [changed(["device.theme_colors"])];
      view.rerender(<DeviceSettingsBridge />);
      await settle();
      expect(writes).toBe(0);
    } finally {
      window.removeEventListener(PALETTES_EVENT, count);
    }
  });
});

describe("the one set last is what the screen shows", () => {
  it("a built-in theme from chat takes over from the colours, and colours take over again", async () => {
    const view = render(<DeviceSettingsBridge />);
    await settle();
    await fromChat(view, { "device.theme_colors": GREEN });
    await waitFor(() => expect(html().dataset.theme).toBe(CHAT_PALETTE_ID));
    await fromChat(view, { "device.theme": "mark23" });
    expect(H.row["device.theme_colors"]).toBeNull(); // the daemon's rule
    await waitFor(() => expect(html().dataset.theme).toBe("mark23"));
    expect(html().style.getPropertyValue("--accent-rgb")).toBe(""); // the chat theme's vars are gone
    await fromChat(view, { "device.theme_colors": PURPLE });
    expect(H.row["device.theme"]).toBeNull();
    await waitFor(() => expect(html().dataset.theme).toBe(CHAT_PALETTE_ID));
  });

  it("a built-in theme picked on screen clears chat's colours on the row", async () => {
    const view = render(<DeviceSettingsBridge />);
    await settle();
    await fromChat(view, { "device.theme_colors": GREEN });
    await waitFor(() => expect(html().dataset.theme).toBe(CHAT_PALETTE_ID));
    chooseTheme("mark1");
    await settle();
    expect(H.puts.map((p) => p.values)).toEqual([{ "device.theme": "mark1", "device.theme_colors": null }]);
    expect(H.row["device.theme_colors"]).toBeNull();
    // The bridge does not mistake its own save for someone else's change.
    H.events = [changed(["device.theme", "device.theme_colors"])];
    view.rerender(<DeviceSettingsBridge />);
    await settle();
    expect(html().dataset.theme).toBe("mark1");
  });

  it("picking (or editing) the chat theme on screen saves its picks as the colours", async () => {
    H.row = { ...UNSET, "device.theme": "mark2" };
    render(<DeviceSettingsBridge />);
    await waitFor(() => expect(html().dataset.theme).toBe("mark2"));
    expect(upsertPalette(makePalette({ id: CHAT_PALETTE_ID, name: "Sea", accent: "#0F766E", second: null, surface: "cool", mode: "system" }))).toBe(true);
    chooseTheme(CHAT_PALETTE_ID);
    await settle();
    expect(H.puts.map((p) => p.values)).toEqual([
      { "device.theme": null, "device.theme_colors": { accent: "#0f766e", surface: "cool", mode: "system", name: "Sea" } },
    ]);
  });

  it("another theme of your own clears both rows", async () => {
    const view = render(<DeviceSettingsBridge />);
    await settle();
    await fromChat(view, { "device.theme_colors": GREEN });
    await waitFor(() => expect(html().dataset.theme).toBe(CHAT_PALETTE_ID));
    const grape = makePalette({ name: "Grape", accent: "#7c3aed", second: null, surface: "tinted", mode: "dark" });
    expect(upsertPalette(grape)).toBe(true);
    chooseTheme(grape.id);
    await settle();
    expect(H.puts.map((p) => p.values)).toEqual([{ "device.theme": null, "device.theme_colors": null }]);
  });

  it("with no colours known, a built-in pick PUTs exactly what it did before", async () => {
    render(<DeviceSettingsBridge />);
    await settle();
    chooseTheme("mark8");
    await settle();
    expect(H.puts.map((p) => p.values)).toEqual([{ "device.theme": "mark8" }]);
  });
});

describe("Undo puts the screen back", () => {
  it("back to the built-in theme the row held before", async () => {
    H.row = { ...UNSET, "device.theme": "mark29" };
    const view = render(<DeviceSettingsBridge />);
    await waitFor(() => expect(html().dataset.theme).toBe("mark29"));
    await fromChat(view, { "device.theme_colors": GREEN });
    await waitFor(() => expect(html().dataset.theme).toBe(CHAT_PALETTE_ID));
    // The setting card's Undo restores the snapshot of BOTH rows.
    await fromChat(view, { "device.theme_colors": null, "device.theme": "mark29" });
    await waitFor(() => expect(html().dataset.theme).toBe("mark29"));
    expect(html().style.getPropertyValue("--accent-rgb")).toBe("");
  });

  it("back to the user's own theme when no built-in theme was set", async () => {
    const mine = makePalette({ name: "Mine", accent: "#ea580c", second: null, surface: "warm", mode: "dark" });
    expect(upsertPalette(mine)).toBe(true);
    applyTheme(mine.id);
    const view = render(<DeviceSettingsBridge />);
    await settle();
    expect(html().dataset.theme).toBe(mine.id);
    await fromChat(view, { "device.theme_colors": GREEN });
    await waitFor(() => expect(html().dataset.theme).toBe(CHAT_PALETTE_ID));
    expect(window.localStorage.getItem(THEME_BEFORE_CHAT_KEY)).toBe(mine.id);
    await fromChat(view, { "device.theme_colors": null });
    await waitFor(() => expect(html().dataset.theme).toBe(mine.id));
    expect(html().style.getPropertyValue("--accent-rgb")).toBe(mine.vars.dark["accent-rgb"]);
  });

  it("back to the default when the theme before is gone", async () => {
    const view = render(<DeviceSettingsBridge />);
    await settle();
    window.localStorage.setItem("ij_theme", "custom-deleted1");
    html().dataset.theme = "custom-deleted1";
    await fromChat(view, { "device.theme_colors": GREEN });
    await waitFor(() => expect(html().dataset.theme).toBe(CHAT_PALETTE_ID));
    await fromChat(view, { "device.theme_colors": null });
    await waitFor(() => expect(html().dataset.theme).toBe("mark2"));
  });

  it("taking the colours away while another theme is on screen leaves it alone", async () => {
    const view = render(<DeviceSettingsBridge />);
    await settle();
    await fromChat(view, { "device.theme_colors": GREEN });
    await waitFor(() => expect(html().dataset.theme).toBe(CHAT_PALETTE_ID));
    applyTheme("mark8"); // another window's pick reached this one first
    await fromChat(view, { "device.theme_colors": null });
    expect(html().dataset.theme).toBe("mark8");
  });
});

describe("a reload keeps it", () => {
  it("the pre-paint script restores the chat theme, and the next window changes nothing", async () => {
    const view = render(<DeviceSettingsBridge />);
    await settle();
    await fromChat(view, { "device.theme_colors": GREEN });
    await waitFor(() => expect(html().dataset.theme).toBe(CHAT_PALETTE_ID));
    const vars = chatPalette()!.vars.dark;
    view.unmount();
    // A reload: the DOM is fresh, browser storage stays.
    delete html().dataset.theme;
    delete html().dataset.scheme;
    html().removeAttribute("style");
    new Function(PRE_PAINT_SCRIPT)();
    expect(html().dataset.theme).toBe(CHAT_PALETTE_ID);
    expect(html().style.getPropertyValue("--accent-rgb")).toBe(vars["accent-rgb"]);
    // The new window's memory starts empty; what it kept between visits does not.
    deviceSettingsState.known = null;
    deviceSettingsState.inFlight = null;
    H.puts.length = 0;
    let writes = 0;
    const count = () => (writes += 1);
    window.addEventListener(PALETTES_EVENT, count);
    try {
      render(<DeviceSettingsBridge />);
      await settle();
      expect(html().dataset.theme).toBe(CHAT_PALETTE_ID);
      expect(writes).toBe(0);
      expect(H.puts).toEqual([]);
    } finally {
      window.removeEventListener(PALETTES_EVENT, count);
    }
  });
});

describe("readable, and never in the user's way", () => {
  const PICKS: [string, string][] = [
    ["#2f855a", "tinted"],
    ["#16a34a", "neutral"],
    ["#84cc16", "warm"],
    ["#a7f3d0", "cool"],
    ["#14532d", "tinted"],
    ["#7c3aed", "cool"],
    ["#a855f7", "tinted"],
    ["#e9d5ff", "neutral"],
    ["#3b0764", "warm"],
  ];
  it.each(PICKS)("%s on a %s background reads well in both versions", (accent, surface) => {
    applyDeviceValues({ ...UNSET, "device.theme_colors": { accent, surface, mode: "dark" } });
    const p = chatPalette()!;
    expect(p.accent).toBe(accent);
    expect(readabilityProblems(p.vars.dark, "dark")).toEqual([]);
    expect(readabilityProblems(p.vars.light, "light")).toEqual([]);
  });

  it("the chat theme never takes one of the user's own places", () => {
    for (let i = 0; i < MAX_PALETTES; i += 1) {
      expect(upsertPalette(makePalette({ name: `T${i}`, accent: "#22d3ee", second: null, surface: "cool", mode: "dark" }))).toBe(true);
    }
    applyDeviceValues({ ...UNSET, "device.theme_colors": GREEN });
    expect(html().dataset.theme).toBe(CHAT_PALETTE_ID);
    expect(ownPaletteCount(loadPalettes())).toBe(MAX_PALETTES);
    expect(loadPalettes()).toHaveLength(MAX_PALETTES + 1);
    // …and a thirteenth of their own is still refused.
    expect(upsertPalette(makePalette({ name: "One more", accent: "#000000", second: null, surface: "cool", mode: "dark" }))).toBe(false);
  });

  it("reads the picks the daemon's way", () => {
    expect(themeColorsFrom({ accent: "0F0", surface: "glossy", mode: "night", name: " a\nb " })).toEqual({
      accent: "#00ff00",
      surface: "neutral",
      mode: "dark",
      name: "a b",
    });
    expect(themeColorsFrom({ accent: "green" })).toBeNull();
    expect(themeColorsFrom("#00ff00")).toBeNull();
  });
});
