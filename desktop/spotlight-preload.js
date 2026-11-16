// Preload for the Spotlight quick-task overlay. Exposes a tiny, safe bridge so
// the overlay's HTML can submit a task + close without any Node access.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("spotlight", {
  // Send the typed words to the main process, which opens them in chat in the
  // main window (v1.506.0). Resolves { ok: true, opened: "chat" } or
  // { ok: false, error }.
  submit: (task) => ipcRenderer.invoke("spotlight:submit", task),
  // Hide the overlay.
  close: () => ipcRenderer.send("spotlight:close"),
  // Register a callback fired each time the overlay is shown (clear + focus).
  onShow: (cb) => ipcRenderer.on("spotlight:show", () => cb()),
});
