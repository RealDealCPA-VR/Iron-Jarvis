/**
 * v1.348.0, polish wave A, lane L2: see the change before approving it.
 *
 * An approval card used to print every non-text argument as raw
 * JSON.stringify output. Now (ideas from 21st.dev agent-elements' Bash and
 * Edit approval footers, MIT, no code taken) the card shows what WILL HAPPEN,
 * in a quiet block inside the same card:
 *  - a file edit (edit_file's old/new, the old_string/new_string spelling,
 *    a MultiEdit `edits` list, docx_edit's replace operations) is a compact
 *    diff through lib/diff.ts, capped with "Show all N lines";
 *  - a file write shows the new text's first lines;
 *  - a command (and pane_send's typed line) is one "$ <command>" line,
 *    verbatim, whole, monospace, no box;
 *  - any other argument is a tidy `key: value` line: nested values
 *    summarised, long text cut with an ellipsis and "Show in full", a path
 *    never cut.
 * The card's buttons, words, ids and the dock's keys are unchanged; the
 * preview draws only the REDACTED arguments the card was handed.
 *
 * Driven through the REAL ApprovalCard / DockAsk with the real helpers; only
 * the daemon POST is mocked.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, API_BASE: "http://localhost", ijToken: () => "", post: H.post };
});

import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { DockAsk } from "@/components/chat/DockAsk";
import {
  approvalPreview,
  argEntry,
  clipText,
  exampleValue,
  linesOf,
  PREVIEW_LINES,
  VALUE_CHARS,
} from "@/lib/approvalPreview";
import type { PendingApproval } from "@/lib/useChatStream";

const card = (approval: Omit<PendingApproval, "callId"> & { callId?: string }) =>
  render(<ApprovalCard approval={{ callId: "c1", ...approval }} />);

const preview = () => screen.getByTestId("approval-preview");
const rowsOf = (el: HTMLElement, kind?: string) =>
  Array.from(el.querySelectorAll<HTMLElement>(kind ? `[data-kind="${kind}"]` : "[data-kind]"));
const classes = (el: Element) => (el.getAttribute("class") ?? "").split(/\s+/);

const numbered = (n: number, prefix = "line") =>
  Array.from({ length: n }, (_, i) => `${prefix} ${i + 1}`).join("\n");

const decisionButtons = () => {
  expect(screen.getByRole("button", { name: "Allow once" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Allow for this conversation" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Deny" })).toBeTruthy();
};

let fetchSpy: ReturnType<typeof vi.fn>;
beforeEach(() => {
  H.post.mockReset();
  H.post.mockResolvedValue({});
  fetchSpy = vi.fn(async () => {
    throw new Error("the approval preview must never fetch");
  });
  vi.stubGlobal("fetch", fetchSpy);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/* ------------------------------------------------------------ file edits */

describe("a file edit shows the change as a compact diff", () => {
  it("edit_file: path line, then removed and added rows with +/- counts, never raw JSON", () => {
    card({
      id: "a1",
      tool: "edit_file",
      args: { path: "notes/todo.md", old: "buy milk\nwalk dog", new: "buy oat milk\nwalk dog" },
    });
    const p = preview();
    expect(p.getAttribute("data-preview")).toBe("edit");
    expect(p.textContent).toContain("path: notes/todo.md");
    const diff = screen.getByTestId("approval-diff");
    expect(rowsOf(diff, "removed").map((r) => r.textContent)).toEqual(["−removed: buy milk"]);
    expect(rowsOf(diff, "added").map((r) => r.textContent)).toEqual(["+added: buy oat milk"]);
    expect(rowsOf(diff, "same").map((r) => r.textContent?.trim())).toEqual(["walk dog"]);
    expect(screen.getByTestId("approval-diff-counts").textContent).toBe("+1 −1");
    // The old/new arguments are not ALSO printed as key: value lines.
    expect(p.textContent).not.toMatch(/\bold:/);
    expect(p.textContent).not.toMatch(/\bnew:/);
    decisionButtons();
  });

  it("the old_string/new_string spelling diffs too (file_path stays a line)", () => {
    card({
      id: "a2",
      tool: "Edit",
      args: { file_path: "src/app.ts", old_string: "const a = 1;", new_string: "const a = 2;" },
    });
    expect(preview().getAttribute("data-preview")).toBe("edit");
    expect(preview().textContent).toContain("file_path: src/app.ts");
    expect(screen.getByTestId("approval-diff-counts").textContent).toBe("+1 −1");
  });

  it("a long change is capped at PREVIEW_LINES with 'Show all N lines', which opens and closes it", () => {
    const before = numbered(10, "old");
    const after = numbered(10, "new");
    card({ id: "a3", tool: "edit_file", args: { path: "big.txt", old: before, new: after } });
    const diff = screen.getByTestId("approval-diff");
    expect(rowsOf(diff)).toHaveLength(PREVIEW_LINES);
    const more = screen.getByTestId("approval-diff-more");
    expect(more.tagName).toBe("BUTTON");
    expect(more.textContent).toBe("Show all 20 lines");
    expect(more.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(more);
    expect(rowsOf(diff)).toHaveLength(20);
    expect(more.textContent).toBe("Show fewer lines");
    expect(more.getAttribute("aria-expanded")).toBe("true");
    expect(document.getElementById(more.getAttribute("aria-controls") ?? "")).not.toBeNull();
    fireEvent.click(more);
    expect(rowsOf(diff)).toHaveLength(PREVIEW_LINES);
    // Opening the lines is not a decision.
    expect(H.post).not.toHaveBeenCalled();
  });

  it("a MultiEdit-style edits list is one diff with a quiet gap between the swaps", () => {
    card({
      id: "a4",
      tool: "MultiEdit",
      args: {
        file_path: "a.py",
        edits: [
          { old_string: "x = 1", new_string: "x = 2" },
          { old_string: "y = 1", new_string: "y = 3" },
        ],
      },
    });
    const diff = screen.getByTestId("approval-diff");
    expect(rowsOf(diff, "gap")).toHaveLength(1);
    expect(rowsOf(diff, "added").map((r) => r.textContent)).toEqual(["+added: x = 2", "+added: y = 3"]);
    expect(screen.getByTestId("approval-diff-counts").textContent).toBe("+2 −2");
    expect(preview().textContent).not.toContain("old_string");
  });

  it("docx_edit: each replace is a diff, any other operation one line of words", () => {
    card({
      id: "a5",
      tool: "docx_edit",
      args: {
        path: "Letter.docx",
        operations: [
          { op: "replace", find: "Dear Sir", replace: "Dear Ms Rivera" },
          { op: "set_cell", table: 1, row: 2, column: 3, value: "42" },
        ],
      },
    });
    const diff = screen.getByTestId("approval-diff");
    expect(rowsOf(diff, "removed")[0].textContent).toContain("Dear Sir");
    expect(rowsOf(diff, "added")[0].textContent).toContain("Dear Ms Rivera");
    expect(rowsOf(diff, "note")[0].textContent).toBe("set_cell: table: 1, row: 2, column: 3, value: 42");
    expect(diff.textContent).not.toContain("{");
  });
});

/* ------------------------------------------------------------ file writes */

describe("a file write shows the new text's first lines", () => {
  it("write_file: the count, the first PREVIEW_LINES lines, and 'Show all N lines'", () => {
    card({ id: "w1", tool: "write_file", args: { path: "report.md", content: numbered(30) + "\n" } });
    expect(preview().getAttribute("data-preview")).toBe("write");
    const block = screen.getByTestId("approval-content");
    expect(block.textContent).toContain("content: 30 lines");
    const pre = block.querySelector("pre")!;
    expect(pre.textContent).toContain("line 12");
    expect(pre.textContent).not.toContain("line 13");
    const more = screen.getByTestId("approval-content-more");
    expect(more.textContent).toBe("Show all 30 lines");
    fireEvent.click(more);
    expect(pre.textContent).toContain("line 30");
    expect(H.post).not.toHaveBeenCalled();
  });

  it("a short write shows every line and no toggle; an empty write says (empty)", () => {
    const { unmount } = card({ id: "w2", tool: "write_file", args: { path: "a.txt", content: "hi\nthere" } });
    expect(screen.getByTestId("approval-content").textContent).toContain("content: 2 lines");
    expect(screen.queryByTestId("approval-content-more")).toBeNull();
    unmount();
    card({ id: "w3", tool: "write_file", args: { path: "a.txt", content: "" } });
    expect(screen.getByTestId("approval-content").textContent).toContain("content: (empty)");
  });

  it("CONTROL: write_file with no content is just its path line (wrapped in full, as v1.314.0 pinned)", () => {
    const LONG =
      "C:/Users/Someone/Documents/Clients/2026/Northwind Traders Holdings LLC/Quarterly Reports/Q3/Reconciliation working papers - final.xlsx";
    card({ id: "w4", tool: "write_file", args: { path: LONG } });
    expect(preview().getAttribute("data-preview")).toBe("args");
    const line = screen.getByText((_, el) => !!el && el.tagName === "P" && (el.textContent ?? "").includes(LONG));
    expect(classes(line)).not.toContain("truncate");
  });
});

/* ------------------------------------------------------------ commands */

describe("a command is one terminal-looking line", () => {
  it("shell: '$ <command>' in monospace, verbatim, wrapping, and no colour box", () => {
    card({ id: "s1", tool: "shell", args: { command: "git status --short" } });
    expect(preview().getAttribute("data-preview")).toBe("command");
    const pre = screen.getByText("git status --short");
    expect(pre.tagName).toBe("PRE");
    expect(pre.getAttribute("data-testid")).toBe("approval-command");
    expect(pre.textContent).toBe("$ git status --short");
    const cls = classes(pre);
    expect(cls).toContain("font-mono");
    expect(cls).toContain("whitespace-pre-wrap");
    // No box: no border, no background tint, no rounded panel, no padding box.
    expect(cls.filter((c) => /^(border|bg-|rounded|shadow|px-|py-|p-)/.test(c))).toEqual([]);
    // The "$" is for the eye; a screen reader hears the command.
    const dollar = pre.querySelector('[aria-hidden="true"]');
    expect(dollar?.textContent?.trim()).toBe("$");
  });

  it("a multi-line command is shown whole, never cut", () => {
    const cmd = numbered(25, "echo");
    card({ id: "s2", tool: "shell", args: { command: cmd } });
    expect(screen.getByTestId("approval-command").textContent).toBe(`$ ${cmd}`);
    expect(screen.queryByTestId("approval-args-more")).toBeNull();
  });

  it("pane_send's typed line is a command too; its pane rides a key: value line", () => {
    card({ id: "s3", tool: "pane_send", args: { pane: "build-1", text: "npm test", wait: true } });
    expect(preview().getAttribute("data-preview")).toBe("command");
    expect(screen.getByTestId("approval-command").textContent).toBe("$ npm test");
    expect(preview().textContent).toContain("pane: build-1");
    expect(preview().textContent).toContain("wait: true");
  });

  it("repl / run_code: the code verbatim and whole, no '$'", () => {
    card({ id: "s4", tool: "run_code", args: { language: "python", code: "print(1)\nprint(2)" } });
    expect(preview().getAttribute("data-preview")).toBe("code");
    expect(screen.getByTestId("approval-code").textContent).toBe("print(1)\nprint(2)");
    expect(preview().textContent).toContain("language: python");
  });
});

/* ------------------------------------------------------------ other tools */

describe("any other tool shows tidy key: value lines", () => {
  it("nested values are summarised in words, never raw JSON", () => {
    card({
      id: "o1",
      tool: "workflow_create",
      args: {
        name: "Nightly",
        steps: [{ kind: "agent" }, { kind: "tool" }],
        options: { retries: 2, notify: { phone: true }, owner: "me" },
        tags: ["tax", "monthly"],
        limits: { max: 3, min: 1 },
        count: 4,
        dry_run: false,
      },
    });
    const text = preview().textContent ?? "";
    expect(text).toContain("steps: 2 items");
    expect(text).toContain("options: 3 fields: retries, notify, owner");
    expect(text).toContain("tags: tax, monthly");
    expect(text).toContain("limits: max: 3, min: 1");
    expect(text).toContain("count: 4");
    expect(text).toContain("dry_run: false");
    expect(text).not.toContain("{");
    expect(text).not.toContain("[");
    expect(text).not.toContain('"');
  });

  it("a long text is cut with an ellipsis; 'Show in full' shows all of it (and the JSON of a summary)", () => {
    const body = "word ".repeat(120).trim();
    card({ id: "o2", tool: "email", args: { to: "a@b.c", body, meta: { a: { b: 1 } } } });
    const text = () => preview().textContent ?? "";
    expect(text()).toContain("…");
    expect(text()).not.toContain(body);
    expect(text()).toContain("meta: 1 field: a");
    const more = screen.getByTestId("approval-args-more");
    expect(more.textContent).toBe("Show in full");
    fireEvent.click(more);
    expect(text()).toContain(body);
    expect(text()).toContain('"b": 1');
    expect(more.textContent).toBe("Show less");
    expect(H.post).not.toHaveBeenCalled();
  });

  it("a path is never cut, however long", () => {
    const path = "C:/" + "deep/".repeat(80) + "file.txt";
    expect(path.length).toBeGreaterThan(VALUE_CHARS);
    card({ id: "o3", tool: "rename_file", args: { source: path, dest: path + ".bak" } });
    expect(preview().textContent).toContain(path + ".bak");
    expect(screen.queryByTestId("approval-args-more")).toBeNull();
  });

  it("an empty value says (empty); the REDACTED value is drawn as handed over and nothing is fetched", () => {
    card({ id: "o4", tool: "browser_type", args: { text: "[redacted]", note: "", gone: null } });
    const text = preview().textContent ?? "";
    expect(text).toContain("text: [redacted]");
    expect(text).toContain("note: (empty)");
    expect(text).toContain("gone: (empty)");
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(H.post).not.toHaveBeenCalled();
  });

  it("a batch example summarises a nested value instead of printing JSON", () => {
    card({
      id: "o5",
      tool: "rename_file",
      count: 3,
      examples: [{ source: "1.pdf", opts: { a: 1, b: { c: 2 } } }, { source: "2.pdf" }],
    });
    const card0 = screen.getByTestId("chat-approval-card");
    expect(card0.textContent).toContain("source: 1.pdf · opts: 2 fields: a, b");
    expect(card0.textContent).not.toContain("{");
    expect(screen.queryByTestId("approval-preview")).toBeNull();
    expect(screen.getByRole("button", { name: "Allow these 3" })).toBeTruthy();
  });
});

/* ------------------------------------------------------------ calm + keys */

describe("the preview keeps the calm card and its keys", () => {
  it("no border or shadow anywhere in the preview, every transition stops under reduced motion", () => {
    card({ id: "c1", tool: "edit_file", args: { path: "x", old: numbered(9, "a"), new: numbered(9, "b") } });
    const els = [preview(), ...Array.from(preview().querySelectorAll("*"))];
    for (const el of els) {
      const cls = classes(el);
      expect(cls.filter((c) => /^(border|shadow)/.test(c)), el.outerHTML.slice(0, 80)).toEqual([]);
      if (cls.some((c) => c.startsWith("transition"))) expect(cls).toContain("motion-reduce:transition-none");
      // Whole pixels only.
      expect(cls.filter((c) => /^text-\[\d+\.\d+px\]$/.test(c))).toEqual([]);
    }
    // The decline still reads as a warning colour (unchanged card).
    expect(screen.getByRole("button", { name: "Deny" }).className).toContain("text-tone-danger");
  });

  it("in the dock: Enter on 'Show all' opens the lines and never allows; Enter on the card still allows once", async () => {
    render(
      <DockAsk
        asks={[
          {
            kind: "approval",
            id: "d1",
            approval: { id: "d1", callId: "c1", tool: "edit_file", args: { path: "x", old: numbered(9, "a"), new: numbered(9, "b") } },
          },
        ]}
        onAnswerElicitation={vi.fn(async () => ({ ok: true }) as const)}
        onDecideSampling={vi.fn(async () => true)}
        armDelayMs={0}
      />,
    );
    const more = screen.getByTestId("approval-diff-more");
    more.focus();
    fireEvent.keyDown(more, { key: "Enter" });
    await new Promise((r) => setTimeout(r, 20));
    expect(H.post).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByTestId("chat-approval-card"), { key: "Enter" });
    await waitFor(() =>
      expect(H.post.mock.calls.filter((c) => c[0] === "/chat/approvals/d1").map((c) => c[1])).toEqual([
        { decision: "once" },
      ]),
    );
  });

  it("every answer and the card's words stay for each preview kind", () => {
    for (const args of [
      { path: "a", old: "1", new: "2" },
      { path: "a", content: "x" },
      { command: "ls" },
      { code: "1" },
      { q: { a: 1 } },
    ]) {
      const { unmount } = card({ id: "k", tool: "edit_file", args });
      const c = screen.getByTestId("chat-approval-card");
      expect(c.getAttribute("role")).toBe("alertdialog");
      expect(c.getAttribute("aria-label")).toBe("Approve edit_file?");
      expect(within(c).getByTestId("approval-waiting")).toBeTruthy();
      decisionButtons();
      expect(c.textContent).not.toContain("—");
      unmount();
    }
  });
});

/* ------------------------------------------------------------ pure helpers */

describe("lib/approvalPreview (pure)", () => {
  it("classifies by the arguments' shape, in their own order", () => {
    expect(approvalPreview("edit_file", { path: "p", old: "a", new: "b" }).parts.map((p) => p.type)).toEqual([
      "arg",
      "diff",
    ]);
    expect(approvalPreview("write_document", { path: "p", content: "x" }).kind).toBe("write");
    // write_document with rows (not text) stays tidy lines.
    expect(approvalPreview("write_document", { path: "p.xlsx", content: [[1, 2]] }).kind).toBe("args");
    expect(approvalPreview("shell", { command: "dir" }).kind).toBe("command");
    expect(approvalPreview("repl", { code: "x" }).kind).toBe("code");
    expect(approvalPreview("pane_send", { pane: "p", text: "ls" }).kind).toBe("command");
    // A non-terminal tool's `text` is an ordinary argument.
    expect(approvalPreview("notify", { text: "hi" }).kind).toBe("args");
  });

  it("linesOf: empty is no lines; one trailing newline adds no empty line; CRLF splits", () => {
    expect(linesOf("")).toEqual([]);
    expect(linesOf("a\nb\n")).toEqual(["a", "b"]);
    expect(linesOf("a\r\nb")).toEqual(["a", "b"]);
  });

  it("clipText cuts by characters and by lines, with one ellipsis", () => {
    expect(clipText("short")).toBe("short");
    expect(clipText("x".repeat(VALUE_CHARS + 50))).toBe("x".repeat(VALUE_CHARS) + "…");
    expect(clipText("1\n2\n3\n4\n5\n6")).toBe("1\n2\n3\n4…");
  });

  it("argEntry / exampleValue speak in words", () => {
    expect(argEntry("k", [{ a: 1 }]).short).toBe("1 item");
    expect(argEntry("k", { a: { b: 1 }, c: 2 }).short).toBe("2 fields: a, c");
    expect(argEntry("k", {}).empty).toBe(true);
    expect(argEntry("k", []).empty).toBe(true);
    expect(argEntry("path", "y".repeat(500)).cut).toBe(false);
    expect(argEntry("body", "y".repeat(500)).cut).toBe(true);
    expect(exampleValue("a/b.pdf")).toBe("a/b.pdf");
    expect(exampleValue(null)).toBe("(empty)");
  });
});
