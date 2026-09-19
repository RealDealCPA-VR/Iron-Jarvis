/**
 * v1.280.0 — the Agents job card opens on the agent the last job went to.
 *
 * It used to open on the Team every visit; a person who hands most work to one
 * agent picked it every time. The pick is remembered per browser
 * (`ij_agents_last_target`) and restored only while the roster still lists it
 * — otherwise the card falls back to the Team, visibly, as it always did for
 * an unmatched target.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/lib/useApi", () => ({
  useApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  get: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
  post: () => Promise.resolve({ id: "s1", status: "active" }),
}));

import {
  JobPostCard,
  LAST_TARGET_KEY,
  TEAM_TARGET,
  readLastTarget,
  rememberLastTarget,
} from "@/components/agents/JobPostCard";
import type { RosterEntry } from "@/components/agents/RosterStrip";

const ROSTER: RosterEntry[] = [
  { name: "builder", kind: "builtin", description: "Builds things", delegable: true, healthy: true, stats: null },
  { name: "researcher", kind: "builtin", description: "Reads things", delegable: true, healthy: true, stats: null },
];

function targetSelect(): HTMLSelectElement {
  return screen.getByRole("option", { name: /builder/i }).closest("select") as HTMLSelectElement;
}

beforeEach(() => {
  window.localStorage.clear();
});
afterEach(() => {
  cleanup();
});

describe("the job card remembers its target (v1.280.0)", () => {
  it("opens on the Team when nothing was ever picked", () => {
    render(<JobPostCard roster={ROSTER} />);
    expect(targetSelect().value).toBe(TEAM_TARGET);
  });

  it("opens on the last pick while the roster still lists it", () => {
    window.localStorage.setItem(LAST_TARGET_KEY, "researcher");
    render(<JobPostCard roster={ROSTER} />);
    expect(targetSelect().value).toBe("researcher");
  });

  it("falls back to the Team, visibly, when the remembered agent is gone", () => {
    window.localStorage.setItem(LAST_TARGET_KEY, "custom:vanished");
    render(<JobPostCard roster={ROSTER} />);
    expect(targetSelect().value).toBe(TEAM_TARGET);
  });

  it("a pick is remembered; picking the Team forgets it", () => {
    render(<JobPostCard roster={ROSTER} />);
    fireEvent.change(targetSelect(), { target: { value: "builder" } });
    expect(window.localStorage.getItem(LAST_TARGET_KEY)).toBe("builder");
    fireEvent.change(targetSelect(), { target: { value: TEAM_TARGET } });
    expect(window.localStorage.getItem(LAST_TARGET_KEY)).toBeNull();
  });

  it("the helpers never throw on a refusing store", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    try {
      expect(readLastTarget()).toBe(TEAM_TARGET);
      expect(() => rememberLastTarget("builder")).not.toThrow();
    } finally {
      spy.mockRestore();
    }
  });
});
