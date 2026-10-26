/**
 * v1.395.0 (the long-jobs plan's Phase 6, decisions 7 and 8): WHY each team
 * member took its piece — on the helper row, on its Timeline row and card —
 * and each member's RECORD per role in Settings › Model team.
 *
 * Fed with a REAL recorded reply (fixtures/team-router-timeline-v1395.json,
 * written by tests/test_team_live_v1395.py through the real Build route, the
 * real router, the team router and the fake Iron-Proxy): a coding session on
 * Codex whose planner asked for `role:code` twice while the local box was
 * busy — the first piece went to Claude Code's lowest-usage account, which
 * that very call pushed to 83%, so the second went to its OTHER account.
 */
import React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({ api: {} as Record<string, unknown> }));

vi.mock("@/lib/useApi", () => ({
  useApi: (p: string | null) => ({ data: p ? (hooks.api[p] ?? null) : null, error: null, loading: false, reload: () => {} }),
  usePolledApi: (p: string | null) => ({ data: p ? (hooks.api[p] ?? null) : null, error: null, loading: false, reload: () => {} }),
}));

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
    get: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    post: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
  };
});

import { HarnessTimeline } from "@/components/harness/HarnessTimeline";
import { LiveHelperRows, SavedHelperRows } from "@/components/harness/HarnessHelpers";
import { ModelTeam } from "@/components/settings/ModelTeam";
import { applyHelperFrame, decodeSavedHelpers, settledFromLive, type HelperView } from "@/lib/buildHelpers";
import { agentPhrase } from "@/lib/agentRun";
import { decodeMessage, type BuildMessage, type BuildSessionView } from "@/lib/buildSessions";
import { decodeTeam, recordWords, ROLE_NOUNS } from "@/lib/modelTeam";

const FIXTURE = JSON.parse(
  readFileSync(path.join(__dirname, "fixtures", "team-router-timeline-v1395.json"), "utf8").replace(/\r\n/g, "\n"),
) as { question: Record<string, unknown>; line: Record<string, unknown> & { helpers: Record<string, unknown>[] } };

const LINE = decodeMessage(FIXTURE.line) as BuildMessage;
const QUESTION = decodeMessage(FIXTURE.question) as BuildMessage;
const FIRST = "Claude Code (Work Max): the local box is busy";
const SECOND = "Claude Code (Spare): the local box is busy; skipped Claude Code (Work Max): 83% of its 5-hour window";

function viewOf(messages: BuildMessage[]): BuildSessionView {
  return { id: "s1", messages, setup: { provider: "codex-cli", model: "subscription" } } as unknown as BuildSessionView;
}

beforeEach(() => {
  hooks.api = {};
});
afterEach(() => cleanup());

describe("the recorded reply says why each member took its piece", () => {
  it("the fixture is the daemon's own: two code pieces, two reasons", () => {
    expect(FIXTURE.line.helpers.map((h) => [h.role, h.reason])).toEqual([
      ["code", FIRST],
      ["code", SECOND],
    ]);
  });

  it("each saved helper row says why, under the model it ran on", () => {
    render(<SavedHelperRows raw={FIXTURE.line.helpers} />);
    const rows = screen.getAllByTestId("helper-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByTestId("helper-reason").textContent).toBe(`Why: ${FIRST}`);
    expect(within(rows[1]).getByTestId("helper-reason").textContent).toBe(`Why: ${SECOND}`);
  });

  it("each helper's Timeline row carries its reason, and its card says Why", () => {
    render(
      <div style={{ height: 600 }}>
        <HarnessTimeline view={viewOf([QUESTION, LINE])} live={null} models={[]} />
      </div>,
    );
    const helperRows = screen.getAllByTestId("timeline-row").filter((r) => r.getAttribute("data-kind") === "helper");
    expect(helperRows).toHaveLength(2);
    expect(helperRows.map((r) => within(r).getByTestId("timeline-row-reason").textContent)).toEqual([
      ` · ${FIRST}`,
      ` · ${SECOND}`,
    ]);
    // The row's tooltip and its bar's words carry it too.
    expect(helperRows[1].querySelector("span[title]")?.getAttribute("title")).toContain(SECOND);
    const bar = within(helperRows[1]).getByTestId("timeline-bar");
    expect(bar.getAttribute("aria-label")).toContain(`(${SECOND})`);
    fireEvent.click(bar);
    const card = screen.getByTestId("timeline-card");
    const facts = Array.from(card.querySelectorAll("dt")).map((dt) => [dt.textContent, dt.nextElementSibling?.textContent]);
    expect(facts).toContainEqual(["Why", SECOND]);
  });

  it("a reply's other rows carry no reason (the reply's own bar, its steps)", () => {
    render(
      <div style={{ height: 600 }}>
        <HarnessTimeline view={viewOf([QUESTION, LINE])} live={null} models={[]} />
      </div>,
    );
    const others = screen.getAllByTestId("timeline-row").filter((r) => r.getAttribute("data-kind") !== "helper");
    expect(others.length).toBeGreaterThan(0);
    for (const r of others) expect(within(r).queryByTestId("timeline-row-reason")).toBeNull();
  });
});

describe("the live row", () => {
  it("says the role and why as soon as the router picked, and keeps it when it settles", () => {
    let helpers: HelperView[] = [];
    helpers = applyHelperFrame(helpers, { id: "c1", agent: "role:code", task: "Write util.py.", state: "starting" });
    helpers = applyHelperFrame(helpers, { id: "c1", state: "starting", role: "code", reason: "local: good record for coding" });
    expect([helpers[0].role, helpers[0].reason]).toEqual(["code", "local: good record for coding"]);
    const { rerender } = render(<LiveHelperRows helpers={helpers} />);
    expect(screen.getByTestId("helper-reason").textContent).toBe("Why: local: good record for coding");
    helpers = applyHelperFrame(helpers, { id: "c1", session: "sess_1", agent: "custom:glm", state: "running" });
    rerender(<LiveHelperRows helpers={helpers} />);
    expect(screen.getByTestId("helper-reason").textContent).toBe("Why: local: good record for coding");
    helpers = applyHelperFrame(helpers, { id: "c1", state: "done", result: "Wrote it.", ended: 2 });
    const settled = settledFromLive(helpers[0]);
    expect([settled?.role, settled?.reason]).toEqual(["code", "local: good record for coding"]);
  });

  it("a role nobody could take says it WAITED, on the row that never started", () => {
    let helpers: HelperView[] = [];
    helpers = applyHelperFrame(helpers, { id: "c2", agent: "role:test", task: "Run the tests.", state: "starting" });
    const why = "waited: every cloud account that can take testing is near its limit; the earliest reset is 3:40 PM";
    helpers = applyHelperFrame(helpers, { id: "c2", role: "test", reason: why });
    helpers = applyHelperFrame(helpers, {
      id: "c2", state: "failed", ended: 3,
      result: "No team member can take testing now: skipped Codex (Codex Plus): 96% of its 5-hour window.",
    });
    render(<LiveHelperRows helpers={helpers} />);
    const row = screen.getByTestId("helper-row");
    expect(row.getAttribute("data-state")).toBe("failed");
    expect(within(row).getByTestId("helper-reason").textContent).toBe(`Why: ${why}`);
    // Named by the ROLE it asked for — no member was picked.
    expect(row.textContent).toContain("A team member for testing could not start");
    expect(agentPhrase("role:code")).toBe("a team member for coding");
    expect(agentPhrase("role:design")).toBe("a team member");
    expect(agentPhrase("custom:glm")).toBe("your glm agent");
  });

  it("junk on the wire reads as no reason", () => {
    const saved = decodeSavedHelpers([{ id: "x", state: "done", reason: 7, role: { a: 1 } }]);
    expect([saved[0].reason, saved[0].role]).toEqual([undefined, undefined]);
    render(<SavedHelperRows raw={[{ id: "x", session: "s", state: "done", reason: "" }]} />);
    expect(screen.queryByTestId("helper-reason")).toBeNull();
  });
});

describe("Settings › Model team shows each member's record per role", () => {
  const member = (name: string, roles: string[], records: unknown) => ({
    name, roster_name: `custom:${name}`, description: "", provider: "ollama", model: "qwen3:8b",
    place: "this_pc", cost: "free", roles, account: "", account_name: "", vendor: "", usage: null,
    available: true, paused: false, records,
  });

  it("in the daemon's own words, one role after another", () => {
    hooks.api = {
      "/team": {
        roles: [],
        members: [
          member("glm", ["read", "code"], {
            read: { runs: 5, passed: 4, streak: 0, status: "good", words: "good (4 of 5 passed)" },
            code: { runs: 3, passed: 1, streak: 2, status: "not_good", words: "failed the last 2 in a row (1 of 3 passed)" },
          }),
          member("fresh", ["review"], {
            review: { runs: 0, passed: 0, streak: 0, status: "unknown", words: "no record yet" },
          }),
          // An older daemon sends no records: nothing is said.
          member("old", ["test"], undefined),
        ],
      },
      "/build/accounts": { claude: [], codex: [] },
      "/models": { models: [] },
    };
    render(<ModelTeam />);
    expect(screen.getByTestId("team-record-glm").textContent).toBe(
      "Record: reading: good (4 of 5 passed) · coding: failed the last 2 in a row (1 of 3 passed)",
    );
    expect(screen.getByTestId("team-record-fresh").textContent).toBe("Record: reviewing: no record yet");
    expect(screen.queryByTestId("team-record-old")).toBeNull();
  });

  it("decodes the record and says it in role nouns (the daemon's ROLE_NOUNS twin)", () => {
    expect(ROLE_NOUNS).toEqual({ read: "reading", code: "coding", review: "reviewing", test: "testing" });
    const m = decodeTeam({ members: [member("a", ["code"], { code: { runs: 4, passed: 3, streak: 0, status: "good", words: "good (3 of 4 passed)" }, read: { words: "not held" } })] }).members[0];
    expect(m.records).toEqual({ code: { runs: 4, passed: 3, streak: 0, status: "good", words: "good (3 of 4 passed)" } });
    expect(recordWords(m)).toBe("coding: good (3 of 4 passed)");
  });
});
