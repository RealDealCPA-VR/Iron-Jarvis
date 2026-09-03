/**
 * v1.226.0: are we running inside the packaged desktop shell? The Electron
 * preload exposes `window.ironjarvis.isDesktop` (desktop/preload.js); in a
 * browser tab it is absent. Offline copy branches on this: the desktop app
 * SUPERVISES its daemon (restart ladder in main.js), so telling that user to
 * run `uv run ironjarvis serve` is wrong advice.
 */
export function isDesktopShell(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return !!(window as unknown as { ironjarvis?: { isDesktop?: boolean } }).ironjarvis
      ?.isDesktop;
  } catch {
    return false;
  }
}

/** The one line both offline surfaces show inside the desktop shell. */
export const DESKTOP_OFFLINE_HINT =
  "Iron Jarvis is restarting its local service… if this persists, use the tray → Quit and relaunch.";
