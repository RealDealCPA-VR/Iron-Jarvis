/**
 * A custom agent is an employee (v1.295.0, wave 1): a job card, a monthly
 * allowance, a day off.
 *
 * WHAT THESE TESTS GUARD — the WIRE, not the look:
 *  - creating with the disclosure open POSTs every employee field under the
 *    daemon's names, a blank step budget sends NO key, a blank allowance is 0;
 *  - an edit sends ONLY what changed: the model as a provider+model pair and
 *    nothing else, deny chips as `deny_tools` only when touched, and an
 *    untouched save carries none of the employee keys at all;
 *  - Pause POSTs /agents/<name>/pause with the typed reason; Resume POSTs
 *    /resume;
 *  - the allowance meter says the reading in words and carries the daemon's
 *    status (the colour), the paused badge and the reports-to caption
 *    render from the row, and all of it is ABSENT on an older daemon's row;
 *  - the roster's paused pill, the job card's disabled option + reason, and
 *    the bell's two new activity mappings.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: Record<string, unknown> }>,
  patches: [] as Array<{ path: string; body: Record<string, unknown> }>,
  /** When set, the mocked PATCH rejects with it (the 422 pin). */
  patchReject: null as null | (() => Promise<never>),
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
    get: (path: string) => Promise.resolve(path === "/tools" ? { tools: [] } : {}),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    post: (path: string, body: Record<string, unknown>) => {
      hooks.posts.push({ path, body });
      return Promise.resolve({});
    },
    patch: (path: string, body: Record<string, unknown>) => {
      hooks.patches.push({ path, body });
      return hooks.patchReject ? hooks.patchReject() : Promise.resolve({});
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
    // v1.250.0 mock contract: every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: unknown }) =>
      createElement(Fragment, null, children as never),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => tagFor(String(tag)),
    }),
  };
});

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { PauseCircle, Wallet } from "lucide-react";
import type { DynamicAgentFull } from "@/components/agents/SetupCard";
import {
  createEmployeeFields,
  EMPTY_DRAFT,
  patchEmployeeFields,
} from "@/components/agents/SetupCard";
import { RosterStrip, type RosterEntry } from "@/components/agents/RosterStrip";
import { toActivity } from "@/components/NotificationBell";
import type { AgentAllowance, IJEvent } from "@/lib/types";
import { SetupCardHarness } from "./helpers/setupCardHarness";

const BUILTIN = ["supervisor", "builder", "planner", "reviewer"];
const MODELS = [
  { provider: "openai", model: "gpt-5" },
  { provider: "anthropic", model: "claude-opus" },
];

/** An agent as an OLDER daemon serves it — none of the v1.295.0 fields. */
const PLAIN: DynamicAgentFull = {
  name: "skeptic",
  description: "challenges assumptions",
  base_type: "builder",
  tools: [],
  effective_tools: ["read_file"],
};

const ANALYST: DynamicAgentFull = {
  name: "analyst",
  description: "your analyst",
  base_type: "planner",
  tools: [],
};

const OK_ALLOWANCE: AgentAllowance = {
  month: "2026-10",
  tokens: 50_000,
  usd: 0,
  spent_tokens: 12_400,
  spent_usd: 0.31,
  runs: 3,
  pct: 24,
  left_tokens: 37_600,
  left_usd: null,
  status: "ok",
};

function renderCard(dynamic: DynamicAgentFull[], opts: { onChanged?: () => void } = {}) {
  return render(
    <SetupCardHarness
      initialOpen
      builtin={BUILTIN}
      dynamic={dynamic}
      remotes={[]}
      models={MODELS}
      onAgentsChanged={opts.onChanged ?? (() => {})}
      onRemotesChanged={() => {}}
    />,
  );
}

function openEditor(name: string) {
  fireEvent.click(screen.getByTitle(new RegExp(`Edit the persona of "${name}"`)));
  return within(screen.getByTestId(`employee-editor-${name}`));
}

const lastPatch = () => hooks.patches[hooks.patches.length - 1];

beforeEach(() => {
  hooks.api = {};
  hooks.posts = [];
  hooks.patches = [];
});
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

/* ------------------------------------------------------------- creating --- */

describe("creating an employee", () => {
  async function fillAndOpen() {
    renderCard([ANALYST]);
    fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: "skeptic" } });
    fireEvent.change(screen.getByLabelText("Persona prompt"), {
      target: { value: "You are a skeptic." },
    });
    fireEvent.click(screen.getByTestId("employee-details-toggle"));
    return within(screen.getByTestId("employee-details"));
  }

  it("POSTs every employee field under the daemon's names", async () => {
    const details = await fillAndOpen();
    const base = details.getByLabelText("Base type") as HTMLSelectElement;
    // The daemon's list minus the supervisor — the one that hands work out.
    expect(Array.from(base.options).map((o) => o.value)).toEqual([
      "builder",
      "planner",
      "reviewer",
    ]);
    fireEvent.change(base, { target: { value: "planner" } });
    fireEvent.change(details.getByLabelText("Approval posture"), {
      target: { value: "approve_for_me" },
    });
    fireEvent.change(details.getByLabelText("Monthly token allowance"), {
      target: { value: "50000" },
    });
    fireEvent.change(details.getByLabelText("Monthly dollar allowance"), {
      target: { value: "10" },
    });
    fireEvent.change(details.getByLabelText("Step budget"), { target: { value: "40" } });
    const reports = details.getByLabelText("Reports to") as HTMLSelectElement;
    // You, the builtins, then the OTHER custom agents by bare name.
    // v1.316.0: built-in TEXT reads as a name (agentLabel); values stay raw.
    expect(Array.from(reports.options).map((o) => [o.value, o.textContent])).toEqual([
      ["", "You"],
      ["supervisor", "Supervisor"],
      ["builder", "Builder"],
      ["planner", "Planner"],
      ["reviewer", "Reviewer"],
      ["custom:analyst", "analyst"],
    ]);
    fireEvent.change(reports, { target: { value: "custom:analyst" } });
    const skillBox = within(screen.getByTestId("skills-new")).getByLabelText("Add a skill for skeptic");
    fireEvent.change(skillBox, { target: { value: "tax-research" } });
    fireEvent.keyDown(skillBox, { key: "Enter" });

    fireEvent.click(screen.getByRole("button", { name: /^create agent$/i }));
    // The success note is set LAST in the handler — the honest "it landed".
    await screen.findByText(/"skeptic" is ready/);
    expect(hooks.posts).toHaveLength(1);
    expect(hooks.posts[0].path).toBe("/agents");
    expect(hooks.posts[0].body).toEqual({
      name: "skeptic",
      system_prompt: "You are a skeptic.",
      tools: [],
      description: "",
      provider: "",
      model: "",
      base_type: "planner",
      approval_mode: "approve_for_me",
      allowance_tokens: 50000,
      allowance_usd: 10,
      reports_to: "custom:analyst",
      max_steps: 40,
      skills: ["tax-research"],
    });
  });

  it("sends 0 for a blank allowance and NO max_steps key when the budget is blank", async () => {
    await fillAndOpen();
    fireEvent.click(screen.getByRole("button", { name: /^create agent$/i }));
    await screen.findByText(/"skeptic" is ready/);
    const body = hooks.posts[0].body;
    expect(body.allowance_tokens).toBe(0);
    expect(body.allowance_usd).toBe(0);
    expect(body.base_type).toBe("builder");
    expect(body.approval_mode).toBe("");
    expect(body.reports_to).toBe("");
    expect(body.skills).toEqual([]);
    expect("max_steps" in body).toBe(false);
  });

  it("createEmployeeFields: blank steps omit the key, a bad budget is left out too", () => {
    expect(createEmployeeFields(EMPTY_DRAFT, "builder")).toEqual({
      base_type: "builder",
      skills: [],
      approval_mode: "",
      allowance_tokens: 0,
      allowance_usd: 0,
      reports_to: "",
    });
    expect("max_steps" in createEmployeeFields({ ...EMPTY_DRAFT, maxSteps: "0" }, "b")).toBe(false);
    expect("max_steps" in createEmployeeFields({ ...EMPTY_DRAFT, maxSteps: "abc" }, "b")).toBe(
      false,
    );
    expect(createEmployeeFields({ ...EMPTY_DRAFT, maxSteps: "12" }, "b").max_steps).toBe(12);
  });
});

/* -------------------------------------------------------------- editing --- */

describe("editing an employee sends ONLY what changed", () => {
  it("changing only the model PATCHes {provider, model} and nothing else", async () => {
    // The row HAS a persona: an unchanged one is not re-sent (it used to be).
    renderCard([
      {
        ...PLAIN,
        system_prompt: "You are a skeptic.",
        allowance: OK_ALLOWANCE,
        approval_mode: "always_ask",
        max_steps: 30,
        skills: ["tax-research"],
        deny_tools: ["shell"],
      },
    ]);
    const editor = openEditor("skeptic");
    fireEvent.change(editor.getByLabelText("Preferred model for skeptic"), {
      target: { value: "openai|gpt-5" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    // The editor closes AFTER the PATCH resolves — wait for that, not the call.
    await waitFor(() => expect(screen.queryByTestId("employee-editor-skeptic")).toBeNull());
    expect(hooks.patches).toHaveLength(1);
    expect(lastPatch().path).toBe("/agents/skeptic");
    expect(lastPatch().body).toEqual({ provider: "openai", model: "gpt-5" });
  });

  it("a changed persona still PATCHes system_prompt, alone", async () => {
    renderCard([{ ...PLAIN, system_prompt: "You are a skeptic." }]);
    openEditor("skeptic");
    fireEvent.change(screen.getByLabelText("Persona prompt for skeptic"), {
      target: { value: "You are a careful skeptic." },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(screen.queryByTestId("employee-editor-skeptic")).toBeNull());
    expect(lastPatch().body).toEqual({ system_prompt: "You are a careful skeptic." });
  });

  it("skills chips PATCH `skills` only when touched", async () => {
    renderCard([{ ...PLAIN, skills: ["tax-research"] }]);
    const editor = openEditor("skeptic");
    const chips = within(editor.getByTestId("skills-skeptic"));
    expect(chips.getByText("tax-research")).toBeTruthy();
    const box = chips.getByLabelText("Add a skill for skeptic");
    fireEvent.change(box, { target: { value: "excel" } });
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(screen.queryByTestId("employee-editor-skeptic")).toBeNull());
    expect(lastPatch().body).toEqual({ skills: ["tax-research", "excel"] });
  });

  it("an untouched save carries no employee key at all (anti-vacuity)", async () => {
    renderCard([
      { ...PLAIN, system_prompt: "You are a skeptic.", allowance: OK_ALLOWANCE, deny_tools: ["shell"], skills: ["x"], reports_to: "builder" },
    ]);
    openEditor("skeptic");
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(screen.queryByTestId("employee-editor-skeptic")).toBeNull());
    expect(lastPatch().body).toEqual({});
  });

  it("deny chips: Enter adds, × removes, and deny_tools is PATCHed only when touched", async () => {
    renderCard([{ ...PLAIN, deny_tools: ["shell"] }]);
    const editor = openEditor("skeptic");
    const chips = within(editor.getByTestId("deny-tools-skeptic"));
    expect(chips.getByText("shell")).toBeTruthy();
    const box = chips.getByLabelText("Deny a tool for skeptic");
    fireEvent.change(box, { target: { value: "web_search" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(chips.getByText("web_search")).toBeTruthy();
    fireEvent.click(chips.getByLabelText("Stop denying shell for skeptic"));
    expect(chips.queryByText("shell")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(screen.queryByTestId("employee-editor-skeptic")).toBeNull());
    expect(lastPatch().body).toEqual({ deny_tools: ["web_search"] });
  });

  it("every other job-card field PATCHes under its wire name, blank steps as null", async () => {
    renderCard([ANALYST, { ...PLAIN, max_steps: 30 }]);
    const editor = openEditor("skeptic");
    fireEvent.change(editor.getByLabelText("Base type for skeptic"), {
      target: { value: "reviewer" },
    });
    fireEvent.change(editor.getByLabelText("Approval posture for skeptic"), {
      target: { value: "always_ask" },
    });
    fireEvent.change(editor.getByLabelText("Monthly token allowance for skeptic"), {
      target: { value: "1000" },
    });
    fireEvent.change(editor.getByLabelText("Step budget for skeptic"), {
      target: { value: "" },
    });
    fireEvent.change(editor.getByLabelText("Reports to for skeptic"), {
      target: { value: "custom:analyst" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(screen.queryByTestId("employee-editor-skeptic")).toBeNull());
    // A BLANKED budget is `clear_max_steps: true` and NO `max_steps` — the
    // route reads null as "keep" and refuses 0, so neither can clear it.
    expect(lastPatch().body).toEqual({
      base_type: "reviewer",
      approval_mode: "always_ask",
      allowance_tokens: 1000,
      clear_max_steps: true,
      reports_to: "custom:analyst",
    });
  });

  it("a typed step budget PATCHes max_steps: N, and nothing else", async () => {
    renderCard([{ ...PLAIN, max_steps: 30 }]);
    const editor = openEditor("skeptic");
    fireEvent.change(editor.getByLabelText("Step budget for skeptic"), {
      target: { value: "45" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(screen.queryByTestId("employee-editor-skeptic")).toBeNull());
    expect(lastPatch().body).toEqual({ max_steps: 45 });
  });

  it("patchEmployeeFields never sends an unchanged field; blank clears, a number sets", () => {
    const d = { ...EMPTY_DRAFT, allowanceTokens: "500", maxSteps: "20" };
    expect(patchEmployeeFields(d, { ...d })).toEqual({});
    expect(patchEmployeeFields(d, { ...d, allowanceTokens: "" })).toEqual({ allowance_tokens: 0 });
    expect(patchEmployeeFields(d, { ...d, maxSteps: "" })).toEqual({ clear_max_steps: true });
    expect(patchEmployeeFields(d, { ...d, maxSteps: "7" })).toEqual({ max_steps: 7 });
    // Garbage a number input lets through sends neither key.
    expect(patchEmployeeFields(d, { ...d, maxSteps: "e" })).toEqual({});
    expect("max_steps" in patchEmployeeFields(d, { ...d, maxSteps: "" })).toBe(false);
  });

  it("a refused budget renders the daemon's sentence on the row (api.ts flattens a pydantic list first)", async () => {
    const { ApiError } = await import("@/lib/api");
    hooks.patchReject = () => Promise.reject(new ApiError("max_steps: must be 1..200", 422));
    renderCard([{ ...PLAIN, max_steps: 30 }]);
    const editor = openEditor("skeptic");
    fireEvent.change(editor.getByLabelText("Step budget for skeptic"), {
      target: { value: "999" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    // The error is set LAST in the failing handler; the editor stays open.
    await screen.findByText("max_steps: must be 1..200");
    expect(screen.getByTestId("employee-editor-skeptic")).toBeTruthy();
    hooks.patchReject = null;
  });
});

/* -------------------------------------------------------------- day off --- */

describe("a day off", () => {
  it("Pause posts /agents/<name>/pause with the typed reason, then closes the box", async () => {
    const onChanged = vi.fn();
    renderCard([PLAIN], { onChanged });
    expect(screen.queryByTestId("agent-paused-skeptic")).toBeNull();
    fireEvent.click(screen.getByTestId("agent-pause-skeptic"));
    fireEvent.change(screen.getByTestId("agent-pause-reason-skeptic"), {
      target: { value: "budget review" },
    });
    fireEvent.click(screen.getByTestId("agent-pause-confirm-skeptic"));
    // The box closes LAST in the handler, after the POST landed.
    await waitFor(() => expect(screen.queryByTestId("agent-pause-reason-skeptic")).toBeNull());
    expect(hooks.posts).toEqual([
      { path: "/agents/skeptic/pause", body: { reason: "budget review" } },
    ]);
    expect(onChanged).toHaveBeenCalled();
  });

  it("Resume posts /agents/<name>/resume; the badge and the Resume button come from the row", async () => {
    const onChanged = vi.fn();
    renderCard([{ ...PLAIN, paused: { reason: "on leave", at: "2026-10-01T09:00:00Z" } }], {
      onChanged,
    });
    expect(screen.getByTestId("agent-paused-skeptic").textContent).toBe("Paused — on leave");
    expect(screen.queryByTestId("agent-pause-skeptic")).toBeNull();
    fireEvent.click(screen.getByTestId("agent-resume-skeptic"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(hooks.posts).toEqual([{ path: "/agents/skeptic/resume", body: {} }]);
  });
});

/* ------------------------------------------------------------ the meter --- */

describe("the allowance meter and the captions on the row", () => {
  it("says the reading in words with the daemon's status, zinc while ok", () => {
    renderCard([{ ...PLAIN, allowance: OK_ALLOWANCE, reports_to: "builder" }]);
    const meter = screen.getByTestId("agent-allowance-skeptic");
    expect(meter.getAttribute("data-status")).toBe("ok");
    expect(meter.textContent).toBe("Spent 12.4k of 50k tokens this month · 24%");
    expect(meter.querySelector(".bg-zinc-500")).toBeTruthy();
    expect((meter.querySelector("[role=progressbar] > div") as HTMLElement).style.width).toBe(
      "24%",
    );
    expect(screen.getByTestId("agent-reports-to-skeptic").textContent).toBe("Reports to Builder"); // v1.316.0: a built-in reads as a name
  });

  it("warning is amber, exhausted is rose, and a dollar bound rides along", () => {
    renderCard([
      { ...PLAIN, name: "warn", allowance: { ...OK_ALLOWANCE, usd: 10, spent_usd: 1.2, pct: 82, status: "warning" } },
      { ...PLAIN, name: "gone", allowance: { ...OK_ALLOWANCE, pct: 100, status: "exhausted" } },
      { ...PLAIN, name: "usd", allowance: { ...OK_ALLOWANCE, tokens: 0, usd: 10, spent_usd: 1.2, pct: 12, status: "ok" } },
    ]);
    const warn = screen.getByTestId("agent-allowance-warn");
    expect(warn.getAttribute("data-status")).toBe("warning");
    expect(warn.querySelector(".bg-amber-400")).toBeTruthy();
    expect(warn.textContent).toBe("Spent 12.4k of 50k tokens this month · $1.20 of $10 · 82%");
    const gone = screen.getByTestId("agent-allowance-gone");
    expect(gone.getAttribute("data-status")).toBe("exhausted");
    expect(gone.querySelector(".bg-rose-500")).toBeTruthy();
    expect(screen.getByTestId("agent-allowance-usd").textContent).toBe(
      "Spent $1.20 of $10 this month · 12%",
    );
  });

  it("an unlimited allowance, and an older daemon's row, render none of it", () => {
    renderCard([
      { ...PLAIN, name: "free", allowance: { ...OK_ALLOWANCE, tokens: 0, pct: null, status: "unlimited" } },
      { ...PLAIN, name: "old" },
    ]);
    for (const n of ["free", "old"]) {
      expect(screen.queryByTestId(`agent-allowance-${n}`)).toBeNull();
      expect(screen.queryByTestId(`agent-paused-${n}`)).toBeNull();
      expect(screen.queryByTestId(`agent-reports-to-${n}`)).toBeNull();
    }
    // The pause door is still there — an older daemon just answers 404.
    expect(screen.getByTestId("agent-pause-old")).toBeTruthy();
  });
});

/* --------------------------------------------------------------- roster --- */

const ROSTER: RosterEntry[] = [
  {
    name: "custom:analyst",
    kind: "dynamic",
    description: "your analyst",
    delegable: true,
    healthy: false,
    stats: null,
    activity: "busy",
    paused: true,
    pause_reason: "budget review",
    allowance: { ...OK_ALLOWANCE, pct: 82, status: "warning" },
  },
  {
    name: "builder",
    kind: "builtin",
    description: "hands-on doer",
    delegable: true,
    healthy: true,
    stats: null,
  },
];


/* ----------------------------------------------------------------- bell --- */

describe("toActivity maps the two employee events", () => {
  const ev = (type: string, payload: Record<string, unknown>): IJEvent => ({
    id: "e1",
    type,
    session_id: null,
    ts: "2026-10-01T09:00:00Z",
    payload,
  });

  // v1.309.0: bare /agents is the New-task composer now, so an agent's row
  // opens THAT agent on Your team (links-wave1-v1309 pins the contract).
  it("agent.paused → that agent on Your team, PauseCircle, the name and the reason", () => {
    const item = toActivity(ev("agent.paused", { name: "skeptic", reason: "budget review" }));
    expect(item).toMatchObject({
      href: "/agents?view=team&agent=skeptic",
      icon: PauseCircle,
      title: "Agent paused: skeptic",
      body: "budget review",
    });
    // Defensive like its neighbours: a malformed payload still maps.
    expect(toActivity(ev("agent.paused", { name: 7, reason: null }))).toMatchObject({
      title: "Agent paused: an agent",
      body: "",
    });
  });

  it("agent.allowance_warning → that agent on Your team, Wallet, the percentage and the bound that is set", () => {
    const tokens = toActivity(
      ev("agent.allowance_warning", {
        name: "skeptic",
        pct: 82.4,
        spent_tokens: 41200,
        spent_usd: 1.2,
        allowance_tokens: 50000,
        allowance_usd: 10,
      }),
    );
    expect(tokens).toMatchObject({
      href: "/agents?view=team&agent=skeptic",
      icon: Wallet,
      title: "skeptic has used 82% of its monthly allowance",
      body: "41.2k of 50k tokens",
    });
    // The bell formats through lib/format, never through RosterStrip (which
    // would drag framer-motion into the layout's chunk).
    const src = readFileSync(
      join(process.cwd(), "components", "NotificationBell.tsx"),
      "utf8",
    ).replace(/\r\n/g, "\n");
    expect(src).toContain('formatTokens } from "@/lib/format"');
    expect(src).not.toContain("RosterStrip");
    const usd = toActivity(
      ev("agent.allowance_warning", {
        name: "skeptic",
        pct: 90,
        spent_tokens: 41200,
        spent_usd: 9,
        allowance_tokens: 0,
        allowance_usd: 10,
      }),
    );
    expect(usd?.body).toBe("$9.00 of $10");
    expect(toActivity(ev("agent.allowance_warning", { name: "x", pct: 90 }))?.body).toBe("");
  });
});
