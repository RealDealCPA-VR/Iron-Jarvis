/**
 * v1.329.0 — calm chat wave 6, H4: the mission screen speaks the chat's calm
 * words, and the workflow chips the chat's draft card renders are theme
 * tokens.
 *
 * The final audit after wave 5 found the mission screen still saying, in its
 * own em-dash copy, a fact the chat receipt now says plainly:
 *   AgentCards:    "Mock answer — no real model ran (mock · mock-1)"
 *   MissionOutput: "Mock answer — no real model ran."
 * The chat says "Mock answer. No real model ran." (TurnReceipt), in the warn
 * tone. The other dash asides on the mission screen (the Stop note, the
 * refused-retry sentences, the empty states, the draft note, the follow-up
 * placeholder) go the same way, and so do the literal hues and half-pixel
 * sizes in those two files.
 *
 * And components/workflow/agents.ts held literal amber / sky / emerald /
 * violet chip classes that WorkflowDraftCard (a chat card) renders; they are
 * tone tokens now and the S3 guard reads that file (see
 * chat-cards-whole-pixels-v1329).
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  posts: [] as Array<{ path: string; body: unknown }>,
  postErrors: {} as Record<string, { message: string; status: number }>,
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
    get: () => Promise.resolve({}),
    post: (p: string, body?: unknown) => {
      H.posts.push({ path: p, body });
      const err = H.postErrors[p];
      if (err) return Promise.reject(new ApiError(err.message, err.status));
      return Promise.resolve({});
    },
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
  };
});

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { AgentCards, mockLine } from "@/components/agents/mission/AgentCards";
import { MissionOutput, plainSentences } from "@/components/agents/mission/MissionOutput";
import { AGENT_META, KIND_META, STEP_KINDS, AGENT_TYPES } from "@/components/workflow/agents";
import type { MissionMember, MissionView } from "@/lib/mission";
import type { MissionLive, MissionLiveStore } from "@/lib/useMission";

const ROOT = path.join(__dirname, "..");
const readSrc = (rel: string) => readFileSync(path.join(ROOT, rel), "utf8").replace(/\r\n/g, "\n");

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
    started_at: null,
    finished_at: null,
    provider: "mock",
    model: "mock-1",
    ...over,
  };
}

function view(over: Partial<MissionView["session"]> = {}, extra: Partial<MissionView> = {}): MissionView {
  return {
    session: {
      id: "s1",
      task: "write a market report",
      objective: "write a market report",
      status: "completed",
      outcome: null,
      project_id: null,
      created_at: null,
      finished_at: null,
      interrupted: false,
      continued_as: null,
      provider: "mock",
      model: "mock",
      route_note: "",
      ...over,
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
const liveStore = (live: MissionLive = EMPTY): MissionLiveStore => ({ get: () => live, subscribe: () => () => {} });

function renderOutput(v: MissionView, live: MissionLive = EMPTY) {
  return render(
    <MissionOutput view={v} liveStore={liveStore(live)} objective="" onChanged={() => {}} onOpen={() => {}} />,
  );
}

beforeEach(() => {
  H.posts = [];
  H.postErrors = {};
});
afterEach(cleanup);

/* ------------------------------------------------ 1. the teammate cards --- */

describe("a teammate card says the mock in the chat's words", () => {
  async function modelLine(m: MissionMember): Promise<HTMLElement> {
    render(<AgentCards members={[m]} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    return screen.findByTestId(`mission-card-model-${m.agent}`);
  }

  it("a DONE mock teammate: 'Mock answer. No real model ran (mock · mock-1).' in the warn tone", async () => {
    const line = await modelLine(member());
    expect(line.textContent).toBe("Mock answer. No real model ran (mock · mock-1).");
    expect(line.className).toContain("text-tone-warn");
    expect(line.textContent).not.toContain("—");
  });

  it("a mock teammate that has not answered says so in plain sentences", async () => {
    const line = await modelLine(member({ status: "working", model: "" }));
    expect(line.textContent).toBe("On the mock. No real model ran (mock).");
  });

  it("control: a real model keeps the receipt's tense and the quiet colour", async () => {
    const line = await modelLine(member({ provider: "fleet-custom", model: "glm-5.3-flash" }));
    expect(line.textContent).toBe("Answered by fleet-custom · glm-5.3-flash");
    expect(line.className).toContain("text-zinc-500");
    expect(line.className).not.toContain("tone-warn");
  });

  it("Stop on a teammate says it in two plain sentences", async () => {
    render(<AgentCards members={[member({ status: "working", provider: "fleet-custom", model: "x" })]} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    fireEvent.click(await screen.findByTestId("mission-card-stop-researcher"));
    await waitFor(() =>
      expect(screen.getByText("Stopping. Jarvis carries on with the rest of the team.")).toBeTruthy(),
    );
    expect(H.posts.map((p) => p.path)).toEqual(["/sessions/m1/cancel"]);
  });

  it("the status word and bar use tone tokens", () => {
    render(<AgentCards members={[member({ status: "failed", progress: { pct: 40, label: "", basis: "plan" } })]} />);
    const card = screen.getByTestId("mission-card-researcher");
    expect(card.querySelector(".text-tone-danger")).not.toBeNull();
    expect(card.querySelector(".bg-tone-danger\\/70")).not.toBeNull();
  });
});

/* ---------------------------------------------- 2. the mission receipt --- */

describe("the mission receipt says the mock in the chat's words", () => {
  it("a completed mock mission: 'Mock answer. No real model ran.' + the way to Connections", () => {
    renderOutput(view());
    const receipt = screen.getByTestId("mission-receipt");
    expect(receipt.textContent).toBe("Mock answer. No real model ran. Connect a model on Connections");
    expect(receipt.className).toContain("text-tone-warn");
    expect(receipt.querySelector('a[href="/connections"]')).not.toBeNull();
  });

  it("a failed mock mission: 'On the mock. No real model ran.'", () => {
    renderOutput(view({ status: "failed" }));
    expect(screen.getByTestId("mission-receipt").textContent).toMatch(/^On the mock\. No real model ran\. /);
  });

  it("mockLine is the one wording both places read", () => {
    expect(mockLine(true)).toBe("Mock answer. No real model ran.");
    expect(mockLine(false)).toBe("On the mock. No real model ran.");
  });

  it("a card's parenthetical never breaks inside 'mock-1'", async () => {
    render(<AgentCards members={[member()]} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    const line = await screen.findByTestId("mission-card-model-researcher");
    const paren = [...line.querySelectorAll("span")].find((s) => s.textContent === "(mock · mock-1)");
    expect(paren?.className).toContain("whitespace-nowrap");
  });
});

/* ------------------------------------- 3. the rest of the mission copy --- */

describe("the mission screen's other sentences are plain, with no dash asides", () => {
  it("a refused retry shows the daemon's words as plain sentences", async () => {
    H.postErrors["/missions/s1/retry-failed"] = {
      status: 409,
      message: "a follow-up of this mission is already running or queued — wait for it to finish before retrying",
    };
    renderOutput(
      view(
        { status: "failed", provider: "fleet-custom", model: "x" },
        { deliverable: { text: "", documents: [], worklist: { total: 5, done: 3, failed: 2, pending: 0, doing: 0 } } },
      ),
    );
    const retry = screen.getByTestId("mission-retry-failed");
    fireEvent.click(retry);
    await waitFor(() =>
      expect(screen.getByTestId("mission-action-note").textContent).toBe(
        "A follow-up of this mission is already running or queued. Wait for it to finish before retrying.",
      ),
    );
    expect(screen.getByTestId("mission-action-note").className).toContain("text-tone-danger");
  });

  it("plainSentences: each dash becomes a stop, words kept, one stop at the end", () => {
    expect(plainSentences("this mission is still running — wait for it to finish before retrying")).toBe(
      "This mission is still running. Wait for it to finish before retrying.",
    );
    expect(plainSentences("nothing to do")).toBe("Nothing to do.");
    expect(plainSentences("Already said.")).toBe("Already said.");
    // A hyphen inside a word is not a clause break.
    expect(plainSentences("a follow-up is queued")).toBe("A follow-up is queued.");
  });

  it("a failed mission with no result says where to look in two sentences", () => {
    renderOutput(view({ status: "failed", provider: "fleet-custom", model: "x" }));
    expect(screen.getByTestId("mission-output-empty").textContent).toBe(
      "The team could not finish this objective. The activity below says where it stopped.",
    );
  });

  it("a finished mission with no files says so plainly on Preview", () => {
    renderOutput(view({ provider: "fleet-custom", model: "x" }));
    fireEvent.click(screen.getByTestId("mission-tab-preview"));
    expect(screen.getByTestId("mission-preview-empty").textContent).toBe(
      "The team did not create any files. The result is in Report.",
    );
  });

  it("a teammate's draft is labelled in plain sentences", () => {
    renderOutput(view({ status: "active", provider: "fleet-custom", model: "x" }), {
      ...EMPTY,
      members: { m1: { name: "Researcher", text: "Draft text", done: false, phase: "" } },
      latest: "m1",
    });
    expect(screen.getByTestId("mission-draft-note").textContent).toBe("Draft in progress. Researcher is writing.");
  });

  it("the follow-up box asks without a dash aside", () => {
    renderOutput(view({ provider: "fleet-custom", model: "x" }));
    const box = screen.getByTestId("mission-followup-input") as HTMLTextAreaElement;
    expect(box.placeholder).toBe("Ask for changes, for example make it shorter or add a summary table");
  });
});

/* ------------------------------------------------ 4. a source guard -------- */

const HUES =
  "slate|gray|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose";
const PROPS =
  "text|bg|border|ring|from|via|to|fill|stroke|outline|decoration|divide|placeholder|shadow|caret|accent";
const LITERAL_HUE = new RegExp(
  `(?<![\\w-])(?:${PROPS})(?:-[trblxyse])?-(?:${HUES})-\\d{2,3}(?:\\/(?:\\d{1,3}|\\[[0-9.]+\\]))?`,
  "g",
);
const HALF_PIXEL = /text-\[\d+\.5px\]/g;

/** The source with comments removed (JSX `{/* … *\/}`, block and line
 *  comments), so only code and copy are left. These files hold no `//` inside
 *  a string, which the line-comment cut relies on. */
function codeOnly(src: string): string {
  return src
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

describe("the mission files and the workflow chips keep to the calm rules", () => {
  const MISSION = ["components/agents/mission/AgentCards.tsx", "components/agents/mission/MissionOutput.tsx"];

  it("no em-dash aside in the mission files' copy (a lone dash placeholder is not an aside)", () => {
    for (const rel of MISSION) {
      const code = codeOnly(readSrc(rel)).replace(/"—"/g, "");
      const lines = code.split("\n").filter((l) => l.includes("—"));
      expect(lines, rel).toEqual([]);
    }
    // Anti-vacuity: the comment cut leaves the copy in place.
    expect(codeOnly(readSrc(MISSION[1]))).toContain("The team could not finish this objective.");
  });

  it("no literal hue and no half-pixel size in the mission files", () => {
    for (const rel of MISSION) {
      const src = readSrc(rel);
      expect([...src.matchAll(LITERAL_HUE)].map((m) => m[0]), rel).toEqual([]);
      expect([...src.matchAll(HALF_PIXEL)].map((m) => m[0]), rel).toEqual([]);
    }
  });

  it("every workflow chip, tile, glow and minimap fill is a theme token", () => {
    const metas = [
      ...AGENT_TYPES.map((t) => [t, AGENT_META[t].chip, AGENT_META[t].tile] as const),
      ...STEP_KINDS.map((k) => [k, KIND_META[k].chip, KIND_META[k].chip] as const),
    ];
    for (const [name, chip, tile] of metas) {
      for (const cls of [chip, tile]) {
        expect(cls, name).toMatch(/^border-(accent|tone-(violet|warn|success|info))\/30 bg-\1\/10 text-/);
        expect([...cls.matchAll(LITERAL_HUE)].length, `${name}: ${cls}`).toBe(0);
      }
    }
    for (const t of AGENT_TYPES) {
      expect(AGENT_META[t].glow, t).toMatch(/^rgb\(var\(--(accent-rgb|tone-[a-z]+)\)\/0\.55\)$/);
      expect(AGENT_META[t].hex, t).toMatch(/^rgb\(var\(--(accent-rgb|tone-[a-z]+)\)\)$/);
    }
    // The four tones each say a different thing at a glance.
    expect(KIND_META.ask.chip).toContain("text-tone-warn");
    expect(KIND_META.tool.chip).toContain("text-tone-info");
    expect(KIND_META.notify.chip).toContain("text-tone-success");
    expect(AGENT_META.planner.chip).toContain("text-tone-violet");
  });

  it("the step-kind blurbs the inspector shows are plain sentences", () => {
    for (const k of STEP_KINDS) expect(KIND_META[k].blurb, k).not.toContain("—");
    expect(KIND_META.tool.blurb).toBe("One tool call, the same every run, with no model.");
    expect(KIND_META.ask.blurb).toBe("The run pauses and asks you. It resumes when you answer.");
  });

  it("every tone the chips use is defined (no invented token)", () => {
    const tw = readSrc("tailwind.config.ts");
    const used = new Set<string>();
    const all = [
      ...AGENT_TYPES.flatMap((t) => [AGENT_META[t].chip, AGENT_META[t].glow]),
      ...STEP_KINDS.map((k) => KIND_META[k].chip),
    ].join(" ");
    for (const m of all.matchAll(/tone-([a-z]+)/g)) used.add(m[1]);
    expect(used.size).toBeGreaterThanOrEqual(4);
    const css = readSrc("app/globals.css");
    for (const tone of used) {
      expect(tw, tone).toMatch(new RegExp(`\\b${tone}: "rgb\\(var\\(--tone-${tone}\\)`));
      expect(css, tone).toContain(`--tone-${tone}:`);
    }
  });
});
