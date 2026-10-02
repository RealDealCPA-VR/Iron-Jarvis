/**
 * v1.299.0 — schedule knobs: skills, folder, chain, pre-run script, skip-memory.
 *
 * Pinned here, each against the WIRE (routes/schedules.py + scheduling/):
 *  - the knobs render on the task form only when the daemon's rows carry
 *    them (or there are no rows to read); rows WITHOUT the keys = today's
 *    form, no Edit action;
 *  - create sends `skills`, `workspace_root`, `context_from`, `script
 *    {command, timeout_s, cwd}` and `skip_memory` under those exact names;
 *  - ANTI-VACUITY: with every knob untouched the POST body is byte-for-byte
 *    today's — {name, kind, payload: {task}, cron} — nothing is sent "empty";
 *  - Edit opens on a row and PATCHes /schedules/{name} with `payload_set` for
 *    knobs set and `payload_unset` for knobs cleared, only what changed;
 *    nothing changed = no request; a 404 names the older daemon.
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
    calls: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    patches: [] as { path: string; body: unknown }[],
    responses: {} as Record<string, unknown>,
    patchError: null as InstanceType<typeof FakeApiError> | null,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    api.calls.push(path);
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    return Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    return Promise.resolve({});
  },
  patch: (path: string, body?: unknown) => {
    api.patches.push({ path, body });
    if (api.patchError) return Promise.reject(api.patchError);
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
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

import SchedulesPage from "@/app/schedules/page";
import type { Schedule } from "@/lib/types";

afterEach(() => {
  cleanup();
  api.calls = [];
  api.posts = [];
  api.patches = [];
  api.responses = {};
  api.patchError = null;
});

/* ---------------------------------------------------------------- fixtures */

/** A row from a daemon that knows the knobs: every key present, most empty. */
function knobbed(over: Partial<Schedule> = {}): Schedule {
  return {
    name: "nightly",
    cron: "0 2 * * *",
    kind: "task",
    enabled: true,
    next_run: null,
    last_run: null,
    trigger_type: "cron",
    payload_json: JSON.stringify({ task: "Nightly rounds." }),
    skills: [],
    workspace_root: "",
    context_from: "",
    script: null,
    skip_memory: false,
    ...over,
  } as Schedule;
}

/** A row from an OLDER daemon: no knob key at all. */
function older(over: Partial<Schedule> = {}): Schedule {
  return {
    name: "old-row",
    cron: "0 9 * * *",
    kind: "task",
    enabled: true,
    next_run: null,
    last_run: null,
    trigger_type: "cron",
    payload_json: JSON.stringify({ task: "Morning rounds." }),
    ...over,
  } as Schedule;
}

function mountPage(schedules: Schedule[] = []) {
  api.responses["/schedules"] = { schedules };
  api.responses["/workflows"] = { workflows: [] };
  api.responses["/projects"] = { projects: [] };
  api.responses["/comm/channels"] = { channels: [] };
  api.responses["/agents"] = { builtin: ["builder", "researcher"], dynamic: [] };
  return render(<SchedulesPage />);
}

async function fillBasics(name: string, task: string) {
  fireEvent.change(await screen.findByPlaceholderText("morning-briefing"), { target: { value: name } });
  fireEvent.change(screen.getByLabelText("Task text"), { target: { value: task } });
}

function set(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

/* ------------------------------------------------------------------ create */

describe("schedule knobs on create (v1.299.0)", () => {
  it("hides the knobs and the Edit action when the daemon's rows lack the keys", async () => {
    mountPage([older()]);
    await screen.findByText("old-row");
    expect(screen.queryByTestId("schedule-knobs")).toBeNull();
    expect(screen.queryByTestId("schedule-edit-old-row")).toBeNull();
  });

  it("sends every knob under the wire's names", async () => {
    mountPage([knobbed()]);
    await screen.findByTestId("schedule-edit-nightly"); // the rows (and their knob keys) landed
    await fillBasics("weekly-report", "Write the weekly report.");
    expect(screen.getByTestId("schedule-knobs")).toBeInTheDocument();

    const skills = within(screen.getByTestId("schedule-skills")).getByLabelText(/Add a skill/);
    fireEvent.change(skills, { target: { value: "research" } });
    fireEvent.keyDown(skills, { key: "Enter" });
    fireEvent.change(skills, { target: { value: "research" } }); // a duplicate adds nothing
    fireEvent.keyDown(skills, { key: "Enter" });
    set("Folder", "C:\\work\\reports");
    set("Use the result of", "nightly");
    set("Pre-run command", "git pull");
    set("Pre-run timeout (seconds)", "30");
    set("Pre-run folder", "home"); // the daemon accepts only workspace|home
    fireEvent.click(screen.getByLabelText("Skip memory"));

    fireEvent.click(screen.getByRole("button", { name: /add schedule/i }));
    await waitFor(() => expect(api.posts).toHaveLength(1));
    expect(api.posts[0].path).toBe("/schedules");
    expect(api.posts[0].body).toEqual({
      name: "weekly-report",
      kind: "task",
      cron: "0 9 * * *",
      payload: {
        task: "Write the weekly report.",
        skills: ["research"],
        workspace_root: "C:\\work\\reports",
        context_from: "nightly",
        script: { command: "git pull", timeout_s: 30, cwd: "home" },
        skip_memory: true,
      },
    });
    // The success note is the END of the handler — the form has reset.
    await screen.findByText(/Schedule "weekly-report" added/);
    expect(screen.getByLabelText("Skip memory")).not.toBeChecked();
    expect((screen.getByLabelText("Folder") as HTMLInputElement).value).toBe("");
  });

  it("ANTI-VACUITY: untouched knobs post exactly today's body", async () => {
    mountPage([]);
    await fillBasics("morning", "Say good morning.");
    expect(screen.getByTestId("schedule-knobs")).toBeInTheDocument(); // no rows: knobs shown
    fireEvent.click(screen.getByRole("button", { name: /add schedule/i }));
    await waitFor(() => expect(api.posts).toHaveLength(1));
    expect(api.posts[0].body).toEqual({
      name: "morning",
      kind: "task",
      cron: "0 9 * * *",
      payload: { task: "Say good morning." },
    });
    expect(JSON.stringify(api.posts[0].body)).not.toMatch(/skills|workspace_root|context_from|script|skip_memory/);
  });

  it("the chain picker never offers the schedule its own name", async () => {
    mountPage([knobbed(), knobbed({ name: "weekly" })]);
    await screen.findByTestId("schedule-edit-weekly");
    fireEvent.change(await screen.findByPlaceholderText("morning-briefing"), { target: { value: "nightly" } });
    const options = within(screen.getByLabelText("Use the result of"))
      .getAllByRole("option")
      .map((o) => (o as HTMLOptionElement).value);
    expect(options).toEqual(["", "weekly"]);
  });
});

/* -------------------------------------------------------------------- edit */

describe("Edit a schedule (v1.299.0)", () => {
  it("PATCHes payload_set for knobs set and payload_unset for knobs cleared — only what changed", async () => {
    mountPage([
      knobbed({
        skills: ["ops"],
        workspace_root: "C:\\old",
        skip_memory: true,
        script: { command: "git pull", timeout_s: 20 },
      }),
      knobbed({ name: "weekly" }),
    ]);
    fireEvent.click(await screen.findByTestId("schedule-edit-nightly"));
    const editor = await screen.findByTestId("schedule-editor");
    // Prefilled from the row.
    expect((within(editor).getByLabelText("Folder") as HTMLInputElement).value).toBe("C:\\old");
    expect(within(editor).getByLabelText("Skip memory")).toBeChecked();
    expect((within(editor).getByLabelText("Pre-run command") as HTMLInputElement).value).toBe("git pull");

    fireEvent.click(within(editor).getByLabelText("Remove skill ops for nightly")); // → unset
    fireEvent.change(within(editor).getByLabelText("Folder"), { target: { value: "C:\\new" } }); // → set
    fireEvent.click(within(editor).getByLabelText("Skip memory")); // → unset
    fireEvent.change(within(editor).getByLabelText("Use the result of"), { target: { value: "weekly" } }); // → set
    // Cron, task text and the script are left alone: they must not be sent.
    fireEvent.click(within(editor).getByTestId("schedule-editor-save"));

    await waitFor(() => expect(api.patches).toHaveLength(1));
    expect(api.patches[0]).toEqual({
      path: "/schedules/nightly",
      body: {
        payload_set: { workspace_root: "C:\\new", context_from: "weekly" },
        payload_unset: ["skills", "skip_memory"],
      },
    });
    // Saved = the editor is gone and the page says so.
    await waitFor(() => expect(screen.queryByTestId("schedule-editor")).toBeNull());
    await screen.findByText(/Schedule "nightly" updated/);
  });

  it("a re-timed schedule rides top-level; the name is read-only; nothing changed sends nothing", async () => {
    mountPage([knobbed()]);
    fireEvent.click(await screen.findByTestId("schedule-edit-nightly"));
    let editor = await screen.findByTestId("schedule-editor");
    fireEvent.click(within(editor).getByTestId("schedule-editor-save"));
    await waitFor(() => expect(screen.queryByTestId("schedule-editor")).toBeNull());
    expect(api.patches).toEqual([]);

    fireEvent.click(screen.getByTestId("schedule-edit-nightly"));
    editor = await screen.findByTestId("schedule-editor");
    // PATCH /schedules/{name} has no `name` field: the editor never sends one.
    expect(within(editor).getByLabelText("Schedule name")).toHaveAttribute("readonly");
    fireEvent.change(within(editor).getByLabelText("Cron expression"), { target: { value: "0 3 * * *" } });
    fireEvent.change(within(editor).getByLabelText("Task text"), { target: { value: "Nightly rounds, briefly." } });
    fireEvent.click(within(editor).getByTestId("schedule-editor-save"));
    await waitFor(() => expect(api.patches).toHaveLength(1));
    expect(api.patches[0].body).toEqual({
      cron: "0 3 * * *",
      payload_set: { task: "Nightly rounds, briefly." },
    });
  });

  it("a 404 on PATCH names the older daemon instead of a bare error", async () => {
    api.patchError = new api.FakeApiError("Not Found", 404);
    mountPage([knobbed()]);
    fireEvent.click(await screen.findByTestId("schedule-edit-nightly"));
    const editor = await screen.findByTestId("schedule-editor");
    fireEvent.change(within(editor).getByLabelText("Folder"), { target: { value: "C:\\x" } });
    fireEvent.click(within(editor).getByTestId("schedule-editor-save"));
    await within(editor).findByText(/cannot edit schedules yet/);
    expect(screen.getByTestId("schedule-editor")).toBeInTheDocument(); // still open to retry
  });
});
