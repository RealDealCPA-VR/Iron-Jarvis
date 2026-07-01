// Iron Jarvis — Electron main process (CommonJS).
//
// What this does:
//   1. Spawns the Python daemon (dev: `uv run ironjarvis serve`; packaged: the
//      frozen ironjarvis.exe) with a per-install IRONJARVIS_TOKEN.
//   2. Spawns the Next.js dashboard (dev: `pnpm start`; packaged: standalone
//      server.js via Electron's bundled Node).
//   3. Shows a dark "Starting Iron Jarvis…" splash while polling the dashboard.
//   4. When the dashboard answers, opens the real window (size/pos restored from
//      window-state.json) on http://localhost:<DASHBOARD_PORT>.
//   5. ALWAYS-ON: closing the window HIDES it to a system tray; the daemon +
//      dashboard keep running so the scheduler/cron/webhooks survive for weeks.
//      Only an explicit Quit (tray menu / app.quit) tears the children down.
//
// The repo (daemon + ./dashboard) is expected one directory above this file.

const {
  app,
  BrowserWindow,
  Menu,
  Tray,
  shell,
  dialog,
  globalShortcut,
  session,
  screen,
  nativeImage,
} = require("electron");
const { spawn, spawnSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");

const windowState = require("./windowState");

// --- Configuration -------------------------------------------------------

// Two run modes:
//  - DEV (not packaged): the repo (daemon + ./dashboard) sits one dir above this
//    file; we drive it via `uv run ironjarvis serve` + `pnpm start`.
//  - PACKAGED (installed .exe): a frozen daemon exe + a Next.js *standalone*
//    server are bundled under resources/; we run them via the frozen exe and
//    Electron's own bundled Node — NO Python, uv, Node, or pnpm required.
const IS_PACKAGED = app.isPackaged;
const REPO_ROOT = path.join(__dirname, "..");
const DASHBOARD_DIR = path.join(REPO_ROOT, "dashboard");
const RES_DIR = process.resourcesPath || REPO_ROOT;
const DAEMON_EXE = path.join(RES_DIR, "daemon", "ironjarvis.exe");
const DASHBOARD_SERVER = path.join(RES_DIR, "dashboard", "server.js");

// The dashboard's API base (NEXT_PUBLIC_IJ_API) is baked at build time to
// 127.0.0.1:8787, so the bundled daemon MUST listen on 8787.
const DAEMON_PORT = parseInt(process.env.IJ_DAEMON_PORT || "8787", 10);
const DASHBOARD_PORT = parseInt(process.env.IJ_DASHBOARD_PORT || "3000", 10);

const DASHBOARD_URL = `http://localhost:${DASHBOARD_PORT}`;
const DASHBOARD_PROBE_URL = `http://127.0.0.1:${DASHBOARD_PORT}/`;
const STARTUP_TIMEOUT_MS = 30000;

const HOTKEY = "CommandOrControl+Shift+J";

// --- State ---------------------------------------------------------------

let daemonProc = null;
let dashboardProc = null;
let loadingWin = null;
let mainWin = null;
let tray = null;
let shuttingDown = false;
// isQuitting distinguishes "user wants to fully exit" (tear everything down)
// from a normal window close (just hide to the tray, keep the daemon alive).
let isQuitting = false;
let authToken = null; // per-install bearer token (also passed to the daemon)
let userDataDir = null; // app.getPath('userData') — set once app is ready
let saveBoundsTimer = null; // debounce timer for window-state writes

// --- Per-install auth token ---------------------------------------------
// The local daemon is RCE-by-design; a token blocks drive-by requests from any
// website (the daemon enforces IRONJARVIS_TOKEN when set). We generate one on
// first launch, persist it under userData, pass it to the daemon's env, and the
// browser sends it back (localStorage 'ij_token' -> header + ws ?token=).

function getOrCreateToken() {
  const file = path.join(userDataDir, "token.txt");
  try {
    const existing = (fs.readFileSync(file, "utf8") || "").trim();
    if (/^[a-f0-9]{32,}$/i.test(existing)) return existing;
  } catch {
    /* not created yet */
  }
  const token = crypto.randomBytes(32).toString("hex");
  try {
    fs.writeFileSync(file, token, { encoding: "utf8", mode: 0o600 });
  } catch (err) {
    // Non-fatal: a fresh token each launch is still internally consistent
    // (daemon env + browser localStorage both get THIS value this session).
    console.error("[token] could not persist token.txt:", err && err.message);
  }
  return token;
}

// Inject the bearer token on every HTTP/WS request to the daemon origin. This
// is the belt-and-suspenders for HTTP: requests are authorized even before the
// renderer's localStorage is populated (the WS guard still relies on the
// localStorage-driven ?token= query, which the preload sets pre-bundle).
function installAuthHeaderInjection() {
  if (!authToken) return;
  // A webRequest match pattern matches ANY port on the host and must NOT contain a
  // port — Electron 42 hard-rejects `*://127.0.0.1:8787/*` ("Invalid port"), which
  // previously threw here and aborted startup BEFORE the daemon was ever spawned.
  const filter = { urls: ["*://127.0.0.1/*", "*://localhost/*"] };
  try {
    session.defaultSession.webRequest.onBeforeSendHeaders(filter, (details, callback) => {
      const headers = details.requestHeaders || {};
      if (!headers.Authorization && !headers.authorization) {
        headers.Authorization = `Bearer ${authToken}`;
      }
      callback({ requestHeaders: headers });
    });
  } catch (err) {
    // Non-fatal: the renderer also carries the token via localStorage / ?token=.
    // Never let this stop the app from booting the daemon + dashboard.
    console.error("[auth] header injection unavailable:", err && err.message);
  }
}

// --- Child process helpers ----------------------------------------------

function spawnChild(label, command, args, cwd, extraEnv, useShell = true) {
  const child = spawn(command, args, {
    cwd,
    // Dev resolves uv/pnpm via cmd.exe (shell:true); packaged spawns the frozen
    // exe and Electron's node binary directly (shell:false).
    shell: useShell,
    windowsHide: true,
    env: { ...process.env, ...(extraEnv || {}) },
  });

  if (child.stdout) {
    child.stdout.on("data", (d) => process.stdout.write(`[${label}] ${d}`));
  }
  if (child.stderr) {
    child.stderr.on("data", (d) => process.stderr.write(`[${label}] ${d}`));
  }
  child.on("error", (err) => {
    // With shell:true the inner command (uv/pnpm) won't raise ENOENT here —
    // that's covered by the preflight check below. This catches shell failures.
    console.error(`[${label}] spawn error:`, err.message);
  });
  child.on("exit", (code, signal) => {
    console.log(`[${label}] exited (code=${code}, signal=${signal}, pid=${child.pid})`);
  });

  console.log(`[${label}] started pid=${child.pid}: ${command} ${args.join(" ")} (cwd=${cwd})`);
  return child;
}

// Resolve whether a command is on PATH (so we can show a friendly dialog
// instead of silently timing out when uv/pnpm aren't installed).
function commandExists(cmd) {
  return new Promise((resolve) => {
    const probe = process.platform === "win32" ? "where" : "which";
    const child = spawn(probe, [cmd], { shell: true, windowsHide: true });
    child.on("error", () => resolve(false));
    child.on("exit", (code) => resolve(code === 0));
  });
}

function killChild(child, label) {
  if (!child) return;
  // Already exited?
  if (child.exitCode !== null || child.signalCode !== null) return;
  const pid = child.pid;
  if (!pid) return;
  try {
    if (process.platform === "win32") {
      // SYNCHRONOUS: an auto-update must overwrite the running frozen daemon exe
      // (resources/daemon/ironjarvis.exe) — if we return before the process tree
      // dies, NSIS hits a file lock and CORRUPTS the upgrade. spawnSync blocks
      // until taskkill has force-terminated the tree. /T = tree, /F = force.
      spawnSync("taskkill", ["/pid", String(pid), "/T", "/F"], { windowsHide: true });
    } else {
      child.kill("SIGTERM");
    }
    console.log(`[${label}] killed (pid=${pid})`);
  } catch (err) {
    console.error(`[${label}] failed to kill (pid=${pid}):`, err.message);
  }
}

function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  killChild(daemonProc, "daemon");
  killChild(dashboardProc, "dashboard");
}

// --- Dashboard readiness polling ----------------------------------------

function waitForDashboard(timeoutMs, intervalMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = http.get(DASHBOARD_PROBE_URL, (res) => {
        res.resume(); // drain
        resolve(); // any HTTP response means the server is listening
      });
      req.on("error", () => {
        if (Date.now() >= deadline) {
          reject(new Error(`dashboard did not respond within ${timeoutMs}ms`));
        } else {
          setTimeout(attempt, intervalMs);
        }
      });
      req.setTimeout(2500, () => req.destroy(new Error("probe timeout")));
    };
    attempt();
  });
}

// --- Daemon readiness polling -------------------------------------------
// A foreign process (or a stale daemon) squatting on port 8787 must NOT be
// mistaken for a healthy Iron Jarvis: the client URL is baked to 127.0.0.1:8787,
// so if the wrong thing answers there, the whole app is silently broken. We
// require a real /health 200 from OUR daemon (bearer token) before proceeding.
function waitForDaemon(timeoutMs, intervalMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      // Fail FAST if the daemon child already exited — e.g. serve()'s preflight
      // found a foreign program on the port and exited non-zero. Don't wait 30s.
      if (daemonProc && daemonProc.exitCode !== null && daemonProc.exitCode !== 0) {
        return reject(new Error(`daemon exited early (code ${daemonProc.exitCode}) — port in use?`));
      }
      const req = http.get(
        `http://127.0.0.1:${DAEMON_PORT}/health`,
        { headers: authToken ? { Authorization: `Bearer ${authToken}` } : {} },
        (res) => {
          let body = "";
          res.setEncoding("utf8");
          res.on("data", (c) => (body += c));
          res.on("end", () => {
            // Require OUR daemon's health shape, not just any 200 — a foreign
            // server squatting on the baked port must not pass the gate.
            let ok = false;
            if (res.statusCode === 200) {
              try {
                const d = JSON.parse(body);
                ok = d && d.status === "ok" && !!d.version;
              } catch {
                ok = false;
              }
            }
            ok ? resolve() : retry();
          });
        }
      );
      req.on("error", retry);
      req.setTimeout(2500, () => req.destroy(new Error("probe timeout")));
      function retry() {
        if (Date.now() >= deadline) reject(new Error(`daemon /health not healthy within ${timeoutMs}ms`));
        else setTimeout(attempt, intervalMs);
      }
    };
    attempt();
  });
}

// --- Failed-update recovery sentinel ------------------------------------
// electron-updater/NSIS keep no prior version, so a bad auto-update that won't
// boot would strand the user. Before installing we drop a marker; each launch
// bumps its attempt count; a clean boot clears it; repeated boot failures with
// the marker present trigger a recovery dialog (reinstall the previous release).
function updatePendingFile() {
  return path.join(userDataDir, ".update-pending.json");
}
function markUpdatePending(version) {
  try {
    fs.writeFileSync(updatePendingFile(), JSON.stringify({ version: version || null, attempts: 0 }), "utf8");
  } catch (err) {
    console.error("[update] could not write pending marker:", err && err.message);
  }
}
function readAndBumpUpdatePending() {
  let rec;
  try {
    rec = JSON.parse(fs.readFileSync(updatePendingFile(), "utf8"));
  } catch {
    return null; // no pending update
  }
  rec.attempts = (rec.attempts || 0) + 1;
  try {
    fs.writeFileSync(updatePendingFile(), JSON.stringify(rec), "utf8");
  } catch {
    /* best effort */
  }
  return rec;
}
function clearUpdatePending() {
  try {
    fs.unlinkSync(updatePendingFile());
  } catch {
    /* not present */
  }
}

// --- Window-state persistence -------------------------------------------

function flushWindowState() {
  if (saveBoundsTimer) {
    clearTimeout(saveBoundsTimer);
    saveBoundsTimer = null;
  }
  if (!userDataDir || !mainWin || mainWin.isDestroyed()) return;
  // Don't persist a minimized/fullscreen rectangle — restore should bring back
  // the last "normal" size.
  if (mainWin.isMinimized() || mainWin.isFullScreen()) return;
  windowState.saveBounds(userDataDir, mainWin.getBounds());
}

function scheduleSaveWindowState() {
  if (!mainWin || mainWin.isDestroyed()) return;
  if (mainWin.isMinimized() || mainWin.isFullScreen()) return;
  if (saveBoundsTimer) clearTimeout(saveBoundsTimer);
  saveBoundsTimer = setTimeout(() => {
    saveBoundsTimer = null;
    if (mainWin && !mainWin.isDestroyed() && mainWin.isVisible()) {
      windowState.saveBounds(userDataDir, mainWin.getBounds());
    }
  }, 600);
}

// Compute the BrowserWindow bounds to open with: restore the saved rect when it
// is still visible on a connected display; keep just the size (centered) when
// the saved position is off-screen; otherwise the shipped 1440x900 default.
function initialBounds() {
  const fallback = { ...windowState.DEFAULT_BOUNDS };
  const saved = windowState.loadBounds(userDataDir);
  if (!saved) return { bounds: fallback, center: true };
  if (windowState.isVisibleOnDisplay(saved, screen.getAllDisplays())) {
    return { bounds: saved, center: false };
  }
  // Size is usable but the monitor it lived on is gone -> keep size, recenter.
  return { bounds: { width: saved.width, height: saved.height }, center: true };
}

// --- Windows -------------------------------------------------------------

function createLoadingWindow() {
  loadingWin = new BrowserWindow({
    width: 520,
    height: 380,
    backgroundColor: "#0a0a0f",
    frame: false,
    resizable: false,
    center: true,
    show: true,
    title: "Starting Iron Jarvis…",
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  loadingWin.loadFile(path.join(__dirname, "loading.html"));
}

function createMainWindow() {
  const { bounds, center } = initialBounds();

  mainWin = new BrowserWindow({
    width: bounds.width,
    height: bounds.height,
    ...(center ? { center: true } : { x: bounds.x, y: bounds.y }),
    backgroundColor: "#0a0a0f",
    show: false,
    title: "Iron Jarvis",
    icon: path.join(__dirname, "assets", "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      // Hand the per-install token to preload.js so it can seed localStorage
      // BEFORE the dashboard bundle runs (no 401 race). Empty when token-less.
      additionalArguments: [`--ij-token=${authToken || ""}`],
    },
  });

  mainWin.once("ready-to-show", () => {
    mainWin.show();
    if (loadingWin && !loadingWin.isDestroyed()) loadingWin.close();
    loadingWin = null;
  });

  // Safety net for the token: if the preload's localStorage write didn't take
  // (sandbox/timing), set it from the page's main world and reload ONCE so
  // steady-state requests carry it. When preload already set it (the normal
  // path) the value matches and we DON'T reload (no flicker). Guarded so the
  // reload can happen at most once -> no permanent 401, no reload loop.
  let tokenEnsured = false;
  mainWin.webContents.on("did-finish-load", () => {
    if (tokenEnsured || !authToken) return;
    const lit = JSON.stringify(authToken);
    const js =
      "(() => { try {" +
      `  if (localStorage.getItem('ij_token') !== ${lit}) {` +
      `    localStorage.setItem('ij_token', ${lit}); return 'set';` +
      "  } return 'present';" +
      "} catch (e) { return 'error'; } })()";
    mainWin.webContents
      .executeJavaScript(js)
      .then((result) => {
        tokenEnsured = true;
        if (result === "set" && mainWin && !mainWin.isDestroyed()) {
          // Token was missing when the page first loaded -> reload so the
          // already-issued (and any future) requests re-run WITH the token.
          mainWin.webContents.reload();
        }
      })
      .catch((err) => {
        tokenEnsured = true;
        console.error("[token] localStorage ensure failed:", err && err.message);
      });
  });

  // Open target=_blank / external links in the system browser, not in-app.
  mainWin.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  // Keep navigation inside the dashboard origin; everything else → browser.
  mainWin.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(DASHBOARD_URL) && !url.startsWith(DASHBOARD_PROBE_URL)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  // Persist size/position as the user moves/resizes.
  mainWin.on("resize", scheduleSaveWindowState);
  mainWin.on("move", scheduleSaveWindowState);

  // The reliability fix: a normal window close HIDES to the tray (keeping the
  // daemon + dashboard alive). Only an explicit Quit (isQuitting) really closes.
  mainWin.on("close", (event) => {
    flushWindowState();
    if (!isQuitting) {
      event.preventDefault();
      if (mainWin.isFullScreen()) mainWin.setFullScreen(false);
      mainWin.hide();
    }
  });

  mainWin.on("closed", () => {
    mainWin = null;
  });

  mainWin.loadURL(DASHBOARD_URL);
}

// Show (and if necessary recreate) the main window — used by the tray, the
// global hotkey, and a second app launch.
function showMainWindow() {
  if (mainWin && !mainWin.isDestroyed()) {
    if (mainWin.isMinimized()) mainWin.restore();
    if (!mainWin.isVisible()) mainWin.show();
    mainWin.focus();
  } else {
    // Window was torn down but the app is still alive in the tray -> rebuild it.
    createMainWindow();
  }
}

// --- System tray ---------------------------------------------------------

function createTray() {
  if (tray) return;
  const iconPath = path.join(__dirname, "assets", "icon.png");
  let image;
  try {
    image = nativeImage.createFromPath(iconPath);
  } catch {
    image = nativeImage.createEmpty();
  }
  try {
    tray = new Tray(image.isEmpty() ? nativeImage.createEmpty() : image);
  } catch (err) {
    console.error("[tray] could not create tray:", err && err.message);
    return;
  }
  tray.setToolTip("Iron Jarvis — running");
  const menu = Menu.buildFromTemplate([
    { label: "Open Iron Jarvis", click: () => showMainWindow() },
    { type: "separator" },
    {
      label: "Quit Iron Jarvis",
      click: () => {
        isQuitting = true;
        app.quit();
      },
    },
  ]);
  tray.setContextMenu(menu);
  // Left-click / double-click both reopen the window (idempotent).
  tray.on("click", () => showMainWindow());
  tray.on("double-click", () => showMainWindow());
}

// --- Auto-update (packaged builds only) ---------------------------------
// Dev mode uses the in-app git self-update (ironjarvis self-update / the
// Updates page); a packaged installer self-updates from GitHub Releases via
// electron-updater (publish config in package.json -> build.publish).

function checkForUpdates() {
  if (!IS_PACKAGED) return;
  let autoUpdater;
  try {
    ({ autoUpdater } = require("electron-updater"));
  } catch (err) {
    console.error("[update] electron-updater unavailable:", err.message);
    return;
  }
  autoUpdater.autoDownload = true;
  autoUpdater.on("error", (err) => console.error("[update] error:", err && err.message));
  autoUpdater.on("update-available", (info) =>
    console.log("[update] available:", info && info.version)
  );
  autoUpdater.on("update-downloaded", (info) => {
    const choice = dialog.showMessageBoxSync({
      type: "info",
      buttons: ["Restart now", "Later"],
      defaultId: 0,
      title: "Iron Jarvis — update ready",
      message: `Version ${info && info.version} has been downloaded.`,
      detail: "Restart to install the update.",
    });
    if (choice === 0) {
      isQuitting = true; // allow the window to actually close
      // Drop a recovery marker so the NEXT boot can detect a broken update.
      markUpdatePending(info && info.version);
      // Kill the daemon+dashboard SYNCHRONOUSLY first (shutdown() now blocks until
      // the process tree is dead) so NSIS can overwrite the locked frozen exe;
      // do NOT pre-set shuttingDown (that would make shutdown() early-return and
      // orphan the children — the very bug that bricks the update).
      shutdown();
      autoUpdater.quitAndInstall(false, true);
    }
  });
  autoUpdater
    .checkForUpdatesAndNotify()
    .catch((err) => console.error("[update] check failed:", err && err.message));
}

// --- Application menu ----------------------------------------------------

function buildMenu() {
  const template = [
    {
      label: "Iron Jarvis",
      submenu: [
        { label: "Open / Show Window", accelerator: HOTKEY, click: () => showMainWindow() },
        { type: "separator" },
        { role: "reload" },
        { role: "forceReload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        {
          label: "Quit Iron Jarvis",
          accelerator: "CommandOrControl+Q",
          click: () => {
            isQuitting = true;
            app.quit();
          },
        },
      ],
    },
    { role: "editMenu" },
    {
      label: "View",
      submenu: [
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    { role: "windowMenu" },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// --- Startup sequence ----------------------------------------------------

async function startup() {
  userDataDir = app.getPath("userData"); // writable per-user state dir
  authToken = getOrCreateToken();
  installAuthHeaderInjection();
  // If a just-applied update exists, bump its attempt count now; a clean boot
  // below clears it, repeated failures trigger the recovery dialog.
  const pendingUpdate = readAndBumpUpdatePending();

  buildMenu();
  createTray();
  createLoadingWindow();
  registerHotkey();

  if (IS_PACKAGED) {
    // PACKAGED: frozen daemon exe + standalone dashboard run by Electron's Node.
    // No Python/uv/Node/pnpm required on the user's machine.
    const stateDir = userDataDir; // the daemon's .ironjarvis lives here
    // 1) Frozen daemon. Must serve on 8787 to match the build-time-baked client URL.
    daemonProc = spawnChild(
      "daemon",
      DAEMON_EXE,
      ["serve", "--host", "127.0.0.1", "--port", String(DAEMON_PORT), "--root", stateDir],
      path.dirname(DAEMON_EXE),
      // Blank out any ambient IRONJARVIS_HOME (e.g. left over from source/dev use)
      // so the packaged app's per-install userData home always wins — an empty
      // value makes resolve_home() fall back to --root (userData/.ironjarvis).
      { IRONJARVIS_TOKEN: authToken, IRONJARVIS_HOME: "" },
      false
    );
    // 2) Next.js standalone server (server.js) via Electron's bundled Node.
    dashboardProc = spawnChild(
      "dashboard",
      process.execPath,
      [DASHBOARD_SERVER],
      path.dirname(DASHBOARD_SERVER),
      {
        ELECTRON_RUN_AS_NODE: "1",
        PORT: String(DASHBOARD_PORT),
        HOSTNAME: "127.0.0.1",
        NODE_ENV: "production",
      },
      false
    );
  } else {
    // DEV: drive the repo via uv + pnpm; preflight that they're installed.
    const [hasUv, hasPnpm] = await Promise.all([
      commandExists("uv"),
      commandExists("pnpm"),
    ]);
    const missing = [];
    if (!hasUv) missing.push("uv          → https://docs.astral.sh/uv/getting-started/installation/");
    if (!hasPnpm) missing.push("pnpm        → https://pnpm.io/installation");
    if (missing.length) {
      dialog.showErrorBox(
        "Iron Jarvis — missing prerequisites",
        "Could not find the required tool(s) on your PATH:\n\n" +
          "  - " + missing.join("\n  - ") + "\n\n" +
          "Iron Jarvis (dev mode) launches the local repo's Python daemon (via uv) and\n" +
          "the Next.js dashboard (via pnpm). Install the tool(s) above, then relaunch."
      );
      isQuitting = true;
      shutdown();
      app.quit();
      return;
    }
    // 1) Python daemon (FastAPI on DAEMON_PORT) with the per-install token.
    daemonProc = spawnChild(
      "daemon",
      "uv",
      ["run", "ironjarvis", "serve", "--host", "127.0.0.1", "--port", String(DAEMON_PORT), "--root", REPO_ROOT],
      REPO_ROOT,
      { IRONJARVIS_TOKEN: authToken }
    );
    // 2) Next.js dashboard. `next start` honours the PORT env var.
    dashboardProc = spawnChild("dashboard", "pnpm", ["start"], DASHBOARD_DIR, {
      PORT: String(DASHBOARD_PORT),
    });
  }

  // 3) Health-gate the DAEMON first (guards a foreign process squatting on the
  //    baked port), then the dashboard, then swap the splash for the real window.
  try {
    await waitForDaemon(STARTUP_TIMEOUT_MS, 500);
  } catch (err) {
    handleStartupFailure(
      "Iron Jarvis — daemon did not start",
      `The Iron Jarvis daemon did not answer on http://127.0.0.1:${DAEMON_PORT} within ` +
        `${Math.round(STARTUP_TIMEOUT_MS / 1000)}s.\n\n` +
        `Most common cause: another program is already using port ${DAEMON_PORT}. Close it, ` +
        "then relaunch. Check the [daemon] logs for details.",
      pendingUpdate
    );
    return;
  }
  try {
    await waitForDashboard(STARTUP_TIMEOUT_MS, 500);
  } catch (err) {
    handleStartupFailure(
      "Iron Jarvis — dashboard did not start",
      `The dashboard at ${DASHBOARD_PROBE_URL} did not respond within ` +
        `${Math.round(STARTUP_TIMEOUT_MS / 1000)}s.\n\n` +
        (IS_PACKAGED
          ? "Check the [dashboard] logs for details."
          : "Most common cause: the dashboard has not been built yet. Build it once:\n\n" +
            "    cd dashboard\n    pnpm install\n    pnpm build\n\n" +
            "Then relaunch Iron Jarvis. Check the terminal for [daemon]/[dashboard] logs."),
      pendingUpdate
    );
    return;
  }

  clearUpdatePending(); // a clean, healthy boot means the current version is good
  createMainWindow();
  checkForUpdates();
}

// Shared startup-failure path. After a just-applied update that repeatedly fails
// to boot, offer a concrete recovery (reinstall the previous release) instead of
// looping on a generic error — electron-updater/NSIS keep no prior version.
function handleStartupFailure(title, message, pendingUpdate) {
  if (pendingUpdate && pendingUpdate.attempts >= 2) {
    const choice = dialog.showMessageBoxSync({
      type: "error",
      buttons: ["Open Releases page", "Quit"],
      defaultId: 0,
      title: "Iron Jarvis — update failed to start",
      message: `The update to version ${pendingUpdate.version || "(unknown)"} is not starting.`,
      detail:
        "Reinstall the previous working version from the Releases page, then relaunch. " +
        "Your data (settings, sessions, keys) is untouched.",
    });
    if (choice === 0) {
      shell.openExternal("https://github.com/RealDealCPA-VR/Iron-Jarvis/releases");
    }
  } else {
    dialog.showErrorBox(title, message);
  }
  isQuitting = true;
  shutdown();
  app.quit();
}

// --- Global hotkey -------------------------------------------------------

function registerHotkey() {
  try {
    const ok = globalShortcut.register(HOTKEY, () => showMainWindow());
    if (!ok) console.warn(`[hotkey] ${HOTKEY} registration failed (already taken?)`);
  } catch (err) {
    console.error("[hotkey] registration error:", err && err.message);
  }
}

// --- App lifecycle -------------------------------------------------------

// Single-instance: a second launch focuses/opens the existing window instead of
// spawning a duplicate daemon/dashboard pair.
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWin && !mainWin.isDestroyed()) {
      showMainWindow();
    } else if (loadingWin && !loadingWin.isDestroyed()) {
      if (loadingWin.isMinimized()) loadingWin.restore();
      loadingWin.focus();
    }
    // else still booting: the in-flight startup will open the window itself.
  });

  app.whenReady().then(startup);

  app.on("activate", () => {
    // macOS: re-open/show a window if the app is still alive. Don't create one
    // mid-boot (the splash is up and startup will open the real window).
    if (shuttingDown) return;
    if (mainWin && !mainWin.isDestroyed()) {
      showMainWindow();
    } else if (!loadingWin) {
      createMainWindow();
    }
  });

  // ALWAYS-ON: do NOT quit when the window is closed. The window hides to the
  // tray (see the 'close' handler) and the daemon + dashboard keep running.
  // Teardown happens only via an explicit Quit (isQuitting -> before-quit).
  app.on("window-all-closed", () => {
    // Intentionally empty: stay resident in the tray.
  });

  app.on("before-quit", () => {
    isQuitting = true;
    flushWindowState();
    shutdown();
  });

  app.on("will-quit", () => {
    globalShortcut.unregisterAll();
    if (tray) {
      try {
        tray.destroy();
      } catch {
        /* ignore */
      }
      tray = null;
    }
  });

  // Belt-and-suspenders: kill children if the main process is torn down.
  process.on("exit", shutdown);
  process.on("SIGINT", () => {
    isQuitting = true;
    shutdown();
    app.quit();
  });
  process.on("SIGTERM", () => {
    isQuitting = true;
    shutdown();
    app.quit();
  });
}
