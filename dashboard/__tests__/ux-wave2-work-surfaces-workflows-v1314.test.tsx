/**
 * v1.314.0 — UX wave 2, track T2: the Workflows canvas's fresh demo reads as
 * an EXAMPLE.
 *
 * WHAT THE USER SAW (fresh__workflows): a brand-new user lands on a canvas
 * titled `demo-workflow` (slug style) with three steps and the trigger's
 * "ON RUN · CLICK TO CHANGE" in tracking-wide caps; nothing says it is an
 * example, and a plain Run/Save used the slug name.
 *
 * Verifier's adjustment (binding): NO separate slug key — the name is free
 * text, sent as-is to /workflows/run and restored by Load, so both defaults
 * (the initial state AND run()'s empty-name fallback) become a human name;
 * the "Example" notice hides once a saved workflow is loaded or the graph is
 * edited. Anti-vacuity: Load / Save / Schedule / Add step / Run workflow stay.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));

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
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
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
  appendDictation: (t: string, c: string) => (t ? `${t} ${c}` : c),
}));
// canvas-v1170's stub: jsdom cannot lay out React Flow.
vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@xyflow/react")>();
  const { createElement } = await import("react");
  return {
    ...actual,
    ReactFlow: ({ children }: { children?: ReactNode }) => createElement("div", { "data-testid": "rf-canvas" }, children),
    ReactFlowProvider: ({ children }: { children?: ReactNode }) => createElement("div", null, children),
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

const src = (rel: string): string => readFileSync(join(process.cwd(), rel), "utf8").replace(/\r\n/g, "\n");

/** The className of the nearest `className="…"` before `needle` (ux-wave1-design's helper). */
function classBefore(text: string, needle: string, window = 400): string {
  const at = text.indexOf(needle);
  if (at < 0) return `<<${needle} not found>>`;
  const head = text.slice(Math.max(0, at - window), at);
  const all = [...head.matchAll(/className="([^"]*)"/g)];
  return all.length ? all[all.length - 1][1] : "";
}

const SAVED_STEPS = [{ id: "s1", agent: "builder", task: "Do the thing", name: "Step 1" }];

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  getMock.mockImplementation(async () => ({ workflows: [] }));
  postMock.mockImplementation(async (path: string) =>
    path === "/workflows/run" ? { id: "r1", workflow_name: "x", status: "completed", steps_json: "[]", outputs_json: "{}" } : {},
  );
  localStorage.clear();
});
afterEach(cleanup);

describe("Workflows: the fresh canvas is an example, named like a person would", () => {
  it("the default name is not the slug 'demo-workflow' (nor any slug)", () => {
    render(<WorkflowCanvas />);
    const name = (screen.getByLabelText("Workflow name") as HTMLInputElement).value;
    expect(name).not.toBe("demo-workflow");
    expect(name.trim().length).toBeGreaterThan(0);
    expect(name).not.toMatch(/^[a-z0-9]+(?:-[a-z0-9]+)+$/);
  });

  it("a one-line notice says this is an example; anti-vacuity: every toolbar control stays", () => {
    render(<WorkflowCanvas />);
    const notice = screen.getByTestId("workflow-example-notice");
    expect(notice.textContent ?? "").toMatch(/example/i);
    for (const name of [/Load/, /^\s*Save\s*$/, /Add step/, /Run workflow/]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("link", { name: /Schedule/ })).toBeInTheDocument();
  });

  it("the notice hides once a saved workflow is loaded", async () => {
    render(<WorkflowCanvas />);
    expect(screen.getByTestId("workflow-example-notice")).toBeInTheDocument();
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("ij:load-workflow", {
          detail: { id: 1, name: "client-intake", description: "", steps_json: JSON.stringify(SAVED_STEPS), project_id: null },
        }),
      );
    });
    await screen.findByDisplayValue("client-intake");
    expect(screen.queryByTestId("workflow-example-notice")).toBeNull();
  });

  it("the notice hides once the graph is edited (Add step)", () => {
    render(<WorkflowCanvas />);
    expect(screen.getByTestId("workflow-example-notice")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Add step/ }));
    expect(screen.queryByTestId("workflow-example-notice")).toBeNull();
  });

  it("Run with the name cleared sends a human name, never 'demo-workflow'", async () => {
    render(<WorkflowCanvas />);
    fireEvent.change(screen.getByLabelText("Workflow name"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: /Run workflow/ }));
    await waitFor(() => expect(postMock.mock.calls.some((c) => c[0] === "/workflows/run")).toBe(true));
    const body = postMock.mock.calls.find((c) => c[0] === "/workflows/run")![1] as { name: string };
    expect(body.name).not.toBe("demo-workflow");
    expect(body.name.trim().length).toBeGreaterThan(0);
  });

  it("source: 'demo-workflow' is gone from the canvas (both defaults)", () => {
    expect(src("components/workflow/WorkflowCanvas.tsx")).not.toContain("demo-workflow");
  });

  it("source: the trigger's 'On run · click to change' is sentence case, not all-caps", () => {
    const cls = classBefore(src("components/workflow/TriggerNode.tsx"), "On run · click to change", 200);
    expect(cls).not.toMatch(/(^|\s)uppercase(\s|$)/);
  });
});
