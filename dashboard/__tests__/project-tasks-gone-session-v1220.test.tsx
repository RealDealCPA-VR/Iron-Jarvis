import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

/**
 * v1.220.0 — a rehydrated project task whose session no longer exists.
 *
 * ProjectTasks stashes the started run in localStorage so a live strip
 * survives a reload, and rehydrates it on every mount. When that session has
 * since been deleted (Sessions page, a reset database) the 2s poll got a 404,
 * rendered "status check failed — retrying…" and retried forever: the run
 * never reaches a terminal status, and nothing ever cleared the stash. The
 * poll now treats a 404 as "this run is gone": the strip disappears and the
 * stash is forgotten, so the next mount starts clean.
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
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    api.calls.push(path);
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
    default: ({
      href,
      children,
      ...rest
    }: {
      href: string;
      children?: React.ReactNode;
    }) => createElement("a", { href, ...rest }, children),
  };
});

import { ProjectTasks } from "@/components/project/ProjectTasks";

const RUN_KEY = "ij:projtask:proj_1";

function stashRun(id: string) {
  window.localStorage.setItem(
    RUN_KEY,
    JSON.stringify({
      id,
      project_id: "proj_1",
      task: "summarize",
      status: "active",
      output: "chat",
      target_path: null,
      created_at: "2026-09-01T00:00:00Z",
    }),
  );
}

describe("ProjectTasks — a stashed run whose session is gone", () => {
  beforeEach(() => {
    api.calls.length = 0;
    for (const k of Object.keys(api.responses)) delete api.responses[k];
    window.localStorage.clear();
  });
  afterEach(() => cleanup());

  it("drops the strip and forgets the stash on a 404", async () => {
    stashRun("sess_gone");
    api.responses["/sessions/sess_gone"] = new api.FakeApiError("no such session", 404);

    render(
      <ProjectTasks projectId="proj_1" hasRoot={false} sessions={[]} />,
    );

    await waitFor(() => expect(api.calls).toContain("/sessions/sess_gone"));
    await waitFor(() => expect(window.localStorage.getItem(RUN_KEY)).toBeNull());
    expect(screen.queryByText(/open session/)).not.toBeInTheDocument();
    expect(screen.queryByText(/status check failed/)).not.toBeInTheDocument();
  });

  it("keeps retrying on any other failure (a transient error is not 'gone')", async () => {
    stashRun("sess_flaky");
    api.responses["/sessions/sess_flaky"] = new api.FakeApiError("daemon offline", 0);

    render(
      <ProjectTasks projectId="proj_1" hasRoot={false} sessions={[]} />,
    );

    await waitFor(() => expect(screen.getByText(/status check failed/)).toBeInTheDocument());
    expect(window.localStorage.getItem(RUN_KEY)).not.toBeNull();
    expect(screen.getByText(/open session/)).toBeInTheDocument();
  });
});
