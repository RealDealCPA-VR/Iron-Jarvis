/**
 * v1.358.0 (wave 8, lane 8C) — chat receipts say one thing once.
 *
 * The post-build re-audit found the chat saying the same things twice or in
 * engine words. Each acceptance below is driven through the REAL component or
 * helper (only the network is stubbed):
 *
 *  (1) ONE RECEIPT. A helper-agent run's settled row carries the facts; the
 *      reply under it (RunResultCard `withRow`) draws no second copy — no
 *      headline, no "answered by", no tool chips, no "Full transcript", no
 *      box — only the files, an honest note, the errors folded, Try again.
 *      The step-by-step log moved into the row (AgentRunReceipt `activity`,
 *      "Step by step"). The card that remains (a run with no such row) says
 *      the shared status table's words ("Done", never "Task complete") and
 *      the row's "Open run". lib/runReceipt pairs a reply with its row.
 *  (2) TOOL WORDS, NEVER IDS (G19). agents/mission.py `tool_line` and
 *      lib/runWords `toolStepLine` say a tool in the same words, held to ONE
 *      fixture (fixtures/tool-lines-v13580.json, written by
 *      tests/test_one_receipt_v13580.py from the real Python).
 *  (3) A CUT-OFF REPLY'S LINE SAYS SO. WorkLine never puts "✓ Answered in N
 *      s" above "The reply was cut off.": it reads "Cut off after N s" under
 *      the warn mark.
 *  (4) rest_call has words: "Called crm", counted "called a web service".
 *  (6) components/onboarding/ConnectDoors uses theme tone tokens.
 *
 * (5) the Sessions armed press, (7) the Build pane's Details fold and the
 * real chat page are pinned in their own files (each needs its own mocks):
 * sessions-armed-delete-v13580, pane-chat-details-v13580,
 * one-receipt-chat-v13580.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const H = vi.hoisted(() => ({ posts: [] as { path: string; body: unknown }[] }));
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status = 500;
  },
  API_BASE: "",
  ijToken: () => "",
  get: async () => ({}),
  post: async (p: string, body: unknown) => {
    H.posts.push({ path: p, body });
    return { reverted: [], skipped: [], considered: 0 };
  },
  put: async () => ({}),
  del: async () => ({}),
}));

import fixture from "./fixtures/tool-lines-v13580.json";
import { RunResultCard, resultHeadline, type RunResult } from "@/components/chat/RunResultCard";
import { AgentRunReceipt } from "@/components/chat/AgentRunReceipt";
import { AgentRunSettled } from "@/components/chat/AgentRunRow";
import { LiveToolRows, WorkLine, workSummary } from "@/components/chat/WorkLine";
import { AGENT_TOOL_NAMES, toolDidWords, toolFailedWords, toolStepLine } from "@/lib/runWords";
import { stepTarget, toolKind } from "@/lib/workTarget";
import { stepCounts, stepPhrase, stepPhraseText } from "@/lib/stepWords";
import { REST_CALL_TOOL, appNameWords, serviceNameWords, toolWords } from "@/lib/toolWords";
import { pairRunReceipts, stampCarriesReceipt } from "@/lib/runReceipt";
import { statusWords } from "@/lib/statusWords";
import type { AgentRunStamp } from "@/lib/agentRun";
import type { ToolCard } from "@/lib/useChatStream";

beforeEach(() => {
  H.posts.length = 0;
});
afterEach(cleanup);

/** Every visible text node (a CLOSED <details> shows only its summary). */
function visibleText(root: Element): string {
  const out: string[] = [];
  const walk = (n: Node) => {
    if (n.nodeType === Node.TEXT_NODE) {
      out.push(n.textContent ?? "");
      return;
    }
    if (n instanceof HTMLDetailsElement && !n.open) {
      const s = n.querySelector("summary");
      if (s) walk(s);
      return;
    }
    n.childNodes.forEach(walk);
  };
  walk(root);
  return out.join(" ");
}

/** A class a calm surface must not draw: a border, a shadow, a card's fill
 *  at rest (a 3 px separator dot's own grey is not a box). */
const BOX = /(^|\s)(border(-|\s|$)|shadow|bg-(white|ink|black|tone|accent))/;

const RESULT: RunResult = {
  found: true,
  session_id: "s1",
  status: "completed",
  task: "find what we decided",
  summary: "",
  steps: 6,
  tools_used: [
    { tool: "history_search", count: 83 },
    { tool: "memory_read", count: 1 },
    { tool: "write_file", count: 1 },
  ],
  tools_failed: [{ tool: "memory_read", count: 1 }],
  files_created: ["brief.md", "notes.md"],
  files_changed: ["plan.xlsx"],
  errors: [{ tool: "memory_read", error: "missing required: layer — memory_read needs [layer, key]; got [key]" }],
  revertable: 2,
  duration_s: 125,
  outcome: "completed_with_failures",
  provider: "claude-cli",
  model: "claude-opus-5-5",
  agent_name: null,
  agent_type: "researcher",
  route_note: "Opus could not be used (unreachable), so Sonnet answered.",
  on_this_network: false,
  activity: [
    { at: "2026-10-19T09:00:01", who: "researcher", session_id: "s1", text: "researcher searched your chats", tone: "ok" },
    { at: "2026-10-19T09:00:02", who: "researcher", session_id: "s1", text: "researcher tried a memory lookup and it failed", tone: "warn" },
  ],
};

const STAMP: AgentRunStamp = {
  session: "s1",
  startedAt: 1_000,
  endedAt: 126_000,
  steps: 6,
  status: "completed",
  outcome: "completed_with_failures",
  provider: "claude-cli",
  model: "claude-opus-5-5",
  tools: [
    { tool: "history_search", count: 83, failed: 0 },
    { tool: "memory_read", count: 1, failed: 1 },
  ],
};

/* ============================================================ (1) one receipt */

describe("(1) the reply under a settled row draws no second copy of the receipt", () => {
  it("withRow: no headline, no 'answered by', no chips, no route note, no link, no box — only the rest", () => {
    const onRetry = vi.fn();
    const { container } = render(<RunResultCard result={RESULT} withRow onRetry={onRetry} />);
    const rest = screen.getByTestId("run-result-rest");
    for (const id of [
      "run-result-headline",
      "run-result-meta",
      "run-result-model",
      "run-result-route-note",
      "run-result-tool",
      "run-result-steps-toggle",
      "run-result-open",
    ]) {
      expect(screen.queryByTestId(id), id).toBeNull();
    }
    const seen = visibleText(container);
    for (const words of ["answered by", "Full transcript", "Open run", "Task complete", "Searched your chats", "Tools"]) {
      expect(seen, words).not.toContain(words);
    }
    // No box anywhere: the rest is quiet lines (a press may fill on hover).
    for (const el of [rest, ...Array.from(rest.querySelectorAll("*"))]) {
      expect(el.getAttribute("class") ?? "", el.outerHTML.slice(0, 80)).not.toMatch(BOX);
    }
    // What the row does NOT say is still here, in the work line's words.
    expect(screen.getByTestId("run-result-files-made").textContent).toBe("Made brief.md, notes.md");
    expect(screen.getByTestId("run-result-files-changed").textContent).toBe("Changed plan.xlsx");
    // The honest note for a run that finished short, in plain words.
    const note = screen.getByTestId("run-result-note");
    expect(note.textContent).toMatch(/Some of the changes this run tried failed/);
    expect(note.className).toContain("text-tone-warn");
    expect(note.textContent).not.toMatch(/session|worklist/i);
    // The errors fold: closed, then the step in words with the raw text one press away.
    expect(screen.queryByTestId("run-result-error")).toBeNull();
    const toggle = screen.getByTestId("run-result-errors-toggle");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const err = screen.getByTestId("run-result-error");
    expect(within(err).getByText("Memory lookup failed")).toBeTruthy();
    expect(screen.getByTestId("run-result-error-raw").textContent).toContain("missing required: layer");
    // The presses stay: Try again and Revert.
    fireEvent.click(screen.getByRole("button", { name: /Try again/ }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /Revert \(2\)/ })).toBeTruthy();
  });

  it("withRow on a run that did nothing: the honest note, said once, never boxed", () => {
    render(<RunResultCard result={{ ...RESULT, tools_used: [], tools_failed: [], errors: [], outcome: null, files_created: [], files_changed: [] }} withRow />);
    const note = screen.getByTestId("run-result-note");
    expect(note.textContent).toMatch(/^No tools ran and no files changed/);
    expect(note.className).not.toMatch(BOX);
  });

  it("the full card (no row above) keeps its facts, in the status table's words and the row's link words", () => {
    render(<RunResultCard result={{ ...RESULT, outcome: "completed", tools_failed: [], errors: [] }} />);
    expect(screen.getByTestId("run-result-headline").textContent).toBe(statusWords("completed").word);
    expect(screen.getByTestId("run-result-headline").textContent).toBe("Done");
    expect(screen.getByTestId("run-result-meta").textContent).toMatch(/^answered by Opus 5\.5/);
    expect(screen.getByTestId("run-result-open").textContent).toBe("Open run");
    expect(screen.queryByText(/Full transcript/)).toBeNull();
    expect(screen.queryByText("Task complete")).toBeNull();
    // The step-by-step fold is the shared one (same ids, same words).
    fireEvent.click(screen.getByTestId("run-result-steps-toggle"));
    expect(within(screen.getByTestId("run-result-activity")).getAllByRole("listitem")).toHaveLength(2);
  });

  it("resultHeadline speaks the one status table for every plain end", () => {
    expect(resultHeadline({ ...RESULT, outcome: "completed" })).toBe(statusWords("completed").word);
    expect(resultHeadline({ ...RESULT, status: "failed", summary: "boom" })).toBe(statusWords("failed").word);
    expect(resultHeadline({ ...RESULT, status: "cancelled" })).toBe(statusWords("cancelled").word);
    expect(resultHeadline(RESULT)).toBe(statusWords("completed", "completed_with_failures").word);
    expect(resultHeadline({ ...RESULT, outcome: null, unanswered_asks: 3 })).toBe(
      statusWords("completed", "needs_you").word,
    );
  });

  it("the settled row opens onto the run's step-by-step log ('Step by step'), no box", () => {
    render(
      <AgentRunSettled name="The researcher" run={STAMP} why="Handing this to the researcher: needs the agent.">
        <AgentRunReceipt run={STAMP} activity={RESULT.activity} />
      </AgentRunSettled>,
    );
    fireEvent.click(screen.getByTestId("agent-run-toggle"));
    const body = screen.getByTestId("agent-run-receipt");
    const toggle = within(body).getByTestId("agent-run-steps-toggle");
    expect(toggle.textContent).toBe("Step by step");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    const rows = within(screen.getByTestId("agent-run-activity")).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual([
      "researcher searched your chats",
      "researcher tried a memory lookup and it failed",
    ]);
    expect(rows[1].getAttribute("data-tone")).toBe("warn");
    for (const el of [body, ...Array.from(body.querySelectorAll("*"))]) {
      expect(el.getAttribute("class") ?? "").not.toMatch(BOX);
    }
  });

  it("CONTROL: a row with no paired log shows no fold (and an old stamp still renders nothing new)", () => {
    const { container } = render(<AgentRunReceipt run={STAMP} />);
    expect(container.querySelector('[data-testid="agent-run-steps-toggle"]')).toBeNull();
    cleanup();
    const old = render(<AgentRunReceipt run={{ session: "s1", startedAt: 1, endedAt: 2, steps: 1 }} />);
    expect(old.container.textContent).toBe("");
  });
});

describe("(1) lib/runReceipt pairs a reply with the settled row above it", () => {
  const handOff = (stamp: Partial<AgentRunStamp> | null) => ({
    role: "assistant",
    content: "",
    escalated: "needs the agent",
    ...(stamp ? { agentRun: { ...STAMP, ...stamp } } : {}),
  });
  const reply = (session = "s1") => ({
    role: "assistant",
    content: "Found it.",
    fromSession: session,
    runResult: { ...RESULT, session_id: session },
  });

  it("a receipt-carrying row pairs with its reply: the reply is under it, the row gets the log", () => {
    const msgs = [{ role: "user", content: "q" }, handOff({}), reply()];
    const pairs = pairRunReceipts(msgs);
    expect(pairs.underRow).toEqual({ 2: true });
    expect(pairs.activityFor[1]).toBe((msgs[2] as { runResult: RunResult }).runResult.activity); // the same reference
  });

  it("CONTROLS: an old stamp, a bare Stop stamp, another run, or a reply before the row keep the full card", () => {
    const old = { session: "s1", startedAt: 1, endedAt: 2, steps: 3 };
    expect(pairRunReceipts([{ role: "assistant", content: "", escalated: "x", agentRun: old }, reply()]).underRow).toEqual({});
    const bareStop = { session: "s1", startedAt: 1, endedAt: 2, steps: null, status: "cancelled" };
    expect(pairRunReceipts([{ role: "assistant", content: "", escalated: "x", agentRun: bareStop }, reply()]).underRow).toEqual({});
    expect(pairRunReceipts([handOff({}), reply("s2")]).underRow).toEqual({});
    expect(pairRunReceipts([reply(), handOff({})]).underRow).toEqual({});
    expect(pairRunReceipts([handOff(null), reply()]).underRow).toEqual({});
    expect(pairRunReceipts(undefined as never).underRow).toEqual({});
  });

  it("stampCarriesReceipt: how it ended AND what served it or what it did", () => {
    expect(stampCarriesReceipt(STAMP)).toBe(true);
    expect(stampCarriesReceipt({ ...STAMP, provider: undefined, model: undefined, tools: [] })).toBe(false);
    expect(stampCarriesReceipt({ ...STAMP, status: undefined })).toBe(false);
    expect(stampCarriesReceipt(null)).toBe(false);
  });
});

/* =============================================== (2) tool words, never ids */

type Fixture = { lines: Record<string, { ok: string; failed: string }>; python_own_words: string[] };
const FIX = fixture as unknown as Fixture;

describe("(2) the run's log says tools in words: TypeScript == Python, one fixture", () => {
  it("every id the Python fallback words reads the same through toolStepLine", () => {
    const ids = Object.keys(FIX.lines);
    expect(ids.length).toBeGreaterThanOrEqual(40); // anti-vacuity
    for (const id of ids) {
      expect(toolStepLine(id, true), id).toBe(FIX.lines[id].ok);
      expect(toolStepLine(id, false), id).toBe(FIX.lines[id].failed);
    }
  });

  it("every agent-side built-in the card words is covered by the fixture (or worded by Python's own table)", () => {
    const covered = new Set([...Object.keys(FIX.lines), ...FIX.python_own_words]);
    for (const name of AGENT_TOOL_NAMES) expect(covered.has(name), name).toBe(true);
  });

  it("the audit's own examples: 'searched your chats', an app as '<app>: <action>'", () => {
    expect(toolStepLine("history_search", true)).toBe("searched your chats");
    expect(toolStepLine("mcp__gmail__search_messages", true)).toBe("used gmail: search messages");
    expect(toolStepLine("mcp__github__listOpenIssues", true)).toBe("used github: list open issues");
    expect(toolStepLine("history_search", true)).not.toMatch(/history search/);
    // The run card's chips and the work line read an app the same way.
    expect(toolDidWords("mcp__github__listOpenIssues", 1)).toBe("Used github: list open issues");
    expect(appNameWords("google_drive")).toBe("google drive");
  });
});

/* ======================================================= (4) rest_call words */

describe("(4) a call to a saved web service reads in words", () => {
  it("lib/workTarget: its own kind, its target the service's NAME only", () => {
    expect(REST_CALL_TOOL).toBe("rest_call");
    expect(toolKind("rest_call")).toBe("service");
    expect(stepTarget("rest_call", { service: "crm (svc_1a2b)", path: "/contacts/42", method: "GET" })).toBe("crm");
    expect(stepTarget("rest_call", { path: "/contacts/42" })).toBeNull();
    // A credential-shaped name is dropped whole, never shown.
    expect(stepTarget("rest_call", { service: "sk-ant-0123456789abcdefghijklmnopqrstu" })).toBeNull();
    expect(serviceNameWords("  crm   (x_1) ")).toBe("crm");
  });

  it("lib/stepWords: 'Called crm', counted 'called a web service' — never 'Ran rest_call' / 'ran 1 tool'", () => {
    expect(stepPhraseText(stepPhrase("rest_call", "crm"))).toBe("Called crm");
    expect(stepPhraseText(stepPhrase("rest_call", null))).toBe("Called a web service");
    expect(stepCounts([{ name: "rest_call", ok: true }])).toEqual(["called a web service"]);
    expect(stepCounts([{ name: "rest_call" }, { name: "rest_call" }])).toEqual(["called your web services 2 times"]);
    expect(stepCounts([{ name: "rest_call" }]).join()).not.toMatch(/tool/);
    // The approval card's words are unchanged.
    expect(toolWords("rest_call")).toBe("call one of your saved web services");
    // The run card says it the same way.
    expect(toolDidWords("rest_call", 1)).toBe("Called a web service");
    expect(toolFailedWords("rest_call", 1)).toBe("One web service call failed");
  });

  it("the live and the folded work line say it in words; the id stays in a title only", () => {
    const card: ToolCard = { id: "c1", name: "rest_call", status: "done", ok: true, args: { service: "crm", path: "/contacts/42" } };
    const { container } = render(<LiveToolRows cards={[card]} />);
    const seen = visibleText(container);
    expect(seen).toContain("Called");
    expect(seen).toContain("crm");
    expect(seen).not.toMatch(/rest_call|Ran rest|\/contacts/);
    cleanup();
    expect(workSummary({ steps: [{ name: "rest_call", ok: true, ms: 300, target: "crm" }], timing: { startedAt: 0, firstTokenAt: 500, endedAt: 2_000 } })).toBe(
      "Worked for 2.0 s · called a web service",
    );
  });
});

/* =================================================== (3) a cut-off reply */

const TIMING = { startedAt: 0, firstTokenAt: 800, endedAt: 2_100 };

describe("(3) a cut-off reply's line says it was cut off, never a check mark", () => {
  it.each(["interrupted", "truncated"] as const)("a plain answer %s reads 'Cut off after 2.1 s' under the warn mark", (cutOff) => {
    render(<WorkLine timing={TIMING} cutOff={cutOff} />);
    expect(screen.getByTestId("work-summary").textContent).toBe("Cut off after 2.1 s");
    expect(screen.queryByTestId("work-line-check")).toBeNull();
    expect(screen.getByTestId("work-line-warn").getAttribute("class")).toContain("text-tone-warn");
    expect(screen.getByTestId("work-line").getAttribute("data-outcome")).toBe("cut-off");
    expect(screen.queryByText(/Answered in/)).toBeNull();
  });

  it("CONTROL: a reply that ended on its own still reads '✓ Answered in 2.1 s'", () => {
    render(<WorkLine timing={TIMING} />);
    expect(screen.getByTestId("work-summary").textContent).toBe("Answered in 2.1 s");
    expect(screen.getByTestId("work-line-check")).toBeTruthy();
    expect(screen.queryByTestId("work-line-warn")).toBeNull();
  });

  it("a folded line with steps ends '· cut off' in the warn tone, under the warn mark", () => {
    render(<WorkLine steps={[{ name: "read_file", ok: true, ms: 400, target: "a.md" }]} timing={TIMING} cutOff="interrupted" />);
    expect(screen.getByTestId("work-summary").textContent).toBe("Worked for 2.1 s · read 1 file · cut off");
    expect(screen.getByTestId("work-summary-cut").className).toContain("text-tone-warn");
    expect(screen.queryByTestId("work-line-check")).toBeNull();
    expect(screen.getByTestId("work-line-warn")).toBeTruthy();
  });

  it("a cut-off line with a failed step keeps both warn pieces", () => {
    render(
      <WorkLine
        steps={[{ name: "read_file", ok: true, ms: 400 }, { name: "web_fetch", ok: false, ms: 300 }]}
        timing={TIMING}
        cutOff="truncated"
      />,
    );
    expect(screen.getByTestId("work-summary").textContent).toBe("Worked for 2.1 s · read 1 file, 1 failed · cut off");
    expect(screen.getByTestId("work-summary-failed").textContent).toBe("1 failed");
    expect(screen.getByTestId("work-summary-cut").textContent).toBe("cut off");
  });
});

/* ========================================== (6) ConnectDoors theme tokens */

describe("(6) the first-run model doors use theme tone tokens, never literal hues", () => {
  const SRC = readFileSync(path.join(__dirname, "..", "components", "onboarding", "ConnectDoors.tsx"), "utf8").replace(
    /\r\n/g,
    "\n",
  );
  it("no literal rose / emerald / red / green text, fill or border classes", () => {
    expect(SRC).not.toMatch(/\b(text|bg|border)-(rose|emerald|red|green)-\d/);
  });
  it("the starter model's error line reads in the danger token (anti-vacuity: the line exists)", () => {
    const at = SRC.indexOf("{starter.error && (");
    expect(at).toBeGreaterThan(-1);
    expect(SRC.slice(at, at + 200)).toContain('className="mt-1 block text-tone-danger"');
  });
});
