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
import {
  DEFAULT_THEME,
  THEMES,
  THEME_STORAGE_KEY,
  allThemes,
  applyTheme,
  isCustomTheme,
  type Mark,
} from "@/lib/theme";
import {
  CHAT_PALETTE_ID,
  MAX_NAME,
  loadPalettes,
  makePalette,
  upsertPalette,
  type CustomPalette,
  type PaletteMode,
  type Surface,
} from "@/lib/themePalette";
import { TEXT_SIZE_STORAGE_KEY, applyTextSize, textSizeFrom, type TextSize } from "@/lib/textSize";

export { __resetDeviceSettings } from "@/lib/deviceSettingsState";

/** The rows this module applies (settings/schema.py). */
export const DEVICE_THEME_KEY = "device.theme";
/** v1.442.0 (Wave 7 group C): a theme of the user's OWN colours, settable
 *  from chat ("change the theme to something green and calm"). The row holds
 *  the theme maker's picks; this module builds the theme with the theme
 *  maker's own generator (lib/themePalette `makePalette` →
 *  `generatePalette`, readability fitting and all), keeps it among this
 *  device's own themes under ONE id (`CHAT_PALETTE_ID`, replaced, never
 *  piled up — so the layout's pre-paint script restores it after a reload)
 *  and paints it. This row and `device.theme` name the same thing two ways:
 *  the daemon's writer clears one when the other gets a value, so the one set
 *  LAST is what the screen shows. When this row goes back to "not set" with
 *  no built-in theme set either (an Undo of the first chat theme), the screen
 *  goes back to the theme it showed before (`THEME_BEFORE_CHAT_KEY`). */
export const DEVICE_THEME_COLORS_KEY = "device.theme_colors";
export const DEVICE_PERSONA_KEY = "device.chat_persona";
export const DEVICE_AUTO_TOOLS_KEY = "device.auto_tools";
export const DEVICE_READ_ALOUD_KEY = "device.read_aloud";
/** v1.456.0: how big everything looks in the desktop app (lib/textSize). */
export const DEVICE_TEXT_SIZE_KEY = "device.text_size";

/** The two rows that name "the theme on this device" (settings/theme_colors
 *  LINKED): a save of one owns the other too (refreshDeviceSettings). */
const THEME_PARTNER: Record<string, string> = {
  [DEVICE_THEME_KEY]: DEVICE_THEME_COLORS_KEY,
  [DEVICE_THEME_COLORS_KEY]: DEVICE_THEME_KEY,
};

/** Where the theme shown before chat's own colours is kept (an Undo goes back to it). */
export const THEME_BEFORE_CHAT_KEY = "ij_theme_before_chat";
/** The chat theme's name when chat gave none. */
export const CHAT_THEME_NAME = "Theme from chat";

export const APPLIED_DEVICE_KEYS = [
  DEVICE_THEME_KEY,
  DEVICE_THEME_COLORS_KEY,
  DEVICE_PERSONA_KEY,
  DEVICE_AUTO_TOOLS_KEY,
  DEVICE_READ_ALOUD_KEY,
  DEVICE_TEXT_SIZE_KEY,
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
        if (!(key in (raw as Record<string, unknown>))) continue;
        const v = (raw as Record<string, unknown>)[key];
        if (v === null || typeof v === "string" || typeof v === "boolean") known[key] = v;
        else if (key === DEVICE_THEME_COLORS_KEY) known[key] = themeColorsFrom(v);
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

/* ---- a theme of the user's own colours (v1.442.0) ------------------------- */

/** The picks `device.theme_colors` holds (settings/theme_colors.py). */
export interface ThemeColors {
  accent: string;
  second?: string;
  surface: Surface;
  mode: PaletteMode;
  name?: string;
}

const SURFACE_IDS: readonly Surface[] = ["neutral", "cool", "warm", "tinted"];
const MODE_IDS: readonly PaletteMode[] = ["dark", "light", "system"];

function hexOf(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const m = /^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$/.exec(raw.trim());
  if (!m) return null;
  const h = m[1].toLowerCase();
  return "#" + (h.length === 3 ? h.split("").map((c) => c + c).join("") : h);
}

/** The picks, checked and in the daemon's one canonical form
 *  (settings/theme_colors.normalize: lower-case six-digit hex; `second` and
 *  `name` only when given), or null when this is no set of colours. */
export function themeColorsFrom(raw: unknown): ThemeColors | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  const accent = hexOf(r.accent);
  if (!accent) return null;
  const out: ThemeColors = {
    accent,
    surface: SURFACE_IDS.includes(r.surface as Surface) ? (r.surface as Surface) : "neutral",
    mode: MODE_IDS.includes(r.mode as PaletteMode) ? (r.mode as PaletteMode) : "dark",
  };
  const second = hexOf(r.second);
  if (second) out.second = second;
  if (typeof r.name === "string") {
    const name = r.name.replace(/[\u0000-\u001f\u007f]/g, " ").split(/\s+/).filter(Boolean).join(" ");
    if (name) out.name = name.slice(0, MAX_NAME);
  }
  return out;
}

/** The picks of one of this device's palettes, as the row holds them. */
export function themeColorsOf(p: CustomPalette): ThemeColors {
  return themeColorsFrom({
    accent: p.accent,
    second: p.second,
    surface: p.surface,
    mode: p.mode,
    name: p.name,
  }) as ThemeColors;
}

/** One value's identity (an object by its keys, in any order). */
function valueKey(v: unknown): string {
  if (v && typeof v === "object" && !Array.isArray(v)) {
    const rec = v as Record<string, unknown>;
    return JSON.stringify(Object.keys(rec).sort().map((k) => [k, rec[k]]));
  }
  return JSON.stringify(v ?? null);
}

function sameValue(a: unknown, b: unknown): boolean {
  return valueKey(a) === valueKey(b);
}

/** Build the chat theme from the picks with the theme maker's own generator,
 *  keep it among this device's themes, and paint it. False when it could not
 *  be kept (storage refused it). */
function applyThemeColors(colors: ThemeColors): boolean {
  const existing = loadPalettes().find((p) => p.id === CHAT_PALETTE_ID);
  const palette = makePalette({
    id: CHAT_PALETTE_ID,
    name: colors.name ?? CHAT_THEME_NAME,
    accent: colors.accent,
    second: colors.second ?? null,
    surface: colors.surface,
    mode: colors.mode,
    // A background the person chose for this theme on screen stays with it.
    background: existing?.background ?? null,
  });
  if (!upsertPalette(palette)) return false;
  const on = typeof document !== "undefined" ? document.documentElement.dataset.theme : undefined;
  if (on && on !== CHAT_PALETTE_ID) store(THEME_BEFORE_CHAT_KEY, on);
  applyTheme(CHAT_PALETTE_ID);
  return true;
}

/** The colours went back to "not set" and no built-in theme is set either
 *  (an Undo of the first chat theme): if the screen still shows the chat
 *  theme, go back to the theme it showed before (else the default). */
function leaveChatTheme(): void {
  if (typeof document === "undefined" || document.documentElement.dataset.theme !== CHAT_PALETTE_ID) return;
  let stored: string | null = null;
  let before: string | null = null;
  try {
    stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    before = window.localStorage.getItem(THEME_BEFORE_CHAT_KEY);
  } catch {
    stored = before = null;
  }
  const usable = (id: string | null): id is string =>
    !!id && id !== CHAT_PALETTE_ID && allThemes().some((t) => t.id === id);
  // v1.442.0 review: ANOTHER window of this device that picked a theme of
  // the user's own (which clears both rows) has already written it to the
  // shared storage: that newer pick wins over the theme before chat (going
  // back to that one also overwrote the other window's pick for the next
  // load). In this window the storage still names the chat theme.
  const target = usable(stored) ? stored : usable(before) ? before : DEFAULT_THEME;
  applyTheme(target);
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
    case DEVICE_THEME_COLORS_KEY: {
      const colors = themeColorsFrom(value);
      return colors ? applyThemeColors(colors) : false;
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
    case DEVICE_TEXT_SIZE_KEY: {
      const size = textSizeFrom(value);
      if (size === null) return false;
      applyTextSize(size);
      return true;
    }
  }
  return false;
}

/** What the screen ALREADY keeps for a key in its own storage, or
 *  `undefined` when it keeps nothing (it shows the default). A theme of the
 *  user's own is kept as `null`: the row only holds built-in themes. */
function screenValue(key: AppliedDeviceKey): Value | undefined {
  // v1.442.0: chat's own colours are only ever set from chat (or by picking
  // that theme on screen, which saves the row): the row decides.
  if (key === DEVICE_THEME_COLORS_KEY) return undefined;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(
      key === DEVICE_THEME_KEY
        ? THEME_STORAGE_KEY
        : key === DEVICE_PERSONA_KEY
          ? PERSONA_STORAGE_KEY
          : key === DEVICE_AUTO_TOOLS_KEY
            ? AUTO_TOOLS_STORAGE_KEY
            : key === DEVICE_TEXT_SIZE_KEY
              ? TEXT_SIZE_STORAGE_KEY
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
    case DEVICE_TEXT_SIZE_KEY: {
      const size = textSizeFrom(raw);
      return size === null ? undefined : String(size);
    }
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
    const v: Value =
      raw === null || typeof raw === "string" || typeof raw === "boolean"
        ? raw
        : key === DEVICE_THEME_COLORS_KEY
          ? themeColorsFrom(raw)
          : null;
    if (key in map && sameValue(map[key], v)) {
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
    const before = key in map ? map[key] : null;
    map[key] = v;
    moved = true;
    if (v !== null) applyOne(key, v);
    else if (key === DEVICE_THEME_COLORS_KEY && before !== null) {
      const builtin = values[DEVICE_THEME_KEY];
      if (!isBuiltinThemeId(builtin)) {
        // v1.442.0: chat's colours were taken away and no built-in theme took
        // their place.
        leaveChatTheme();
      } else if (typeof document !== "undefined" && document.documentElement.dataset.theme === CHAT_PALETTE_ID) {
        // v1.442.0 review: a built-in theme took their place and is normally
        // painted by its own row above — but not when this window already
        // knew that theme (a read taken while its pick was out), and then
        // the chat theme stayed on screen under a row naming Daylight.
        applyTheme(builtin);
      }
    }
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
          // v1.442.0 review: the two theme rows are ONE choice (the daemon's
          // writer clears one when the other is set), so a save of either
          // owns both — a read that answered before a Daylight pick landed
          // painted chat's colours over it, and nothing painted it back.
          const owners = key in THEME_PARTNER ? [key, THEME_PARTNER[key]] : [key];
          if (owners.some((k) => (state.pending.get(k) ?? 0) > 0)) {
            // A save still out owns this key: read again once it answers.
            state.recheck = true;
            return true;
          }
          if (owners.some((k) => (state.seq.get(k) ?? 0) !== (startSeq.get(k) ?? 0))) {
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
  // v1.442.0 (Wave 7 group C): the two theme rows go together, so the
  // screen's pick is the one set last: a built-in theme clears chat's own
  // colours; the chat theme (picked again, or edited in the theme maker)
  // saves its picks as those colours; any other theme of your own clears
  // both (it is a screen-only choice).
  let colors: ThemeColors | null = null;
  if (mark.id === CHAT_PALETTE_ID) {
    const p = loadPalettes().find((x) => x.id === CHAT_PALETTE_ID);
    colors = p ? themeColorsOf(p) : null;
  }
  const values: Partial<Record<AppliedDeviceKey, Value>> = {
    [DEVICE_THEME_KEY]: isCustomTheme(mark.id) ? null : mark.id,
  };
  // Only when there is something to say: colours to keep, or colours this
  // window knows the row still holds (a press otherwise PUTs exactly what it
  // did before this release; the writer clears the colours itself when a
  // built-in theme is set).
  const known = knownMap();
  if (colors || (DEVICE_THEME_COLORS_KEY in known && known[DEVICE_THEME_COLORS_KEY] !== null)) {
    values[DEVICE_THEME_COLORS_KEY] = colors;
  }
  void writeDeviceValues(values);
  return mark;
}

/**
 * v1.456.0: a text size chosen on screen — the desktop app's Ctrl + = /
 * Ctrl + - / Ctrl + 0 (the shell has already applied it and says so through
 * `textSize.onChanged`), or a press anywhere else. Keeps it for this window,
 * hands it to the shell (a no-op when the shell already holds it) and saves
 * it as this device's row, so Settings and chat's `config_list` say it too.
 */
export function chooseTextSize(size: TextSize): void {
  applyTextSize(size);
  void writeDeviceValues({ [DEVICE_TEXT_SIZE_KEY]: String(size) });
}
