/**
 * v1.232.0 (audit Wave 6, task 6A) — the Build pane: Clear scrollback (U13)
 * and the PTY resize gate.
 *
 * A terminal session has ONE PTY and any number of attached clients; every
 * attach used to send `resize` on open and on every ResizeObserver tick, and
 * the daemon keeps last-writer semantics — so a phone attaching over
 * Tailscale reflowed the desktop's running session. `resizeGate.resizeAllowed`
 * is the pure rule (visible pane AND focused document) and `sendResize`
 * consults it. U13: a visible header action clears the on-screen history and
 * repaints (client-side; the shell keeps running).
 *
 * xterm cannot mount under jsdom, so the pane's wiring is pinned at the
 * source (the convention of terminal-reconnect-v1226 / pane-state-v1217).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { holderVisible, resizeAllowed } from "@/components/terminal/resizeGate";

function holder(visible = true): HTMLElement {
  const el = document.createElement("div");
  (el as HTMLElement & { checkVisibility?: () => boolean }).checkVisibility = () => visible;
  return el;
}

afterEach(() => vi.restoreAllMocks());

describe("resizeAllowed (v1.232.0)", () => {
  it("a visible pane in the focused window may resize", () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    expect(resizeAllowed(holder(true))).toBe(true);
  });

  it("a hidden pane (rail box under visibility:hidden) never resizes", () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    expect(resizeAllowed(holder(false))).toBe(false);
  });

  it("a visible pane in an UNFOCUSED document (phone on the desk, background tab) never resizes", () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(false);
    expect(resizeAllowed(holder(true))).toBe(false);
  });

  it("no holder means no resize; a document without hasFocus is not gated", () => {
    expect(resizeAllowed(null)).toBe(false);
    const doc = {} as unknown as Document;
    expect(resizeAllowed(holder(true), doc)).toBe(true);
    expect(resizeAllowed(holder(false), doc)).toBe(false);
  });

  it("holderVisible defaults to visible where checkVisibility is absent", () => {
    expect(holderVisible(document.createElement("div"))).toBe(true);
  });
});

describe("TerminalPane wiring (v1.232.0 source pins)", () => {
  const src = readFileSync(
    join(process.cwd(), "components", "terminal", "TerminalPane.tsx"),
    "utf8",
  );

  it("sendResize consults the gate BEFORE touching the socket", () => {
    expect(src).toContain('import { resizeAllowed } from "@/components/terminal/resizeGate";');
    // v1.245.0: sendResize takes `force` (open + window focus claim the size
    // even when unchanged); the gate still comes first.
    const send = src.indexOf("const sendResize = (force = false) => {");
    const gate = src.indexOf("if (!resizeAllowed(holder)) return;", send);
    const wire = src.indexOf('JSON.stringify({ type: "resize"', send);
    expect(send).toBeGreaterThan(-1);
    expect(gate).toBeGreaterThan(send);
    expect(wire).toBeGreaterThan(gate);
  });

  it("gaining window focus claims the size (the attach that becomes primary)", () => {
    expect(src).toContain('window.addEventListener("focus", onWinFocus);');
    expect(src).toContain('window.removeEventListener("focus", onWinFocus);');
  });

  it("the header has a visible Clear scrollback action that clears and repaints client-side", () => {
    expect(src).toContain('aria-label="Clear scrollback"');
    expect(src).toMatch(/clearRef\.current = \(\) => \{\s*try \{\s*live\.clear\(\);\s*live\.refresh\(0, Math\.max\(0, live\.rows - 1\)\);/);
    const btn = src.indexOf('aria-label="Clear scrollback"');
    const click = src.lastIndexOf("clearRef.current?.();", btn);
    expect(click).toBeGreaterThan(-1);
    expect(btn - click).toBeLessThan(400);
    // Nothing goes over the wire for a clear: no socket send near the handler.
    expect(src.slice(click, btn)).not.toContain("ws.send");
  });
});
