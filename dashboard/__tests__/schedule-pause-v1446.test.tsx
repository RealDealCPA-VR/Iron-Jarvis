/**
 * v1.446.0 — ease-of-use review items 24, 23 (Schedules half) and 25.
 *
 * Before: a schedule row showed a dot for on/off but nothing turned it off, so
 * "pause my morning briefing while I'm away" meant deleting it. While
 * Emergency stop was on the page still promised "Next run: tomorrow 8:00 AM".
 * The edit window needed a raw cron expression for any time change.
 *
 * Pinned here:
 *  - every row has a switch (role="switch", named with the schedule's name,
 *    `aria-checked` = on) that PATCHes /schedules/{name} {enabled} — nothing
 *    else — and then reads the list again;
 *  - a paused row says "Paused" and promises no next run;
 *  - a finished one-time schedule offers no switch (nothing to resume);
 *  - while Emergency stop is on the page says so above the list and every
 *    next run reads "Paused by Emergency stop" (the time only in the title);
 *  - the edit window offers the add form's Repeat presets; a preset needs no
 *    cron box, and choosing one PATCHes its cron.
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
    patches: [] as { path: string; body: unknown }[],
    responses: {} as Record<string, unknown>,
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
  post: () => Promise.resolve({}),
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
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

import SchedulesPage from "@/app/schedules/page";
import { noteEmergencyStop, resetEmergencyStopForTests } from "@/components/EmergencyStopStrip";
import type { Schedule } from "@/lib/types";

afterEach(() => {
  cleanup();
  resetEmergencyStopForTests();
  api.calls = [];
  api.patches = [];
  api.responses = {};
});

const NEXT = "2030-01-02T08:00:00Z";

function row(over: Partial<Schedule> = {}): Schedule {
  return {
    name: "morning-briefing",
    cron: "0 8 * * 1-5",
    kind: "task",
    enabled: true,
    next_run: NEXT,
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

function mount(rows: Schedule[], stopOn = false) {
  api.responses["/schedules"] = { schedules: rows };
  api.responses["/workflows"] = { workflows: [] };
  api.responses["/agents"] = { builtin: ["builder"], dynamic: [] };
  api.responses["/projects"] = { projects: [] };
  api.responses["/comm/channels"] = { channels: [] };
  api.responses["/autonomy"] = { kill_switch: stopOn };
  render(<SchedulesPage />);
}

const schedulesReads = () => api.calls.filter((p) => p === "/schedules").length;

describe("pause and resume a schedule", () => {
  it("the switch is named with the schedule, says on, and Pause PATCHes {enabled: false} then re-reads", async () => {
    mount([row()]);
    const sw = await screen.findByRole("switch", { name: "Run morning-briefing on schedule" });
    expect(sw).toHaveAttribute("aria-checked", "true");
    const before = schedulesReads();
    fireEvent.click(sw);
    await waitFor(() => expect(api.patches).toHaveLength(1));
    expect(api.patches[0]).toEqual({ path: "/schedules/morning-briefing", body: { enabled: false } });
    await waitFor(() => expect(schedulesReads()).toBeGreaterThan(before));
    expect(await screen.findByText(/Schedule "morning-briefing" paused/)).toBeInTheDocument();
  });

  it("a paused row says Paused, promises no next run, and Resume PATCHes {enabled: true}", async () => {
    mount([row({ enabled: false, next_run: null })]);
    const sw = await screen.findByRole("switch", { name: "Run morning-briefing on schedule" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    expect(sw).toHaveTextContent("Paused");
    const next = screen.getByTestId("schedule-next-morning-briefing");
    expect(next.textContent).toBe("Paused");
    fireEvent.click(sw);
    await waitFor(() =>
      expect(api.patches).toEqual([{ path: "/schedules/morning-briefing", body: { enabled: true } }]),
    );
    expect(await screen.findByText(/Schedule "morning-briefing" resumed/)).toBeInTheDocument();
  });

  it("a one-time schedule that already ran offers no switch", async () => {
    mount([
      row({
        name: "once-done",
        cron: null as unknown as string,
        trigger_type: "date",
        run_at: "2020-01-01T09:00:00Z",
        enabled: false,
        next_run: null,
      }),
    ]);
    await screen.findByTestId("schedule-next-once-done");
    expect(screen.queryByRole("switch")).toBeNull();
  });
});

describe("Emergency stop on the Schedules page", () => {
  it("says so above the list, and a next run reads as paused, the time only in its title", async () => {
    mount([row()], true);
    expect(
      await screen.findByText("Emergency stop is on — schedules are skipped until you turn it off."),
    ).toBeInTheDocument();
    const next = screen.getByTestId("schedule-next-morning-briefing");
    await waitFor(() => expect(next.textContent).toBe("Paused by Emergency stop"));
    expect(next.querySelector("[title]")?.getAttribute("title")).toMatch(/^Would run .+ skipped while Emergency stop is on$/);
  });

  it("off: no notice and the next run time shows (control)", async () => {
    mount([row()]);
    await screen.findByTestId("schedule-next-morning-briefing");
    noteEmergencyStop(false);
    const next = screen.getByTestId("schedule-next-morning-briefing");
    expect(next.textContent).not.toMatch(/Paused/);
    expect(next.textContent).toMatch(/·/);
    expect(screen.queryByTestId("schedules-emergency-stop")).toBeNull();
  });
});

describe("the edit window uses the friendly Repeat presets", () => {
  it("a preset schedule opens on its preset with no cron box, and a new preset PATCHes its cron", async () => {
    mount([row()]);
    fireEvent.click(await screen.findByTestId("schedule-edit-morning-briefing"));
    const editor = await screen.findByTestId("schedule-editor");
    const repeat = within(editor).getByLabelText("Repeat") as HTMLSelectElement;
    expect(repeat.value).toBe("0 8 * * 1-5");
    expect(within(editor).queryByLabelText("Cron expression")).toBeNull();
    fireEvent.change(repeat, { target: { value: "0 9 * * 1-5" } });
    fireEvent.click(within(editor).getByTestId("schedule-editor-save"));
    await waitFor(() => expect(api.patches).toHaveLength(1));
    expect(api.patches[0].body).toEqual({ cron: "0 9 * * 1-5" });
  });

  it("Custom shows the cron box; a stored pattern no preset names opens on Custom", async () => {
    mount([row({ name: "odd", cron: "5 4 * * *" })]);
    fireEvent.click(await screen.findByTestId("schedule-edit-odd"));
    const editor = await screen.findByTestId("schedule-editor");
    expect((within(editor).getByLabelText("Repeat") as HTMLSelectElement).value).toBe("__advanced__");
    expect(within(editor).getByLabelText("Cron expression")).toHaveValue("5 4 * * *");
  });
});
