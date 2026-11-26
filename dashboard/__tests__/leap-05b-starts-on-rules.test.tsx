/**
 * Leap 05-b (R2.9): "when another task fails", and every start in one place
 * — the page half.
 *
 *  - the Rules page lists your schedules in a second section, "At a time",
 *    each a row that opens Schedules, with what it does and what it last did
 *    (GET /starts, read once when the page opens);
 *  - a row of GET /starts that is not one of the app's own pages is dropped;
 *  - the add form offers "Another task fails": which task, its name and how
 *    it must have ended ride the POST;
 *  - the workflow canvas's trigger node lists what already runs a SAVED
 *    workflow (GET /starts?workflow=<name>), and reads nothing for an unsaved
 *    one;
 *  - review: "Run it once more" is offered for a schedule or a workflow and
 *    rides the POST (`again`);
 *  - the new words never use "start" as a noun.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    gets: [] as string[],
    responses: {} as Record<string, unknown>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    api.gets.push(path);
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    return Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    return Promise.resolve({ ok: true });
  },
  patch: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {actions}
    </div>
  ),
}));

import ReflexPage from "@/app/reflex/page";
import { TriggerInspector } from "@/components/workflow/TriggerInspector";
import {
  AT_A_TIME,
  AT_A_TIME_WORDS,
  ENDED_OUTCOME_CHOICES,
  ENDED_WHAT_CHOICES,
  ONCE_MORE_LABEL,
  ONCE_MORE_WORDS,
  RUNS_IT_NOW,
  decodeStarts,
} from "@/lib/reflexRules";
import { NAV_ENTRIES } from "@/lib/nav";

afterEach(() => {
  cleanup();
  api.posts = [];
  api.gets = [];
  api.responses = {};
});

const SCHEDULE = {
  id: "sched_1",
  kind: "schedule",
  name: "Nightly report",
  when_words: "Every day at 2:00 AM.",
  does_words: "Does: Write the report.",
  enabled: true,
  last_words: "Last run: Failed. The run failed: the model is down.",
  href: "/schedules",
  store: "schedule",
};
const RULE_ROW = {
  id: "reflex_9",
  kind: "run_ended",
  name: "Tell me why",
  when_words: "When the schedule “Nightly report” fails.",
  does_words: "Jarvis does: Say why.",
  enabled: true,
  last_words: "Has not run yet.",
  href: "/reflex?rule=reflex_9",
  store: "rule",
};

function rule(over: Record<string, unknown> = {}) {
  return {
    id: "reflex_9",
    name: "Tell me why",
    source: "run_ended",
    match: "",
    action: "session",
    target: "",
    task_template: "Say why",
    project_id: null,
    enabled: true,
    created_at: "2026-11-26T10:00:00Z",
    last_fired_at: null,
    fire_count: 0,
    start: { what: "schedule", name: "Nightly report", outcome: "failed" },
    when_words: RULE_ROW.when_words,
    does_words: RULE_ROW.does_words,
    where_words: "In a work folder of its own.",
    limits_words: "Starts at most 12 times an hour, then pauses and tells you. Anything risky asks you first.",
    reports_to: null,
    watcher: null,
    ...over,
  };
}

function mountRules(rules: unknown[], starts: unknown[]) {
  api.responses["/reflex/rules"] = { rules };
  api.responses["/starts"] = { starts };
  api.responses["/workflows"] = { workflows: [] };
  api.responses["/agents/remote"] = { agents: [] };
  api.responses["/webhooks"] = { webhooks: [] };
  api.responses["/projects"] = { projects: [] };
  api.responses["/triggers"] = { triggers: {} };
  api.responses["/rehearsals?kind=rule"] = { rehearsals: [] };
  render(<ReflexPage />);
}

describe("every start in one place, on Rules", () => {
  it("lists your schedules At a time, each opening Schedules, with what it last did", async () => {
    mountRules([rule()], [SCHEDULE, RULE_ROW]);
    const section = await screen.findByTestId("reflex-at-a-time");
    const row = within(section).getByTestId("reflex-schedule-sched_1");
    expect(row.getAttribute("href")).toBe("/schedules");
    expect(row.textContent).toContain("Nightly report");
    expect(row.textContent).toContain("Every day at 2:00 AM. Does: Write the report.");
    expect(row.textContent).toContain("Last run: Failed.");
    expect(screen.getByText(`${AT_A_TIME} · 1`)).toBeTruthy();
    expect(screen.getByText(AT_A_TIME_WORDS)).toBeTruthy();
    // A rule is listed with the rules, never in the schedules' section.
    expect(within(section).queryByText("Tell me why")).toBeNull();
    expect(await screen.findByTestId("reflex-lines-reflex_9")).toBeTruthy();
    expect(api.gets.filter((g) => g === "/starts")).toHaveLength(1);
  });

  it("shows no At a time section when there are no schedules (or the list cannot be read)", async () => {
    mountRules([rule()], [RULE_ROW]);
    await screen.findByTestId("reflex-lines-reflex_9");
    expect(screen.queryByTestId("reflex-at-a-time")).toBeNull();
  });

  it("drops a row that is not one of the app's own pages", () => {
    const rows = decodeStarts({
      starts: [
        SCHEDULE,
        { ...SCHEDULE, id: "x", href: "https://evil.example/" },
        { ...SCHEDULE, id: "y", href: "//evil.example" },
        { ...SCHEDULE, id: "", name: "no id" },
        { ...SCHEDULE, id: "z", store: "elsewhere" },
      ],
    });
    expect(rows.map((r) => r.id)).toEqual(["sched_1"]);
    expect(decodeStarts(null)).toEqual([]);
    expect(decodeStarts({ starts: "nope" })).toEqual([]);
  });
});

describe("a rule for when another task fails", () => {
  it("the add form offers it and sends which task, its name and how it ended", async () => {
    mountRules([], []);
    fireEvent.click((await screen.findAllByText("Add rule"))[0]);
    fireEvent.click(screen.getByTestId("reflex-shape-other"));
    const source = screen.getByLabelText("Signal source") as HTMLSelectElement;
    expect(Array.from(source.options).map((o) => o.textContent)).toContain("Another task fails");
    fireEvent.change(source, { target: { value: "run_ended" } });
    const what = screen.getByTestId("reflex-ended-what") as HTMLSelectElement;
    expect(Array.from(what.options).map((o) => o.value)).toEqual(ENDED_WHAT_CHOICES.map((c) => c.value));
    fireEvent.change(what, { target: { value: "workflow" } });
    fireEvent.change(screen.getByTestId("reflex-ended-name"), { target: { value: "Month end" } });
    fireEvent.change(screen.getByTestId("reflex-ended-outcome"), { target: { value: "any" } });
    fireEvent.submit(source.closest("form")!);
    await waitFor(() => expect(api.posts).toHaveLength(1));
    expect(api.posts[0].path).toBe("/reflex/rules");
    expect(api.posts[0].body).toMatchObject({
      source: "run_ended", action: "session", match: "", what: "workflow", what_name: "Month end", outcome: "any",
    });
  });

  it("offers Run it once more for a schedule or a workflow only, and sends it", async () => {
    mountRules([], []);
    fireEvent.click((await screen.findAllByText("Add rule"))[0]);
    fireEvent.click(screen.getByTestId("reflex-shape-other"));
    const source = screen.getByLabelText("Signal source") as HTMLSelectElement;
    fireEvent.change(source, { target: { value: "run_ended" } });
    const what = screen.getByTestId("reflex-ended-what") as HTMLSelectElement;
    // An agent run has no record to run again from: no box.
    fireEvent.change(what, { target: { value: "agent" } });
    expect(screen.queryByTestId("reflex-ended-again")).toBeNull();
    fireEvent.change(what, { target: { value: "schedule" } });
    const again = screen.getByTestId("reflex-ended-again") as HTMLInputElement;
    expect(again.closest("label")?.textContent).toContain(ONCE_MORE_LABEL);
    expect(again.checked).toBe(false);
    fireEvent.click(again);
    fireEvent.change(screen.getByTestId("reflex-ended-name"), { target: { value: "Nightly report" } });
    fireEvent.submit(source.closest("form")!);
    await waitFor(() => expect(api.posts).toHaveLength(1));
    expect(api.posts[0].body).toMatchObject({ what: "schedule", what_name: "Nightly report", again: true });
  });

  it("its row is in its own section with the four lines", async () => {
    mountRules([rule()], []);
    const lines = await screen.findByTestId("reflex-lines-reflex_9");
    expect(lines.textContent).toContain("When the schedule “Nightly report” fails.");
    expect(screen.getByText("When another task fails · 1")).toBeTruthy();
  });
});

describe("the canvas's trigger node", () => {
  it("lists what already runs a saved workflow", async () => {
    api.responses["/starts?workflow=Month%20end"] = {
      starts: [
        { ...SCHEDULE, id: "sched_2", name: "Month end run", when_words: "On day 1 of every month at 6:00 AM." },
        { ...RULE_ROW, id: "reflex_3", kind: "webhook", name: "Inbox", when_words: "When the webhook “inbox” is called.", href: "/reflex?rule=reflex_3" },
      ],
    };
    render(<TriggerInspector workflowName="Month end" saved onClose={() => undefined} />);
    const list = await screen.findByTestId("trigger-runs-it-now");
    expect(list.textContent).toContain(RUNS_IT_NOW);
    const links = within(list).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/schedules", "/reflex?rule=reflex_3"]);
    expect(links[0].textContent).toBe("Schedule “Month end run”: On day 1 of every month at 6:00 AM.");
    expect(links[1].textContent).toBe("Rule “Inbox”: When the webhook “inbox” is called.");
  });

  it("reads nothing for a workflow that is not saved", async () => {
    render(<TriggerInspector workflowName="Draft" saved={false} onClose={() => undefined} />);
    await new Promise((r) => setTimeout(r, 0));
    expect(api.gets).toEqual([]);
    expect(screen.queryByTestId("trigger-runs-it-now")).toBeNull();
  });
});

describe("the words", () => {
  it("never use start as a noun", () => {
    const words = [
      AT_A_TIME, AT_A_TIME_WORDS, RUNS_IT_NOW, ONCE_MORE_LABEL, ONCE_MORE_WORDS,
      ...ENDED_WHAT_CHOICES.map((c) => c.label), ...ENDED_OUTCOME_CHOICES.map((c) => c.label),
    ];
    for (const w of words) {
      expect(w).not.toMatch(/\bstarts?\b/i);
    }
  });

  it("search finds Rules for a task that fails, and its line keeps the 05-a words", () => {
    // Review 3: the line shortened in review 2 dropped "in a folder", which
    // the 05-a pin reads (leap-05a-rule-card.test.tsx); both are held here.
    const rules = NAV_ENTRIES.find((i) => i.href === "/reflex");
    expect(rules?.aliases).toContain("when a task fails");
    expect(rules?.blurb).toMatch(/another task fails/);
    expect(rules?.blurb).toMatch(/file lands in a folder/);
    expect(rules?.blurb).toMatch(/Schedules/);
  });
});
