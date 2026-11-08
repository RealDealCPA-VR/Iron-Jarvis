/**
 * AN review of v1.450.0 Wave D lane D3, item 37: the page's own question.
 *
 *  - a HELD Enter (auto-repeat) on the focused "Undo it" never answers yes;
 *  - once answered, the focus goes back to the press that asked (the
 *    browser's confirm() did that; falling to the page let a later Enter
 *    answer a docked card);
 *  - leaving a chat answers its open Undo question "no", and the chat's
 *    window Escape for its menus never acts while the chat is hidden behind
 *    the Code view (source pins: the full page is pinned in
 *    wave-d3-review-composer-v1450).
 */
import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { PageQuestion, usePageQuestion } from "@/components/PageQuestion";

const read = (rel: string) => readFileSync(join(process.cwd(), rel), "utf8").replace(/\r\n/g, "\n");

afterEach(() => cleanup());

function Harness({ answers }: { answers: boolean[] }) {
  const q = usePageQuestion();
  return (
    <div>
      <button type="button" data-testid="opener" onClick={() => void q.ask("Undo this write?", { yes: "Undo it" }).then((v) => answers.push(v))}>
        Undo
      </button>
      <PageQuestion state={q.state} onAnswer={q.answer} testId="q" />
    </div>
  );
}

describe("review: the page's question", () => {
  it("a held Enter on the focused yes press is refused, and nothing is answered", async () => {
    const answers: boolean[] = [];
    render(<Harness answers={answers} />);
    fireEvent.click(screen.getByTestId("opener"));
    const yes = await screen.findByTestId("q-yes");
    expect(document.activeElement).toBe(yes);
    const ev = new KeyboardEvent("keydown", { key: "Enter", repeat: true, bubbles: true, cancelable: true });
    act(() => {
      yes.dispatchEvent(ev);
    });
    expect(ev.defaultPrevented).toBe(true);
    // A first, deliberate Enter is not refused (the browser then clicks).
    const first = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
    act(() => {
      yes.dispatchEvent(first);
    });
    expect(first.defaultPrevented).toBe(false);
    await act(async () => {});
    expect(answers).toEqual([]);
    expect(screen.getByTestId("q")).toBeInTheDocument();
  });

  it("answered, the focus goes back to the press that asked (yes and no)", async () => {
    const answers: boolean[] = [];
    render(<Harness answers={answers} />);
    const opener = screen.getByTestId("opener");
    for (const which of ["yes", "no"] as const) {
      opener.focus();
      fireEvent.click(opener);
      const btn = await screen.findByTestId(`q-${which}`);
      expect(document.activeElement).not.toBe(opener);
      fireEvent.click(btn);
      await act(async () => {});
      expect(screen.queryByTestId("q")).toBeNull();
      expect(document.activeElement).toBe(opener);
    }
    expect(answers).toEqual([true, false]);
  });

  it("Escape on the question answers no and gives the focus back", async () => {
    const answers: boolean[] = [];
    render(<Harness answers={answers} />);
    const opener = screen.getByTestId("opener");
    opener.focus();
    fireEvent.click(opener);
    const yes = await screen.findByTestId("q-yes");
    fireEvent.keyDown(yes, { key: "Escape" });
    await act(async () => {});
    expect(answers).toEqual([false]);
    expect(document.activeElement).toBe(opener);
  });

  it("source: leaving a chat answers its Undo question no; a hidden chat takes no Escape", () => {
    const src = read("app/chat/page.tsx");
    const leave = src.slice(src.indexOf("function leaveConversation() {"));
    const end = leave.indexOf("\n  }\n");
    expect(leave.slice(0, end)).toContain("undoQuestion.answer(false);");
    const i = src.indexOf("const askInDockRef = useRef(false);");
    expect(i).toBeGreaterThan(0);
    const fx = src.slice(i, i + 900);
    expect(fx).toMatch(/if \(surfaceHiddenRef\.current\) return;/);
    expect(fx.indexOf("if (askInDockRef.current) return;")).toBeLessThan(fx.indexOf("e.stopPropagation();"));
  });
});
