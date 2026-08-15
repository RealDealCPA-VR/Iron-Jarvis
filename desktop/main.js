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
  clipboard,
} = require("electron");
const { spawn, spawnSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");

const windowState = require("./windowState");
// FAIL-OPEN require: v1.126.0 shipped with integrity.js missing from
// build.files — the packaged main process died at this line before a single
// window existed. A helper module being absent from the bundle must degrade
// (integrity checking off, loudly logged) — never brick the boot.
let integrity = null;
try {
  integrity = require("./integrity");
} catch (err) {
  console.error("[integrity] module missing — install verification disabled:", err && err.message);
}

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

//: The two origins the main window is ever allowed to be on. An ORIGIN, not a
//: prefix (v1.175.0): `will-navigate` used to allow anything starting with
//: DASHBOARD_URL, which has no trailing slash — so
//: `http://localhost:8788@evil.com/` satisfied it. That is userinfo; the real
//: host is evil.com. The main window carries the preload that exposes
//: `window.ironjarvis` (the per-install daemon TOKEN, clipboard, the update
//: bridge) and a preload survives navigation, so a single click on a link in
//: model-authored or fetched content could have handed all of it to a remote
//: page. URL parsing decides this now, because string prefixes cannot.
const DASHBOARD_ORIGINS = new Set([
  `http://localhost:${DASHBOARD_PORT}`,
  `http://127.0.0.1:${DASHBOARD_PORT}`,
]);

/** True only when `url` genuinely resolves to the local dashboard origin. */
function isDashboardUrl(url) {
  try {
    return DASHBOARD_ORIGINS.has(new URL(String(url)).origin);
  } catch {
    return false; // unparseable → not ours → goes to the system browser
  }
}

/**
 * True when an IPC message came from a frame still ON the dashboard origin
 * (v1.175.0). The privileged handlers below — reading the user's clipboard,
 * installing an update — used to answer whoever asked, because a handler that
 * ignores its `event` cannot tell one sender from another. A preload survives
 * navigation and is shared by every frame in the window, so this is what stops
 * an off-origin page (or an embedded frame) from reaching them. Checks the
 * SENDER FRAME rather than the window: an iframe is a different frame with the
 * same `webContents`.
 */
function isTrustedDashboardSender(event) {
  try {
    const frame = event && event.senderFrame;
    const url = frame ? String(frame.url || "") : "";
    return url ? isDashboardUrl(url) : false;
  } catch {
    return false; // frame already gone → not trusted
  }
}
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

// Merge one key into desktop-settings.json without clobbering the others —
// the file now carries more than the close preference (hardware-acceleration
// opt-out for the GPU-crash fallback), so every writer must read-merge-write.
function writeDesktopSetting(key, value) {
  try {
    let raw = {};
    try {
      raw = JSON.parse(fs.readFileSync(desktopSettingsFile(), "utf8")) || {};
    } catch {
      /* first write */
    }
    raw[key] = value;
    fs.writeFileSync(desktopSettingsFile(), JSON.stringify(raw), "utf8");
  } catch (err) {
    console.error("[settings] could not persist desktop-settings.json:", err && err.message);
  }
}

function setKeepRunningPref(value) {
  keepRunningPref = !!value;
  writeDesktopSetting("keepRunningInBackground", keepRunningPref);
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

// --- Bundled-install integrity gate (packaged only) -----------------------
// The v1.124.0 auto-update once landed HALF-EXTRACTED (NSIS interrupted):
// resources/dashboard/node_modules stopped partway through `next`, the
// dashboard crash-looped on "Cannot find module", and nothing told the user
// their INSTALL was damaged. afterPack now inventories every bundled file
// (install-manifest.json); this gate verifies the inventory BEFORE spawning
// anything and, on damage, offers a one-click repair from the already-
// downloaded installer instead of a crash loop.

function verifyInstallIntegrity() {
  const clean = { ok: true, checked: 0, missing: [], mismatched: [] };
  if (!IS_PACKAGED || !integrity) return clean;
  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(integrity.manifestPath(RES_DIR), "utf8"));
  } catch {
    return clean; // pre-manifest install — nothing to verify against
  }
  const res = integrity.verifyManifest(RES_DIR, manifest);
  if (!res.ok) {
    const log = fileLogger("desktop");
    log(
      `[integrity] DAMAGED install: ${res.missing.length} missing, ` +
        `${res.mismatched.length} wrong-size (of ${res.checked} checked)\n`
    );
    for (const f of res.missing.slice(0, 20)) log(`[integrity] missing: ${f}\n`);
    for (const f of res.mismatched.slice(0, 20)) log(`[integrity] wrong size: ${f}\n`);
  }
  return res;
}

// Orphaned frozen daemons (a crashed session's child that never died) hold
// file locks inside resources/daemon — NSIS then can't replace those files and
// the next boot comes up half-installed. The image name is unique to Iron
// Jarvis, so force-killing every instance is safe. Best-effort, synchronous
// (the installer must not start until the locks are gone).
function sweepOrphanDaemons() {
  if (process.platform !== "win32") return;
  try {
    spawnSync("taskkill", ["/F", "/T", "/IM", "ironjarvis.exe"], { windowsHide: true });
  } catch {
    /* best effort */
  }
}

// The updater keeps the last downloaded installer + its sha512 under
// %LOCALAPPDATA%/iron-jarvis-desktop-updater/pending — re-running it is a full
// repair (the v1.124.0 incident's installer was INTACT; only the extraction
// was interrupted). Returns the exe path only when the digest matches; never
// run a half-downloaded installer.
function findCachedInstaller() {
  try {
    const base = process.env.LOCALAPPDATA;
    if (!base) return null;
    const pending = path.join(base, "iron-jarvis-desktop-updater", "pending");
    const info = JSON.parse(fs.readFileSync(path.join(pending, "update-info.json"), "utf8"));
    if (!info || !info.fileName) return null;
    const exe = path.join(pending, info.fileName);
    const digest = crypto.createHash("sha512").update(fs.readFileSync(exe)).digest("base64");
    if (info.sha512 && digest !== info.sha512) return null;
    return exe;
  } catch {
    return null;
  }
}

// Damaged-install dialog: name the damage precisely, then repair with one
// click when a verified installer is cached (else point at Releases). Quits
// either way — booting half-installed code would only corrupt trust further.
function handleCorruptInstall(result) {
  const examples = result.missing.concat(result.mismatched).slice(0, 5).join("\n    ");
  const cached = findCachedInstaller();
  const buttons = cached
    ? ["Repair now", "Open releases page", "Quit"]
    : ["Open releases page", "Quit"];
  const choice = dialog.showMessageBoxSync({
    type: "error",
    buttons,
    defaultId: 0,
    cancelId: buttons.length - 1,
    noLink: true,
    title: "Iron Jarvis — installation damaged",
    message: "The last update did not install completely.",
    detail:
      `${result.missing.length} bundled file(s) are missing and ${result.mismatched.length} ` +
      `have the wrong size (of ${result.checked} checked) — the installer was likely ` +
      "interrupted.\n\n" +
      (cached
        ? "Repair re-runs the already-downloaded installer."
        : "Reinstall the latest version from the Releases page.") +
      " Your data, settings, and sessions are untouched.\n\n" +
      `First affected files:\n    ${examples}`,
  });
  isQuitting = true;
  if (cached && choice === 0) {
    sweepOrphanDaemons(); // clear any locks BEFORE the installer extracts
    try {
      const child = spawn(cached, [], { detached: true, stdio: "ignore" });
      child.unref();
    } catch (err) {
      console.error("[integrity] could not launch repair installer:", err && err.message);
      shell.openExternal("https://github.com/RealDealCPA-VR/Iron-Jarvis/releases/latest");
    }
  } else if (choice === (cached ? 1 : 0)) {
    shell.openExternal("https://github.com/RealDealCPA-VR/Iron-Jarvis/releases/latest");
  }
  shutdown();
  app.quit();
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

// Chromium's spellchecker underlines misspellings out of the box, but
// Electron shows NO context menu unless the app builds one — so corrections
// were invisible. This surfaces the dictionary suggestions (click to replace),
// add-to-dictionary, and the standard edit actions on right-click.
function installSpellcheckMenu(win) {
  win.webContents.on("context-menu", (_event, params) => {
    const items = [];
    for (const suggestion of params.dictionarySuggestions || []) {
      items.push({
        label: suggestion,
        click: () => win.webContents.replaceMisspelling(suggestion),
      });
    }
    if (params.misspelledWord) {
      if (items.length === 0) items.push({ label: "No suggestions", enabled: false });
      items.push(
        {
          label: `Add "${params.misspelledWord}" to dictionary`,
          click: () =>
            win.webContents.session.addWordToSpellCheckerDictionary(
              params.misspelledWord
            ),
        },
        { type: "separator" }
      );
    }
    if (params.isEditable) {
      items.push(
        { role: "cut", enabled: params.selectionText.length > 0 },
        { role: "copy", enabled: params.selectionText.length > 0 },
        { role: "paste" },
        { role: "selectAll" }
      );
    } else if (params.selectionText && params.selectionText.trim()) {
      items.push({ role: "copy" });
    }
    if (items.length > 0) Menu.buildFromTemplate(items).popup({ window: win });
  });
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
    // Custom title bar (v1.111.0) — the frontier-desktop chrome the user asked
    // for: the app draws its own top strip (hamburger · mark · global search)
    // while close/max/min stay NATIVE via the Windows controls overlay, so
    // Win11 snap layouts and OS conventions keep working. The dashboard's
    // <TitleBar> owns the strip; height here must match its h-10 (40px), or
    // the native buttons misalign against our row. Browser mode is unaffected
    // (no overlay outside Electron — the bar just renders as a normal header).
    titleBarStyle: "hidden",
    titleBarOverlay: {
      color: "#0a0a0f", // matches backgroundColor: the strip reads as one piece
      symbolColor: "#a6b0ba", // window-control glyphs: zinc, not pure white
      height: 40,
    },
    icon: path.join(__dirname, "assets", "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: true, // OS spellchecker on Windows; suggestions via context menu
      // Hand the per-install token to preload.js so it can seed localStorage
      // BEFORE the dashboard bundle runs (no 401 race). Empty when token-less.
      additionalArguments: [`--ij-token=${authToken || ""}`],
    },
  });
  installSpellcheckMenu(mainWin);

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
    if (!isDashboardUrl(url)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });
  // A server-side redirect never fires will-navigate, so guard it too: without
  // this, a dashboard-origin URL that 302s off-origin lands in the window with
  // the preload still attached.
  mainWin.webContents.on("will-redirect", (event, url) => {
    if (!isDashboardUrl(url)) {
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

  // A frozen or crashed renderer must self-heal, never strand the user
  // (v1.130.0). Attached per-creation: hide-to-tray destroys the window and
  // showMainWindow rebuilds it, so the watchdog rides every incarnation.
  installRendererWatchdog(mainWin);

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
      spellcheck: true,
    },
  });
  installSpellcheckMenu(spotlightWin);
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
    // GET /sessions/{id} returns { session, transcript } — read the NESTED
    // session (a top-level s.status was always undefined, so the "done"
    // notification never fired until the 15-min cap).
    const sess = (s && s.session) || s || {};
    const status = sess.status;
    if (status === "completed" || status === "failed" || elapsed > 15 * 60 * 1000) {
      clearInterval(timer);
      try {
        const note = new Notification({
          title:
            status === "failed"
              ? "Task failed"
              : `Task done: ${String(task).slice(0, 60)}`,
          body: sess.summary
            ? String(sess.summary).slice(0, 140)
            : "Click to open the result.",
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
  // Native clipboard for the terminal (paste/copy) — never permission-gated.
  // Native toast for the "This PC" notification destination (v1.118.0).
  // Clicking it restores the window — the alert is an invitation back in.
  ipcMain.handle("notify:show", (_e, opts) => {
    try {
      const note = new Notification({
        title: String(opts?.title || "Iron Jarvis"),
        body: String(opts?.body || ""),
      });
      note.on("click", () => {
        try {
          if (mainWin && !mainWin.isDestroyed()) {
            mainWin.show();
            mainWin.focus();
          }
        } catch {}
      });
      note.show();
      return true;
    } catch {
      return false;
    }
  });
  // Sender-checked: this hands back whatever the user last copied — passwords,
  // client data — so it answers the dashboard only (v1.175.0). Refusal returns
  // the same empty string as an unavailable clipboard, which callers handle.
  ipcMain.handle("clipboard:read", (event) => {
    if (!isTrustedDashboardSender(event)) return "";
    try {
      return clipboard.readText();
    } catch {
      return "";
    }
  });
  ipcMain.handle("clipboard:write", (_e, text) => {
    try {
      clipboard.writeText(String(text ?? ""));
    } catch {
      /* clipboard unavailable */
    }
    return true;
  });
  // Rich copy (v1.161.0): BOTH flavours in one write, which is what makes a
  // drafted email keep its bold, lists and links when pasted into Outlook or
  // Gmail. Two separate writeText/writeHTML calls would not do — the second
  // clears the first, leaving whichever ran last and losing the other. The
  // plain text is not optional: a composer that cannot take HTML (or a paste
  // into a plain-text field) falls back to it, and without it that paste is
  // empty. Throws are reported, never swallowed, so the renderer can say
  // "Copied as text" instead of claiming a rich copy that did not happen.
  ipcMain.handle("clipboard:writeHtml", (_e, html, text) => {
    clipboard.write({ text: String(text ?? ""), html: String(html ?? "") });
    return true;
  });
  // Theme-aware window controls (v1.112.0). The native min/max/close strip is
  // painted by WINDOWS from titleBarOverlay colors frozen at window creation —
  // it cannot see CSS, so a light theme (Mark 8) left a black button strip on
  // a white bar. The renderer resolves its theme's actual colors and pushes
  // them here on boot and on every theme flip. Hex-validated because this
  // crosses the IPC trust boundary; height stays pinned to the bar's 40px.
  ipcMain.handle("titlebar:set-overlay", (_e, opts) => {
    try {
      const color = String(opts?.color ?? "");
      const symbolColor = String(opts?.symbolColor ?? "");
      if (!/^#[0-9a-f]{6}$/i.test(color) || !/^#[0-9a-f]{6}$/i.test(symbolColor))
        return false;
      if (
        mainWin &&
        !mainWin.isDestroyed() &&
        typeof mainWin.setTitleBarOverlay === "function"
      ) {
        mainWin.setTitleBarOverlay({ color, symbolColor, height: 40 });
        return true;
      }
    } catch {
      /* overlay unsupported on this platform — the bar itself still themes */
    }
    return false;
  });
  // Update control for the dashboard Updates page (the packaged-app updater —
  // distinct from the git self-update the page previously only knew about).
  ipcMain.handle("update:getState", () => ({
    ..._updateState,
    current: _updateState.current || safeAppVersion(),
  }));
  ipcMain.handle("update:check", async () => {
    const au = initUpdater();
    if (!au) {
      _emitUpdateState({ status: "unsupported" });
      return _updateState;
    }
    _emitUpdateState({ status: "checking", error: null });
    try {
      await au.checkForUpdates();
    } catch (err) {
      _emitUpdateState({
        status: "error",
        error: friendlyUpdateError((err && err.message) || "check failed"),
      });
    }
    return _updateState;
  });
  // Sender-checked (v1.175.0): this quits the app and runs an installer. The
  // tray item and the update notification call applyPendingUpdate() directly —
  // they are main-process code and never come through here.
  ipcMain.handle("update:apply", (event) => {
    if (!isTrustedDashboardSender(event)) return false;
    if (pendingUpdateInfo) applyPendingUpdate();
    return true;
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
    // The always-available unfreeze: reloads just the UI (state lives in the
    // daemon). Discoverable here because a frozen window can't show its own
    // menus — the tray keeps working even when the renderer doesn't.
    { label: "Reload UI", click: () => reloadUI() },
    { type: "separator" },
    {
      label: "Keep running in background",
      type: "checkbox",
      checked: keepRunningPref === true,
      click: (item) => setKeepRunningPref(item.checked),
    },
    {
      label: "Use hardware acceleration",
      type: "checkbox",
      checked: !hwAccelDisabled,
      click: (item) => setHwAccelPref(!item.checked),
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

// Live update state, mirrored to the dashboard's Updates page (so the packaged
// app finally has a real "check for updates" UI instead of the git-only page).
let _updateState = {
  status: "idle", // idle | checking | up-to-date | available | downloading | downloaded | error | unsupported
  current: null,
  version: null,
  percent: 0,
  error: null,
};

function _emitUpdateState(patch) {
  _updateState = { ..._updateState, ...patch, current: _updateState.current || safeAppVersion() };
  try {
    if (mainWin && !mainWin.isDestroyed()) {
      mainWin.webContents.send("update:state", _updateState);
    }
  } catch {
    /* window gone */
  }
}

function safeAppVersion() {
  try {
    return app.getVersion();
  } catch {
    return null;
  }
}

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
  // Our own children are dead (shutdown() blocks on taskkill), but an ORPHANED
  // daemon from an earlier crashed session still locks resources/daemon and
  // makes NSIS extract a partial install. Sweep them before handing off.
  sweepOrphanDaemons();
  try {
    _autoUpdater.quitAndInstall(false, true);
  } catch (err) {
    console.error("[update] quitAndInstall failed:", err && err.message);
  }
}

// A checkForUpdates 404 on latest.yml is the PUBLISHING WINDOW, not a fault: CI
// pre-creates the release, then uploads the installer + latest.yml over the next
// several minutes. Translate that (and any opaque updater failure) into a plain
// sentence so NEITHER the auto-check NOR the manual "Check for updates" ever
// surfaces a raw HttpError stack trace.
function friendlyUpdateError(msg) {
  const m = (msg || "update failed").toString();
  if (/latest\.yml/i.test(m) && /404|not.*found|cannot find/i.test(m)) {
    return (
      "A new version is being prepared — its files are still uploading (this " +
      "takes a few minutes after a release goes out). You're on the latest " +
      "available version until then; check again shortly."
    );
  }
  return m;
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
  autoUpdater.on("checking-for-update", () =>
    _emitUpdateState({ status: "checking", error: null })
  );
  autoUpdater.on("update-not-available", (info) => {
    _emitUpdateState({ status: "up-to-date", version: (info && info.version) || null });
  });
  autoUpdater.on("download-progress", (p) =>
    _emitUpdateState({ status: "downloading", percent: Math.round((p && p.percent) || 0) })
  );
  autoUpdater.on("error", (err) => {
    const msg = (err && err.message) || "update failed";
    console.error("[update] error:", msg);
    _emitUpdateState({ status: "error", error: friendlyUpdateError(msg) });
  });
  autoUpdater.on("update-available", (info) => {
    console.log("[update] available:", info && info.version);
    _emitUpdateState({ status: "available", version: (info && info.version) || null });
  });
  autoUpdater.on("update-downloaded", (info) => {
    _emitUpdateState({ status: "downloaded", version: (info && info.version) || null, percent: 100 });
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
  installGpuFallback(); // GPU-crash counter -> offer software rendering
  createTray();
  if (!START_HIDDEN) createLoadingWindow(); // login-boot goes straight to tray
  registerHotkey();

  if (IS_PACKAGED) {
    // PACKAGED: frozen daemon exe + standalone dashboard run by Electron's Node.
    // No Python/uv/Node/pnpm required on the user's machine. Both children run
    // under the crash supervisor (auto-restart with backoff).
    //
    // FIRST: the install-integrity gate. A half-extracted update must repair,
    // not boot into a crash loop (the v1.124.0 incident).
    const integrityResult = verifyInstallIntegrity();
    if (!integrityResult.ok) {
      handleCorruptInstall(integrityResult);
      return;
    }
    const stateDir = userDataDir; // the daemon's .ironjarvis lives here
    // Bundled OFFLINE voice model (Vosk). extraResources ships it next to the
    // daemon; point the daemon at it so speech-to-text works with no key/server/
    // internet. Only set when the model is actually present (dev has none), so
    // resolution falls through cleanly otherwise.
    const voskModelDir = path.join(RES_DIR, "vosk-model");
    const voskEnv =
      fs.existsSync(path.join(voskModelDir, "am"))
        ? { IRONJARVIS_VOSK_MODEL: voskModelDir }
        : {};
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
        { IRONJARVIS_TOKEN: authToken, IRONJARVIS_HOME: "", ...voskEnv },
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
    // A failed health gate on a DAMAGED install (e.g. files were still being
    // extracted when the pre-spawn check ran) routes to repair, not the
    // generic port-conflict message.
    const integrityResult = verifyInstallIntegrity();
    if (!integrityResult.ok) {
      handleCorruptInstall(integrityResult);
      return;
    }
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
    const integrityResult = verifyInstallIntegrity();
    if (!integrityResult.ok) {
      handleCorruptInstall(integrityResult);
      return;
    }
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

// --- Renderer watchdog (v1.130.0) ----------------------------------------
// The daemon and dashboard children have a crash supervisor; until now the
// RENDERER — the process the user actually looks at — had nothing. A wedged
// or dead renderer left a frozen window forever (the 2026-08-03 incident).
// Three layers fix that:
//   detect   'unresponsive' (Chromium's own hang signal) + a main->preload
//            heartbeat that catches soft freezes Chromium never flags.
//   recover  kill the hung renderer; 'render-process-gone' reloads it. The
//            dashboard is stateless-by-design (all state in the daemon), so a
//            reload is a ~3s blip, not data loss. A breaker stops reload
//            storms and levels with the user instead.
//   learn    every incident goes to logs/renderer.log AND the daemon's event
//            log (desktop.incident) so the NEXT freeze arrives with evidence.

const WATCHDOG_PING_MS = 5000;
const WATCHDOG_MISSED_LIMIT = 3; // ≥15s of a blocked renderer thread = frozen
const RECOVERY_WINDOW_MS = 5 * 60 * 1000;
const RECOVERY_MAX_IN_WINDOW = 3;

let _wdMissed = 0;
let _wdTimer = null;
let _wdRecoveries = []; // timestamps of recent auto-recoveries (breaker)
let _wdUnresponsiveTimer = null;

function rendererLog(line) {
  const stamp = new Date().toISOString();
  fileLogger("renderer")(`${stamp} ${line}\n`);
}

// Fire-and-forget incident record into the daemon's event log — makes freezes
// first-class, queryable events (SELECT .. FROM eventrecord WHERE type LIKE
// 'desktop.%'). Must never throw and never block recovery.
function reportIncident(kind, detail) {
  rendererLog(`[incident] ${kind}: ${detail}`);
  try {
    fetch(`http://127.0.0.1:${DAEMON_PORT}/system/incident`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
      },
      body: JSON.stringify({ kind, detail: String(detail || "") }),
    }).catch(() => {});
  } catch {
    /* daemon down — renderer.log still has it */
  }
}

// The breaker: recovery is only a cure if it converges. A renderer that dies
// 3+ times in 5 minutes has a systemic cause (GPU driver, bad state) — stop
// the reload loop and tell the user what to try, honestly.
function recoveryAllowed() {
  const now = Date.now();
  _wdRecoveries = _wdRecoveries.filter((t) => now - t < RECOVERY_WINDOW_MS);
  return _wdRecoveries.length < RECOVERY_MAX_IN_WINDOW;
}

function notifyRecovered(reason) {
  try {
    new Notification({
      title: "Iron Jarvis",
      body: `The window stopped responding (${reason}) and was reloaded. Nothing was lost — chats and settings live in the local service.`,
    }).show();
  } catch {
    /* notifications unavailable */
  }
}

function recoveryExhausted() {
  reportIncident("recovery-exhausted", "renderer failed repeatedly; auto-heal paused");
  if (!mainWin || mainWin.isDestroyed()) return;
  dialog
    .showMessageBox(mainWin, {
      type: "warning",
      buttons: ["Restart Iron Jarvis", "Turn off hardware acceleration + restart", "Not now"],
      defaultId: 0,
      cancelId: 2,
      noLink: true,
      title: "Iron Jarvis — the window keeps failing",
      message: "The app window has stopped responding several times in a row.",
      detail:
        "A full restart usually clears this. If it keeps happening, turning off " +
        "hardware acceleration works around GPU-driver problems (the most common " +
        "cause). Your data is safe either way.",
    })
    .then(({ response }) => {
      if (response === 1) writeDesktopSetting("disableHardwareAcceleration", true);
      if (response === 0 || response === 1) {
        isQuitting = true;
        app.relaunch();
        app.quit();
      }
    })
    .catch(() => {});
}

// Recover from a HUNG (still-alive) renderer: kill it so 'render-process-gone'
// runs the one shared reload path. Guarded by the breaker.
function recoverRenderer(reason) {
  if (!mainWin || mainWin.isDestroyed()) return;
  if (!recoveryAllowed()) {
    recoveryExhausted();
    return;
  }
  _wdRecoveries.push(Date.now());
  reportIncident("renderer-frozen", reason);
  try {
    mainWin.webContents.forcefullyCrashRenderer();
  } catch (err) {
    rendererLog(`[watchdog] forcefullyCrashRenderer failed: ${err && err.message}`);
  }
  notifyRecovered(reason);
}

// Heartbeat pong — registered ONCE (createMainWindow can run many times as the
// window is destroyed/rebuilt on hide-to-tray).
let _wdPongInstalled = false;
function installWatchdogPong() {
  if (_wdPongInstalled) return;
  _wdPongInstalled = true;
  ipcMain.on("watchdog:pong", () => {
    _wdMissed = 0;
  });
}

function installRendererWatchdog(win) {
  installWatchdogPong();
  const wc = win.webContents;

  // Chromium's own hang detector. Give it a short grace ('responsive' often
  // follows a momentary stall, e.g. the OS paging under memory pressure) —
  // only a hang that OUTLIVES the grace gets recovered.
  wc.on("unresponsive", () => {
    rendererLog("[watchdog] renderer reported unresponsive");
    if (_wdUnresponsiveTimer) clearTimeout(_wdUnresponsiveTimer);
    _wdUnresponsiveTimer = setTimeout(() => {
      _wdUnresponsiveTimer = null;
      recoverRenderer("unresponsive");
    }, 5000);
  });
  wc.on("responsive", () => {
    rendererLog("[watchdog] renderer responsive again");
    if (_wdUnresponsiveTimer) {
      clearTimeout(_wdUnresponsiveTimer);
      _wdUnresponsiveTimer = null;
    }
  });

  // The one shared recovery path: any renderer death that isn't an intentional
  // teardown reloads the window in place. Covers forcefullyCrashRenderer
  // (watchdog), real crashes, and OOM kills.
  wc.on("render-process-gone", (_event, details) => {
    const why = `${details.reason} (exit ${details.exitCode})`;
    if (details.reason === "clean-exit" || shuttingDown || isQuitting) {
      rendererLog(`[watchdog] renderer gone: ${why} — intentional, no action`);
      return;
    }
    reportIncident("renderer-gone", why);
    // 'killed' is the watchdog's own forcefullyCrashRenderer (already counted
    // + notified in recoverRenderer); anything else is a spontaneous death
    // that must pass the same breaker so a crash loop can't reload forever.
    if (details.reason !== "killed") {
      if (!recoveryAllowed()) {
        recoveryExhausted();
        return;
      }
      _wdRecoveries.push(Date.now());
      notifyRecovered(details.reason);
    }
    setTimeout(() => {
      try {
        if (mainWin && !mainWin.isDestroyed()) mainWin.webContents.reload();
      } catch (err) {
        rendererLog(`[watchdog] reload after crash failed: ${err && err.message}`);
      }
    }, 250);
  });

  // Renderer console errors -> logs/renderer.log. This is the observability
  // this incident lacked: until now the UI's errors were written NOWHERE.
  // (Supports both the legacy positional and the newer event-object shapes.)
  wc.on("console-message", (eventOrLegacy, level, message) => {
    try {
      const isNew = eventOrLegacy && typeof eventOrLegacy.level === "string";
      const lvl = isNew ? eventOrLegacy.level : level;
      const text = isNew ? eventOrLegacy.message : message;
      if (lvl === "error" || lvl === 3) rendererLog(`[console] ${text}`);
    } catch {
      /* logging must never break the app */
    }
  });

  // Heartbeat: catches soft freezes 'unresponsive' never fires for (input
  // dead but compositor alive). Only enforced while the window is visible —
  // a hidden/minimized window may be throttled and must not false-positive.
  if (_wdTimer) clearInterval(_wdTimer);
  _wdMissed = 0;
  _wdTimer = setInterval(() => {
    if (!mainWin || mainWin.isDestroyed()) return;
    if (!mainWin.isVisible() || mainWin.isMinimized()) {
      _wdMissed = 0;
      return;
    }
    if (wc.isLoading()) {
      _wdMissed = 0; // navigation/reload in flight — pings can't land yet
      return;
    }
    _wdMissed += 1;
    if (_wdMissed > WATCHDOG_MISSED_LIMIT) {
      _wdMissed = 0;
      recoverRenderer("heartbeat");
      return;
    }
    try {
      wc.send("watchdog:ping");
    } catch {
      /* webContents mid-teardown */
    }
  }, WATCHDOG_PING_MS);

  win.on("closed", () => {
    if (_wdTimer) clearInterval(_wdTimer);
    _wdTimer = null;
    if (_wdUnresponsiveTimer) clearTimeout(_wdUnresponsiveTimer);
    _wdUnresponsiveTimer = null;
  });
}

// Always-available escape hatch (tray + Ctrl+R): reload just the UI. All
// state lives in the daemon, so this is always safe.
function reloadUI() {
  if (mainWin && !mainWin.isDestroyed()) {
    try {
      mainWin.webContents.reloadIgnoringCache();
      return;
    } catch {
      /* fall through to a rebuild */
    }
  }
  showMainWindow();
}

// --- GPU-crash fallback (v1.130.0) ----------------------------------------
// Repeated GPU-process deaths are the classic driver-vs-Chromium fight and a
// prime suspect for compositor freezes. After 2 in one session, offer a
// relaunch with hardware acceleration off — persisted, reversible in the tray.

let _gpuCrashes = 0;
let _gpuPromptShown = false;

function installGpuFallback() {
  app.on("child-process-gone", (_event, details) => {
    if (!details || details.type !== "GPU") return;
    if (details.reason === "clean-exit") return;
    _gpuCrashes += 1;
    reportIncident("gpu-process-gone", `${details.reason} (#${_gpuCrashes} this session)`);
    if (_gpuCrashes < 2 || _gpuPromptShown || hwAccelDisabled) return;
    _gpuPromptShown = true;
    dialog
      .showMessageBox({
        type: "warning",
        buttons: ["Restart without hardware acceleration", "Not now"],
        defaultId: 0,
        cancelId: 1,
        noLink: true,
        title: "Iron Jarvis — graphics driver trouble",
        message: "The graphics process has crashed twice this session.",
        detail:
          "This is almost always a GPU-driver issue. Running without hardware " +
          "acceleration avoids it (slightly higher CPU use; you can turn it back " +
          "on any time from the tray menu).",
      })
      .then(({ response }) => {
        if (response === 0) {
          writeDesktopSetting("disableHardwareAcceleration", true);
          isQuitting = true;
          app.relaunch();
          app.quit();
        }
      })
      .catch(() => {});
  });
}

// --- App lifecycle -------------------------------------------------------

// Hardware-acceleration opt-out must apply BEFORE app ready — read the
// persisted flag early (userDataDir isn't set yet; derive the path directly).
let hwAccelDisabled = false;
try {
  const early = JSON.parse(
    fs.readFileSync(path.join(app.getPath("userData"), "desktop-settings.json"), "utf8")
  );
  if (early && early.disableHardwareAcceleration === true) {
    app.disableHardwareAcceleration();
    hwAccelDisabled = true;
  }
} catch {
  /* no settings yet — hardware acceleration stays on (the default) */
}

function setHwAccelPref(disabled) {
  writeDesktopSetting("disableHardwareAcceleration", !!disabled);
  dialog
    .showMessageBox({
      type: "question",
      buttons: ["Restart now", "Later"],
      defaultId: 0,
      cancelId: 1,
      noLink: true,
      title: "Iron Jarvis",
      message: "Restart to apply the graphics change?",
      detail: "The hardware-acceleration setting takes effect on the next launch.",
    })
    .then(({ response }) => {
      if (response === 0) {
        isQuitting = true;
        app.relaunch();
        app.quit();
      } else {
        refreshMenus();
      }
    })
    .catch(() => refreshMenus());
}

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
      // autoInstallOnAppQuit runs NSIS after this quit — clear any orphaned
      // daemons' file locks first, exactly like the explicit-update path.
      if (pendingUpdateInfo) sweepOrphanDaemons();
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
