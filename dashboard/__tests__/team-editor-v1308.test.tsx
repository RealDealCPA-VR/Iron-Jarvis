// v1.308.0 — the team editor picks who works a PROJECT's objectives. A
// coordinator (supervisor, planner) cannot be handed a mission's part, so it is
// never offered; the words say "objectives", never "round table" or "seats".

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const teamEditorHarness1308 = vi.hoisted(() => ({ api: {} as Record<string, unknown> }));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (teamEditorHarness1308.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  API_BASE: "",
  ijToken: () => "",
  get: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  post: () => Promise.resolve({}),
}));
vi.mock("@/components/agents/AgentFace", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  default: (p: { name: string }) => <span data-testid="face" data-name={p.name} />,
}));

import { TeamEditor } from "@/components/agents/world/TeamEditor";

afterEach(cleanup);

describe("TeamEditor (v1.308.0)", () => {
  it("never offers a coordinator, and talks about objectives, not a table", () => {
    teamEditorHarness1308.api["/projects/p1/team"] = { team: [], suggestions: [] };
    render(
      <TeamEditor
        projectId="p1"
        projectName="Acme"
        mode="build"
        onSaved={() => {}}
        roster={[
          { name: "builder", kind: "builtin", description: "", delegable: true, healthy: true, stats: null, line: "" },
          { name: "planner", kind: "builtin", description: "", delegable: false, healthy: true, stats: null, line: "" },
          { name: "supervisor", kind: "builtin", description: "", delegable: false, healthy: true, stats: null, line: "" },
        ] as never}
      />,
    );
    expect(screen.getByTestId("team-add-builder")).toBeTruthy();
    expect(screen.queryByTestId("team-add-planner")).toBeNull();
    expect(screen.queryByTestId("team-add-supervisor")).toBeNull();
    const text = document.body.textContent ?? "";
    expect(text).toContain("Pick who works on Acme's objectives");
    expect(text).not.toMatch(/round table|seat/i);
  });
});
