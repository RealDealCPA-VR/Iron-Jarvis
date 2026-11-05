/**
 * v1.433.0: the window's memory for lib/deviceSettings — what this window
 * last knew of each device setting, and who listens for a change.
 *
 * Import-free on purpose: the test setup file resets it between tests, and a
 * module it imports is cached BEFORE a test file's `vi.mock("@/lib/api")`
 * applies — lib/deviceSettings itself must never be imported there, or it
 * would keep the real api and reach for a real daemon.
 */

export type DeviceValue = string | boolean | null;

export interface DeviceSettingsState {
  /** The last value this window knew for each key; null = not loaded yet. */
  known: Record<string, DeviceValue> | null;
  /** Listeners by setting key. */
  listeners: Map<string, Set<(value: never) => void>>;
  /** The read in flight, and whether one more read was asked for. */
  inFlight: Promise<void> | null;
  again: boolean;
}

export const deviceSettingsState: DeviceSettingsState = {
  known: null,
  listeners: new Map(),
  inFlight: null,
  again: false,
};

/** Where `known` is kept between visits. */
export const KNOWN_STORAGE_KEY = "ij_device_known";

/** Tests: forget what this window knew and who listens. */
export function __resetDeviceSettings(): void {
  deviceSettingsState.known = null;
  deviceSettingsState.inFlight = null;
  deviceSettingsState.again = false;
  deviceSettingsState.listeners.clear();
  try {
    window.localStorage.removeItem(KNOWN_STORAGE_KEY);
  } catch {
    /* no storage in this environment */
  }
}
