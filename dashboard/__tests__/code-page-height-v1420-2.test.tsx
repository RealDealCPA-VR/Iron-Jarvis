/**
 * v1.420.2 — two things from one screenshot of a Chat › Code session.
 *
 * 1. THE PAGE NEVER SCROLLS PAST THE CODE VIEW (a user bug). A transcript that
 *    is not a positioning box lets a screen-reader-only label (`sr-only` is
 *    position:absolute) be placed by the whole view: the transcript does not
 *    clip it and it stretches the page. Measured in a real browser on a copy
 *    of the user's session (9,094 px in a 950 px window) and pinned there by
 *    e2e/code-page-height.spec.ts — which CI does not run, so the classes that
 *    make it hold are pinned here too: every scroll box of both Code views is
 *    `relative`.
 * 2. THE SANDBOX NOTE TO THE MODEL STAYS OFF THE SCREEN. The shell tool starts
 *    a command's output with "[native runtime — limits advisory, see first
 *    shell result]" (or its first-time paragraph) when it ran without the
 *    sandbox. Every command row previewed it. The preview skips it, and the
 *    opened output says it once in plain words.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, API_BASE: "http://localhost", ijToken: () => "" };
});

import { LiveToolRows } from "@/components/chat/WorkLine";
import { PlanChecklist } from "@/components/chat/PlanChecklist";
import type { ToolCard } from "@/lib/useChatStream";
import { RUNTIME_NOTE_LINES, UNCONFINED_WORDS, isRuntimeNote, withoutRuntimeNotes } from "@/lib/stepDetail";

afterEach(cleanup);

/* ------------------------------------------------------- 1. the page height */

const read = (rel: string) => readFileSync(join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");

/** The className the source gives the element carrying `testid` (the first
 *  className after it, within the same tag). */
function classOf(src: string, testid: string): string {
  const at = src.indexOf(`data-testid="${testid}"`);
  expect(at, `no element ${testid}`).toBeGreaterThan(-1);
  const tagEnd = src.indexOf(">", at);
  const tag = src.slice(at, tagEnd);
  const m = tag.match(/className=\{?[`"]([^`"]*)[`"]/);
  expect(m, `no className on ${testid}`).not.toBeNull();
  return m![1];
}

describe("every scroll box of both Code views positions what it holds", () => {
  it("Chat › Code's transcript is relative", () => {
    expect(classOf(read("components/harness/HarnessSession.tsx"), "harness-transcript").split(/\s+/)).toContain("relative");
  });

  it("Build › Code's transcript is relative", () => {
    expect(classOf(read("components/buildCode/BuildCodeView.tsx"), "code-transcript").split(/\s+/)).toContain("relative");
  });

  it("the Code view's empty, Tonight and Last night boxes are relative", () => {
    const src = read("components/harness/IjHarnessView.tsx");
    expect(classOf(src, "harness-no-session").split(/\s+/)).toContain("relative");
    // The two lists carry no test id of their own: the box right around each.
    for (const list of ["<TonightList", "<LastNightList"]) {
      const at = src.indexOf(list);
      expect(at).toBeGreaterThan(-1);
      const open = src.lastIndexOf("<div", at);
      const box = src.slice(open, at);
      expect(box).toMatch(/className="[^"]*\brelative\b[^"]*overflow-y-auto/);
    }
  });
});

describe("a plan row keeps its screen-reader words inside itself", () => {
  it("every row is a positioning box, wherever the checklist is shown", () => {
    render(
      <PlanChecklist
        plan={{
          total: 2,
          steps: [
            { index: 0, goal: "Read the repo", state: "done" },
            { index: 1, goal: "Find the icon", state: "running" },
          ],
        }}
      />,
    );
    const rows = screen.getAllByTestId("plan-step");
    expect(rows).toHaveLength(2);
    for (const row of rows) {
      expect(row.className.split(/\s+/)).toContain("relative");
      // The words a screen reader says are still there.
      expect(row.querySelector(".sr-only")).not.toBeNull();
    }
  });
});

/* ---------------------------------------------- 2. the sandbox note to the model */

const COMPACT = "[native runtime — limits advisory, see first shell result]";
const FIRST =
  "[warning] sandbox: filesystem/host/network policy NOT enforced — ran on the native runtime (Docker unavailable); workspace_only/host_access/internet limits are advisory only for this run";

describe("the shell tool's sandbox note", () => {
  it("knows both of the shell tool's lines, and nothing else", () => {
    expect(RUNTIME_NOTE_LINES).toEqual([COMPACT, FIRST]);
    expect(isRuntimeNote(`  ${COMPACT}  `)).toBe(true);
    expect(isRuntimeNote("native runtime")).toBe(false);
    expect(isRuntimeNote(`${COMPACT} and more`)).toBe(false);
  });

  it("is taken out of an output, and says it was there", () => {
    expect(withoutRuntimeNotes(`${COMPACT}\nhello`)).toEqual({ text: "hello", unconfined: true });
    expect(withoutRuntimeNotes(`${FIRST}\r\nline 1\r\nline 2`)).toEqual({ text: "line 1\nline 2", unconfined: true });
    expect(withoutRuntimeNotes(COMPACT)).toEqual({ text: "", unconfined: true });
    // An output without it is handed back untouched (CRLF and all).
    expect(withoutRuntimeNotes("a\r\nb")).toEqual({ text: "a\r\nb", unconfined: false });
    expect(withoutRuntimeNotes("")).toEqual({ text: "", unconfined: false });
  });

  it("a command row previews the command's own output, never the note", () => {
    const cards: ToolCard[] = [
      {
        id: "a",
        name: "shell",
        status: "done",
        ok: true,
        args: { command: "git status" },
        output: `${COMPACT}\nOn branch main`,
        startedAt: 1,
        endedAt: 201,
      },
      {
        id: "b",
        name: "shell",
        status: "done",
        ok: true,
        args: { command: "cd C:\\work" },
        output: COMPACT,
        startedAt: 1,
        endedAt: 101,
      },
    ];
    render(<LiveToolRows cards={cards} />);
    const [withOutput, quiet] = screen.getAllByTestId("work-row");
    expect(withOutput.textContent).toContain("On branch main");
    for (const row of [withOutput, quiet]) {
      expect(row.textContent).not.toContain("native runtime");
      expect(row.textContent).not.toContain("advisory");
    }
  });

  it("an opened command says it ran without the sandbox, in plain words, once", () => {
    const cards: ToolCard[] = [
      {
        id: "a",
        name: "shell",
        status: "done",
        ok: true,
        args: { command: "git status" },
        output: `${FIRST}\nOn branch main`,
        detail: { input: '{"command": "git status"}', output: `${FIRST}\nOn branch main` },
        startedAt: 1,
        endedAt: 201,
      },
    ];
    render(<LiveToolRows cards={cards} />);
    const row = screen.getByTestId("work-row");
    expect(row.textContent).toContain("On branch main");
    expect(row.textContent).not.toContain("NOT enforced");
    fireEvent.click(row);
    const panel = screen.getByTestId("step-detail");
    expect(within(panel).getByTestId("step-detail-output").textContent).toBe("On branch main");
    expect(within(panel).getByTestId("step-detail-unconfined").textContent).toBe(UNCONFINED_WORDS);
    expect(panel.textContent).not.toContain("native runtime");
  });

  it("an opened command that ran IN the sandbox says nothing about it", () => {
    const cards: ToolCard[] = [
      {
        id: "a",
        name: "shell",
        status: "done",
        ok: true,
        args: { command: "git status" },
        output: "On branch main",
        detail: { input: '{"command": "git status"}', output: "On branch main" },
        startedAt: 1,
        endedAt: 201,
      },
    ];
    render(<LiveToolRows cards={cards} />);
    fireEvent.click(screen.getByTestId("work-row"));
    expect(screen.queryByTestId("step-detail-unconfined")).toBeNull();
  });
});
