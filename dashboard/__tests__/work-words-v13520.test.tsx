/**
 * v1.352.0 (readiness audit G22): steps from connected apps and the browser
 * read in words.
 *
 * THE FAILURE. A connected app's call read "Ran mcp__gmail__search_messages"
 * on the work line and counted as "ran 1 tool"; browser work read "Ran
 * browser_click"; a test pinned the raw id on screen; nothing next to a
 * failed app step offered the fix.
 *
 * ACCEPTANCE (as a new user reads it): a turn that searched Gmail through a
 * connected app reads "Worked for 3 s · used gmail" with the row "gmail:
 * search messages"; a browser click reads "Used your browser".
 *
 * Every pin drives the REAL vocabulary (lib/workTarget, lib/stepWords,
 * lib/toolWords) through the REAL work line (components/chat/WorkLine: the
 * folded line, its rows, the live rows) and the expanded receipt
 * (TurnReceipt) — nothing under test is stubbed.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import { LiveToolRows, WorkLine } from "@/components/chat/WorkLine";
import { TurnReceipt } from "@/components/chat/TurnReceipt";
import type { ToolCard } from "@/lib/useChatStream";
import { stepTarget, toolKind } from "@/lib/workTarget";
import { stepCounts, stepPhrase, stepPhraseText } from "@/lib/stepWords";
import {
  BROWSER_TOOLS,
  appToolNameWords,
  appToolParts,
  browserActionWords,
  isBuiltinBrowserTool,
  toolWords,
} from "@/lib/toolWords";

afterEach(() => cleanup());

const GMAIL = "mcp__gmail__search_messages";
const THREE_SECONDS = { startedAt: 1_000, firstTokenAt: 2_000, endedAt: 4_000 };

function openLine() {
  fireEvent.click(screen.getByRole("button", { expanded: false }));
  return within(screen.getByTestId("work-rows"));
}

/** The lucide icon a row draws (its svg carries `lucide-<name>`). */
function iconOf(row: HTMLElement): string {
  return row.querySelector("svg")?.getAttribute("class") ?? "";
}

/* ======================================================== the acceptance */

describe("a turn that searched Gmail through a connected app", () => {
  it("folds under 'Worked for 3.0 s · used gmail', never 'ran 1 tool'", () => {
    render(<WorkLine steps={[{ name: GMAIL, ok: true, ms: 400 }]} timing={THREE_SECONDS} />);
    expect(screen.getByTestId("work-summary").textContent).toBe("Worked for 3.0 s · used gmail");
    expect(screen.getByTestId("work-line").textContent).not.toMatch(/ran 1 tool/);
  });

  it("its row reads 'gmail: search messages' under a plug; the raw id is only in the hover", () => {
    render(<WorkLine steps={[{ name: GMAIL, ok: true, ms: 400 }]} timing={THREE_SECONDS} />);
    const rows = openLine();
    const [row] = rows.getAllByTestId("work-row");
    expect(row.textContent).toBe("gmail: search messages0.4 s");
    expect(within(row).getByText("gmail:")).toBeInTheDocument();
    const subject = within(row).getByTestId("work-target");
    expect(subject.textContent).toBe("search messages");
    expect(subject.getAttribute("title")).toBe(`search messages (${GMAIL})`);
    expect(screen.queryByText(GMAIL)).toBeNull();
    expect(iconOf(row)).toMatch(/lucide-plug/);
  });

  it("the expanded receipt under the reply says the same step the same way", () => {
    render(<TurnReceipt toolsUsed={[GMAIL]} steps={[{ name: GMAIL, ok: true, ms: 400 }]} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    const [step] = screen.getAllByTestId("turn-step");
    expect(step.textContent).toBe("gmail: search messages · 0.4 s");
    expect(step.getAttribute("title")).toContain(GMAIL);
  });
});

describe("a browser click", () => {
  it("folds under '… · used your browser' and its row reads 'Used your browser · click' under a window", () => {
    render(<WorkLine steps={[{ name: "browser_click", ok: true, ms: 200 }]} timing={THREE_SECONDS} />);
    expect(screen.getByTestId("work-summary").textContent).toBe("Worked for 3.0 s · used your browser");
    const [row] = openLine().getAllByTestId("work-row");
    expect(row.textContent).toBe("Used your browserclick0.2 s");
    expect(within(row).getByText("Used your browser")).toBeInTheDocument();
    // The action is the quiet word after the dot; its tool id is the hover.
    expect(within(row).getByText("click").getAttribute("title")).toBe("browser_click");
    expect(screen.queryByText("browser_click")).toBeNull();
    expect(iconOf(row)).toMatch(/lucide-app-window/);
  });

  it("the receipt says 'Used your browser · click'", () => {
    render(<TurnReceipt toolsUsed={["browser_click"]} steps={[{ name: "browser_click", ok: true, ms: 200 }]} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getAllByTestId("turn-step")[0].textContent).toBe("Used your browser · click · 0.2 s");
  });
});

/* =========================================================== the counter */

describe("one counter: apps by name, the browser as one surface", () => {
  it("counts each app by its own name, in the order first used, and the browser as one", () => {
    expect(
      stepCounts([
        { name: "read_file", ok: true },
        { name: GMAIL, ok: true },
        { name: "browser_navigate", ok: true },
        { name: "mcp__github__list_issues", ok: true },
        { name: "mcp__gmail__get_message", ok: true },
        { name: "browser_click", ok: true },
        { name: "excel_query", ok: true },
      ]),
      // Readiness 12A (G22, moved on purpose): a registered built-in counts
      // as itself; only a custom tool is "ran 1 tool".
    ).toEqual(["read 1 file", "used your browser 2 times", "used gmail 2 times", "used github", "queried a workbook"]);
  });

  it("a failed app step counts only as failed (the v1.351.0 rule holds)", () => {
    expect(stepCounts([{ name: GMAIL, ok: false }])).toEqual(["1 failed"]);
    expect(stepCounts([{ name: GMAIL, ok: true }, { name: GMAIL, ok: false }])).toEqual(["used gmail", "1 failed"]);
  });

  it("the live line counts the same way while the turn runs", () => {
    const cards: ToolCard[] = [
      { id: "a", name: GMAIL, status: "done", ok: true, args: { query: "from:boss" }, startedAt: 1_000, endedAt: 1_400 },
      { id: "b", name: "browser_navigate", status: "running", args: { url: "https://example.com" } },
    ];
    render(<LiveToolRows cards={cards} />);
    expect(screen.getByTestId("live-work-words").textContent).toMatch(/· used gmail$/);
  });
});

/* =========================================================== the live rows */

describe("live rows say the app and the browser in words too", () => {
  it("a running app call reads 'gmail: search messages' and shimmers; its args stay in the hover", () => {
    const card: ToolCard = { id: "a", name: GMAIL, status: "running", args: { query: "from:boss" } };
    render(<LiveToolRows cards={[card]} />);
    const [row] = screen.getAllByTestId("work-row");
    expect(row.getAttribute("data-state")).toBe("running");
    const words = within(row).getByText("gmail:");
    expect(words.className).toContain("ij-shimmer");
    const subject = within(row).getByTestId("work-target");
    expect(subject.textContent).toBe("search messages");
    // What it was called with, on hover only (it was an unknown tool's hover).
    expect(subject.getAttribute("title")).toBe(`from:boss (${GMAIL})`);
    expect(screen.queryByText(GMAIL)).toBeNull();
  });

  it("a running browser step reads 'Using your browser · navigate'", () => {
    const card: ToolCard = { id: "b", name: "browser_navigate", status: "running", args: { url: "https://example.com/a" } };
    render(<LiveToolRows cards={[card]} />);
    const [row] = screen.getAllByTestId("work-row");
    expect(within(row).getByText("Using your browser")).toBeInTheDocument();
    expect(within(row).getByText("navigate")).toBeInTheDocument();
    expect(screen.queryByText("browser_navigate")).toBeNull();
  });
});

/* ===================================================== the way forward */

describe("a failed app step offers 'Fix in Tools'", () => {
  it("on the folded rows: one quiet link to /tools beneath the failed row, never inside it", () => {
    render(
      <WorkLine
        steps={[
          { name: GMAIL, ok: false, ms: 400 },
          { name: "write_file", ok: false, ms: 100 },
          { name: "mcp__github__list_issues", ok: true, ms: 300 },
        ]}
        timing={THREE_SECONDS}
      />,
    );
    const rows = openLine();
    const links = rows.getAllByRole("link", { name: "Fix in Tools" });
    // Only the FAILED APP step: not the failed built-in, not the app that worked.
    expect(links).toHaveLength(1);
    const link = links[0];
    expect(link.getAttribute("href")).toBe("/tools");
    expect(link.getAttribute("title")).toBe("Open Tools to check gmail");
    expect(link.closest("button")).toBeNull();
    // It follows the gmail row, before the next step.
    const [gmailRow, writeRow] = rows.getAllByTestId("work-row");
    expect(gmailRow.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(link.compareDocumentPosition(writeRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Quiet: grey words, no box, no border, no fill.
    expect(link.className).toContain("text-zinc-500");
    expect(link.className).not.toMatch(/(^|\s)(border|bg-|shadow|rounded-(lg|xl))/);
  });

  it("on a live failed app call too; never on a live failed built-in, nor on a live app call that worked or still runs", () => {
    const cards: ToolCard[] = [
      { id: "a", name: GMAIL, status: "done", ok: false, output: "401 token expired" },
      { id: "b", name: "write_file", status: "done", ok: false, args: { path: "C:\\work\\out.md" } },
      // v1.352.0 review: the live row's "only a FAILED app step" rule, pinned
      // (offering the link on every app call survived the doer's pins).
      { id: "c", name: "mcp__github__list_issues", status: "done", ok: true, startedAt: 1_000, endedAt: 1_300 },
      { id: "d", name: "mcp__calendar__list_events", status: "running" },
    ];
    render(<LiveToolRows cards={cards} />);
    const links = screen.getAllByRole("link", { name: "Fix in Tools" });
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("title")).toBe("Open Tools to check gmail");
  });

  it("an app step that opens (it has a detail) keeps the link outside its button", () => {
    render(
      <WorkLine
        steps={[{ name: GMAIL, ok: false, ms: 400, detail: { input: '{"query":"x"}', output: "401 token expired" } }]}
        timing={THREE_SECONDS}
      />,
    );
    const rows = openLine();
    const row = rows.getByTestId("work-row");
    expect(row.tagName).toBe("BUTTON");
    const link = rows.getByRole("link", { name: "Fix in Tools" });
    expect(row.contains(link)).toBe(false);
  });
});

/* ===================================================== the vocabulary */

describe("the kinds stay EXACT: never a prefix, never a guess", () => {
  it("an app is mcp__<pack>__<tool> with both parts; anything else is not", () => {
    expect(toolKind(GMAIL)).toBe("app");
    expect(appToolParts(GMAIL)).toEqual({ app: "gmail", tool: "search_messages" });
    // Split at the FIRST "__", as the registry joins them.
    expect(appToolParts("mcp__files__read__all")).toEqual({ app: "files", tool: "read__all" });
    for (const name of ["mcp__gmail__", "mcp____search", "mcp__gmail", "mcp_gmail__x", "mcp__gm ail__x", "MCP__gmail__x"]) {
      expect(toolKind(name), name).toBe("tool");
    }
    expect(appToolParts("mcp__gmail__a\nb")).toBeNull();
  });

  // v1.400.9: fifteen — browser_fill_form (one card for a whole form) joined.
  it("the browser kind is the 15 built-ins by exact name (the approval card's list)", () => {
    expect(BROWSER_TOOLS.size).toBe(15);
    for (const t of BROWSER_TOOLS) {
      expect(toolKind(t), t).toBe("browser");
      expect(isBuiltinBrowserTool(t)).toBe(true);
    }
    // An agent-made custom tool may take any browser_* name: it is a tool.
    expect(toolKind("browser_backup")).toBe("tool");
    expect(toolKind("Browser_click")).toBe("tool");
    expect(browserActionWords("browser_read_page")).toBe("read page");
    expect(browserActionWords("browser_backup")).toBeNull();
  });

  it("neither an app nor a browser step carries a target (their arguments are not described)", () => {
    expect(stepTarget(GMAIL, { query: "from:boss" })).toBeNull();
    expect(stepTarget("browser_navigate", { url: "https://example.com" })).toBeNull();
  });

  it("a tool's name reads as words; an acronym keeps its case", () => {
    expect(appToolNameWords("search_messages")).toBe("search messages");
    expect(appToolNameWords("listOpenIssues")).toBe("list open issues");
    expect(appToolNameWords("get-file")).toBe("get file");
    expect(appToolNameWords("get_PR")).toBe("get PR");
  });

  it("the phrase both lines share", () => {
    expect(stepPhraseText(stepPhrase(GMAIL))).toBe("gmail: search messages");
    expect(stepPhraseText(stepPhrase("browser_click"))).toBe("Used your browser · click");
    // Unchanged: a known tool, an unknown tool, an older step.
    expect(stepPhraseText(stepPhrase("read_file", "harbor.xlsx"))).toBe("Read harbor.xlsx");
    // Readiness 12A (G22, moved on purpose): a registered built-in reads in
    // the run log's words; a custom tool still names itself.
    expect(stepPhraseText(stepPhrase("excel_query"))).toBe("Queried a workbook");
    expect(stepPhraseText(stepPhrase("rename_real_file"))).toBe("Ran rename_real_file");
    expect(stepPhraseText(stepPhrase("read_file"))).toBe("Read · read_file");
  });
});

describe("the approval card's words for an app tool", () => {
  it("'use gmail (search messages)', read off the id; built-ins and unknowns unchanged", () => {
    expect(toolWords(GMAIL)).toBe("use gmail (search messages)");
    expect(toolWords("mcp__github__listOpenIssues")).toBe("use github (list open issues)");
    expect(toolWords("write_file")).toBe("save a file");
    expect(toolWords("browser_click")).toBe("act in a browser tab");
    // Readiness 12A (G24, moved on purpose): every registered built-in has
    // the card's words; a custom tool still has none of its own.
    expect(toolWords("excel_query")).toBe("query a workbook");
    expect(toolWords("rename_real_file")).toBeNull();
    expect(toolWords("mcp__gmail__")).toBeNull();
  });
});
