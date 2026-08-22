import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * v1.200.0 — the Reflexes page reaches the context spine (CONNECT-AUDIT item 5).
 *
 * Reflex-spawned work was never project-grounded: the rule model had no
 * project field and the page never mentioned projects at all. The fix's UI
 * half, pinned here:
 *
 *  - the add form carries a project picker (same idiom as the schedules
 *    form: a <select> defaulting to "No project") and SENDS `project_id`
 *    with the POST — a picker that renders but never reaches the wire is
 *    the silent failure mode;
 *  - a remote-agent action hides the picker AND submits project_id null
 *    even if one was picked first (no grounding seam on someone else's
 *    endpoint — storing a project there would claim grounding that cannot
 *    happen);
 *  - a rule row that carries a project renders a project BADGE with the
 *    project's NAME (the id alone is unreadable), and an ungrounded rule
 *    renders none.
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
    return Promise.resolve({ ok: true });
  },
  patch: () => Promise.resolve({}),
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
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {actions}
    </div>
  ),
}));

import ReflexPage from "@/app/reflex/page";

afterEach(() => {
  cleanup();
  api.calls = [];
  api.posts = [];
  api.responses = {};
});

function seed(rules: unknown[] = []) {
  api.responses["/reflex/rules"] = { rules };
  api.responses["/workflows"] = { workflows: [{ name: "nightly" }] };
  api.responses["/agents/remote"] = { agents: [] };
  api.responses["/webhooks"] = { webhooks: [] };
  api.responses["/projects"] = {
    projects: [
      { id: "proj_1", name: "Tax Client A" },
      { id: "proj_2", name: "Side Quest" },
    ],
  };
  api.responses["/triggers"] = {};
}

function rule(over: Record<string, unknown> = {}) {
  return {
    id: "reflex_1",
    name: "missing-1099",
    source: "email",
    match: "1099",
    action: "session",
    target: "",
    task_template: "",
    project_id: null,
    enabled: true,
    created_at: "2026-08-22T10:00:00Z",
    last_fired_at: null,
    fire_count: 0,
    ...over,
  };
}

async function openForm() {
  render(<ReflexPage />);
  fireEvent.click(await screen.findByText("Add reflex"));
  // The picker's options come from GET /projects — wait for them.
  await screen.findByRole("option", { name: "Tax Client A" });
}

/* ------------------------------------------------------ the form's project */

describe("the add form grounds a rule in a project", () => {
  it("sends the picked project_id with the POST", async () => {
    seed();
    await openForm();

    // comm source (no slug needed) + workflow action targeting "nightly".
    fireEvent.change(screen.getByLabelText("Signal source"), {
      target: { value: "comm" },
    });
    fireEvent.change(await screen.findByLabelText("Workflow"), {
      target: { value: "nightly" },
    });
    fireEvent.change(screen.getByLabelText("Project"), {
      target: { value: "proj_1" },
    });
    fireEvent.submit(document.querySelector("form")!);

    await waitFor(() => expect(api.posts.length).toBe(1));
    const body = api.posts[0].body as Record<string, unknown>;
    expect(api.posts[0].path).toBe("/reflex/rules");
    expect(body.project_id).toBe("proj_1");
    expect(body.action).toBe("workflow");
    expect(body.target).toBe("nightly");
  });

  it("defaults to No project and then sends project_id null", async () => {
    seed();
    await openForm();

    const picker = screen.getByLabelText("Project") as HTMLSelectElement;
    expect(picker.value).toBe("");

    fireEvent.change(screen.getByLabelText("Signal source"), {
      target: { value: "comm" },
    });
    fireEvent.change(screen.getByLabelText("Action"), {
      target: { value: "session" },
    });
    fireEvent.submit(document.querySelector("form")!);

    await waitFor(() => expect(api.posts.length).toBe(1));
    expect((api.posts[0].body as Record<string, unknown>).project_id).toBeNull();
  });

  it("hides the picker for a remote-agent action and drops a stale pick", async () => {
    seed();
    api.responses["/agents/remote"] = { agents: [{ name: "hermes" }] };
    await openForm();

    // Pick a project FIRST, then switch to remote_agent: the picker hides and
    // the stale pick must not ride the POST — a remote agent has no grounding
    // seam, so storing a project there would be a claim the run can't honour.
    fireEvent.change(screen.getByLabelText("Signal source"), {
      target: { value: "comm" },
    });
    fireEvent.change(screen.getByLabelText("Project"), {
      target: { value: "proj_2" },
    });
    fireEvent.change(screen.getByLabelText("Action"), {
      target: { value: "remote_agent" },
    });
    expect(screen.queryByLabelText("Project")).toBeNull();

    fireEvent.change(await screen.findByLabelText("Remote agent"), {
      target: { value: "hermes" },
    });
    fireEvent.submit(document.querySelector("form")!);

    await waitFor(() => expect(api.posts.length).toBe(1));
    expect((api.posts[0].body as Record<string, unknown>).project_id).toBeNull();
  });
});

/* --------------------------------------------------------- the row's badge */

describe("a grounded rule wears its project badge", () => {
  it("renders the project NAME on rows that carry one, nothing on the rest", async () => {
    seed([
      rule({ id: "reflex_1", name: "missing-1099", project_id: "proj_1" }),
      rule({ id: "reflex_2", name: "ungrounded", project_id: null }),
    ]);
    render(<ReflexPage />);

    expect(await screen.findByText("Tax Client A")).toBeInTheDocument();
    expect(screen.getByText("missing-1099")).toBeInTheDocument();
    expect(screen.getByText("ungrounded")).toBeInTheDocument();
    // Exactly one badge: the ungrounded row shows none.
    expect(screen.getAllByText("Tax Client A")).toHaveLength(1);
  });

  it("falls back to the raw id when the project list doesn't know it", async () => {
    seed([rule({ id: "reflex_3", name: "orphan", project_id: "proj_gone" })]);
    render(<ReflexPage />);

    // An unknown/deleted project still renders honestly (the id), never blank.
    expect(await screen.findByText("proj_gone")).toBeInTheDocument();
  });
});
