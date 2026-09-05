/**
 * v1.230.0 (audit Wave 4, U2) — three surfaces, one markdown renderer.
 *
 * The session detail's summary card and a project's "Recent runs" rows used to
 * print the model's markdown RAW — `**Done** — wrote *report.md*` with every
 * asterisk showing — because chat was the only surface that owned a renderer.
 * `components/Markdown.tsx` is that renderer, lifted out of chat/page.tsx:
 *  - `Markdown` renders `**bold**` as a <strong>, no literal asterisks;
 *  - the session summary card renders through it;
 *  - a one-line project row goes through `plainText` (markers stripped, words
 *    kept — a truncated row has no room for block layout).
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

/* ---- shared api seam (session page + project rows) ----------------------- */
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
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    }
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

/* ---- session detail page harness (mirrors session-files-v1168's) --------- */
const pageApi = vi.hoisted(() => ({
  detail: null as unknown,
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path && /^\/sessions\/[^/]+$/.test(path) ? pageApi.detail : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: () => ({
    data: null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({
    text: "",
    tools: [],
    phase: null,
    active: false,
    start: () => {},
    stop: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [] }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    enabled: false,
    supported: false,
    toggle: () => {},
    speak: () => {},
    stop: () => {},
  }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: () => {} }) }));
vi.mock("@/components/ReviewPanel", () => ({ ReviewPanel: () => null }));
vi.mock("@/components/TracesPanel", () => ({ TracesPanel: () => null }));
vi.mock("@/components/SessionFeedback", () => ({ SessionFeedback: () => null }));
vi.mock("@/components/TimeTravelFeed", () => ({ TimeTravelFeed: () => null }));
vi.mock("@/components/chat/DocPreview", () => ({
  DocPreview: () => null,
  appLabelFor: () => "app",
}));
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (prev: string, chunk: string) => prev + chunk,
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

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial",
    "animate",
    "exit",
    "transition",
    "variants",
    "layout",
    "whileHover",
  ]);
  const tagFor =
    (tag: string) => (props: Record<string, unknown>) => {
      const rest: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(props)) {
        if (!MOTION_ONLY.has(k)) rest[k] = v;
      }
      return createElement(tag, rest);
    };
  const cache = new Map<string, unknown>();
  return {
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

import { Markdown, plainText } from "@/components/Markdown";
import SessionDetailPage from "@/app/sessions/[id]/page";
import { ProjectTasks } from "@/components/project/ProjectTasks";
import type { SessionView } from "@/lib/types";

afterEach(() => {
  cleanup();
  api.calls = [];
  api.responses = {};
  pageApi.detail = null;
  window.localStorage.clear();
});

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

function setDetail(id: string, over: Record<string, unknown> = {}) {
  pageApi.detail = {
    session: {
      id,
      task: `task ${id}`,
      agent_type: "coder",
      provider: "openai",
      model: "gpt-x",
      status: "completed",
      workspace_path: "C:\\ws\\s-1",
      summary: "",
      origin: null,
      created_at: "2026-08-12T10:00:00Z",
      finished_at: "2026-08-12T10:05:00Z",
      ...over,
    },
    transcript: { runs: [], tools: [] },
  };
}

/* ------------------------------------------------------------------------ */
describe("components/Markdown — the one renderer", () => {
  it("renders **bold** as <strong> with no literal asterisks", () => {
    const { container } = render(<Markdown content={"**Done** — wrote *report.md*"} />);
    const strong = container.querySelector("strong");
    expect(strong).not.toBeNull();
    expect(strong!.textContent).toBe("Done");
    expect(container.querySelector("em")!.textContent).toBe("report.md");
    expect(container.textContent).not.toContain("*");
  });

  it("plainText strips markers, keeps words, and leaves snake_case alone", () => {
    expect(plainText("**Done** — wrote *report.md* in `out/`")).toBe(
      "Done — wrote report.md in out/",
    );
    expect(plainText("## Result\n- ran `write_file` and read_file\n- [docs](http://x)")).toBe(
      "Result ran write_file and read_file docs",
    );
    expect(plainText("```py\nprint(1)\n```\n> quoted _word_ ~~gone~~")).toBe(
      "print(1) quoted word gone",
    );
    expect(plainText("")).toBe("");
  });
});

describe("session detail — the summary card renders markdown", () => {
  it("a '**bold**' summary shows a <strong>, never the asterisks", async () => {
    setDetail("s-1", { summary: "**Finished** — wrote *report.md* and 2 more" });
    render(<SessionDetailPage params={fakeParams("s-1")} />);
    const card = await screen.findByTestId("session-summary");
    expect(card.querySelector("strong")!.textContent).toBe("Finished");
    expect(card.textContent).toContain("report.md");
    expect(card.textContent).not.toContain("*");
  });
});

describe("project Recent runs — a one-line row without markers", () => {
  it("a '**bold**' summary row prints the words only", async () => {
    const done: SessionView = {
      id: "s-old",
      task: "summarize the folder",
      agent_type: "coder",
      provider: "openai",
      model: "gpt-x",
      status: "completed",
      summary: "**Done** — wrote *report.md*",
      created_at: "2026-09-01T00:00:00Z",
    } as unknown as SessionView;
    render(<ProjectTasks projectId="proj_1" hasRoot={false} sessions={[done]} />);
    const row = await screen.findByText(/wrote report\.md/);
    expect(row.textContent).toBe("Done — wrote report.md");
    expect(row.textContent).not.toContain("*");
  });
});
