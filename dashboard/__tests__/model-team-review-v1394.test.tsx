/**
 * v1.394.0 review (the long-jobs plan's Phase 5): what the doer's dashboard
 * pins did not catch, each one RED on the doer's code first.
 *
 *  - Settings › Model team saves a member's roles AT ONCE, and its row drew
 *    the roles from the last answer of GET /team. A second tick pressed after
 *    the first PATCH answered but before the reload landed was computed from
 *    the OLD roles and sent them — the first tick was lost (the v1.393.0
 *    lost-update lesson). The row now keeps what it last sent.
 *  - The same for the account picker: it snapped back to the old account
 *    until the reload landed.
 *  - The empty account choice said "Iron-Proxy's pick"; in a coding session a
 *    member with no account of its own runs on the session's account (or one
 *    of the session's group), so the words say what is true everywhere.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  patches: [] as Array<{ path: string; body: Record<string, unknown> }>,
  release: [] as Array<() => void>,
  refuse: false,
}));

vi.mock("@/lib/useApi", () => ({
  // reload() is a no-op: the reload "has not landed yet" for the whole test.
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
    patch: (path: string, body: Record<string, unknown>) => {
      hooks.patches.push({ path, body });
      return new Promise<Record<string, unknown>>((resolve, reject) =>
        hooks.release.push(() => (hooks.refuse ? reject(new ApiError("That account is not listed.", 422)) : resolve({}))),
      );
    },
  };
});

import { ModelTeam } from "@/components/settings/ModelTeam";

const MEMBER = {
  name: "codex-coder",
  roster_name: "custom:codex-coder",
  description: "writes code",
  provider: "codex-cli",
  model: "subscription",
  place: "cloud",
  cost: "plan",
  roles: ["code"],
  account: "",
  account_name: "",
  vendor: "codex",
  usage: null,
  available: true,
  paused: false,
};

beforeEach(() => {
  hooks.api = {
    "/team": { roles: [], members: [MEMBER] },
    "/build/accounts": {
      claude: [],
      codex: [
        { id: "x-plus", name: "Codex Plus", status: "ready" },
        { id: "x-team", name: "Codex Team", status: "ready" },
      ],
    },
    "/models": { models: [] },
  };
  hooks.patches = [];
  hooks.release = [];
  hooks.refuse = false;
});
afterEach(() => cleanup());

const box = (id: string) => screen.getByTestId(`team-role-codex-coder-${id}`) as HTMLInputElement;

async function answerLastPatch() {
  const release = hooks.release.shift();
  expect(release).toBeTruthy();
  release!();
}

describe("Settings › Model team keeps what it last sent", () => {
  it("two quick role ticks: the second keeps the first", async () => {
    render(<ModelTeam />);
    fireEvent.click(box("read"));
    await waitFor(() => expect(hooks.patches).toHaveLength(1));
    expect(hooks.patches[0].body).toEqual({ roles: ["read", "code"] });
    await answerLastPatch();
    // The PATCH answered; GET /team has NOT been read again yet.
    await waitFor(() => expect(box("review").disabled).toBe(false));
    fireEvent.click(box("review"));
    await waitFor(() => expect(hooks.patches).toHaveLength(2));
    expect(hooks.patches[1].body).toEqual({ roles: ["read", "code", "review"] });
    // While the second save is out the row shows it, and after it too.
    expect([box("read").checked, box("code").checked, box("review").checked]).toEqual([true, true, true]);
    await answerLastPatch();
    await waitFor(() => expect(box("review").disabled).toBe(false));
    expect([box("read").checked, box("code").checked, box("review").checked]).toEqual([true, true, true]);
  });

  it("the account picker shows the account it saved, not the old one", async () => {
    render(<ModelTeam />);
    const select = () => screen.getByTestId("team-account-codex-coder") as HTMLSelectElement;
    fireEvent.change(select(), { target: { value: "x-team" } });
    await waitFor(() => expect(hooks.patches).toHaveLength(1));
    expect(hooks.patches[0].body).toEqual({ account: "x-team" });
    await answerLastPatch();
    await waitFor(() => expect(select().disabled).toBe(false));
    expect(select().value).toBe("x-team");
  });

  it("a refused save puts the row back and says why", async () => {
    hooks.refuse = true;
    render(<ModelTeam />);
    fireEvent.click(box("read"));
    await waitFor(() => expect(hooks.patches).toHaveLength(1));
    await answerLastPatch();
    await screen.findByText("That account is not listed.");
    expect([box("read").checked, box("code").checked]).toEqual([false, true]);
  });

  it("the empty account choice says what is true in a coding session too", () => {
    render(<ModelTeam />);
    const first = (screen.getByTestId("team-account-codex-coder") as HTMLSelectElement).options[0];
    expect(first.value).toBe("");
    expect(first.textContent).toBe("No account of its own");
    expect(first.textContent).not.toMatch(/Iron-Proxy's pick/);
  });
});
