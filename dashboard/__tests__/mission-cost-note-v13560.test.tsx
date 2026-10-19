/**
 * v1.356.0, wave 6 lane 6C (G79): a mission approval shows its cost note.
 *
 * The mission screen draws its OWN approval card (MissionOutput's local
 * ApprovalCard). A teammate's priced image request showed its price on the
 * chat card (`approval-cost`) but not here, where the user presses Allow —
 * the mission view's `waiting_on` never carried it. The daemon now hands the
 * ask's cost sentence through `pending_for` → the mission view's waiting
 * entry (`cost_note`, a sentence, never arguments), and the card shows it on
 * one quiet line under the ask, in the card's own undo-line style.
 *
 * The real MissionOutput; only the transport (lib/api), the model catalog
 * hook and next/link are doubles (the plan-mission-v1350 harness).
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: [] as string[],
  posts: [] as Array<{ path: string; body: unknown }>,
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
      return Promise.reject(new ApiError(`unmocked GET ${p}`, 404));
    },
    post: (p: string, body?: unknown) => {
      H.posts.push({ path: p, body });
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

import { MissionOutput, askCostLine } from "@/components/agents/mission/MissionOutput";
import type { MissionMember, MissionView, WaitingOn } from "@/lib/mission";
import type { MissionLive, MissionLiveStore } from "@/lib/useMission";

/** The sentence the daemon's spend check writes for a priced image
 *  (tools/media_tool → media/base.Quote.words), as the mission view carries it. */
const PRICED = "Generate 1 image on Pixio for 12 credits (your price)";

type Ask = WaitingOn & { cost_note?: string };

function member(waiting: Ask | null): MissionMember {
  return {
    session_id: "c2",
    agent: "custom:designer",
    name: "designer",
    kind: "custom",
    task: "make the cover picture",
    status: waiting ? "waiting_you" : "working",
    progress: { pct: null, label: "", basis: "steps" },
    activity: "",
    waiting_on: waiting as WaitingOn | null,
    steps: 0,
    result: "",
    files: [],
    started_at: null,
    finished_at: null,
    provider: "",
    model: "",
  };
}

function view(extra: Partial<MissionView> = {}): MissionView {
  return {
    session: {
      id: "s1",
      task: "make a launch page",
      objective: "make a launch page",
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
    coordinator: { name: "Jarvis", status: "working", waiting_on: null, steps: 0 },
    members: [],
    progress: { done: 0, total: 1 },
    activity: [],
    deliverable: { text: "", documents: [], worklist: null },
    ...extra,
  } as MissionView;
}

const EMPTY: MissionLive = { coordinator: "", coordinatorPhase: "", coordinatorNarration: "", members: {}, latest: null };
const liveStore: MissionLiveStore = { get: () => EMPTY, subscribe: () => () => {} };

function renderOutput(v: MissionView) {
  return render(<MissionOutput view={v} liveStore={liveStore} objective="" onChanged={() => {}} onOpen={() => {}} />);
}

beforeEach(() => {
  H.gets = [];
  H.posts = [];
});
afterEach(cleanup);

describe("the mission card says what a priced ask costs, before Allow", () => {
  it("a teammate's priced image request: the cost sentence on the card, then the answers", async () => {
    renderOutput(view({ members: [member({ approval_id: "ask-7", tool: "generate_media", cost_note: PRICED })] }));
    const card = await screen.findByTestId("mission-approval");
    expect(card.textContent).toContain("designer wants to");
    const cost = within(card).getByTestId("mission-approval-cost");
    expect(cost.textContent).toBe(PRICED);
    // It sits BEFORE the answers (read before Allow), in the card's own flow.
    const allow = within(card).getByRole("button", { name: "Allow once" });
    expect(cost.compareDocumentPosition(allow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The quiet line: the undo line's own look — theme tokens, whole pixels,
    // no border, no box.
    expect(cost.className).toBe("mt-0.5 text-[12px] text-zinc-400");
    expect(cost.className).not.toMatch(/border|shadow|rounded|bg-/);
    // Allow still answers exactly that ask, once.
    fireEvent.click(allow);
    await waitFor(() => expect(H.posts).toEqual([{ path: "/chat/approvals/ask-7", body: { decision: "once" } }]));
  });

  it("the coordinator's own priced ask shows it too", async () => {
    renderOutput(
      view({
        coordinator: {
          name: "Jarvis",
          status: "waiting_you",
          waiting_on: { approval_id: "ask-8", tool: "generate_media", cost_note: PRICED } as WaitingOn,
          steps: 0,
        },
      }),
    );
    const card = await screen.findByTestId("mission-approval");
    expect(within(card).getByTestId("mission-approval-cost").textContent).toBe(PRICED);
  });

  it("CONTROL: an ask with no price has no cost line (the card is exactly as before)", async () => {
    renderOutput(view({ members: [member({ approval_id: "ask-9", tool: "write_file" })] }));
    const card = await screen.findByTestId("mission-approval");
    expect(card.textContent).toContain("designer wants to save a file");
    expect(within(card).queryByTestId("mission-approval-cost")).toBeNull();
  });

  it("CONTROL: a plan ask never shows a cost line, whatever rides it", async () => {
    renderOutput(view({ members: [member({ approval_id: "ask-10", tool: "approve_plan", cost_note: PRICED })] }));
    const card = await screen.findByTestId("mission-approval");
    expect(within(card).queryByTestId("mission-approval-cost")).toBeNull();
  });

  it("only a non-empty string is words (an older daemon sends no key)", () => {
    expect(askCostLine({ approval_id: "a", tool: "t" })).toBe("");
    expect(askCostLine({ approval_id: "a", tool: "t", cost_note: "   " })).toBe("");
    expect(askCostLine({ approval_id: "a", tool: "t", cost_note: 12 as unknown as string })).toBe("");
    expect(askCostLine({ approval_id: "a", tool: "t", cost_note: ` ${PRICED} ` })).toBe(PRICED);
  });
});

describe("the source keeps the calm card rules", () => {
  it("MissionOutput's cost line is drawn from theme tokens only", () => {
    const src = readFileSync(
      path.join(__dirname, "..", "components", "agents", "mission", "MissionOutput.tsx"),
      "utf8",
    ).replace(/\r\n/g, "\n");
    const at = src.indexOf('data-testid="mission-approval-cost"');
    expect(at).toBeGreaterThan(-1);
    const line = src.slice(at, at + 200);
    expect(line).not.toMatch(/#[0-9a-f]{3,6}\b|rgb\(|(?:emerald|rose|amber|sky|violet)-\d/);
    expect(line).not.toMatch(/text-\[\d+\.5px\]/);
  });
});
