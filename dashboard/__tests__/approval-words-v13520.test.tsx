/**
 * v1.352.0 (product-readiness wave 2, lane 2B) — the mission screen uses
 * chat's words (G23); one set of approval words and a bell ask that says what
 * and why (G24); approval cards say whether the action can be undone (G25).
 *
 * Every block drives the REAL component: the chat's ApprovalCard, the
 * NotificationBell (its poll, its answer POST, its "Approved" line), the
 * mission screen's MissionOutput and AgentCards with the REAL useModels
 * catalog read. Only the transport (lib/api), the event socket, desktop
 * notifications and next/link are stand-ins.
 *
 * THE ACCEPTANCE LINES, as a new user would read them:
 *  - a mission run on the local fleet: "Answered by fleet on DGX Spark";
 *  - a waiting teammate: "Waiting for your OK to save a file.";
 *  - each finished teammate shows how long it took ("2 min 5 s");
 *  - a schedule's ask in the bell: "Your nightly-brief run wants to save a
 *    file" · Allow once · Allow for this run · Deny, and after Allow
 *    "Approved. The run continues · Open run";
 *  - an ask to send a message: "This may not be undoable."; an ask to save a
 *    file in the work folder: "You can undo this.".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  gets: [] as string[],
  posts: [] as Array<{ path: string; body: unknown }>,
  /** Called after each POST lands — a test can change what the poll answers. */
  afterPost: null as null | ((path: string) => void),
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
    get: (path: string) => {
      H.gets.push(path);
      return Promise.resolve(JSON.parse(JSON.stringify(H.api[path] ?? {})));
    },
    post: (path: string, body?: unknown) => {
      H.posts.push({ path, body });
      H.afterPost?.(path);
      return Promise.resolve({ ok: true });
    },
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
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

import { __resetApiCache } from "@/lib/apiCache";
import {
  APPROVAL_LABELS as CARD_LABELS,
  ApprovalCard,
  scopeLabel as cardScopeLabel,
} from "@/components/chat/ApprovalCard";
import { NotificationBell } from "@/components/NotificationBell";
import { AgentCards, memberTook } from "@/components/agents/mission/AgentCards";
import { MissionOutput } from "@/components/agents/mission/MissionOutput";
import {
  ANSWERED_LINGER_MS,
  APPROVAL_LABELS,
  askPhrase,
  runSubject,
  scopeLabel,
  undoLine,
} from "@/lib/approvalWords";
import { endpointLabel, ranOnWords, tookMs } from "@/lib/ranOnWords";
import type { MissionMember, MissionView } from "@/lib/mission";
import type { MissionLive, MissionLiveStore } from "@/lib/useMission";

const PENDING = "/chat/approvals/pending";

beforeEach(() => {
  H.api = {};
  H.gets = [];
  H.posts = [];
  H.afterPost = null;
  __resetApiCache();
});
afterEach(() => cleanup());

const buttonNames = (el: HTMLElement) => within(el).getAllByRole("button").map((b) => b.textContent?.trim());
const folded = (s: string | null | undefined) => (s ?? "").replace(/\s+/g, " ").trim();

/* =========================================================== the words === */

describe("one set of approval words (lib/approvalWords)", () => {
  it("the card's labels ARE the shared words (one definition, re-exported)", () => {
    expect(CARD_LABELS).toBe(APPROVAL_LABELS);
    expect(cardScopeLabel).toBe(scopeLabel);
    expect(APPROVAL_LABELS).toEqual({
      once: "Allow once",
      chat: "Allow for this chat",
      run: "Allow for this run",
      tab: "Allow for this tab",
      always: "Always allow exactly this",
      deny: "Deny",
    });
  });

  it("names who asks from the run's origin, never a guessed name", () => {
    expect(runSubject("schedule:nightly-brief")).toBe("Your nightly-brief run");
    expect(runSubject("reflex:inbox-sorter")).toBe("Your inbox-sorter run");
    expect(runSubject("job:mission")).toBe("Your mission");
    expect(runSubject("job:mission-member")).toBe("A mission teammate");
    // An internal id is never shown; no origin is "an agent run".
    expect(runSubject("goal:goal_8f2a")).toBe("An agent run");
    expect(runSubject("schedule:")).toBe("An agent run");
    expect(runSubject(undefined)).toBe("An agent run");
  });

  it("says the tool in the card's words, else 'run <id>'", () => {
    expect(askPhrase("write_file")).toBe("save a file");
    expect(askPhrase("shell")).toBe("run a command");
    expect(askPhrase("custom_backup")).toBe("run custom_backup");
  });

  it("the undo line has three answers: can, may not, nothing to say", () => {
    expect(undoLine(true)).toBe("You can undo this.");
    expect(undoLine(false)).toBe("This may not be undoable.");
    expect(undoLine(undefined)).toBeNull();
    expect(undoLine("true")).toBeNull();
  });
});

/* ======================================================= the chat card === */

describe("the approval card (G24 scope words, G25 undo line)", () => {
  const ASK = { id: "apr_1", callId: "c1", tool: "write_file", args: { path: "notes/brief.md", content: "# hi" } };

  it("in a chat it offers Allow once / Allow for this chat / Deny and posts `conversation`", async () => {
    const onConversation = vi.fn();
    render(<ApprovalCard approval={ASK} onConversation={onConversation} />);
    const card = screen.getByTestId("chat-approval-card");
    expect(buttonNames(card)).toEqual(["Allow once", "Allow for this chat", "Deny"]);
    expect(screen.getByTestId("approval-scope").getAttribute("data-scope")).toBe("chat");
    expect(folded(card.textContent)).toContain("“this chat” stops asking for write_file here");
    fireEvent.click(screen.getByRole("button", { name: "Allow for this chat" }));
    await waitFor(() => expect(onConversation).toHaveBeenCalledWith("write_file"));
    expect(H.posts).toEqual([{ path: "/chat/approvals/apr_1", body: { decision: "conversation" } }]);
  });

  it("docked in the composer's place it is the chat's card too", () => {
    render(<ApprovalCard approval={ASK} docked />);
    expect(screen.getByRole("button", { name: "Allow for this chat" })).toBeTruthy();
  });

  it("on an agent run's ask (the session page: no composer to arm) it says 'Allow for this run'", () => {
    render(<ApprovalCard approval={ASK} />);
    const card = screen.getByTestId("chat-approval-card");
    expect(buttonNames(card)).toEqual(["Allow once", "Allow for this run", "Deny"]);
    expect(folded(card.textContent)).toContain("The run is paused until you answer.");
    expect(folded(card.textContent)).toContain("“this run” stops asking for write_file here");
    expect(card.textContent).not.toMatch(/conversation/);
  });

  it("a file save says it can be undone; a send says it may not be; a read says nothing", () => {
    const { unmount } = render(<ApprovalCard approval={{ ...ASK, reversible: true }} scope="chat" />);
    const can = screen.getByTestId("approval-undo");
    expect(can.textContent).toBe("You can undo this.");
    expect(can.getAttribute("data-reversible")).toBe("true");
    // The calm line: quiet grey, no box, whole pixels.
    expect(can.className).toContain("text-zinc-400");
    expect(can.className).not.toMatch(/\b(border|rounded|shadow|bg-)/);
    expect(can.className).not.toMatch(/\[\d+\.\d+px\]/);
    unmount();

    const SEND = { id: "apr_2", callId: "c2", tool: "notify", args: { message: "your summary is ready" } };
    const sent = render(<ApprovalCard approval={{ ...SEND, reversible: false }} scope="chat" />);
    expect(screen.getByTestId("approval-undo").textContent).toBe("This may not be undoable.");
    sent.unmount();

    render(<ApprovalCard approval={ASK} scope="chat" />);
    expect(screen.queryByTestId("approval-undo")).toBeNull();
  });

  it("a plan ask never shows an undo line (a plan is not an action)", () => {
    render(
      <ApprovalCard
        approval={{ id: "apr_p", callId: "c3", tool: "approve_plan", args: { steps: ["a"] }, reversible: false }}
      />,
    );
    expect(screen.queryByTestId("approval-undo")).toBeNull();
    expect(buttonNames(screen.getByTestId("chat-approval-card"))).toEqual(["Allow once", "Deny"]);
  });
});

/* ============================================================ the bell === */

const NIGHTLY = {
  id: "apr_nb",
  tool: "write_file",
  session_id: "session_nb",
  requested_at: "2026-10-18T03:00:00",
  timeout_s: 300,
  mission_id: null,
  project_id: null,
  task: "Write the nightly brief",
  origin: "schedule:nightly-brief",
  reversible: true,
};

function pending(rows: unknown[]) {
  H.api[PENDING] = { approvals: rows };
  H.api["/computeruse"] = { pending_approvals: 0 };
  H.api["/diagnostics"] = { pending_reviews: 0 };
  H.api["/workflows/runs?status=waiting&slim=true&limit=200"] = { runs: [] };
}

async function openBellRow(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
  return screen.findByTestId("bell-agent-approval");
}

describe("the bell's ask says what and why (G24) and whether it can be undone (G25)", () => {
  it("a schedule's ask: who, what, for what, undo, and the card's three answers", async () => {
    pending([NIGHTLY]);
    render(<NotificationBell />);
    const row = await openBellRow();
    const line = within(row).getByTestId("bell-approval-line");
    expect(folded(line.textContent)).toBe(
      "Your nightly-brief run wants to save a file. The run waits up to 5 min for you.",
    );
    // The exact tool id stays on the row, in the sentence's title — never in
    // monospace in the reading line.
    expect(line.querySelector('[title="The exact tool: write_file"]')).not.toBeNull();
    expect(line.querySelector(".font-mono")).toBeNull();
    expect(within(row).getByTestId("bell-approval-task").textContent).toBe("for: Write the nightly brief");
    expect(within(row).getByTestId("bell-approval-undo").textContent).toBe("You can undo this.");
    expect(buttonNames(row)).toEqual(["Allow once", "Allow for this run", "Deny"]);
  });

  it("after Allow: 'Approved. The run continues · Open run' for a few seconds, then the row leaves", async () => {
    pending([NIGHTLY]);
    // The daemon resolves the ask: the next poll no longer lists it. The
    // confirmation must outlive that poll (the bell holds it, not the list).
    H.afterPost = (p) => {
      if (p === `/chat/approvals/${NIGHTLY.id}`) H.api[PENDING] = { approvals: [] };
    };
    render(<NotificationBell />);
    const row = await openBellRow();
    // v1.352.0 re-review (screen readers): the dropdown's live region is in
    // the page, EMPTY, before the answer, and the same element then says what
    // happened (a region that mounts with its words is often never read).
    const live = screen.getByTestId("bell-answered-live");
    expect(live.getAttribute("role")).toBe("status");
    expect(live.textContent).toBe("");
    fireEvent.click(within(row).getByRole("button", { name: "Allow once" }));
    const done = await screen.findByTestId("bell-agent-answered");
    expect(H.posts).toEqual([{ path: `/chat/approvals/${NIGHTLY.id}`, body: { decision: "once" } }]);
    expect(done.getAttribute("data-outcome")).toBe("approved");
    expect(folded(within(done).getByTestId("bell-agent-answered-line").textContent)).toBe(
      "Approved. The run continues · Open run",
    );
    expect(screen.getByTestId("bell-answered-live")).toBe(live);
    expect(live.textContent).toBe("Approved. The run continues.");
    // ONE announcement: the row itself is not a second live region.
    expect(within(done).queryByRole("status")).toBeNull();
    expect(within(done).getByTestId("bell-agent-answered-open").getAttribute("href")).toBe("/sessions/session_nb");
    expect(screen.queryByTestId("bell-agent-approval")).toBeNull();
    // Still there after the re-poll the answer triggered…
    await waitFor(() => expect(H.gets.filter((g) => g === PENDING).length).toBeGreaterThan(1));
    expect(screen.getByTestId("bell-agent-answered")).toBeTruthy();
    // …and gone once its few seconds are up.
    await waitFor(() => expect(screen.queryByTestId("bell-agent-answered")).toBeNull(), {
      timeout: ANSWERED_LINGER_MS + 4000,
    });
    // Nothing waits on the user: the honest empty state again.
    expect(screen.getByText(/all caught up/i)).toBeTruthy();
  });

  it("after Deny: 'Denied. That action will not run.' and no Open run", async () => {
    pending([NIGHTLY]);
    render(<NotificationBell />);
    const row = await openBellRow();
    fireEvent.click(within(row).getByRole("button", { name: "Deny" }));
    const done = await screen.findByTestId("bell-agent-answered");
    expect(done.getAttribute("data-outcome")).toBe("denied");
    expect(folded(done.textContent)).toBe("Denied. That action will not run.");
    expect(screen.getByTestId("bell-answered-live").textContent).toBe("Denied. That action will not run.");
    expect(within(done).queryByTestId("bell-agent-answered-open")).toBeNull();
    expect(H.posts).toEqual([{ path: `/chat/approvals/${NIGHTLY.id}`, body: { decision: "deny" } }]);
  });

  it("an ask to send a message says it may not be undoable; a mission teammate says so", async () => {
    pending([
      {
        ...NIGHTLY,
        id: "apr_send",
        tool: "notify",
        session_id: "session_t",
        origin: "job:mission-member",
        task: "Tell the client their summary is ready",
        reversible: false,
        mission_id: "m1",
      },
    ]);
    render(<NotificationBell />);
    const row = await openBellRow();
    expect(folded(within(row).getByTestId("bell-approval-line").textContent)).toMatch(
      /^A mission teammate wants to \S/,
    );
    expect(within(row).getByTestId("bell-approval-undo").textContent).toBe("This may not be undoable.");
  });

  it("an older daemon's row (no task, origin or verdict) reads 'An agent run' with no extra lines", async () => {
    pending([{ id: "apr_old", tool: "edit_file", session_id: "s1", requested_at: "2026-10-10T10:00:00" }]);
    render(<NotificationBell />);
    const row = await openBellRow();
    expect(folded(within(row).getByTestId("bell-approval-line").textContent)).toBe(
      "An agent run wants to change a file. The run is waiting for you.",
    );
    expect(within(row).queryByTestId("bell-approval-task")).toBeNull();
    expect(within(row).queryByTestId("bell-approval-undo")).toBeNull();
  });
});

/* ===================================================== the mission screen === */

/** The live /models catalog shape: a custom endpoint the user named "DGX
 *  Spark" (its fleet twin carries the label) and a fleet box "RTX6000ada". */
const CATALOG = {
  models: [
    { provider: "custom", model: "fleet", kind: "local", available: true },
    { provider: "fleet-custom", model: "fleet", name: "DGX Spark", source: "endpoint", kind: "local", available: true },
    { provider: "fleet-rtx6000ada", model: "brain (RTX)", name: "RTX6000ada", source: "endpoint", kind: "local", available: true },
    { provider: "claude-cli", model: "claude-opus-5-5", label: "Opus 5.5", kind: "cli", available: true },
  ],
};

function member(over: Partial<MissionMember> = {}): MissionMember {
  return {
    session_id: "m1",
    agent: "researcher",
    name: "Researcher",
    kind: "builtin",
    task: "find the numbers",
    status: "done",
    progress: { pct: 100, label: "", basis: "done" },
    activity: "",
    waiting_on: null,
    steps: 3,
    result: "",
    files: [],
    started_at: "2026-10-18T12:00:00.250000",
    finished_at: "2026-10-18T12:02:05.250000",
    provider: "fleet-rtx6000ada",
    model: "brain (RTX)",
    ...over,
  };
}

function view(session: Partial<MissionView["session"]> = {}, extra: Partial<MissionView> = {}): MissionView {
  return {
    session: {
      id: "s1",
      task: "write the market report",
      objective: "write the market report",
      status: "completed",
      outcome: null,
      project_id: null,
      created_at: null,
      finished_at: null,
      interrupted: false,
      continued_as: null,
      provider: "custom",
      model: "fleet",
      route_note: "",
      ...session,
    },
    coordinator: { name: "Jarvis", status: "done", waiting_on: null, steps: 1 },
    members: [],
    progress: { done: 0, total: 0 },
    activity: [],
    deliverable: { text: "", documents: [], worklist: null },
    ...extra,
  };
}

const EMPTY: MissionLive = { coordinator: "", coordinatorPhase: "", coordinatorNarration: "", members: {}, latest: null };
const liveStore: MissionLiveStore = { get: () => EMPTY, subscribe: () => () => {} };
const renderOutput = (v: MissionView) =>
  render(<MissionOutput view={v} liveStore={liveStore} objective="" onChanged={() => {}} onOpen={() => {}} />);

describe("the mission screen uses chat's words (G23)", () => {
  it("a mission on the local fleet reads 'Answered by fleet on DGX Spark' (the raw stamp in the title)", async () => {
    H.api["/models"] = CATALOG;
    renderOutput(view());
    const receipt = await screen.findByTestId("mission-receipt");
    await waitFor(() => expect(folded(receipt.textContent)).toBe("Answered by fleet on DGX Spark"));
    expect(receipt.querySelector('[title="custom · fleet"]')).not.toBeNull();
    expect(H.gets).toContain("/models");
  });

  it("before the catalog answers it still names the place in words, never 'custom · fleet'", () => {
    renderOutput(view());
    const receipt = screen.getByTestId("mission-receipt");
    expect(folded(receipt.textContent)).toBe("Answered by fleet on your own endpoint");
    expect(receipt.textContent).not.toContain("·");
  });

  it("each finished teammate shows how long it took; one still at work does not", async () => {
    H.api["/models"] = CATALOG;
    render(
      <AgentCards
        members={[
          member(),
          member({ session_id: "m2", agent: "writer", name: "Writer", status: "working", finished_at: null }),
          member({ session_id: "m3", agent: "checker", name: "Checker", status: "failed" }),
        ]}
      />,
    );
    const took = screen.getByTestId("mission-card-took-researcher");
    expect(took.textContent).toBe("· took 2 min 5 s");
    // The visible words; the dot is decoration, "took" is for a screen reader.
    expect(took.querySelector('[aria-hidden="true"]')?.textContent).toBe("· ");
    expect(took.querySelector(".sr-only")?.textContent).toBe("took ");
    expect(took.className).toContain("text-zinc-500");
    expect(screen.getByTestId("mission-card-took-checker").textContent).toContain("2 min 5 s");
    expect(screen.queryByTestId("mission-card-took-writer")).toBeNull();
    // The model line, opened: the model on the box the user named.
    fireEvent.click(within(screen.getByTestId("mission-card-researcher")).getAllByRole("button")[0]);
    await waitFor(() =>
      expect(screen.getByTestId("mission-card-model-researcher").textContent).toBe(
        "Answered by brain (RTX) on RTX6000ada",
      ),
    );
    expect(screen.getByTestId("mission-card-model-researcher").getAttribute("title")).toBe(
      "fleet-rtx6000ada · brain (RTX)",
    );
  });

  it("a waiting teammate reads 'Waiting for your OK to save a file.' (the id in the title)", () => {
    render(
      <AgentCards
        members={[
          member({
            status: "waiting_you",
            finished_at: null,
            waiting_on: { approval_id: "a9", tool: "write_file" },
          }),
        ]}
      />,
    );
    fireEvent.click(within(screen.getByTestId("mission-card-researcher")).getAllByRole("button")[0]);
    const waiting = screen.getByTestId("mission-card-waiting-researcher");
    expect(waiting.textContent).toBe("Waiting for your OK to save a file.");
    expect(waiting.getAttribute("title")).toBe("The exact tool: write_file");
  });

  it("the mission's ask card speaks the chat card's words: Allow once / Allow for this run / Deny", async () => {
    renderOutput(
      view(
        { status: "active" },
        {
          coordinator: {
            name: "Jarvis",
            status: "waiting_you",
            waiting_on: { approval_id: "ask-1", tool: "write_file" },
            steps: 1,
          },
        },
      ),
    );
    const card = screen.getByTestId("mission-approval");
    expect(folded(within(card).getByTestId("mission-approval-ask").textContent)).toBe(
      "Jarvis wants to save a file. Nothing runs until you answer.",
    );
    expect(buttonNames(card)).toEqual(["Allow once", "Allow for this run", "Deny"]);
    expect(card.textContent).not.toMatch(/Decline|for this task/);
    fireEvent.click(within(card).getByText("Deny"));
    await waitFor(() => expect(H.posts).toEqual([{ path: "/chat/approvals/ask-1", body: { decision: "deny" } }]));
  });
});

/* =========================================================== the helpers === */

describe("the receipt's words, as data", () => {
  it("a config-seeded endpoint finds its label on its fleet twin; a cloud CLI reads its plain name", () => {
    expect(endpointLabel("custom", CATALOG.models)).toBe("DGX Spark");
    expect(endpointLabel("fleet-rtx6000ada", CATALOG.models)).toBe("RTX6000ada");
    expect(endpointLabel("claude-cli", CATALOG.models)).toBe("Claude Code");
    expect(endpointLabel("fleet-gone", CATALOG.models)).toBe("fleet-gone");
    expect(ranOnWords("claude-cli", "claude-opus-5-5", CATALOG.models)).toEqual({
      text: "Opus 5.5 on Claude Code",
      title: "claude-cli · claude-opus-5-5",
    });
    // A catalog that has not arrived (or a stand-in that is not a list).
    expect(ranOnWords("custom", "fleet", undefined).text).toBe("fleet on your own endpoint");
  });

  it("a duration needs both ends in order; nothing is guessed", () => {
    expect(tookMs("2026-10-18T12:00:00", "2026-10-18T12:00:09")).toBe(9000);
    expect(tookMs(null, "2026-10-18T12:00:09")).toBeNull();
    expect(tookMs("2026-10-18T12:00:09", "2026-10-18T12:00:00")).toBeNull();
    expect(tookMs("not a date", "2026-10-18T12:00:00")).toBeNull();
    expect(memberTook(member({ status: "done" }))).toBe("2 min 5 s");
    expect(memberTook(member({ status: "working" }))).toBeNull();
  });
});
