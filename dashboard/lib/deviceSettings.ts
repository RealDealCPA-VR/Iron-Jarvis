/**
 * v1.433.0 (Wave 1 B, decision D1): THIS DEVICE'S SETTINGS, applied live.
 *
 * Settings (settings/schema.py) has five rows kept per device by the daemon
 * (`<home>/device_prefs.json`, GET/PUT /settings/device): the theme, the
 * approvals default, the chat persona, Auto tools and read aloud. Chat's own
 * settings tools can change them ("switch to Daylight", "read replies
 * aloud"). Until v1.433.0 only the approvals row was read back
 * (lib/approvalDefault); the screen kept the other four in browser storage,
 * so a change made from chat (or the Settings rows) did nothing on screen,
 * and `config_list` reported values the screen did not show.
 *
 * Now ONE module carries them both ways:
 *
 *  - IN: `refreshDeviceSettings()` reads this device's values and applies
 *    each one that CHANGED since this window last knew it — the theme through
 *    lib/theme's `applyTheme` (an id that is not a built-in theme is
 *    ignored), and the persona, Auto tools and read aloud into the browser
 *    storage the screen already reads, plus a notice to whoever shows them
 *    (`onDeviceSetting`: the chat page, every `useTTS`). The bridge in the
 *    layout (components/DeviceSettingsBridge) calls it once when the window
 *    opens and again whenever a `config.changed` event names a `device.*`
 *    key. It never runs per keystroke, and a listener hears only a real
 *    change, so the chat page re-renders only when one of its values moves.
 *  - OUT: a press on the screen's own control (the theme switcher, the
 *    read-aloud toggle, the persona picker, the Auto tools switch) calls
 *    `writeDeviceSetting`, so the daemon's row — what chat's `config_list`
 *    reports — matches the screen.
 *
 * "Changed since this window last knew it" is the rule that keeps a stale
 * row from undoing a screen choice: the last value seen for each key is kept
 * in localStorage (`ij_device_known`) and in memory, so a reload whose row
 * still holds what it held before changes nothing on screen (a theme of the
 * user's own, which is a screen-only choice, survives), while a NEW value —
 * set from chat, from the Settings rows, or from another window of this
 * device — is applied.
 *
 * v1.433.0 review, three more rules:
 *  - A save not yet answered decides its key: a read that answers meanwhile
 *    (the window's first read, or the read a previous save's
 *    `config.changed` started) may hold the row from BEFORE the press, so it
 *    is set aside for that key and read again once the saves are answered.
 *  - Saves go one at a time, in press order, so the row ends on the LAST
 *    press (two PUTs at once may land in either order).
 *  - The FIRST time this browser sees a key (upgrade day: these rows did
 *    nothing on screen before this release, so a row set back then is older
 *    than what the person sees), a value the screen already keeps wins, and
 *    a row that says otherwise is set to it — else a later chat change to
 *    the row's old value would raise no event and never reach the screen. A
 *    screen that keeps nothing for the key takes the row.
 *
 * An older daemon answers 404 for /settings/device: nothing is applied and a
 * press still works exactly as before (the write is best effort). Kept out of
 * lib/api.ts (most page tests mock that module wholesale), and it imports
 * nothing from lib/useTTS (most chat tests mock that one): useTTS imports
 * from here.
 */

import { get, put } from "@/lib/api";
import { getDeviceId } from "@/lib/device";
import {
  KNOWN_STORAGE_KEY,
  deviceSettingsState as state,
  type DeviceValue,
} from "@/lib/deviceSettingsState";
import { THEMES, THEME_STORAGE_KEY, allThemes, applyTheme, isCustomTheme, type Mark } from "@/lib/theme";

export { __resetDeviceSettings } from "@/lib/deviceSettingsState";

/** The four rows this module applies (settings/schema.py). */
export const DEVICE_THEME_KEY = "device.theme";
export const DEVICE_PERSONA_KEY = "device.chat_persona";
export const DEVICE_AUTO_TOOLS_KEY = "device.auto_tools";
export const DEVICE_READ_ALOUD_KEY = "device.read_aloud";

export const APPLIED_DEVICE_KEYS = [
  DEVICE_THEME_KEY,
  DEVICE_PERSONA_KEY,
  DEVICE_AUTO_TOOLS_KEY,
  DEVICE_READ_ALOUD_KEY,
] as const;
export type AppliedDeviceKey = (typeof APPLIED_DEVICE_KEYS)[number];

/** The browser storage the screen has always read for each (unchanged). */
export const PERSONA_STORAGE_KEY = "ij_chat_persona";
export const AUTO_TOOLS_STORAGE_KEY = "ij_chat_auto_tools";
export const READ_ALOUD_STORAGE_KEY = "ironjarvis.tts.enabled";

/** The one event that can name a device setting (its payload's `keys`). */
export const DEVICE_EVENT_TYPES = ["config.changed"] as const;

type Value = DeviceValue;

interface Notified {
  [DEVICE_PERSONA_KEY]: string;
  [DEVICE_AUTO_TOOLS_KEY]: boolean;
  [DEVICE_READ_ALOUD_KEY]: boolean;
}

/* ---- what this window knows ---------------------------------------------- */

function knownMap(): Record<string, Value> {
  if (state.known) return state.known;
  const known: Record<string, Value> = {};
  state.known = known;
  try {
    const raw = JSON.parse(window.localStorage.getItem(KNOWN_STORAGE_KEY) || "{}") as unknown;
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      for (const key of APPLIED_DEVICE_KEYS) {
        const v = (raw as Record<string, unknown>)[key];
        if (v === null || typeof v === "string" || typeof v === "boolean") known[key] = v;
      }
    }
  } catch {
    /* unreadable or blocked storage: start from nothing known */
  }
  return known;
}

function saveKnown(): void {
  try {
    window.localStorage.setItem(KNOWN_STORAGE_KEY, JSON.stringify(knownMap()));
  } catch {
    /* private window: this window still remembers */
  }
}

/* ---- who shows them ------------------------------------------------------- */

function listenersOf<K extends keyof Notified>(key: K): Set<(value: Notified[K]) => void> {
  let set = state.listeners.get(key);
  if (!set) {
    set = new Set();
    state.listeners.set(key, set);
  }
  return set as unknown as Set<(value: Notified[K]) => void>;
}

/** Hear a device value that was just applied (never one the screen already
 *  holds). Returns the unsubscribe. */
export function onDeviceSetting<K extends keyof Notified>(
  key: K,
  cb: (value: Notified[K]) => void,
): () => void {
  const set = listenersOf(key);
  set.add(cb);
  return () => {
    set.delete(cb);
  };
}

function notify<K extends keyof Notified>(key: K, value: Notified[K]): void {
  for (const cb of Array.from(listenersOf(key))) {
    try {
      cb(value);
    } catch {
      /* one listener's fault never stops the others */
    }
  }
}

function store(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* blocked storage: the listeners still apply it for this window */
  }
}

/** A built-in theme id (lib/theme THEMES). A theme of the user's own is a
 *  screen-only choice, so the row never names one. */
export function isBuiltinThemeId(id: unknown): id is string {
  return typeof id === "string" && THEMES.some((t) => t.id === id);
}

/** Apply ONE value to the screen. False = not a value this key takes
 *  (ignored, nothing changed). */
function applyOne(key: AppliedDeviceKey, value: unknown): boolean {
  switch (key) {
    case DEVICE_THEME_KEY: {
      if (!isBuiltinThemeId(value)) return false;
      const on = typeof document !== "undefined" ? document.documentElement.dataset.theme : undefined;
      if (on !== value) applyTheme(value);
      return true;
    }
    case DEVICE_PERSONA_KEY: {
      if (typeof value !== "string" || !value.trim()) return false;
      const persona = value.trim();
      store(PERSONA_STORAGE_KEY, persona);
      notify(DEVICE_PERSONA_KEY, persona);
      return true;
    }
    case DEVICE_AUTO_TOOLS_KEY: {
      if (typeof value !== "boolean") return false;
      store(AUTO_TOOLS_STORAGE_KEY, value ? "1" : "0");
      notify(DEVICE_AUTO_TOOLS_KEY, value);
      return true;
    }
    case DEVICE_READ_ALOUD_KEY: {
      if (typeof value !== "boolean") return false;
      store(READ_ALOUD_STORAGE_KEY, value ? "1" : "0");
      notify(DEVICE_READ_ALOUD_KEY, value);
      return true;
    }
  }
  return false;
}

/** What the screen ALREADY keeps for a key in its own storage, or
 *  `undefined` when it keeps nothing (it shows the default). A theme of the
 *  user's own is kept as `null`: the row only holds built-in themes. */
function screenValue(key: AppliedDeviceKey): Value | undefined {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(
      key === DEVICE_THEME_KEY
        ? THEME_STORAGE_KEY
        : key === DEVICE_PERSONA_KEY
          ? PERSONA_STORAGE_KEY
          : key === DEVICE_AUTO_TOOLS_KEY
            ? AUTO_TOOLS_STORAGE_KEY
            : READ_ALOUD_STORAGE_KEY,
    );
  } catch {
    return undefined;
  }
  if (raw === null) return undefined;
  switch (key) {
    case DEVICE_THEME_KEY:
      if (isBuiltinThemeId(raw)) return raw;
      // A palette that still exists is the user's own choice; a stale id
      // (a deleted palette) paints the default, so it is no choice.
      return isCustomTheme(raw) && allThemes().some((t) => t.id === raw) ? null : undefined;
    case DEVICE_PERSONA_KEY:
      return raw.trim() ? raw.trim() : undefined;
    case DEVICE_AUTO_TOOLS_KEY:
    case DEVICE_READ_ALOUD_KEY:
      return raw === "1" ? true : raw === "0" ? false : undefined;
  }
  return undefined;
}

/** The `values` of a GET/PUT /settings/device answer, or null. */
export function deviceValuesFrom(raw: unknown): Record<string, unknown> | null {
  if (!raw || typeof raw !== "object") return null;
  const values = (raw as { values?: unknown }).values;
  return values && typeof values === "object" && !Array.isArray(values)
    ? (values as Record<string, unknown>)
    : null;
}

/** Apply every value of `values` that changed since this window last knew
 *  it. Exported for the tests; the bridge goes through
 *  `refreshDeviceSettings`, which passes `setAside` — true for a key a save
 *  owns while this read was out (the value is then left for a later read).
 *  The first look at a key keeps what the screen already holds (see the
 *  module comment). */
export function applyDeviceValues(
  values: Record<string, unknown>,
  setAside?: (key: AppliedDeviceKey) => boolean,
): void {
  const map = knownMap();
  let moved = false;
  const reconcile: Partial<Record<AppliedDeviceKey, Value>> = {};
  for (const key of APPLIED_DEVICE_KEYS) {
    // A row an older daemon does not have: leave the screen as it is.
    if (!(key in values)) continue;
    const raw = values[key];
    const v: Value = raw === null || typeof raw === "string" || typeof raw === "boolean" ? raw : null;
    if (key in map && map[key] === v) {
      state.confirmed.set(key, v);
      continue;
    }
    if (setAside?.(key)) continue;
    state.confirmed.set(key, v);
    if (!(key in map)) {
      const screen = screenValue(key);
      if (screen !== undefined) {
        // The first look, and the screen keeps its own value: it wins.
        map[key] = v;
        moved = true;
        if (v !== null && screen !== v) reconcile[key] = screen;
        continue;
      }
    }
    map[key] = v;
    moved = true;
    if (v !== null) applyOne(key, v);
  }
  if (moved) saveKnown();
  if (Object.keys(reconcile).length > 0) void writeDeviceValues(reconcile);
}

/** This device's values, or null (an older daemon's 404, offline). */
async function readDeviceValues(): Promise<Record<string, unknown> | null> {
  try {
    const raw = await get<unknown>(`/settings/device?device_id=${encodeURIComponent(getDeviceId())}`);
    return deviceValuesFrom(raw);
  } catch {
    return null;
  }
}

/** Read this device's settings and apply what changed. One read at a time:
 *  a call while one is out asks for ONE more read after it. Never throws. */
export function refreshDeviceSettings(): Promise<void> {
  if (state.inFlight) {
    state.again = true;
    return state.inFlight;
  }
  const run: Promise<void> = (async () => {
    do {
      state.again = false;
      const startSeq = new Map(state.seq);
      const values = await readDeviceValues();
      if (values) {
        applyDeviceValues(values, (key) => {
          if ((state.pending.get(key) ?? 0) > 0) {
            // A save still out owns this key: read again once it answers.
            state.recheck = true;
            return true;
          }
          if ((state.seq.get(key) ?? 0) !== (startSeq.get(key) ?? 0)) {
            // A save landed while this read was out: read again now.
            state.again = true;
            return true;
          }
          return false;
        });
      }
    } while (state.again);
  })()
    .catch(() => {
      /* never throws */
    })
    .finally(() => {
      if (state.inFlight === run) state.inFlight = null;
    });
  state.inFlight = run;
  return run;
}

/** Does this `config.changed` payload name a device setting? */
export function namesDeviceKey(payload: unknown): boolean {
  if (!payload || typeof payload !== "object") return false;
  const keys = (payload as { keys?: unknown }).keys;
  return Array.isArray(keys) && keys.some((k) => typeof k === "string" && k.startsWith("device."));
}

/**
 * Save a press on the screen's own control as this device's row. Best
 * effort: false when the daemon did not take it (an older daemon, offline),
 * and then what this window knew goes back to the daemon's own value, so the
 * next read does not mistake the old row for a new choice. Never throws.
 */
export function writeDeviceSetting(key: AppliedDeviceKey, value: Value): Promise<boolean> {
  return writeDeviceValues({ [key]: value });
}

/** One save of several keys (the first look's reconcile uses it: one PUT,
 *  one ledger row). Saves run one at a time, in call order. */
function writeDeviceValues(values: Partial<Record<AppliedDeviceKey, Value>>): Promise<boolean> {
  const map = knownMap();
  // This window's books as they are now (the test setup swaps them between
  // tests; a save that answers later keeps to its own).
  const { pending, seq: seqs, confirmed } = state;
  const keys = Object.keys(values) as AppliedDeviceKey[];
  const mine = new Map<AppliedDeviceKey, number>();
  // Known BEFORE the write lands: the `config.changed` this write raises
  // reads the row back, and it must not be taken for someone else's change.
  for (const key of keys) {
    // What this window believed the daemon held (kept since an earlier
    // visit), so a refused save can put it back.
    if (!confirmed.has(key) && !pending.has(key) && key in map) confirmed.set(key, map[key]);
    const seq = (seqs.get(key) ?? 0) + 1;
    seqs.set(key, seq);
    pending.set(key, (pending.get(key) ?? 0) + 1);
    mine.set(key, seq);
    map[key] = values[key] as Value;
  }
  saveKnown();
  const run = state.writeChain.then(async () => {
    try {
      await put("/settings/device", { device_id: getDeviceId(), values });
      for (const key of keys) confirmed.set(key, values[key] as Value);
      return true;
    } catch {
      let back = false;
      for (const [key, seq] of mine) {
        if (seqs.get(key) !== seq) continue; // a later press owns it now
        if (confirmed.has(key)) map[key] = confirmed.get(key) as Value;
        else delete map[key];
        back = true;
      }
      if (back && state.known === map) saveKnown();
      return false;
    } finally {
      for (const key of keys) {
        const left = (pending.get(key) ?? 1) - 1;
        if (left > 0) pending.set(key, left);
        else pending.delete(key);
      }
      if (state.pending === pending && pending.size === 0 && state.recheck) {
        state.recheck = false;
        void refreshDeviceSettings();
      }
    }
  });
  state.writeChain = run.catch(() => false);
  return run;
}

/**
 * The theme a person picked on screen (the switcher, the palette's "Theme:"
 * commands, the theme maker): apply it, and save it as this device's row —
 * a built-in theme by its id; a theme of the user's own clears the row
 * ("not set"), since that row only holds built-in themes.
 */
export function chooseTheme(id: string): Mark | null {
  const mark = applyTheme(id);
  if (!mark) return null;
  void writeDeviceSetting(DEVICE_THEME_KEY, isCustomTheme(mark.id) ? null : mark.id);
  return mark;
}
