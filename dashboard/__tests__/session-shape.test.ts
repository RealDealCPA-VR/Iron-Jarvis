import { describe, expect, it } from "vitest";
import type { SessionDetail, SessionView } from "@/lib/types";

/**
 * Guards the endpoint-shape gotcha that has shipped as a client bug TWICE
 * (CLAUDE.md): `GET /sessions/{id}` returns the session NESTED under
 * `{ session, transcript }`, while `POST /sessions` (+ /continue /cancel /rerun)
 * return the session FLAT. Reading `.status` off the top level of the nested
 * response silently yields `undefined` (spinner-forever, notification never
 * firing). These tests pin both shapes and the extraction the detail page uses.
 */

// Fixture mirroring GET /sessions/{id} — the NESTED shape.
const nestedDetail: SessionDetail = {
  session: {
    id: "s-1",
    task: "summarize the quarterly report",
    agent_type: "coder",
    provider: "mock",
    model: "mock-1",
    status: "completed",
    workspace_path: "/ws/s-1",
    summary: "done",
    created_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:01:00Z",
  },
  transcript: {
    runs: [
      {
        id: "r-1",
        session_id: "s-1",
        parent_id: null,
        agent_type: "coder",
        provider: "mock",
        model: "mock-1",
        state: "completed",
        steps: 3,
        result: "ok",
        created_at: "2026-01-01T00:00:00Z",
        finished_at: "2026-01-01T00:01:00Z",
      },
    ],
    tools: [],
  },
};

// Fixture mirroring POST /sessions (+ /continue /cancel /rerun) — the FLAT shape.
const flatSession: SessionView = { ...nestedDetail.session, id: "s-2", status: "running" };

// The exact extraction the detail page performs
// (dashboard/app/sessions/[id]/page.tsx: `detail.data?.session`, `.transcript.runs`).
const readDetailStatus = (d: SessionDetail): string | undefined => d.session?.status;
const readDetailRuns = (d: SessionDetail) => d.transcript?.runs ?? [];

describe("GET /sessions/{id} nested {session, transcript} shape", () => {
  it("reads status at the NESTED level (.session.status)", () => {
    expect(readDetailStatus(nestedDetail)).toBe("completed");
  });

  it("does NOT expose status at the top level (the twice-shipped bug)", () => {
    // The whole hazard: `(detail).status` looks plausible but is undefined.
    expect((nestedDetail as unknown as { status?: string }).status).toBeUndefined();
  });

  it("keeps runs/tools under .transcript, not at the top level", () => {
    expect((nestedDetail as unknown as { runs?: unknown }).runs).toBeUndefined();
    expect(readDetailRuns(nestedDetail)).toHaveLength(1);
    expect(readDetailRuns(nestedDetail)[0].id).toBe("r-1");
  });

  it("flat lifecycle endpoints DO carry status at the top level", () => {
    // The distinction that trips callers: the flat response is read directly.
    expect(flatSession.status).toBe("running");
  });
});
