/**
 * v1.394.0 (the long-jobs plan's Phase 5): YOUR MODEL TEAM, the dashboard half.
 *
 * Pinned here (the wire and the words, not the look):
 *  - Settings › Model team lists each member with its model (the model menu's
 *    words), where it runs and its cost type, and its usage from the cached
 *    snapshot — "Not known yet" when there is none, never "0%";
 *  - a member's roles and account are changed right there (PATCH
 *    /agents/<name>, only the field changed);
 *  - **Make a starter team** shows a preview first and POSTs NOTHING until
 *    Create; local models ticked, Claude Code and Codex unticked, pay per use
 *    only with the switch (GET /team/starter?paid=1); Create sends exactly
 *    the ticked rows; with no model connected it says so and offers nothing;
 *  - the agent's own card (Agents page) edits its roles — sent only when
 *    touched — and says "Model team · …" when it has any;
 *  - a coding session's helper row names the member AND the model it ran on.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  gets: [] as string[],
  responses: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: Record<string, unknown> }>,
  patches: [] as Array<{ path: string; body: Record<string, unknown> }>,
  postAnswer: {} as unknown,
}));

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
    get: (path: string) => {
      hooks.gets.push(path);
      if (path === "/tools") return Promise.resolve({ tools: [] });
      return Promise.resolve(hooks.responses[path] ?? {});
    },
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    post: (path: string, body: Record<string, unknown>) => {
      hooks.posts.push({ path, body });
      return Promise.resolve(hooks.postAnswer);
    },
    patch: (path: string, body: Record<string, unknown>) => {
      hooks.patches.push({ path, body });
      return Promise.resolve({});
    },
  };
});

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "whileHover"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  return {
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: unknown }) => createElement(Fragment, null, children as never),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => tagFor(String(tag)),
    }),
  };
});

import { ModelTeam } from "@/components/settings/ModelTeam";
import { SavedHelperRows, LiveHelperRows } from "@/components/harness/HarnessHelpers";
import { applyHelperFrame } from "@/lib/buildHelpers";
import { decodeTeam, usageWords } from "@/lib/modelTeam";
import { cleanRoles, roleWords, TEAM_ROLE_IDS } from "@/lib/teamRoles";
import type { DynamicAgentFull } from "@/components/agents/SetupCard";
import { SetupCardHarness } from "./helpers/setupCardHarness";

const MODELS = [
  { provider: "ollama", model: "qwen3:8b", label: "Qwen 3 8B", available: true },
  { provider: "codex-cli", model: "subscription", available: true },
];

const TEAM = {
  roles: [
    { id: "read", label: "Reading and summarizing" },
    { id: "code", label: "Writing code" },
    { id: "review", label: "Reviewing a change" },
    { id: "test", label: "Running and fixing tests" },
  ],
  members: [
    {
      name: "local-reader",
      roster_name: "custom:local-reader",
      description: "reads and summarizes",
      provider: "ollama",
      model: "qwen3:8b",
      place: "this_pc",
      cost: "free",
      roles: ["read"],
      account: "",
      account_name: "",
      vendor: "",
      usage: null,
      available: true,
      paused: false,
    },
    {
      name: "codex-coder",
      roster_name: "custom:codex-coder",
      description: "writes code",
      provider: "codex-cli",
      model: "subscription",
      place: "cloud",
      cost: "plan",
      roles: ["code", "test"],
      account: "",
      account_name: "",
      vendor: "codex",
      usage: null,
      available: true,
      paused: false,
    },
    {
      name: "claude-reviewer",
      roster_name: "custom:claude-reviewer",
      description: "",
      provider: "claude-cli",
      model: "subscription",
      place: "cloud",
      cost: "plan",
      roles: ["review"],
      account: "c-work",
      account_name: "Work Max",
      vendor: "claude",
      usage: { account: "c-work", account_name: "Work Max", status: "usage", percent: 42, window_mins: 300 },
      available: true,
      paused: false,
    },
  ],
};

const ACCOUNTS = {
  claude: [{ id: "c-work", name: "Work Max", status: "usage" }],
  codex: [
    { id: "x-plus", name: "Codex Plus", status: "ready" },
    { id: "x-team", name: "Codex Team", status: "limit" },
  ],
};

const STARTER = {
  rows: [
    { key: "ollama|qwen3:8b", provider: "ollama", model: "qwen3:8b", group: "local", roles: ["read", "code"],
      place: "this_pc", cost: "free", ticked: true, exists: null, description: "", name: "qwen3-8b" },
    { key: "claude-cli|subscription", provider: "claude-cli", model: "subscription", group: "subscription",
      roles: ["code", "review"], place: "cloud", cost: "plan", ticked: false, exists: null, description: "",
      name: "claude-code" },
    { key: "codex-cli|subscription", provider: "codex-cli", model: "subscription", group: "subscription",
      roles: ["code", "test"], place: "cloud", cost: "plan", ticked: false, exists: null, description: "",
      name: "codex" },
  ],
  paid: false,
  paid_available: 1,
  note: "",
};

beforeEach(() => {
  hooks.api = { "/team": TEAM, "/build/accounts": ACCOUNTS, "/models": { models: MODELS } };
  hooks.gets = [];
  hooks.responses = {};
  hooks.posts = [];
  hooks.patches = [];
  hooks.postAnswer = { created: [], skipped: [] };
});
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("the role vocabulary", () => {
  it("cleans roles to the known ones, in order, and says them in words", () => {
    expect(TEAM_ROLE_IDS).toEqual(["read", "code", "review", "test"]);
    expect(cleanRoles(["TEST", "read", "cook", 3, "read"])).toEqual(["read", "test"]);
    expect(roleWords(["code", "read"])).toBe("Reading and summarizing, Writing code");
  });
});

describe("usage words never say 0% for an unknown", () => {
  const base = { provider: "codex-cli", account: "", usage: null } as const;
  it("says each case in plain words", () => {
    expect(usageWords({ ...base, cost: "plan" })).toBe("Not known yet");
    expect(usageWords({ ...base, cost: "free" })).toBe("No usage limit");
    expect(usageWords({ ...base, cost: "pay" })).toBe("Billed per use");
    expect(usageWords({ ...base, provider: "", cost: "" })).toBe("Uses the model of whoever hands it work");
    const m = decodeTeam(TEAM).members[2];
    expect(usageWords(m)).toBe("42% of the 5-hour window used (Work Max)");
    expect(usageWords({ ...m, account: "" })).toBe("42% of the 5-hour window used (Work Max, Iron-Proxy's pick)");
    expect(usageWords({ ...m, usage: { ...m.usage!, status: "limit", percent: null } })).toBe(
      "At its usage limit (Work Max)",
    );
  });
});

describe("Settings › Model team", () => {
  it("lists each member with its model, where it runs, its cost type and its usage", () => {
    render(<ModelTeam />);
    // The model menu's words for a model in a list (modelPickWords + the catalog).
    expect(screen.getByTestId("team-model-local-reader").textContent).toBe("Ollama · qwen3:8b");
    expect(screen.getByTestId("team-where-local-reader").textContent).toBe("This PC · Free");
    expect(screen.getByTestId("team-usage-local-reader").textContent).toBe("Usage: No usage limit");
    expect(screen.getByTestId("team-where-codex-coder").textContent).toBe("Cloud · Your plan");
    expect(screen.getByTestId("team-usage-codex-coder").textContent).toBe("Usage: Not known yet");
    expect(screen.getByTestId("team-usage-claude-reviewer").textContent).toBe(
      "Usage: 42% of the 5-hour window used (Work Max)",
    );
    expect(screen.getByTestId("model-team").textContent).not.toMatch(/\b0%/);
  });

  it("changes a member's roles and account on its row, one field at a time", async () => {
    render(<ModelTeam />);
    fireEvent.click(screen.getByTestId("team-role-codex-coder-review"));
    await waitFor(() => expect(hooks.patches).toHaveLength(1));
    expect(hooks.patches[0]).toEqual({ path: "/agents/codex-coder", body: { roles: ["code", "review", "test"] } });
    const select = screen.getByTestId("team-account-codex-coder") as HTMLSelectElement;
    // Only that vendor's accounts, and Iron-Proxy's pick first.
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(["Iron-Proxy's pick", "Codex Plus", "Codex Team"]);
    fireEvent.change(select, { target: { value: "x-plus" } });
    await waitFor(() => expect(hooks.patches).toHaveLength(2));
    expect(hooks.patches[1]).toEqual({ path: "/agents/codex-coder", body: { account: "x-plus" } });
    // A free local member has no account to pick.
    expect(screen.queryByTestId("team-account-local-reader")).toBeNull();
  });

  it("previews a starter team and makes nothing until Create", async () => {
    hooks.api["/team"] = { roles: TEAM.roles, members: [] };
    hooks.responses["/team/starter"] = STARTER;
    render(<ModelTeam />);
    expect(screen.getByTestId("team-empty")).toBeTruthy();
    fireEvent.click(screen.getByTestId("team-starter-open"));
    await screen.findByTestId("team-starter", {}, { timeout: 10_000 });
    expect(hooks.gets).toContain("/team/starter");
    expect(hooks.posts).toEqual([]); // looking makes nothing
    const local = screen.getByTestId("team-starter-tick-ollama|qwen3:8b") as HTMLInputElement;
    const claude = screen.getByTestId("team-starter-tick-claude-cli|subscription") as HTMLInputElement;
    const codex = screen.getByTestId("team-starter-tick-codex-cli|subscription") as HTMLInputElement;
    expect([local.checked, claude.checked, codex.checked]).toEqual([true, false, false]);
    expect(screen.getByTestId("team-starter-create").textContent).toBe("Create (1)");
    // Add Codex, rename it, and give it the review role too.
    fireEvent.click(codex);
    fireEvent.change(screen.getByTestId("team-starter-name-codex-cli|subscription"), { target: { value: "my-codex" } });
    fireEvent.click(screen.getByTestId("team-role-starter-codex-cli|subscription-review"));
    hooks.postAnswer = { created: ["qwen3-8b", "my-codex"], skipped: [] };
    fireEvent.click(screen.getByTestId("team-starter-create"));
    await waitFor(() => expect(hooks.posts).toHaveLength(1));
    expect(hooks.posts[0]).toEqual({
      path: "/team/starter",
      body: {
        paid: false,
        rows: [
          { key: "ollama|qwen3:8b", name: "qwen3-8b", roles: ["read", "code"] },
          { key: "codex-cli|subscription", name: "my-codex", roles: ["code", "review", "test"] },
        ],
      },
    });
    await waitFor(() => expect(screen.queryByTestId("team-starter")).toBeNull());
    expect(screen.getByText(/Added 2 to the team: qwen3-8b, my-codex\./)).toBeTruthy();
  });

  it("asks for pay-per-use models only when the switch is turned on", async () => {
    hooks.responses["/team/starter"] = STARTER;
    hooks.responses["/team/starter?paid=1"] = {
      ...STARTER,
      paid: true,
      rows: [
        ...STARTER.rows,
        { key: "openai|gpt-4o", provider: "openai", model: "gpt-4o", group: "paid", roles: ["code", "review"],
          place: "cloud", cost: "pay", ticked: false, exists: null, description: "", name: "openai-gpt-4o" },
      ],
    };
    render(<ModelTeam />);
    fireEvent.click(screen.getByTestId("team-starter-open"));
    await screen.findByTestId("team-starter", {}, { timeout: 10_000 });
    expect(screen.queryByTestId("team-starter-row-openai|gpt-4o")).toBeNull();
    fireEvent.click(screen.getByTestId("team-starter-paid"));
    await waitFor(() => expect(hooks.gets).toContain("/team/starter?paid=1"), { timeout: 10_000 });
    const paid = (await screen.findByTestId("team-starter-tick-openai|gpt-4o", {}, { timeout: 10_000 })) as HTMLInputElement;
    expect(paid.checked).toBe(false);
  });

  it("with no model connected it says so and offers no Create", async () => {
    hooks.responses["/team/starter"] = {
      rows: [],
      paid: false,
      paid_available: 0,
      note: "No model is connected yet, so there is no one to put on the team.",
    };
    render(<ModelTeam />);
    fireEvent.click(screen.getByTestId("team-starter-open"));
    const note = await screen.findByTestId("team-starter-note", {}, { timeout: 10_000 });
    expect(note.textContent).toMatch(/No model is connected yet/);
    expect((screen.getByTestId("team-starter-create") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId("team-starter-create"));
    expect(hooks.posts).toEqual([]);
  });
});

describe("where it lives", () => {
  it("is a Settings section under Models, reached at /settings?section=models-team", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const src = readFileSync(join(process.cwd(), "components", "settings", "SettingsHome.tsx"), "utf8").replace(/\r\n/g, "\n");
    const models = src.slice(src.indexOf("  models: ["), src.indexOf("  connections: ["));
    expect(models).toContain('id: "models-team"');
    expect(models).toContain('label: "Model team"');
    expect(models).toContain('import("@/components/settings/ModelTeam"), "ModelTeam"');
  });
});

/* ------------------------------------------------- the agent's own card --- */

const MEMBER: DynamicAgentFull = {
  name: "codex-coder",
  description: "writes code",
  base_type: "builder",
  provider: "codex-cli",
  model: "subscription",
  tools: [],
  roles: ["code", "test"],
};

function renderCard(dynamic: DynamicAgentFull[]) {
  return render(
    <SetupCardHarness
      initialOpen
      builtin={["supervisor", "builder", "planner", "reviewer"]}
      dynamic={dynamic}
      remotes={[]}
      models={MODELS}
      onAgentsChanged={() => {}}
      onRemotesChanged={() => {}}
    />,
  );
}

describe("an agent's card on the Agents page", () => {
  it("says its team roles, and edits them — sent only when touched", async () => {
    renderCard([MEMBER, { name: "plain", description: "", base_type: "builder", tools: [] }]);
    expect(screen.getByTestId("agent-roles-codex-coder").textContent).toBe(
      "Model team · Writing code, Running and fixing tests",
    );
    expect(screen.queryByTestId("agent-roles-plain")).toBeNull();
    // An untouched save sends no roles.
    fireEvent.click(screen.getByTitle(/Edit the persona of "codex-coder"/));
    fireEvent.change(screen.getByLabelText("Description for codex-coder"), { target: { value: "writes more code" } });
    fireEvent.click(screen.getByRole("button", { name: /^Save/ }));
    await waitFor(() => expect(hooks.patches).toHaveLength(1));
    expect(hooks.patches[0].body).not.toHaveProperty("roles");
    // A touched one sends them.
    fireEvent.click(screen.getByTitle(/Edit the persona of "codex-coder"/));
    const roles = within(screen.getByTestId("team-roles-codex-coder"));
    fireEvent.click(roles.getByTestId("team-role-codex-coder-read"));
    fireEvent.click(screen.getByRole("button", { name: /^Save/ }));
    await waitFor(() => expect(hooks.patches).toHaveLength(2));
    expect(hooks.patches[1].body.roles).toEqual(["read", "code", "test"]);
  });
});

/* ------------------------------------------------ a coding session's row --- */

describe("a coding session's helper row names the member and its model", () => {
  it("names the model on the live row and on the saved one", () => {
    let helpers = applyHelperFrame([], {
      id: "h1", agent: "custom:local-reader", task: "Read app.py.", state: "running", session: "s-1",
    });
    helpers = applyHelperFrame(helpers, { id: "h1", provider: "ollama", model: "qwen3:8b" });
    render(<LiveHelperRows helpers={helpers} models={MODELS} />);
    expect(screen.getByTestId("helper-model").textContent).toBe("On Qwen 3 8B");
    expect(screen.getByText(/Your local-reader agent/)).toBeTruthy();
    cleanup();
    render(
      <SavedHelperRows
        models={MODELS}
        raw={[{ id: "h2", agent: "custom:codex-coder", task: "Write it.", state: "done", session: "s-2",
                provider: "codex-cli", model: "subscription", startedAt: 1, endedAt: 2, steps: 1, work: [] }]}
      />,
    );
    expect(screen.getByTestId("helper-model").textContent).toMatch(/^On /);
    expect(screen.getByTestId("helper-model").textContent).not.toBe("On subscription");
    expect(screen.getByText(/Your codex-coder agent/)).toBeTruthy();
  });

  it("says nothing about a model the row does not know", () => {
    render(
      <SavedHelperRows raw={[{ id: "h3", agent: "researcher", task: "t", state: "done", session: "s-3", work: [] }]} />,
    );
    expect(screen.queryByTestId("helper-model")).toBeNull();
  });
});
