/**
 * v1.226.0 (F-D-4) — the terminal pane no longer dead-ends on "Session
 * closed" ~5s into a daemon restart.
 *
 * The schedule is a pure function (paneStatusCore.terminalReconnectDelayMs):
 *  - four quick retries (0.5/1/1.5/2s) whatever /health says — a blip;
 *  - then, while the daemon is OFFLINE per useDaemon(), keep retrying with a
 *    backoff capped at 10s (the restart rehydrates the same terminal id);
 *  - a REACHABLE daemon that still refuses the attach stops the schedule
 *    (null) — that is the "Connection lost" overlay with a Reconnect button.
 * Close code 4000 (the shell exited) never enters the schedule; the pane
 * source is pinned for that and for the Reconnect lever.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  TERM_QUICK_RETRIES,
  TERM_RECONNECT_MAX_MS,
  terminalReconnectDelayMs,
} from "@/components/terminal/paneStatusCore";

describe("terminalReconnectDelayMs (v1.226.0)", () => {
  it("four quick retries regardless of daemon reachability", () => {
    for (const online of [true, false]) {
      expect(terminalReconnectDelayMs(0, online)).toBe(500);
      expect(terminalReconnectDelayMs(1, online)).toBe(1000);
      expect(terminalReconnectDelayMs(2, online)).toBe(1500);
      expect(terminalReconnectDelayMs(3, online)).toBe(2000);
    }
    expect(TERM_QUICK_RETRIES).toBe(4);
  });

  it("keeps retrying with capped backoff while the daemon is offline", () => {
    expect(terminalReconnectDelayMs(4, false)).toBe(2500);
    expect(terminalReconnectDelayMs(10, false)).toBe(5500);
    expect(terminalReconnectDelayMs(19, false)).toBe(TERM_RECONNECT_MAX_MS);
    expect(terminalReconnectDelayMs(500, false)).toBe(TERM_RECONNECT_MAX_MS);
    expect(TERM_RECONNECT_MAX_MS).toBe(10_000);
  });

  it("stops (null) only when the daemon is reachable and the quick retries are spent", () => {
    expect(terminalReconnectDelayMs(4, true)).toBeNull();
    expect(terminalReconnectDelayMs(40, true)).toBeNull();
  });
});

describe("TerminalPane wiring (v1.226.0 source pins; the socket is the host's since v1.243.0)", () => {
  // Normalised at the reader: CI checks files out with CRLF (v1.232.1).
  const read = (...p: string[]) =>
    readFileSync(join(process.cwd(), ...p), "utf8").replace(/\r\n/g, "\n");
  const src = read("components", "terminal", "TerminalPane.tsx");
  // v1.243.0: the socket and its reconnect schedule live in the PaneHost,
  // which outlives the pane; the mounted pane hands its /health verdict over.
  const host = read("components", "terminal", "paneHost.ts");

  it("the close handler consults the schedule with the live daemon reachability", () => {
    expect(host).toContain("terminalReconnectDelayMs(this.attempts, this.daemonOnline)");
    // 4000 (shell exited) is still the one permanent stop, ahead of the schedule.
    const exitStop = host.indexOf("if (ev.code === 4000) {");
    const schedule = host.indexOf("terminalReconnectDelayMs(this.attempts,");
    expect(exitStop).toBeGreaterThan(-1);
    expect(schedule).toBeGreaterThan(exitStop);
    // …and the reachability it reads is the pane's live one.
    expect(src).toContain("h.daemonOnline = daemonOnlineRef.current;");
    expect(src).toContain("hostRef.current.daemonOnline = daemonOnline;");
  });

  it("a lost link renders a Reconnect button that re-runs connect; an exited shell does not", () => {
    expect(src).toContain('{lostLink ? "Connection lost" : "Session closed"}');
    expect(src).toContain("onClick={() => reconnectRef.current?.()}");
    expect(src).toContain("reconnectRef.current = () => h.reconnectNow();");
    // The overlay is pointer-events-none; the button must opt back in.
    expect(src).toMatch(/pointer-events-auto[^>]*>\s*Reconnect/);
    // The exited-shell path clears the lost flag so no lever is offered.
    expect(host).toMatch(
      /ev\.code === 4000\) \{\s*this\.exited = true;\s*this\.setConn\("closed", false\);/,
    );
  });
});
