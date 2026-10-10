/**
 * v1.329.0 — calm chat wave 9, K4: the /agents "Your team" tab gets the calm
 * look of the New task view beside it, and the last mission dashes go.
 *
 * The verification audit after wave 8 (v8team__default__desk.png,
 * v8team__mark1__phone.png) found the tab still pre-calm:
 *  - a large bordered outer card (TeamScreen's card-surface);
 *  - bordered "Assign work" and "Appearance" sub-cards;
 *  - shouted labels (ASSIGN WORK / APPEARANCE / SHAPE / EYES / COLOUR);
 *  - an accent-bordered "Roster" pill and a filled "Queue for Builder";
 *  - "Nothing queued — give Builder a job above and it runs when Builder is
 *    free." (AgentInbox.tsx).
 *
 * Now: no outer card; the tabs are quiet text tabs; every part (Inbox,
 * Folder, Coach, Appearance) is a plain section under a hairline with a quiet
 * sentence-case label; the agent's one card is the Assign work composer,
 * drawn like the mission composer; each section has ONE primary, quiet until
 * there is something to do; tone tokens, whole pixels, plain sentences. The
 * same panel is the body of the AgentsModal dialog, so both are checked.
 * WorldBoard's empty state reads as plain sentences too.
 */

import React from "react";
import { readdirSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const T = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: unknown }>,
}));

function apiState(p: string | null) {
  return { data: p ? (T.api[p] ?? null) : null, error: null, loading: false, reload: () => {} };
}

vi.mock("@/lib/useApi", () => ({
  useApi: (p: string | null) => apiState(p),
  usePolledApi: (p: string | null) => apiState(p),
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
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
    get: (p: string) => Promise.resolve(T.api[p] ?? {}),
    post: (p: string, body?: unknown) => {
      T.posts.push({ path: p, body });
      return Promise.resolve({ assignment: { id: "a1" }, created: true });
    },
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDocumentVisible", () => ({ useDocumentVisible: () => true }));
vi.mock("@/lib/useReviews", () => ({ useReviews: () => ({ reviews: {}, loading: false, reload: () => {} }) }));
vi.mock("@/components/kanban/KanbanBoard", () => ({ KanbanBoard: () => null }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "whileHover", "layout"]);
  const cache = new Map<string, (props: Record<string, unknown>) => unknown>();
  const tagFor = (tag: string) => {
    let c = cache.get(tag);
    if (!c) {
      c = (props: Record<string, unknown>) => {
        const rest: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
        return createElement(tag, rest);
      };
      cache.set(tag, c);
    }
    return c;
  };
  return {
    // v1.250.0 mock contract: every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: unknown }) => createElement(Fragment, null, children as never),
    useReducedMotion: () => true,
    motion: new Proxy({} as Record<string, unknown>, { get: (_t, tag) => tagFor(String(tag)) }),
  };
});

import { AgentsModal, AgentsPanel } from "@/components/agents/AgentsModal";
import { AgentInbox, healthLine, queuedSentence } from "@/components/agents/AgentInbox";
import { LivePill, type RosterEntry } from "@/components/agents/RosterStrip";
import { WorldBoard } from "@/components/agents/world/WorldBoard";
import { DASHBOARD_ROOT, asides, copyPieces, readSrc } from "./helpers/dashGuard";
import { calmUses, uncalm } from "./helpers/calmVariant";

const classes = (el: Element | null | undefined) => (el?.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);

/** A boxed card: the app's card class, or a FULL border (a hairline on one
 *  side, `border-t` / `border-b` / `border-r`, is not a box). */
function looksBoxed(el: Element): boolean {
  const c = classes(el);
  return c.includes("card-surface") || c.includes("border") || c.some((x) => /^border-(white|accent|zinc)/.test(x));
}
const boxes = (root: Element) => Array.from(root.querySelectorAll("*")).filter(looksBoxed);
const shouted = (root: Element) => Array.from(root.querySelectorAll("*")).filter((el) => classes(el).includes("uppercase"));

const BUILDER: RosterEntry = {
  name: "builder",
  kind: "builtin",
  description: "hands-on doer for files, shell, and documents",
  delegable: true,
  healthy: true,
  stats: null,
} as RosterEntry;
const LEDGER: RosterEntry = {
  name: "custom:ledger",
  kind: "dynamic",
  description: "matches the bank feed to the ledger",
  delegable: true,
  healthy: true,
  stats: null,
} as RosterEntry;
const HERMES: RosterEntry = {
  name: "remote:hermes",
  kind: "remote",
  description: "a Hermes on the lab box",
  delegable: true,
  healthy: false,
  stats: null,
} as RosterEntry;
const EMPTY_INBOX = { inbox: { queued: [], claimed: [], running: [], blocked: [], recent: [] }, health: null };

beforeEach(() => {
  T.posts = [];
  T.api = {
    "/agents/faces": { faces: {} },
    "/projects": { projects: [{ id: "p1", name: "Harbor Street Cafe", status: "active" }] },
    "/agents/builder/inbox": { assignee: "builder", ...EMPTY_INBOX },
    "/agents/custom%3Aledger/inbox": { assignee: "custom:ledger", ...EMPTY_INBOX },
    "/agents/ledger/files": {
      name: "ledger",
      instructions: "# Ledger\nMatch every line.",
      notes: "",
      revisions: [{ id: "r1", at: "2026-10-01T10:00:00Z", reason: "coach", bytes: 120 }],
      folder: "C:\\sample\\agents\\ledger",
    },
    "/agents/ledger/coach": {
      report: { agent: "ledger", runs: [], clusters: [] },
      proposals: [
        {
          id: "p1",
          agent: "ledger",
          status: "pending",
          kind: "change",
          target: "AGENTS.md",
          rationale: "It keeps calling the shell tool to read CSV files.",
          before: "Match every line.",
          after: "Match every line.\nRead CSV files with read_file.",
        },
      ],
      last_reason: "",
    },
  };
});
afterEach(cleanup);

function panel(over: Partial<React.ComponentProps<typeof AgentsPanel>> = {}) {
  const props = {
    roster: [BUILDER, LEDGER, HERMES],
    dynamic: [],
    remotes: [],
    models: [],
    selected: { kind: "builtin" as const, name: "builder" },
    onSelect: vi.fn(),
    onAgentsChanged: vi.fn(),
    onRemotesChanged: vi.fn(),
    ...over,
  };
  render(<AgentsPanel {...props} />);
  return { props, root: screen.getByTestId("agents-panel") };
}

/* ------------------------------------------------------------- 1. panel --- */

describe("the Your team panel is plain sections, not boxes", () => {
  it("no card-surface and no bordered box anywhere in the agent's screen", async () => {
    const { root } = panel();
    await screen.findByTestId("agent-inbox-builder");
    expect(boxes(root).map((el) => el.getAttribute("data-testid") || el.tagName)).toEqual([]);
    // Anti-vacuity: the screen really drew the agent's parts.
    expect(screen.getByTestId("agent-detail-builder")).toBeTruthy();
    expect(screen.getByTestId("face-picker-builder")).toBeTruthy();
  });

  it("the agent's ONE card is the Assign work composer, drawn like the mission composer", async () => {
    const { root } = panel();
    const card = await screen.findByTestId("inbox-assign-card-builder");
    const filled = Array.from(root.querySelectorAll("*")).filter((el) => classes(el).includes("bg-ink-800"));
    expect(filled).toEqual([card]);
    const box = within(card).getByLabelText("Assign work");
    expect(classes(box)).toContain("bg-transparent");
    expect(classes(box)).not.toContain("field");
  });

  it("no shouted label: Assign work, Appearance, Shape, Eyes and Colour are sentence case", async () => {
    const { root } = panel();
    await screen.findByTestId("agent-inbox-builder");
    expect(shouted(root)).toEqual([]);
    for (const word of ["Assign work", "Appearance", "Shape", "Eyes", "Colour", "Inbox"]) {
      const el = within(root).getByText(word);
      expect(classes(el), word).not.toContain("uppercase");
      expect(classes(el).some((c) => c.startsWith("tracking-")), word).toBe(false);
    }
  });

  it("each section sits under a hairline with a quiet label", async () => {
    panel();
    const inbox = await screen.findByTestId("agent-inbox-builder");
    const look = screen.getByTestId("agent-appearance-builder");
    for (const section of [inbox, look]) {
      expect(classes(section)).toEqual(expect.arrayContaining(["border-t", "hairline"]));
      const label = section.querySelector("h3");
      expect(classes(label)).toEqual(expect.arrayContaining(["text-[13px]", "font-medium", "text-zinc-400"]));
      expect(classes(label)).not.toContain("font-semibold");
    }
  });

  it("the tabs are quiet text tabs: the open one in ink, neither a pill", () => {
    const { root } = panel();
    const tabs = within(root).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Agents", "New & manage"]);
    const [on, off] = tabs;
    expect(on.getAttribute("aria-selected")).toBe("true");
    expect(classes(on)).toContain("text-zinc-100");
    expect(classes(off)).toContain("text-zinc-500");
    for (const t of tabs) {
      expect(classes(t).some((c) => c.startsWith("bg-") || c.startsWith("ring-")), t.textContent ?? "").toBe(false);
    }
    // On the page the heading is for a screen reader (the tab above says it).
    expect(classes(within(root).getByRole("heading", { name: "Your team" }))).toContain("sr-only");
  });

  it("Queue is the inbox's one primary: quiet until there is a task, then the accent", async () => {
    const { root } = panel();
    const queue = (await screen.findByTestId("inbox-queue-builder")) as HTMLButtonElement;
    expect(queue.disabled).toBe(true);
    expect(classes(queue)).not.toContain("btn-accent");
    expect(root.querySelectorAll(".btn-accent")).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Assign work"), { target: { value: "Draft the supplier price list" } });
    expect(queue.disabled).toBe(false);
    expect(classes(queue)).toContain("btn-accent");
    // ONE accent on the agent's screen: Apply face stays quiet (no change).
    expect(root.querySelectorAll(".btn-accent")).toHaveLength(1);
    fireEvent.click(queue);
    await waitFor(() =>
      expect(screen.getByText("Queued for Builder. It runs when Builder is free.")).toBeTruthy(),
    );
    expect(T.posts.map((p) => p.path)).toEqual(["/assignments"]);
  });

  it("the inbox's empty line is two plain sentences", async () => {
    panel();
    const line = await screen.findByTestId("inbox-empty-builder");
    expect(line.textContent).toBe("Nothing queued. Give builder a job above and it runs when builder is free.");
  });

  it("the open row is a soft fill, not an accent-bordered box; a remote's kind is a quiet word", () => {
    const { root } = panel();
    const list = within(root).getByTestId("agents-modal-list");
    const open = list.querySelector('[data-open="true"]');
    expect(open?.textContent).toContain("builder");
    expect(classes(open)).toContain("bg-white/[0.06]");
    expect(classes(open).some((c) => c.startsWith("border"))).toBe(false);
    const kind = within(list).getByTestId("roster-kind-hermes");
    expect(classes(kind)).toContain("text-zinc-500");
    expect(classes(kind).some((c) => c.startsWith("border") || c.startsWith("bg-"))).toBe(false);
    // The offline mark is the danger TONE, not a literal rose pill.
    const off = within(list).getByText("offline");
    expect(classes(off)).toContain("text-tone-danger");
    // New agent is a ghost row (no dashed box) that still opens New & manage.
    const add = within(list).getByTestId("agents-modal-new");
    expect(classes(add).some((c) => c.startsWith("border"))).toBe(false);
    fireEvent.click(add);
    expect(within(root).getByRole("tab", { name: "New & manage" }).getAttribute("aria-selected")).toBe("true");
  });
});

/* ---------------------------------------------- 2. a custom agent's parts --- */

describe("a custom agent's folder and coach are plain sections too", () => {
  it("Folder and Coach sit under hairlines; a proposal is a row; Accept is the coach's one primary", async () => {
    panel({ selected: { kind: "dynamic", name: "ledger" } });
    const files = await screen.findByTestId("files-ledger");
    const coach = screen.getByTestId("coach-ledger");
    for (const section of [files, coach]) {
      expect(classes(section)).toEqual(expect.arrayContaining(["border-t", "hairline"]));
      expect(boxes(section)).toEqual([]);
      expect(shouted(section)).toEqual([]);
    }
    const proposal = within(coach).getByTestId("coach-proposal-p1");
    expect(looksBoxed(proposal)).toBe(false);
    expect(classes(within(coach).getByTestId("coach-accept-p1"))).toContain("btn-accent");
    expect(classes(within(coach).getByTestId("coach-decline-p1"))).not.toContain("btn-accent");
    expect(coach.querySelectorAll(".btn-accent")).toHaveLength(1);
    // The folder's Save buttons are quiet until there is something to save.
    fireEvent.click(within(files).getByTestId("files-edit-ledger"));
    expect(classes(within(files).getByTestId("files-save-ledger"))).not.toContain("btn-accent");
  });
});

/* ------------------------------------------------- 3. New & manage tab --- */

describe("New & manage is calm: quiet labels, no boxed forms, one primary per form", () => {
  it("no box, no shouted label; Create agent is quiet until a name and persona are typed", () => {
    const { root } = panel();
    fireEvent.click(within(root).getByRole("tab", { name: "New & manage" }));
    const mine = within(root).getByTestId("your-agents-section");
    const remote = within(root).getByTestId("remote-agents-section");
    expect(boxes(root)).toEqual([]);
    expect(shouted(root)).toEqual([]);
    for (const form of [within(mine).getByTestId("create-agent-form"), within(remote).getByTestId("connect-remote-form")]) {
      expect(classes(form)).toEqual(expect.arrayContaining(["border-t", "hairline"]));
    }
    const create = within(mine).getByRole("button", { name: /Create agent/ }) as HTMLButtonElement;
    expect(create.disabled).toBe(true);
    expect(classes(create)).not.toContain("btn-accent");
    expect(root.querySelectorAll(".btn-accent")).toHaveLength(0);
    fireEvent.change(within(mine).getByLabelText("Agent name"), { target: { value: "skeptic" } });
    fireEvent.change(within(mine).getByLabelText("Persona prompt"), { target: { value: "You challenge assumptions." } });
    expect(create.disabled).toBe(false);
    expect(classes(create)).toContain("btn-accent");
    expect(root.querySelectorAll(".btn-accent")).toHaveLength(1);
    // The remote form keeps its own one primary, quiet until filled.
    const connect = within(remote).getByRole("button", { name: /Connect remote/ });
    expect(classes(connect)).not.toContain("btn-accent");
  });

  it("its copy is plain sentences", () => {
    const { root } = panel();
    fireEvent.click(within(root).getByRole("tab", { name: "New & manage" }));
    const text = root.textContent ?? "";
    expect(text).toContain("An agent of your own is a persona prompt plus an optional preferred model. It carries both into every thread it joins.");
    expect(text).not.toMatch(/(^|\s)[—–](\s|$)/);
    expect(within(root).getByLabelText("Agent name").getAttribute("placeholder")).toBe("Name, for example skeptic");
  });
});

/* ------------------------------------------------------- 4. the dialog --- */

describe("the AgentsModal dialog wraps the very same calm panel", () => {
  it("a visible heading and a Close; the same panel, no boxes, no shouted labels", async () => {
    const onClose = vi.fn();
    render(
      <AgentsModal
        roster={[BUILDER]}
        dynamic={[]}
        remotes={[]}
        models={[]}
        selected={null}
        onSelect={() => {}}
        onAgentsChanged={() => {}}
        onRemotesChanged={() => {}}
        onClose={onClose}
      />,
    );
    const dialog = await screen.findByTestId("agents-modal");
    const root = within(dialog).getByTestId("agents-panel");
    const h = within(root).getByRole("heading", { name: "Agents" });
    expect(classes(h)).not.toContain("sr-only");
    await within(root).findByTestId("agent-inbox-builder");
    expect(boxes(root)).toEqual([]);
    expect(shouted(root)).toEqual([]);
    fireEvent.click(within(root).getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

/* --------------------------------------------------- 5. words and dots --- */

describe("the team tab's words are plain and its marks are tone tokens", () => {
  it("queuedSentence and healthLine carry no dash aside", () => {
    expect(queuedSentence("custom:ledger-checker")).toBe("Queued for ledger-checker. It runs when ledger-checker is free.");
    expect(queuedSentence("file_manager")).toBe("Queued for File manager. It runs when File manager is free.");
    expect(healthLine(null).text).toBe("Never ran");
  });

  it("the health dot and the liveness pill are tone tokens, not literal hues", () => {
    T.api["/agents/builder/inbox"] = {
      assignee: "builder",
      ...EMPTY_INBOX,
      health: { last_run_at: new Date().toISOString(), last_outcome: "failed", last_error: "disk full", last_wake_at: null, queued: 0, running: 0, blocked: 0 },
    };
    render(<AgentInbox name="builder" />);
    const health = screen.getByTestId("inbox-health-builder");
    expect(health.textContent).toMatch(/^Last ran .+ · failed: disk full$/);
    expect(classes(health.querySelector("span"))).toContain("bg-tone-danger");
    cleanup();
    render(<LivePill state="busy" bare="builder" testId="pill" />);
    const pill = screen.getByTestId("pill");
    expect(classes(pill)).toContain("text-tone-warn");
    expect(classes(pill).some((c) => c.startsWith("border") || /^text-\[\d+\.5px\]$/.test(c))).toBe(false);
    expect(classes(screen.getByTestId("pill-dot"))).toContain("bg-tone-warn");
  });

  it("WorldBoard's empty state is two plain sentences", () => {
    T.api["/sessions?project_id=p1"] = { sessions: [] };
    render(<WorldBoard projectId="p1" />);
    expect(screen.getByTestId("world-board").textContent).toContain(
      "No work on this project's board yet. Give the team a task.",
    );
  });
});

/* ----------------------------------------------------- 6. source guard --- */

/** The team tab's files: everything the Your team panel draws, plus the
 *  WorldBoard and the shared look module. */
const TEAM_FILES = [
  "components/agents/AgentsModal.tsx",
  "components/agents/AgentInbox.tsx",
  "components/agents/AgentFiles.tsx",
  "components/agents/AgentCoach.tsx",
  "components/agents/AgentPortrait.tsx",
  "components/agents/PortraitCropper.tsx",
  "components/agents/SetupCard.tsx",
  "components/agents/teamLook.ts",
  "components/agents/mission/TeamScreen.tsx",
  "components/agents/world/WorldBoard.tsx",
];

/* The no-dash reader is the shared one (__tests__/helpers/dashGuard.ts,
   v1.330.0): it reads every string literal, template piece and JSX text node
   through the TypeScript parser (comments never count) and also catches a
   lone dash that a `{" "}` sibling turns into an aside. */

const HUES =
  "slate|gray|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose";
const PROPS =
  "text|bg|border|ring|from|via|to|fill|stroke|outline|decoration|divide|placeholder|shadow|caret|accent";
const LITERAL_HUE = new RegExp(
  `(?<![\\w-])(?:${PROPS})(?:-[trblxyse])?-(?:${HUES})-\\d{2,3}(?:\\/(?:\\d{1,3}|\\[[0-9.]+\\]))?`,
  "g",
);
const HALF_PIXEL = /text-\[\d+\.5px\]/g;

describe("the team tab's files keep to the calm rules", () => {
  it("no spaced em or en dash in any user-visible string or JSX text", () => {
    expect(TEAM_FILES.flatMap((rel) => asides(rel))).toEqual([]);
  });

  it("the guard reads copy, not comments (anti-vacuity)", () => {
    const inbox = copyPieces("components/agents/AgentInbox.tsx").map((p) => p.text).join("\n");
    expect(inbox).toContain("Nothing queued. Give ");
    const probe = ['// a comment — fine', 'const a = "one — two";', "const b = <p>left — right</p>;", 'const c = "—";'].join("\n");
    expect(asides("probe.tsx", probe).map((s) => s.split(":")[1])).toEqual(["2", "3"]);
  });

  it("no literal hue and no half-pixel size", () => {
    for (const rel of TEAM_FILES) {
      const src = readSrc(rel);
      expect([...src.matchAll(LITERAL_HUE)].map((m) => m[0]), rel).toEqual([]);
      expect([...src.matchAll(HALF_PIXEL)].map((m) => m[0]), rel).toEqual([]);
    }
  });

  it("the Your team screen has no outer card", () => {
    const team = readSrc("components/agents/mission/TeamScreen.tsx").replace(/\{\/\*[\s\S]*?\*\/\}/g, "").replace(/\/\/[^\n]*/g, "");
    expect(team).not.toContain("card-surface");
    // It still keeps its window height from md (the coordinator pin's var).
    expect(team).toContain("md:h-[calc(100vh-9rem-var(--ij-strip-h,0px))]");
  });
});

/* v1.330.0 (calm chat wave 11, M4): the inbox's status chip, a custom agent's
   and a remote agent's Delete, a remote's kind chip, the built-in fallback
   chips and a project's Completed outcome chip were the default (bordered,
   tinted) Badge and ConfirmButton. Every <Badge> and <ConfirmButton> in the
   team tab's files and the project lists (components/agents/world) is the
   calm variant now; a default one, or a spread that could hide the variant,
   fails here. */
describe("the team tab and the project lists draw every Badge and ConfirmButton calm", () => {
  const WORLD_DIR = "components/agents/world";
  const CALM_FILES = [
    ...TEAM_FILES.filter((f) => f.endsWith(".tsx")),
    ...readdirSync(path.join(DASHBOARD_ROOT, WORLD_DIR))
      .filter((f) => f.endsWith(".tsx"))
      .map((f) => `${WORLD_DIR}/${f}`),
  ];

  it.each(CALM_FILES)("%s has no default Badge or ConfirmButton", (rel) => {
    expect(uncalm(rel)).toEqual([]);
  });

  it("the guard sees the real chips and buttons (anti-vacuity)", () => {
    expect(CALM_FILES).toContain("components/agents/world/CompletedList.tsx");
    const n = (rel: string) => calmUses(rel).length;
    // The custom row's Delete, the remote row's kind chip, disabled chip and
    // Delete, the built-ins' fallback chip.
    expect(n("components/agents/SetupCard.tsx")).toBeGreaterThanOrEqual(5);
    expect(n("components/agents/AgentInbox.tsx")).toBeGreaterThanOrEqual(1);
    expect(n("components/agents/world/CompletedList.tsx")).toBeGreaterThanOrEqual(1);
  });

  it("no hand-rolled bordered status pill is left on an agent row", () => {
    const setup = readSrc("components/agents/SetupCard.tsx");
    expect(setup).not.toMatch(/rounded-md border border-zinc-500\/25 bg-zinc-500\/10/);
    expect(setup).not.toMatch(/rounded-md border border-accent\/30 bg-accent\/\[0\.08\][^"]*font-mono/);
  });
});
