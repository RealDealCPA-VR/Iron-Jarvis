/**
 * v1.352.0 integrate — wave 2's seams, fed by what the REAL daemon sends.
 *
 * `fixtures/receipt-seams-v13520.json` is written by
 * `tests/test_receipt_seams_v13520.py` from the real app factory (regenerate
 * with IJ_REGEN_RECEIPT_SEAMS=1). This file feeds the SAME data through the
 * real dashboard pieces, so a shape change on either side fails one of them.
 *
 * (a) THE RECEIPT ON THE HELPER AGENT. Wave 1 (G18) put provider / model /
 *     agent_name / route_note / on_this_network / activity on
 *     GET /sessions/{id}/result. Lane 2A's card (RunResultCard) and settled
 *     row (lib/agentRun stamp -> AgentRunSettled + AgentRunReceipt) render
 *     each: who ran it ("Your tax-reader agent worked for 3 min · part
 *     failed"), on what and WHERE ("answered by Claude Sonnet 4.6 · cloud",
 *     "Ran on llama3.2 · local"), the route note, and what it did, step by
 *     step, in the mission screen's words ("needed your OK to run a command").
 *     The status words are the ONE table's (lib/statusWords), and that table
 *     covers every value wave 1 stores: the session statuses and outcomes,
 *     and every schedule `last_status` scheduling/run_outcome.py writes.
 * (b) ONE APPROVAL VOCABULARY. One real ask (a file save) as each surface
 *     receives it: the stream frame through the REAL useChatStream (the chat
 *     card in the dock, a Build pane), the bell's listing row, the mission
 *     view's coordinator, a project's paused task (approval.requested). Every
 *     surface answers with the same words (lib/approvalWords: Allow once ·
 *     Allow for this chat|run · Deny) and the same undo line. The session
 *     page is pinned in approval-session-page-v13520.test.tsx (its own mocks).
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: unknown }>,
  events: [] as Array<Record<string, unknown>>,
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    cancelled = false;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    API_BASE: "",
    ijToken: () => "",
    sseUrl: (p: string) => p,
    flattenDetail: (d: unknown) => String(d ?? ""),
    get: (p: string) => Promise.resolve(JSON.parse(JSON.stringify(H.api[p] ?? {}))),
    post: (p: string, body?: unknown) => {
      H.posts.push({ path: p, body });
      return Promise.resolve({ ok: true });
    },
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: H.events, connected: true }) }));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: vi.fn(),
  }),
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import fixture from "./fixtures/receipt-seams-v13520.json";
import { __resetApiCache } from "@/lib/apiCache";
import { RunResultCard, type RunResult } from "@/components/chat/RunResultCard";
import { AgentRunSettled } from "@/components/chat/AgentRunRow";
import { AgentRunReceipt } from "@/components/chat/AgentRunReceipt";
import { readAgentRun, settleHandOff, settledRunName } from "@/lib/agentRun";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { DockAsk } from "@/components/chat/DockAsk";
import { PaneAsk } from "@/components/terminal/PaneAsk";
import { NotificationBell } from "@/components/NotificationBell";
import { MissionOutput } from "@/components/agents/mission/MissionOutput";
import { ProjectApprovals } from "@/components/project/ProjectSurfaces";
import { collectDockAsks } from "@/lib/dockAsk";
import { APPROVAL_LABELS, undoLine } from "@/lib/approvalWords";
import { decodeSSE, useChatStream } from "@/lib/useChatStream";
import { scheduleRunWords, statusWords } from "@/lib/statusWords";
import { scheduleStatusWords } from "@/lib/scheduleOutcome";
import type { MissionView } from "@/lib/mission";
import type { MissionLive, MissionLiveStore } from "@/lib/useMission";

const ROOT = path.join(__dirname, "..", "..");
const read = (rel: string) => readFileSync(path.join(ROOT, rel), "utf8").replace(/\r\n/g, "\n");
const folded = (s: string | null | undefined) => (s ?? "").replace(/\s+/g, " ").trim();
const buttonNames = (el: HTMLElement) => within(el).getAllByRole("button").map((b) => folded(b.textContent));

beforeEach(() => {
  H.api = {};
  H.posts = [];
  H.events = [];
  __resetApiCache();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

type Case = (typeof fixture.receipts)["cloud"];
const CASES: Array<[string, Case, "cloud" | "local", string]> = [
  ["a cloud model", fixture.receipts.cloud, "cloud", "Sonnet 4.6"],
  ["a local model", fixture.receipts.local, "local", "llama3.2"],
];

/* ============================================ (a) the receipt on the helper */

describe("(a) the helper agent's card reads the wave-1 receipt the daemon sends", () => {
  it.each(CASES)("%s: who, where, the route note and what it did", async (_n, c, where) => {
    const result = c.result as unknown as RunResult;
    render(<RunResultCard result={result} />);
    // How it ended: the ledger's verdict (a completed run whose write failed).
    expect(screen.getByTestId("run-result-headline").textContent).toBe("Finished with failures");
    // Who served it, in the reply receipt's words, and WHERE (G21).
    const meta = screen.getByTestId("run-result-meta");
    expect(folded(meta.textContent)).toMatch(/^answered by \S/);
    expect(screen.getByTestId("run-result-where").textContent).toBe(where);
    // The route note: the run's model failed over (the mission screen's words).
    expect(screen.getByTestId("run-result-route-note").textContent).toBe(c.result.route_note);
    // What it did, step by step, folded behind one quiet toggle.
    expect(screen.queryByTestId("run-result-activity")).toBeNull();
    fireEvent.click(screen.getByTestId("run-result-steps-toggle"));
    const rows = within(screen.getByTestId("run-result-activity")).getAllByRole("listitem");
    expect(rows.map((r) => folded(r.textContent))).toEqual(c.result.activity.map((a) => a.text));
    expect(rows.map((r) => r.getAttribute("data-tone"))).toEqual(c.result.activity.map((a) => a.tone));
    // One vocabulary: the ask row speaks the approval card's words.
    expect(rows.map((r) => r.textContent)).toContain("tax-reader needed your OK to run a command");
  });

  it.each(CASES)("%s: the line the daemon appended carries the SAME receipt", (_n, c) => {
    expect(c.line.receipt).toEqual(c.result);
    expect(c.line.sessionId).toBe(c.result.session_id);
  });

  it.each(CASES)("%s: the settled row names who ran it, how it ended and where", (_n, c, where, model) => {
    // A hand-off line that named nobody (the default agent): the receipt does.
    const msgs = [{ role: "assistant", content: "", escalated: "needs files", awaitingSession: c.result.session_id }];
    const settled = settleHandOff(msgs, c.result.session_id, c.row, c.result.steps, null, c.line.receipt);
    const run = readAgentRun(settled[0]);
    expect(run).not.toBeNull();
    expect(run!.agent).toBe("custom:tax-reader");
    expect(run!.onThisNetwork).toBe(where === "local");
    render(
      <AgentRunSettled name={settledRunName(undefined, run!)} run={run!} why="Handing this over.">
        <AgentRunReceipt run={run!} />
      </AgentRunSettled>,
    );
    // ONE status table: the ledger's "completed_with_failures" reads "part
    // failed" here exactly as "Part failed" reads on the Sessions page.
    expect(screen.getByTestId("agent-run-words").textContent).toBe("Your tax-reader agent worked for 3 min · part failed");
    fireEvent.click(screen.getByTestId("agent-run-toggle"));
    const modelRow = screen.getByTestId("agent-run-model");
    // "Ran on <model>" · "<where>" (the WorkRow's detail, its title the
    // receipt's own where sentence).
    expect(within(modelRow).getByText(`Ran on ${model}`)).toBeTruthy();
    const whereWord = within(modelRow).getByText(where);
    expect(whereWord.getAttribute("title")).toMatch(where === "local" ? /your own hardware/ : /left your network/);
    expect(screen.getByTestId("agent-run-route-note").textContent).toBe(c.result.route_note);
  });

  it("an older receipt (no where, no log, no who) shows nothing new", () => {
    const old = { ...(fixture.receipts.cloud.result as unknown as RunResult) };
    delete (old as Partial<RunResult>).on_this_network;
    delete (old as Partial<RunResult>).activity;
    render(<RunResultCard result={old} />);
    expect(screen.queryByTestId("run-result-where")).toBeNull();
    expect(screen.queryByTestId("run-result-steps-toggle")).toBeNull();
  });
});

describe("(a) ONE status table covers every outcome wave 1 stores", () => {
  const py = read("src/iron_jarvis/scheduling/run_outcome.py");
  const models = read("src/iron_jarvis/core/models.py");
  const outcome = read("src/iron_jarvis/agents/outcome.py");

  it("every schedule last_status the scheduler writes has the table's word, and the project list says the same", () => {
    const written = [...py.matchAll(/^STATUS_[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => m[1]);
    written.push("missed", "failed"); // scheduling/service.py; the legacy FAILED_STATUSES value
    expect(written).toEqual(expect.arrayContaining(["ok", "error", "needs_you", "partial", "cancelled", "skipped"]));
    // v1.358.0 (lane 8A, updated on purpose): the outcome leads.
    const known = new Set(["Done", "Needs you", "Part failed", "Failed", "Stopped", "Skipped", "Missed"]);
    for (const v of written) {
      expect(known.has(scheduleRunWords(v).word), v).toBe(true);
      // lib/scheduleOutcome keeps no second copy of the words.
      expect(scheduleStatusWords(v).label, v).toBe(scheduleRunWords(v).word);
    }
    const so = read("dashboard/lib/scheduleOutcome.ts");
    expect(so).toMatch(/return scheduleStatusLabel\(status\);/);
    expect(so).not.toMatch(/label: "/); // no second word table
  });

  it("every session status and every ledger outcome reads a word from the table, never its raw id", () => {
    const block = /class SessionStatus\(str, enum\.Enum\):([\s\S]*?)\n\n/.exec(models)?.[1] ?? "";
    const statuses = [...block.matchAll(/^\s+[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => m[1]);
    expect(statuses).toEqual(["active", "queued", "completed", "failed", "cancelled"]);
    const words = statuses.map((s) => statusWords(s).word);
    expect(words).toEqual(["Working", "Queued", "Done", "Failed", "Stopped"]);
    const outcomes = [...outcome.matchAll(/^OUTCOME_[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => m[1]);
    expect(outcomes.sort()).toEqual(["completed", "completed_with_failures", "needs_you"]);
    expect(statusWords("completed", "completed").word).toBe("Done");
    // The SAME words for the same verdict, on a session and on a schedule.
    expect(statusWords("completed", "needs_you").word).toBe(scheduleRunWords("needs_you").word);
    expect(statusWords("completed", "completed_with_failures").word).toBe(scheduleRunWords("partial").word);
    // A stop is never painted like a failure, anywhere.
    expect(statusWords("cancelled").tone).toBe("slate");
    expect(scheduleRunWords("cancelled").tone).toBe("slate");
  });
});

/* ================================================= (b) one approval vocabulary */

const ASK = fixture.asks;
const SCOPE_RUN = [APPROVAL_LABELS.once, APPROVAL_LABELS.run, APPROVAL_LABELS.deny];
const SCOPE_CHAT = [APPROVAL_LABELS.once, APPROVAL_LABELS.chat, APPROVAL_LABELS.deny];
const UNDO = undoLine(true);
const ALL_WORDS = new Set<string>(Object.values(APPROVAL_LABELS));

/** The answers a chat card offers for this ask: the three, plus "Always allow
 *  exactly this" because the daemon offered it (`can_always`), all from the
 *  one table. */
function expectChatAnswers(card: HTMLElement) {
  const names = buttonNames(card);
  expect(names.every((n) => ALL_WORDS.has(n)), names.join(" | ")).toBe(true);
  expect(names.filter((n) => n !== APPROVAL_LABELS.always)).toEqual(SCOPE_CHAT);
  expect(names).toContain(APPROVAL_LABELS.always);
}

const enc = new TextEncoder();
function hangingBody(chunks: string[]) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      // never closes: the turn is paused on the ask
    },
  });
}

/** The stream frame through the REAL hook: the PendingApproval it builds. */
async function approvalFromTheHook() {
  const frame = `event: approval\ndata: ${JSON.stringify(ASK.stream_frame)}\n\n`;
  vi.stubGlobal("fetch", vi.fn(async () => new Response(hangingBody([frame]))));
  const { result } = renderHook(() => useChatStream());
  act(() => {
    void result.current.run({ messages: [] });
  });
  await waitFor(() => expect(result.current.approval).not.toBeNull());
  return result.current.approval!;
}

describe("(b) one ask, the same words on every surface that answers it", () => {
  it("the stream frame's undo verdict survives the hook's whitelist", () => {
    const ev = decodeSSE("approval", JSON.stringify(ASK.stream_frame));
    expect(ev && ev.type === "approval" && ev.reversible).toBe(true);
    // Anything but a real boolean is "not said".
    const junk = decodeSSE("approval", JSON.stringify({ ...ASK.stream_frame, reversible: "yes" }));
    expect(junk && junk.type === "approval" && "reversible" in junk).toBe(false);
  });

  it("the chat's card, in the composer's place (DockAsk)", async () => {
    const approval = await approvalFromTheHook();
    expect(approval.reversible).toBe(true);
    render(
      <DockAsk
        asks={collectDockAsks({ approval })}
        onAnswerElicitation={async () => ({ ok: true })}
        onDecideSampling={async () => true}
      />,
    );
    const dock = screen.getByTestId("dock-ask");
    const card = within(dock).getByTestId("chat-approval-card");
    expectChatAnswers(card);
    expect(within(card).getByTestId("approval-undo").textContent).toBe(UNDO);
  });

  it("a Build pane's card (PaneAsk)", async () => {
    const approval = await approvalFromTheHook();
    render(<PaneAsk approval={approval} />);
    const card = within(screen.getByTestId("pane-ask")).getByTestId("chat-approval-card");
    expectChatAnswers(card);
    expect(within(card).getByTestId("approval-undo").textContent).toBe(UNDO);
  });

  it("the bell's row (the listing row the daemon sends)", async () => {
    H.api["/chat/approvals/pending"] = { approvals: [ASK.pending_row] };
    H.api["/computeruse"] = { pending_approvals: 0 };
    H.api["/diagnostics"] = { pending_reviews: 0 };
    H.api["/workflows/runs?status=waiting&slim=true&limit=200"] = { runs: [] };
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
    const row = await screen.findByTestId("bell-agent-approval");
    expect(buttonNames(row)).toEqual(expect.arrayContaining(SCOPE_RUN));
    expect(buttonNames(row).filter((b) => /^(Allow|Deny|Approve|Decline)/.test(b))).toEqual(SCOPE_RUN);
    expect(within(row).getByTestId("bell-approval-undo").textContent).toBe(UNDO);
    expect(folded(within(row).getByTestId("bell-approval-line").textContent)).toMatch(
      /^Your nightly-brief run wants to save a file\./,
    );
  });

  it("the mission card (the mission view's coordinator ask)", () => {
    const view: MissionView = {
      session: {
        id: "session_brief",
        task: "Write the nightly brief",
        objective: "Write the nightly brief",
        status: "active",
        outcome: null,
        project_id: null,
        created_at: null,
        finished_at: null,
        interrupted: false,
        continued_as: null,
        provider: "",
        model: "",
        route_note: "",
      },
      coordinator: { name: "Jarvis", status: "waiting_you", waiting_on: ASK.mission_waiting, steps: 1 },
      members: [],
      progress: { done: 0, total: 0 },
      activity: [],
      deliverable: { text: "", documents: [], worklist: null },
    } as MissionView;
    const empty: MissionLive = { coordinator: "", coordinatorPhase: "", coordinatorNarration: "", members: {}, latest: null };
    const store: MissionLiveStore = { get: () => empty, subscribe: () => () => {} };
    render(<MissionOutput view={view} liveStore={store} objective="" onChanged={() => {}} onOpen={() => {}} />);
    const card = screen.getByTestId("mission-approval");
    expect(buttonNames(card)).toEqual(SCOPE_RUN);
    expect(within(card).getByTestId("mission-approval-undo").textContent).toBe(UNDO);
  });

  it("a project's paused task (the runtime's approval.requested event)", async () => {
    H.events = [
      {
        id: "ev1",
        type: "approval.requested",
        session_id: "session_brief",
        payload: { ...ASK.requested },
      },
    ];
    H.api["/sessions/session_brief"] = { session: { project_id: "p1" } };
    render(<ProjectApprovals projectId="p1" />);
    const card = await screen.findByTestId("chat-approval-card");
    expect(buttonNames(card)).toEqual(SCOPE_RUN);
    expect(within(card).getByTestId("approval-undo").textContent).toBe(UNDO);
  });

  it("an escalated run's ask in the chat's dock keeps the verdict (the page's fold, listing poll and dock mapping)", () => {
    const page = read("dashboard/app/chat/page.tsx");
    // The approval.requested fold, the listing poll and the dock mapping each
    // carry `reversible` (a real boolean only) into the dock's card.
    expect(page).toMatch(/typeof p\.reversible === "boolean" \? \{ reversible: p\.reversible \}/);
    expect(page).toMatch(/typeof a\.reversible === "boolean" \? \{ reversible: a\.reversible \}/);
    expect(page).toMatch(/typeof ask\.reversible === "boolean" \? \{ reversible: ask\.reversible \}/);
    // …and the chat card's own grant note uses the button's words.
    expect(page).toContain("Allowed for this chat.");
    expect(page).not.toContain("Allowed for this conversation.");
  });

  it("the card itself: chat words when it can arm the composer, run words elsewhere (one helper)", () => {
    const a = { id: "x", callId: "", tool: "write_file", reversible: true };
    const { unmount } = render(<ApprovalCard approval={a} docked />);
    expect(buttonNames(screen.getByTestId("chat-approval-card"))).toEqual(SCOPE_CHAT);
    unmount();
    render(<ApprovalCard approval={a} scope="run" />);
    expect(buttonNames(screen.getByTestId("chat-approval-card"))).toEqual(SCOPE_RUN);
  });
});
