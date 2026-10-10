/**
 * CALM CHAT W4 F6 (v1.329.0): the chat's project views lose their boxes.
 *
 *  - TASKS (`ProjectSurface view="tasks"`): the run-a-task form keeps every
 *    control (task box, mic, Deliverable, filename, Assign to, Run) but no
 *    card: no `card-surface`, the task box is not a `field` box, the selects
 *    are ghost chips, and Run is the ONE primary (`btn-accent`) action. The
 *    "" choice reads "Me, run it now" (no em-dash aside).
 *  - The PROJECT PAGE keeps the card (control: `ProjectTasks` without `bare`).
 *  - BOARD: the empty / offline states are a quiet line, never a titled card.
 *  - MEDIA: the grid or its empty line, no card.
 *  - The drawer's helpers: `useOpenTerminal` POSTs /terminals with the folder
 *    and goes to Build focused on the new pane; a refusal is a sentence.
 *  - KnowledgePanel / ArtifactsRail have a `bare` form for the drawer; their
 *    default (the project page, the session page) keeps the box.
 */

import { type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    FakeApiError,
    posts: [] as { path: string; body: unknown }[],
    responses: {} as Record<string, unknown>,
    postResponses: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    const r = api.responses[path];
    if (r instanceof Error) return Promise.reject(r);
    return Promise.resolve(r === undefined ? {} : r);
  },
  post: (path: string, body: unknown) => {
    api.posts.push({ path, body });
    const r = api.postResponses[path];
    if (r instanceof Error) return Promise.reject(r);
    return Promise.resolve(r === undefined ? {} : r);
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useReviews", () => ({ useReviews: () => ({ reviews: {}, reload: () => {} }) }));
vi.mock("@/components/kanban/KanbanBoard", () => ({
  KanbanBoard: () => <div data-testid="kanban-stub" />,
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: ReactNode; href: string } & Record<string, unknown>) => (
    <a href={href} {...(rest as Record<string, never>)}>
      {children}
    </a>
  ),
}));

import { ProjectSurface } from "@/components/project/ProjectSurfaces";
import { ProjectTasks, SELF_LABEL, assigneeOptions } from "@/components/project/ProjectTasks";
import { KnowledgePanel } from "@/components/project/KnowledgePanel";
import { ArtifactsRail } from "@/components/chat/ArtifactsRail";
import { terminalHref, useOpenTerminal } from "@/components/chat/ProjectPanelParts";

afterEach(() => {
  cleanup();
  api.posts = [];
  api.responses = {};
  api.postResponses = {};
});

function classes(el: Element | null | undefined): string[] {
  return ((el?.getAttribute("class") ?? "") as string).split(/\s+/).filter(Boolean);
}

const BOARD = "/sessions?project_id=proj_1";
const MEDIA = "/creative/items?project_id=proj_1&limit=200";

describe("Tasks in the chat column: the run form without its card", () => {
  it("no card anywhere in the surface; the form is a plain labelled section", async () => {
    const { container } = render(<ProjectSurface projectId="proj_1" hasRoot view="tasks" />);
    const form = await screen.findByTestId("project-tasks");
    expect(form.getAttribute("data-bare")).toBe("true");
    expect(container.querySelector(".card-surface")).toBeNull();
    expect(form.querySelector("h2")?.textContent).toBe("Run a task");
  });

  it("the task box is not a field box, and its edge is one hairline", async () => {
    render(<ProjectSurface projectId="proj_1" hasRoot view="tasks" />);
    const box = await screen.findByLabelText("Task for an agent in this project");
    expect(classes(box)).not.toContain("field");
    expect(classes(box)).toContain("bg-transparent");
    const edge = box.parentElement as HTMLElement;
    expect(classes(edge)).toEqual(expect.arrayContaining(["border-b", "hairline"]));
  });

  it("every control stays, as ghosts, with ONE primary action (Run)", async () => {
    render(<ProjectSurface projectId="proj_1" hasRoot view="tasks" />);
    const deliverable = await screen.findByLabelText("Deliverable");
    const assign = screen.getByLabelText("Assign to");
    for (const sel of [deliverable, assign]) {
      expect(classes(sel)).not.toContain("field");
      expect(classes(sel)).toContain("bg-transparent");
      expect(classes(sel)).toContain("hover:bg-white/[0.06]");
    }
    // The v1.315.0 floor survives the restyle.
    expect(classes(deliverable)).toContain("min-w-[10rem]");
    // The mic stays (jsdom has no speech engine, so it is the disabled one).
    expect(screen.getByRole("button", { name: /Voice input unavailable|Start dictation/ })).toBeTruthy();
    fireEvent.change(deliverable, { target: { value: "md" } });
    expect(classes(screen.getByLabelText("Deliverable filename"))).not.toContain("field");
    const primaries = Array.from(document.querySelectorAll("button")).filter((b) =>
      classes(b).includes("btn-accent"),
    );
    expect(primaries.map((b) => b.textContent?.trim())).toEqual(["Run"]);
  });

  it("the run-it-myself choice is plain words, and so is a custom agent", async () => {
    render(<ProjectSurface projectId="proj_1" hasRoot view="tasks" />);
    const assign = (await screen.findByLabelText("Assign to")) as HTMLSelectElement;
    expect(assign.options[0].textContent).toBe(SELF_LABEL);
    expect(SELF_LABEL).toBe("Me, run it now");
    expect(
      assigneeOptions({ builtin: [], dynamic: [{ name: "drafter" }] } as never).map((o) => o.label),
    ).toEqual(["drafter (yours)"]);
  });

  it("no em-dash aside in the form's own sentences", async () => {
    render(<ProjectSurface projectId="proj_1" hasRoot={false} view="tasks" />);
    const form = await screen.findByTestId("project-tasks");
    await screen.findByText(/A file deliverable needs a project folder/);
    expect(form.textContent ?? "").not.toMatch(/\s—\s/);
  });

  it("CONTROL: the project page (no `bare`) keeps its card and its field boxes", () => {
    const { container } = render(<ProjectTasks projectId="proj_1" hasRoot sessions={[]} />);
    expect(container.querySelector(".card-surface")).not.toBeNull();
    expect(screen.queryByTestId("project-tasks")).toBeNull();
    expect(classes(screen.getByLabelText("Task for an agent in this project"))).toContain("field");
    expect(classes(screen.getByLabelText("Deliverable"))).toContain("field");
  });

  it("a run still plans and posts from the bare form (nothing lost with the box)", async () => {
    api.postResponses["/projects/proj_1/task/plan"] = { tools: [] };
    api.postResponses["/projects/proj_1/task"] = {
      id: "s-new",
      task: "sum it",
      status: "active",
      output: "chat",
      created_at: "2026-10-09T10:00:00Z",
    };
    // GET /sessions/{id} is NESTED ({session, transcript}).
    api.responses["/sessions/s-new"] = {
      session: { id: "s-new", task: "sum it", status: "active", created_at: "2026-10-09T10:00:00Z" },
      transcript: [],
    };
    render(<ProjectTasks projectId="proj_1" hasRoot sessions={[]} bare />);
    fireEvent.change(screen.getByLabelText("Task for an agent in this project"), {
      target: { value: "sum the ledger" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^\s*Run\s*$/ }));
    });
    await waitFor(() =>
      expect(api.posts).toContainEqual({
        path: "/projects/proj_1/task",
        body: { text: "sum the ledger", output: "chat", allow_tools: [] },
      }),
    );
    // The live strip is a hairline row, not a box.
    const strip = await screen.findByTestId("project-task-run");
    expect(classes(strip)).toEqual(expect.arrayContaining(["border-t", "hairline"]));
    expect(classes(strip)).not.toContain("rounded-lg");
  });
});

describe("Board and Media in the chat column: no outer card", () => {
  it("an empty board is a quiet line in plain words, not a titled card", async () => {
    api.responses[BOARD] = { sessions: [] };
    const { container } = render(<ProjectSurface projectId="proj_1" hasRoot view="board" />);
    await screen.findByText("No sessions in this project yet. Run a task from the Tasks tab.");
    expect(container.querySelector(".card-surface")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Board" })).toBeNull();
  });

  it("an offline board says so, still without a card", async () => {
    api.responses[BOARD] = new api.FakeApiError("down", 0);
    const { container } = render(<ProjectSurface projectId="proj_1" hasRoot view="board" />);
    await screen.findByText("Board unavailable. The daemon looks offline.");
    expect(container.querySelector(".card-surface")).toBeNull();
  });

  it("a board with sessions is the board itself", async () => {
    api.responses[BOARD] = {
      sessions: [{ id: "s1", project_id: "proj_1", status: "completed", task: "t", created_at: "x" }],
    };
    const { container } = render(<ProjectSurface projectId="proj_1" hasRoot view="board" />);
    await screen.findByTestId("kanban-stub");
    expect(container.querySelector(".card-surface")).toBeNull();
  });

  it("media is its grid or its empty line, no card", async () => {
    api.responses[MEDIA] = { items: [] };
    const { container } = render(<ProjectSurface projectId="proj_1" hasRoot view="media" />);
    await screen.findByText(/No media in this project yet\. Media made in this project/);
    expect(container.querySelector(".card-surface")).toBeNull();
  });
});

describe("the drawer's terminal action", () => {
  it("opens a NEW terminal in the folder and goes to Build focused on it", async () => {
    api.postResponses["/terminals"] = { id: "term_9" };
    const went: string[] = [];
    const { result } = renderHook(() => useOpenTerminal("C:\\Work\\Harbor", (h) => went.push(h)));
    await act(async () => {
      await result.current.open();
    });
    expect(api.posts).toEqual([{ path: "/terminals", body: { cwd: "C:\\Work\\Harbor" } }]);
    expect(went).toEqual([terminalHref("term_9")]);
    expect(went[0]).toBe("/terminals?focus=term_9");
    expect(result.current.error).toBeNull();
  });

  it("a refusal stays on screen as a sentence and goes nowhere", async () => {
    api.postResponses["/terminals"] = new api.FakeApiError("Too many terminals are open (5).", 429);
    const went: string[] = [];
    const { result } = renderHook(() => useOpenTerminal("C:\\Work", (h) => went.push(h)));
    await act(async () => {
      await result.current.open();
    });
    expect(went).toEqual([]);
    expect(result.current.error).toBe("Too many terminals are open (5).");
    expect(result.current.busy).toBe(false);
  });

  it("no folder, no request", async () => {
    const { result } = renderHook(() => useOpenTerminal(null, () => {}));
    await act(async () => {
      await result.current.open();
    });
    expect(api.posts).toEqual([]);
  });
});

describe("bare forms for the drawer; the boxes stay elsewhere", () => {
  it("KnowledgePanel bare: no card, the count beside its one sentence, ghost buttons", async () => {
    api.responses["/projects/proj_1/knowledge"] = { knowledge: [], count: 0 };
    const { container } = render(<KnowledgePanel projectId="proj_1" bare />);
    const panel = await screen.findByTestId("knowledge-panel");
    expect(container.querySelector(".card-surface")).toBeNull();
    expect(panel.textContent).toContain("0 items");
    for (const name of [/Paste a note/, /Add file/]) {
      expect(classes(screen.getByRole("button", { name }))).not.toContain("btn-ghost");
    }
    await screen.findByText("No knowledge yet. Paste a note or add a file to ground this project.");
  });

  it("KnowledgePanel CONTROL: the project page's rail keeps the card", () => {
    const { container } = render(<KnowledgePanel projectId="proj_1" />);
    expect(container.querySelector(".card-surface")).not.toBeNull();
  });

  it("ArtifactsRail bare: no rounded border of its own; the default keeps it", () => {
    const items = [{ path: "C:/work/report.docx" }];
    const { container, unmount } = render(
      <ArtifactsRail items={items} onPreview={() => {}} downloadHref={() => "#"} bare />,
    );
    const root = container.firstElementChild as HTMLElement;
    expect(classes(root)).not.toContain("rounded-xl");
    expect(classes(root)).not.toContain("border");
    unmount();
    const boxed = render(<ArtifactsRail items={items} onPreview={() => {}} downloadHref={() => "#"} />);
    expect(classes(boxed.container.firstElementChild)).toEqual(
      expect.arrayContaining(["rounded-xl", "border"]),
    );
  });
});
