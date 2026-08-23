/**
 * Goal birth v1.208.0 — goals are BORN IN CONTEXT, never configured from a
 * blank panel.
 *
 * What carries weight here:
 *
 *  - the chip's HEURISTIC is deterministic and narrow: recurring vocabulary
 *    in the user's OWN message, or a turn that ran workflow/schedule tools —
 *    and NEVER trivial Q&A ("what is 2+2"), because a false chip trains the
 *    user to ignore every chip;
 *  - dismissal is per turn (the workflow-chip idiom) — "Not now" kills the
 *    offer for this turn;
 *  - the card is PRE-FILLED deterministically from the turn: short name,
 *    template contract quoting the ask verbatim, schedule suggested from the
 *    vocabulary ("every morning" → the 9am cron preset, no vocabulary →
 *    manual);
 *  - the budget default is TIGHT ($2, checked before every run), visible,
 *    and editable;
 *  - Create POSTs the EXACT wire body to /goals — nothing granted, verifier
 *    manual, budget a single dollar cap checked before every run;
 *  - the guarantees sentence at the button is pinned VERBATIM — no rewrite
 *    may quietly soften it;
 *  - a rejected create shows the daemon's error verbatim; success shows the
 *    door to /autonomy;
 *  - page.tsx call-site pins (the v1.163.0 lesson: a mutation deleting the
 *    real call site leaves every component test green).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    posts: [] as { path: string; body: unknown }[],
    postResponses: {} as Record<string, unknown>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  get: () => Promise.reject(new api.FakeApiError("unmocked GET", 404)),
  post: (path: string, body: unknown) => {
    api.posts.push({ path, body });
    const r = api.postResponses[path];
    if (r instanceof api.FakeApiError) return Promise.reject(r);
    return Promise.resolve(r ?? {});
  },
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

// Next's Link needs a router context in a real app; the established test
// idiom is a plain anchor that keeps href + onClick behaviour.
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import {
  GOAL_DEFAULT_BUDGET_DOLLARS,
  GOAL_GUARANTEES,
  GoalBirth,
  goalContractFrom,
  goalNameFrom,
  shouldOfferGoal,
  suggestSchedule,
} from "@/components/chat/GoalContractCard";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  api.posts.length = 0;
  for (const k of Object.keys(api.postResponses)) delete api.postResponses[k];
});

const RECURRING_ASK = "every morning summarize the inbox";

/** Chip → card, for the tests that exercise the card. */
function openCard(userText = RECURRING_ASK, projectId?: string) {
  render(
    <GoalBirth userText={userText} toolsUsed={[]} projectId={projectId} />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: /keep doing this\? → make it a goal/i }),
  );
  return screen.getByTestId("goal-contract-card");
}

/* ------------------------------------------------------------------------- */
describe("shouldOfferGoal — the deliberately-high bar", () => {
  it("fires on recurring vocabulary in the user's own words", () => {
    expect(shouldOfferGoal(RECURRING_ASK)).toBe(true);
    expect(shouldOfferGoal("watch the CI and reopen failures")).toBe(true);
    expect(shouldOfferGoal("monitor disk space on the NAS")).toBe(true);
    expect(shouldOfferGoal("send the digest each week")).toBe(true);
    expect(shouldOfferGoal("keep checking the ticket queue")).toBe(true);
  });

  it("NEVER fires on trivial Q&A", () => {
    expect(shouldOfferGoal("what is 2+2")).toBe(false);
    expect(shouldOfferGoal("explain this stack trace")).toBe(false);
    expect(shouldOfferGoal("")).toBe(false);
  });

  it("is word-bounded — everyone/watches/monitored do not match", () => {
    expect(shouldOfferGoal("everyone loves fancy watches")).toBe(false);
  });

  it("fires when the turn ran workflow/schedule tools, even without vocabulary", () => {
    expect(shouldOfferGoal("close the books", ["workflow_run"])).toBe(true);
    expect(shouldOfferGoal("remind me later", ["schedule_create"])).toBe(true);
    expect(shouldOfferGoal("close the books", ["read_document"])).toBe(false);
  });
});

/* ------------------------------------------------------------------------- */
describe("deterministic pre-fill derivations", () => {
  it("goalNameFrom drops the scheduling lead-in and keeps a short core", () => {
    expect(goalNameFrom(RECURRING_ASK)).toBe("summarize the inbox");
    expect(goalNameFrom("please can you daily check the ticket queue")).toBe(
      "check the ticket queue",
    );
    expect(goalNameFrom("")).toBe("standing goal");
  });

  it("goalContractFrom quotes the ask VERBATIM inside a fixed template", () => {
    const c = goalContractFrom(RECURRING_ASK);
    expect(c).toContain(`The ask, in your words: "${RECURRING_ASK}"`);
    expect(c).toContain("Standing goal, born from a chat turn.");
    expect(c).toContain("stay inside the budget");
  });

  it("suggestSchedule maps vocabulary to cron presets, else manual", () => {
    expect(suggestSchedule(RECURRING_ASK)).toBe("0 9 * * *");
    expect(suggestSchedule("run the backup nightly")).toBe("0 21 * * *");
    expect(suggestSchedule("send the digest each week")).toBe("0 9 * * 1");
    // Recurring-LOOKING but not calendar-shaped → manual (no schedule sent).
    expect(suggestSchedule("watch the CI and reopen failures")).toBe("");
  });
});

/* ------------------------------------------------------------------------- */
describe("the chip — shows, never over-triggers, dismisses per turn", () => {
  it("appears for a recurring ask", () => {
    render(<GoalBirth userText={RECURRING_ASK} toolsUsed={[]} />);
    expect(
      screen.getByRole("button", {
        name: /keep doing this\? → make it a goal/i,
      }),
    ).toBeInTheDocument();
  });

  it("renders NOTHING for trivial Q&A", () => {
    const { container } = render(
      <GoalBirth userText="what is 2+2" toolsUsed={[]} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("the × dismisses the offer for this turn", () => {
    render(<GoalBirth userText={RECURRING_ASK} toolsUsed={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(
      screen.queryByRole("button", {
        name: /keep doing this\? → make it a goal/i,
      }),
    ).not.toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------------- */
describe("the card — pre-filled review, one decision", () => {
  it("pre-fills name, contract (ask verbatim), and the suggested schedule", () => {
    openCard();
    expect(screen.getByLabelText("Goal name")).toHaveValue(
      "summarize the inbox",
    );
    expect(
      (screen.getByLabelText("Goal contract") as HTMLTextAreaElement).value,
    ).toContain(`The ask, in your words: "${RECURRING_ASK}"`);
    expect(screen.getByLabelText("Goal schedule")).toHaveValue("0 9 * * *");
    // The placeholder must say what actually happens (the engine produces
    // the scheduler row): empty = manual, a cron fires on schedule.
    expect(screen.getByLabelText("Goal schedule")).toHaveAttribute(
      "placeholder",
      "empty = manual — you run it; a cron fires on schedule",
    );
  });

  it("shows the TIGHT default budget ($2) and it is editable", () => {
    openCard();
    const budget = screen.getByLabelText("Goal budget in dollars");
    expect(budget).toHaveValue(String(GOAL_DEFAULT_BUDGET_DOLLARS));
    expect(GOAL_DEFAULT_BUDGET_DOLLARS).toBe(2);
    fireEvent.change(budget, { target: { value: "5" } });
    expect(budget).toHaveValue("5");
  });

  it("a zero/garbage budget disables Create — the pre-run gate cannot be blank", () => {
    openCard();
    fireEvent.change(screen.getByLabelText("Goal budget in dollars"), {
      target: { value: "0" },
    });
    expect(
      screen.getByText(/budget must be a number above zero/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create goal" })).toBeDisabled();
  });

  it("Create POSTs the EXACT wire body — nothing granted, verifier manual", async () => {
    openCard(RECURRING_ASK, "p1");
    fireEvent.click(screen.getByRole("button", { name: "Create goal" }));
    await waitFor(() => expect(api.posts.length).toBe(1));
    expect(api.posts[0].path).toBe("/goals");
    expect(api.posts[0].body).toEqual({
      name: "summarize the inbox",
      contract_text: goalContractFrom(RECURRING_ASK),
      schedule: "0 9 * * *",
      budget: { max_dollars: 2 },
      verifier: { kind: "manual" },
      project_id: "p1",
    });
  });

  it("no vocabulary schedule and no project → those keys are ABSENT, and an edited budget is sent", async () => {
    openCard("watch the CI and reopen failures");
    fireEvent.change(screen.getByLabelText("Goal budget in dollars"), {
      target: { value: "5" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create goal" }));
    await waitFor(() => expect(api.posts.length).toBe(1));
    const body = api.posts[0].body as Record<string, unknown>;
    expect(body.budget).toEqual({ max_dollars: 5 });
    expect("schedule" in body).toBe(false);
    expect("project_id" in body).toBe(false);
    expect("allowed_grants" in body).toBe(false);
  });

  it("the guarantees are stated AT the button, verbatim", () => {
    // Pin the SENTENCE, not just the constant — a rewrite of the constant
    // must fail here, not silently ride along.
    // "Checked before every run" is the CHECKABLE truth (the gate is
    // pre-spawn: a run already in progress finishes and is counted — nothing
    // stops it mid-flight). "Stop always works" is engine truth: stop cancels
    // the running session.
    expect(GOAL_GUARANTEES).toBe(
      "Deny-floor tools can never be granted. The budget is checked before " +
        "every run — a run already in progress finishes and is counted. " +
        "Everything is logged and undoable. Stop always works.",
    );
    openCard();
    expect(screen.getByText(GOAL_GUARANTEES)).toBeInTheDocument();
  });

  it("success shows the door: 'See your goal' → /autonomy", async () => {
    openCard();
    fireEvent.click(screen.getByRole("button", { name: "Create goal" }));
    const door = await screen.findByRole("link", { name: /see your goal/i });
    expect(door).toHaveAttribute("href", "/autonomy");
    expect(screen.getByText("Goal created")).toBeInTheDocument();
  });

  it("a rejected create shows the daemon's error VERBATIM", async () => {
    api.postResponses["/goals"] = new api.FakeApiError(
      "budget: max_dollars must be at least 0.5",
      422,
    );
    openCard();
    fireEvent.click(screen.getByRole("button", { name: "Create goal" }));
    expect(
      await screen.findByText("budget: max_dollars must be at least 0.5"),
    ).toBeInTheDocument();
  });

  it("'Not now' on the card retires the whole offer for this turn", () => {
    openCard();
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(screen.queryByTestId("goal-contract-card")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: /keep doing this\? → make it a goal/i,
      }),
    ).not.toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------------- */
describe("source pins — the call site the component tests cannot see", () => {
  const pageSrc = readFileSync(
    join(__dirname, "..", "app", "chat", "page.tsx"),
    "utf8",
  );
  const cardSrc = readFileSync(
    join(__dirname, "..", "components", "chat", "GoalContractCard.tsx"),
    "utf8",
  );

  it("chat page imports GoalBirth and mounts it on the newest settled reply", () => {
    expect(pageSrc).toContain(
      'import { GoalBirth } from "@/components/chat/GoalContractCard";',
    );
    // Gated exactly like the workflow chip: last message, turn settled.
    expect(pageSrc).toMatch(
      /\{i === messages\.length - 1 && !busy && \(\s*<GoalBirth/,
    );
  });

  it("the chip judges the USER'S message (the turn before) plus the turn's tools", () => {
    const at = pageSrc.indexOf("<GoalBirth");
    expect(at).toBeGreaterThan(-1);
    const mount = pageSrc.slice(at, at + 600);
    expect(mount).toContain("messages[i - 1].content");
    expect(mount).toContain('messages[i - 1].role === "user"');
    expect(mount).toContain("toolsUsed={m.toolsUsed}");
    expect(mount).toContain("projectId={projectId}");
  });

  it("the over-trigger bar is stated where the heuristic lives", () => {
    // The comment is load-bearing: it tells the next editor WHY the
    // heuristic must stay narrow before they widen it.
    expect(cardSrc).toMatch(/trains the user to ignore/i);
    expect(pageSrc).toMatch(/false chip trains the user\s+to ignore/i);
  });

  it("no control bytes in the touched sources", () => {
    const control = new RegExp("[\u0000-\u0008\u000B\u000C\u000E-\u001F]");
    for (const src of [pageSrc, cardSrc]) {
      expect(control.test(src)).toBe(false);
    }
  });
});
