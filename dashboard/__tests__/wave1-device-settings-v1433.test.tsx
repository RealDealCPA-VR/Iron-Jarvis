/**
 * v1.433.0 (Wave 1 B, decision D1): this device's settings reach the screen.
 *
 * Settings has four device rows chat's settings tools can change — the
 * theme, the chat persona, Auto tools and read aloud. Until now the screen
 * kept them in browser storage only, so "switch to Daylight" from chat
 * changed a row nothing read. lib/deviceSettings + the layout's
 * DeviceSettingsBridge now read the rows when the window opens and whenever
 * a `config.changed` event names a `device.*` key, and apply what changed;
 * a press on the screen's own control saves the row back.
 *
 * Here: the bridge (events in, theme and read aloud out), the "changed since
 * this window knew it" rule (a stale row never undoes a screen choice), an
 * older daemon's 404, and the screen's presses (theme switcher, a theme of
 * your own, the read-aloud toggle) writing the row. The chat page's persona
 * and Auto tools are in wave1-device-settings-chat-v1433; Settings ›
 * Appearance in wave1-device-settings-appearance-v1433.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    device: {} as Record<string, unknown> | Error,
    gets: [] as string[],
    puts: [] as { path: string; body: { device_id?: string; values?: Record<string, unknown> } }[],
    putFails: false,
    events: [] as { id: string; type: string; session_id: null; ts: string; payload: Record<string, unknown> }[],
    connected: true,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  get: async (path: string) => {
    H.gets.push(path);
    if (path.startsWith("/settings/device")) {
      if (H.device instanceof Error) throw H.device;
      return { device_id: "dev_test", values: { ...H.device } };
    }
    throw new H.FakeApiError(`unmocked GET ${path}`, 500);
  },
  put: async (path: string, body: { device_id?: string; values?: Record<string, unknown> }) => {
    H.puts.push({ path, body });
    if (H.putFails) throw new H.FakeApiError("Not Found", 404);
    return { device_id: body.device_id, values: body.values };
  },
  post: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: H.events, connected: H.connected }),
}));

import { DeviceSettingsBridge } from "@/components/DeviceSettingsBridge";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { chooseTheme, refreshDeviceSettings } from "@/lib/deviceSettings";
import { makePalette, upsertPalette } from "@/lib/themePalette";
import { useTTS } from "@/lib/useTTS";

const ALL_UNSET = {
  "device.theme": null,
  "device.approval_mode": null,
  "device.chat_persona": null,
  "device.auto_tools": null,
  "device.read_aloud": null,
};

let eventSeq = 0;
function configChanged(keys: string[]) {
  eventSeq += 1;
  return {
    id: `ev_${eventSeq}`,
    type: "config.changed",
    session_id: null,
    ts: "2026-11-04T10:00:00Z",
    payload: { keys, actor: "chat", action_id: "" },
  };
}

/** Let every pending read and write settle. */
async function settle() {
  await act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

const html = () => document.documentElement;
const devicePuts = () => H.puts.filter((p) => p.path === "/settings/device");
const deviceGets = () => H.gets.filter((p) => p.startsWith("/settings/device")).length;

function stubSpeech() {
  const synth = {
    getVoices: () => [],
    addEventListener: () => {},
    removeEventListener: () => {},
    cancel: vi.fn(),
    speak: vi.fn(),
    pending: false,
  };
  Object.defineProperty(window, "speechSynthesis", { value: synth, configurable: true });
  (globalThis as unknown as { SpeechSynthesisUtterance: unknown }).SpeechSynthesisUtterance = class {
    constructor(public text: string) {}
  };
  return synth;
}

function TtsProbe() {
  const tts = useTTS();
  return (
    <button type="button" data-testid="tts" aria-pressed={tts.enabled} onClick={tts.toggle}>
      {tts.enabled ? "on" : "off"}
    </button>
  );
}

beforeEach(() => {
  H.device = { ...ALL_UNSET };
  H.gets.length = 0;
  H.puts.length = 0;
  H.putFails = false;
  H.events = [];
  H.connected = true;
  window.localStorage.clear();
  delete html().dataset.theme;
  delete html().dataset.scheme;
  html().removeAttribute("style");
});
afterEach(() => cleanup());

describe("the theme row reaches the screen", () => {
  it("is applied when the window opens", async () => {
    H.device = { ...ALL_UNSET, "device.theme": "mark1" };
    render(<DeviceSettingsBridge />);
    await waitFor(() => expect(html().dataset.theme).toBe("mark1"));
    expect(window.localStorage.getItem("ij_theme")).toBe("mark1");
    expect(html().dataset.scheme).toBe("light");
  });

  it("a config.changed naming device.theme (chat set it) applies the new theme live", async () => {
    H.device = { ...ALL_UNSET, "device.theme": "mark2" };
    const view = render(<DeviceSettingsBridge />);
    await waitFor(() => expect(html().dataset.theme).toBe("mark2"));
    H.device = { ...ALL_UNSET, "device.theme": "mark23" };
    H.events = [configChanged(["device.theme"])];
    view.rerender(<DeviceSettingsBridge />);
    await waitFor(() => expect(html().dataset.theme).toBe("mark23"));
    expect(window.localStorage.getItem("ij_theme")).toBe("mark23");
  });

  it("an event about another setting reads nothing", async () => {
    const view = render(<DeviceSettingsBridge />);
    await settle();
    const before = deviceGets();
    H.events = [configChanged(["default_model", "max_agent_steps"])];
    view.rerender(<DeviceSettingsBridge />);
    await settle();
    expect(deviceGets()).toBe(before);
  });

  it("an event already handled is never read again, however many come after it", async () => {
    const view = render(<DeviceSettingsBridge />);
    await settle();
    const before = deviceGets();
    const ev1 = configChanged(["device.theme"]);
    H.events = [ev1];
    view.rerender(<DeviceSettingsBridge />);
    await settle();
    // The same window again (a re-render with no new frame).
    H.events = [ev1];
    view.rerender(<DeviceSettingsBridge />);
    await settle();
    // Newer frames about other settings, the handled one still behind them.
    const ev2 = configChanged(["default_model"]);
    H.events = [ev2, ev1];
    view.rerender(<DeviceSettingsBridge />);
    await settle();
    const ev3 = configChanged(["max_agent_steps"]);
    H.events = [ev3, ev2, ev1];
    view.rerender(<DeviceSettingsBridge />);
    await settle();
    expect(deviceGets()).toBe(before + 1);
  });

  it("an id that is not a built-in theme is ignored", async () => {
    html().dataset.theme = "mark8";
    H.device = { ...ALL_UNSET, "device.theme": "neon-dreams" };
    render(<DeviceSettingsBridge />);
    await settle();
    expect(deviceGets()).toBeGreaterThan(0);
    expect(html().dataset.theme).toBe("mark8");
  });

  it("a row naming a theme of your own is ignored too (the row holds built-in themes only)", async () => {
    const p = makePalette({ name: "Grape", accent: "#7c3aed", second: null, surface: "tinted", mode: "dark" });
    expect(upsertPalette(p)).toBe(true);
    html().dataset.theme = "mark8";
    H.device = { ...ALL_UNSET, "device.theme": p.id };
    render(<DeviceSettingsBridge />);
    await settle();
    expect(deviceGets()).toBeGreaterThan(0);
    expect(html().dataset.theme).toBe("mark8");
  });

  it("a row that still holds what this device last knew never undoes a theme of your own", async () => {
    // The user picked a palette of their own on screen (a screen-only
    // choice); the row still names the Mark this device last saw.
    window.localStorage.setItem("ij_device_known", JSON.stringify({ "device.theme": "mark2" }));
    html().dataset.theme = "custom-abc123";
    H.device = { ...ALL_UNSET, "device.theme": "mark2" };
    render(<DeviceSettingsBridge />);
    await settle();
    expect(deviceGets()).toBeGreaterThan(0);
    expect(html().dataset.theme).toBe("custom-abc123");
  });

  it("an older daemon's 404 changes nothing on screen", async () => {
    html().dataset.theme = "mark8";
    window.localStorage.setItem("ij_theme", "mark8");
    window.localStorage.setItem("ironjarvis.tts.enabled", "0");
    H.device = new H.FakeApiError("Not Found", 404);
    render(<DeviceSettingsBridge />);
    await settle();
    expect(deviceGets()).toBeGreaterThan(0);
    expect(html().dataset.theme).toBe("mark8");
    expect(window.localStorage.getItem("ij_theme")).toBe("mark8");
    expect(window.localStorage.getItem("ironjarvis.tts.enabled")).toBe("0");
    expect(window.localStorage.getItem("ij_device_known")).toBeNull();
    expect(H.puts).toEqual([]);
  });

  it("reads again when the events socket comes back after a drop", async () => {
    const view = render(<DeviceSettingsBridge />);
    await settle();
    const before = deviceGets();
    H.connected = false;
    view.rerender(<DeviceSettingsBridge />);
    await settle();
    H.device = { ...ALL_UNSET, "device.theme": "mark29" };
    H.connected = true;
    view.rerender(<DeviceSettingsBridge />);
    await waitFor(() => expect(html().dataset.theme).toBe("mark29"));
    expect(deviceGets()).toBe(before + 1);
  });
});

describe("a theme picked on screen saves the row", () => {
  it("the theme switcher PUTs the built-in theme it applied", async () => {
    render(<ThemeSwitcher variant="drawer" />);
    fireEvent.click(screen.getByRole("button", { name: /Daylight/ }));
    expect(html().dataset.theme).toBe("mark1");
    await settle();
    expect(devicePuts().map((p) => p.body.values)).toEqual([{ "device.theme": "mark1" }]);
    expect(typeof devicePuts()[0].body.device_id).toBe("string");
  });

  it("a theme of your own clears the row (it holds built-in themes only)", async () => {
    const p = makePalette({ name: "Grape", accent: "#7c3aed", second: null, surface: "tinted", mode: "dark" });
    expect(upsertPalette(p)).toBe(true);
    chooseTheme(p.id);
    expect(html().dataset.theme).toBe(p.id);
    await settle();
    expect(devicePuts().map((x) => x.body.values)).toEqual([{ "device.theme": null }]);
  });

  it("a press the daemon refused does not let the old row undo it later", async () => {
    H.device = { ...ALL_UNSET, "device.theme": "mark2" };
    const view = render(<DeviceSettingsBridge />);
    await waitFor(() => expect(html().dataset.theme).toBe("mark2"));
    H.putFails = true;
    chooseTheme("mark1");
    await settle();
    expect(html().dataset.theme).toBe("mark1");
    // Another device key changes (the row for the theme still says mark2).
    H.events = [configChanged(["device.read_aloud"])];
    view.rerender(<DeviceSettingsBridge />);
    await settle();
    await act(async () => {
      await refreshDeviceSettings();
    });
    expect(html().dataset.theme).toBe("mark1");
  });
});

describe("read aloud follows the device row, and the toggle saves it", () => {
  it("a chat change turns spoken replies on, and off again", async () => {
    stubSpeech();
    H.device = { ...ALL_UNSET, "device.read_aloud": true };
    const view = render(
      <>
        <DeviceSettingsBridge />
        <TtsProbe />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("tts").textContent).toBe("on"));
    expect(window.localStorage.getItem("ironjarvis.tts.enabled")).toBe("1");
    H.device = { ...ALL_UNSET, "device.read_aloud": false };
    H.events = [configChanged(["device.read_aloud"])];
    view.rerender(
      <>
        <DeviceSettingsBridge />
        <TtsProbe />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("tts").textContent).toBe("off"));
    expect(window.localStorage.getItem("ironjarvis.tts.enabled")).toBe("0");
    // Nothing the bridge applied was written back.
    expect(devicePuts()).toEqual([]);
  });

  it("the toggle PUTs the row", async () => {
    stubSpeech();
    render(<TtsProbe />);
    await waitFor(() => expect(screen.getByTestId("tts").textContent).toBe("off"));
    fireEvent.click(screen.getByTestId("tts"));
    expect(screen.getByTestId("tts").textContent).toBe("on");
    fireEvent.click(screen.getByTestId("tts"));
    await settle();
    expect(devicePuts().map((p) => p.body.values)).toEqual([
      { "device.read_aloud": true },
      { "device.read_aloud": false },
    ]);
  });
});
