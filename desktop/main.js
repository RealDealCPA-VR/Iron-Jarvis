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
//   5. CLOSE BEHAVIOR (user-controlled): closing the window can either hide to a
//      system tray (daemon + dashboard keep running so scheduler/cron/webhooks
//      survive) or fully quit. The choice is a persisted preference
//      (desktop-settings.json); when unset, the first close prompts the user
//      (default: quit) and can remember the answer. A checkable "Keep running in
//      background" item in the tray + app menu flips it any time.
//   6. RELIABILITY: child stdout/stderr is teed to rotating log files under
//      userData/logs (a Start-Menu launch has no console — without this, failures
//      are undiagnosable); crashed children auto-restart with backoff and notify
//      after repeated failures; Quit asks the daemon to exit gracefully (POST
//      /shutdown) before force-killing; updates re-check periodically, not just
//      at boot; optional start-at-login boots hidden to the tray (--hidden).
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
  Notification,
  ipcMain,
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
// 8788 (next to the daemon's 8787), NOT 3000: every Next/CRA dev server on the
// machine defaults to 3000, and a foreign app squatting there would break Iron
// Jarvis. The daemon's Host/Origin guard + CORS trust any loopback origin, so
// the port choice needs no daemon-side allowlist change.
const DASHBOARD_PORT = parseInt(process.env.IJ_DASHBOARD_PORT || "8788", 10);

const DASHBOARD_URL = `http://localhost:${DASHBOARD_PORT}`;
const DASHBOARD_PROBE_URL = `http://127.0.0.1:${DASHBOARD_PORT}/`;
// Packaged cold boots are slow the first time (AV scans the PyInstaller-frozen
// daemon exe) — give them 90s; dev keeps the tight 30s feedback loop.
const STARTUP_TIMEOUT_MS = IS_PACKAGED ? 90000 : 30000;

const HOTKEY = "CommandOrControl+Shift+J"; // show/focus the main window
const SPOTLIGHT_HOTKEY = "CommandOrControl+Shift+Space"; // quick-task overlay

// --hidden: boot straight to the tray with no window (start-at-login mode).
const START_HIDDEN = process.argv.includes("--hidden");

// --- State ---------------------------------------------------------------

let daemonProc = null;
let dashboardProc = null;
let loadingWin = null;
let mainWin = null;
let spotlightWin = null;
let tray = null;
let shuttingDown = false;
// isQuitting distinguishes "user wants to fully exit" (tear everything down)
// from a normal window close (just hide to the tray, keep the daemon alive).
let isQuitting = false;
let authToken = null; // per-install bearer token (also passed to the daemon)
let userDataDir = null; // app.getPath('userData') — set once app is ready
let saveBoundsTimer = null; // debounce timer for window-state writes
// What a window close does: true = keep running (hide to tray), false = fully
// quit, null = not chosen yet (prompt on close). Persisted to
// desktop-settings.json; the fresh-install default is "quit".
let keepRunningPref = null;
// Set once both children pass their health gates — lets a second launch (or the
// tray) reopen the window after a --hidden boot that never created one.
let bootComplete = false;
// before-quit runs async teardown (graceful daemon stop) exactly once.
let quitProcessed = false;
// {version} once an update has finished downloading and is ready to install —
// surfaced as a clickable notification + a top-of-tray "Restart to update" item.
let pendingUpdateInfo = null;

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
      // SCOPE: the pattern above matches ANY loopback port (Electron rejects
      // ports in match patterns), but the bearer token must ONLY ever reach
      // OUR daemon — attaching it to some other local app's port leaks it.
      const url = details.url || "";
      const isDaemon =
        url.startsWith(`http://127.0.0.1:${DAEMON_PORT}/`) ||
        url.startsWith(`http://localhost:${DAEMON_PORT}/`);
      if (isDaemon && !headers.Authorization && !headers.authorization) {
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

// --- Media (microphone) permission --------------------------------------
// The dashboard's voice dictation calls getUserMedia. Electron auto-approves
// permission REQUESTS by default, but with NO permission-CHECK handler a
// synchronous media check can fail — surfacing to the page as an "audio-capture"
// error ("No microphone found"). We serve only our own trusted, bundled
// dashboard over loopback, so grant media (mic/camera) on both the async request
// AND the sync check. Everything else stays at Electron's default (approved),
// since the renderer can only ever load the local dashboard (will-navigate
// keeps it in-origin).
function installMediaPermissions() {
  try {
    const ses = session.defaultSession;
    // Approve permission REQUESTS (matches Electron's default) AND — the piece
    // that was missing — the synchronous permission CHECK, which getUserMedia
    // consults; without it a media check can be denied and the page reports
    // "No microphone found".
    ses.setPermissionRequestHandler((_wc, _permission, callback) => callback(true));
    ses.setPermissionCheckHandler(() => true);
  } catch (err) {
    console.error("[permissions] media handler unavailable:", err && err.message);
  }
}

// --- Desktop settings: close-to-tray preference -------------------------
// "Keep running in background" is user-controlled and persisted next to the
// other per-install state (token.txt, window-state.json). An absent/invalid
// file means "undecided" -> the window-close handler prompts once (default:
// quit) and can remember the answer.

function desktopSettingsFile() {
  return path.join(userDataDir, "desktop-settings.json");
}

function loadDesktopSettings() {
  try {
    const raw = JSON.parse(fs.readFileSync(desktopSettingsFile(), "utf8"));
    keepRunningPref =
      raw && typeof raw.keepRunningInBackground === "boolean"
        ? raw.keepRunningInBackground
        : null;
  } catch {
    keepRunningPref = null; // not created yet -> undecided
  }
}

function setKeepRunningPref(value) {
  keepRunningPref = !!value;
  try {
    fs.writeFileSync(
      desktopSettingsFile(),
      JSON.stringify({ keepRunningInBackground: keepRunningPref }),
      "utf8"
    );
  } catch (err) {
    console.error("[settings] could not persist desktop-settings.json:", err && err.message);
  }
  refreshMenus(); // reflect the new state in the tray + app-menu checkboxes
}

// Hide the window to the tray, keeping the daemon + dashboard alive.
// MEMORY: a hidden BrowserWindow keeps its whole renderer tree resident
// (~hundreds of MB) — destroy it after hiding and let showMainWindow() rebuild
// it on demand. hide() first so the visual response is instant; destroy() (not
// close()) skips the 'close' handler, so no prompt/recursion.
function hideToTray() {
  if (!mainWin || mainWin.isDestroyed()) return;
  flushWindowState();
  if (mainWin.isFullScreen()) mainWin.setFullScreen(false);
  const win = mainWin;
  win.hide();
  setImmediate(() => {
    try {
      if (!win.isDestroyed()) win.destroy(); // fires 'closed' -> mainWin = null
    } catch {
      /* already gone */
    }
  });
}

// --- Start at login --------------------------------------------------------
// A daily driver with "keep running in background" wants to survive reboots.
// Packaged builds only: in dev the login item would point at electron.exe and
// leave junk startup entries behind.

function getStartAtLogin() {
  try {
    return app.getLoginItemSettings().openAtLogin;
  } catch {
    return false;
  }
}

function setStartAtLogin(enabled) {
  try {
    app.setLoginItemSettings({
      openAtLogin: !!enabled,
      args: ["--hidden"], // boot straight to the tray, no window flash at login
    });
  } catch (err) {
    console.error("[login-item] could not update:", err && err.message);
  }
  refreshMenus();
}

// --- Child log files ------------------------------------------------------
// A Start-Menu launch has NO console: without a file sink every [daemon] /
// [dashboard] line is lost and a 2am failure is undiagnosable. Each child gets
// userData/logs/<label>.log with a simple size rotation (current + .1).

const LOG_MAX_BYTES = 5 * 1024 * 1024;
const _fileLoggers = {}; // label -> write(chunk)

function fileLogger(label) {
  if (_fileLoggers[label]) return _fileLoggers[label];
  let stream = null;
  let size = 0;
  let logPath = null;
  const write = (chunk) => {
    // Logging must never break the app — swallow every fs error.
    try {
      if (!stream) {
        const dir = path.join(userDataDir, "logs");
        fs.mkdirSync(dir, { recursive: true });
        logPath = path.join(dir, `${label}.log`);
        try {
          size = fs.statSync(logPath).size;
        } catch {
          size = 0;
        }
        stream = fs.createWriteStream(logPath, { flags: "a" });
      }
      if (size > LOG_MAX_BYTES) {
        try {
          stream.end();
          fs.rmSync(`${logPath}.1`, { force: true });
          fs.renameSync(logPath, `${logPath}.1`);
        } catch {
          /* rotation is best-effort */
        }
        size = 0;
        stream = fs.createWriteStream(logPath, { flags: "a" });
      }
      const s = String(chunk);
      size += Buffer.byteLength(s);
      stream.write(s);
    } catch {
      /* never throw from a logger */
    }
  };
  _fileLoggers[label] = write;
  return write;
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

  const toFile = fileLogger(label);
  if (child.stdout) {
    child.stdout.on("data", (d) => {
      process.stdout.write(`[${label}] ${d}`);
      toFile(d);
    });
  }
  if (child.stderr) {
    child.stderr.on("data", (d) => {
      process.stderr.write(`[${label}] ${d}`);
      toFile(d);
    });
  }
  child.on("error", (err) => {
    // With shell:true the inner command (uv/pnpm) won't raise ENOENT here —
    // that's covered by the preflight check below. This catches shell failures.
    console.error(`[${label}] spawn error:`, err.message);
    toFile(`[main] spawn error: ${err.message}\n`);
  });
  child.on("exit", (code, signal) => {
    console.log(`[${label}] exited (code=${code}, signal=${signal}, pid=${child.pid})`);
    toFile(`[main] exited (code=${code}, signal=${signal}, pid=${child.pid})\n`);
  });

  console.log(`[${label}] started pid=${child.pid}: ${command} ${args.join(" ")} (cwd=${cwd})`);
  toFile(`[main] ${new Date().toISOString()} started pid=${child.pid}: ${command} ${args.join(" ")}\n`);
  return child;
}

// --- Crash supervisor -----------------------------------------------------
// A daemon that dies at 2am while hidden in the tray must NOT stay dead with
// the tray still claiming "running" — schedules/webhooks would be silently off
// until a manual relaunch. Unexpected exits restart with backoff; repeated
// fast crashes surface a notification instead of looping forever silently.

const RESTART_BACKOFF_MS = [1000, 5000, 15000, 60000];
const _services = {}; // label -> { spawnFn, restarts, lastStart }

function startService(label, spawnFn) {
  const rec = _services[label] || (_services[label] = { restarts: 0, lastStart: 0 });
  rec.spawnFn = spawnFn;
  rec.lastStart = Date.now();
  const child = spawnFn();
  if (label === "daemon") daemonProc = child;
  else if (label === "dashboard") dashboardProc = child;
  child.on("exit", () => {
    if (shuttingDown || isQuitting) return; // expected teardown
    const uptime = Date.now() - rec.lastStart;
    if (uptime > 5 * 60 * 1000) rec.restarts = 0; // ran healthy — reset the ladder
    rec.restarts += 1;
    const delay = RESTART_BACKOFF_MS[Math.min(rec.restarts - 1, RESTART_BACKOFF_MS.length - 1)];
    console.error(`[${label}] unexpected exit — restart #${rec.restarts} in ${delay}ms`);
    fileLogger(label)(`[main] unexpected exit — restart #${rec.restarts} in ${delay}ms\n`);
    if (rec.restarts === 3) notifyCrashLoop(label);
    setTimeout(() => {
      if (!shuttingDown && !isQuitting) startService(label, rec.spawnFn);
    }, delay);
  });
  return child;
}

function notifyCrashLoop(label) {
  const logsDir = path.join(userDataDir || "", "logs");
  try {
    if (tray) tray.setToolTip(`Iron Jarvis — ${label} is restarting repeatedly (check logs)`);
  } catch {
    /* tray may be gone */
  }
  try {
    new Notification({
      title: "Iron Jarvis — problem",
      body: `The ${label} keeps crashing and is being restarted. Logs: ${logsDir}`,
    }).show();
  } catch {
    /* notifications unavailable */
  }
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

// Ask the daemon to exit cleanly (POST /shutdown -> uvicorn SIGTERM -> lifespan
// shutdown) and wait briefly for the process to die. Resolves true when it
// exited by itself; false means the caller should force-kill. The auto-update
// path deliberately SKIPS this and calls shutdown() synchronously — NSIS needs
// the process tree dead before it returns.
function requestDaemonShutdown(timeoutMs) {
  return new Promise((resolve) => {
    if (!daemonProc || daemonProc.exitCode !== null || daemonProc.signalCode !== null) {
      return resolve(true); // never started or already gone
    }
    try {
      const req = http.request(
        {
          host: "127.0.0.1",
          port: DAEMON_PORT,
          path: "/shutdown",
          method: "POST",
          headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
        },
        (res) => res.resume()
      );
      req.on("error", () => {
        /* daemon not answering — the force-kill fallback covers it */
      });
      req.setTimeout(1000, () => req.destroy(new Error("shutdown request timeout")));
      req.end();
    } catch {
      return resolve(false);
    }
    const deadline = Date.now() + timeoutMs;
    const timer = setInterval(() => {
      const gone =
        !daemonProc || daemonProc.exitCode !== null || daemonProc.signalCode !== null;
      if (gone || Date.now() >= deadline) {
        clearInterval(timer);
        resolve(gone);
      }
    }, 100);
  });
}

// --- Dashboard readiness polling ----------------------------------------
// Like the daemon gate below, this must not be fooled by a FOREIGN server on
// the port: "any HTTP response" would happily load someone else's app into the
// Iron Jarvis window. Require the dashboard's own marker (its <title>) in the
// response body before declaring ready.

function waitForDashboard(timeoutMs, intervalMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const retry = (why) => {
        if (Date.now() >= deadline) {
          reject(
            new Error(
              `dashboard did not answer with the Iron Jarvis app within ${timeoutMs}ms` +
                (why ? ` (${why})` : "")
            )
          );
        } else {
          setTimeout(attempt, intervalMs);
        }
      };
      const req = http.get(DASHBOARD_PROBE_URL, (res) => {
        let body = "";
        res.setEncoding("utf8");
        res.on("data", (c) => {
          if (body.length < 256 * 1024) body += c; // cap: the marker is in <head>
        });
        res.on("end", () => {
          if (/iron\s*jarvis/i.test(body)) resolve();
          else retry("a different app answered on this port");
        });
        res.on("error", () => retry());
      });
      req.on("error", () => retry());
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

  // User-controlled close behavior. When the preference is set we honor it
  // directly; when it's undecided we prompt once (default button = Quit, the
  // fresh-install default) and optionally remember the answer. An explicit Quit
  // (isQuitting, e.g. tray/app-menu Quit) always falls straight through.
  mainWin.on("close", (event) => {
    flushWindowState();
    if (isQuitting) return;

    if (keepRunningPref === true) {
      event.preventDefault();
      hideToTray();
      return;
    }
    if (keepRunningPref === false) {
      // Fully quit: let this close proceed; before-quit tears down the children.
      isQuitting = true;
      app.quit();
      return;
    }

    // Undecided -> ask. Cancel the close now and act on the async answer. (The
    // sync dialog can't return the checkbox state, so we use the async form and
    // always preventDefault first, then hide/quit once the user responds.)
    event.preventDefault();
    dialog
      .showMessageBox(mainWin, {
        type: "question",
        buttons: ["Keep running", "Quit completely"],
        defaultId: 1, // Enter = Quit (the fresh-install default)
        cancelId: 0, // Esc aborts the teardown (safe: keep running)
        noLink: true,
        title: "Close Iron Jarvis?",
        message: "Keep Iron Jarvis running in the background?",
        detail:
          "Keeping it running lets schedules, cron jobs, and webhooks stay active " +
          "while the window is closed. Quitting stops everything until you next open the app.",
        checkboxLabel: "Remember my choice",
        checkboxChecked: false,
      })
      .then(({ response, checkboxChecked }) => {
        const keepRunning = response === 0;
        if (checkboxChecked) setKeepRunningPref(keepRunning);
        if (keepRunning) {
          hideToTray();
        } else {
          isQuitting = true;
          app.quit();
        }
      })
      .catch((err) => {
        // On a dialog failure don't tear anything down — hide to the tray; the
        // user can still Quit explicitly from the tray/app menu.
        console.error("[close] prompt failed:", err && err.message);
        hideToTray();
      });
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

// --- Spotlight: global quick-task overlay --------------------------------
// A frameless always-on-top input that opens ANYWHERE in Windows on
// Ctrl+Shift+Space: type a task, Enter, and an agent runs it in the
// background — a notification (click -> the session) fires when it's done. This
// is the daily-driver gesture that makes Iron Jarvis ambient, not an app you
// have to go open.

// A tiny promise-based HTTP call to OUR daemon from the MAIN process (Node http,
// so no browser Origin/CORS — the Host/Origin guard passes) with the bearer.
function daemonRequest(method, apiPath, body) {
  return new Promise((resolve, reject) => {
    const payload = body ? Buffer.from(JSON.stringify(body)) : null;
    const req = http.request(
      {
        host: "127.0.0.1",
        port: DAEMON_PORT,
        path: apiPath,
        method,
        headers: {
          "Content-Type": "application/json",
          ...(payload ? { "Content-Length": payload.length } : {}),
          ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
        },
      },
      (res) => {
        let data = "";
        res.setEncoding("utf8");
        res.on("data", (c) => (data += c));
        res.on("end", () => {
          let json = null;
          try {
            json = data ? JSON.parse(data) : null;
          } catch {
            /* non-JSON */
          }
          if (res.statusCode >= 200 && res.statusCode < 300) resolve(json);
          else reject(new Error((json && json.detail) || `HTTP ${res.statusCode}`));
        });
      }
    );
    req.on("error", reject);
    req.setTimeout(15000, () => req.destroy(new Error("daemon request timed out")));
    if (payload) req.write(payload);
    req.end();
  });
}

function createSpotlightWindow() {
  if (spotlightWin && !spotlightWin.isDestroyed()) return spotlightWin;
  const { width } = screen.getPrimaryDisplay().workAreaSize;
  const w = 620;
  spotlightWin = new BrowserWindow({
    width: w,
    height: 150,
    x: Math.round((width - w) / 2),
    y: 180,
    frame: false,
    transparent: true,
    resizable: false,
    movable: true,
    show: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    fullscreenable: false,
    backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(__dirname, "spotlight-preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  spotlightWin.setAlwaysOnTop(true, "screen-saver");
  spotlightWin.loadFile(path.join(__dirname, "spotlight.html"));
  // Close it if it loses focus (feels like a real spotlight).
  spotlightWin.on("blur", () => {
    if (spotlightWin && !spotlightWin.isDestroyed()) spotlightWin.hide();
  });
  spotlightWin.on("closed", () => {
    spotlightWin = null;
  });
  return spotlightWin;
}

function toggleSpotlight() {
  const win = createSpotlightWindow();
  if (win.isVisible()) {
    win.hide();
    return;
  }
  win.show();
  win.focus();
  win.webContents.send("spotlight:show"); // clear + focus the input
}

// Run a spotlight task: start a background session, then poll for completion and
// fire a clickable "done" notification (click -> open the session).
async function runSpotlightTask(task) {
  const created = await daemonRequest("POST", "/sessions", {
    task,
    agent_type: "builder",
    wait: false,
  });
  const id = created && created.id;
  if (!id) throw new Error("could not start the task");
  // Poll for completion (up to ~15 min) then notify. Best-effort — a failure to
  // poll/notify never surfaces to the user beyond the "started" they already saw.
  let elapsed = 0;
  const timer = setInterval(async () => {
    elapsed += 4000;
    let s = null;
    try {
      s = await daemonRequest("GET", `/sessions/${id}`, null);
    } catch {
      /* transient */
    }
    const status = s && s.status;
    if (status === "completed" || status === "failed" || elapsed > 15 * 60 * 1000) {
      clearInterval(timer);
      try {
        const note = new Notification({
          title:
            status === "failed"
              ? "Task failed"
              : `Task done: ${String(task).slice(0, 60)}`,
          body:
            (s && s.summary ? String(s.summary).slice(0, 140) : "Click to open the result."),
        });
        note.on("click", () => {
          showMainWindow();
          if (mainWin && !mainWin.isDestroyed()) {
            mainWin.loadURL(`${DASHBOARD_URL}/sessions/${id}`);
          }
        });
        note.show();
      } catch {
        /* notifications unavailable */
      }
    }
  }, 4000);
  return { ok: true, id };
}

function installSpotlightIpc() {
  ipcMain.handle("spotlight:submit", async (_e, task) => {
    const t = String(task || "").trim();
    if (!t) return { ok: false, error: "empty task" };
    try {
      return await runSpotlightTask(t);
    } catch (err) {
      return { ok: false, error: (err && err.message) || String(err) };
    }
  });
  ipcMain.on("spotlight:close", () => {
    if (spotlightWin && !spotlightWin.isDestroyed()) spotlightWin.hide();
  });
}

// --- System tray ---------------------------------------------------------

// Built fresh each time so the "Keep running in background" checkbox reflects
// the current preference (toggled from either menu or set by the close prompt).
function buildTrayContextMenu() {
  const template = [];
  // A downloaded update surfaces as a PROMINENT, one-click tray item at the very
  // top (plus the OS notification) so it's never buried in an easy-to-miss modal.
  if (pendingUpdateInfo) {
    template.push(
      {
        label: `Restart to update (v${pendingUpdateInfo.version})`,
        click: () => applyPendingUpdate(),
      },
      { type: "separator" }
    );
  }
  template.push(
    { label: "Open Iron Jarvis", click: () => showMainWindow() },
    { label: "Quick task…  (Ctrl+Shift+Space)", click: () => toggleSpotlight() },
    { type: "separator" },
    {
      label: "Keep running in background",
      type: "checkbox",
      checked: keepRunningPref === true,
      click: (item) => setKeepRunningPref(item.checked),
    }
  );
  if (IS_PACKAGED) {
    template.push({
      label: "Start at login",
      type: "checkbox",
      checked: getStartAtLogin(),
      click: (item) => setStartAtLogin(item.checked),
    });
  }
  template.push(
    { type: "separator" },
    {
      label: "Quit Iron Jarvis",
      click: () => {
        isQuitting = true;
        app.quit();
      },
    }
  );
  return Menu.buildFromTemplate(template);
}

function refreshTrayMenu() {
  if (!tray) return;
  try {
    tray.setContextMenu(buildTrayContextMenu());
  } catch (err) {
    console.error("[tray] could not refresh menu:", err && err.message);
  }
}

// Rebuild both menus so their "Keep running in background" checkboxes stay in
// sync after a toggle (from either menu) or a close-prompt answer.
function refreshMenus() {
  buildMenu();
  refreshTrayMenu();
}

function createTray() {
  if (tray) return;
  // Windows renders tray icons crispest from .ico; fall back to the png.
  const icoPath = path.join(__dirname, "assets", "icon.ico");
  const iconPath = fs.existsSync(icoPath)
    ? icoPath
    : path.join(__dirname, "assets", "icon.png");
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
  tray.setContextMenu(buildTrayContextMenu());
  // Left-click / double-click both reopen the window (idempotent).
  tray.on("click", () => showMainWindow());
  tray.on("double-click", () => showMainWindow());
}

// --- Auto-update (packaged builds only) ---------------------------------
// Dev mode uses the in-app git self-update (ironjarvis self-update / the
// Updates page); a packaged installer self-updates from GitHub Releases via
// electron-updater (publish config in package.json -> build.publish).

// A tray app can stay resident for WEEKS — checking only at boot means never
// seeing an update. init once (listeners), then re-check every 30 minutes so a
// freshly-pushed release is detected + downloaded promptly (not up to 12h later).
const UPDATE_RECHECK_MS = 30 * 60 * 1000;
let _autoUpdater = null;

// Install a downloaded update: kill the daemon+dashboard SYNCHRONOUSLY first
// (shutdown() blocks until the process tree is dead) so NSIS can overwrite the
// locked frozen exe — do NOT pre-set shuttingDown (that would make shutdown()
// early-return and ORPHAN the children, the very bug that bricks the update) —
// then quit + install + relaunch. Shared by the notification click, the tray
// item, and the in-app "Restart to update" affordance.
function applyPendingUpdate() {
  if (!pendingUpdateInfo || !_autoUpdater) return;
  isQuitting = true; // allow the window to actually close
  markUpdatePending(pendingUpdateInfo.version); // recovery marker for a bad update
  shutdown();
  try {
    _autoUpdater.quitAndInstall(false, true);
  } catch (err) {
    console.error("[update] quitAndInstall failed:", err && err.message);
  }
}

function initUpdater() {
  if (_autoUpdater || !IS_PACKAGED) return _autoUpdater;
  try {
    ({ autoUpdater: _autoUpdater } = require("electron-updater"));
  } catch (err) {
    console.error("[update] electron-updater unavailable:", err.message);
    return null;
  }
  const autoUpdater = _autoUpdater;
  autoUpdater.autoDownload = true;
  // Also apply a downloaded update on a REAL Quit (tray/menu Quit) as a bonus.
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.on("error", (err) => console.error("[update] error:", err && err.message));
  autoUpdater.on("update-available", (info) =>
    console.log("[update] available:", info && info.version)
  );
  autoUpdater.on("update-downloaded", (info) => {
    // Surface a ready update PROMINENTLY but non-intrusively (the user chose
    // notify + one-click): a clickable OS notification + a top-of-tray
    // "Restart to update" item. NOTHING restarts until they choose to — so a
    // running agent session is never interrupted by surprise.
    pendingUpdateInfo = { version: (info && info.version) || "" };
    console.log("[update] downloaded + ready:", pendingUpdateInfo.version);
    refreshTrayMenu(); // inserts the "Restart to update (vX)" item
    try {
      if (tray) {
        tray.setToolTip(
          `Iron Jarvis — update v${pendingUpdateInfo.version} ready (restart to install)`
        );
      }
    } catch {
      /* tray may be gone */
    }
    try {
      const note = new Notification({
        title: `Iron Jarvis v${pendingUpdateInfo.version} is ready`,
        body: "Click to restart and install now — or do it later from the tray icon.",
      });
      note.on("click", () => applyPendingUpdate());
      note.show();
    } catch (err) {
      console.error("[update] notification unavailable:", err && err.message);
    }
  });
  return autoUpdater;
}

function checkForUpdates() {
  const autoUpdater = initUpdater();
  if (!autoUpdater) return;
  // checkForUpdates (not ...AndNotify): autoDownload fetches it, and our own
  // update-downloaded handler shows the clickable notification — we don't want
  // electron-updater's separate default notification competing with ours.
  autoUpdater
    .checkForUpdates()
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
        {
          label: "Keep running in background when window is closed",
          type: "checkbox",
          checked: keepRunningPref === true,
          click: (item) => setKeepRunningPref(item.checked),
        },
        ...(IS_PACKAGED
          ? [
              {
                label: "Start at login (hidden in tray)",
                type: "checkbox",
                checked: getStartAtLogin(),
                click: (item) => setStartAtLogin(item.checked),
              },
            ]
          : []),
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
  // Windows toast notifications (crash-loop, hotkey conflicts) need a stable
  // AppUserModelID that matches the installer's appId.
  app.setAppUserModelId("com.realdealcpa.ironjarvis");
  loadDesktopSettings(); // load the close-to-tray preference before menus/tray
  authToken = getOrCreateToken();
  installAuthHeaderInjection();
  installMediaPermissions(); // let the dashboard's voice dictation use the mic
  // If a just-applied update exists, bump its attempt count now; a clean boot
  // below clears it, repeated failures trigger the recovery dialog.
  const pendingUpdate = readAndBumpUpdatePending();

  buildMenu();
  installSpotlightIpc(); // wire the quick-task overlay's IPC before the hotkey
  createTray();
  if (!START_HIDDEN) createLoadingWindow(); // login-boot goes straight to tray
  registerHotkey();

  if (IS_PACKAGED) {
    // PACKAGED: frozen daemon exe + standalone dashboard run by Electron's Node.
    // No Python/uv/Node/pnpm required on the user's machine. Both children run
    // under the crash supervisor (auto-restart with backoff).
    const stateDir = userDataDir; // the daemon's .ironjarvis lives here
    // 1) Frozen daemon. Must serve on 8787 to match the build-time-baked client URL.
    startService("daemon", () =>
      spawnChild(
        "daemon",
        DAEMON_EXE,
        ["serve", "--host", "127.0.0.1", "--port", String(DAEMON_PORT), "--root", stateDir],
        path.dirname(DAEMON_EXE),
        // Blank out any ambient IRONJARVIS_HOME (e.g. left over from source/dev use)
        // so the packaged app's per-install userData home always wins — an empty
        // value makes resolve_home() fall back to --root (userData/.ironjarvis).
        { IRONJARVIS_TOKEN: authToken, IRONJARVIS_HOME: "" },
        false
      )
    );
    // 2) Next.js standalone server (server.js) via Electron's bundled Node.
    startService("dashboard", () =>
      spawnChild(
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
      )
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
    startService("daemon", () =>
      spawnChild(
        "daemon",
        "uv",
        ["run", "ironjarvis", "serve", "--host", "127.0.0.1", "--port", String(DAEMON_PORT), "--root", REPO_ROOT],
        REPO_ROOT,
        { IRONJARVIS_TOKEN: authToken }
      )
    );
    // 2) Next.js dashboard. `next start` honours the PORT env var.
    startService("dashboard", () =>
      spawnChild("dashboard", "pnpm", ["start"], DASHBOARD_DIR, {
        PORT: String(DASHBOARD_PORT),
      })
    );
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
  bootComplete = true;
  if (START_HIDDEN) {
    // Login boot: stay in the tray — the window is created on demand (tray
    // click / hotkey / second launch). Close the splash if one exists.
    if (loadingWin && !loadingWin.isDestroyed()) loadingWin.close();
    loadingWin = null;
  } else {
    createMainWindow();
  }
  checkForUpdates();
  // Long-lived tray apps must keep looking for updates, not just at boot.
  setInterval(checkForUpdates, UPDATE_RECHECK_MS);
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
  // The Spotlight quick-task overlay — best-effort; a taken combo just no-ops
  // (the tray "Quick task…" item + the in-app UI still work).
  try {
    globalShortcut.register(SPOTLIGHT_HOTKEY, () => toggleSpotlight());
  } catch (err) {
    console.error("[hotkey] spotlight registration error:", err && err.message);
  }
  try {
    const ok = globalShortcut.register(HOTKEY, () => showMainWindow());
    if (!ok) {
      console.warn(`[hotkey] ${HOTKEY} registration failed (already taken?)`);
      // Tell the user instead of failing silently — the hotkey is a primary way
      // back to a window that closes to the tray.
      try {
        new Notification({
          title: "Iron Jarvis",
          body: `The global hotkey ${HOTKEY} is taken by another app — use the tray icon to open Iron Jarvis.`,
        }).show();
      } catch {
        /* notifications unavailable */
      }
    }
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
    } else if (bootComplete) {
      // Hidden in the tray with no window (e.g. --hidden login boot, or the
      // window was destroyed on hide) — a second launch means "show me the app".
      showMainWindow();
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

  // Quit path: ask the daemon to exit CLEANLY first (drains requests, runs the
  // FastAPI lifespan shutdown) and only force-kill as the fallback. The auto-
  // update path never gets here with work to do — it runs shutdown() itself
  // synchronously (shuttingDown set) before quitAndInstall, so this falls through.
  app.on("before-quit", (event) => {
    isQuitting = true;
    flushWindowState();
    if (shuttingDown || quitProcessed) return; // teardown already done/in-flight
    event.preventDefault();
    quitProcessed = true;
    requestDaemonShutdown(2000).finally(() => {
      shutdown(); // force-kills whatever is still alive (incl. the dashboard)
      app.quit(); // re-enters before-quit; falls through this time
    });
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
