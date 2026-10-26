/**
 * v1.395.0 REVIEW of the team router (the long-jobs plan's Phase 6): the
 * MISSION screen says why a teammate took its piece, as a coding session's
 * helper row and Timeline already do ("Why: local: good record for
 * reading") — on the teammate's card, folded with its other details, and only
 * when the team router picked it (a teammate the planner named has none).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    API_BASE: "",
    ijToken: () => "",
    sseUrl: (p: string) => p,
    get: () => Promise.resolve({}),
    post: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
  };
});

import { AgentCards } from "@/components/agents/mission/AgentCards";
import { decodeMission, type MissionMember } from "@/lib/mission";

afterEach(() => cleanup());

function member(over: Partial<MissionMember> = {}): MissionMember {
  return {
    session_id: "m1",
    agent: "custom:glm",
    name: "Your glm agent",
    kind: "custom",
    task: "Read app.py.",
    status: "done",
    progress: { pct: 100, label: "", basis: "done" },
    activity: "",
    waiting_on: null,
    steps: 2,
    result: "",
    files: [],
    started_at: null,
    finished_at: null,
    provider: "ollama",
    model: "qwen3:8b",
    ...over,
  };
}

describe("the mission screen's teammate card says why", () => {
  it("a routed teammate's card says the router's sentence when opened", () => {
    render(<AgentCards members={[member({ reason: "local: good record for reading" })]} />);
    expect(screen.queryByTestId("mission-card-why-custom:glm")).toBeNull(); // folded
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByTestId("mission-card-why-custom:glm").textContent).toBe(
      "Why: local: good record for reading",
    );
  });

  it("a teammate the planner named says nothing of the kind", () => {
    render(<AgentCards members={[member({ reason: "" })]} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.queryByTestId("mission-card-why-custom:glm")).toBeNull();
  });

  it("the view's decoder keeps the daemon's reason, cut, and reads junk as none", () => {
    const raw = (reason: unknown) => ({
      found: true,
      session: { id: "s1", task: "t", status: "completed" },
      coordinator: { name: "Jarvis", status: "done", steps: 1 },
      members: [{ agent: "custom:glm", name: "glm", status: "done", reason }],
      activity: [],
      deliverable: { text: "", documents: [], worklist: null },
    });
    expect(decodeMission(raw("Codex: the local box is busy"))?.members[0].reason).toBe(
      "Codex: the local box is busy",
    );
    expect(decodeMission(raw("x".repeat(400)))?.members[0].reason).toHaveLength(300);
    expect(decodeMission(raw(7))?.members[0].reason).toBe("");
  });
});
