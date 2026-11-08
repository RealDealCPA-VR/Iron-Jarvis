/**
 * Wave D (readiness plan 0h.5), builder D2's dashboard half — items 34, 35, 36, 42.
 *
 *  34  Rules and watchers are edited in place (Edit → the same form, PATCH);
 *      the Rules form opens on "a message arrives → an agent does a task";
 *      a rule whose source cannot hear says so on its row (from /triggers).
 *  35  "Every minute" is the last preset and warns; an unnamed task schedule
 *      takes its name from the task's first words; the editor reads a stored
 *      cron back into choices (how often, time, days, agent) and a cron it
 *      cannot read is "Custom schedule (advanced)" with the string in a fold.
 *  36  Tonight takes its folder with the shared picker; a folder that is not a
 *      repository offers "Make this folder a repository" (POST /build/git/init).
 *  42  The step editor says why its JSON box is not JSON (line, column), and a
 *      save refused for one step is said in that step's panel.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    calls: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    patches: [] as { path: string; body: unknown }[],
    responses: {} as Record<string, unknown>,
    postAnswers: {} as Record<string, unknown>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    api.calls.push(path);
    const key = Object.keys(api.responses).find((k) => k === path || (k.endsWith("*") && path.startsWith(k.slice(0, -1))));
    const r = key === undefined ? undefined : api.responses[key];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    return Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    return Promise.resolve(api.postAnswers[path] ?? { ok: true });
  },
  patch: (path: string, body?: unknown) => {
    api.patches.push({ path, body });
    return Promise.resolve({});
  },
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

import { buildCron, parseCron, planWords, CUSTOM_SCHEDULE_WORDS } from "@/lib/scheduleCron";
import { EVERY_MINUTE_WARNING, REPEAT_PRESETS, nameFromTask, uniqueScheduleName } from "@/lib/schedules";
import { SLACK_OFF_WORDS, sourceOffWords } from "@/lib/reflexRules";
import { jsonProblem, stepProblemOf } from "@/lib/workflowStepProblems";
import SchedulesPage from "@/app/schedules/page";
import ReflexPage from "@/app/reflex/page";
import SentinelsPage from "@/app/sentinels/page";
import { NodeInspector } from "@/components/workflow/NodeInspector";
import { TonightList, MAKE_REPO_WORDS, REPO_CHECK_MS } from "@/components/harness/TonightList";
import type { Schedule } from "@/lib/types";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  api.calls = [];
  api.posts = [];
  api.patches = [];
  api.responses = {};
  api.postAnswers = {};
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
});

/* ------------------------------------------------------------- 35: pure */

describe("item 35 — the cron reader and the presets", () => {
  it("reads every repeat shape back and writes it again unchanged", () => {
    for (const cron of ["* * * * *", "*/15 * * * *", "5 * * * *", "0 9 * * *", "0 8 * * 1-5", "30 16 * * 1,3,5", "0 0 1 * *"]) {
      const plan = parseCron(cron);
      expect(plan, cron).not.toBeNull();
      expect(buildCron(plan!), cron).toBe(cron);
    }
    expect(planWords(parseCron("0 8 * * 1-5")!)).toBe("Weekdays at 8:00 AM");
    expect(parseCron("0 9 * * 0-6")!.kind).toBe("daily");
  });

  it("never guesses: a month field, a list of hours or a name is Custom", () => {
    for (const cron of ["0 9 1 1 *", "0 9,17 * * *", "0 9 * * MON", "0 9 1-7 * *", "bad", ""]) {
      expect(parseCron(cron), cron).toBeNull();
    }
    expect(CUSTOM_SCHEDULE_WORDS).toBe("Custom schedule (advanced)");
  });

  it("Every minute is the LAST preset", () => {
    expect(REPEAT_PRESETS[REPEAT_PRESETS.length - 1]).toEqual({ label: "Every minute", cron: "* * * * *" });
    expect(REPEAT_PRESETS.slice(0, -1).some((p) => p.cron === "* * * * *")).toBe(false);
  });

  it("a name from the task's first words", () => {
    expect(nameFromTask("Write my morning briefing: summarize yesterday")).toBe("write-my-morning-briefing-summarize");
    expect(nameFromTask("   ")).toBe("");
  });
});

/* ------------------------------------------------------- 35: the page */

function scheduleRow(over: Partial<Schedule> = {}): Schedule {
  return {
    name: "morning-briefing",
    cron: "0 8 * * 1-5",
    kind: "task",
    enabled: true,
    next_run: null,
    last_run: null,
    trigger_type: "cron",
    payload_json: JSON.stringify({ task: "Write my morning briefing." }),
    skills: [],
    workspace_root: "",
    context_from: "",
    script: null,
    skip_memory: false,
    ...over,
  } as Schedule;
}

function mountSchedules(rows: Schedule[] = []) {
  api.responses["/schedules"] = { schedules: rows };
  api.responses["/workflows"] = { workflows: [] };
  api.responses["/agents"] = { builtin: ["builder", "reviewer"], dynamic: [] };
  api.responses["/projects"] = { projects: [] };
  api.responses["/comm/channels"] = { channels: [] };
  api.responses["/autonomy"] = { enabled: false, kill_switch: false };
  render(<SchedulesPage />);
}

describe("item 35 — the Schedules page", () => {
  it("picking Every minute shows the warning; another preset does not", async () => {
    mountSchedules();
    const repeat = (await screen.findByLabelText("Repeat")) as HTMLSelectElement;
    expect(screen.queryByTestId("schedule-every-minute-warning")).toBeNull();
    fireEvent.change(repeat, { target: { value: "* * * * *" } });
    expect(screen.getByTestId("schedule-every-minute-warning").textContent).toContain(EVERY_MINUTE_WARNING);
    fireEvent.change(repeat, { target: { value: "0 * * * *" } });
    expect(screen.queryByTestId("schedule-every-minute-warning")).toBeNull();
  });

  it("an unnamed task schedule is named from its task, said before Add", async () => {
    mountSchedules();
    const task = await screen.findByLabelText("Task text");
    fireEvent.change(task, { target: { value: "Tidy my Downloads folder every evening" } });
    expect(screen.getByTestId("schedule-name-hint").textContent).toContain("tidy-my-downloads-folder-every");
    fireEvent.submit(task.closest("form")!);
    await waitFor(() => expect(api.posts.find((p) => p.path === "/schedules")).toBeTruthy());
    expect((api.posts.find((p) => p.path === "/schedules")!.body as { name: string }).name).toBe(
      "tidy-my-downloads-folder-every",
    );
  });

  // AN review: the daemon refuses a name in use ("already exists"), so a second
  // schedule made from the same task takes the next free name.
  it("a filled-in name never takes one a schedule already has", async () => {
    expect(uniqueScheduleName("a", [])).toBe("a");
    expect(uniqueScheduleName("a", ["a", "a-2"])).toBe("a-3");
    expect(uniqueScheduleName("x".repeat(40), ["x".repeat(40)]).length).toBeLessThanOrEqual(40);
    mountSchedules([scheduleRow({ name: "tidy-my-downloads-folder-every" })]);
    const task = await screen.findByLabelText("Task text");
    await waitFor(() => expect(screen.getAllByText(/tidy-my-downloads-folder-every/).length).toBeGreaterThan(0));
    fireEvent.change(task, { target: { value: "Tidy my Downloads folder every evening" } });
    expect(screen.getByTestId("schedule-name-hint").textContent).toContain("tidy-my-downloads-folder-every-2");
    fireEvent.submit(task.closest("form")!);
    await waitFor(() => expect(api.posts.find((p) => p.path === "/schedules")).toBeTruthy());
    expect((api.posts.find((p) => p.path === "/schedules")!.body as { name: string }).name).toBe(
      "tidy-my-downloads-folder-every-2",
    );
  });

  it("the editor reads the cron into days and a time, and a day change PATCHes the new cron", async () => {
    mountSchedules([scheduleRow()]);
    fireEvent.click(await screen.findByTestId("schedule-edit-morning-briefing"));
    const editor = await screen.findByTestId("schedule-editor");
    expect((within(editor).getByLabelText("Repeat") as HTMLSelectElement).value).toBe("weekly");
    expect(within(editor).queryByLabelText("Cron expression")).toBeNull();
    expect((within(editor).getByLabelText("Time") as HTMLInputElement).value).toBe("08:00");
    expect((within(editor).getByLabelText("Mon") as HTMLInputElement).checked).toBe(true);
    expect((within(editor).getByLabelText("Sat") as HTMLInputElement).checked).toBe(false);
    expect(within(editor).getByTestId("schedule-editor-plan").textContent).toBe("Weekdays at 8:00 AM");
    fireEvent.click(within(editor).getByLabelText("Fri"));
    fireEvent.change(within(editor).getByLabelText("Time"), { target: { value: "07:30" } });
    fireEvent.click(within(editor).getByTestId("schedule-editor-save"));
    await waitFor(() => expect(api.patches).toHaveLength(1));
    expect(api.patches[0].body).toEqual({ cron: "30 7 * * 1-4" });
  });

  it("opening and saving an untouched schedule sends nothing", async () => {
    mountSchedules([scheduleRow({ cron: "0 9 * * 1,2,3,4,5" })]);
    fireEvent.click(await screen.findByTestId("schedule-edit-morning-briefing"));
    const editor = await screen.findByTestId("schedule-editor");
    fireEvent.click(within(editor).getByTestId("schedule-editor-save"));
    await waitFor(() => expect(screen.queryByTestId("schedule-editor")).toBeNull());
    expect(api.patches).toHaveLength(0);
  });

  it("a cron it cannot read is Custom schedule (advanced), the string in a fold", async () => {
    mountSchedules([scheduleRow({ name: "odd", cron: "0 9,17 * * *" })]);
    fireEvent.click(await screen.findByTestId("schedule-edit-odd"));
    const editor = await screen.findByTestId("schedule-editor");
    const repeat = within(editor).getByLabelText("Repeat") as HTMLSelectElement;
    expect(repeat.value).toBe("custom");
    expect(repeat.selectedOptions[0].textContent).toBe(CUSTOM_SCHEDULE_WORDS);
    const fold = within(editor).getByTestId("schedule-editor-custom");
    expect(fold.tagName).toBe("DETAILS");
    expect(within(fold).getByLabelText("Cron expression")).toHaveValue("0 9,17 * * *");
  });

  it("the editor keeps who runs it: a new agent rides payload_set, builder unsets it", async () => {
    mountSchedules([scheduleRow()]);
    fireEvent.click(await screen.findByTestId("schedule-edit-morning-briefing"));
    const editor = await screen.findByTestId("schedule-editor");
    fireEvent.change(within(editor).getByTestId("schedule-editor-agent"), { target: { value: "reviewer" } });
    fireEvent.click(within(editor).getByTestId("schedule-editor-save"));
    await waitFor(() => expect(api.patches).toHaveLength(1));
    expect(api.patches[0].body).toEqual({ payload_set: { agent_type: "reviewer" } });
  });

  it("the Folder box has the shared picker", async () => {
    const pickFolder = vi.fn(async () => ({ path: "C:\\work\\reports" }));
    (window as unknown as { ironjarvis?: unknown }).ironjarvis = { dialog: { pickFolder } };
    mountSchedules([scheduleRow()]);
    fireEvent.click(await screen.findByTestId("schedule-edit-morning-briefing"));
    const editor = await screen.findByTestId("schedule-editor");
    fireEvent.click(within(editor).getByTestId("schedule-folder-choose"));
    await waitFor(() => expect((within(editor).getByLabelText("Folder") as HTMLInputElement).value).toBe("C:\\work\\reports"));
    expect(pickFolder).toHaveBeenCalledTimes(1);
  });
});

/* ------------------------------------------------------------- 34: rules */

function ruleRow(over: Record<string, unknown> = {}) {
  return {
    id: "reflex_1",
    name: "slack-deploys",
    source: "slack",
    match: "deploy",
    action: "session",
    target: "",
    task_template: "Check {text}",
    project_id: null,
    enabled: true,
    created_at: "2026-08-22T10:00:00Z",
    last_fired_at: null,
    fire_count: 0,
    ...over,
  };
}

function mountRules(rules: unknown[] = [], triggers: unknown = { triggers: {} }) {
  api.responses["/reflex/rules"] = { rules };
  api.responses["/workflows"] = { workflows: [{ name: "nightly" }] };
  api.responses["/agents/remote"] = { agents: [] };
  api.responses["/webhooks"] = { webhooks: [] };
  api.responses["/projects"] = { projects: [] };
  if (triggers !== undefined) api.responses["/triggers"] = triggers;
  render(<ReflexPage />);
}

describe("item 34 — Rules", () => {
  it("the form opens on a message arriving and an agent doing a task", async () => {
    mountRules();
    fireEvent.click(await screen.findByText("Add reflex"));
    const source = (await screen.findByLabelText("Signal source")) as HTMLSelectElement;
    expect(source.value).toBe("comm");
    expect(Array.from(source.options).map((o) => o.value)).toEqual(["comm", "email", "slack"]);
    expect(screen.queryByLabelText("Action")).toBeNull();
    expect(screen.getByTestId("reflex-everyday-action")).toBeTruthy();
    fireEvent.submit(source.closest("form")!);
    await waitFor(() => expect(api.posts).toHaveLength(1));
    expect(api.posts[0].body).toMatchObject({ source: "comm", action: "session" });
    // The other shapes are one choice away.
    fireEvent.click(screen.getByTestId("reflex-shape-other"));
    expect(Array.from((screen.getByLabelText("Signal source") as HTMLSelectElement).options).map((o) => o.value)).toContain(
      "webhook",
    );
    expect(screen.getByLabelText("Action")).toBeTruthy();
  });

  it("Edit opens the form prefilled and saves with PATCH", async () => {
    mountRules([ruleRow()]);
    fireEvent.click(await screen.findByTestId("reflex-edit-reflex_1"));
    expect(screen.getByText("Edit reflex")).toBeTruthy();
    expect((screen.getByLabelText("Signal source") as HTMLSelectElement).value).toBe("slack");
    const keyword = screen.getByDisplayValue("deploy");
    fireEvent.change(keyword, { target: { value: "release" } });
    fireEvent.submit(keyword.closest("form")!);
    await waitFor(() => expect(api.patches).toHaveLength(1));
    expect(api.patches[0].path).toBe("/reflex/rules/reflex_1");
    expect(api.patches[0].body).toMatchObject({ match: "release", source: "slack", action: "session", task_template: "Check {text}" });
    expect(api.posts).toHaveLength(0);
  });

  it("a Slack rule says Slack isn't listening when /triggers says so", async () => {
    mountRules([ruleRow()], { triggers: { slack: { enabled: true, credentialed: false } } });
    expect((await screen.findByTestId("reflex-source-off-reflex_1")).textContent).toBe(SLACK_OFF_WORDS);
  });

  it("a listening Slack, or no /triggers answer yet, says nothing", async () => {
    expect(sourceOffWords("slack", null)).toBe("");
    expect(sourceOffWords("slack", { triggers: { slack: { enabled: true, credentialed: true } } })).toBe("");
    expect(sourceOffWords("comm", { triggers: {} })).toBe("");
    mountRules([ruleRow()], { triggers: { slack: { enabled: true, credentialed: true } } });
    await screen.findByTestId("reflex-edit-reflex_1");
    await act(async () => {});
    expect(screen.queryByTestId("reflex-source-off-reflex_1")).toBeNull();
  });
});

/* --------------------------------------------------------- 34: watchers */

describe("item 34 — Sentinels", () => {
  it("Edit opens the form prefilled (the name is the key) and saves with PATCH", async () => {
    api.responses["/sentinels"] = {
      enabled: true,
      sentinels: [
        {
          id: "sentinel_1", name: "downloads", kind: "file", config: { path: "C:\\Users\\me\\Downloads", glob: "*.pdf" },
          task: "File it", agent_type: "builder", risk: "low", enabled: true, last_checked_at: null, created_at: "2026-01-01T00:00:00Z",
        },
      ],
    };
    api.responses["/agents"] = { builtin: ["builder"], dynamic: [] };
    render(<SentinelsPage />);
    fireEvent.click(await screen.findByTestId("sentinel-edit-sentinel_1"));
    const name = screen.getByDisplayValue("downloads") as HTMLInputElement;
    expect(name.readOnly).toBe(true);
    fireEvent.change(screen.getByDisplayValue("*.pdf"), { target: { value: "" } });
    fireEvent.submit(name.closest("form")!);
    await waitFor(() => expect(api.patches).toHaveLength(1));
    expect(api.patches[0]).toEqual({
      path: "/sentinels/downloads",
      body: { path: "C:\\Users\\me\\Downloads", glob: "", task: "File it", agent_type: "builder", risk: "low" },
    });
    expect(api.posts).toHaveLength(0);
  });
});

/* -------------------------------------------------------------- 36: tonight */

describe("item 36 — Tonight's folder", () => {
  function mountTonight() {
    api.responses["/build/night"] = {
      tasks: [], window: { start: "23:00", end: "07:00", open: false, words: "Starts at 11:00 PM tonight." },
      limits: { at_once: 2, task_minutes: 90, card_wait_minutes: 15 }, emergency_stop: false,
    };
    api.responses["/models"] = { models: [] };
    render(<TonightList folders={[]} onOpenSession={() => {}} />);
  }

  it("picks the folder with the shared picker; a folder that is not a repository is made one in one press", async () => {
    const pickFolder = vi.fn(async () => ({ path: "C:\\work\\shop" }));
    (window as unknown as { ironjarvis?: unknown }).ironjarvis = { dialog: { pickFolder } };
    api.responses["/build/git/folder*"] = { git: true, repo: false, can_branch: false, note: "" };
    api.postAnswers["/build/git/init"] = { made: true, repo: true, can_branch: true, left_out: [{ path: ".env", why: "secret" }] };
    mountTonight();
    fireEvent.click(await screen.findByTestId("tonight-add"));
    fireEvent.click(screen.getByTestId("tonight-folder-choose"));
    await waitFor(() => expect((screen.getByTestId("tonight-folder") as HTMLInputElement).value).toBe("C:\\work\\shop"));
    const press = await screen.findByTestId("tonight-make-repo", {}, { timeout: REPO_CHECK_MS + 3000 });
    expect(press.textContent).toContain(MAKE_REPO_WORDS);
    fireEvent.click(press);
    await waitFor(() => expect(api.posts.find((p) => p.path === "/build/git/init")).toBeTruthy());
    expect(api.posts.find((p) => p.path === "/build/git/init")!.body).toEqual({ path: "C:\\work\\shop" });
    expect((await screen.findByTestId("tonight-repo-made")).textContent).toContain("Left out of it: .env");
    expect(screen.queryByTestId("tonight-make-repo")).toBeNull();
  });

  it("a folder already in a repository offers nothing", async () => {
    api.responses["/build/git/folder*"] = { git: true, repo: true, can_branch: true, note: "" };
    mountTonight();
    fireEvent.click(await screen.findByTestId("tonight-add"));
    fireEvent.change(screen.getByTestId("tonight-folder"), { target: { value: "C:\\work\\api" } });
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("/build/git/folder"))).toBe(true), {
      timeout: REPO_CHECK_MS + 3000,
    });
    await act(async () => {});
    expect(screen.queryByTestId("tonight-make-repo")).toBeNull();
  });
});

/* -------------------------------------------------------- 42: step editor */

describe("item 42 — the step editor", () => {
  it("says why the arguments are not JSON, with the line and column", () => {
    const bad = jsonProblem('{\n  "path": "."\n  "x": 1\n}');
    expect(bad.ok).toBe(false);
    expect(bad.words).toMatch(/at line 3, column \d+/);
    expect(jsonProblem("[1]").ok).toBe(false);
    expect(jsonProblem("").value).toEqual({});
  });

  it("the Arguments box shows the problem and keeps the old value; a fix clears it", () => {
    const onChange = vi.fn();
    api.responses["/agents"] = { builtin: ["builder"], dynamic: [] };
    render(
      <NodeInspector
        data={{ name: "List", agent: "builder", task: "", kind: "tool", tool: "list_folder", args: { path: "." }, index: 1 }}
        onChange={onChange}
        onDelete={() => {}}
        onClose={() => {}}
      />,
    );
    const box = screen.getByDisplayValue(/"path": "\."/) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: '{"path": ".",}' } });
    fireEvent.blur(box);
    expect(screen.getByTestId("step-args-problem").textContent).toMatch(/isn't valid JSON at line 1, column \d+/);
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.change(box, { target: { value: '{"path": "src"}' } });
    fireEvent.blur(box);
    expect(screen.queryByTestId("step-args-problem")).toBeNull();
    expect(onChange).toHaveBeenCalledWith({ args: { path: "src" } });
  });

  it("a save refused for a step is read back to that step and said in its panel", () => {
    expect(stepProblemOf("steps[2] (List files): no tool named 'list_foldr' — pick one")).toEqual({
      index: 2,
      words: "no tool named 'list_foldr' — pick one",
    });
    expect(stepProblemOf("steps[0].kind: 'tools' is not one of agent|tool")).toEqual({
      index: 0,
      words: "'tools' is not one of agent|tool",
    });
    expect(stepProblemOf("Name the workflow before saving.")).toBeNull();
    api.responses["/agents"] = { builtin: ["builder"], dynamic: [] };
    render(
      <NodeInspector
        data={{ name: "List", agent: "builder", task: "", kind: "tool", tool: "list_foldr", args: {}, index: 1 }}
        onChange={() => {}}
        onDelete={() => {}}
        onClose={() => {}}
        problem="no tool named 'list_foldr'"
      />,
    );
    expect(screen.getByTestId("step-problem").textContent).toBe("no tool named 'list_foldr'");
  });
});
