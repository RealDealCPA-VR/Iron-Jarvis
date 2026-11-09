/**
 * v1.456.0: TEXT SIZE across the app (the owner: "change font sizes across the
 * app in settings").
 *
 * `device.text_size` is this device's row (settings/schema.py). Here: a row
 * set from chat or Settings reaches the desktop shell's zoom bridge; a size
 * off the list is ignored; the shell's own keys (Ctrl + = / - / 0, heard as
 * `textSize.onChanged`) are saved back as the row; the window hands its kept
 * size to the shell when it opens; and a browser tab (no bridge) still saves
 * the row and never throws.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";

const H = vi.hoisted(() => ({
  device: {} as Record<string, unknown>,
  puts: [] as { path: string; body: { device_id?: string; values?: Record<string, unknown> } }[],
  events: [] as { id: string; type: string; session_id: null; ts: string; payload: Record<string, unknown> }[],
}));

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  API_BASE: "",
  get: async (path: string) => {
    if (path.startsWith("/settings/device")) return { device_id: "dev_test", values: { ...H.device } };
    throw new Error(`unmocked GET ${path}`);
  },
  put: async (path: string, body: { device_id?: string; values?: Record<string, unknown> }) => {
    H.puts.push({ path, body });
    return { device_id: body.device_id, values: body.values };
  },
  post: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: H.events, connected: true }),
}));

import { DeviceSettingsBridge } from "@/components/DeviceSettingsBridge";
import { DEVICE_TEXT_SIZE_KEY, refreshDeviceSettings } from "@/lib/deviceSettings";
import { TEXT_SIZES, TEXT_SIZE_STORAGE_KEY, textSizeFrom } from "@/lib/textSize";

const UNSET = {
  "device.theme": null,
  "device.chat_persona": null,
  "device.auto_tools": null,
  "device.read_aloud": null,
  "device.text_size": null,
};

type Listener = (pct: number) => void;
let sets: number[] = [];
let listeners: Listener[] = [];

function installBridge() {
  sets = [];
  listeners = [];
  (window as unknown as { ironjarvis?: unknown }).ironjarvis = {
    textSize: {
      set: async (pct: number) => {
        sets.push(pct);
        return true;
      },
      get: async () => 100,
      onChanged: (cb: Listener) => {
        listeners.push(cb);
        return () => {
          listeners = listeners.filter((l) => l !== cb);
        };
      },
    },
  };
}

async function settle() {
  await act(async () => {
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  });
}

let eventSeq = 0;
function configChanged(keys: string[]) {
  eventSeq += 1;
  return {
    id: `ev_ts_${eventSeq}`,
    type: "config.changed",
    session_id: null,
    ts: "2026-11-08T10:00:00Z",
    payload: { keys, actor: "chat", action_id: "" },
  };
}

const devicePuts = () => H.puts.filter((p) => p.path === "/settings/device");

beforeEach(() => {
  H.device = { ...UNSET };
  H.puts.length = 0;
  H.events = [];
  window.localStorage.clear();
  installBridge();
});
afterEach(() => {
  cleanup();
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
});

describe("the sizes", () => {
  it("are the five the daemon offers, and nothing else reads as one", () => {
    expect([...TEXT_SIZES]).toEqual([90, 100, 110, 125, 150]);
    expect(textSizeFrom("125")).toBe(125);
    expect(textSizeFrom(110)).toBe(110);
    for (const bad of ["300", "", "big", null, undefined, 99.5, {}]) expect(textSizeFrom(bad)).toBeNull();
  });
});

describe("a row set from chat or Settings reaches the shell", () => {
  it("applies a new size through the bridge and keeps it for the window", async () => {
    render(<DeviceSettingsBridge />);
    await settle();
    expect(sets).toEqual([]); // nothing chosen yet: the shell keeps its own

    H.device = { ...UNSET, [DEVICE_TEXT_SIZE_KEY]: "125" };
    await act(async () => {
      await refreshDeviceSettings();
    });
    expect(sets).toEqual([125]);
    expect(window.localStorage.getItem(TEXT_SIZE_STORAGE_KEY)).toBe("125");
  });

  it("follows a config.changed event naming the row", async () => {
    const view = render(<DeviceSettingsBridge />);
    await settle();
    H.device = { ...UNSET, [DEVICE_TEXT_SIZE_KEY]: "90" };
    H.events = [configChanged([DEVICE_TEXT_SIZE_KEY])];
    view.rerender(<DeviceSettingsBridge />);
    await settle();
    expect(sets).toEqual([90]);
  });

  it("ignores a size that is not on the list", async () => {
    render(<DeviceSettingsBridge />);
    await settle();
    H.device = { ...UNSET, [DEVICE_TEXT_SIZE_KEY]: "300" };
    await act(async () => {
      await refreshDeviceSettings();
    });
    expect(sets).toEqual([]);
    expect(window.localStorage.getItem(TEXT_SIZE_STORAGE_KEY)).toBeNull();
  });
});

describe("the shell's own keys are saved as the row", () => {
  it("saves a size the shell chose (Ctrl + = / - / 0) for this device", async () => {
    render(<DeviceSettingsBridge />);
    await settle();
    expect(listeners).toHaveLength(1);
    await act(async () => {
      listeners[0](110);
    });
    await settle();
    const put = devicePuts().at(-1);
    expect(put?.body.values).toEqual({ [DEVICE_TEXT_SIZE_KEY]: "110" });
    expect(window.localStorage.getItem(TEXT_SIZE_STORAGE_KEY)).toBe("110");
  });

  it("never saves a size off the list the shell might send", async () => {
    render(<DeviceSettingsBridge />);
    await settle();
    await act(async () => {
      listeners[0](333);
    });
    await settle();
    expect(devicePuts()).toEqual([]);
  });

  it("stops listening when the window goes", async () => {
    const view = render(<DeviceSettingsBridge />);
    await settle();
    view.unmount();
    expect(listeners).toHaveLength(0);
  });
});

describe("opening a window", () => {
  it("hands the shell the size this window last chose", async () => {
    window.localStorage.setItem(TEXT_SIZE_STORAGE_KEY, "150");
    render(<DeviceSettingsBridge />);
    await settle();
    expect(sets[0]).toBe(150);
  });

  it("works in a browser tab with no bridge: the row is still applied to storage, nothing throws", async () => {
    delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
    render(<DeviceSettingsBridge />);
    await settle();
    H.device = { ...UNSET, [DEVICE_TEXT_SIZE_KEY]: "110" };
    await act(async () => {
      await refreshDeviceSettings();
    });
    expect(window.localStorage.getItem(TEXT_SIZE_STORAGE_KEY)).toBe("110");
  });
});
