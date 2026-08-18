import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Badge, StatusDot, Empty, LoaderInline } from "@/components/ui";

/**
 * Smoke render of a few shared ui.tsx primitives — proves they mount in jsdom
 * and encode their core contract (Badge shows its value; a live status gets the
 * pulse class). Cheap regression net for the most-reused presentational bits.
 */
describe("ui.tsx primitives", () => {
  it("Badge renders its value text", () => {
    render(<Badge value="completed" />);
    expect(screen.getByText("completed")).toBeInTheDocument();
  });

  it("StatusDot pulses for in-flight (running) states", () => {
    const { container } = render(<StatusDot status="running" />);
    const dot = container.querySelector("span");
    expect(dot).toBeTruthy();
    expect(dot?.className).toContain("animate-pulse-glow");
  });

  it("StatusDot does NOT pulse for a terminal (completed) state", () => {
    const { container } = render(<StatusDot status="completed" />);
    expect(container.querySelector("span")?.className).not.toContain("animate-pulse-glow");
  });

  it("Empty renders its message", () => {
    render(<Empty>Nothing here yet.</Empty>);
    expect(screen.getByText("Nothing here yet.")).toBeInTheDocument();
  });
});

/**
 * v1.185.0 — the spinner family was the app's one unguarded animation.
 *
 * Every other moving thing here checks `prefers-reduced-motion` (the 3D graph's
 * auto-orbit, the ambient body drift, smooth scrolling in `useFocusRef`,
 * AgentFace's blink). `animate-spin-slow` did not, and it is applied DIRECTLY in
 * a dozen components — so the guard belongs on the class, in the one stylesheet,
 * rather than on any component that happens to use it.
 *
 * ASSERTED AGAINST THE STYLESHEET SOURCE because jsdom compiles no Tailwind and
 * evaluates no media query: a render-based test here could only prove the class
 * name is on the element, which is exactly what was already true while the bug
 * was live. Reading the rule is the v1.175.0 shape — extract the real mechanism
 * and check it, rather than checking something adjacent to it.
 */
describe("motion is guarded", () => {
  const css = readFileSync(join(process.cwd(), "app", "globals.css"), "utf8");

  /** The body of every `@media (prefers-reduced-motion: reduce)` block. */
  function reducedMotionBlocks(): string[] {
    const out: string[] = [];
    const marker = "@media (prefers-reduced-motion: reduce)";
    let at = css.indexOf(marker);
    while (at !== -1) {
      // Walk braces from the block's opening one so a nested rule cannot end
      // the block early — a naive indexOf("}") would stop at the FIRST inner
      // rule and report the guard as missing.
      let i = css.indexOf("{", at);
      let depth = 0;
      const start = i;
      for (; i < css.length; i++) {
        if (css[i] === "{") depth++;
        else if (css[i] === "}" && --depth === 0) break;
      }
      out.push(css.slice(start + 1, i));
      at = css.indexOf(marker, i);
    }
    return out;
  }

  it("stops animate-spin-slow under prefers-reduced-motion", () => {
    const blocks = reducedMotionBlocks();
    expect(blocks.length).toBeGreaterThan(0);
    const guard = blocks.find((b) => b.includes(".animate-spin-slow"));
    expect(guard, "no reduced-motion rule covers .animate-spin-slow").toBeTruthy();
    expect(guard).toMatch(/\.animate-spin-slow\s*\{[^}]*animation:\s*none/);
  });

  it("LoaderInline still says it is working when the motion is gone", () => {
    // A stopped spinner is a STILL CIRCLE, so the state cannot be carried by
    // the rotation any more. Without a label that is indistinguishable from an
    // idle icon — a worse failure than the spin it replaced — so the role and
    // the text are the other half of the fix, not decoration.
    const { unmount } = render(<LoaderInline />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByText("Working…")).toBeInTheDocument();
    unmount();

    render(<LoaderInline label="Saving…" />);
    expect(screen.getByRole("status")).toHaveTextContent("Saving…");
  });
});
