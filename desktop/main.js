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
  powerMonitor,
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
  desktopLog("error", "[integrity] module missing — install verification disabled:", err && err.message);
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
// THE BROWSER ADD-ON (v1.239.0), in BOTH layouts, in one declaration — the same
// shape DAEMON_EXE and DASHBOARD_SERVER use above, and at the same scope FOR A
// REASON. It first landed inside the `if (IS_PACKAGED)` boot branch, which made
// its dev arm unreachable: in a source run the branch never executes, so the
// daemon was never told where the add-on is and the Browser page could only fall
// back to guessing. The packaging test that claimed to pin "both layouts" passed
// anyway, because it reads the declaration rather than reaching it.
//
// Packaged: electron-builder puts it in extraResources as "browser-addon" and
// afterPack.js inventories it. Source checkout: the repo's own extensions/chrome.
// The daemon is told which, so the doctor and the Browser page can name a REAL
// folder for Chrome's Load unpacked — a user who ran the installer has no
// checkout, which is the whole point of D27.
const BROWSER_ADDON_DIR = IS_PACKAGED
  ? path.join(RES_DIR, "browser-addon")
  : path.join(REPO_ROOT, "extensions", "chrome");

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
// How often the boot gates re-probe the daemon and the dashboard (v1.250.0,
// S-01). Was 500 ms, which on a boot that becomes ready at t+6.1s costs up to
// half a second of pure waiting — the splash stays up while both children are
// already answering. 150 ms is still far cheaper than what it waits for (each
// probe is one local HTTP GET with its own 2.5 s timeout) and shortens the
// average blind spot to ~75 ms.
const GATE_POLL_MS = 150;

const HOTKEY = "CommandOrControl+Shift+J"; // show/focus the main window (preferred)
const SPOTLIGHT_HOTKEY = "CommandOrControl+Shift+Space"; // quick-task overlay
// v1.229.0 (audit D2): the window hotkey is a LADDER. Ctrl+Shift+J was held by
// another app on the user's own machine (Win32 error 1409), registration
// failed once at boot, and every surface — tray, Help, README, the Overview
// tips card — kept advertising it as fact. Each ladder is tried in order; what
// actually registered (or null) lives in `hotkeyState`, which the tray label,
// the app menu, the tray hint and the dashboard (`shell:getState`) all read.
const HOTKEY_LADDER = [HOTKEY, "CommandOrControl+Alt+J"];
const SPOTLIGHT_LADDER = [SPOTLIGHT_HOTKEY];
const HOTKEY_RETRY_MS = 30 * 60 * 1000; // a taken key is retried while null
const hotkeyState = { window: null, spotlight: null };

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
// A second launch DURING a --hidden boot has nothing to show: there is no main
// window, no splash, and bootComplete is still false (a packaged cold boot can
// take up to 90s while AV scans the frozen daemon). Record the intent here so
// startup() opens the window as soon as the health gate passes, instead of
// silently dropping the user's explicit launch.
let showWindowWhenReady = false;
// before-quit runs async teardown (graceful daemon stop) exactly once.
let quitProcessed = false;
// {version} once an update has finished downloading and is ready to install —
// surfaced as a clickable notification + a top-of-tray "Restart to update" item.
let pendingUpdateInfo = null;
// True between the start of an install handoff and either the app quitting or
// abortUpdateInstall() putting everything back. Guards against a second click
// (tray item, notification, Updates page) re-entering mid-teardown.
let updateInstallInFlight = false;

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
    desktopLog("error", "[token] could not persist token.txt:", err && err.message);
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
    desktopLog("error", "[auth] header injection unavailable:", err && err.message);
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
    desktopLog("error", "[permissions] media handler unavailable:", err && err.message);
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
    desktopLog("error", "[settings] could not persist desktop-settings.json:", err && err.message);
  }
}

function setKeepRunningPref(value) {
  keepRunningPref = !!value;
  writeDesktopSetting("keepRunningInBackground", keepRunningPref);
  refreshMenus(); // reflect the new state in the tray + app-menu checkboxes
}

// One-time "where did it go?" hint (v1.197.0). The close prompt explains WHAT
// keep-running means, but after the window vanishes nothing says WHERE the app
// went — a non-technical user reads the empty taskbar as "it quit" and
// relaunches (or reinstalls). Shown once per install, on the first real hide,
// and never again: it is a signpost, not a nag.
function maybeShowTrayHint() {
  let shown = false;
  try {
    const raw = JSON.parse(fs.readFileSync(desktopSettingsFile(), "utf8"));
    shown = !!(raw && raw.trayHintShown);
  } catch {
    /* no settings file yet -> not shown */
  }
  if (shown) return;
  writeDesktopSetting("trayHintShown", true);
  try {
    new Notification({
      title: "Iron Jarvis is still running",
      body:
        "Find it in the system tray (near the clock). " +
        (hotkeyState.window
          ? `Press ${accelLabel(hotkeyState.window)} to reopen the window; `
          : "Click the tray icon to reopen the window; ") +
        "to stop it completely use the tray icon → Quit Iron Jarvis.",
    }).show();
  } catch {
    /* notifications unavailable — the tray tooltip still carries the truth */
  }
}

// Hide the window to the tray, keeping the daemon + dashboard alive.
// MEMORY: a hidden BrowserWindow keeps its whole renderer tree resident
// (~hundreds of MB) — destroy it after hiding and let showMainWindow() rebuild
// it on demand. hide() first so the visual response is instant; destroy() (not
// close()) skips the 'close' handler, so no prompt/recursion.
function hideToTray() {
  if (!mainWin || mainWin.isDestroyed()) return;
  maybeShowTrayHint();
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
    desktopLog("error", "[login-item] could not update:", err && err.message);
  }
  refreshMenus();
}

// One question, asked once (v1.249.0, R-04). A Windows Update restart at 03:29
// on 2026-09-09 left Iron Jarvis closed for ~18 hours — schedules, Slack and
// webhooks all off, and nothing on the machine would have brought it back.
// Start-at-login already exists (tray + app menu) but ships OFF and nobody
// finds it, so ASK, with Yes pre-selected, and never ask again either way.
function maybeOfferStartWithWindows() {
  if (!IS_PACKAGED || process.platform !== "win32" || START_HIDDEN) return;
  let asked = false;
  try {
    const raw = JSON.parse(fs.readFileSync(desktopSettingsFile(), "utf8"));
    asked = !!(raw && raw.startWithWindowsAsked);
  } catch {
    /* no settings file yet -> never asked */
  }
  if (asked) return;
  writeDesktopSetting("startWithWindowsAsked", true); // asked; the answer is optional
  if (getStartAtLogin()) return; // already on — nothing to offer
  try {
    dialog
      .showMessageBox({
        type: "question",
        buttons: ["Yes, start with Windows", "Not now"],
        defaultId: 0,
        cancelId: 1,
        noLink: true,
        title: "Iron Jarvis",
        message: "Start Iron Jarvis with Windows (in the tray)?",
        detail:
          "Then schedules, messages and jobs waiting for you keep working after Windows " +
          "restarts — an overnight update no longer leaves Iron Jarvis closed. It starts " +
          "quietly in the tray, and you can turn this off any time from the tray menu.",
      })
      .then(({ response }) => {
        if (response === 0) setStartAtLogin(true);
      })
      .catch(() => {
        /* dialog unavailable — the tray toggle still works */
      });
  } catch {
    /* never block boot on a dialog */
  }
}

// --- Windows shutting down is not a crash (v1.249.0, R-04) ----------------
// At a Windows restart the OS terminates our children. The supervisor read
// that as a crash ("unexpected exit — restart #1"), counted it toward the
// 24-hour crash toast, and respawned into a machine that was going away.
// 0x40010004 (DBG_TERMINATE_PROCESS) and 0xC000013A (CTRL_C at session end)
// are the codes Windows uses for it.
const WINDOWS_SHUTDOWN_EXIT_CODES = new Set([0x40010004, 0xc000013a, -1073741510]);
//: A shutdown-SHAPED exit with no session-end signal waits this long before
//: respawning: if Windows really is going down we are gone well before it,
//: and if it was a one-off the service still comes back by itself.
const WINDOWS_SHUTDOWN_GRACE_MS = 15000;
let windowsSessionEnding = false;

function isWindowsShutdownExit(code) {
  return typeof code === "number" && WINDOWS_SHUTDOWN_EXIT_CODES.has(code);
}

function markWindowsSessionEnding(why) {
  if (windowsSessionEnding) return;
  windowsSessionEnding = true;
  desktopLog("warn", `[main] Windows is ${why} — the children will not be restarted`);
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

// Open userData/logs in the OS file manager (v1.229.0, audit D8/OBS5). The
// tray item and Settings → Maintenance → "Open logs folder" (IPC
// `shell:openLogs`) both land here. The folder is created first so a fresh
// install opens an empty folder instead of an error. Resolves
// `{ ok: true, path }` or `{ ok: false, path, error }` — never throws, so a
// missing file manager is a sentence in the UI, not a swallowed rejection.
function openLogsFolder() {
  const dir = path.join(userDataDir || "", "logs");
  try {
    fs.mkdirSync(dir, { recursive: true });
  } catch {
    /* openPath reports the real failure below */
  }
  return shell.openPath(dir).then(
    (err) => (err ? { ok: false, path: dir, error: String(err) } : { ok: true, path: dir }),
    (e) => ({ ok: false, path: dir, error: String((e && e.message) || e) })
  );
}

// Main-process errors went to the console ONLY — and a Start-Menu launch has
// no console, so every console.error/warn in this file was written NOWHERE in
// the packaged app (v1.226.0, F-E-6). Same sink as the children get:
// userData/logs/desktop.log, plus the console for a dev run.
function desktopLog(level, ...args) {
  try {
    (level === "warn" ? console.warn : console.error)(...args);
  } catch {
    /* console may be gone */
  }
  try {
    const text = args
      .map((a) => {
        if (a instanceof Error) return a.stack || a.message;
        if (typeof a === "string") return a;
        try {
          return JSON.stringify(a);
        } catch {
          return String(a);
        }
      })
      .join(" ");
    fileLogger("desktop")(`${new Date().toISOString()} [${level}] ${text}\n`);
  } catch {
    /* never throw from a logger */
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

  const toFile = fileLogger(label);
  // Every chunk carries an ISO time (v1.229.0, audit D5): a crash at 2am and
  // a kill at 9am used to be indistinguishable in the file — neither the
  // child's lines nor the supervisor's exit line said WHEN.
  const stamped = (d) => `${new Date().toISOString()} ${d}`;
  if (child.stdout) {
    child.stdout.on("data", (d) => {
      process.stdout.write(`[${label}] ${d}`);
      toFile(stamped(d));
    });
  }
  if (child.stderr) {
    child.stderr.on("data", (d) => {
      process.stderr.write(`[${label}] ${d}`);
      toFile(stamped(d));
    });
  }
  child.on("error", (err) => {
    // With shell:true the inner command (uv/pnpm) won't raise ENOENT here —
    // that's covered by the preflight check below. This catches shell failures.
    desktopLog("error", `[${label}] spawn error:`, err.message);
    toFile(`[main] spawn error: ${err.message}\n`);
  });
  child.on("exit", (code, signal) => {
    console.log(`[${label}] exited (code=${code}, signal=${signal}, pid=${child.pid})`);
    toFile(`[main] ${new Date().toISOString()} exited (code=${code}, signal=${signal}, pid=${child.pid})\n`);
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
// The ladder has a CEILING (v1.229.0, audit D1): a child that dies within
// FAST_DEATH_MS of its spawn FAST_DEATHS_TO_VERIFY times in a row is checked
// against the install manifest first (a half-applied update → the Repair
// dialog, not a 60 s loop forever); a clean install that still cannot keep
// its child alive stops after RESTART_CAP_MAX restarts in RESTART_CAP_WINDOW_MS,
// says so once, and hands the user a tray "Restart Iron Jarvis" item.
const FAST_DEATH_MS = 3000;
const FAST_DEATHS_TO_VERIFY = 3;
const RESTART_CAP_MAX = 10;
const RESTART_CAP_WINDOW_MS = 15 * 60 * 1000;
// A child that dies every six minutes ran "healthy" by the ladder's 5-minute
// rule, so its counter reset before every increment and it was restarted
// forever with no toast (audit D3): a second window counts deaths in a day.
const DEATHS_DAY_MS = 24 * 60 * 60 * 1000;
const DEATHS_DAY_TOAST_AT = 3;
const _services = {}; // label -> { spawnFn, restarts, lastStart, fastDeaths, restartTimes, deaths, capped, restartTimer, restartSeq }

// Tray tooltip truth (audit D4): notifyCrashLoop/notifyWatchdogExhausted
// wrote "restarting repeatedly" and nothing ever wrote "running" back. Each
// warning marks its service degraded; the tooltip returns to "running" only
// when NO service is degraded (a healthy daemon must not clear a dashboard
// that is still looping).
const _trayDegraded = new Set();
function markTrayDegraded(label, text) {
  _trayDegraded.add(label);
  try {
    if (tray) tray.setToolTip(text);
  } catch {
    /* tray may be gone */
  }
}
function markTrayHealthy(label) {
  if (!_trayDegraded.delete(label) || _trayDegraded.size) return;
  try {
    if (tray) tray.setToolTip("Iron Jarvis — running");
  } catch {
    /* tray may be gone */
  }
}

function startService(label, spawnFn) {
  const rec = _services[label] || (_services[label] = { restarts: 0, lastStart: 0 });
  rec.spawnFn = spawnFn;
  rec.lastStart = Date.now();
  rec.adopted = false; // a child of our own is (being) started
  const child = spawnFn();
  if (label === "daemon") daemonProc = child;
  else if (label === "dashboard") {
    dashboardProc = child;
    armDashboardReload(); // a window on the error page gets this spawn's answer (audit D1)
  }
  // The ladder hooks "close", not "exit" (v1.226.0, F-E-3): a spawn that FAILS
  // (ENOENT — AV quarantined the frozen exe right after killing the daemon)
  // emits error + close and never exit, so the old hook silently disarmed on
  // that first restart and the app stayed dead with the tray saying running.
  // close fires after a normal exit too; `gone` keeps the error-path fallback
  // from counting the same death twice.
  let gone = false;
  const onGone = (code) => {
    if (gone) return;
    gone = true;
    if (shuttingDown || isQuitting) return; // expected teardown
    // v1.249.0 (R-04): Windows taking the machine down is a normal close —
    // never a crash, never a respawn into a dying session.
    if (windowsSessionEnding) {
      fileLogger(label)(`[main] ${new Date().toISOString()} ended because Windows is shutting down — not restarting\n`);
      return;
    }
    if (isWindowsShutdownExit(code)) {
      // The exit code alone is not proof. Wait out the grace window: if this
      // process is still here, Windows was NOT shutting down and the service
      // comes back — without either outcome counting as a crash.
      fileLogger(label)(`[main] ${new Date().toISOString()} exit ${code} looks like a Windows shutdown — deciding in ${WINDOWS_SHUTDOWN_GRACE_MS}ms\n`);
      setTimeout(() => {
        if (shuttingDown || isQuitting || windowsSessionEnding) return;
        startService(label, rec.spawnFn);
      }, WINDOWS_SHUTDOWN_GRACE_MS);
      return;
    }
    // Contract C2 (v1.226.0, F-E-2): serve exits 75 when an Iron Jarvis daemon
    // ALREADY listens on the port — it attached, it did not start. That is not
    // a crash: restarting it looped forever against a stale daemon (13 spawns
    // in 10 minutes and a false "keeps crashing" toast).
    if (label === "daemon" && code === 75) {
      adoptOrReplaceExistingDaemon(rec);
      return;
    }
    const now = Date.now();
    if (rec.manualRestart) {
      // The tray's "Restart Iron Jarvis" killed it on purpose: respawn at
      // once, counters already reset, nothing counted as a crash.
      rec.manualRestart = false;
      fileLogger(label)(`[main] ${new Date(now).toISOString()} restarting on request\n`);
      startService(label, rec.spawnFn);
      return;
    }
    const uptime = now - rec.lastStart;
    if (uptime > 5 * 60 * 1000) {
      rec.restarts = 0; // ran healthy — reset the ladder
      rec.swept = false;
      markTrayHealthy(label); // audit D4: it ran, so the tooltip stops saying "repeatedly"
    }
    rec.restarts += 1;
    rec.fastDeaths = uptime < FAST_DEATH_MS ? (rec.fastDeaths || 0) + 1 : 0;
    rec.deaths = (rec.deaths || []).filter((t) => now - t < DEATHS_DAY_MS);
    rec.deaths.push(now);
    if (rec.fastDeaths === FAST_DEATHS_TO_VERIFY) {
      // Three deaths within seconds of spawn: is the install whole? (audit D1)
      const integrityResult = verifyInstallIntegrity();
      if (!integrityResult.ok) {
        fileLogger(label)(`[main] ${new Date(now).toISOString()} died ${rec.fastDeaths}x within ${FAST_DEATH_MS}ms of spawn and the install is damaged — offering repair\n`);
        handleCorruptInstall(integrityResult);
        return;
      }
      fileLogger(label)(`[main] ${new Date(now).toISOString()} died ${rec.fastDeaths}x within ${FAST_DEATH_MS}ms of spawn; install verified intact (${integrityResult.checked} files)\n`);
    }
    rec.restartTimes = (rec.restartTimes || []).filter((t) => now - t < RESTART_CAP_WINDOW_MS);
    if (rec.restartTimes.length >= RESTART_CAP_MAX) {
      rec.capped = true;
      const minutes = Math.round(RESTART_CAP_WINDOW_MS / 60000);
      desktopLog("error", `[${label}] ${rec.restartTimes.length} restarts in ${minutes} minutes — giving up; use the tray's "Restart Iron Jarvis"`);
      fileLogger(label)(`[main] ${new Date(now).toISOString()} ${rec.restartTimes.length} restarts in ${minutes} minutes — giving up; use the tray's "Restart Iron Jarvis"\n`);
      notifyCrashLoop(label, `The ${label} crashed ${rec.restartTimes.length} times in ${minutes} minutes and is no longer being restarted. Use the tray's "Restart Iron Jarvis" to try again.`);
      refreshTrayMenu(); // adds the "Restart Iron Jarvis" item
      return;
    }
    rec.restartTimes.push(now);
    const delay = RESTART_BACKOFF_MS[Math.min(rec.restarts - 1, RESTART_BACKOFF_MS.length - 1)];
    desktopLog("error", `[${label}] unexpected exit — restart #${rec.restarts} in ${delay}ms`);
    fileLogger(label)(`[main] ${new Date(now).toISOString()} unexpected exit — restart #${rec.restarts} in ${delay}ms\n`);
    if (rec.restarts === 3) notifyCrashLoop(label);
    else if (rec.deaths.length === DEATHS_DAY_TOAST_AT) {
      notifyCrashLoop(label, `The ${label} has crashed ${rec.deaths.length} times in the last 24 hours and is being restarted each time. Logs: ${path.join(userDataDir || "", "logs")}`);
    }
    // The handle lives on the record (review of D1): the tray "Restart Iron
    // Jarvis" spawns a dead child at once and must cancel this pending spawn,
    // or two dashboards race for :8788 and the ladder supervises the loser.
    const seq = (rec.restartSeq = (rec.restartSeq || 0) + 1);
    rec.restartTimer = setTimeout(() => {
      if (rec.restartSeq !== seq) return; // superseded by a tray Restart
      rec.restartTimer = null;
      if (!shuttingDown && !isQuitting) startService(label, rec.spawnFn);
    }, delay);
  };
  child.on("close", (code) => onGone(code));
  child.on("error", () => {
    // Fallback should close never arrive for a failed spawn. Only once the
    // child is really dead: an error while it runs (a kill() failure) is not
    // an exit.
    setTimeout(() => {
      if (child.exitCode !== null || child.signalCode !== null) onGone(child.exitCode);
    }, 1000);
  });
  return child;
}

// Tray "Restart Iron Jarvis" (audit D1): resets every ladder counter, respawns
// a capped child, and restarts a live one on purpose (manualRestart keeps
// the ladder from counting that kill as a crash).
function restartServicesFromTray() {
  for (const [label, rec] of Object.entries(_services)) {
    if (!rec.spawnFn) continue;
    rec.restarts = 0;
    rec.fastDeaths = 0;
    rec.restartTimes = [];
    rec.capped = false;
    rec.swept = false;
    rec.restartSeq = (rec.restartSeq || 0) + 1; // a backoff still pending: we spawn now instead
    if (rec.restartTimer) clearTimeout(rec.restartTimer);
    rec.restartTimer = null;
    markTrayHealthy(label);
    const child = label === "daemon" ? daemonProc : dashboardProc;
    const alive = !!child && child.exitCode === null && child.signalCode === null;
    if (alive) {
      rec.manualRestart = true;
      killChild(child, label, "restart"); // onGone respawns at once
    } else if (!rec.adopted) {
      startService(label, rec.spawnFn);
    }
  }
  refreshTrayMenu();
}

function notifyCrashLoop(label, body) {
  const logsDir = path.join(userDataDir || "", "logs");
  const rec = _services[label];
  markTrayDegraded(
    label,
    rec && rec.capped
      ? `Iron Jarvis — ${label} stopped (crashed too often; Restart from the tray)`
      : `Iron Jarvis — ${label} is restarting repeatedly (check logs)`
  );
  try {
    new Notification({
      title: "Iron Jarvis — problem",
      body: body || `The ${label} keeps crashing and is being restarted. Logs: ${logsDir}`,
    }).show();
  } catch {
    /* notifications unavailable */
  }
}

// Exit 75 (contract C2 as amended, v1.226.0): an Iron Jarvis daemon already
// owns the port. "Ours" is decided by the OWNERSHIP PROBE — never by version
// alone, because a same-version dev daemon (no token) would have been adopted
// silently: the wrong HOME on screen, then killed by our Quit.
//   ours + our version      → adopt it: the user keeps working, no restart storm.
//   ours + another version  → a stale orphan of THIS install (after an update):
//                             sweep our image name and restart ONCE; a second
//                             75 after the sweep means we could not kill it.
//   not ours                → say so once (port + version); no restart, no sweep.
let _daemonClaimedThisSession = false; // we adopted or swept a daemon: it is ours to sweep on Quit

const OWNERSHIP_RETRY_MS = 2000;
const OWNERSHIP_MAX_TRIES = 3;

function adoptOrReplaceExistingDaemon(rec, attempt = 1) {
  Promise.all([
    probeDaemonHealth(DAEMON_WATCHDOG_PROBE_TIMEOUT_MS),
    probeDaemonOwnership(DAEMON_WATCHDOG_PROBE_TIMEOUT_MS),
  ]).then(([health, owner]) => {
    if (shuttingDown || isQuitting) return;
    const appVersion = app.getVersion();
    const found = health ? `v${health.version}` : "no /health answer";
    if (owner === "unknown") {
      // Not proven either way (review S3). Re-run the decision shortly; the
      // boot gate is doing the same. foreignNotified is NEVER latched here —
      // an unverifiable daemon is not a foreign one.
      if (attempt < OWNERSHIP_MAX_TRIES) {
        fileLogger("daemon")(`[main] could not verify who owns port ${DAEMON_PORT} (${found}) — retrying (${attempt}/${OWNERSHIP_MAX_TRIES})\n`);
        setTimeout(() => adoptOrReplaceExistingDaemon(rec, attempt + 1), OWNERSHIP_RETRY_MS);
        return;
      }
      if (!health) {
        // Nothing answers on the port any more — whoever held it is gone.
        // Start our own (no sweep: nothing was proven ours); a still-held
        // port comes back as another 75 or a 1 and is decided again.
        desktopLog("warn", `[daemon] port ${DAEMON_PORT} went quiet after exit 75 — starting our own daemon`);
        fileLogger("daemon")(`[main] port ${DAEMON_PORT} went quiet after exit 75 — starting our own daemon\n`);
        startService("daemon", rec.spawnFn);
        return;
      }
      desktopLog("error", `[daemon] could not verify who owns port ${DAEMON_PORT} (${found}) after ${OWNERSHIP_MAX_TRIES} tries — not adopting, not restarting`);
      fileLogger("daemon")(`[main] could not verify who owns port ${DAEMON_PORT} (${found}) after ${OWNERSHIP_MAX_TRIES} tries — not adopting, not restarting\n`);
      return;
    }
    if (owner === "foreign") {
      desktopLog("error", `[daemon] port ${DAEMON_PORT} is held by an Iron Jarvis daemon that is not ours (${found}) — not adopting, not restarting`);
      fileLogger("daemon")(`[main] port ${DAEMON_PORT} is held by an Iron Jarvis daemon that is not ours (${found}) — not adopting, not restarting\n`);
      if (!rec.foreignNotified) {
        rec.foreignNotified = true;
        notifyForeignDaemon(found);
      }
      return;
    }
    if (health && health.version === appVersion) {
      rec.adopted = true;
      rec.restarts = 0;
      rec.swept = false;
      _daemonClaimedThisSession = true;
      desktopLog("warn", `[daemon] adopted an existing daemon (v${health.version}) on port ${DAEMON_PORT}`);
      fileLogger("daemon")(`[main] adopted an existing daemon (v${health.version}) on port ${DAEMON_PORT}\n`);
      try {
        if (tray) tray.setToolTip("Iron Jarvis — running");
      } catch {
        /* tray may be gone */
      }
      return;
    }
    if (rec.swept) {
      desktopLog("error", `[daemon] port ${DAEMON_PORT} is still held by our stale daemon (${found}) after a sweep — not restarting again`);
      fileLogger("daemon")(`[main] port ${DAEMON_PORT} still held by our stale daemon (${found}) after a sweep — giving up\n`);
      if (!rec.foreignNotified) {
        rec.foreignNotified = true;
        notifyForeignDaemon(found);
      }
      return;
    }
    rec.swept = true;
    _daemonClaimedThisSession = true;
    desktopLog("warn", `[daemon] our stale daemon of another version (${found}; this app is v${appVersion}) holds port ${DAEMON_PORT} — sweeping orphans and restarting once`);
    fileLogger("daemon")(`[main] our stale daemon of another version (${found}; this app is v${appVersion}) holds port ${DAEMON_PORT} — sweeping orphans and restarting once\n`);
    sweepOrphanDaemons();
    startService("daemon", rec.spawnFn);
  });
}

// One GET, status only (null on any failure). Used by the ownership probe.
function httpStatus(apiPath, bearer, timeoutMs) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => {
      if (done) return;
      done = true;
      resolve(v);
    };
    try {
      const req = http.get(
        `http://127.0.0.1:${DAEMON_PORT}${apiPath}`,
        { headers: { Authorization: `Bearer ${bearer}` } },
        (res) => {
          res.resume();
          finish(res.statusCode);
        }
      );
      req.on("error", () => finish(null));
      req.setTimeout(timeoutMs, () => req.destroy(new Error("status probe timeout")));
    } catch {
      finish(null);
    }
  });
}

// The OWNERSHIP PROBE (contract C2 as amended, v1.226.0): the daemon on the
// port is ours only if a token-guarded route answers 200 to OUR bearer AND
// refuses (401/403) a deliberately wrong one. A token-less dev daemon says 200
// to both → not ours. A stale orphan of this install shares token.txt → ours.
// TRI-STATE (review S3): "ours" / "foreign" / "unknown". Foreign is PROVEN —
// 200 to both bearers, or our own bearer refused. Anything with a missing
// status (timeout, refused connection) is unknown: a transient 5s stall of one
// localhost request must not be read as "someone else's daemon". Never
// rejects; no token of our own means we cannot prove anything → unknown.
function probeDaemonOwnership(timeoutMs) {
  if (!authToken) return Promise.resolve("unknown");
  const refused = (s) => s === 401 || s === 403;
  return Promise.all([
    httpStatus("/diagnostics/reliability", authToken, timeoutMs),
    httpStatus("/diagnostics/reliability", "deliberately-wrong-token", timeoutMs),
  ])
    .then(([mine, wrong]) => {
      if (mine === 200 && refused(wrong)) return "ours";
      if (mine === 200 && wrong === 200) return "foreign";
      if (refused(mine)) return "foreign";
      return "unknown";
    })
    .catch(() => "unknown");
}

function notifyForeignDaemon(found) {
  try {
    if (tray) tray.setToolTip(`Iron Jarvis — another Iron Jarvis daemon (${found}) holds port ${DAEMON_PORT}`);
  } catch {
    /* tray may be gone */
  }
  try {
    new Notification({
      title: "Iron Jarvis — problem",
      body: `Another Iron Jarvis daemon (${found}) is using port ${DAEMON_PORT} and could not be replaced. Close it, then Quit and relaunch.`,
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

function killChild(child, label, reason = "quit") {
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
    console.log(`[${label}] killed (pid=${pid}, reason=${reason})`);
    // The file used to show only "exited (code=1)" for a kill AND for a crash
    // (audit D5) — say which, and why, next to the child's own last lines.
    fileLogger(label)(`[main] ${new Date().toISOString()} killed pid=${pid} reason=${reason}\n`);
  } catch (err) {
    desktopLog("error", `[${label}] failed to kill (pid=${pid}):`, err.message);
  }
}

function shutdown(reason) {
  if (shuttingDown) return;
  shuttingDown = true;
  const why = typeof reason === "string" ? reason : "quit"; // process.on("exit") passes the code
  killChild(daemonProc, "daemon", why);
  killChild(dashboardProc, "dashboard", why);
}

// Ask the daemon to exit cleanly (POST /shutdown -> uvicorn SIGTERM -> lifespan
// shutdown) and wait briefly for the process to die. Resolves true when it
// exited by itself; false means the caller should force-kill. The auto-update
// path deliberately SKIPS this and calls shutdown() synchronously — NSIS needs
// the process tree dead before it returns.
// POST /shutdown, best-effort; true when the request was issued. A daemon that
// is not answering is covered by the caller's force-kill fallback.
function postDaemonShutdown() {
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
    return true;
  } catch {
    return false;
  }
}

// Poll /health until it stops answering (true) or the budget runs out (false).
function waitForDaemonPortClosed(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve) => {
    const poll = () => {
      probeDaemonHealth(1000).then((health) => {
        if (!health) return resolve(true);
        if (Date.now() >= deadline) return resolve(false);
        setTimeout(poll, 250);
      });
    };
    setTimeout(poll, 250);
  });
}

// Quit-time sweep (contract C2 as amended, v1.226.0): taskkill by image name
// hits EVERY ironjarvis.exe on the machine — including a dev `uv run
// ironjarvis serve` (.venv/Scripts/ironjarvis.exe) on any port. So never
// blind: sweep only when this session adopted or swept a daemon (it is ours),
// or when /health still answers AND the ownership probe says ours. Resolves
// whether a sweep ran; never rejects.
let _quitOwnershipVerdict = null; // requestDaemonShutdown's answer, reused so Quit probes at most once

function sweepOwnDaemonOnQuit() {
  if (_daemonClaimedThisSession) {
    sweepOrphanDaemons();
    return Promise.resolve(true);
  }
  if (_quitOwnershipVerdict) {
    const ours = _quitOwnershipVerdict === "ours";
    if (ours) sweepOrphanDaemons();
    return Promise.resolve(ours);
  }
  return probeDaemonHealth(1000)
    .then((health) => (health ? probeDaemonOwnership(1500) : "unknown"))
    .then((owner) => {
      const ours = owner === "ours";
      if (ours) sweepOrphanDaemons();
      return ours;
    })
    .catch(() => false);
}

function requestDaemonShutdown(timeoutMs) {
  return new Promise((resolve) => {
    if (!daemonProc || daemonProc.exitCode !== null || daemonProc.signalCode !== null) {
      // Our child is gone — the daemon may not be (v1.226.0, F-E-2): an
      // ADOPTED daemon (exit 75) or a stale one from a session that died hard
      // still owns the port, and resolving true here left it running after
      // Quit — schedules and webhooks firing from a "closed" app. If /health
      // still answers, ask it to stop too and wait (bounded) for the port to
      // close; the caller's sweep covers one that ignores us.
      probeDaemonHealth(1500).then((health) => {
        if (!health) return resolve(true); // never started or already gone
        // Only a daemon PROVEN ours gets the request (review S2): a token-less
        // dev daemon accepts POST /shutdown from anyone, so posting on "it
        // answers /health" killed the user's dev daemon by HTTP instead.
        const owned = _daemonClaimedThisSession
          ? Promise.resolve("ours")
          : probeDaemonOwnership(1500);
        owned.then((owner) => {
          _quitOwnershipVerdict = owner; // the quit sweep reuses this — no second probe
          if (owner !== "ours") return resolve(true); // not ours to stop
          if (!postDaemonShutdown()) return resolve(false);
          waitForDaemonPortClosed(timeoutMs).then(resolve);
        });
      });
      return;
    }
    if (!postDaemonShutdown()) return resolve(false);
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
  let lastWhy = ""; // the most specific reason a probe was refused, for the timeout message
  return new Promise((resolve, reject) => {
    const attempt = () => {
      // Fail FAST if the daemon child already exited — e.g. serve()'s preflight
      // found a foreign program on the port and exited non-zero. Don't wait 30s.
      // 75 is contract C2 (v1.226.0): "attached to an existing Iron Jarvis
      // daemon" — the supervisor adopts or replaces it; keep polling.
      if (
        daemonProc &&
        daemonProc.exitCode !== null &&
        daemonProc.exitCode !== 0 &&
        daemonProc.exitCode !== 75
      ) {
        // A NEGATIVE code is a spawn failure (ENOENT/EACCES — v1.226.0, F-E-3):
        // the daemon program itself, not the port, is the problem.
        if (daemonProc.exitCode < 0) {
          return reject(
            new Error(
              `the daemon program could not be started (missing or blocked file; spawn code ${daemonProc.exitCode})`
            )
          );
        }
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
            let why = "";
            if (res.statusCode === 200) {
              try {
                const d = JSON.parse(body);
                ok = d && d.status === "ok" && !!d.version;
                // A healthy daemon of ANOTHER version is not ours (v1.226.0,
                // F-B-2): the daily driver would run against a stale or dev
                // daemon's sessions and settings. The three version files are
                // kept equal by the suite, so this compares like with like.
                if (ok && d.version !== app.getVersion()) {
                  ok = false;
                  why = `an Iron Jarvis daemon of a different version (v${d.version}; this app is v${app.getVersion()}) answers on the port`;
                }
                // A version match is not ownership (review S1): a token-less
                // dev daemon of the same version passed this gate at t=1s and
                // the packaged app booted against ITS home. Require the
                // ownership probe; once the supervisor has proven the holder
                // foreign (exit 75 → foreignNotified) fail now, not at 90s.
                if (ok) {
                  const rec = _services.daemon;
                  const foreignWhy = `port ${DAEMON_PORT} is held by an Iron Jarvis daemon that is not ours (v${d.version}) — stop it or change its port`;
                  if (rec && rec.foreignNotified) return reject(new Error(foreignWhy));
                  probeDaemonOwnership(2500).then((owner) => {
                    if (owner === "ours") return resolve();
                    retry(owner === "foreign" ? foreignWhy : `could not verify who owns port ${DAEMON_PORT} (v${d.version})`);
                  });
                  return;
                }
              } catch {
                ok = false;
              }
            }
            retry(why);
          });
        }
      );
      req.on("error", () => retry());
      req.setTimeout(2500, () => req.destroy(new Error("probe timeout")));
      function retry(why) {
        if (why) lastWhy = why;
        if (Date.now() >= deadline) {
          reject(
            new Error(`daemon /health not healthy within ${timeoutMs}ms` + (lastWhy ? ` (${lastWhy})` : ""))
          );
        } else setTimeout(attempt, intervalMs);
      }
    };
    attempt();
  });
}

// --- Daemon liveness watchdog (v1.226.0, F-E-1) --------------------------
// The crash supervisor only sees a daemon that DIES. One that stays bound on
// the port but stops answering (a wedged event loop) was never detected: the
// tray said running while schedules, webhooks, reflex and autonomy were dead
// until a manual Quit + relaunch. After boot, probe /health every 30s; three
// misses in a row with the child still alive → log, record the incident, kill
// it, and let the exit ladder bring it back. A breaker like the renderer's:
// three kills in 15 minutes means the cause is systemic — stop and say so.
const DAEMON_WATCHDOG_MS = 30000;
const DAEMON_WATCHDOG_PROBE_TIMEOUT_MS = 5000;
const DAEMON_WATCHDOG_MISS_LIMIT = 3;
const DAEMON_WATCHDOG_BREAKER_WINDOW_MS = 15 * 60 * 1000;
const DAEMON_WATCHDOG_BREAKER_MAX = 3;
let _dwTimer = null;
let _dwMissed = 0;
let _dwPid = null; // a fresh child (after a restart) gets a full grace period
let _dwKills = []; // timestamps of watchdog kills (breaker)
let _dwBreakerTripped = false;
let _dwProbeInFlight = false;
// A RESTARTED child gets the boot gate's grace (v1.226.0 review R4): no miss
// is counted until it has answered /health once or STARTUP_TIMEOUT_MS has
// passed since it was spawned — a cold packaged boot can take up to 90s and
// the old 60-90s (tick-phase dependent) grace killed a booting daemon three
// times in a row, tripped the breaker and left the app down for minutes.
let _dwBooting = false;

// GET /health with the bearer. Resolves the parsed body when it is OUR daemon's
// healthy shape (status ok + version) — the same acceptance as the boot gate —
// else null. Never rejects.
function probeDaemonHealth(timeoutMs) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => {
      if (done) return;
      done = true;
      resolve(v);
    };
    try {
      const req = http.get(
        `http://127.0.0.1:${DAEMON_PORT}/health`,
        { headers: authToken ? { Authorization: `Bearer ${authToken}` } : {} },
        (res) => {
          let body = "";
          res.setEncoding("utf8");
          res.on("data", (c) => {
            if (body.length < 64 * 1024) body += c;
          });
          res.on("end", () => {
            let d = null;
            if (res.statusCode === 200) {
              try {
                d = JSON.parse(body);
              } catch {
                d = null;
              }
            }
            finish(d && d.status === "ok" && d.version ? d : null);
          });
          res.on("error", () => finish(null));
        }
      );
      req.on("error", () => finish(null));
      req.setTimeout(timeoutMs, () => req.destroy(new Error("health probe timeout")));
    } catch {
      finish(null);
    }
  });
}

function daemonWatchdogPaused() {
  return shuttingDown || isQuitting || updateInstallInFlight;
}

function daemonWatchdogTick() {
  if (daemonWatchdogPaused()) {
    _dwMissed = 0;
    return;
  }
  if (_dwProbeInFlight) return;
  const pid = daemonProc ? daemonProc.pid : null;
  if (pid !== _dwPid) {
    _dwPid = pid;
    _dwMissed = 0;
    _dwBooting = true;
  }
  _dwProbeInFlight = true;
  probeDaemonHealth(DAEMON_WATCHDOG_PROBE_TIMEOUT_MS).then((health) => {
    _dwProbeInFlight = false;
    if (daemonWatchdogPaused()) {
      _dwMissed = 0;
      return;
    }
    const rec = _services.daemon;
    if (health) {
      _dwMissed = 0;
      _dwBooting = false;
      markTrayHealthy("daemon"); // audit D4: the next healthy /health says so on the tray
      return;
    }
    if (_dwBooting && rec && Date.now() - rec.lastStart < STARTUP_TIMEOUT_MS) return; // still booting
    _dwMissed += 1;
    if (_dwMissed < DAEMON_WATCHDOG_MISS_LIMIT) return;
    _dwMissed = 0;
    const alive = !!daemonProc && daemonProc.exitCode === null && daemonProc.signalCode === null;
    if (!alive) {
      // Nothing of ours to kill: a dead child is the exit ladder's job. An
      // ADOPTED daemon (exit 75) has no child and no ladder, though — it went
      // away, so start our own.
      if (rec && rec.adopted && rec.spawnFn) {
        rec.adopted = false;
        desktopLog("warn", "[daemon] watchdog: the adopted daemon stopped answering /health — sweeping it and starting our own");
        fileLogger("daemon")("[main] watchdog: the adopted daemon stopped answering /health — sweeping it and starting our own\n");
        // It is OURS (adoption requires the ownership probe) and it may still
        // hold the port while wedged: sweep first, or serve's preflight sees a
        // held port with a dead /health, exits 1, and the ladder loops.
        sweepOrphanDaemons();
        startService("daemon", rec.spawnFn);
      }
      return;
    }
    const now = Date.now();
    _dwKills = _dwKills.filter((t) => now - t < DAEMON_WATCHDOG_BREAKER_WINDOW_MS);
    if (_dwKills.length >= DAEMON_WATCHDOG_BREAKER_MAX) {
      if (!_dwBreakerTripped) {
        _dwBreakerTripped = true;
        desktopLog("error", `[daemon] watchdog: ${_dwKills.length} restarts in ${Math.round(DAEMON_WATCHDOG_BREAKER_WINDOW_MS / 60000)} minutes are not converging — breaker open, not killing again`);
        fileLogger("daemon")(`[main] watchdog: ${_dwKills.length} restarts in ${Math.round(DAEMON_WATCHDOG_BREAKER_WINDOW_MS / 60000)} minutes — breaker open, not killing again\n`);
        notifyWatchdogExhausted();
      }
      return;
    }
    _dwBreakerTripped = false;
    _dwKills.push(now);
    const detail = `/health missed ${DAEMON_WATCHDOG_MISS_LIMIT}x (~${Math.round((DAEMON_WATCHDOG_MS * DAEMON_WATCHDOG_MISS_LIMIT) / 1000)}s) while pid=${daemonProc.pid} was alive — killed for restart`;
    desktopLog("error", `[daemon] watchdog: ${detail}`);
    fileLogger("daemon")(`[main] watchdog: ${detail}\n`);
    reportIncident("daemon-wedged", detail);
    killChild(daemonProc, "daemon", "watchdog"); // the exit ladder restarts it
  });
}

function notifyWatchdogExhausted() {
  const logsDir = path.join(userDataDir || "", "logs");
  markTrayDegraded("daemon", "Iron Jarvis — the daemon keeps stalling (check logs)");
  try {
    new Notification({
      title: "Iron Jarvis — problem",
      body: `The daemon stopped answering repeatedly and automatic restarts are paused. Quit and relaunch. Logs: ${logsDir}`,
    }).show();
  } catch {
    /* notifications unavailable */
  }
}

function installDaemonWatchdog() {
  if (_dwTimer) clearInterval(_dwTimer);
  _dwMissed = 0;
  _dwBooting = false; // the boot gate just passed: this child has answered
  _dwPid = daemonProc ? daemonProc.pid : null;
  _dwTimer = setInterval(daemonWatchdogTick, DAEMON_WATCHDOG_MS);
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
    desktopLog("error", "[update] could not write pending marker:", err && err.message);
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
      desktopLog("error", "[integrity] could not launch repair installer:", err && err.message);
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
  // v1.249.0 (R-04): Windows warns a top-level window that the session is
  // ending before it terminates anything — record it so the supervisor reads
  // the children's deaths correctly.
  mainWin.on("session-end", () => markWindowsSessionEnding("ending this session"));

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
        desktopLog("error", "[token] localStorage ensure failed:", err && err.message);
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
        desktopLog("error", "[close] prompt failed:", err && err.message);
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
  installDashboardReloadOnFailure(mainWin);

  mainWin.loadURL(DASHBOARD_URL);
}

// A dashboard-child outage used to strand the window on Chromium's error page
// for good (v1.226.0, F-E-7): a reload during the 1-60s restart window failed
// and nothing ever retried. Wait for the dashboard to answer with the Iron
// Jarvis app again, then load it. -3 is ERR_ABORTED — a navigation superseded
// by another one — not a failure.
// That wait was ONE-SHOT (v1.229.0, audit D1): after 60 s it gave up and a
// dashboard the ladder brought back five minutes later never reached the
// window. It now loops while the window sits on the error page, pauses only
// when the ladder has given up on the dashboard, and is re-armed by every
// dashboard spawn (startService).
let _dashboardReloadPending = false;
let _dashboardLoadFailed = false; // the main frame is on Chromium's error page
let _dashboardReloadWc = null;
function armDashboardReload() {
  if (!_dashboardLoadFailed || _dashboardReloadPending) return;
  const wc = _dashboardReloadWc;
  if (!wc || wc.isDestroyed()) {
    _dashboardLoadFailed = false;
    return;
  }
  const rec = _services.dashboard;
  if (rec && rec.capped) return; // nothing is coming back until the tray Restart
  _dashboardReloadPending = true;
  waitForDashboard(60000, 500)
    .then(() => {
      _dashboardReloadPending = false;
      _dashboardLoadFailed = false; // a failed loadURL sets it again via did-fail-load
      if (!wc.isDestroyed()) wc.loadURL(DASHBOARD_URL);
    })
    .catch(() => {
      _dashboardReloadPending = false;
      armDashboardReload(); // still on the error page — keep waiting
    });
}
function installDashboardReloadOnFailure(win) {
  const wc = win.webContents;
  wc.on("did-fail-load", (_e, code, _desc, url, isMainFrame) => {
    if (code === -3 || !isMainFrame || !isDashboardUrl(url)) return;
    _dashboardLoadFailed = true;
    _dashboardReloadWc = wc;
    if (!_dashboardReloadPending) {
      desktopLog("warn", `[window] dashboard load failed (${code}) — waiting for the dashboard to come back`);
    }
    armDashboardReload();
  });
  wc.on("destroyed", () => {
    if (_dashboardReloadWc === wc) {
      _dashboardReloadWc = null;
      _dashboardLoadFailed = false;
    }
  });
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
          // showMainWindow(), not a guarded show(): hideToTray() DESTROYS the
          // window (mainWin = null), which is the most common state a toast is
          // clicked in — a bare show/focus made the "invitation back in" a
          // no-op there. Matches the Spotlight notification's handler.
          showMainWindow();
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
  // Image clipboard (v1.194.0): Win+Shift+S puts a BITMAP on the clipboard, and
  // in the packaged app `navigator.clipboard.read()` is permission-gated — the
  // same reason the text read above goes through IPC. Without this, pasting a
  // snip into the Build page works in a browser tab and silently does nothing in
  // the app the user actually runs. Sender-checked exactly like clipboard:read
  // (a screenshot is at least as sensitive as copied text); refusal returns the
  // same `null` as an empty clipboard, which callers already handle.
  // Shape: null = the clipboard holds no image; `{error:"unreadable"}` = there
  // WAS one and it would not encode (never reported as "nothing copied" — a
  // silent downgrade reads as user error); otherwise PNG base64 + its size.
  ipcMain.handle("clipboard:readImage", (event) => {
    if (!isTrustedDashboardSender(event)) return null;
    let size = { width: 0, height: 0 };
    try {
      const img = clipboard.readImage();
      if (!img || img.isEmpty()) return null;
      size = img.getSize() || size;
      const png = img.toPNG();
      if (png && png.length)
        return {
          base64: png.toString("base64"),
          bytes: png.length,
          width: size.width | 0,
          height: size.height | 0,
        };
    } catch {
      /* falls through to the honest unreadable report */
    }
    return {
      error: "unreadable",
      bytes: 0,
      width: size.width | 0,
      height: size.height | 0,
    };
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
  // Shell facts the dashboard must not guess (v1.229.0, audit D2): which
  // global hotkeys ARE registered right now, as the label a user presses
  // ("Ctrl+Alt+J") or null when every rung of the ladder was taken. Sender-
  // checked like the other privileged handlers.
  ipcMain.handle("shell:getState", (event) => {
    if (!isTrustedDashboardSender(event)) return null;
    return shellState();
  });
  // Open the logs folder (v1.229.0, audit D8/OBS5). Sender-checked: it
  // launches the OS file manager on the user's machine.
  ipcMain.handle("shell:openLogs", (event) => {
    if (!isTrustedDashboardSender(event)) return null;
    return openLogsFolder();
  });
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
  // tray item and the update notification call requestUpdateInstall() directly —
  // they are main-process code and never come through here.
  ipcMain.handle("update:apply", (event) => {
    if (!isTrustedDashboardSender(event)) return false;
    if (pendingUpdateInfo) requestUpdateInstall();
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
        click: () => requestUpdateInstall(),
      },
      { type: "separator" }
    );
  }
  template.push(
    {
      label: hotkeyState.window
        ? `Open Iron Jarvis (${accelLabel(hotkeyState.window)})`
        : "Open Iron Jarvis — hotkey unavailable (taken by another app)",
      click: () => showMainWindow(),
    },
    {
      label: hotkeyState.spotlight
        ? `Quick task…  (${accelLabel(hotkeyState.spotlight)})`
        : "Quick task… — hotkey unavailable (taken by another app)",
      click: () => toggleSpotlight(),
    },
    // The always-available unfreeze: reloads just the UI (state lives in the
    // daemon). Discoverable here because a frozen window can't show its own
    // menus — the tray keeps working even when the renderer doesn't.
    { label: "Reload UI", click: () => reloadUI() },
    // Shown only once the ladder has given up on a child (audit D1) — the
    // user's way back without a Quit + relaunch.
    ...(Object.values(_services).some((r) => r.capped)
      ? [{ label: "Restart Iron Jarvis", click: () => restartServicesFromTray() }]
      : []),
    // Where the daemon/dashboard/desktop logs live (v1.229.0, audit D8): the
    // path used to appear only inside a crash toast.
    { label: "Open logs folder", click: () => openLogsFolder() },
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
    desktopLog("error", "[tray] could not refresh menu:", err && err.message);
  }
}

// Rebuild both menus so their "Keep running in background" checkboxes stay in
// sync after a toggle (from either menu) or a close-prompt answer.
function refreshMenus() {
  buildMenu();
  refreshTrayMenu();
}

// --- Jobs waiting for you, with no window open (v1.249.0, R-03) ----------
// Since v1.247.0 an attended ask WAITS instead of expiring — and closing the
// window to the tray destroys the renderer, so the page's bell (the only thing
// that raised a Windows toast) is gone exactly when the wait starts. The tray
// said "running" and a job could sit there all day. The main process now
// watches the same listing the bell polls and speaks for it.

//: How often the tray checks for waiting jobs.
const ASK_WATCH_MS = 25000;
//: Reminders after the first notice, measured from when we first saw the ask.
const ASK_REMINDER_MS = [60 * 60 * 1000, 8 * 60 * 60 * 1000];
let askSeen = {}; // id -> { first: ms, sent: number }
let askWaitingCount = 0;

//: Plain words for the common asks; anything else names the tool as it is.
const ASK_PHRASES = {
  rename_file: ["rename", "file", "files"],
  rename_real_file: ["rename", "file", "files"],
  write_file: ["write", "file", "files"],
  write_document: ["create", "document", "documents"],
  delete_file: ["delete", "file", "files"],
  move_file: ["move", "file", "files"],
  excel_edit: ["edit", "workbook", "workbooks"],
  shell: ["run", "command", "commands"],
};

function plainAskSummary(ask) {
  const count = Number(ask && ask.count) > 1 ? Math.floor(Number(ask.count)) : 1;
  const tool = String((ask && ask.tool) || "a tool");
  const phrase = ASK_PHRASES[tool];
  if (phrase) {
    return count > 1 ? `${phrase[0]} ${count} ${phrase[2]}` : `${phrase[0]} a ${phrase[1]}`;
  }
  const spoken = tool.replace(/_/g, " ");
  return count > 1 ? `use ${spoken} (${count} times)` : `use ${spoken}`;
}

// WHAT TO SAY, and to whom — pure, so the schedule is testable without toasts.
// While a window is open the page's bell owns the announcing: the ask is still
// RECORDED here (so closing the window later does not re-announce it), but
// nothing is raised, and reminders stay with the surface the user can see.
function planAskNotifications(seen, approvals, now, windowOpen) {
  const next = {};
  const notify = [];
  for (const ask of Array.isArray(approvals) ? approvals : []) {
    const id = ask && typeof ask.id === "string" ? ask.id : "";
    if (!id) continue;
    const prev = seen[id];
    const rec = prev ? { first: prev.first, sent: prev.sent } : { first: now, sent: 0 };
    const summary = plainAskSummary(ask);
    const sessionId = typeof ask.session_id === "string" ? ask.session_id : "";
    if (!prev) {
      if (windowOpen) {
        rec.sent = 1; // the bell in front of the user has it
      } else {
        notify.push({
          id,
          sessionId,
          title: "Jarvis is waiting for you",
          body: `${summary} — click to open the job.`,
        });
        rec.sent = 1;
      }
    } else if (!windowOpen) {
      const due = ASK_REMINDER_MS[rec.sent - 1];
      if (due !== undefined && now - rec.first >= due) {
        // How long it has ACTUALLY waited, not which reminder this is: the
        // first reminder can land hours late (the window was open until now),
        // and "waiting 1 hour" for a job that has sat for five is a lie.
        const hours = Math.max(1, Math.floor((now - rec.first) / (60 * 60 * 1000)));
        notify.push({
          id,
          sessionId,
          title: "Still waiting for you",
          body: `${summary} has been waiting ${hours} hour${hours === 1 ? "" : "s"} — click to open the job.`,
        });
        rec.sent += 1;
      }
    }
    next[id] = rec;
  }
  return { seen: next, notify, waiting: Object.keys(next).length };
}

function setAskWaitingTooltip(count) {
  askWaitingCount = count;
  if (_trayDegraded.size) return; // a degraded service is the louder truth
  try {
    if (!tray) return;
    tray.setToolTip(
      count > 0
        ? `Iron Jarvis — ${count} job${count === 1 ? "" : "s"} waiting for you`
        : "Iron Jarvis — running"
    );
  } catch {
    /* tray may be gone */
  }
}

function showAskNotification(item) {
  try {
    const note = new Notification({ title: item.title, body: item.body });
    note.on("click", () => {
      showMainWindow();
      if (item.sessionId && mainWin && !mainWin.isDestroyed()) {
        mainWin.loadURL(`${DASHBOARD_URL}/sessions/${item.sessionId}`);
      }
    });
    note.show();
  } catch {
    /* notifications unavailable — the tray tooltip still carries the count */
  }
}

function installAskWatcher() {
  const tick = () => {
    const windowOpen = !!(mainWin && !mainWin.isDestroyed());
    daemonRequest("GET", "/chat/approvals/pending", null)
      .then((res) => {
        const list = res && Array.isArray(res.approvals) ? res.approvals : null;
        if (!list) return; // nothing answerable — keep what we know
        const plan = planAskNotifications(askSeen, list, Date.now(), windowOpen);
        askSeen = plan.seen;
        setAskWaitingTooltip(plan.waiting);
        for (const item of plan.notify) showAskNotification(item);
      })
      .catch(() => {
        /* daemon busy or down — try again on the next tick */
      });
  };
  setInterval(tick, ASK_WATCH_MS);
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
    desktopLog("error", "[tray] could not create tray:", err && err.message);
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

// Install a downloaded update. TWO invariants, and they used to be in the wrong
// order:
//   1. The daemon+dashboard must die SYNCHRONOUSLY (shutdown() blocks on
//      taskkill) before NSIS extracts, or it hits a file lock on the running
//      frozen exe and CORRUPTS the upgrade. Do NOT pre-set shuttingDown —
//      shutdown() would early-return and ORPHAN the children.
//   2. Nothing may be torn down until the installer handoff is CONFIRMED.
//      quitAndInstall never throws to us: electron-updater's install() returns
//      false and routes every failure through its 'error' event (the real
//      trigger is a cached download that went away — e.g. the 30-min re-check
//      invalidated it while pendingUpdateInfo stayed set). Killing first meant
//      that failure left the app RESIDENT with dead children, shuttingDown
//      permanently true (the crash supervisor disabled for the session),
//      schedules/webhooks silently off, and no dialog anywhere.
// Both hold because the failure surfaces SYNCHRONOUSLY, inside the
// quitAndInstall call, while a success only QUEUES app.quit() on setImmediate —
// so the teardown below still runs ahead of the quit and long before NSIS
// reaches the extraction step.
// Shared by the notification click, the tray item, and the in-app
// "Restart to update" affordance — all through requestUpdateInstall() below.
//: The tidy stop an update gets before NSIS runs (v1.249.0, R-02) — the same
//: budget Quit uses, because it is the same teardown: uvicorn drains open
//: streams, then the lifespan folds the WAL and writes the terminal snapshot.
//: Before this, an update force-killed the tree in 0.6 s and none of that ran.
const UPDATE_TIDY_SHUTDOWN_MS = 5000;

// Is the downloaded installer still there, and whole (v1.249.0, R-02)?
// "ok" | "missing" | "corrupt" | "unknown". UNKNOWN means this layout tells us
// nothing (a dev run, a future electron-updater) and the install proceeds
// exactly as before. The known failure trigger is a cached download the 30-min
// re-check invalidated while pendingUpdateInfo stayed set — checking it FIRST
// is what keeps that case from stopping the daemon for an install that cannot
// happen.
function cachedInstallerState() {
  try {
    const base = process.env.LOCALAPPDATA;
    if (!base) return "unknown";
    const pending = path.join(base, "iron-jarvis-desktop-updater", "pending");
    let info;
    try {
      info = JSON.parse(fs.readFileSync(path.join(pending, "update-info.json"), "utf8"));
    } catch {
      return "unknown"; // no marker to check against
    }
    if (!info || !info.fileName) return "unknown";
    const exe = path.join(pending, info.fileName);
    if (!fs.existsSync(exe)) return "missing";
    if (!info.sha512) return "unknown";
    const digest = crypto.createHash("sha512").update(fs.readFileSync(exe)).digest("base64");
    return digest === info.sha512 ? "ok" : "corrupt";
  } catch {
    return "unknown";
  }
}

// Bring back any child that is not running (v1.249.0, R-02). The install path
// stops the daemon BEFORE the handoff, so a handoff that never happens must
// not leave the app sitting there with a dead service.
function respawnStoppedServices() {
  for (const [label, rec] of Object.entries(_services)) {
    if (!rec.spawnFn || rec.adopted) continue;
    const child = label === "daemon" ? daemonProc : dashboardProc;
    const alive = !!child && child.exitCode === null && child.signalCode === null;
    if (!alive) startService(label, rec.spawnFn);
  }
}

async function applyPendingUpdate() {
  if (!pendingUpdateInfo || !_autoUpdater || updateInstallInFlight) return;
  updateInstallInFlight = true;
  // R-02: check the download BEFORE anything is stopped — a missing or
  // half-downloaded installer can then never cost the user a live daemon.
  const cached = cachedInstallerState();
  if (cached === "missing" || cached === "corrupt") {
    abortUpdateInstall(
      new Error(
        cached === "missing"
          ? "the downloaded update is no longer on disk"
          : "the downloaded update is incomplete (its checksum does not match)"
      )
    );
    return;
  }
  isQuitting = true; // allow the window to actually close
  markUpdatePending(pendingUpdateInfo.version); // recovery marker for a bad update
  // R-02: close the daemon the SAME tidy way Quit does, so the WAL is folded
  // and the terminal snapshot is written; force-kill only if it will not go.
  let stopped = false;
  try {
    stopped = await requestDaemonShutdown(UPDATE_TIDY_SHUTDOWN_MS);
  } catch {
    stopped = false;
  }
  if (!stopped) killChild(daemonProc, "daemon", "update");
  let failure = null;
  const onInstallError = (err) => {
    failure = err || new Error("the installer did not start");
  };
  _autoUpdater.once("error", onInstallError);
  try {
    _autoUpdater.quitAndInstall(false, true);
  } catch (err) {
    failure = err;
  }
  try {
    _autoUpdater.removeListener("error", onInstallError);
  } catch {
    /* listener already gone */
  }
  if (failure) {
    // R-02: the daemon was stopped for an install that never started — bring
    // it back BEFORE the dialog, so the app is whole while the user reads it.
    isQuitting = false;
    respawnStoppedServices();
    abortUpdateInstall(failure);
    return;
  }
  // Handoff accepted: the installer is launching and app.quit() is queued. Kill
  // whatever is still alive (the dashboard, and the daemon if it ignored the
  // tidy stop). An ORPHANED daemon from an earlier crashed session also locks
  // resources/daemon, so sweep by image name too.
  shutdown("update");
  sweepOrphanDaemons();
}

// A ready update installs only when the USER asks (tray item, notification
// click, Updates page) — but "Restart to update" used to taskkill the daemon
// with agent sessions and workflow runs mid-flight and no warning, while the
// Updates page promised the opposite (v1.226.0, F-E-4). Ask the daemon what is
// running first (contract C6, GET /system/activity); if it is busy, let the
// user choose. Best-effort: a daemon that cannot answer does not block the
// install — that is exactly the behaviour this had before.
const ACTIVITY_PROBE_TIMEOUT_MS = 3000;
let updatePromptInFlight = false;

// The work in progress, in the user's words (v1.249.0, R-02). Pure: the dialog
// and its test read the same sentence. The old dialog counted agent sessions
// and workflow runs only, so a chat reply being written and a Build pane with
// Claude working in it were killed without ever being mentioned.
function describeBusyWork(activity) {
  const a = activity || {};
  const n = (key) => {
    const v = Number(a[key]);
    return Number.isFinite(v) && v > 0 ? Math.floor(v) : 0;
  };
  const plural = (c, one, many) => `${c} ${c === 1 ? one : many}`;
  const parts = [];
  const chats = n("chat_replies");
  if (chats) parts.push(`${plural(chats, "chat reply", "chat replies")} in progress`);
  const panes = n("busy_panes");
  if (panes) {
    const seen = [];
    for (const raw of Array.isArray(a.busy_pane_clis) ? a.busy_pane_clis : []) {
      const name = String(raw || "").trim();
      if (!name) continue;
      const label = name.charAt(0).toUpperCase() + name.slice(1);
      if (!seen.includes(label)) seen.push(label);
    }
    parts.push(
      `${plural(panes, "Build pane", "Build panes")} working${seen.length ? ` (${seen.join(", ")})` : ""}`
    );
  }
  const sessions = n("active_sessions");
  if (sessions) parts.push(`${plural(sessions, "background job", "background jobs")} running`);
  const runs = n("running_workflow_runs");
  if (runs) parts.push(`${plural(runs, "workflow run", "workflow runs")} running`);
  return parts.join(" · ") || "Work is still in progress";
}

//: How often "Install when idle" re-checks (v1.249.0, R-02).
const INSTALL_WHEN_IDLE_MS = 60 * 1000;
let installWhenIdleTimer = null;

// The third answer to the busy dialog: wait, then install by itself. A daemon
// that cannot answer counts as idle — the same best-effort rule the prompt
// itself follows, so a dead daemon never strands a ready update.
function installWhenIdle() {
  if (installWhenIdleTimer) return;
  try {
    if (tray) tray.setToolTip("Iron Jarvis — update installs when the work finishes");
  } catch {
    /* tray may be gone */
  }
  installWhenIdleTimer = setInterval(() => {
    if (!pendingUpdateInfo || updateInstallInFlight) {
      clearInterval(installWhenIdleTimer);
      installWhenIdleTimer = null;
      return;
    }
    probeDaemonActivity(ACTIVITY_PROBE_TIMEOUT_MS).then((activity) => {
      if (!pendingUpdateInfo || updateInstallInFlight) return;
      if (activity && activity.busy) return; // still working — ask again in a minute
      clearInterval(installWhenIdleTimer);
      installWhenIdleTimer = null;
      applyPendingUpdate();
    });
  }, INSTALL_WHEN_IDLE_MS);
}

// Resolves the parsed /system/activity body, or null on any failure. Never rejects.
function probeDaemonActivity(timeoutMs) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => {
      if (done) return;
      done = true;
      resolve(v);
    };
    try {
      const req = http.get(
        `http://127.0.0.1:${DAEMON_PORT}/system/activity`,
        { headers: authToken ? { Authorization: `Bearer ${authToken}` } : {} },
        (res) => {
          let body = "";
          res.setEncoding("utf8");
          res.on("data", (c) => {
            if (body.length < 64 * 1024) body += c;
          });
          res.on("end", () => {
            let d = null;
            if (res.statusCode === 200) {
              try {
                d = JSON.parse(body);
              } catch {
                d = null;
              }
            }
            finish(d && typeof d === "object" ? d : null);
          });
          res.on("error", () => finish(null));
        }
      );
      req.on("error", () => finish(null));
      req.setTimeout(timeoutMs, () => req.destroy(new Error("activity probe timeout")));
    } catch {
      finish(null);
    }
  });
}

function requestUpdateInstall() {
  if (!pendingUpdateInfo || updateInstallInFlight || updatePromptInFlight) return;
  updatePromptInFlight = true;
  probeDaemonActivity(ACTIVITY_PROBE_TIMEOUT_MS)
    .then((activity) => {
      if (!pendingUpdateInfo || updateInstallInFlight) return;
      if (activity && activity.busy) {
        let choice = 1;
        try {
          choice = dialog.showMessageBoxSync({
            type: "warning",
            buttons: ["Install now", "Later", "Install when idle"],
            defaultId: 1,
            cancelId: 1,
            noLink: true,
            title: "Iron Jarvis — work in progress",
            message: `${describeBusyWork(activity)}.`,
            detail:
              "Installing now stops that work: a reply being written stops, programs running in " +
              "Build panes are closed, and background jobs are interrupted — you can continue " +
              "them after the restart. Later keeps the update ready in the tray. Install when " +
              "idle waits and installs it as soon as nothing is running.",
          });
        } catch {
          choice = 0; // a dialog failure must not block the install — proceed as before
        }
        if (choice === 2) {
          installWhenIdle();
          return;
        }
        if (choice !== 0) return; // Later
      }
      applyPendingUpdate();
    })
    .catch((err) => {
      desktopLog("error", "[update] activity check failed:", err && err.message);
      if (!updateInstallInFlight) applyPendingUpdate();
    })
    .finally(() => {
      updatePromptInFlight = false;
    });
}

// The installer never started, so the app is staying on this version. Put
// everything back the way it was and SAY SO — silence here is what stranded the
// user with a dead-looking app.
function abortUpdateInstall(err) {
  const raw = (err && err.message) || String(err || "the installer did not start");
  desktopLog("error", "[update] install did not start:", raw);
  updateInstallInFlight = false;
  clearUpdatePending(); // no install happened; the marker would misreport the next boot
  // Ordering above means the children are normally still alive and the crash
  // supervisor was never disabled — so we simply stay resident. If a teardown
  // DID already run, there is nothing left to be resident for (dead children,
  // shuttingDown latched, so the supervisor cannot bring them back): tell the
  // user, then quit cleanly so a relaunch restores a working app. Never leave
  // the half-torn-down zombie this function exists to prevent.
  const childrenGone = shuttingDown;
  if (!childrenGone) isQuitting = false; // a close must hide to the tray again
  // The update is NOT ready — stop the tray claiming it is. The next 30-min
  // check re-downloads it if it is still available.
  pendingUpdateInfo = null;
  refreshTrayMenu();
  try {
    if (tray) tray.setToolTip("Iron Jarvis — running");
  } catch {
    /* tray may be gone */
  }
  const friendly = friendlyUpdateError(raw);
  _emitUpdateState({ status: "error", error: friendly });
  try {
    // Sync, like the other update/install dialogs here: an error the user must
    // see, and no promise rejection to leak.
    dialog.showMessageBoxSync({
      type: "error",
      buttons: ["OK"],
      defaultId: 0,
      noLink: true,
      title: "Iron Jarvis — update did not install",
      message: "The update could not be started, so Iron Jarvis is still running the current version.",
      detail:
        `${friendly}\n\n` +
        "Nothing was changed and your data is untouched. Iron Jarvis will look for " +
        "the update again shortly; you can also install it manually from the " +
        "Releases page.",
    });
  } catch (dlgErr) {
    desktopLog("error", "[update] could not show the abort dialog:", dlgErr && dlgErr.message);
  }
  if (childrenGone) app.quit();
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
    desktopLog("error", "[update] electron-updater unavailable:", err.message);
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
    desktopLog("error", "[update] error:", msg);
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
      note.on("click", () => requestUpdateInstall());
      note.show();
    } catch (err) {
      desktopLog("error", "[update] notification unavailable:", err && err.message);
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
    .catch((err) => desktopLog("error", "[update] check failed:", err && err.message));
}

// --- Application menu ----------------------------------------------------

function buildMenu() {
  const template = [
    {
      label: "Iron Jarvis",
      submenu: [
        {
          label: "Open / Show Window",
          // Only a key that is really ours — an accelerator the OS gave to
          // another app would render in the menu and do nothing.
          ...(hotkeyState.window ? { accelerator: hotkeyState.window } : {}),
          click: () => showMainWindow(),
        },
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
  // v1.249.0 (R-04): on the platforms that emit it, the OS says the session is
  // ending before it kills anything (Windows also tells the window itself).
  try {
    powerMonitor.on("shutdown", () => markWindowsSessionEnding("shutting down"));
  } catch {
    /* powerMonitor unavailable — the exit-code path still covers it */
  }
  if (!START_HIDDEN) createLoadingWindow(); // login-boot goes straight to tray
  registerHotkey();
  installHotkeyRetry();

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
    // The add-on's folder is resolved at module scope (BROWSER_ADDON_DIR); this
    // only decides whether to TELL the daemon about it. Guarded by the manifest
    // rather than the directory: an extraResources entry that shipped empty (the
    // dist/ output is gitignored, so a build that skipped the add-on step
    // produces exactly that) must fall through and be reported by the doctor,
    // not exported as if it were loadable.
    const addonEnv = fs.existsSync(path.join(BROWSER_ADDON_DIR, "manifest.json"))
      ? { IRONJARVIS_BROWSER_ADDON_DIR: BROWSER_ADDON_DIR }
      : {};
    const voskModelDir = path.join(RES_DIR, "vosk-model");
    const voskEnv =
      fs.existsSync(path.join(voskModelDir, "am"))
        ? { IRONJARVIS_VOSK_MODEL: voskModelDir }
        : {};
    const resourceEnv = { ...voskEnv, ...addonEnv };
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
        { IRONJARVIS_TOKEN: authToken, IRONJARVIS_HOME: "", ...resourceEnv },
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

  // 3) Health-gate the DAEMON (guards a foreign process squatting on the baked
  //    port) and the dashboard AT THE SAME TIME, then swap the splash for the
  //    real window.
  //
  // WHY BOTH AT ONCE (v1.250.0, S-01): these gates used to run one after the
  // other, and the dashboard is ready in ~0.1 s while the daemon takes seconds
  // — so the app sat on the splash for the daemon, and only THEN started
  // probing (and rendering) a dashboard that had been up the whole time. The
  // window's first paint was pushed ~2 s past the moment it could have
  // happened. The gates are independent probes of independent children; the
  // ONLY ordering that ever mattered is that both must pass before a window is
  // shown, which `Promise.allSettled` below preserves exactly.
  //
  // The daemon's verdict is still reported FIRST when both fail: its failure is
  // the one that explains the other (no daemon → the dashboard renders an
  // offline shell), and `classifyStartupFailure` keys off the daemon's child
  // exit + log tail (v1.249.0, R-06).
  const [daemonGate, dashboardGate] = await Promise.allSettled([
    waitForDaemon(STARTUP_TIMEOUT_MS, GATE_POLL_MS),
    waitForDashboard(STARTUP_TIMEOUT_MS, GATE_POLL_MS),
  ]);
  try {
    if (daemonGate.status === "rejected") throw daemonGate.reason;
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
      (err && err.message) || "no details",
      pendingUpdate,
      {
        label: "daemon",
        exitCode: daemonProc ? daemonProc.exitCode : null,
        gateError: (err && err.message) || "",
        port: DAEMON_PORT,
        timeoutS: Math.round(STARTUP_TIMEOUT_MS / 1000),
      }
    );
    return;
  }
  try {
    // Already probed ABOVE, concurrently with the daemon (v1.250.0, S-01) —
    // re-awaiting here would reinstate the serial wait this change removes.
    if (dashboardGate.status === "rejected") throw dashboardGate.reason;
  } catch (err) {
    const integrityResult = verifyInstallIntegrity();
    if (!integrityResult.ok) {
      handleCorruptInstall(integrityResult);
      return;
    }
    handleStartupFailure(
      "Iron Jarvis — dashboard did not start",
      (err && err.message) ||
        (IS_PACKAGED ? "no details" : "the dashboard may not have been built yet (pnpm build)"),
      pendingUpdate,
      {
        label: "dashboard",
        exitCode: dashboardProc ? dashboardProc.exitCode : null,
        gateError: (err && err.message) || "",
        port: DASHBOARD_PORT,
        timeoutS: Math.round(STARTUP_TIMEOUT_MS / 1000),
      }
    );
    return;
  }

  clearUpdatePending(); // a clean, healthy boot means the current version is good
  bootComplete = true;
  // showWindowWhenReady: a second launch arrived DURING this boot with no
  // window and no splash to focus (the --hidden case) — that launch was the
  // user asking for the app, so open it now rather than staying invisible.
  if (START_HIDDEN && !showWindowWhenReady) {
    // Login boot: stay in the tray — the window is created on demand (tray
    // click / hotkey / second launch). Close the splash if one exists.
    if (loadingWin && !loadingWin.isDestroyed()) loadingWin.close();
    loadingWin = null;
  } else {
    createMainWindow();
  }
  showWindowWhenReady = false;
  installDaemonWatchdog(); // v1.226.0: a daemon that is up but not answering gets restarted
  installAskWatcher(); // v1.249.0 (R-03): waiting jobs reach a closed window
  maybeOfferStartWithWindows(); // v1.249.0 (R-04): asked once
  checkForUpdates();
  // Long-lived tray apps must keep looking for updates, not just at boot.
  setInterval(checkForUpdates, UPDATE_RECHECK_MS);
}

// The last lines a child wrote, for the failure classifier (v1.249.0, R-06).
// Best-effort: no log, no tail, and the classifier simply has less to go on.
function readLogTail(label, maxBytes = 16 * 1024) {
  try {
    const file = path.join(userDataDir || "", "logs", `${label}.log`);
    const stat = fs.statSync(file);
    const length = Math.min(stat.size, maxBytes);
    const fd = fs.openSync(file, "r");
    try {
      const buf = Buffer.alloc(length);
      fs.readSync(fd, buf, 0, length, Math.max(0, stat.size - length));
      return buf.toString("utf8");
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return "";
  }
}

// WHY it did not start, in words the user can act on (v1.249.0, R-06). Every
// failure used to read "another program is already using port 8787" — including
// a locked database and a failed data upgrade — and then the app quit. Pure:
// the dialog and its test read the same sentences.
function classifyStartupFailure(context) {
  const c = context || {};
  const label = c.label === "dashboard" ? "dashboard" : "daemon";
  const service = label === "dashboard" ? "The Iron Jarvis dashboard" : "The Iron Jarvis service";
  const port = c.port || DAEMON_PORT;
  const seconds = c.timeoutS || Math.round(STARTUP_TIMEOUT_MS / 1000);
  const hay = `${String(c.logTail || "")}\n${String(c.gateError || "")}`;
  const has = (re) => re.test(hay);
  // 10048 is the Windows socket code; uvicorn prints it as "[Errno 10048]"
  // there and as EADDRINUSE elsewhere, so match the NUMBER, not one wording.
  if (has(/in use by another program|EADDRINUSE|address already in use|10048|only one usage of each socket address/i)) {
    return {
      cause: "port",
      message: `Another program is using the connection ${service.toLowerCase()} needs.`,
      detail:
        `Port ${port} is taken — often a second copy of Iron Jarvis, or a development server. ` +
        "Close it and press Retry.",
    };
  }
  if (has(/database is locked|database table is locked/i)) {
    return {
      cause: "db-locked",
      message: "Iron Jarvis's database is locked by another program.",
      detail:
        "Usually a second copy of Iron Jarvis, or a backup/sync tool (OneDrive, antivirus) " +
        "holding the file. Close it and press Retry. Your data is untouched.",
    };
  }
  if (has(/no such column|no such table|alembic|migration failed|OperationalError/i)) {
    return {
      cause: "upgrade",
      message: "The step that updates your saved data for this version did not finish.",
      detail:
        "Nothing has been deleted. Press Retry; if it fails again, use Open logs and reinstall " +
        "the previous version from the Releases page.",
    };
  }
  if (has(/ENOENT|EACCES|EPERM|not recognized as an internal|cannot find the (file|path)/i)) {
    return {
      cause: "missing",
      message: `${service} could not be started.`,
      detail:
        "Its program file is missing or was blocked — antivirus software sometimes quarantines " +
        "it. Press Retry; if it fails again, reinstall Iron Jarvis.",
    };
  }
  if (has(/Traceback \(most recent call last\)/) || (typeof c.exitCode === "number" && c.exitCode !== 0)) {
    return {
      cause: "crash",
      message: `${service} stopped with an error while starting.`,
      detail: "Press Retry. If it happens again, Open logs shows what went wrong.",
    };
  }
  return {
    cause: "timeout",
    message: `${service} did not answer within ${seconds} seconds.`,
    detail: "Your PC may still be busy starting up. Press Retry.",
  };
}

// Shared startup-failure path. After a just-applied update that repeatedly fails
// to boot, offer a concrete recovery (reinstall the previous release) instead of
// looping on a generic error — electron-updater/NSIS keep no prior version.
// Otherwise (v1.249.0, R-06): name the likely cause and offer Retry / Open logs
// / Quit, instead of an error box that only ever quit.
function handleStartupFailure(title, message, pendingUpdate, context) {
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
    const info = classifyStartupFailure({
      label: (context && context.label) || "daemon",
      exitCode: context && context.exitCode,
      logTail: readLogTail((context && context.label) || "daemon"),
      gateError: (context && context.gateError) || message,
      port: context && context.port,
      timeoutS: context && context.timeoutS,
    });
    for (;;) {
      let choice = 2;
      try {
        choice = dialog.showMessageBoxSync({
          type: "error",
          buttons: ["Retry", "Open logs", "Quit"],
          defaultId: 0,
          cancelId: 2,
          noLink: true,
          title,
          message: info.message,
          detail: `${info.detail}\n\nDetails: ${(context && context.gateError) || message}`,
        });
      } catch {
        choice = 2; // no dialog (no display?) — fall through to the quit below
      }
      if (choice === 1) {
        openLogsFolder(); // and ask again, so Retry stays one click away
        continue;
      }
      if (choice === 0) {
        isQuitting = true;
        shutdown();
        app.relaunch();
        app.exit(0);
        return;
      }
      break;
    }
  }
  isQuitting = true;
  shutdown();
  app.quit();
}

// --- Global hotkey -------------------------------------------------------

/** "CommandOrControl+Alt+J" -> "Ctrl+Alt+J" (Cmd on macOS): the label a user presses. */
function accelLabel(accel) {
  if (!accel) return null;
  return String(accel).replace("CommandOrControl", process.platform === "darwin" ? "Cmd" : "Ctrl");
}

/** What the dashboard is told (`shell:getState`): labels, never the internal accelerator strings. */
function shellState() {
  return {
    platform: process.platform,
    hotkeys: {
      window: accelLabel(hotkeyState.window),
      spotlight: accelLabel(hotkeyState.spotlight),
    },
    // The preferred keys, so a page can say WHICH one was taken.
    preferred: { window: accelLabel(HOTKEY), spotlight: accelLabel(SPOTLIGHT_HOTKEY) },
  };
}

/** Try each accelerator in order; return the first one that registered, else null. */
function registerLadder(ladder, handler) {
  for (const accel of ladder) {
    try {
      if (globalShortcut.register(accel, handler)) return accel;
      desktopLog("warn", `[hotkey] ${accel} registration failed (taken by another app?)`);
    } catch (err) {
      desktopLog("error", `[hotkey] ${accel} registration error:`, err && err.message);
    }
  }
  return null;
}

let _hotkeyNotified = false;
function registerHotkey() {
  const before = { window: hotkeyState.window, spotlight: hotkeyState.spotlight };
  // The Spotlight quick-task overlay — best-effort; a taken combo just no-ops
  // (the tray "Quick task…" item + the in-app UI still work).
  if (!hotkeyState.spotlight) {
    hotkeyState.spotlight = registerLadder(SPOTLIGHT_LADDER, () => toggleSpotlight());
  }
  if (!hotkeyState.window) {
    hotkeyState.window = registerLadder(HOTKEY_LADDER, () => showMainWindow());
    if (hotkeyState.window && hotkeyState.window !== HOTKEY) {
      desktopLog("warn", `[hotkey] ${HOTKEY} is taken — using ${hotkeyState.window} instead`);
    }
    if (!hotkeyState.window && !_hotkeyNotified) {
      _hotkeyNotified = true; // once per process, not once per 30-min retry
      desktopLog("warn", `[hotkey] no window hotkey could be registered (${HOTKEY_LADDER.join(", ")} all taken)`);
      // Tell the user instead of failing silently — the hotkey is a primary way
      // back to a window that closes to the tray.
      try {
        new Notification({
          title: "Iron Jarvis",
          body: `The global hotkeys ${HOTKEY_LADDER.map(accelLabel).join(" and ")} are taken by other apps — use the tray icon to open Iron Jarvis.`,
        }).show();
      } catch {
        /* notifications unavailable */
      }
    }
  }
  if (before.window !== hotkeyState.window || before.spotlight !== hotkeyState.spotlight) {
    desktopLog("warn", `[hotkey] window=${hotkeyState.window || "none"} spotlight=${hotkeyState.spotlight || "none"}`);
    try {
      refreshMenus(); // the tray label + app-menu accelerator read hotkeyState
    } catch {
      /* menus not built yet */
    }
  }
  return hotkeyState;
}

// A key another app held at boot may be free later (that app quit, or the
// user changed its binding): retry every 30 min and whenever a window of ours
// takes focus, but only while a rung is still null — never re-register a key
// we already hold.
let _hotkeyRetryTimer = null;
function installHotkeyRetry() {
  if (_hotkeyRetryTimer) return;
  const retry = () => {
    if (!hotkeyState.window || !hotkeyState.spotlight) registerHotkey();
  };
  _hotkeyRetryTimer = setInterval(retry, HOTKEY_RETRY_MS);
  if (_hotkeyRetryTimer && typeof _hotkeyRetryTimer.unref === "function") _hotkeyRetryTimer.unref();
  app.on("browser-window-focus", retry);
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
    } else {
      // Still booting with NOTHING to focus. This is the --hidden login boot:
      // startup() skips the splash and its START_HIDDEN branch deliberately
      // creates no window, so the in-flight startup would NOT open one and the
      // launch used to vanish. Record the intent; startup() honours it once the
      // health gate passes.
      showWindowWhenReady = true;
    }
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
    // 5s, not 2s (v1.226.0, F-E-5): uvicorn drains open streams before the
    // lifespan runs, so a Quit with a chat/session stream open never finished
    // in 2s and the terminal snapshot + browser close were routinely skipped.
    requestDaemonShutdown(5000).finally(() => {
      shutdown(); // force-kills whatever is still alive (incl. the dashboard)
      // A stale or adopted daemon that ignored /shutdown must not outlive Quit
      // (v1.226.0, F-E-2) — but only OURS is swept (contract C2 as amended);
      // a blind sweep killed every dev daemon on the machine.
      sweepOwnDaemonOnQuit().finally(() => {
        // autoInstallOnAppQuit runs NSIS after this quit — so this IS an update
        // install, exactly like the explicit-update path, and it gets the same
        // two preparations: the failed-update recovery marker (without it the
        // whole readAndBumpUpdatePending -> handleStartupFailure recovery is
        // bypassed for every quit-installed update) and the orphan sweep that
        // frees resources/daemon's file locks before the installer extracts.
        if (pendingUpdateInfo) {
          markUpdatePending(pendingUpdateInfo.version);
          sweepOrphanDaemons();
        }
        app.quit(); // re-enters before-quit; falls through this time
      });
    });
  });

  app.on("will-quit", () => {
    globalShortcut.unregisterAll();
    if (_dwTimer) clearInterval(_dwTimer);
    _dwTimer = null;
    if (_hotkeyRetryTimer) clearInterval(_hotkeyRetryTimer);
    _hotkeyRetryTimer = null;
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
