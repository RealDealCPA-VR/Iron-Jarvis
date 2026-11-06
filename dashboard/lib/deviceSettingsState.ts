/**
 * v1.433.0: the window's memory for lib/deviceSettings — what this window
 * last knew of each device setting, and who listens for a change.
 *
 * Import-free on purpose: the test setup file resets it between tests, and a
 * module it imports is cached BEFORE a test file's `vi.mock("@/lib/api")`
 * applies — lib/deviceSettings itself must never be imported there, or it
 * would keep the real api and reach for a real daemon.
 */

/** v1.442.0: an object for `device.theme_colors` (the theme maker's picks). */
export type DeviceValue = string | boolean | null | object;

export interface DeviceSettingsState {
  /** The last value this window knew for each key; null = not loaded yet. */
  known: Record<string, DeviceValue> | null;
  /** Listeners by setting key. */
  listeners: Map<string, Set<(value: never) => void>>;
  /** The read in flight, and whether one more read was asked for. */
  inFlight: Promise<void> | null;
  again: boolean;
  /** v1.433.0 review: saves go one at a time, in press order (the tail). */
  writeChain: Promise<unknown>;
  /** Saves not yet answered, by key: a read answering meanwhile may hold the
   *  row from BEFORE the press, so it never decides that key. */
  pending: Map<string, number>;
  /** A count per key, moved by every save, so a read can tell a press landed
   *  while it was out. */
  seq: Map<string, number>;
  /** The daemon's value for each key as last read or saved (what a failed
   *  save puts back as known). */
  confirmed: Map<string, DeviceValue>;
  /** A read set a key aside for a save still out: read again once the saves
   *  are answered. */
  recheck: boolean;
}

export const deviceSettingsState: DeviceSettingsState = {
  known: null,
  listeners: new Map(),
  inFlight: null,
  again: false,
  writeChain: Promise.resolve(),
  pending: new Map(),
  seq: new Map(),
  confirmed: new Map(),
  recheck: false,
};

/** Where `known` is kept between visits. */
export const KNOWN_STORAGE_KEY = "ij_device_known";

/** Tests: forget what this window knew and who listens. */
export function __resetDeviceSettings(): void {
  deviceSettingsState.known = null;
  deviceSettingsState.inFlight = null;
  deviceSettingsState.again = false;
  deviceSettingsState.listeners.clear();
  deviceSettingsState.writeChain = Promise.resolve();
  deviceSettingsState.pending = new Map();
  deviceSettingsState.seq = new Map();
  deviceSettingsState.confirmed = new Map();
  deviceSettingsState.recheck = false;
  try {
    window.localStorage.removeItem(KNOWN_STORAGE_KEY);
  } catch {
    /* no storage in this environment */
  }
}
