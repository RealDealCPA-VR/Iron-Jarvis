/**
 * v1.423.0 (Wave 3, Team notes Phase 1b): a member card's REVIEWERS'
 * FINDINGS line, the dashboard half.
 *
 * Pinned here:
 *  - the ONE quiet line `team-findings-<name>` says "Reviewers' findings
 *    (30 days): 6 confirmed, 1 did not apply · first-pass approval 3 of 7",
 *    leaving out a part that is zero;
 *  - NOTHING at all when every number is zero, and nothing for an older
 *    daemon that sends no `findings` (decoded defensively: junk reads as
 *    zero, never as a number it is not).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const hooks = vi.hoisted(() => ({ api: {} as Record<string, unknown> }));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
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

import { ModelTeam } from "@/components/settings/ModelTeam";
import { decodeFindings, decodeTeam, findingsWords, type TeamFindings } from "@/lib/modelTeam";

function member(name: string, findings?: unknown): Record<string, unknown> {
  return {
    name,
    roster_name: `custom:${name}`,
    description: "",
    provider: "ollama",
    model: "qwen3:8b",
    place: "this_pc",
    cost: "free",
    roles: ["code"],
    account: "",
    account_name: "",
    vendor: "",
    usage: null,
    available: true,
    paused: false,
    ...(findings === undefined ? {} : { findings }),
  };
}

const ZEROS = { confirmed: 0, did_not_apply: 0, said_fixed: 0, open: 0, first_pass: { approved: 0, of: 0 } };
const SOME = { confirmed: 6, did_not_apply: 1, said_fixed: 0, open: 0, first_pass: { approved: 3, of: 7 } };

function f(over: Partial<TeamFindings> = {}): TeamFindings {
  return { confirmed: 0, didNotApply: 0, saidFixed: 0, open: 0, firstPass: { approved: 0, of: 0 }, ...over };
}

beforeEach(() => {
  hooks.api = {
    "/team": { members: [member("qwen", SOME), member("glm", ZEROS), member("old")] },
    "/build/accounts": { claude: [], codex: [] },
    "/models": { models: [] },
  };
});
afterEach(cleanup);

describe("the findings words", () => {
  it("says the owner's example exactly and leaves out every zero part", () => {
    expect(findingsWords(f({ confirmed: 6, didNotApply: 1, firstPass: { approved: 3, of: 7 } }))).toBe(
      "Reviewers' findings (30 days): 6 confirmed, 1 did not apply · first-pass approval 3 of 7",
    );
    expect(findingsWords(f({ confirmed: 2, saidFixed: 1, didNotApply: 3, open: 4 }))).toBe(
      "Reviewers' findings (30 days): 2 confirmed, 1 said fixed but not confirmed, 3 did not apply, 4 not answered",
    );
    expect(findingsWords(f({ open: 1 }))).toBe("Reviewers' findings (30 days): 1 not answered");
    expect(findingsWords(f({ firstPass: { approved: 0, of: 2 } }))).toBe(
      "Reviewers' findings (30 days): first-pass approval 0 of 2",
    );
  });

  it("is empty when everything is zero, or when the daemon sent none", () => {
    expect(findingsWords(f())).toBe("");
    expect(findingsWords(null)).toBe("");
  });

  it("decodes defensively", () => {
    expect(decodeFindings(undefined)).toBeNull();
    expect(decodeFindings("6 confirmed")).toBeNull();
    expect(decodeFindings({ confirmed: "6", did_not_apply: -2, open: 1.6, first_pass: { approved: 9, of: 2 } })).toEqual(
      f({ open: 2, firstPass: { approved: 2, of: 2 } }),
    );
    expect(decodeTeam({ members: [member("old")] }).members[0].findings).toBeNull();
    expect(decodeTeam({ members: [member("qwen", SOME)] }).members[0].findings).toEqual(
      f({ confirmed: 6, didNotApply: 1, firstPass: { approved: 3, of: 7 } }),
    );
  });
});

describe("the member card", () => {
  it("shows ONE quiet line for a member with findings, nothing for zeros or an older daemon", () => {
    render(<ModelTeam />);
    expect(screen.getByTestId("team-findings-qwen").textContent).toBe(
      "Reviewers' findings (30 days): 6 confirmed, 1 did not apply · first-pass approval 3 of 7",
    );
    expect(screen.getAllByText(/Reviewers' findings/)).toHaveLength(1);
    expect(screen.getByTestId("team-member-glm")).toBeTruthy();
    expect(screen.queryByTestId("team-findings-glm")).toBeNull();
    expect(screen.getByTestId("team-member-old")).toBeTruthy();
    expect(screen.queryByTestId("team-findings-old")).toBeNull();
  });
});
