/**
 * v1.450.0 — Wave D, lane D3 (docs/PRODUCT-READINESS-RESULTS.md 0h.5, items
 * 37 and 38): the page asks its own questions, and a press that cannot be
 * pressed says why.
 *
 *  37  The four native confirm() boxes are the page's own question
 *      (components/PageQuestion.tsx): the chat's Undo, a Build terminal
 *      chat's Undo, DocPreview's "Replace it?", Creative's publish gate.
 *      A Build pane's ask says "Click Allow once to run it" when Enter is
 *      refused on a safety card (it was swallowed in silence). The Autonomy
 *      page's presses are the shared <Button>.
 *  38  whyDisabled / DisabledNote; the chat's Send and Build › Code's Start
 *      carry their reason; Copy on a code block shows on a touch screen; the
 *      long-paste fold is a view only (lib/composerFold).
 *
 * The chat page's own behaviour (fold, placeholder, Escape per menu) is in
 * wave-d3-composer-v1450.test.tsx; Build's stuck states in
 * wave-d3-build-v1450.test.tsx.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    get: vi.fn(),
    post: vi.fn(async () => ({})),
    ApiError,
    API_BASE: "http://api.test",
    ijToken: () => "tok",
  };
});

import { PageQuestion, usePageQuestion } from "@/components/PageQuestion";
import { DocPreview } from "@/components/chat/DocPreview";
import { PaneAsk } from "@/components/terminal/PaneAsk";
import { ENTER_BLOCKED_NOTE } from "@/lib/dockAsk";
import { fillInFirst, whyDisabled, SEND_NEEDS_WORDS } from "@/lib/whyDisabled";
import { DisabledNote } from "@/components/DisabledNote";
import { FOLDED_BOX_PX, LONG_PASTE_LINES, foldsAt, lineCount, showAllWords } from "@/lib/composerFold";
import { ApiError, get, post } from "@/lib/api";
import type { PendingApproval } from "@/lib/useChatStream";

const ROOT = process.cwd();
const read = (rel: string) => readFileSync(join(ROOT, rel), "utf8").replace(/\r\n/g, "\n");

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/* ====================================================== 37: the question */

function Harness({ onAnswer }: { onAnswer: (v: boolean) => void }) {
  const q = usePageQuestion();
  return (
    <div>
      <button type="button" onClick={() => void q.ask("Undo this write?", { yes: "Undo it" }).then(onAnswer)}>
        ask
      </button>
      <button type="button" onClick={() => void q.ask("Replace it?", { yes: "Replace it" }).then(onAnswer)}>
        ask2
      </button>
      <PageQuestion state={q.state} onAnswer={q.answer} testId="q" />
    </div>
  );
}

describe("37: the page's own question replaces confirm()", () => {
  it("the yes press answers yes, the no press and Escape answer no; the question goes away", async () => {
    const answers: boolean[] = [];
    render(<Harness onAnswer={(v) => answers.push(v)} />);
    fireEvent.click(screen.getByText("ask"));
    const q = await screen.findByTestId("q");
    expect(q.textContent).toContain("Undo this write?");
    expect(screen.getByTestId("q-yes").textContent).toBe("Undo it");
    expect(document.activeElement).toBe(screen.getByTestId("q-yes")); // the keyboard answers it
    fireEvent.click(screen.getByTestId("q-yes"));
    await waitFor(() => expect(answers).toEqual([true]));
    expect(screen.queryByTestId("q")).toBeNull();
    fireEvent.click(screen.getByText("ask"));
    fireEvent.click(await screen.findByTestId("q-no"));
    await waitFor(() => expect(answers).toEqual([true, false]));
    fireEvent.click(screen.getByText("ask"));
    fireEvent.keyDown(await screen.findByTestId("q"), { key: "Escape" });
    await waitFor(() => expect(answers).toEqual([true, false, false]));
  });

  it("a second question answers the first no; leaving answers no — no promise is left hanging", async () => {
    const answers: boolean[] = [];
    const { unmount } = render(<Harness onAnswer={(v) => answers.push(v)} />);
    fireEvent.click(screen.getByText("ask"));
    fireEvent.click(screen.getByText("ask2"));
    await waitFor(() => expect(answers).toEqual([false]));
    expect(screen.getByTestId("q").textContent).toContain("Replace it?");
    unmount();
    await waitFor(() => expect(answers).toEqual([false, false]));
  });

  it("DocPreview: a copy over a file of the same name asks on the page, and only Replace it overwrites", async () => {
    const native = vi.spyOn(window, "confirm");
    vi.mocked(get).mockImplementation(async (p: string) => {
      if (p.startsWith("/documents/places")) return { places: [{ key: "docs", label: "Documents", path: "C:\\Docs" }] } as never;
      if (p.startsWith("/documents/preview"))
        return { kind: "text", name: "memo.txt", path: "C:\\w\\memo.txt", suffix: ".txt", content: "hi" } as never;
      if (p.startsWith("/documents/siblings")) return { files: [] } as never;
      return {} as never;
    });
    let calls = 0;
    vi.mocked(post).mockImplementation(async (_p: string, body?: unknown) => {
      calls += 1;
      if (!(body as { overwrite?: boolean }).overwrite) throw new ApiError("exists", 409);
      return { path: "C:\\Docs\\memo.txt", folder: "C:\\Docs" } as never;
    });
    render(<DocPreview path="C:\\w\\memo.txt" onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Documents/ }));
    const q = await screen.findByTestId("doc-replace-confirm");
    expect(q.textContent).toContain("is already in that folder. Replace it?");
    // Declined: nothing more is sent.
    fireEvent.click(screen.getByTestId("doc-replace-confirm-no"));
    await waitFor(() => expect(screen.queryByTestId("doc-replace-confirm")).toBeNull());
    expect(calls).toBe(1);
    // Again, and Replace it: the second post overwrites.
    fireEvent.click(screen.getByRole("button", { name: /Documents/ }));
    fireEvent.click(await screen.findByTestId("doc-replace-confirm-yes"));
    await waitFor(() => expect(screen.getByText(/Saved to C:\\Docs\\memo\.txt/)).toBeInTheDocument());
    expect(vi.mocked(post).mock.calls.map((c) => (c[1] as { overwrite: boolean }).overwrite)).toEqual([false, false, true]);
    expect(native).not.toHaveBeenCalled();
    native.mockRestore();
  });

  it("source: the chat's, the pane's and Creative's questions are the page's own", () => {
    const chat = read("app/chat/page.tsx");
    const undo = chat.slice(chat.indexOf("async function undoWrite("), chat.indexOf("async function undoWrite(") + 600);
    expect(undo).toContain("await undoQuestion.ask(confirmUndoPrompt(row?.kind, base)");
    expect(chat).toMatch(/<PageQuestion\s+state=\{undoQuestion\.state\}\s+onAnswer=\{undoQuestion\.answer\}\s+testId="chat-undo-confirm"/);
    const pane = read("components/terminal/PaneChat.tsx");
    expect(pane).toMatch(/await undoQuestion\.ask\(paneUndoPrompt\(/);
    expect(pane).toMatch(/testId="pane-undo-confirm"/);
    const creative = read("app/creative/page.tsx");
    expect(creative).toMatch(/<PageQuestionDialog\s+state=\{publishQuestion\.state\}/);
    expect(creative).toMatch(/async function confirmPublish\(\): Promise<boolean> \{\n  if \(!publishAsker\) return false;/);
  });
});

/* ================================================ 37: a pane's blocked Enter */

describe("37: a Build pane's ask says why Enter did nothing", () => {
  const SAFE: PendingApproval = {
    id: "ap_safe",
    callId: "c1",
    tool: "shell",
    args: { command: "echo hi" },
    onceOnly: true,
    onceReason: "command",
  };
  const ORDINARY: PendingApproval = { id: "ap_plain", callId: "c2", tool: "shell", args: { command: "git status" } };

  it("Enter on a safety card shows ENTER_BLOCKED_NOTE in its live region, then it goes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<PaneAsk approval={SAFE} armDelayMs={0} />);
      expect(screen.getByTestId("pane-ask-enter-live").textContent).toBe("");
      fireEvent.keyDown(screen.getByTestId("pane-ask"), { key: "Enter" });
      expect(screen.getByTestId("pane-ask-enter-blocked").textContent).toBe(ENTER_BLOCKED_NOTE);
      expect(screen.getByTestId("pane-ask-enter-live").getAttribute("role")).toBe("status");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000);
      });
      expect(screen.queryByTestId("pane-ask-enter-blocked")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("control: on an ordinary card Enter allows and no note shows", async () => {
    render(<PaneAsk approval={ORDINARY} armDelayMs={0} />);
    fireEvent.keyDown(screen.getByTestId("pane-ask"), { key: "Enter" });
    await waitFor(() => expect(vi.mocked(post)).toHaveBeenCalled());
    expect(screen.queryByTestId("pane-ask-enter-blocked")).toBeNull();
  });
});

/* ============================================== 37: the Autonomy page's presses */

describe("37: the Autonomy page uses the shared buttons", () => {
  it("every raw <button> left is a disclosure (aria-expanded); the actions are <Button>", () => {
    const src = read("app/autonomy/page.tsx");
    const raws = src.split("<button").slice(1);
    expect(raws.length).toBe(3); // the three Show/Hide toggles
    for (const r of raws) expect(r.slice(0, 220)).toContain("aria-expanded");
    // Each press's own JSX words (never a comment that names it).
    for (const words of [
      "/> Check now",
      '"Enable autonomy"}',
      "/> Emergency stop",
      "/> Send briefing",
      "/> Approve",
      "/> Run now",
      "/> Pause",
      "/> Resume",
      "/> Add goal",
      "Not now\n",
    ]) {
      const at = src.indexOf(words);
      expect(at, words).toBeGreaterThan(0);
      const open = Math.max(src.lastIndexOf("<Button", at), src.lastIndexOf("<button", at));
      expect(src.slice(open, open + 7), words).toBe("<Button");
    }
    expect(src).not.toContain("const smallBtn");
  });
});

/* ============================================ 38: a disabled press says why */

describe("38: whyDisabled and DisabledNote", () => {
  it("the first reason that applies wins; none = enabled", () => {
    expect(whyDisabled(false, null, "b", "c")).toBe("b");
    expect(whyDisabled(false, "", undefined, 0)).toBeNull();
    expect(fillInFirst(["API key"], "connect")).toBe("Fill in API key to connect.");
    expect(fillInFirst(["Name", "", "Base URL"], "add the service")).toBe("Fill in Name and Base URL to add the service.");
    expect(fillInFirst(["A", "B", "C"], "x")).toBe("Fill in A, B and C to x.");
    expect(fillInFirst([], "x")).toBeNull();
  });

  it("the note draws the reason, and nothing when the press is enabled", () => {
    const { rerender } = render(<DisabledNote why="Fill in API key to connect." testId="n" />);
    expect(screen.getByTestId("n").textContent).toBe("Fill in API key to connect.");
    rerender(<DisabledNote why={null} testId="n" />);
    expect(screen.queryByTestId("n")).toBeNull();
  });

  it("source: the chat's Send, Start, Connect and Add carry their reason", () => {
    const chat = read("app/chat/page.tsx");
    const send = chat.slice(chat.indexOf("const SendArrow = memo("), chat.indexOf("/** Voice Chat auto-send"));
    expect(send).toContain("SEND_NEEDS_WORDS");
    expect(send).toContain('title={why ?? "Send (Enter)"}');
    expect(SEND_NEEDS_WORDS).toBe("Type a message or attach a file first.");
    const start = read("components/buildCode/StartComposer.tsx");
    expect(start).toContain('title={startWhy ?? "Send (Enter)"}');
    expect(start).toMatch(/<DisabledNote why=\{startWhy\} testId="code-start-why"/);
    const dir = read("components/settings/pages/DirectoryPage.tsx");
    expect(dir).toMatch(/<DisabledNote why=\{busy \? null : connectWhy\} testId=\{`connector-why-\$\{c\.id\}`\}/);
    const rest = read("components/connections/RestHookups.tsx");
    expect(rest).toMatch(/<DisabledNote why=\{addWhy\} testId="add-integ-why"/);
    const doors = read("components/onboarding/ConnectDoors.tsx");
    expect(doors).toContain('"Paste a key first."');
  });
});

/* ============================================ 38: Copy on touch, the fold */

describe("38: Copy shows on touch; the fold is a view", () => {
  it("the code block's Copy is visible where there is no hover", () => {
    expect(read("components/chat/CodeBlock.tsx")).toContain("[@media(hover:none)]:opacity-100");
  });

  it("a message folds past LONG_PASTE_LINES and its words name the whole count", () => {
    expect(LONG_PASTE_LINES).toBe(15);
    expect(FOLDED_BOX_PX).toBe(160);
    expect(lineCount("")).toBe(0);
    expect(lineCount("a")).toBe(1);
    expect(lineCount("a\nb\n")).toBe(3);
    expect(foldsAt(15)).toBe(false);
    expect(foldsAt(16)).toBe(true);
    expect(showAllWords(40)).toBe("Show all 40 lines");
  });
});
