/**
 * v1.330.0 (calm chat wave 13, O2): the Board card's buttons match, and the
 * last literal hues and fractional sizes on the board and in inline code go.
 *
 * The wave-12 audit found:
 *  1. On a failed card (the chat drawer's Board, the Agents project board),
 *     Retry was an accent-BORDERED 32px button beside the calm 28px Dismiss:
 *     two heights, two looks in one row. Retry now wears the calm Dismiss's
 *     own shape (CALM_CONFIRM.base: 12px, at least 28px tall, no border) and
 *     stays the row's one primary through a quiet accent fill + accent text.
 *  2. A review card's Approve used literal emerald hues, and the provider chip
 *     a half-pixel text-[10.5px]. Approve now reads in tone-success (every
 *     theme re-inks it, Daylight included; its shape is unchanged so it still
 *     pairs with Reject), the provider chip is 11px.
 *  3. Inline code in a reply was text-[0.85em]: 11.9px in a 14px reply. It is
 *     now `calc(1em - 1px)`: one pixel under the text around it, so it still
 *     follows a heading (15 -> 14px) or a 13px table cell (-> 12px), and it
 *     lands on a whole pixel wherever the surrounding text does. Measured live
 *     on the scratch stack: reply 14 -> 13, h2 15 -> 14, h3 14 -> 13, table
 *     13 -> 12, and the same in the Build pane's reply box.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

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

import { KanbanBoard } from "@/components/kanban/KanbanBoard";
import { Markdown } from "@/components/Markdown";
import { CALM_CONFIRM } from "@/components/ui";
import type { Review, SessionView } from "@/lib/types";

afterEach(() => {
  cleanup();
  hooks.responses = {};
  window.localStorage.clear();
});

/* ---------------------------------------------------------------- helpers */

const tokens = (el: Element | null | undefined) => (el?.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);
const BORDER = /^(?:[a-z-]+:)*border(?:-|$)/;
const HUES =
  "slate|gray|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose";
const LITERAL_HUE = new RegExp(`^(?:[a-z-]+:)*(?:text|bg|border|ring)-(?:${HUES})-\\d{2,3}(?:/.*)?$`);
const FRACTIONAL_SIZE = /^(?:[a-z-]+:)*text-\[(?:\d*\.\d+(?:px|em|rem)|[\d.]+em)\]$/;
/** Colour and state tokens: what may differ between a primary and a ghost. */
const COLOUR = /^(?:[a-z-]+:)*(?:bg|text-(?:zinc|accent|tone)|text-white)-?/;

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
const FAILED = sv("s-failed", { status: "failed", outcome: null });
const REVIEW = sv("s-review");
const REVIEWS: Record<string, Review> = {
  "s-review": { session_id: "s-review", changed_files: ["vendors.csv"], diff: "", risk: "low" },
};

function renderBoard() {
  hooks.responses["/sessions/teams"] = { parents: {} };
  return render(<KanbanBoard sessions={[FAILED, REVIEW]} reviews={REVIEWS} reload={() => {}} projectId="p1" />);
}

/* ========================================== 1. failed card: one look */

describe("a failed card's Retry and Dismiss are one row, one look", () => {
  it("Retry has no border and no fractional size", () => {
    renderBoard();
    const retry = screen.getByTestId("failed-retry");
    expect(retry.textContent).toContain("Retry");
    expect(tokens(retry).filter((c) => BORDER.test(c))).toEqual([]);
    expect(tokens(retry).filter((c) => FRACTIONAL_SIZE.test(c) || /^text-\[\d+px\]$/.test(c))).toEqual([]);
  });

  it("Retry wears the calm Dismiss's own shape: every shape token of Retry is one Dismiss has", () => {
    renderBoard();
    const retry = screen.getByTestId("failed-retry");
    const dismiss = screen.getByRole("button", { name: "Dismiss" });
    expect(dismiss.getAttribute("data-confirm-variant")).toBe("calm");
    const shape = tokens(retry).filter((c) => !COLOUR.test(c));
    // Anti-vacuity: the shape really carries the height, the size and the box.
    expect(shape).toEqual(
      expect.arrayContaining(["min-h-7", "text-xs", "rounded-lg", "px-2.5", "flex-1", "justify-center", "font-medium"]),
    );
    const theirs = new Set(tokens(dismiss));
    expect(shape.filter((c) => !theirs.has(c)), "Retry shape tokens Dismiss lacks").toEqual([]);
  });

  it("Retry carries every token of the calm button base except its own padding pick (the same 28px)", () => {
    renderBoard();
    const mine = new Set(tokens(screen.getByTestId("failed-retry")));
    const base = CALM_CONFIRM.base.split(/\s+/).filter((c) => c !== "py-1");
    expect(base.filter((c) => !mine.has(c))).toEqual([]);
    // The Dismiss beside it is given py-1.5 by the card; Retry uses the same.
    expect(mine.has("py-1.5")).toBe(true);
  });

  it("Retry is still the row's one primary: a quiet accent fill and accent text; Dismiss is the ghost", () => {
    renderBoard();
    const retry = tokens(screen.getByTestId("failed-retry"));
    expect(retry).toEqual(expect.arrayContaining(["bg-accent/10", "text-accent-soft"]));
    const dismiss = tokens(screen.getByRole("button", { name: "Dismiss" }));
    expect(dismiss.filter((c) => /^bg-/.test(c))).toEqual([]);
    expect(dismiss).toContain("text-zinc-400");
  });
});

/* ============================== 2. Approve and the provider chip */

describe("a card's Approve and provider chip: theme tones, whole pixels", () => {
  it("Approve reads in tone-success with no literal hue (Daylight re-inks it)", () => {
    renderBoard();
    const approve = screen.getByRole("button", { name: /Approve/ });
    expect(tokens(approve).filter((c) => LITERAL_HUE.test(c))).toEqual([]);
    expect(tokens(approve)).toEqual(
      expect.arrayContaining(["text-tone-success", "bg-tone-success/10", "border-tone-success/30"]),
    );
  });

  it("the provider chip is a whole 11px", () => {
    renderBoard();
    const chips = screen.getAllByTitle("fleet-lab / glm");
    expect(chips.length).toBeGreaterThan(0);
    for (const chip of chips) {
      expect(tokens(chip)).toContain("text-[11px]");
      expect(tokens(chip).filter((c) => FRACTIONAL_SIZE.test(c))).toEqual([]);
    }
  });

  it("the patterns catch what they claim (anti-vacuity)", () => {
    for (const bad of ["text-emerald-300", "border-emerald-500/30", "hover:bg-emerald-500/20"]) {
      expect(LITERAL_HUE.test(bad), bad).toBe(true);
    }
    for (const bad of ["text-[10.5px]", "text-[0.85em]", "text-[1em]"]) expect(FRACTIONAL_SIZE.test(bad), bad).toBe(true);
    for (const good of ["text-tone-success", "bg-tone-success/10", "text-[11px]", "text-[length:calc(1em-1px)]"]) {
      expect(LITERAL_HUE.test(good) || FRACTIONAL_SIZE.test(good), good).toBe(false);
    }
  });
});

/* ======================================================= 3. inline code */

const INLINE = "text-[length:calc(1em-1px)]";

describe("inline code: one pixel under the text around it", () => {
  it("inline code in a paragraph, a heading, a list and a table uses the 1em - 1px rule", () => {
    const { container } = render(
      <Markdown
        content={[
          "## Fix the `node_modules` path",
          "",
          "Set `NODE_PATH` once.",
          "",
          "- Delete `.next`.",
          "",
          "| Name | Value |",
          "| --- | --- |",
          "| `NODE_PATH` | `C:/tools` |",
        ].join("\n")}
      />,
    );
    const codes = [...container.querySelectorAll("code")];
    expect(codes.length).toBe(5);
    for (const c of codes) {
      expect(tokens(c), c.textContent ?? "").toContain(INLINE);
      expect(tokens(c).filter((t) => FRACTIONAL_SIZE.test(t)), c.textContent ?? "").toEqual([]);
    }
    // It sits INSIDE the heading, so it scales with it (no fixed size).
    expect(container.querySelector("h2 code")).toBeTruthy();
  });

  it("code inside a fenced block keeps the block's own size (no inline rule)", () => {
    const { container } = render(<Markdown content={"```\nnpm install\n```"} />);
    const code = container.querySelector("pre code");
    expect(code).toBeTruthy();
    expect(tokens(code)).not.toContain(INLINE);
  });

  it("source: Markdown.tsx has no fractional or em text size left", () => {
    const src = readFileSync(path.join(__dirname, "..", "components", "Markdown.tsx"), "utf8").replace(/\r\n/g, "\n");
    expect(src).toContain(INLINE);
    const hits = [...src.matchAll(/text-\[(?:\d*\.\d+(?:px|em|rem)|[\d.]+em)\]/g)].map((m) => m[0]);
    expect(hits).toEqual([]);
  });
});
