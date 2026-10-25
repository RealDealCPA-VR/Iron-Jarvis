/**
 * Readiness 12A (G22/G24) — every registered built-in tool reads in words on
 * the approval card and the chat's work line, from ONE source.
 *
 * Before: the card read "The assistant wants to run assign_work" and the work
 * line "Ran guide_search" (counted "ran 1 tool") for every built-in their hand
 * tables did not list, while the run log said "searched the Iron Jarvis
 * guide". Now agents/mission.card_words and step_line_words are the source,
 * generated into lib/builtinToolWords (scripts/gen_tool_words.py).
 *
 * The contract fixture (fixtures/tool-words-everywhere-v1387.json) is written
 * by tests/test_tool_words_everywhere_v1387.py from the REAL registry and
 * the real Python words; here EVERY built-in in it goes through the real
 * `toolWords`, the real ApprovalCard and the real work line.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    API_BASE: "",
    ijToken: () => "",
    sseUrl: (p: string) => p,
    get: () => Promise.resolve({}),
    post: () => Promise.resolve({ ok: true }),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { toolWords } from "@/lib/toolWords";
import { askPhrase } from "@/lib/approvalWords";
import { kindWords, stepCounts, stepPhrase, stepPhraseText } from "@/lib/stepWords";
import { toolKind } from "@/lib/workTarget";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { LiveToolRows } from "@/components/chat/WorkLine";

const FIX = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "tool-words-everywhere-v1387.json"), "utf-8").replace(/\r\n/g, "\n"),
) as { builtins: string[]; card: Record<string, string>; line: Record<string, [string, string]> };

const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

afterEach(() => cleanup());

describe("the approval card says what every built-in does (G24)", () => {
  it("anti-vacuity: the fixture is the real registry", () => {
    expect(FIX.builtins.length).toBeGreaterThanOrEqual(150);
    for (const id of ["assign_work", "guide_search", "pack_restart", "history_search", "remember_preference"]) {
      expect(FIX.builtins).toContain(id);
    }
  });

  it("toolWords and the card's lead say the daemon's words for EVERY built-in, never its id", () => {
    for (const id of FIX.builtins) {
      expect(toolWords(id), id).toBe(FIX.card[id]);
      expect(askPhrase(id), id).toBe(FIX.card[id]);
      expect(askPhrase(id), id).not.toMatch(/_/);
    }
  });

  it("the real card reads them: 'The assistant wants to queue a task for a teammate'", () => {
    for (const id of FIX.builtins) {
      render(<ApprovalCard approval={{ id: `apr_${id}`, callId: "c1", tool: id, args: {} }} />);
      const card = screen.getByTestId("chat-approval-card");
      const text = (card.textContent ?? "").replace(/\s+/g, " ");
      expect(text, id).toContain(`The assistant wants to ${FIX.card[id]}`);
      expect(text, id).not.toContain(`wants to run ${id}`);
      cleanup();
    }
  });

  it("a custom tool (not a built-in) still names itself — nothing invented", () => {
    expect(toolWords("rename_real_file")).toBeNull();
    expect(askPhrase("rename_real_file")).toBe("run rename_real_file");
  });
});

describe("the chat's work line says what every built-in did (G22)", () => {
  it("no built-in reads 'Ran <its id>' or leads with its id, done or running", () => {
    for (const id of FIX.builtins) {
      const text = stepPhraseText(stepPhrase(id));
      expect(text, id).not.toBe(`Ran ${id}`);
      // A kind's row with no target keeps its id as the quiet word after the
      // dot ("Made · convert_document", v1.329.0) — its WORDS lead.
      expect(text.split(" · ")[0], id).not.toMatch(/_/);
      const kind = toolKind(id);
      const running = kindWords(kind, id, { subject: false, running: true });
      expect(running, id).not.toMatch(/_/);
      // The catch-all's bare "Running <id>" is gone for every built-in.
      if (kind === "tool") expect(running, id).not.toBe("Running");
    }
  });

  it("a built-in the line does not word by kind reads the run log's own words", () => {
    const ids = Object.keys(FIX.line);
    expect(ids.length).toBeGreaterThan(100);
    for (const id of ids) {
      const [done, doing] = FIX.line[id];
      expect(toolKind(id), id).toBe("tool");
      expect(stepPhraseText(stepPhrase(id)), id).toBe(done);
      expect(kindWords("tool", id, { subject: false, running: true }), id).toBe(doing);
      // Counted as itself, never "ran 1 tool".
      expect(stepCounts([{ name: id }, { name: id }]), id).toEqual([`${lowerFirst(done)} 2 times`]);
      expect(stepCounts([{ name: id }]), id).toEqual([lowerFirst(done)]);
    }
  });

  it("the live work line: 'Searching the Iron Jarvis guide' while it runs, counted in words", () => {
    render(
      <LiveToolRows
        cards={[
          { id: "a", name: "history_search", status: "done", ok: true, args: {}, startedAt: 1, endedAt: 2 },
          { id: "b", name: "guide_search", status: "running", args: {}, startedAt: 3 },
        ]}
        since={1}
      />,
    );
    const rows = screen.getByTestId("live-tool-rows");
    expect(rows.textContent).toContain("Searched your chats");
    expect(rows.textContent).toContain("Searching the Iron Jarvis guide");
    expect(screen.getByTestId("live-work-words").textContent).toMatch(/searched your chats$/);
    expect(rows.textContent).not.toMatch(/history_search|guide_search|Ran |ran 1 tool/);
  });

  it("nothing else changed: a custom tool still reads 'Ran <its id>' and counts as a tool", () => {
    expect(stepPhraseText(stepPhrase("rename_real_file"))).toBe("Ran rename_real_file");
    expect(stepCounts([{ name: "rename_real_file" }])).toEqual(["ran 1 tool"]);
    expect(stepPhraseText(stepPhrase("read_file", "a.xlsx"))).toBe("Read a.xlsx");
  });
});
