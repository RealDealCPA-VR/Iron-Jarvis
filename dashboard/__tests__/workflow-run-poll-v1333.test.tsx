/**
 * v1.333.0 (lane F, F5) — the canvas stops reading a parked run nobody is
 * looking at.
 *
 * WorkflowCanvas polled `GET /workflows/runs/{id}` every 2 s on a bare
 * setInterval for as long as the run was not terminal — and a run parked on
 * an ask (`waiting`) is not terminal, so a minimised window read it every 2 s
 * for days. Now the poll runs through useVisibleInterval (no ticks while
 * hidden, ONE read on return) and a tick never starts while the previous
 * read is still on its way. Same 2 s cadence while the window is seen.
 *
 * Harness: canvas-v1170's (the React Flow renderer stubbed, the toolbar real).
 * Counts only.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";

const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
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

const STEPS = [{ name: "A", agent: "builder", task: "t" }];
const RUN_PATH = "/workflows/runs/wfrun-1";
const PARKED = {
  id: "wfrun-1",
  workflow_name: "parked",
  status: "waiting",
  steps_json: JSON.stringify(STEPS),
  outputs_json: "{}",
  waiting_json: JSON.stringify({ question: "Which folder?" }),
};

const runReads = () => getMock.mock.calls.filter((c) => c[0] === RUN_PATH).length;

let hidden = false;
function setHidden(h: boolean) {
  hidden = h;
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}
const flush = async (n = 5) => {
  for (let i = 0; i < n; i += 1) await act(async () => {});
};
const advanceEach = async (stepMs: number, times: number) => {
  for (let i = 0; i < times; i += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(stepMs);
    });
  }
};

/** Load a def, click Run: the run is created PARKED (`waiting`). */
async function startParkedRun() {
  render(<WorkflowCanvas />);
  await act(async () => {
    window.dispatchEvent(
      new CustomEvent("ij:load-workflow", {
        detail: { id: 1, name: "parked", description: "", steps_json: JSON.stringify(STEPS) },
      }),
    );
  });
  await flush();
  fireEvent.click(screen.getByRole("button", { name: /Run workflow/ }));
  await flush();
  expect(postMock.mock.calls.filter((c) => c[0] === "/workflows/run")).toHaveLength(1);
}

describe("WorkflowCanvas — polling a live run", () => {
  let hangRun = false;
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    hangRun = false;
    getMock.mockImplementation(async (path: string) => {
      if (path === RUN_PATH) {
        if (hangRun) return new Promise(() => {});
        return PARKED;
      }
      return { workflows: [] };
    });
    postMock.mockImplementation(async (path: string) =>
      path === "/workflows/run" ? { ...PARKED, status: "running" } : {},
    );
    hidden = false;
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => (hidden ? "hidden" : "visible"),
    });
    vi.useFakeTimers();
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    delete (document as unknown as Record<string, unknown>).hidden;
    delete (document as unknown as Record<string, unknown>).visibilityState;
  });

  it("a parked run is read every 2 s while the window is visible (unchanged)", async () => {
    await startParkedRun();
    expect(runReads()).toBe(0); // the first read is one interval after Run, as before
    await advanceEach(2000, 10);
    expect(runReads()).toBe(10);
  });

  it("a hidden window reads a parked run NEVER, then ONCE the moment it is visible again", async () => {
    await startParkedRun();
    await advanceEach(2000, 2);
    expect(runReads()).toBe(2);
    setHidden(true);
    await advanceEach(2000, 60); // two minutes minimised
    expect(runReads()).toBe(2);
    setHidden(false);
    await flush();
    expect(runReads()).toBe(3);
  });

  it("a tick never starts while the previous read is still on its way", async () => {
    hangRun = true;
    await startParkedRun();
    await advanceEach(2000, 30);
    expect(runReads()).toBe(1);
  });
});
