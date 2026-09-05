/**
 * v1.229.0 (audit Wave 3, D2) — the tips card shows the hotkey that is REALLY
 * registered.
 *
 * desktop/main.js tries Ctrl+Shift+J, then Ctrl+Alt+J (another app held the
 * first on the user's own machine), and reports what it holds through the
 * preload bridge as `window.ironjarvis.shell.getState()`. PowerTips used to
 * hard-code Ctrl+Shift+J. Pinned:
 *  - getState says window=Ctrl+Alt+J -> the row shows Ctrl / Alt / J, no Shift+J.
 *  - getState says window=null -> a sentence naming the taken key and the tray,
 *    not a keycap; the Spotlight row still shows its key.
 *  - an older bridge with no `shell` -> the defaults (the v1.198.0 rows), so a
 *    stub `{}` still renders Ctrl+Shift+J.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { PowerTips } from "@/components/PowerTips";

const DISMISS_KEY = "ij_power_tips_dismissed";
const win = window as unknown as Record<string, unknown>;

function bridge(state: unknown) {
  win.ironjarvis = { isDesktop: true, shell: { getState: async () => state } };
}

beforeEach(() => {
  localStorage.removeItem(DISMISS_KEY);
  delete win.ironjarvis;
});
afterEach(() => {
  cleanup();
  delete win.ironjarvis;
});

describe("PowerTips reads the live hotkey (v1.229.0)", () => {
  it("renders the fallback key when Ctrl+Shift+J was taken and Ctrl+Alt+J registered", async () => {
    bridge({
      platform: "win32",
      hotkeys: { window: "Ctrl+Alt+J", spotlight: "Ctrl+Shift+Space" },
      preferred: { window: "Ctrl+Shift+J", spotlight: "Ctrl+Shift+Space" },
    });
    render(<PowerTips />);
    await screen.findByText("Four shortcuts worth learning");
    expect(screen.getByText("Alt")).not.toBeNull();
    expect(screen.getByText("J")).not.toBeNull();
    expect(screen.getByText(/Reopen the Iron Jarvis window/)).not.toBeNull();
    // Only the Spotlight row carries Shift now — the window row must not.
    expect(screen.getAllByText("Shift")).toHaveLength(1);
    expect(screen.getByText("Space")).not.toBeNull();
    expect(screen.queryByText(/hotkey unavailable/)).toBeNull();
  });

  it("says the hotkey is unavailable, naming the taken key, when every rung was taken", async () => {
    bridge({
      platform: "win32",
      hotkeys: { window: null, spotlight: "Ctrl+Shift+Space" },
      preferred: { window: "Ctrl+Shift+J", spotlight: "Ctrl+Shift+Space" },
    });
    render(<PowerTips />);
    await screen.findByText("Four shortcuts worth learning");
    const row = screen.getByText(/hotkey unavailable/);
    expect(row.textContent).toContain("Ctrl+Shift+J is taken by another app");
    expect(row.textContent).toContain("tray");
    // No keycap claims a window key: the only "J" keycap would be a lie.
    expect(screen.queryByText("J")).toBeNull();
    expect(screen.queryByText("Alt")).toBeNull();
    expect(screen.queryByText(/Reopen the Iron Jarvis window from anywhere/)).toBeNull();
    // Spotlight still has its key.
    expect(screen.getByText("Space")).not.toBeNull();
    expect(screen.getByText(/Spotlight — quick-ask/)).not.toBeNull();
  });

  it("older bridge without shell.getState keeps the default rows", async () => {
    win.ironjarvis = {};
    render(<PowerTips />);
    await screen.findByText("Four shortcuts worth learning");
    expect(screen.getAllByText("Shift")).toHaveLength(2);
    expect(screen.getByText("J")).not.toBeNull();
    expect(screen.queryByText(/hotkey unavailable/)).toBeNull();
  });

  it("a refused sender (getState resolves null) falls back to the defaults, never to nothing", async () => {
    bridge(null);
    render(<PowerTips />);
    await screen.findByText("Four shortcuts worth learning");
    expect(screen.getByText("J")).not.toBeNull();
  });
});
