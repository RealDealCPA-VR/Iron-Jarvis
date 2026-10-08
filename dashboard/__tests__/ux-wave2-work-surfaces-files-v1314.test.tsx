/**
 * v1.314.0 — UX wave 2 ("words & empty states"), track T2: Documents,
 * Artifacts (saved scripts), Projects and Creative.
 *
 * WHAT THE USER SAW:
 *  - Documents (fresh__documents): "Uploads to the daemon…", "Saved under the
 *    daemon's documents folder", "Absolute or relative path", THREE different
 *    "Browse" spellings for two different behaviours (the drop zone uploads a
 *    copy, the button reads in place), and Redact placeholders that read like
 *    a real client's data.
 *  - Artifacts (fresh__artifacts): "Scripts · 0", an empty state naming the
 *    internal tool `run_code`, tiles saying "exit 0" / "never run", a raw
 *    origin badge, and "Run again" on a script that never ran.
 *  - Projects (fresh__projects): a bare form; nothing says what a project is.
 *  - Creative (fresh__creative): the empty Creations card only offers "Open
 *    Chat" while the page's own Create studio sits above it, and the subtitle
 *    says "arm the pixio tools with the + menu".
 *
 * Anti-vacuity (every describe): the read/redact pickers still OPEN; the
 * format list stays; Run and the two-press Delete stay; the exit code and the
 * raw origin stay reachable in a title; the project form, its fields and
 * Create project stay; "Open Chat" stays as a link to /chat.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const hooks = vi.hoisted(() => {
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
    posts: [] as { path: string; body?: unknown }[],
    search: "",
  };
});

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));
vi.mock("@/lib/api", () => ({
  API_BASE: "http://test",
  ApiError: hooks.FakeApiError,
  ijToken: () => "",
  setIjToken: () => {},
  onUnauthorizedChange: () => () => {},
  wsUrl: (p: string) => `ws://test${p}`,
  sseUrl: (p: string) => `http://test${p}`,
  get: (path: string) => {
    const r = hooks.responses[path];
    return r === undefined
      ? Promise.reject(new hooks.FakeApiError(`unmocked GET ${path}`, 404))
      : Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    hooks.posts.push({ path, body });
    return Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
  api: () => Promise.resolve({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(hooks.search),
  usePathname: () => "/",
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
      <p data-testid="page-subtitle">{subtitle}</p>
      {actions}
    </div>
  ),
}));
// The real picker browses drives; here it only proves which picker OPENED.
vi.mock("@/components/FilePickerModal", () => ({
  FilePickerModal: ({ open, title }: { open: boolean; title?: string }) =>
    open ? <div data-testid="picker">{title}</div> : null,
}));
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial", "animate", "exit", "transition", "variants", "layout",
    "whileHover", "whileTap", "whileInView", "viewport",
  ]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

const DocumentsPage = (await import("@/app/documents/page")).default;
const ArtifactsPage = (await import("@/app/artifacts/page")).default;
const ProjectsPage = (await import("@/app/projects/page")).default;
const CreativePage = (await import("@/app/creative/page")).default;

const src = (rel: string): string => readFileSync(join(process.cwd(), rel), "utf8").replace(/\r\n/g, "\n");

beforeEach(() => {
  hooks.search = "";
  hooks.posts = [];
  hooks.responses = { "/documents/live": { docs: [] }, "/helpdocs": { docs: [] } };
  window.localStorage.clear();
  window.HTMLElement.prototype.scrollTo = vi.fn();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/* ========================================================================== */
/*  Documents                                                                  */
/* ========================================================================== */

describe("Documents: no 'daemon', one name per Browse behaviour", () => {
  it("the page never says 'daemon' or 'Absolute or relative'", () => {
    render(<DocumentsPage />);
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/daemon/i);
    expect(text).not.toMatch(/Absolute or relative/i);
    // Anti-vacuity: the formats the reader handles are still listed.
    expect(text).toMatch(/PDF, Word, Excel, PowerPoint/);
  });

  it("no control reads plain 'Browse'; the drop zone says 'choose a file'", () => {
    render(<DocumentsPage />);
    expect(screen.queryAllByText(/^\s*Browse(…|\.\.\.)?\s*$/)).toHaveLength(0);
    const zone = screen.getByText(/Drop a file here/).closest("div") as HTMLElement;
    expect(zone.textContent ?? "").toMatch(/choose a file/i);
  });

  it("both in-place folder pickers are 'Pick from folders…' and each still opens ITS picker", () => {
    render(<DocumentsPage />);
    const pickers = screen.getAllByRole("button", { name: /Pick from folders/ });
    expect(pickers).toHaveLength(2);
    fireEvent.click(pickers[0]);
    expect(screen.getByTestId("picker")).toHaveTextContent("Pick a file to read");
    cleanup();
    render(<DocumentsPage />);
    fireEvent.click(screen.getAllByRole("button", { name: /Pick from folders/ })[1]);
    expect(screen.getByTestId("picker")).toHaveTextContent("Pick a document to redact");
  });

  it("the Redact examples read as examples ('e.g.'), not as a real client's data", () => {
    render(<DocumentsPage />);
    const doc = screen.getByLabelText("Document to redact") as HTMLInputElement;
    const names = screen.getByLabelText("Extra terms to flag") as HTMLInputElement;
    expect(doc.placeholder).toMatch(/^e\.g\./);
    expect(names.placeholder).toMatch(/^e\.g\./);
  });

  it("source: every field label shares one style (no sentence-case text-xs labels)", () => {
    const s = src("app/documents/page.tsx");
    expect(s).not.toMatch(/<label className="text-xs text-zinc-400"/);
  });
});

/* ========================================================================== */
/*  Artifacts → Saved scripts                                                  */
/* ========================================================================== */

type Art = {
  id: string;
  name: string;
  language: string;
  description: string;
  origin: string;
  session_id: string | null;
  project_id: string | null;
  run_count: number;
  last_exit_code: number | null;
  last_run_at: string | null;
  updated_at: string | null;
  size: number;
};

function art(id: string, over: Partial<Art> = {}): Art {
  return {
    id,
    name: `run_${id}`,
    language: "python",
    description: `Totals the ${id} column`,
    origin: "run_code",
    session_id: null,
    project_id: null,
    run_count: 0,
    last_exit_code: null,
    last_run_at: null,
    updated_at: "2026-10-01T00:00:00Z",
    size: 120,
    ...over,
  };
}

function seedArtifacts(items: Art[]) {
  hooks.responses["/code-artifacts"] = { artifacts: items, count: items.length };
  for (const a of items) {
    hooks.responses[`/code-artifacts/${a.id}`] = { ...a, source: "print(1)", last_output: "" };
  }
}

describe("Artifacts: plain words for what a saved script is and how it went", () => {
  it("the empty state names no internal tool and says what lands here", () => {
    seedArtifacts([]);
    render(<ArtifactsPage />);
    const empty = screen.getByTestId("empty-state");
    expect(document.body.textContent ?? "").not.toMatch(/run_code/);
    expect(empty.textContent ?? "").toMatch(/script|program/i);
    expect(screen.queryByText(/Scripts · 0/)).toBeNull();
  });

  it("a never-run script reads 'Not run yet' and its button is 'Run', not 'Run again'", () => {
    seedArtifacts([art("a1")]);
    render(<ArtifactsPage />);
    expect(screen.getByText("Not run yet")).toBeInTheDocument();
    expect(screen.queryByText(/never run/)).toBeNull();
    fireEvent.click(screen.getByText("Totals the a1 column").closest("button") as HTMLElement);
    expect(screen.getByRole("button", { name: /^\s*Run\s*$/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Run again/ })).toBeNull();
    // Anti-vacuity: the two-press Delete is still offered.
    expect(screen.getByRole("button", { name: /Delete/ })).toBeInTheDocument();
  });

  it("exit codes read 'Worked' / 'Failed'; the number stays in a title", () => {
    seedArtifacts([
      art("ok", { last_exit_code: 0, last_run_at: "2026-10-01T01:00:00Z", run_count: 1 }),
      art("bad", { last_exit_code: 1, last_run_at: "2026-10-01T01:00:00Z", run_count: 1 }),
    ]);
    render(<ArtifactsPage />);
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/exit \d/);
    expect(screen.getByText(/Worked/)).toBeInTheDocument();
    const failed = screen.getByText(/Failed/);
    const holder = failed.closest("[title]");
    expect(holder?.getAttribute("title") ?? "").toMatch(/exit code 1/i);
  });

  it("a script that ran keeps 'Run again'; the origin badge is words with the raw value in a title", () => {
    seedArtifacts([art("r1", { last_exit_code: 0, last_run_at: "2026-10-01T01:00:00Z", run_count: 2 })]);
    render(<ArtifactsPage />);
    fireEvent.click(screen.getByText("Totals the r1 column").closest("button") as HTMLElement);
    expect(screen.getByRole("button", { name: /Run again/ })).toBeInTheDocument();
    // The origin is a record: raw in a title, never as the visible word.
    const raw = Array.from(document.querySelectorAll<HTMLElement>("[title]")).filter((e) =>
      (e.getAttribute("title") ?? "").includes("run_code"),
    );
    expect(raw.length).toBeGreaterThan(0);
    expect(document.body.textContent ?? "").not.toMatch(/run_code/);
  });
});

/* ========================================================================== */
/*  Projects                                                                   */
/* ========================================================================== */

describe("Projects: a fresh page says what a project is for", () => {
  it("with no projects, an explainer sits beside the open form", () => {
    hooks.responses["/projects"] = { projects: [] };
    render(<ProjectsPage />);
    const explainer = screen.getByTestId("projects-explainer");
    const t = explainer.textContent ?? "";
    expect(t).toMatch(/brief/i);
    expect(t).toMatch(/folder/i);
    // Anti-vacuity: the form, its fields and its button are unchanged.
    expect(screen.getByLabelText("Project name")).toBeInTheDocument();
    expect(screen.getByLabelText("Project brief")).toBeInTheDocument();
    expect(screen.getByLabelText("Project folder root")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Create project/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Browse for a project folder/ })).toBeInTheDocument();
  });

  it("with a project, the explainer is gone", () => {
    window.localStorage.setItem("ij.projects.view", "list");
    hooks.responses["/projects"] = {
      projects: [
        {
          id: "p1",
          name: "Q3 season",
          brief: "",
          root: null,
          status: "active",
          created_at: "2026-10-01T00:00:00Z",
          updated_at: "2026-10-01T00:00:00Z",
        },
      ],
    };
    render(<ProjectsPage />);
    expect(screen.queryByTestId("projects-explainer")).toBeNull();
  });

  it("source: no firm or client name in the example", () => {
    const s = src("app/projects/page.tsx");
    expect(s).not.toMatch(/Alvarez|Northwind/);
  });
});

/* ========================================================================== */
/*  Creative                                                                   */
/* ========================================================================== */

const ITEMS_PATH = "/creative/items?limit=500";

describe("Creative: the empty gallery offers the page's own Create studio", () => {
  it("a 'Make something' button opens the Create tab; Open Chat stays a link", async () => {
    hooks.responses[ITEMS_PATH] = { items: [], count: 0 };
    render(<CreativePage />);
    const empty = screen.getByTestId("empty-state");
    const make = within(empty).getByRole("button", { name: /make something/i });
    // Anti-vacuity: the Chat door is still a real link.
    expect(within(empty).getByRole("link", { name: /Open Chat/ }).getAttribute("href")).toBe("/chat");
    fireEvent.click(make);
    await waitFor(() => expect(screen.getByRole("tab", { name: /Create/ })).toHaveAttribute("aria-selected", "true"));
  });

  it("the Creations subtitle names no internal tool or '+ menu'", () => {
    hooks.responses[ITEMS_PATH] = { items: [], count: 0 };
    render(<CreativePage />);
    const sub = screen.getByTestId("page-subtitle").textContent ?? "";
    expect(sub).not.toMatch(/pixio/i);
    expect(sub).not.toMatch(/\+ menu/);
    expect(sub).toMatch(/Create/);
  });
});
