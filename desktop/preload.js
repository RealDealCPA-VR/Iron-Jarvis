// Iron Jarvis — preload (runs in an isolated context with limited Node access).
// Exposes a tiny, read-only surface to the dashboard renderer.
const { contextBridge, ipcRenderer } = require("electron");

// --- Per-install auth token injection (primary path, zero 401 race) ---------
// main.js generates a per-install token, passes it to the daemon as
// IRONJARVIS_TOKEN, and hands it to this preload via webPreferences
// additionalArguments (`--ij-token=<hex>`). Because the preload runs BEFORE the
// dashboard's own bundle executes, writing it into localStorage here means the
// very first HTTP fetch AND the first WebSocket connection already carry the
// token — the dashboard's lib/api.ts reads localStorage['ij_token'] for both
// the `Authorization: Bearer` header and the ws `?token=` query param.
//
// Belt-and-suspenders: even if this write is blocked (sandbox/timing), main.js
// (a) injects the Authorization header for daemon-origin HTTP via webRequest and
// (b) re-sets localStorage + reloads once on did-finish-load. So there is never
// a PERMANENT 401.
const IJ_TOKEN_KEY = "ij_token";

function readTokenArg() {
  const prefix = "--ij-token=";
  // additionalArguments are appended to process.argv (works in sandbox too).
  const arg = (process.argv || []).find((a) => typeof a === "string" && a.startsWith(prefix));
  return arg ? arg.slice(prefix.length) : "";
}

(function injectToken() {
  const token = readTokenArg();
  if (!token) return; // no token configured => daemon runs auth-disabled
  try {
    if (window.localStorage.getItem(IJ_TOKEN_KEY) !== token) {
      window.localStorage.setItem(IJ_TOKEN_KEY, token);
    }
  } catch {
    /* covered by main.js webRequest header + did-finish-load fallback */
  }
})();

contextBridge.exposeInMainWorld("ironjarvis", {
  // Lets the dashboard detect it's running inside the desktop shell.
  isDesktop: true,
  version: process.versions.electron,
  // Native clipboard via the main process — the reliable path for terminal
  // paste/copy inside Electron (navigator.clipboard can be permission-gated).
  clipboardReadText: () => ipcRenderer.invoke("clipboard:read"),
  clipboardWriteText: (text) => ipcRenderer.invoke("clipboard:write", String(text ?? "")),
  // App auto-update control for the Updates page (the packaged-app updater).
  update: {
    getState: () => ipcRenderer.invoke("update:getState"),
    check: () => ipcRenderer.invoke("update:check"),
    apply: () => ipcRenderer.invoke("update:apply"),
    // Subscribe to live status; returns an unsubscribe fn.
    onState: (cb) => {
      const handler = (_e, state) => cb(state);
      ipcRenderer.on("update:state", handler);
      return () => ipcRenderer.removeListener("update:state", handler);
    },
  },
});
