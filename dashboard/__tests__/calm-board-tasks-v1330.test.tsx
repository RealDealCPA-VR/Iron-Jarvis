/**
 * v1.330.0 (calm chat wave 12, N4): the project Tasks and Board go calm.
 *
 * The wave-11 audit found the project's Tasks and Board on the calm surfaces
 * (the chat drawer's Tasks / Board tabs, the Agents page's project board)
 * still drawing the default bordered chips and buttons: ProjectTasks'
 * `<Badge value="unknown" />` and its SessionStatusBadge rows, the board's
 * "Clear completed" / "Clear failed", and a card's waiting / outcome chips
 * and "Dismiss".
 *
 * What changed, and the one decision behind it:
 *  - SessionStatusBadge takes a `variant` (default "default"). The Sessions
 *    list and the session page pass nothing, so they keep the bordered pill.
 *  - ProjectTasks passes variant="calm" on BOTH places it renders (the chat
 *    drawer and the older project page): the Assignments rows right below
 *    its Recent runs are AssignmentRow, which is already calm on both, so a
 *    bare-only switch would have put two looks in one list on the project
 *    page.
 *  - KanbanBoard and SessionCard are calm everywhere. The board has no prop
 *    that tells the chat drawer's board from the project page's (both are
 *    `projectId` boards), and the two calm call sites (ProjectSurfaces,
 *    WorldBoard) are outside this change; one board with one look is also
 *    what the calm app wants. "Clear completed" / "Clear failed" only render
 *    on the unscoped Sessions board (a project board never bulk-clears), so
 *    that is where they are calm. Their words, two presses and the 3 s
 *    disarm are the same.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import ts from "typescript";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";

/* ------------------------------------------------------------------ mocks */

const hooks = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return { FakeApiError, responses: {} as Record<string, unknown> };
});

vi.mock("@/lib/api", () => ({
  ApiError: hooks.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  sseUrl: (p: string) => p,
  get: (p: string) => Promise.resolve(hooks.responses[p] ?? {}),
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/lib/useApi", () => {
  const read = (p: string | null) => ({
    data: p ? (hooks.responses[p] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  });
  return { useApi: read, usePolledApi: read };
});
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (prev: string, chunk: string) => prev + chunk,
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial", "animate", "exit", "transition", "variants", "layout",
    "whileHover", "whileTap", "whileInView", "viewport",
  ]);
  const cache = new Map<string, unknown>();
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const motion = new Proxy({} as Record<string, unknown>, {
    get: (_t, tag) => {
      const key = String(tag);
      if (!cache.has(key)) cache.set(key, tagFor(key));
      return cache.get(key);
    },
  });
  return {
    m: motion,
    motion,
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
  };
});

import { SessionStatusBadge } from "@/components/sessions/SessionStatusBadge";
import { KanbanBoard } from "@/components/kanban/KanbanBoard";
import { ProjectTasks } from "@/components/project/ProjectTasks";
import type { SessionView } from "@/lib/types";
import { calmUses, uncalm } from "./helpers/calmVariant";

afterEach(() => {
  cleanup();
  hooks.responses = {};
  window.localStorage.clear();
});

/* ---------------------------------------------------------------- helpers */

const tokens = (el: Element | null | undefined) => (el?.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);
const BORDER = /^(?:[a-z-]+:)*border(?:-|$)/;
const borders = (el: Element) => tokens(el).filter((c) => BORDER.test(c));

/** A calm Badge: the calm marker, no border, no fill, the tone on its dot. */
function expectCalmChip(chip: Element, dot: string, what: string) {
  expect(chip.getAttribute("data-badge-variant"), what).toBe("calm");
  expect(borders(chip), what).toEqual([]);
  expect(tokens(chip).filter((c) => /^bg-/.test(c)), what).toEqual([]);
  expect(tokens(chip.querySelector("span")), what).toContain(dot);
}

const at = "2026-10-10T09:00:00Z";
const sv = (id: string, over: Partial<SessionView> = {}): SessionView => ({
  id,
  project_id: "p1",
  task: `Task ${id}`,
  agent_type: "builder",
  provider: "fleet-lab",
  model: "glm",
  status: "completed",
  workspace_path: "",
  summary: "",
  origin: null,
  outcome: "completed",
  waiting_on: null,
  created_at: at,
  finished_at: at,
  ...over,
});
const WAITING = sv("s-wait", {
  status: "active",
  outcome: null,
  finished_at: null,
  waiting_on: { approval_id: "ap1", tool: "write_document" } as SessionView["waiting_on"],
});
const NEEDS = sv("s-needs", { outcome: "needs_you" });
const PART = sv("s-part", { outcome: "completed_with_failures" });
const OK = sv("s-ok");
const FAILED = sv("s-failed", { status: "failed", outcome: null });

/* ================================================== SessionStatusBadge */

describe("SessionStatusBadge: a variant prop, the default unchanged", () => {
  const cases: [string, SessionView, string][] = [
    ["waiting", WAITING, "Waiting for you · write_document"],
    ["needs you", NEEDS, "Completed · needs you"],
    ["plain status", OK, "completed"],
  ];

  it.each(cases)("%s: with no variant it is still the bordered pill", (_name, s, words) => {
    const { container } = render(<SessionStatusBadge session={s} />);
    const chip = screen.getByText(words);
    expect(chip.getAttribute("data-badge-variant")).toBeNull();
    expect(tokens(chip)).toContain("border");
    expect(container.querySelectorAll("[data-badge-variant]")).toHaveLength(0);
  });

  it.each(cases)('%s: variant="calm" is the quiet chip, same words, tone on the dot', (_name, s, words) => {
    render(<SessionStatusBadge session={s} variant="calm" />);
    const chip = screen.getByText(words);
    expectCalmChip(chip, s === OK ? "bg-tone-success" : "bg-tone-warn", words);
  });

  it("the waiting / outcome test ids still wrap the chip in both looks", () => {
    for (const variant of ["default", "calm"] as const) {
      render(<SessionStatusBadge session={WAITING} variant={variant} />);
      expect(screen.getByTestId("session-waiting-chip").textContent).toBe("Waiting for you · write_document");
      cleanup();
      render(<SessionStatusBadge session={NEEDS} variant={variant} />);
      expect(screen.getByTestId("session-outcome-chip").textContent).toBe("Completed · needs you");
      cleanup();
    }
  });
});

/* ================================================== the board, rendered */

describe("the Kanban board: calm chips on a card, calm Dismiss and Clear", () => {
  it("a waiting card and a short-of-the-job card wear calm chips", () => {
    hooks.responses["/sessions/teams"] = { parents: {} };
    render(<KanbanBoard sessions={[WAITING, NEEDS, PART]} reviews={{}} reload={() => {}} projectId="p1" />);
    expectCalmChip(
      within(screen.getByTestId("session-waiting-chip")).getByText(/Waiting for you/),
      "bg-tone-warn",
      "waiting",
    );
    const outcomes = screen.getAllByTestId("session-outcome-chip");
    expect(outcomes.map((o) => o.textContent)).toEqual(
      expect.arrayContaining(["Completed · needs you", "Completed · with failures"]),
    );
    for (const o of outcomes) expectCalmChip(o.firstElementChild as Element, "bg-tone-warn", o.textContent ?? "");
  });

  it("a failed card's Dismiss is the calm two-press ghost; Retry beside it is unchanged", () => {
    hooks.responses["/sessions/teams"] = { parents: {} };
    render(<KanbanBoard sessions={[FAILED]} reviews={{}} reload={() => {}} projectId="p1" />);
    const dismiss = screen.getByRole("button", { name: "Dismiss" });
    expect(dismiss.getAttribute("data-confirm-variant")).toBe("calm");
    expect(borders(dismiss)).toEqual([]);
    expect(tokens(dismiss)).toEqual(expect.arrayContaining(["flex-1", "justify-center"]));
    expect(screen.getByRole("button", { name: /Retry/ })).toBeTruthy();
  });

  it("the unscoped (Sessions) board: Clear completed / Clear failed are calm, keep their words and the 28px hit area", () => {
    hooks.responses["/sessions/teams"] = { parents: {} };
    render(<KanbanBoard sessions={[OK, NEEDS, FAILED]} reviews={{}} reload={() => {}} />);
    for (const name of ["Clear completed (2)", "Clear failed (1)"]) {
      const btn = screen.getByRole("button", { name });
      expect(btn.getAttribute("data-confirm-variant"), name).toBe("calm");
      expect(borders(btn), name).toEqual([]);
      // The calm geometry (12px, at least 28px tall), pulled in by -my-1 so
      // the lane header keeps its height. No half-pixel override left.
      expect(tokens(btn), name).toEqual(expect.arrayContaining(["min-h-7", "text-xs", "-my-1"]));
      expect(btn.className, name).not.toMatch(/text-\[\d+(?:\.\d+)?px\]|!px-|!py-/);
    }
  });

  it("a project board still offers no bulk clear (it would clear other projects)", () => {
    hooks.responses["/sessions/teams"] = { parents: {} };
    render(<KanbanBoard sessions={[OK, FAILED]} reviews={{}} reload={() => {}} projectId="p1" />);
    expect(screen.queryByRole("button", { name: /^Clear / })).toBeNull();
  });
});

/* ============================================== ProjectTasks, rendered */

describe("ProjectTasks: the Recent runs chips are calm", () => {
  it.each([
    ["the chat drawer (bare)", true],
    ["the project page", false],
  ])("%s: every run's chip is the calm Badge", async (_where, bare) => {
    render(
      <ProjectTasks projectId="p1" hasRoot={false} sessions={[WAITING, NEEDS, PART, OK, FAILED]} bare={bare} />,
    );
    await waitFor(() => expect(screen.getByText("Waiting for you · write_document")).toBeTruthy());
    const dots: Record<string, string> = {
      "Waiting for you · write_document": "bg-tone-warn",
      "Completed · needs you": "bg-tone-warn",
      "Completed · with failures": "bg-tone-warn",
      completed: "bg-tone-success",
      failed: "bg-tone-danger",
    };
    for (const [w, dot] of Object.entries(dots)) expectCalmChip(screen.getByText(w), dot, w);
  });
});

/* ===================================================== the source guard */

const DASH_ROOT = path.join(__dirname, "..");
const readRel = (rel: string) => readFileSync(path.join(DASH_ROOT, rel), "utf8").replace(/\r\n/g, "\n");

/** Every <SessionStatusBadge> in a file with its literal `variant` ("" when
 *  none, "{expr}" when not a plain string). */
function statusBadgeVariants(rel: string, src = readRel(rel)): string[] {
  const file = ts.createSourceFile(rel, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: string[] = [];
  const visit = (n: ts.Node) => {
    if ((ts.isJsxSelfClosingElement(n) || ts.isJsxOpeningElement(n)) && n.tagName.getText(file) === "SessionStatusBadge") {
      const attr = n.attributes.properties.find(
        (p): p is ts.JsxAttribute => ts.isJsxAttribute(p) && p.name.getText(file) === "variant",
      );
      const v = attr?.initializer;
      out.push(!attr ? "" : v && ts.isStringLiteral(v) ? v.text : "{expr}");
    }
    ts.forEachChild(n, visit);
  };
  visit(file);
  return out;
}

const CALM_BOARD_FILES = [
  "components/kanban/KanbanBoard.tsx",
  "components/kanban/SessionCard.tsx",
  "components/project/ProjectTasks.tsx",
];

describe("source guard: the board and the project Tasks stay calm", () => {
  it.each(CALM_BOARD_FILES)('%s: every Badge and ConfirmButton says variant="calm"', (rel) => {
    expect(calmUses(rel).length, rel).toBeGreaterThan(0);
    expect(uncalm(rel)).toEqual([]);
  });

  it("the counts the audit named are all there (nothing was dropped to pass)", () => {
    const count = (rel: string, tag: string) => calmUses(rel).filter((u) => u.tag === tag && u.calm).length;
    expect(count("components/kanban/KanbanBoard.tsx", "ConfirmButton")).toBe(2); // Clear completed / failed
    expect(count("components/kanban/SessionCard.tsx", "Badge")).toBe(2); // waiting / outcome
    expect(count("components/kanban/SessionCard.tsx", "ConfirmButton")).toBe(1); // Dismiss
    expect(count("components/project/ProjectTasks.tsx", "Badge")).toBe(1); // unknown
  });

  it('ProjectTasks passes variant="calm" to every SessionStatusBadge', () => {
    const uses = statusBadgeVariants("components/project/ProjectTasks.tsx");
    expect(uses).toHaveLength(2);
    expect(uses).toEqual(["calm", "calm"]);
  });

  it("the Sessions pages pass no variant, so they keep the bordered pill (the default is unchanged)", () => {
    for (const rel of ["app/sessions/page.tsx", "app/sessions/[id]/page.tsx"]) {
      const uses = statusBadgeVariants(rel);
      expect(uses.length, rel).toBeGreaterThan(0);
      expect(uses.every((v) => v === ""), rel).toBe(true);
    }
  });

  it("the reader finds a missing, a literal and an expression variant (anti-vacuity)", () => {
    const probe = [
      "const a = <SessionStatusBadge session={s} />;",
      'const b = <SessionStatusBadge session={s} variant="calm" />;',
      "const c = <SessionStatusBadge session={s} variant={look} />;",
      '// <SessionStatusBadge variant="calm" /> in a comment',
    ].join("\n");
    expect(statusBadgeVariants("probe.tsx", probe)).toEqual(["", "calm", "{expr}"]);
  });
});
