/**
 * v1.400.0 — TEAM-FIRST: "Hand work to my team first" in Settings.
 *
 * The switch is ONE row of the settings schema (Models › Model team, beside
 * the team's pay-per-use limit). The Settings page draws schema rows by
 * itself, so it needs no code of its own: this test feeds the page the two
 * rows EXACTLY as the daemon serves them (fixtures/team-first-row-v1400.json,
 * checked against `schema.public()` by tests/test_team_first_setting_v1400.py)
 * and pins that the switch shows under "Model team", off, with its words, and
 * that one press + Save sends `{team_first: true}` through the one writer
 * (PUT /settings/values). The Model team panel (components/settings/
 * ModelTeam.tsx) draws no second control for it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";
import rowFixture from "./fixtures/team-first-row-v1400.json";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  }
  return {
    FakeApiError,
    gets: {} as Record<string, unknown>,
    puts: [] as { path: string; body: unknown }[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  get: async (p: string) => {
    const key = p.split("?")[0];
    const r = H.gets[key];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${p}`, 500);
    return r;
  },
  put: async (p: string, body: unknown) => {
    H.puts.push({ path: p, body });
    return { updated: Object.keys((body as { values: object }).values), action_id: "tool_1" };
  },
  post: async () => ({}),
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: { providers: [] }, refresh: vi.fn() }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/settings",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock("@/components/settings/pages/LegacySettingsForm", () => ({
  default: () => <div data-testid="legacy-form">legacy</div>,
  LocalCapabilitiesCard: () => <div>caps</div>,
  ContextWindowsCard: () => <div>windows</div>,
}));

import { SettingsHome } from "@/components/settings/SettingsHome";

const ROOT = path.join(__dirname, "..");
const read = (rel: string) => readFileSync(path.join(ROOT, rel), "utf-8").replace(/\r\n/g, "\n");

const GROUPS = [
  { id: "models", label: "Models", description: "Which AI answers, and how." },
  { id: "system", label: "System", description: "Updates." },
];
const ROWS = (rowFixture as { rows: Record<string, unknown>[] }).rows;

beforeEach(() => {
  H.gets = {
    "/settings/schema": {
      groups: GROUPS,
      settings: [
        { key: "default_model", label: "Default model", group: "models", section: "", type: "string", aliases: [] },
        ...ROWS,
      ],
      secrets: [],
    },
    "/settings/values": { values: { default_model: "", team_paid_cap_usd: 0, team_first: false } },
    "/config/ledger": { changes: [] },
  };
  H.puts = [];
  window.history.replaceState({}, "", "/settings");
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

describe("Hand work to my team first (v1.400.0)", () => {
  it("is one switch in the Model team section, off, with its words", async () => {
    render(<SettingsHome />);
    const row = await screen.findByTestId("setting-team_first");
    expect(row.textContent).toContain("Hand work to my team first");
    expect(row.textContent).toContain(
      "a coding session is asked to hand reading, test runs, first drafts and reviews to your model team by role",
    );
    const switches = within(row).getAllByRole("switch");
    expect(switches).toHaveLength(1);
    expect(switches[0].getAttribute("aria-checked")).toBe("false");
    // Under the "Model team" heading, beside the team's pay-per-use limit.
    const section = row.closest("section") as HTMLElement;
    expect(within(section).getByRole("heading", { level: 3 }).textContent).toBe("Model team");
    expect(within(section).getByTestId("setting-team_paid_cap_usd")).toBeTruthy();
    // Nowhere else on the page.
    expect(screen.getAllByTestId("setting-team_first")).toHaveLength(1);
  });

  it("one press and Save turns it on through the one writer", async () => {
    render(<SettingsHome />);
    const row = await screen.findByTestId("setting-team_first");
    fireEvent.click(within(row).getByRole("switch"));
    const bar = screen.getByTestId("settings-save-bar");
    fireEvent.click(within(bar).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(H.puts).toHaveLength(1));
    expect(H.puts[0].path).toBe("/settings/values");
    expect((H.puts[0].body as { values: Record<string, unknown> }).values).toEqual({ team_first: true });
  });

  it("the Model team panel draws no second control for it", () => {
    const panel = read("components/settings/ModelTeam.tsx");
    expect(panel).not.toMatch(/team_first|team first/i);
  });
});
