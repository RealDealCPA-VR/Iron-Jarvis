/**
 * v1.245.0 — Build quick fixes (the pane half).
 *
 * From the 2026-09-11 Build reliability audit:
 *  - a restored pane ran a FRESH shell (an update or crash ends the CLI), and
 *    there was no way back into the conversation — now a Resume strip types
 *    the CLI's own continue command on one click;
 *  - every ResizeObserver tick sent a resize, and every resize made ConPTY
 *    reflow and the TUI repaint — now the size is sent once it settles, and
 *    an unchanged size sends nothing (open and window focus still force it);
 *  - Ctrl+C with text selected interrupted the running CLI instead of copying
 *    it, the opposite of the Windows Terminal habit.
 * jsdom cannot run xterm, so the strip is rendered on its own and the pane's
 * wiring is source-pinned (the house idiom).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { RESIZE_SETTLE_MS, ResumeStrip } from "@/components/terminal/TerminalPane";

describe("the Resume strip", () => {
  it("names the CLI, resumes on one click, and can be dismissed", () => {
    const onResume = vi.fn();
    const onDismiss = vi.fn();
    render(
      <ResumeStrip
        cliLabel="Claude Code"
        command="claude --continue"
        onResume={onResume}
        onDismiss={onDismiss}
      />,
    );
    expect(screen.getByTestId("resume-strip")).toHaveTextContent(
      "Claude Code was running here before Iron Jarvis restarted.",
    );
    const resume = screen.getByRole("button", { name: "Resume" });
    expect(resume.getAttribute("title")).toContain("claude --continue");
    fireEvent.click(resume);
    expect(onResume).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss the resume offer" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});

describe("the pane's wiring (source-pinned)", () => {
  const pane = readFileSync(
    join(process.cwd(), "components", "terminal", "TerminalPane.tsx"),
    "utf8",
  ).replace(/\r\n/g, "\n");

  it("fits and resizes once the size settles, never per ResizeObserver tick", () => {
    expect(RESIZE_SETTLE_MS).toBeGreaterThanOrEqual(50);
    expect(RESIZE_SETTLE_MS).toBeLessThanOrEqual(250);
    expect(pane).toContain("ro = new ResizeObserver(() => scheduleFit());");
    expect(pane).toContain("}, RESIZE_SETTLE_MS);");
    expect(pane).toContain("if (resizeTimer) clearTimeout(resizeTimer);");
  });

  it("an unchanged size sends nothing — except on open and on window focus", () => {
    const send = pane.slice(pane.indexOf("const sendResize = (force = false) => {"));
    expect(send).toContain('if (!force && host.sentSize === size) return;');
    expect(pane).toContain("sendResize(true); // a new attach claims its size even when unchanged");
    const focus = pane.slice(pane.indexOf("const onWinFocus = () => {"));
    expect(focus.slice(0, 120)).toContain("sendResize(true);");
  });

  it("Ctrl+C copies when text is selected, and still interrupts when it is not", () => {
    const handler = pane.slice(pane.indexOf("const keyHandler = (e: KeyboardEvent)"));
    expect(handler).toContain(
      'if (mod && !e.shiftKey && (e.key === "c" || e.key === "C") && term?.hasSelection()) {',
    );
  });

  it("Resume types the CLI's continue command, then records the CLI and clears the offer", () => {
    const resume = pane.slice(pane.indexOf("function resumeCli()"));
    expect(resume).toContain("hostRef.current?.send(`${command}\\r`)");
    expect(resume).toContain('{ agent_cli: cli, resume_cli: "" }');
    expect(pane).toContain("<ResumeStrip");
  });
});
