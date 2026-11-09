/**
 * v1.456.0: TEXT SIZE — how big everything looks in the desktop app on this
 * PC (the owner: "change font sizes across the app in settings").
 *
 * The row is `device.text_size` (settings/schema.py), one of TEXT_SIZES as a
 * string. The desktop shell applies it as the window's own zoom
 * (desktop/main.js `setTextSize` via preload `textSize.set`): text, icons and
 * spacing grow together, so a layout never cuts a word off the way scaling
 * the font alone would (most of this app's sizes are whole pixels). In a
 * browser tab there is no bridge: the row is still saved, and the help says to
 * use the browser's own zoom.
 *
 * Ctrl + =, Ctrl + - and Ctrl + 0 are the desktop app's own menu keys: the
 * shell changes the zoom itself and tells the window (`onChanged`), which
 * saves the row (lib/deviceSettings `chooseTextSize`).
 *
 * Kept free of imports so lib/deviceSettings and the bridge both read it.
 */

/** Pinned equal to desktop/main.js TEXT_SIZES and the schema's options. */
export const TEXT_SIZES = [90, 100, 110, 125, 150] as const;
export type TextSize = (typeof TEXT_SIZES)[number];
export const DEFAULT_TEXT_SIZE: TextSize = 100;

/** Where the screen keeps this window's choice (the first-look rule reads it). */
export const TEXT_SIZE_STORAGE_KEY = "ij_text_size";

/** A text size from a row value or a stored string, or null. */
export function textSizeFrom(raw: unknown): TextSize | null {
  const n = typeof raw === "number" ? raw : typeof raw === "string" && raw.trim() ? Number(raw) : NaN;
  return (TEXT_SIZES as readonly number[]).includes(n) ? (n as TextSize) : null;
}

/** The desktop shell's text-size bridge (desktop/preload.js `textSize`). */
export interface TextSizeBridge {
  set: (percent: number) => Promise<unknown>;
  get?: () => Promise<unknown>;
  onChanged?: (cb: (percent: number) => void) => () => void;
}

/** The bridge, or null in a browser or an older shell. */
export function textSizeBridge(): TextSizeBridge | null {
  if (typeof window === "undefined") return null;
  try {
    const b = (window as unknown as { ironjarvis?: { textSize?: Partial<TextSizeBridge> } }).ironjarvis
      ?.textSize;
    return b && typeof b.set === "function" ? (b as TextSizeBridge) : null;
  } catch {
    return null;
  }
}

/** The size this window last chose, or null when it never chose one. */
export function storedTextSize(): TextSize | null {
  try {
    return textSizeFrom(window.localStorage.getItem(TEXT_SIZE_STORAGE_KEY));
  } catch {
    return null;
  }
}

/** Keep the choice and hand it to the desktop shell (a browser keeps it only). */
export function applyTextSize(size: TextSize): void {
  try {
    window.localStorage.setItem(TEXT_SIZE_STORAGE_KEY, String(size));
  } catch {
    /* blocked storage: the shell still applies it */
  }
  const bridge = textSizeBridge();
  if (!bridge) return;
  try {
    void Promise.resolve(bridge.set(size)).catch(() => {
      /* an older or refusing shell: nothing to do */
    });
  } catch {
    /* never throws */
  }
}
