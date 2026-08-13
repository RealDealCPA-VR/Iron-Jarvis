import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * v1.171.0 P3 — per-agent routines on the Schedules page.
 *
 * What is guarded, each with a silent failure mode:
 *
 *  - the task-kind form grows a "Who runs it" picker fed by GET /agents
 *    (builtin + dynamic, the NewSessionForm source), defaulting to builder —
 *    a missing picker silently pins every routine to the builder;
 *  - a picked agent lands in the POSTed payload as `agent_type`, and the
 *    DEFAULT builder pick is OMITTED so default payloads look exactly like
 *    pre-v1.171 ones (an always-sent field would make "picked builder" and
 *    "default" indistinguishable server-side);
 *  - task rows show an AgentFace + the agent's name, read from the
 *    server-decoded `agent_type` with a payload-blob fallback for an older
 *    daemon; absent/garbage decays to "builder" (the fire's real default),
 *    never an invented name, and non-task rows show no face row at all;
 *  - the face is DETERMINISTIC: the row's face and any other face for the
 *    same name share the same seeded shape (drift = two identities).
 */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    calls: [] as string[],
    posts: [] as { path: string; body: unknown }[],
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
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    }
    if (r instanceof api.FakeApiError) return Promise.reject(r);
    return Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    return Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({
      href,
      children,
      ...rest
    }: {
      href: string;
      children?: React.ReactNode;
    }) => createElement("a", { href, ...rest }, children),
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
import { faceShape } from "@/components/agents/AgentFace";
import type { Schedule } from "@/lib/types";

afterEach(() => {
  cleanup();
  api.calls = [];
  api.posts = [];
  api.responses = {};
});

/* ---------------------------------------------------------------- fixtures */

function sched(over: Partial<Schedule> = {}): Schedule {
  return {
    name: "remy-rounds",
    cron: "0 9 * * *",
    kind: "task",
    enabled: true,
    next_run: null,
    last_run: null,
    trigger_type: "cron",
    payload_json: JSON.stringify({ task: "Morning rounds." }),
    agent_type: "remy",
    last_status: "",
    last_detail: "",
    last_session_id: "",
    ...over,
  } as Schedule;
}

function mountPage(
  schedules: Schedule[] = [],
  dynamic: { name: string }[] = [{ name: "remy" }],
) {
  api.responses["/schedules"] = { schedules };
  api.responses["/workflows"] = { workflows: [] };
  api.responses["/projects"] = { projects: [] };
  api.responses["/comm/channels"] = { channels: [] };
  api.responses["/agents"] = {
    builtin: ["builder", "researcher", "reviewer"],
    dynamic,
  };
  return render(<SchedulesPage />);
}

/* --------------------------------------------------------------- the form */

describe("agent picker (task form)", () => {
  it("lists builtin + dynamic agents from GET /agents, defaulting to builder", async () => {
    mountPage();
    const picker = (await screen.findByLabelText("Agent")) as HTMLSelectElement;
    expect(picker.value).toBe("builder");
    const labels = within(picker)
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(labels).toEqual(["builder", "researcher", "reviewer", "remy (custom)"]);
    await waitFor(() => expect(api.calls).toContain("/agents"));
  });

  it("a dynamic agent shadowing a builtin name renders ONE option, never two", async () => {
    // POST /agents does not refuse builtin names, so a dynamic "builder"
    // would otherwise render a second <option value="builder"> the select
    // cannot distinguish — and picking the "custom" one silently fires the
    // BUILTIN (the default builder value is omitted from the payload).
    mountPage([], [{ name: "builder" }, { name: "remy" }]);
    const picker = await screen.findByLabelText("Agent");
    // Wait for the live /agents list so the dynamic group has rendered.
    await waitFor(() =>
      expect(
        within(picker as HTMLElement)
          .getAllByRole("option")
          .map((o) => o.textContent),
      ).toContain("remy (custom)"),
    );
    const options = within(picker as HTMLElement).getAllByRole("option");
    expect(
      options.filter((o) => (o as HTMLOptionElement).value === "builder").length,
    ).toBe(1);
    expect(options.map((o) => o.textContent)).not.toContain("builder (custom)");
  });

  it("a picked agent lands in the payload as agent_type", async () => {
    mountPage();
    const picker = await screen.findByLabelText("Agent");
    // Wait for the live /agents list so "remy" is a real option.
    await waitFor(() =>
      expect(
        within(picker as HTMLElement)
          .getAllByRole("option")
          .map((o) => (o as HTMLOptionElement).value),
      ).toContain("remy"),
    );
    fireEvent.change(picker, { target: { value: "remy" } });
    fireEvent.change(screen.getByLabelText("Task text"), {
      target: { value: "Do the rounds." },
    });
    fireEvent.change(screen.getByPlaceholderText("morning-briefing"), {
      target: { value: "remy-rounds" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Add schedule/ }));

    await waitFor(() => expect(api.posts.length).toBe(1));
    const body = api.posts[0].body as {
      kind: string;
      payload: Record<string, unknown>;
    };
    expect(api.posts[0].path).toBe("/schedules");
    expect(body.kind).toBe("task");
    expect(body.payload.agent_type).toBe("remy");
  });

  it("the default builder pick is OMITTED — payloads look exactly like today's", async () => {
    mountPage();
    await screen.findByLabelText("Agent"); // default stays "builder"
    fireEvent.change(screen.getByLabelText("Task text"), {
      target: { value: "Plain task." },
    });
    fireEvent.change(screen.getByPlaceholderText("morning-briefing"), {
      target: { value: "plain" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Add schedule/ }));

    await waitFor(() => expect(api.posts.length).toBe(1));
    const body = api.posts[0].body as { payload: Record<string, unknown> };
    expect("agent_type" in body.payload).toBe(false);
    expect(body.payload.task).toBe("Plain task.");
  });
});

/* --------------------------------------------------------------- the rows */

describe("task rows", () => {
  it("show an AgentFace + the server-decoded agent name", async () => {
    mountPage([sched()]);
    const badge = await screen.findByTestId("schedule-agent");
    expect(badge).toHaveTextContent("remy");
    const face = within(badge).getByTestId("agent-face");
    // Deterministic identity: the row's face carries remy's seeded shape.
    expect(face.getAttribute("data-face-shape")).toBe(faceShape("remy"));
  });

  it("a row without agent_type shows builder — the fire's real default", async () => {
    mountPage([sched({ name: "plain", agent_type: "" })]);
    const badge = await screen.findByTestId("schedule-agent");
    expect(badge).toHaveTextContent("builder");
    expect(within(badge).getByTestId("agent-face").getAttribute("data-face-shape")).toBe(
      faceShape("builder"),
    );
  });

  it("falls back to the payload blob for a daemon older than this dashboard", async () => {
    mountPage([
      sched({
        name: "legacy",
        agent_type: undefined,
        payload_json: JSON.stringify({ task: "x", agent_type: "researcher" }),
      }),
    ]);
    expect(await screen.findByTestId("schedule-agent")).toHaveTextContent("researcher");
  });

  it("a non-string blob agent_type decays to builder, never a coerced name", async () => {
    mountPage([
      sched({
        name: "garbage",
        agent_type: undefined,
        payload_json: JSON.stringify({ task: "x", agent_type: 123 }),
      }),
    ]);
    const badge = await screen.findByTestId("schedule-agent");
    expect(badge).toHaveTextContent("builder");
    expect(badge).not.toHaveTextContent("123");
  });

  it("non-task rows show no face row at all", async () => {
    mountPage([
      sched({
        name: "wf",
        kind: "workflow",
        agent_type: "",
        payload_json: JSON.stringify({ workflow: "w" }),
      }),
    ]);
    await screen.findByText("Workflow: w");
    expect(screen.queryByTestId("schedule-agent")).toBeNull();
  });
});
