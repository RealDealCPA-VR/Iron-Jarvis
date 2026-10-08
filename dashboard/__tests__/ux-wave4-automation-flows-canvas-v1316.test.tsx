/**
 * v1.316.0 — UX wave 4, track T3: the Workflows canvas's NAME field looks
 * like a field, and an empty name is asked for, never silently replaced.
 *
 * WHAT THE USER SAW (fresh__workflows): the name sat in a borderless input
 * that read as a heading, so nobody knew it could be edited.
 *
 * Verifier's adjustment (binding): if the name can be blank, Save must not
 * silently fall back to a default name (a second untitled save would
 * overwrite the first) — it focuses the name field and asks for one.
 *
 * INTERFACE: the name input keeps aria-label "Workflow name" (canvas-v1170
 * and ux-wave1-design pin it), has a visible border at rest (no
 * `border-transparent`) and the placeholder "Untitled workflow" — the same
 * words run() uses for a blank name. Anti-vacuity: Save with a name still
 * POSTs /workflows; Run with a blank name still runs.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

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
    api: vi.fn(async () => ({})),
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
vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@xyflow/react")>();
  const { createElement } = await import("react");
  return {
    ...actual,
    ReactFlow: ({ children }: { children?: ReactNode }) =>
      createElement("div", { "data-testid": "rf-canvas" }, children),
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

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  getMock.mockImplementation(async () => ({ workflows: [] }));
  postMock.mockImplementation(async (path: string) =>
    path === "/workflows/run"
      ? { id: "r1", workflow_name: "x", status: "completed", steps_json: "[]", outputs_json: "{}" }
      : {},
  );
  localStorage.clear();
});
afterEach(cleanup);

const nameBox = () => screen.getByLabelText("Workflow name") as HTMLInputElement;
const posted = (path: string) => postMock.mock.calls.filter((c) => c[0] === path);

describe("T3-W2 the workflow name looks editable", () => {
  it("has a visible border at rest and says 'Untitled workflow' when blank", () => {
    render(<WorkflowCanvas />);
    const box = nameBox();
    expect(box.className).not.toMatch(/(^|\s)border-transparent(\s|$)/);
    expect(box.getAttribute("placeholder")).toBe("Untitled workflow");
  });
});

describe("T3-W3 an empty name is asked for, never replaced", () => {
  it("Save with a blank name POSTs nothing, says so, and puts the cursor in the name field", async () => {
    render(<WorkflowCanvas />);
    fireEvent.change(nameBox(), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: /^\s*Save\s*$/ }));
    await screen.findByText(/Name the workflow before saving/);
    expect(posted("/workflows")).toHaveLength(0);
    expect(document.activeElement).toBe(nameBox());
  });

  it("control: Save with a name still POSTs /workflows under that name", async () => {
    render(<WorkflowCanvas />);
    fireEvent.change(nameBox(), { target: { value: "Client intake" } });
    fireEvent.click(screen.getByRole("button", { name: /^\s*Save\s*$/ }));
    await waitFor(() => expect(posted("/workflows")).toHaveLength(1));
    expect((posted("/workflows")[0][1] as { name: string }).name).toBe("Client intake");
  });

  it("control: Run with a blank name still runs (as 'Untitled workflow')", async () => {
    render(<WorkflowCanvas />);
    fireEvent.change(nameBox(), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /Run workflow/ }));
    await waitFor(() => expect(posted("/workflows/run")).toHaveLength(1));
    expect((posted("/workflows/run")[0][1] as { name: string }).name).toBe("Untitled workflow");
  });
});
