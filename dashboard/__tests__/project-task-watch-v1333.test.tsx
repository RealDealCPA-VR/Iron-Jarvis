import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";

/**
 * v1.333.0 (lane F, F3) — the project task strip watches its run without
 * eating the PC.
 *
 * The strip polled `GET /sessions/{id}` every 2 s on a bare setInterval: on
 * in a minimised window, a second tick fired while the first was still on
 * its way, and every answer carried the WHOLE transcript (~130 KB on a heavy
 * session) for a strip that reads only the session row. Now: the slim read
 * (`?transcript=0`, still nested), useVisibleInterval (no ticks while hidden,
 * ONE read on return), and an in-flight guard. Same 2 s cadence while seen.
 */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    calls: [] as string[],
    responses: {} as Record<string, unknown>,
    hang: false,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    api.calls.push(path);
    if (api.hang && path.startsWith("/sessions/")) return new Promise(() => {});
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    }
    if (r instanceof api.FakeApiError) return Promise.reject(r);
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({ ok: true }),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

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

import { ProjectTasks } from "@/components/project/ProjectTasks";

const RUN_KEY = "ij:projtask:proj_1";
const SLIM = "/sessions/sess_live?transcript=0";
const reads = () => api.calls.filter((c) => c.startsWith("/sessions/sess_live")).length;

function stashRun() {
  window.localStorage.setItem(
    RUN_KEY,
    JSON.stringify({
      id: "sess_live",
      project_id: "proj_1",
      task: "summarize",
      status: "active",
      output: "chat",
      target_path: null,
      created_at: "2026-10-11T00:00:00Z",
    }),
  );
}

let hidden = false;
function setHidden(h: boolean) {
  hidden = h;
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}
const advanceEach = async (stepMs: number, times: number) => {
  for (let i = 0; i < times; i += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(stepMs);
    });
  }
};

describe("ProjectTasks — watching a running task", () => {
  beforeEach(() => {
    api.calls.length = 0;
    api.hang = false;
    for (const k of Object.keys(api.responses)) delete api.responses[k];
    api.responses[SLIM] = {
      session: { id: "sess_live", task: "summarize", status: "active", created_at: "2026-10-11T00:00:00Z" },
      transcript: null,
    };
    window.localStorage.clear();
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

  it("reads the SLIM session (?transcript=0) every 2 s while the window is visible", async () => {
    stashRun();
    render(<ProjectTasks projectId="proj_1" hasRoot={false} sessions={[]} />);
    await act(async () => {});
    expect(api.calls).toContain(SLIM);
    expect(api.calls.filter((c) => c === "/sessions/sess_live")).toEqual([]);
    const first = reads();
    await advanceEach(2000, 5);
    expect(reads()).toBe(first + 5);
  });

  it("a hidden window reads nothing, then reads ONCE the moment it is visible again", async () => {
    stashRun();
    render(<ProjectTasks projectId="proj_1" hasRoot={false} sessions={[]} />);
    await act(async () => {});
    const first = reads();
    setHidden(true);
    await advanceEach(2000, 30); // a minute minimised
    expect(reads()).toBe(first);
    setHidden(false);
    await act(async () => {});
    expect(reads()).toBe(first + 1);
  });

  it("a tick never starts while the previous read is still on its way", async () => {
    api.hang = true; // a daemon that accepts and never answers
    stashRun();
    render(<ProjectTasks projectId="proj_1" hasRoot={false} sessions={[]} />);
    await act(async () => {});
    expect(reads()).toBe(1);
    await advanceEach(2000, 30);
    expect(reads()).toBe(1);
  });
});
