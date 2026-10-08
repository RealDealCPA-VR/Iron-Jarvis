/**
 * v1.315.0 — UX & aesthetic wave 3, track T3 (projects).
 *
 * Four verified findings, each pinned on the REAL component:
 *
 *  1. project-card-button-hierarchy (/projects, card view): a card had five
 *     rows and no first move — an inert "Chat · Tasks · Board · Knowledge"
 *     strip that looked clickable, "Chat" twice, "Make active" as the
 *     brightest control, and Archive/Delete at the same weight as Chat. Now:
 *     ONE accent-filled control (Open workspace), a primary group (Open
 *     workspace + Chat) and a quiet secondary group after `ml-auto` (Set as
 *     focus / Clear focus, Archive / Unarchive, Delete). Archive and Delete
 *     stay two-press ConfirmButtons with a hit target of at least 28px.
 *  2. project-header-unlabelled-model-select (/projects/[id]): a full-width
 *     select reading only "Project default". Now it has a VISIBLE "Model"
 *     label tied to it, a sensible width, the empty choice says what it
 *     means ("Same as app default" — chats and "run now" tasks fall back to
 *     the app's default provider/model when the project pins none; the
 *     title carries the one caveat: a task queued for a custom agent uses
 *     that agent's own model unless pinned), and the lifecycle
 *     buttons sit in their own right-aligned group.
 *  3. phone-project-task-deliverable-collapses: the Deliverable select had
 *     `min-w-0 flex-1` on a wrap row and gave up all its width on a phone
 *     (a ~36px chevron box). Now it keeps a floor / takes its own line.
 *  4. recent-runs-indistinguishable: four identical "Completed · Done. Wrote
 *     RESULT.md…" rows. Now the TASK leads, the summary follows as its own
 *     plainText span, and each row says WHEN.
 *
 * Anti-vacuity: every action stays (open, chat, focus, archive/unarchive,
 * delete — two-press), the knowledge + session counts stay, the model
 * select keeps every option incl. the "(unavailable)" pin and still PATCHes,
 * the run flow still plans → asks for the tool grant → posts the task, and
 * a recent-run row still links to its session with its status badge.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    FakeApiError,
    responses: {} as Record<string, unknown>,
    postResponses: {} as Record<string, unknown>,
    posts: [] as { path: string; body?: unknown }[],
    patches: [] as { path: string; body?: unknown }[],
    dels: [] as string[],
    models: [] as unknown[],
    search: "",
  };
});

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (H.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (H.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({
  useModels: () => ({
    data: { models: H.models },
    models: H.models,
    usable: H.models,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));
vi.mock("@/lib/useReviews", () => ({
  useReviews: () => ({ reviews: {}, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/api", () => ({
  API_BASE: "http://test",
  ApiError: H.FakeApiError,
  ijToken: () => "",
  setIjToken: () => {},
  onUnauthorizedChange: () => () => {},
  wsUrl: (p: string) => `ws://test${p}`,
  sseUrl: (p: string) => `http://test${p}`,
  get: (path: string) => {
    const r = H.responses[path];
    return r === undefined
      ? Promise.reject(new H.FakeApiError(`unmocked GET ${path}`, 404))
      : Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    H.posts.push({ path, body });
    return Promise.resolve(H.postResponses[path] ?? {});
  },
  put: () => Promise.resolve({}),
  patch: (path: string, body?: unknown) => {
    H.patches.push({ path, body });
    return Promise.resolve({});
  },
  del: (path: string) => {
    H.dels.push(path);
    return Promise.resolve({});
  },
  api: () => Promise.resolve({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/link", () => ({
  default: ({ children, href, prefetch: _p, ...rest }: React.ComponentProps<"a"> & { prefetch?: unknown }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(H.search),
  usePathname: () => "/projects/p1",
}));
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (p: string, c: string) => p + c,
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, subtitle, actions }: { title: string; subtitle?: React.ReactNode; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      <p>{subtitle}</p>
      {actions}
    </div>
  ),
}));
vi.mock("@/components/FilePickerModal", () => ({
  FilePickerModal: ({ open, title }: { open: boolean; title?: string }) =>
    open ? <div data-testid="picker">{title}</div> : null,
}));
// Project-page leaves that are not under test.
vi.mock("@/components/kanban/KanbanBoard", () => ({ KanbanBoard: () => null }));
vi.mock("@/components/project/KnowledgePanel", () => ({ KnowledgePanel: () => null }));
vi.mock("@/components/project/ProjectSurfaces", () => ({ ProjectApprovals: () => null }));
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial", "animate", "exit", "transition", "variants", "layout", "layoutId",
    "whileHover", "whileTap", "whileInView", "viewport",
  ]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    // v1.250.0 mock contract: every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    LazyMotion: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    domAnimation: {},
    useReducedMotion: () => true,
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

const ProjectsPage = (await import("@/app/projects/page")).default;
const ProjectWorkspacePage = (await import("@/app/projects/[id]/page")).default;
const { ProjectTasks } = await import("@/components/project/ProjectTasks");
const { timeAgo } = await import("@/lib/format");
import type { SessionView } from "@/lib/types";

const src = (rel: string): string =>
  readFileSync(join(process.cwd(), rel), "utf8").replace(/\r\n/g, "\n");

beforeEach(() => {
  H.responses = {};
  H.postResponses = {};
  H.posts = [];
  H.patches = [];
  H.dels = [];
  H.models = [];
  H.search = "";
  window.localStorage.clear();
  window.HTMLElement.prototype.scrollTo = vi.fn();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

/* ------------------------------------------------------------------ helpers */

const ago = (ms: number) => new Date(Date.now() - ms).toISOString();
const HOUR = 3600_000;
const DAY = 24 * HOUR;

/** True when an element carries an accent FILL (not a hover-only one). */
function accentFilled(el: Element): boolean {
  const tokens = (el.getAttribute("class") ?? "").split(/\s+/);
  return tokens.some((t) => /^bg-accent(\/|$)/.test(t) || t === "btn-accent");
}

/** The card <div> of a project tile, found from its stretched link. */
function cardFor(name: string): HTMLElement {
  const stretched = screen.getByRole("link", { name: `Open ${name} workspace` });
  return stretched.parentElement as HTMLElement;
}

const P_ACTIVE = {
  id: "p1",
  name: "Q3 Bookkeeping",
  brief: "Reconcile client ledgers.",
  root: "C:\\work\\q3",
  root_exists: true,
  status: "active",
  active: true,
  session_count: 5,
  knowledge_count: 3,
  created_at: ago(2 * HOUR),
  updated_at: ago(2 * HOUR),
};
const P_IDLE = {
  id: "p2",
  name: "Website refresh",
  brief: "Rewrite the services page.",
  root: null,
  status: "active",
  active: false,
  session_count: 0,
  knowledge_count: 0,
  created_at: ago(3 * HOUR),
  updated_at: ago(3 * HOUR),
};
const P_ARCHIVED = {
  id: "p3",
  name: "Old audit",
  brief: "",
  root: null,
  status: "archived",
  active: false,
  session_count: 1,
  knowledge_count: 0,
  created_at: ago(5 * DAY),
  updated_at: ago(5 * DAY),
};

function renderCards() {
  H.responses["/projects"] = { projects: [P_ACTIVE, P_IDLE, P_ARCHIVED] };
  render(<ProjectsPage />);
}

/* ========================================================================== */
/*  1. Project cards: one first move, a quiet secondary group                  */
/* ========================================================================== */

describe("project card — one primary action, a quiet secondary group", () => {
  it("no inert 'Chat · Tasks · Board · Knowledge' strip; 'Chat' appears once", () => {
    renderCards();
    for (const name of [P_ACTIVE.name, P_IDLE.name]) {
      const card = cardFor(name);
      const own = (re: RegExp) =>
        Array.from(card.querySelectorAll("*")).filter(
          (n) => n.children.length === 0 || n.childElementCount === 1, // leaf-ish
        ).filter((n) => re.test((n.textContent ?? "").trim()));
      expect(own(/^Tasks$/), `${name}: decorative 'Tasks'`).toHaveLength(0);
      expect(own(/^Board$/), `${name}: decorative 'Board'`).toHaveLength(0);
      // The ONE "Chat" is the real door into the project's chat.
      const chats = within(card).getAllByText(/^\s*Chat\s*$/);
      expect(chats, `${name}: 'Chat' printed more than once`).toHaveLength(1);
      expect(chats[0].closest("a")?.getAttribute("href")).toBe(
        `/chat?project=${encodeURIComponent(name === P_ACTIVE.name ? "p1" : "p2")}`,
      );
    }
  });

  it("the knowledge and session counts are still on the card (moved to the meta line)", () => {
    renderCards();
    const t = cardFor(P_ACTIVE.name).textContent ?? "";
    expect(t).toMatch(/3 knowledge/);
    expect(t).toMatch(/5 sessions/);
    expect(t).toMatch(/created/);
    // The folder is still shown, its full path in the title.
    expect(within(cardFor(P_ACTIVE.name)).getByTitle(P_ACTIVE.root)).toBeInTheDocument();
  });

  it("exactly ONE accent-filled control per card, and it is Open workspace", () => {
    renderCards();
    // The inactive card is the one that had TWO (the CTA + 'Make active').
    const card = cardFor(P_IDLE.name);
    const filled = Array.from(card.querySelectorAll("*")).filter(accentFilled);
    expect(filled.map((e) => (e.textContent ?? "").trim())).toEqual([
      expect.stringMatching(/^Open workspace/),
    ]);
    // The active card: the Active BADGE may glow, but no CONTROL besides the CTA.
    const active = cardFor(P_ACTIVE.name);
    const controls = Array.from(active.querySelectorAll("button, a")).filter(accentFilled);
    for (const c of controls) expect((c.textContent ?? "").trim()).toMatch(/^Open workspace/);
  });

  it("primary group = Open workspace + Chat; secondary group (ml-auto) = focus, Archive, Delete", () => {
    renderCards();
    const card = cardFor(P_IDLE.name);
    const primary = within(card).getByTestId("project-card-primary");
    const secondary = within(card).getByTestId("project-card-secondary");
    expect(secondary.className).toMatch(/(^|\s)(sm:)?ml-auto(\s|$)/);

    expect(within(primary).getByText(/Open workspace/)).toBeInTheDocument();
    expect(within(primary).getByRole("link", { name: /^\s*Chat\s*$/ })).toBeInTheDocument();
    expect(within(primary).queryByRole("button", { name: /Delete|Archive/ })).toBeNull();

    expect(within(secondary).getByRole("button", { name: /Set as focus/ })).toBeInTheDocument();
    expect(within(secondary).getByRole("button", { name: /^Archive$/ })).toBeInTheDocument();
    expect(within(secondary).getByRole("button", { name: /^Delete$/ })).toBeInTheDocument();
    expect(within(secondary).queryByRole("link", { name: /^\s*Chat\s*$/ })).toBeNull();
  });

  it("Archive and Delete keep a hit target of at least 28px (min-h-7 or taller)", () => {
    renderCards();
    const card = cardFor(P_IDLE.name);
    for (const label of [/^Archive$/, /^Delete$/]) {
      const b = within(card).getByRole("button", { name: label });
      expect(b.className, `${label}: ${b.className}`).toMatch(
        /(^|\s)(min-h-7|min-h-8|min-h-\[(2[89]|[3-9]\d)px\]|h-7|h-8)(\s|$)/,
      );
    }
  });

  it("focus words: 'Set as focus' activates, 'Clear focus' clears — the honest 'marker only' hint stays", async () => {
    renderCards();
    const set = within(cardFor(P_IDLE.name)).getByRole("button", { name: /Set as focus/ });
    expect(set.getAttribute("title") ?? "").toMatch(/marker only/i);
    expect(within(cardFor(P_IDLE.name)).queryByText(/Make active/)).toBeNull();
    await act(async () => {
      fireEvent.click(set);
    });
    expect(H.posts.map((p) => p.path)).toContain("/projects/p2/activate");

    const clear = within(cardFor(P_ACTIVE.name)).getByRole("button", { name: /Clear focus/ });
    expect(within(cardFor(P_ACTIVE.name)).queryByText(/^\s*Deactivate\s*$/)).toBeNull();
    await act(async () => {
      fireEvent.click(clear);
    });
    expect(H.posts.map((p) => p.path)).toContain("/projects/deactivate");
  });

  it("anti-vacuity: Delete and Archive are still TWO presses", async () => {
    renderCards();
    const card = cardFor(P_ACTIVE.name);
    const del = within(card).getByRole("button", { name: /^Delete$/ });
    fireEvent.click(del);
    expect(H.dels).toEqual([]);
    // The armed label still names the knowledge that goes with it.
    expect(del.textContent).toMatch(/Delete \+ 3 knowledge\?/);
    await act(async () => {
      fireEvent.click(del);
    });
    expect(H.dels).toEqual(["/projects/p1"]);

    const archive = within(cardFor(P_IDLE.name)).getByRole("button", { name: /^Archive$/ });
    fireEvent.click(archive);
    expect(H.patches).toEqual([]);
    expect(archive.textContent).toMatch(/Archive\?/);
    await act(async () => {
      fireEvent.click(archive);
    });
    expect(H.patches).toEqual([{ path: "/projects/p2", body: { status: "archived" } }]);
  });

  it("anti-vacuity: an archived card keeps Unarchive (one press) + two-press Delete, and no Chat", async () => {
    renderCards();
    const card = cardFor(P_ARCHIVED.name);
    expect(within(card).queryByRole("link", { name: /^\s*Chat\s*$/ })).toBeNull();
    const un = within(card).getByRole("button", { name: /Unarchive/ });
    await act(async () => {
      fireEvent.click(un);
    });
    expect(H.patches).toContainEqual({ path: "/projects/p3", body: { status: "active" } });
    expect(within(card).getByRole("button", { name: /^Delete$/ })).toBeInTheDocument();
  });

  it("anti-vacuity: the whole card still opens the workspace, and the filter still filters", () => {
    renderCards();
    expect(screen.getByRole("link", { name: `Open ${P_IDLE.name} workspace` }).getAttribute("href")).toBe(
      "/projects/p2",
    );
    const filter = screen.getByLabelText("Filter projects by name");
    fireEvent.change(filter, { target: { value: "web" } });
    expect(screen.queryByRole("link", { name: `Open ${P_ACTIVE.name} workspace` })).toBeNull();
    expect(screen.getByRole("link", { name: `Open ${P_IDLE.name} workspace` })).toBeInTheDocument();
  });
});

/* ========================================================================== */
/*  2. Project header: a labelled model select                                 */
/* ========================================================================== */

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

const RUN_A = {
  id: "s-a",
  task: "Reconcile the March invoices",
  agent_type: "coder",
  provider: "mock",
  model: "mock",
  status: "completed",
  summary: "Done. Wrote RESULT.md summarizing the task.",
  created_at: ago(3 * HOUR),
};
const RUN_B = {
  ...RUN_A,
  id: "s-b",
  task: "Draft the client welcome letter",
  created_at: ago(2 * DAY),
};

function renderWorkspace(over: Record<string, unknown> = {}, sessions: unknown[] = []) {
  H.models = [
    { provider: "openai", model: "gpt-x", name: "OpenAI", available: true },
    { provider: "ollama", model: "llama3", available: true },
  ];
  H.responses["/projects/p1"] = {
    project: {
      ...P_IDLE,
      id: "p1",
      name: "Q3 Bookkeeping",
      root: "C:\\work\\q3",
      default_provider: "anthropic",
      default_model: "old-1",
      ...over,
    },
    sessions,
  };
  H.responses["/projects/p1/team"] = { project_id: "p1", members: [], team: [], suggestions: [] };
  H.responses["/ltm/sources"] = { active: [] };
  render(<ProjectWorkspacePage params={fakeParams("p1")} />);
}

function modelSelect(): HTMLSelectElement {
  return screen.getByRole("combobox", { name: /model/i }) as HTMLSelectElement;
}

describe("project header — the model select says what it is", () => {
  it("has a VISIBLE 'Model' label tied to the select", async () => {
    renderWorkspace();
    await screen.findByText(/Open in Chat/);
    const sel = modelSelect();
    const labels = Array.from(sel.labels ?? []);
    expect(labels.length, "no <label> is associated with the model select").toBeGreaterThan(0);
    expect(labels.some((l) => /^\s*Model\b/.test(l.textContent ?? ""))).toBe(true);
    // An aria-label would override the visible label as the accessible name.
    expect(sel.getAttribute("aria-label")).toBeNull();
  });

  it("the empty choice says what it means: 'Same as app default' (value stays '')", async () => {
    renderWorkspace();
    await screen.findByText(/Open in Chat/);
    const empty = Array.from(modelSelect().options).find((o) => o.value === "");
    expect(empty?.textContent).toBe("Same as app default");
    // The short label is true for chats and "run now" tasks; the title must
    // carry the agent caveat (assignments/dispatcher.py falls back to the
    // AGENT's own model, not the app default, when nothing is pinned).
    const title = modelSelect().getAttribute("title") ?? "";
    expect(title).toMatch(/app's default model/i);
    expect(title).toMatch(/agent uses that agent's own model unless you pin/i);
  });

  it("is not a full-width field: w-auto with a max width", async () => {
    renderWorkspace();
    await screen.findByText(/Open in Chat/);
    const cls = modelSelect().className;
    expect(cls).toMatch(/(^|\s)w-auto(\s|$)/);
    expect(cls).toMatch(/(^|\s)max-w-/);
  });

  it("lifecycle (focus, Archive, Delete) sits in its own right-aligned group, apart from Open in Chat", async () => {
    renderWorkspace();
    await screen.findByText(/Open in Chat/);
    const group = screen.getByTestId("project-header-lifecycle");
    expect(group.className).toMatch(/(^|\s)(sm:)?ml-auto(\s|$)/);
    expect(within(group).getByRole("button", { name: /Set as focus|Make active/ })).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: /^Archive$/ })).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: /^Delete$/ })).toBeInTheDocument();
    expect(within(group).queryByText(/Open in Chat/)).toBeNull();
    expect(within(group).queryByTestId("project-team-link")).toBeNull();
  });

  it("anti-vacuity: every option stays (incl. the unavailable pin) and a choice still saves", async () => {
    renderWorkspace();
    await screen.findByText(/Open in Chat/);
    const sel = modelSelect();
    const values = Array.from(sel.options).map((o) => o.value);
    expect(values).toEqual(["", "anthropic::old-1", "openai::gpt-x", "ollama::llama3"]);
    expect(Array.from(sel.options).find((o) => o.value === "anthropic::old-1")?.textContent).toMatch(
      /unavailable/,
    );
    expect(sel.value).toBe("anthropic::old-1");
    await act(async () => {
      fireEvent.change(sel, { target: { value: "openai::gpt-x" } });
    });
    expect(H.patches).toContainEqual({
      path: "/projects/p1",
      body: { default_provider: "openai", default_model: "gpt-x" },
    });
    await act(async () => {
      fireEvent.change(sel, { target: { value: "" } });
    });
    expect(H.patches).toContainEqual({
      path: "/projects/p1",
      body: { default_provider: "", default_model: "" },
    });
  });

  it("anti-vacuity: Open in Chat, Team & objectives and two-press Delete stay in the header", async () => {
    renderWorkspace();
    const chat = await screen.findByText(/Open in Chat/);
    expect(chat.closest("a")?.getAttribute("href")).toBe("/chat?project=p1");
    expect(screen.getByTestId("project-team-link")).toBeInTheDocument();
    const del = screen.getAllByRole("button", { name: /^Delete$/ })[0];
    fireEvent.click(del);
    expect(H.dels).toEqual([]);
    expect(del.textContent).toMatch(/Delete from app\?/);
  });

  it("end to end: the project's recent runs read apart on the page itself", async () => {
    renderWorkspace({}, [RUN_A, RUN_B]);
    await screen.findByText(/Open in Chat/);
    // The Tasks tab (the default) shows the runs — the rows are there today,
    // they just all read "Done. Wrote RESULT.md…".
    expect(await screen.findByText(/Recent runs/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Wrote RESULT\.md/).length).toBeGreaterThan(0);
    expect(screen.getByText(RUN_A.task)).toBeInTheDocument();
    expect(screen.getByText(RUN_B.task)).toBeInTheDocument();
  });
});

/* ========================================================================== */
/*  3. Phone: the Deliverable select keeps a readable width                    */
/* ========================================================================== */

describe("Run a task — the Deliverable select does not collapse on a phone", () => {
  it("the select keeps a width floor or its own line below sm (not a bare min-w-0 flex-1)", () => {
    render(<ProjectTasks projectId="proj_1" hasRoot sessions={[]} />);
    const cls = screen.getByLabelText("Deliverable").className;
    expect(cls, cls).toMatch(
      /(^|\s)(basis-full|min-w-\[(?:[89]|1\d|2\d)rem\]|min-w-\[(?:1[2-9]\d|[2-9]\d\d)px\]|min-w-(?:3[2-9]|[4-9]\d))(\s|$)/,
    );
  });

  it("the projects filter takes its own line below sm (measured 196px at 390 today; the finding wants >= 200)", () => {
    H.responses["/projects"] = { projects: [P_ACTIVE, P_IDLE] };
    render(<ProjectsPage />);
    const wrap = screen.getByLabelText("Filter projects by name").parentElement as HTMLElement;
    const t = wrap.className.split(/\s+/);
    expect(t, wrap.className).toContain("basis-full");
    expect(t.some((c) => /^sm:(basis-auto|basis-0|flex-1)$/.test(c)), wrap.className).toBe(true);
    // The v1.313.0 fix stays: the magnifier paints above the field.
    expect(t).toContain("isolate");
    expect(t).toContain("relative");
  });

  it("anti-vacuity: every deliverable choice stays, in order, file ones gated on a folder", () => {
    render(<ProjectTasks projectId="proj_1" hasRoot={false} sessions={[]} />);
    const sel = screen.getByLabelText("Deliverable") as HTMLSelectElement;
    expect(Array.from(sel.options).map((o) => o.value)).toEqual([
      "chat", "md", "docx", "xlsx", "pdf", "txt", "csv", "pptx", "html",
    ]);
    expect(sel.options[0].textContent).toBe("Reply in chat");
    expect(Array.from(sel.options).filter((o) => o.disabled)).toHaveLength(8);
    expect(screen.getByLabelText("Assign to")).toBeInTheDocument();
  });

  it("end to end: a run still plans, asks for the tool grant, and posts the chosen deliverable", async () => {
    H.postResponses["/projects/proj_1/task/plan"] = {
      tools: [{ perm_key: "write_file", name: "write_file", why: "save the report" }],
    };
    H.postResponses["/projects/proj_1/task"] = {
      id: "s-new",
      task: "summarize",
      status: "active",
      output: "md",
      created_at: ago(0),
    };
    render(<ProjectTasks projectId="proj_1" hasRoot sessions={[]} />);
    fireEvent.change(screen.getByLabelText("Task for an agent in this project"), {
      target: { value: "summarize every PDF" },
    });
    fireEvent.change(screen.getByLabelText("Deliverable"), { target: { value: "md" } });
    expect(screen.getByLabelText("Deliverable filename")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^\s*Run\s*$/ }));
    });
    // The consent step is still there — nothing ran yet.
    const allow = await screen.findByRole("button", { name: /Allow all & run/ });
    expect(H.posts.map((p) => p.path)).toEqual(["/projects/proj_1/task/plan"]);
    await act(async () => {
      fireEvent.click(allow);
    });
    await waitFor(() =>
      expect(H.posts).toContainEqual({
        path: "/projects/proj_1/task",
        body: { text: "summarize every PDF", output: "md", allow_tools: ["write_file"] },
      }),
    );
  });
});

/* ========================================================================== */
/*  4. Recent runs: task first, summary second, and when                       */
/* ========================================================================== */

function runRows(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLAnchorElement>('a[href^="/sessions/"]')).filter(
    (a) => a.closest("li") !== null,
  );
}

describe("Recent runs — rows that read apart", () => {
  it("two runs with the SAME summary show their different tasks", () => {
    render(
      <ProjectTasks projectId="proj_1" hasRoot sessions={[RUN_A, RUN_B] as unknown as SessionView[]} />,
    );
    expect(screen.getByText(RUN_A.task)).toBeInTheDocument();
    expect(screen.getByText(RUN_B.task)).toBeInTheDocument();
  });

  it("each row says WHEN (the sessions table's relative time)", () => {
    render(
      <ProjectTasks projectId="proj_1" hasRoot sessions={[RUN_A, RUN_B] as unknown as SessionView[]} />,
    );
    const rows = runRows();
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain(timeAgo(RUN_A.created_at)); // "3h ago"
    expect(rows[1].textContent).toContain(timeAgo(RUN_B.created_at)); // "2d ago"
  });

  it("the task leads: it comes BEFORE the summary in the row", () => {
    render(<ProjectTasks projectId="proj_1" hasRoot sessions={[RUN_A] as unknown as SessionView[]} />);
    const t = runRows()[0].textContent ?? "";
    expect(t.indexOf(RUN_A.task)).toBeGreaterThanOrEqual(0);
    expect(t.indexOf(RUN_A.task)).toBeLessThan(t.indexOf("Wrote RESULT.md"));
  });

  it("'open →' also shows on keyboard focus, not only on hover", () => {
    render(<ProjectTasks projectId="proj_1" hasRoot sessions={[RUN_A] as unknown as SessionView[]} />);
    const open = within(runRows()[0]).getByText(/open →/);
    expect(open.className).toMatch(/group-focus-visible:opacity-100|group-focus:opacity-100/);
  });

  it("anti-vacuity: the summary is still its OWN plain-text span (not tooltip-only), status + link stay", () => {
    const md = { ...RUN_A, id: "s-md", summary: "**Done** — wrote *report.md*" };
    render(<ProjectTasks projectId="proj_1" hasRoot sessions={[md] as unknown as SessionView[]} />);
    // markdown-surfaces-v1230 pins exactly this: an element whose text is the stripped summary.
    const s = screen.getByText(/wrote report\.md/);
    expect(s.textContent).toBe("Done — wrote report.md");
    const row = runRows()[0];
    expect(row.getAttribute("href")).toBe("/sessions/s-md");
    expect(row.textContent ?? "").toMatch(/completed/i);
  });

  it("anti-vacuity: a run with no summary prints its task once (no empty or doubled line)", () => {
    const bare = { ...RUN_A, id: "s-bare", summary: "" };
    render(<ProjectTasks projectId="proj_1" hasRoot sessions={[bare] as unknown as SessionView[]} />);
    expect(screen.getAllByText(RUN_A.task)).toHaveLength(1);
  });

  it("anti-vacuity: still at most 8 rows", () => {
    const many = Array.from({ length: 11 }, (_, i) => ({ ...RUN_A, id: `s-${i}`, task: `job ${i}` }));
    render(<ProjectTasks projectId="proj_1" hasRoot sessions={many as unknown as SessionView[]} />);
    expect(runRows()).toHaveLength(8);
  });
});

/* ========================================================================== */
/*  Source pins                                                                */
/* ========================================================================== */

describe("source pins (CRLF-normalised)", () => {
  it("the projects page no longer carries the decorative icon strip", () => {
    const s = src("app/projects/page.tsx");
    expect(s).not.toMatch(/<ListChecks size=\{11\} \/> Tasks/);
    expect(s).not.toMatch(/<SquareKanban size=\{11\} \/> Board/);
  });

  it("Archive/Delete on the card and in the header are still ConfirmButtons", () => {
    const list = src("app/projects/page.tsx");
    const ws = src("app/projects/[id]/page.tsx");
    for (const s of [list, ws]) {
      expect(s).toMatch(/<ConfirmButton[\s\S]{0,400}label="Archive"/);
      expect(s).toMatch(/<ConfirmButton[\s\S]{0,400}label="Delete"/);
    }
  });
});

/* ========================================================================== */
/*  4b. Recent runs show what the user ASKED, not the prompt the route built  */
/*      (review fix round). These are the LITERAL strings                     */
/*      routes/projects.py::run_project_task stores as Session.task, and the  */
/*      dispatcher's "Why this was assigned" suffix; the backend half is      */
/*      pinned by tests/test_project_task_prompt_shape_v1315.py.              */
/* ========================================================================== */

const { runAsk } = await import("@/components/project/ProjectTasks");

const FOLDER_LINE =
  "You are working directly inside the project folder — it is your current directory. " +
  "Read and create files here with plain relative paths.";
const CHAT_DELIVERABLE =
  "Deliverable: a clear, complete written answer in your final summary — the summary IS " +
  "the deliverable. Don't create files unless the task itself requires them.";
const FILE_DELIVERABLE =
  "Deliverable: write the result to 'tidy-the-folder.md' (in this folder) using the " +
  "write_document tool — markdown headings/lists/tables become real structure in " +
  "docx/pdf/pptx/html; pass a list of rows for xlsx/csv. State the saved file in your final summary.";
const WORK_LINE =
  "Work autonomously to completion — make reasonable choices instead of asking questions.";
// "\n".join(lines) exactly as the route builds it.
const built = (text: string, folder: boolean, deliverable = CHAT_DELIVERABLE): string =>
  [...(folder ? [FOLDER_LINE] : []), `Task: ${text}`, "", deliverable, WORK_LINE].join("\n");

const FOLDER_TASK = built("tidy the folder", true);
const FILE_TASK = built("tidy the folder", true, FILE_DELIVERABLE);
const NO_FOLDER_TASK = built("tidy the folder", false);
const QUEUED_TASK = `${built("tidy the folder", true)}\n\nWhy this was assigned: project task`;

describe("runAsk — the user's words out of a project task's prompt", () => {
  it.each([
    ["folder, chat deliverable", FOLDER_TASK],
    ["folder, file deliverable", FILE_TASK],
    ["no folder", NO_FOLDER_TASK],
    ["queued for an agent (Why this was assigned)", QUEUED_TASK],
    ["CRLF", FOLDER_TASK.replace(/\n/g, "\r\n")],
  ])("%s → 'tidy the folder'", (_label, task) => {
    expect(runAsk(task)).toBe("tidy the folder");
  });

  it("keeps a multi-line ask whole", () => {
    expect(runAsk(built("first line\nsecond line", true))).toBe("first line\nsecond line");
  });

  it("a 'Deliverable:' the USER typed stays theirs (the route's own line is the last one)", () => {
    expect(runAsk(built("list them\n\nDeliverable: a table", false))).toBe(
      "list them\n\nDeliverable: a table",
    );
  });

  it("anti-vacuity: any other task is returned unchanged", () => {
    for (const t of ["Reconcile the March invoices", "Summarize: Task list for Monday", ""]) {
      expect(runAsk(t)).toBe(t);
    }
  });
});

describe("Recent runs — a folder project's rows lead with the ask, never the preamble", () => {
  const RUNS = [
    { ...RUN_A, id: "s-f", task: FOLDER_TASK },
    { ...RUN_A, id: "s-n", task: built("draft the welcome letter", false) },
    { ...RUN_A, id: "s-q", task: `${built("file the receipts", true)}\n\nWhy this was assigned: project task` },
  ];

  it("each row shows the user's words and none of the route's prompt", () => {
    render(<ProjectTasks projectId="proj_1" hasRoot sessions={RUNS as unknown as SessionView[]} />);
    const rows = runRows();
    expect(rows).toHaveLength(3);
    expect(screen.getByText("tidy the folder")).toBeInTheDocument();
    expect(screen.getByText("draft the welcome letter")).toBeInTheDocument();
    expect(screen.getByText("file the receipts")).toBeInTheDocument();
    for (const row of rows) {
      const t = row.textContent ?? "";
      expect(t).not.toContain("You are working directly");
      expect(t).not.toContain("Deliverable:");
      expect(t).not.toContain("Work autonomously");
      expect(t).not.toContain("Why this was assigned");
      expect(row.getAttribute("title") ?? "").not.toContain("You are working directly");
      expect(row.getAttribute("title") ?? "").not.toContain("Deliverable:");
    }
    expect(rows[0].getAttribute("title")).toBe("tidy the folder");
  });
});
