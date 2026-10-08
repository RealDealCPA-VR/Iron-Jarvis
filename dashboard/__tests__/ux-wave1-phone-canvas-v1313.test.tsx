/**
 * UX wave 1, track T4 — the Workflows toolbar on a phone (v1.313.0).
 *
 * WHAT THE USER SAW at 390px: Load / Save / Schedule… and then a fourth button
 * sliced at the card's edge; "Add step" and the primary "Run workflow" were
 * pushed outside the card (`overflow-hidden`) and could not be seen or tapped.
 * Cause: the action group in components/workflow/WorkflowCanvas.tsx is
 * `flex items-center gap-2` with no wrap.
 *
 * The verifier's fix (it WINS over the icon-only proposal): keep every text
 * label, make the group `flex flex-wrap … w-full sm:w-auto`, give Run workflow
 * `flex-1 sm:flex-none`, and anchor the Load menu `left-0 sm:left-auto
 * sm:right-0` — once Load wraps to the left edge a right-anchored 288px menu
 * would be clipped by the same overflow-hidden card.
 *
 * Harness = canvas-v1170's (React Flow renderer stubbed, toolbar real).
 * NOTE FOR THE COORDINATOR: WorkflowCanvas.tsx is in T2's ownership list
 * (components/workflow/**); /workflows is T4's finding.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";

const { getMock, postMock, patchMock, delMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  patchMock: vi.fn(),
  delMock: vi.fn(),
}));

vi.mock("@/lib/api", () => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: MockApiError,
    get: getMock,
    post: postMock,
    patch: patchMock,
    del: delMock,
    put: vi.fn(async () => ({})),
    API_BASE: "",
    ijToken: () => "",
  };
});

vi.mock("@/lib/useApi", () => ({
  useApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));

vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (text: string, chunk: string) => (text ? `${text} ${chunk}` : chunk),
}));

vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@xyflow/react")>();
  const { createElement } = await import("react");
  return {
    ...actual,
    ReactFlow: ({ children }: { children?: ReactNode }) =>
      createElement("div", { "data-testid": "rf-canvas" }, children),
    ReactFlowProvider: ({ children }: { children?: ReactNode }) =>
      createElement("div", null, children),
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    useReactFlow: () => ({ fitView: async () => false }),
  };
});

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import WorkflowCanvas from "@/components/workflow/WorkflowCanvas";

const tokens = (el: Element): string[] =>
  String((el as HTMLElement).className ?? "").split(/\s+/).filter(Boolean);

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  patchMock.mockReset();
  delMock.mockReset();
  getMock.mockImplementation(async () => ({ workflows: [] }));
  localStorage.clear();
});

afterEach(() => {
  cleanup();
});

function toolbarGroup(): HTMLElement {
  const run = screen.getByRole("button", { name: /Run workflow/ });
  return run.parentElement as HTMLElement;
}

describe("Workflows toolbar wraps on a phone instead of clipping Run workflow", () => {
  it("the action group wraps and drops to its own full-width line below sm", () => {
    render(<WorkflowCanvas />);
    const t = tokens(toolbarGroup());
    expect(t).toContain("flex");
    expect(t).toContain("flex-wrap");
    expect(t).toContain("w-full");
    expect(t).toContain("sm:w-auto");
  });

  it("Run workflow gets the full-width tap target on a phone and its own size on desktop", () => {
    render(<WorkflowCanvas />);
    const t = tokens(screen.getByRole("button", { name: /Run workflow/ }));
    expect(t).toContain("flex-1");
    expect(t).toContain("sm:flex-none");
    expect(t).toContain("btn-accent"); // still the primary action
  });

  it("every action keeps its text label, in the same order, in the same group", () => {
    // Anti-vacuity: nothing was turned icon-only or dropped.
    render(<WorkflowCanvas />);
    const group = toolbarGroup();
    const load = screen.getByRole("button", { name: /Load/ });
    const save = screen.getByRole("button", { name: /^Save$/ });
    const sched = screen.getByRole("link", { name: /Schedule…/ });
    const add = screen.getByRole("button", { name: /Add step/ });
    const run = screen.getByRole("button", { name: /Run workflow/ });
    const order = [load, save, sched, add, run];
    for (const el of order) expect(group.contains(el)).toBe(true);
    for (let i = 1; i < order.length; i++) {
      expect(
        order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
    expect(sched.getAttribute("href")).toMatch(/^\/schedules\?workflow=/);
  });

  it("the Load menu opens from the left edge on a phone (right-anchored from sm up)", async () => {
    render(<WorkflowCanvas />);
    fireEvent.click(screen.getByRole("button", { name: /Load/ }));
    const empty = await screen.findByText(/No saved workflows yet/);
    const menu = empty.closest(".absolute") as HTMLElement;
    expect(menu).not.toBeNull();
    const t = tokens(menu);
    expect(t).toContain("left-0");
    expect(t).toContain("sm:left-auto");
    expect(t).toContain("sm:right-0");
    // Anti-vacuity: the menu still works — refresh button present.
    expect(screen.getByRole("button", { name: "Refresh list" })).toBeInTheDocument();
  });
});
