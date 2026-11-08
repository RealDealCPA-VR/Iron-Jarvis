/**
 * v1.449.0 (plan 0h.5 item 17, Wave B "Did it work?, everywhere") — the
 * mission screen's receipt and card read like every other surface's:
 *
 *   - the receipt line gains the mission's LENGTH (the coordinator's start to
 *     its end, read off the session row's stamps, in the app's duration words
 *     — lib/durationWords "4 min 12 s") and its ACTION COUNT (the
 *     coordinator's steps plus every teammate's); a mission still at work
 *     has no length yet;
 *   - the card for an ask is THE chat card (components/chat/ApprovalCard):
 *     its words, its preview, its undo and cost lines, its buttons, named for
 *     the coordinator or the teammate parked on it; a landed answer re-reads
 *     the view at once; a refused one keeps the card with its own words;
 *   - a teammate card's status is inked by the ONE status table's tone.
 *
 * Only the transport (lib/api), the model catalog and next/link are doubles;
 * MissionOutput, AgentCards and the chat card are the real ones.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: [] as string[],
  getResponses: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: unknown }>,
  postFail: null as null | { status: number; message: string },
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
    get: (p: string) => {
      H.gets.push(p);
      const r = H.getResponses[p];
      if (r === undefined) return Promise.reject(new ApiError(`unmocked GET ${p}`, 404));
      return Promise.resolve(JSON.parse(JSON.stringify(r)));
    },
    post: (p: string, body?: unknown) => {
      H.posts.push({ path: p, body });
      if (H.postFail) return Promise.reject(new ApiError(H.postFail.message, H.postFail.status));
      return Promise.resolve({});
    },
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useModels", () => ({
  useModels: () => ({ models: [], usable: [], data: { models: [] }, error: null, loading: false, reload: () => {} }),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { AgentCards } from "@/components/agents/mission/AgentCards";
import { MissionOutput, missionAskApproval } from "@/components/agents/mission/MissionOutput";
import { missionActions, missionTook, type MissionMember, type MissionView } from "@/lib/mission";
import { secondsText } from "@/lib/durationWords";
import { statusWords, toneInkClass } from "@/lib/statusWords";
import type { MissionLive, MissionLiveStore } from "@/lib/useMission";

function member(over: Partial<MissionMember> = {}): MissionMember {
  return {
    session_id: "c2",
    agent: "custom:writer",
    name: "writer",
    kind: "custom",
    task: "draft the strategy",
    status: "done",
    progress: { pct: 100, label: "", basis: "done" },
    activity: "",
    waiting_on: null,
    steps: 7,
    result: "",
    files: [],
    started_at: "2026-11-07T10:00:05Z",
    finished_at: "2026-11-07T10:03:05Z",
    provider: "fleet-custom",
    model: "glm",
    ...over,
  };
}

function view(extra: Partial<MissionView> = {}, session: Partial<MissionView["session"]> = {}): MissionView {
  return {
    session: {
      id: "s1",
      task: "write a market report",
      objective: "write a market report",
      status: "completed",
      outcome: "completed",
      project_id: null,
      created_at: "2026-11-07T10:00:00Z",
      finished_at: "2026-11-07T10:04:12Z",
      interrupted: false,
      continued_as: null,
      provider: "claude-cli",
      model: "claude-opus",
      route_note: "",
      ...session,
    },
    coordinator: { name: "Jarvis", status: "done", waiting_on: null, steps: 16 },
    members: [member()],
    progress: { done: 1, total: 1 },
    activity: [],
    deliverable: { text: "Here it is.", documents: [], worklist: null },
    ...extra,
  };
}

const EMPTY: MissionLive = { coordinator: "", coordinatorPhase: "", coordinatorNarration: "", members: {}, latest: null };
const liveStore: MissionLiveStore = { get: () => EMPTY, subscribe: () => () => {} };

function renderOutput(v: MissionView, onChanged: () => void = () => {}) {
  return render(<MissionOutput view={v} liveStore={liveStore} objective="" onChanged={onChanged} onOpen={() => {}} />);
}

const buttonNames = (el: HTMLElement) => within(el).getAllByRole("button").map((b) => b.textContent?.trim());

beforeEach(() => {
  H.gets = [];
  H.posts = [];
  H.getResponses = {};
  H.postFail = null;
});
afterEach(cleanup);

describe("the receipt says how long and how much", () => {
  it("missionTook: the coordinator's start to its end, in the app's duration words; '' while it still runs", () => {
    expect(missionTook(view())).toBe("4 min 12 s");
    expect(missionTook(view())).toBe(secondsText(252_000));
    expect(missionTook(view({}, { finished_at: null }))).toBe("");
    expect(missionTook(view({}, { created_at: null }))).toBe("");
    expect(missionTook(view({}, { created_at: "junk" }))).toBe("");
    // An end before the start is unreadable, never a negative.
    expect(missionTook(view({}, { finished_at: "2026-11-07T09:00:00Z" }))).toBe("");
    expect(missionTook(view({}, { finished_at: "2026-11-07T10:00:07.300Z" }))).toBe("7.3 s");
  });

  it("missionActions: the coordinator's steps plus every teammate's", () => {
    expect(missionActions(view())).toBe(23);
    expect(missionActions(view({ members: [member({ steps: 7 }), member({ session_id: "c3", steps: 3 })] }))).toBe(26);
    expect(missionActions(view({ members: [], coordinator: { name: "Jarvis", status: "done", waiting_on: null, steps: 0 } }))).toBe(0);
    // Junk counts are not counted.
    expect(missionActions(view({ members: [member({ steps: Number.NaN })] }))).toBe(16);
  });

  it("on screen: '… · 4 min 12 s · 23 actions' after the model, once the mission ended", async () => {
    renderOutput(view());
    const receipt = await screen.findByTestId("mission-receipt");
    expect(within(receipt).getByTestId("mission-receipt-took").textContent).toBe(" · 4 min 12 s");
    expect(within(receipt).getByTestId("mission-receipt-actions").textContent?.replace(/\s+/g, " ")).toBe(" · 23 actions");
    expect(receipt.textContent?.replace(/\s+/g, " ")).toMatch(/^Answered by claude-opus on Claude Code · 4 min 12 s · 23 actions$/);
  });

  it("on screen: a mission still at work has no length yet; its actions so far are counted", async () => {
    renderOutput(view({ coordinator: { name: "Jarvis", status: "working", waiting_on: null, steps: 2 }, members: [member({ status: "working", steps: 1 })] }, { status: "active", outcome: null, finished_at: null }));
    const receipt = await screen.findByTestId("mission-receipt");
    expect(within(receipt).queryByTestId("mission-receipt-took")).toBeNull();
    expect(within(receipt).getByTestId("mission-receipt-actions").textContent?.replace(/\s+/g, " ")).toBe(" · 3 actions");
    expect(receipt.textContent).toMatch(/^Working with claude-opus/);
  });

  it("one action is singular; no actions say nothing", async () => {
    renderOutput(view({ members: [], coordinator: { name: "Jarvis", status: "done", waiting_on: null, steps: 1 } }));
    expect((await screen.findByTestId("mission-receipt-actions")).textContent?.replace(/\s+/g, " ")).toBe(" · 1 action");
    cleanup();
    renderOutput(view({ members: [], coordinator: { name: "Jarvis", status: "done", waiting_on: null, steps: 0 } }));
    await screen.findByTestId("mission-receipt");
    expect(screen.queryByTestId("mission-receipt-actions")).toBeNull();
  });
});

describe("the card is the chat's ApprovalCard", () => {
  const asking = (waiting_on: MissionView["coordinator"]["waiting_on"]) =>
    view({ coordinator: { name: "Jarvis", status: "waiting_you", waiting_on, steps: 0 } }, { status: "active", outcome: null, finished_at: null });

  it("missionAskApproval: the ask in the card's shape; a plan carries the daemon's steps and never a cost", () => {
    expect(missionAskApproval({ approval_id: "a1", tool: "write_file", reversible: true, cost_note: "12 credits" }, null)).toEqual({
      id: "a1",
      callId: "",
      tool: "write_file",
      args: undefined,
      reversible: true,
      costNote: "12 credits",
    });
    const plan = missionAskApproval({ approval_id: "a2", tool: "approve_plan", cost_note: "12 credits" }, { total: 2, steps: [{ index: 0, goal: "Read", state: "waiting" }, { index: 1, goal: "Write", state: "waiting" }] });
    expect(plan.args).toEqual({ steps: ["Read", "Write"] });
    expect(plan.costNote).toBeUndefined();
    expect(missionAskApproval({ approval_id: "a3", tool: "approve_plan" }, null).args).toBeUndefined();
  });

  it("mounts the real card, named for the coordinator, with the run's wider answer, and re-reads the view once an answer lands", async () => {
    const onChanged = vi.fn();
    renderOutput(asking({ approval_id: "ask-1", tool: "write_file", reversible: true }), onChanged);
    const wrap = await screen.findByTestId("mission-approval");
    const card = within(wrap).getByTestId("chat-approval-card");
    expect(card.getAttribute("role")).toBe("alertdialog");
    expect(card.textContent).toContain("Jarvis wants to save a file");
    expect(within(card).getByTestId("approval-undo").textContent).toMatch(/undo/i);
    expect(within(card).getByTestId("approval-scope").getAttribute("data-scope")).toBe("run");
    expect(buttonNames(card)).toEqual(["Allow once", "Allow for this run", "Deny"]);
    // No redirect field: nothing here sends words to a reply.
    expect(within(card).queryByTestId("approval-redirect")).toBeNull();
    fireEvent.click(within(card).getByText("Allow once"));
    await waitFor(() => expect(H.posts).toEqual([{ path: "/chat/approvals/ask-1", body: { decision: "once" } }]));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  });

  it("a teammate's card is named for the teammate; its answer re-reads too", async () => {
    const onChanged = vi.fn();
    renderOutput(view({ members: [member({ status: "waiting_you", waiting_on: { approval_id: "ask-2", tool: "shell" } })] }, { status: "active", outcome: null, finished_at: null }), onChanged);
    const wrap = await screen.findByTestId("mission-approval");
    expect(wrap.textContent).toContain("writer wants to run a command");
    fireEvent.click(within(wrap).getByText("Deny"));
    await waitFor(() => expect(H.posts).toEqual([{ path: "/chat/approvals/ask-2", body: { decision: "deny" } }]));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  });

  it("a refused answer keeps the card with the card's own words, and re-reads nothing", async () => {
    const onChanged = vi.fn();
    H.postFail = { status: 500, message: "the daemon could not take the answer" };
    renderOutput(asking({ approval_id: "ask-3", tool: "write_file" }), onChanged);
    const wrap = await screen.findByTestId("mission-approval");
    fireEvent.click(within(wrap).getByText("Allow once"));
    expect(await within(wrap).findByText("the daemon could not take the answer")).toBeTruthy();
    expect(within(wrap).getByText("Allow once")).not.toBeDisabled();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("the source: MissionOutput draws no card of its own any more", async () => {
    const { readFileSync } = await import("node:fs");
    const path = await import("node:path");
    const src = readFileSync(path.join(__dirname, "..", "components", "agents", "mission", "MissionOutput.tsx"), "utf8").replace(/\r\n/g, "\n");
    expect(src).toMatch(/import \{ ApprovalCard, type ApprovalAsk \} from "@\/components\/chat\/ApprovalCard"/);
    expect(src).not.toMatch(/data-testid="mission-approval-ask"/);
    expect(src).not.toMatch(/data-testid="mission-approval-plan"/);
    expect(src).not.toMatch(/function ApprovalCard\(/);
  });
});

describe("a teammate card's status is inked by the ONE table", () => {
  it("done is the success tone, waiting the warning tone, failed the danger tone, queued the quiet grey", () => {
    const members = [
      member({ session_id: "d", name: "done-one", status: "done" }),
      member({ session_id: "w", name: "wait-one", status: "waiting_you", waiting_on: { approval_id: "x", tool: "shell" } }),
      member({ session_id: "f", name: "fail-one", status: "failed" }),
      member({ session_id: "q", name: "queued-one", status: "queued" }),
    ];
    render(<AgentCards members={members} onChanged={() => {}} />);
    const ink = (name: string, status: string) => {
      const word = statusWords(status).word;
      const el = screen.getAllByText(word).find((e) => e.closest("[data-testid]")?.textContent?.includes(name)) ?? screen.getAllByText(word)[0];
      return el.className;
    };
    expect(ink("done-one", "done")).toBe(toneInkClass(statusWords("done").tone));
    expect(toneInkClass(statusWords("done").tone)).toBe("text-tone-success");
    expect(ink("wait-one", "waiting_you")).toBe("text-tone-warn");
    expect(ink("fail-one", "failed")).toBe("text-tone-danger");
    expect(ink("queued-one", "queued")).toBe("text-zinc-400");
  });
});
