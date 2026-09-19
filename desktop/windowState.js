// Iron Jarvis — desktop window-state persistence (CommonJS).
//
// Small, dependency-free helpers (fs + path only, NO electron) so the desktop
// window remembers its size/position across launches. The caller (main.js)
// supplies the userData directory and the list of connected displays (from the
// electron `screen` module, which is only valid after app-ready); keeping the
// `screen` access out of this module lets it be required at top level and
// `node --check`ed in isolation.

const fs = require("fs");
const path = require("path");

// Sensible default for a first launch (or when the saved state is unusable):
// the same 1440x900 the app shipped with, centered (no x/y => caller centers).
const DEFAULT_BOUNDS = { width: 1440, height: 900 };

// Hard floors so a corrupt/teeny saved size can never produce an unusable window.
const MIN_WIDTH = 800;
const MIN_HEIGHT = 560;

function stateFile(userDataDir) {
  return path.join(userDataDir, "window-state.json");
}

// Read the persisted bounds, or null when missing/corrupt/unreasonable.
// Returns { width, height, x?, y? } — x/y are only included when both are finite.
function loadBounds(userDataDir) {
  let data;
  try {
    data = JSON.parse(fs.readFileSync(stateFile(userDataDir), "utf8"));
  } catch {
    return null; // not created yet / unreadable / invalid JSON
  }
  const b = data && data.bounds;
  if (
    !b ||
    !Number.isFinite(b.width) ||
    !Number.isFinite(b.height) ||
    b.width < MIN_WIDTH ||
    b.height < MIN_HEIGHT
  ) {
    return null;
  }
  const out = { width: Math.round(b.width), height: Math.round(b.height) };
  if (Number.isFinite(b.x) && Number.isFinite(b.y)) {
    out.x = Math.round(b.x);
    out.y = Math.round(b.y);
  }
  return out;
}

// Persist the bounds (best-effort; failures are swallowed so a read-only
// userData dir can never crash the app).
function saveBounds(userDataDir, bounds) {
  if (!bounds || !Number.isFinite(bounds.width) || !Number.isFinite(bounds.height)) {
    return;
  }
  const payload = {
    bounds: {
      width: Math.round(bounds.width),
      height: Math.round(bounds.height),
      x: Number.isFinite(bounds.x) ? Math.round(bounds.x) : undefined,
      y: Number.isFinite(bounds.y) ? Math.round(bounds.y) : undefined,
    },
    savedAt: new Date().toISOString(),
  };
  try {
    fs.writeFileSync(stateFile(userDataDir), JSON.stringify(payload, null, 2), "utf8");
  } catch {
    /* non-fatal */
  }
}

// Is the rectangle visible enough on SOME connected display that the user can
// grab its title bar? Guards against restoring a window onto a monitor that has
// since been unplugged (saved x/y now far off-screen). `displays` is the array
// from electron's screen.getAllDisplays(). When bounds has no x/y we return
// false so the caller centers it on the primary display.
function isVisibleOnDisplay(bounds, displays) {
  if (!bounds || !Number.isFinite(bounds.x) || !Number.isFinite(bounds.y)) {
    return false;
  }
  if (!Array.isArray(displays) || displays.length === 0) {
    return false;
  }
  return displays.some((d) => {
    const wa = (d && d.workArea) || {};
    if (
      !Number.isFinite(wa.x) ||
      !Number.isFinite(wa.y) ||
      !Number.isFinite(wa.width) ||
      !Number.isFinite(wa.height)
    ) {
      return false;
    }
    const overlapX =
      Math.min(bounds.x + bounds.width, wa.x + wa.width) - Math.max(bounds.x, wa.x);
    const overlapY =
      Math.min(bounds.y + bounds.height, wa.y + wa.height) - Math.max(bounds.y, wa.y);
    // Require a grabbable strip of the window to be on-screen.
    return overlapX > 120 && overlapY > 48;
  });
}

// --- Pop-out windows (v1.283.0) ---------------------------------------------
// A module popped out into its own window remembers its rectangle PER ROUTE
// (`/chat`, `/terminals`, …) in popout-windows.json, and is placed by
// `popoutPlacement`: the saved rectangle when it is still on a connected
// display; otherwise ON ANOTHER SCREEN than the main window when the desk has
// one (that is the whole point of popping out), centred there; on a one-screen
// desk, cascaded off the main window. Pure over its arguments — no electron —
// so the rule is testable under node exactly as it ships.
const POPOUT_DEFAULT_BOUNDS = { width: 1200, height: 820 };
const POPOUT_MIN_WIDTH = 640;
const POPOUT_MIN_HEIGHT = 480;
// Margin kept clear inside a display's work area, and the cascade step when a
// pop-out lands on the same screen as the main window.
const POPOUT_MARGIN = 40;
const POPOUT_CASCADE = 48;

function popoutStateFile(userDataDir) {
  return path.join(userDataDir, "popout-windows.json");
}

function readPopoutState(userDataDir) {
  try {
    const data = JSON.parse(fs.readFileSync(popoutStateFile(userDataDir), "utf8"));
    return data && typeof data.windows === "object" && data.windows ? data.windows : {};
  } catch {
    return {};
  }
}

// The saved rectangle for `key`, or null when missing/corrupt/unreasonable.
function loadPopoutBounds(userDataDir, key) {
  const b = readPopoutState(userDataDir)[String(key || "")];
  if (
    !b ||
    !Number.isFinite(b.width) ||
    !Number.isFinite(b.height) ||
    b.width < POPOUT_MIN_WIDTH ||
    b.height < POPOUT_MIN_HEIGHT
  ) {
    return null;
  }
  const out = { width: Math.round(b.width), height: Math.round(b.height) };
  if (Number.isFinite(b.x) && Number.isFinite(b.y)) {
    out.x = Math.round(b.x);
    out.y = Math.round(b.y);
  }
  return out;
}

// Persist one pop-out's rectangle under its key (best-effort; other keys kept).
function savePopoutBounds(userDataDir, key, bounds) {
  if (!key || !bounds || !Number.isFinite(bounds.width) || !Number.isFinite(bounds.height)) {
    return;
  }
  const windows = readPopoutState(userDataDir);
  windows[String(key)] = {
    width: Math.round(bounds.width),
    height: Math.round(bounds.height),
    x: Number.isFinite(bounds.x) ? Math.round(bounds.x) : undefined,
    y: Number.isFinite(bounds.y) ? Math.round(bounds.y) : undefined,
    savedAt: new Date().toISOString(),
  };
  try {
    fs.writeFileSync(
      popoutStateFile(userDataDir),
      JSON.stringify({ windows }, null, 2),
      "utf8",
    );
  } catch {
    /* non-fatal */
  }
}

function usableDisplays(displays) {
  if (!Array.isArray(displays)) return [];
  return displays.filter((d) => {
    const wa = d && d.workArea;
    return (
      wa &&
      Number.isFinite(wa.x) &&
      Number.isFinite(wa.y) &&
      Number.isFinite(wa.width) &&
      Number.isFinite(wa.height) &&
      wa.width > 0 &&
      wa.height > 0
    );
  });
}

function contains(wa, p) {
  return (
    !!p &&
    p.x >= wa.x &&
    p.x < wa.x + wa.width &&
    p.y >= wa.y &&
    p.y < wa.y + wa.height
  );
}

// Where a pop-out opens. `saved` is loadPopoutBounds' answer (or null),
// `displays` the electron screen.getAllDisplays() array, `mainBounds` the main
// window's rectangle (or null when it is hidden to the tray), `offset` the
// number of pop-outs already open (each later one steps down and right so
// two never sit exactly on top of each other). Returns {x, y, width, height,
// secondary} — `secondary` says the window went to a screen other than the
// main window's. With no usable display the size alone is returned and the
// caller centres.
function popoutPlacement(saved, displays, mainBounds, offset) {
  const list = usableDisplays(displays);
  if (saved && isVisibleOnDisplay(saved, list)) {
    return { x: saved.x, y: saved.y, width: saved.width, height: saved.height, secondary: false };
  }
  const size = saved
    ? { width: saved.width, height: saved.height }
    : { ...POPOUT_DEFAULT_BOUNDS };
  if (list.length === 0) return { ...size, secondary: false };
  const centre = mainBounds
    ? { x: mainBounds.x + mainBounds.width / 2, y: mainBounds.y + mainBounds.height / 2 }
    : null;
  const mainDisplay = centre ? list.find((d) => contains(d.workArea, centre)) || null : null;
  const other = mainDisplay ? list.find((d) => d !== mainDisplay) || null : null;
  const target = other || mainDisplay || list[0];
  const wa = target.workArea;
  const width = Math.max(POPOUT_MIN_WIDTH, Math.min(size.width, wa.width - POPOUT_MARGIN));
  const height = Math.max(POPOUT_MIN_HEIGHT, Math.min(size.height, wa.height - POPOUT_MARGIN));
  const step = Math.max(0, Number(offset) || 0) * POPOUT_CASCADE;
  let x;
  let y;
  if (other || !mainBounds) {
    x = wa.x + Math.round((wa.width - width) / 2) + step;
    y = wa.y + Math.round((wa.height - height) / 2) + step;
  } else {
    x = mainBounds.x + POPOUT_CASCADE + step;
    y = mainBounds.y + POPOUT_CASCADE + step;
  }
  x = Math.max(wa.x, Math.min(x, wa.x + wa.width - width));
  y = Math.max(wa.y, Math.min(y, wa.y + wa.height - height));
  return { x, y, width, height, secondary: !!other };
}

module.exports = {
  DEFAULT_BOUNDS,
  MIN_WIDTH,
  MIN_HEIGHT,
  loadBounds,
  saveBounds,
  isVisibleOnDisplay,
  POPOUT_DEFAULT_BOUNDS,
  POPOUT_MIN_WIDTH,
  POPOUT_MIN_HEIGHT,
  loadPopoutBounds,
  savePopoutBounds,
  popoutPlacement,
};
