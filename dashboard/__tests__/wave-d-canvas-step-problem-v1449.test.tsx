/**
 * Wave D item 42, the canvas half: a save the daemon refused for ONE step
 * ("steps[i] (label): no tool named 'x' — …") opens that step's panel and
 * says the daemon's words there; another save clears it.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  appendDictation: (text: string, chunk: string) => (text ? `${text} ${chunk}` : chunk),
}));
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

import { ApiError } from "@/lib/api";
import WorkflowCanvas from "@/components/workflow/WorkflowCanvas";

afterEach(() => {
  cleanup();
  getMock.mockReset();
  postMock.mockReset();
});

const STEPS = [
  { name: "Gather", agent: "builder", task: "read things" },
  { name: "List", kind: "tool", tool: "list_foldr", args: { path: "." } },
];

describe("a save refused for one step is said beside that step", () => {
  it("opens the step's panel with the daemon's words; a good save clears them", async () => {
    getMock.mockImplementation(async () => ({ workflows: [] }));
    const detail = "steps[1] (List): no tool named 'list_foldr' — pick one of this install's tools in the step";
    postMock.mockImplementationOnce(async () => {
      throw new (ApiError as unknown as new (m: string, s: number) => Error)(detail, 422);
    });
    render(<WorkflowCanvas />);
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("ij:load-workflow", {
          detail: { id: 1, name: "tidy", description: "", steps_json: JSON.stringify(STEPS), project_id: null },
        }),
      );
    });
    await screen.findByDisplayValue("tidy");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const said = await screen.findByTestId("step-problem");
    expect(said.textContent).toBe("no tool named 'list_foldr' — pick one of this install's tools in the step");
    // It is the List step's panel that opened (its tool box holds the name).
    expect(screen.getByDisplayValue("list_foldr")).toBeTruthy();
    postMock.mockImplementation(async () => ({}));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByTestId("step-problem")).toBeNull());
  });
});
