/**
 * Leap 05-a (R1.3): one sentence makes one rule, and a folder or a download
 * can start it — the page half.
 *
 *  - the chat card of a rule says When, Do, Where it works and Limits (the
 *    daemon's words, whitelisted by the decoder) and keeps its Undo;
 *  - the Rules row says the same four lines, where it reports, and — when the
 *    rule paused itself — Resume and Keep it paused, each one press;
 *  - the bell's row for a rule that paused itself opens that rule;
 *  - "Always for this rule" is the button's words on a rule's run;
 *  - a watcher that starts a rule says so on Watchers and is changed on Rules;
 *  - "automation" finds Rules in search.
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
    const key = Object.keys(api.responses).find((k) => k === path || (k.endsWith("*") && path.startsWith(k.slice(0, -1))));
    const r = key === undefined ? undefined : api.responses[key];
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

import { ConfigCards } from "@/components/chat/ConfigCards";
import { decodeConfigCards } from "@/lib/configCards";
import { ALWAYS_RULE, alwaysLabel, alwaysPage, alwaysScopeOf } from "@/lib/approvalWords";
import { KEEP_PAUSED, RESUME, startsRuleWords } from "@/lib/reflexRules";
import { EVENT_WORDS } from "@/lib/eventWords";
import { NAV_ENTRIES } from "@/lib/nav";
import ReflexPage from "@/app/reflex/page";
import SentinelsPage from "@/app/sentinels/page";
import { BELL_ACTIVITY_TYPES, toActivity } from "@/components/NotificationBell";

afterEach(() => {
  cleanup();
  api.posts = [];
  api.gets = [];
  api.responses = {};
});

const LINES = {
  when: "When a new file lands in Intake (matching *.pdf). It looks every 5 minutes; files already there are left alone.",
  does: "Jarvis does: Read {file} and file it in a folder named after its sender.",
  where: "In C:\\Users\\me\\Intake.",
  limits: "Starts at most 12 times an hour, then pauses and tells you. Anything risky asks you first.",
};

describe("the rule's card in chat", () => {
  it("says When, Do, Where it works and Limits, and keeps its Undo", () => {
    const cards = decodeConfigCards([
      {
        kind: "change",
        title: "Rule created",
        key: "reflex.reflex_1",
        label: "Rule “Sort intake”",
        old: null,
        new: "When a new file lands in Intake (matching *.pdf) → an agent does the task · Reports here",
        change_id: "rec_1",
        rule: { ...LINES, extra: "<script>" },
      },
    ]);
    expect(cards).toHaveLength(1);
    const card = cards[0];
    expect(card.kind === "change" && card.rule).toEqual(LINES);
    render(<ConfigCards cards={cards} onSettle={() => undefined} />);
    const lines = screen.getByTestId("config-card-rule");
    const labels = within(lines).getAllByRole("term").map((n) => n.textContent);
    expect(labels).toEqual(["When", "Do", "Where it works", "Limits"]);
    expect(within(lines).getAllByRole("definition").map((n) => n.textContent)).toEqual([
      LINES.when, LINES.does, LINES.where, LINES.limits,
    ]);
    expect(screen.getByRole("button", { name: "Undo" })).toBeTruthy();
  });

  it("a card with no rule lines draws none (any other record's card)", () => {
    const cards = decodeConfigCards([
      { kind: "change", title: "Schedule created", key: "schedule.x", label: "Schedule", old: null, new: "created", change_id: "rec_2" },
    ]);
    render(<ConfigCards cards={cards} onSettle={() => undefined} />);
    expect(screen.queryByTestId("config-card-rule")).toBeNull();
  });
});

function ruleRow(over: Record<string, unknown> = {}) {
  return {
    id: "reflex_1",
    name: "Sort intake",
    source: "file",
    match: "",
    action: "session",
    target: "",
    task_template: "Read {file} and file it in a folder named after its sender",
    project_id: null,
    enabled: true,
    created_at: "2026-11-20T10:00:00Z",
    last_fired_at: null,
    fire_count: 3,
    start: { path: "C:\\Users\\me\\Intake", glob: "*.pdf" },
    workspace_root: "C:\\Users\\me\\Intake",
    max_fires_per_hour: 12,
    paused_at: null,
    paused_reason: null,
    when_words: LINES.when,
    does_words: LINES.does,
    where_words: LINES.where,
    limits_words: LINES.limits,
    reports_to: { thread_id: "chat_1", title: "Intake" },
    watcher: { name: "Rule: Sort intake", last_error: null },
    ...over,
  };
}

function mountRules(rules: unknown[]) {
  api.responses["/reflex/rules"] = { rules };
  api.responses["/workflows"] = { workflows: [] };
  api.responses["/agents/remote"] = { agents: [] };
  api.responses["/webhooks"] = { webhooks: [] };
  api.responses["/projects"] = { projects: [] };
  api.responses["/triggers"] = { triggers: {} };
  api.responses["/rehearsals?kind=rule"] = { rehearsals: [] };
  render(<ReflexPage />);
}

describe("the rule's row on Rules", () => {
  it("is the same four lines, says where it reports, and has no pause card while it runs", async () => {
    mountRules([ruleRow()]);
    const lines = await screen.findByTestId("reflex-lines-reflex_1");
    expect(within(lines).getAllByRole("definition").map((n) => n.textContent)).toEqual([
      LINES.when, LINES.does, LINES.where, LINES.limits,
    ]);
    expect(screen.getByTestId("reports-to-rule").textContent).toContain("Intake");
    expect(screen.queryByTestId("reflex-paused-reflex_1")).toBeNull();
  });

  it("a rule that paused itself offers Resume and Keep it paused, one press each", async () => {
    mountRules([ruleRow({ paused_at: "2026-11-20T12:20:00Z", paused_reason: "Paused: it started 12 times in an hour." })]);
    const card = await screen.findByTestId("reflex-paused-reflex_1");
    expect(card.textContent).toContain("Paused: it started 12 times in an hour.");
    expect(card.textContent).toContain("New files wait and go once you resume it.");
    fireEvent.click(within(card).getByRole("button", { name: RESUME }));
    await waitFor(() => expect(api.posts.map((p) => p.path)).toEqual(["/reflex/rules/reflex_1/resume"]));
    fireEvent.click(within(card).getByRole("button", { name: KEEP_PAUSED }));
    await waitFor(() =>
      expect(api.posts.map((p) => p.path)).toEqual(["/reflex/rules/reflex_1/resume", "/reflex/rules/reflex_1/keep-paused"]),
    );
  });

  it("the add form offers a folder and a download, and sends the folder", async () => {
    mountRules([]);
    fireEvent.click((await screen.findAllByText("Add rule"))[0]);
    fireEvent.click(screen.getByTestId("reflex-shape-other"));
    const source = screen.getByLabelText("Signal source") as HTMLSelectElement;
    expect(Array.from(source.options).map((o) => o.value)).toEqual(
      expect.arrayContaining(["file", "download"]),
    );
    fireEvent.change(source, { target: { value: "file" } });
    fireEvent.change(screen.getByTestId("reflex-folder"), { target: { value: "C:\\Users\\me\\Intake" } });
    fireEvent.change(screen.getByLabelText("Only files like (optional)"), { target: { value: "*.pdf" } });
    fireEvent.submit(source.closest("form")!);
    await waitFor(() => expect(api.posts).toHaveLength(1));
    expect(api.posts[0].path).toBe("/reflex/rules");
    expect(api.posts[0].body).toMatchObject({
      source: "file", action: "session", path: "C:\\Users\\me\\Intake", glob: "*.pdf", match: "",
    });
  });
});

describe("the notice, the Always button, the watcher and search", () => {
  it("the bell's row for a rule that paused itself opens that rule", () => {
    expect(BELL_ACTIVITY_TYPES).toContain("reflex.paused");
    const item = toActivity({
      id: "e1",
      ts: "2026-11-20T12:20:00Z",
      type: "reflex.paused",
      session_id: null,
      payload: { rule: "Sort intake", rule_id: "reflex_1", reason: "Paused: it started 12 times in an hour.", limit: 12 },
    } as never);
    expect(item?.title).toBe("Your rule Sort intake paused itself");
    expect(item?.href).toBe("/reflex?rule=reflex_1");
    expect(item?.body).toContain("Resume it, or keep it paused.");
    expect(EVENT_WORDS["reflex.paused"]).toBe("A rule paused itself");
  });

  it("a rule's run says Always for this rule", () => {
    expect(alwaysScopeOf("rule")).toBe("rule");
    expect(alwaysLabel("rule")).toBe(ALWAYS_RULE);
    expect(ALWAYS_RULE).toBe("Always for this rule");
    expect(alwaysPage("rule")).toBe("Autonomy");
    expect(alwaysScopeOf("anything else")).toBeNull();
  });

  it("a watcher that starts a rule says so on Watchers and is changed on Rules", async () => {
    api.responses["/sentinels"] = {
      enabled: false,
      sentinels: [
        {
          id: "sentinel_1", name: "Rule: Sort intake", kind: "file", config: { path: "C:\\Users\\me\\Intake" },
          task: "", agent_type: "builder", risk: "low", enabled: true, last_checked_at: null,
          created_at: "2026-11-20T10:00:00Z", mode: "rule", rule_id: "reflex_1", rule_name: "Sort intake",
        },
      ],
    };
    api.responses["/agents"] = { agents: [] };
    api.responses["/agents/definitions*"] = { agents: [] };
    render(<SentinelsPage />);
    const link = await screen.findByTestId("sentinel-starts-rule-sentinel_1");
    expect(link.textContent).toBe(startsRuleWords("Sort intake"));
    expect(link.getAttribute("href")).toBe("/reflex?rule=reflex_1");
    expect(screen.queryByTestId("sentinel-edit-sentinel_1")).toBeNull();
  });

  it("automation finds Rules in search", () => {
    const rules = NAV_ENTRIES.find((i) => i.href === "/reflex");
    expect(rules?.aliases).toContain("automation");
    expect(rules?.blurb).toMatch(/file lands in a folder/);
  });
});
