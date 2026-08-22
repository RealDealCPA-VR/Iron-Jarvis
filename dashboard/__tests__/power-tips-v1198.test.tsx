/**
 * v1.198.0 — the Overview "power tips" card: dismissible, honest about
 * environment.
 *
 * The pins that matter:
 *  - In a plain BROWSER session (no `window.ironjarvis` — the Electron
 *    preload bridge is absent) only the two universal tips render: Ctrl+K
 *    (CommandPalette) and the "/" chat skill picker. Ctrl+Shift+J and
 *    Ctrl+Shift+Space are GLOBAL hotkeys registered by desktop/main.js, so
 *    showing them in a browser would advertise keys that do nothing.
 *  - With `window.ironjarvis` present (desktop app), the two global-hotkey
 *    rows appear.
 *  - Dismiss writes `ij_power_tips_dismissed=1` and removes the card; with
 *    that key pre-set the component renders NOTHING (no re-open affordance
 *    by design — Help carries this info; a resurrectable nag is worse).
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { PowerTips } from "@/components/PowerTips";

const DISMISS_KEY = "ij_power_tips_dismissed";

beforeEach(() => {
  // Clean slate: not dismissed, and NOT the desktop app (jsdom has no
  // Electron preload, but a previous test may have stubbed the bridge).
  localStorage.removeItem(DISMISS_KEY);
  delete (window as unknown as Record<string, unknown>).ironjarvis;
});

afterEach(() => {
  cleanup();
  delete (window as unknown as Record<string, unknown>).ironjarvis;
});

describe("PowerTips (v1.198.0)", () => {
  it("browser env: shows Ctrl+K and '/' tips, and NO desktop-only hotkeys", async () => {
    render(<PowerTips />);

    // The count word doubles as the gating assertion: two tips, not four.
    await screen.findByText("Two shortcuts worth learning");

    // Ctrl+K row (keycaps are separate <kbd> elements).
    expect(screen.getByText("Ctrl")).not.toBeNull();
    expect(screen.getByText("K")).not.toBeNull();
    expect(
      screen.getByText(/Search everything — pages, skills, chats/),
    ).not.toBeNull();

    // "/" row.
    expect(screen.getByText("/")).not.toBeNull();
    expect(screen.getByText(/invoke a skill/)).not.toBeNull();

    // Desktop-only hotkeys must be absent: no "Shift" keycap exists in any
    // universal tip, so a single query covers both gated rows.
    expect(screen.queryByText("Shift")).toBeNull();
    expect(screen.queryByText(/Spotlight/)).toBeNull();
    expect(screen.queryByText(/Reopen the Iron Jarvis window/)).toBeNull();
  });

  it("desktop env (window.ironjarvis present): the two global-hotkey rows appear", async () => {
    (window as unknown as Record<string, unknown>).ironjarvis = {};
    render(<PowerTips />);

    await screen.findByText("Four shortcuts worth learning");

    // Ctrl+Shift+J — reopen the window.
    expect(screen.getByText("J")).not.toBeNull();
    expect(screen.getByText(/Reopen the Iron Jarvis window/)).not.toBeNull();

    // Ctrl+Shift+Space — Spotlight quick-ask.
    expect(screen.getByText("Space")).not.toBeNull();
    expect(screen.getByText(/Spotlight/)).not.toBeNull();

    // Two "Shift" keycaps, one per global-hotkey row.
    expect(screen.getAllByText("Shift")).toHaveLength(2);

    // Universal tips still present alongside.
    expect(screen.getByText("K")).not.toBeNull();
    expect(screen.getByText("/")).not.toBeNull();
  });

  it("dismiss stores the key and removes the card", async () => {
    render(<PowerTips />);
    await screen.findByText("Two shortcuts worth learning");

    fireEvent.click(screen.getByLabelText("Dismiss"));

    // Assert the THING itself inside waitFor (CLAUDE.md rule), not a proxy.
    await waitFor(() => {
      expect(screen.queryByText(/shortcuts worth learning/)).toBeNull();
    });
    expect(localStorage.getItem(DISMISS_KEY)).toBe("1");
  });

  it("with the key pre-set, nothing renders (no re-open affordance)", async () => {
    localStorage.setItem(DISMISS_KEY, "1");
    const { container } = render(<PowerTips />);

    // The card must never appear — neither before the storage read (the
    // null-until-read guard) nor after it resolves to dismissed. Flush the
    // effect by waiting for a stable empty DOM.
    await waitFor(() => {
      expect(container.firstChild).toBeNull();
    });
    expect(screen.queryByText(/shortcuts worth learning/)).toBeNull();
    expect(screen.queryByText("Ctrl")).toBeNull();
  });
});
