/**
 * Calm chat wave 4 F1 (v1.329.0): folded work rows say what was done to what.
 *
 * - Each saved step keeps a short SAFE target taken from the call's arguments
 *   on the stream lane: a file's base name, a search cut to ~60 characters,
 *   a page's host, a program's name. Never a full path, never a whole
 *   argument, never anything credential-shaped. It is appended LAST on the
 *   step object.
 * - The unfolded rows read "Read harbor.xlsx", "Searched the web for pier 9
 *   hours", "Ran excel_query"; the live rows use the same words while the
 *   turn runs ("Reading …", "Searching the web for …").
 * - A step saved before v1.329.0 (no target) keeps today's row: "Read ·
 *   read_file".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
  }
  const stream = {
    bodies: [] as Record<string, unknown>[],
    replies: [] as string[],
    extra: [] as Record<string, unknown>[],
    streaming: false,
    version: 0,
    listeners: new Set<() => void>(),
    bump() {
      stream.version += 1;
      for (const l of [...stream.listeners]) l();
    },
  };
  return {
    FakeApiError,
    FakeStreamError,
    stream,
    api: {
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async () => ({}),
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

// The page's stream hook is a double (fixed export list, as every chat-page
// test mocks it); the REAL `stepsFromTools` is exercised on its own below and
// feeds this double, so the page saves exactly what the hook would hand it.
vi.mock("@/lib/useChatStream", async () => {
  const React = await import("react");
  const subscribe = (cb: () => void) => {
    H.stream.listeners.add(cb);
    return () => {
      H.stream.listeners.delete(cb);
    };
  };
  const run = async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
    H.stream.bodies.push(body);
    H.stream.streaming = true;
    H.stream.bump();
    await new Promise<void>((r) => setTimeout(r, 0));
    const reply = H.stream.replies.shift() ?? "done";
    onDelta(reply, reply);
    H.stream.streaming = false;
    H.stream.bump();
    return { reply, ...(H.stream.extra.shift() ?? {}) };
  };
  return {
    StreamError: H.FakeStreamError,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    useChatStream: () => {
      React.useSyncExternalStore(subscribe, () => H.stream.version);
      return {
        streaming: H.stream.streaming,
        text: "",
        thinking: "",
        tools: [],
        mcpAsks: [],
        approval: null,
        phase: H.stream.streaming ? "working" : null,
        run,
        abort: () => {},
      };
    },
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false, reason: null, engine: null, listening: false, processing: false,
    transcript: "", interim: "", error: null, start: () => {}, stop: () => {}, reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: true, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
    readAloud: () => {}, readingKey: null,
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", async () => await vi.importActual("react-markdown"));

import ChatPage from "@/app/chat/page";
import { LiveToolRows, WorkLine, workSteps } from "@/components/chat/WorkLine";
import { cleanTarget, stepTarget, TARGET_MAX } from "@/lib/workTarget";
import type { ToolCard, TurnStep } from "@/lib/useChatStream";

type StreamModule = typeof import("@/lib/useChatStream");
async function realStepsFromTools(cards: ToolCard[]): Promise<TurnStep[]> {
  const real = await vi.importActual<StreamModule>("@/lib/useChatStream");
  return real.stepsFromTools(cards);
}

/* Credential-shaped values. Each is a fake made for this test. */
const KEY = "sk-ant-api03-FAKEfakeFAKEfake1234567890abcd";
const BEARER = "Bearer abcdefghijklmnopqrstuvwxyz0123456789";
const GH = "ghp_FAKE0123456789abcdefghijklmnop";

beforeEach(() => {
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.replies.length = 0;
  H.stream.extra.length = 0;
  H.stream.streaming = false;
  H.api.getResponses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState({}, "", "/chat");
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/** Every piece of text a user could see or hover in a subtree. */
function visibleAndHover(root: Element): string {
  const titles = [...root.querySelectorAll("[title]")].map((e) => e.getAttribute("title") ?? "");
  return `${root.textContent ?? ""}\n${titles.join("\n")}`;
}

/* ------------------------------------------------------------- the target */

describe("a step's target is short and safe", () => {
  it("a file is named by its base name, never its full path", () => {
    expect(stepTarget("read_file", { path: "C:\\Users\\me\\clients\\sales-harbor-st.xlsx" })).toBe("sales-harbor-st.xlsx");
    expect(stepTarget("excel_read", { path: "/home/me/q3/pier-9.xlsx", sheet: "Sheet1" })).toBe("pier-9.xlsx");
    expect(stepTarget("list_folder", { path: "D:\\work\\Q3 returns\\" })).toBe("Q3 returns");
    expect(stepTarget("edit_file", { path: "notes.md", old: "a", new: "b" })).toBe("notes.md");
    // A tool that MAKES a file names the file it made.
    expect(stepTarget("convert_document", { source: "C:\\a\\letter.docx", target: "C:\\a\\letter.pdf" })).toBe("letter.pdf");
    expect(stepTarget("write_document", { path: "C:\\out\\summary.xlsx", content: "rows" })).toBe("summary.xlsx");
    expect(stepTarget("read_document", { paths: ["C:\\a\\one.pdf", "C:\\a\\two.pdf", "C:\\a\\three.pdf"] })).toBe(
      "one.pdf and 2 more",
    );
  });

  it("a search keeps its words on one line, cut to about 60 characters", () => {
    expect(stepTarget("web_search", { query: "pier 9   hours\non sunday" })).toBe("pier 9 hours on sunday");
    expect(stepTarget("grep", { pattern: "\\bTODO\\b" })).toBe("\\bTODO\\b");
    const long = stepTarget("web_search", { query: "word ".repeat(40) })!;
    expect(long.length).toBeLessThanOrEqual(TARGET_MAX);
    expect(long.endsWith("…")).toBe(true);
    // A full path inside a search keeps only its last part.
    expect(stepTarget("grep", { pattern: "C:\\Users\\me\\secret-plans\\budget.xlsx" })).toBe("budget.xlsx");
  });

  it("a web page is named by its site alone", () => {
    expect(stepTarget("web_fetch", { url: "https://www.example.com/a/b?token=abc123#x" })).toBe("example.com");
    expect(stepTarget("web_fetch", { url: "https://user:hunter2@files.example.org:8443/x" })).toBe("files.example.org");
    expect(stepTarget("web_fetch", { url: "file:///C:/secret.txt" })).toBeNull();
    expect(stepTarget("web_fetch", { url: "not a url" })).toBeNull();
  });

  it("a command is named by its program alone, never its arguments", () => {
    expect(stepTarget("shell", { command: `curl -H "Authorization: ${BEARER}" https://api.example.com` })).toBe("curl");
    expect(stepTarget("shell", { command: '"C:\\Program Files\\Git\\bin\\git.exe" status' })).toBe("git");
    expect(stepTarget("shell", { command: `API_KEY=${KEY} ./run.sh` })).toBeNull();
    expect(stepTarget("run_code", { language: "Python", code: `print("${KEY}")` })).toBe("python");
    expect(stepTarget("repl", { code: "x = 1" })).toBeNull();
  });

  it("a tool the app does not know by its exact name gets NO target", () => {
    expect(stepTarget("excel_query", { path: "C:\\a\\b.xlsx", column: "Revenue" })).toBeNull();
    expect(stepTarget("mcp__files__scan", { path: "C:\\a\\b.xlsx" })).toBeNull();
    expect(stepTarget("read_file_backup", { path: "C:\\a\\b.xlsx" })).toBeNull();
  });

  it("a credential-shaped value is dropped whole, never shown in part", () => {
    expect(stepTarget("web_search", { query: `is ${KEY} still valid` })).toBeNull();
    expect(stepTarget("web_search", { query: `github ${GH}` })).toBeNull();
    expect(stepTarget("web_search", { query: "login password=hunter2" })).toBeNull();
    expect(stepTarget("grep", { pattern: "api_key: abcdef" })).toBeNull();
    expect(stepTarget("web_search", { query: "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8" })).toBeNull();
    expect(stepTarget("read_file", { path: `C:\\keys\\${GH}.txt` })).toBeNull();
    // Words joined by "_" or "-" are a file name, not a key.
    expect(stepTarget("read_file", { path: "C:\\q\\quarterly_report_2026_q3_final_version_draft.xlsx" })).toBe(
      "quarterly_report_2026_q3_final_version_draft.xlsx",
    );
    // A saved target is checked again on the way back out.
    expect(cleanTarget(`pier ${KEY}`)).toBeNull();
    expect(cleanTarget(42)).toBeNull();
    expect(cleanTarget("  ")).toBeNull();
  });
});

/* ------------------------------------------------------------- the stream */

describe("the stream hook keeps the target on each step, last", () => {
  it("stepsFromTools appends `target` after name/ok/ms, and only when there is one", async () => {
    const steps = await realStepsFromTools([
      { id: "a", name: "read_file", status: "done", ok: true, args: { path: "C:\\work\\harbor.xlsx" }, startedAt: 1, endedAt: 301 },
      { id: "b", name: "excel_query", status: "done", ok: true, args: { path: "C:\\work\\harbor.xlsx" } },
      { id: "c", name: "web_search", status: "done", ok: true, args: { query: `look up ${KEY}` } },
    ]);
    expect(steps[0]).toEqual({ name: "read_file", ok: true, ms: 300, target: "harbor.xlsx" });
    expect(Object.keys(steps[0])).toEqual(["name", "ok", "ms", "target"]);
    expect(Object.keys(steps[1])).toEqual(["name", "ok", "ms"]);
    expect(Object.keys(steps[2])).toEqual(["name", "ok", "ms"]);
    expect(JSON.stringify(steps)).not.toContain("work\\\\harbor");
    expect(JSON.stringify(steps)).not.toContain(KEY);
  });
});

/* ------------------------------------------------------------ saved rows */

const SAVED: TurnStep[] = [
  { name: "read_file", ok: true, ms: 300, target: "sales-harbor-st.xlsx" },
  { name: "web_search", ok: true, ms: 900, target: "pier 9 hours" },
  { name: "web_fetch", ok: true, ms: 400, target: "example.com" },
  { name: "excel_query", ok: true, ms: 1200 },
  { name: "write_document", ok: false, ms: 50, target: "summary.xlsx" },
];

function openRows() {
  fireEvent.click(screen.getByRole("button", { expanded: false }));
  return within(screen.getByTestId("work-rows")).getAllByTestId("work-row");
}

describe("unfolded rows say what was done to what", () => {
  it("in plain words, the file underlined, the tool id on hover", () => {
    render(<WorkLine steps={SAVED} />);
    const rows = openRows();
    expect(rows.map((r) => r.textContent)).toEqual([
      "Read sales-harbor-st.xlsx0.3 s",
      "Searched the web for pier 9 hours0.9 s",
      "Read a page on example.com0.4 s",
      "Ran excel_query1.2 s",
      "Made summary.xlsx0.1 sfailed",
    ]);
    const file = within(rows[0]).getByTestId("work-target");
    expect(file.textContent).toBe("sales-harbor-st.xlsx");
    expect(file.getAttribute("class")).toMatch(/decoration-dotted/);
    expect(file.getAttribute("title")).toBe("sales-harbor-st.xlsx (read_file)");
    // A search is not a file: no underline.
    expect(within(rows[1]).getByTestId("work-target").getAttribute("class")).not.toMatch(/underline/);
    expect(rows[4].getAttribute("data-state")).toBe("failed");
  });

  it("a step saved before targets existed keeps today's row", () => {
    render(<WorkLine steps={[{ name: "read_file", ok: true, ms: 300 }, { name: "web_search", ok: true, ms: 100 }]} />);
    const rows = openRows();
    expect(rows.map((r) => r.textContent)).toEqual(["Readread_file0.3 s", "Searched the webweb_search0.1 s"]);
    expect(screen.queryByTestId("work-target")).toBeNull();
  });

  it("a saved target that looks like a credential is never shown (falls back to the id)", () => {
    const steps = [
      { name: "web_search", ok: true, ms: 100, target: `find ${KEY}` },
      { name: "read_file", ok: true, ms: 100, target: { evil: true } as unknown as string },
    ];
    expect(workSteps({ steps }).map((s) => s.target)).toEqual([undefined, undefined]);
    const { container } = render(<WorkLine steps={steps} />);
    const rows = openRows();
    expect(rows.map((r) => r.textContent)).toEqual(["Searched the webweb_search0.1 s", "Readread_file0.1 s"]);
    expect(visibleAndHover(container)).not.toContain(KEY);
  });
});

/* ------------------------------------------------------------- live rows */

describe("live rows use the same words", () => {
  it("running and done, with the subject named", () => {
    const cards: ToolCard[] = [
      { id: "a", name: "read_file", status: "done", ok: true, args: { path: "C:\\work\\harbor.xlsx" }, startedAt: 1, endedAt: 301 },
      { id: "b", name: "web_search", status: "running", args: { query: "pier 9 hours" } },
      { id: "c", name: "excel_query", status: "running", args: { column: "Revenue" } },
    ];
    render(<LiveToolRows cards={cards} />);
    const [read, search, query] = screen.getAllByTestId("work-row");
    expect(read.textContent).toBe("Read harbor.xlsx0.3 s");
    // The live file keeps its full path on hover (this screen only; never saved).
    expect(within(read).getByTestId("work-target").getAttribute("title")).toBe("C:\\work\\harbor.xlsx (read_file)");
    expect(search.textContent).toBe("Searching the web for pier 9 hours");
    expect(query.textContent).toBe("Running excel_query");
  });

  it("a secret-looking argument is never on screen or in a hover", () => {
    const cards: ToolCard[] = [
      { id: "a", name: "web_search", status: "done", ok: true, args: { query: `check ${KEY}` } },
      { id: "b", name: "shell", status: "running", args: { command: `curl -H "Authorization: ${BEARER}" https://x.example` } },
      { id: "c", name: "web_fetch", status: "done", ok: true, args: { url: `https://api.example.com/v1?key=${GH}` } },
    ];
    const { container } = render(<LiveToolRows cards={cards} />);
    const all = visibleAndHover(container);
    for (const secret of [KEY, BEARER, GH, "Authorization"]) expect(all).not.toContain(secret);
    const [search, shell, page] = screen.getAllByTestId("work-row");
    expect(search.textContent).toBe("Searched the webweb_search");
    expect(shell.textContent).toBe("Running curl");
    expect(page.textContent).toBe("Read a page on api.example.com");
  });

  it("a running command and an unknown tool show what they were called with on hover", () => {
    const cards: ToolCard[] = [
      { id: "a", name: "shell", status: "running", args: { command: "git  push origin\nmain" } },
      { id: "b", name: "excel_query", status: "running", args: { path: "C:\\work\\harbor.xlsx", column: "Revenue" } },
      { id: "c", name: "excel_query", status: "running", args: { query: `find ${KEY}` } },
    ];
    render(<LiveToolRows cards={cards} />);
    const [shell, tool, secret] = screen.getAllByTestId("work-row");
    // The visible words are unchanged: the program, the tool id.
    expect(shell.textContent).toBe("Running git");
    expect(tool.textContent).toBe("Running excel_query");
    // The hover is the whole one-line command, then the tool id.
    expect(within(shell).getByTestId("work-target").getAttribute("title")).toBe("git push origin main (shell)");
    expect(within(tool).getByTestId("work-target").getAttribute("title")).toBe("C:\\work\\harbor.xlsx (excel_query)");
    // A credential-shaped argument keeps the older hover: the id alone.
    expect(within(secret).getByTestId("work-target").getAttribute("title")).toBe("excel_query");
    expect(visibleAndHover(secret)).not.toContain(KEY);
  });

  it("the hover is never saved: the step still carries only the program name", async () => {
    const steps = await realStepsFromTools([
      { id: "a", name: "shell", status: "done", ok: true, args: { command: "git push origin main" } },
    ]);
    expect(steps).toEqual([{ name: "shell", ok: true, ms: null, target: "git" }]);
  });
});

/* -------------------------------------------------------------- the page */

describe("on the chat page, the target is saved with the step and read back", () => {
  it("a turn saves each step's target LAST and the folded rows name it", async () => {
    const cards: ToolCard[] = [
      { id: "a", name: "read_file", status: "done", ok: true, args: { path: "C:\\Users\\me\\sales-harbor-st.xlsx" }, startedAt: 1, endedAt: 401 },
      { id: "b", name: "web_search", status: "done", ok: true, args: { query: `pier 9 hours ${KEY}` }, startedAt: 1, endedAt: 201 },
      { id: "c", name: "excel_query", status: "done", ok: true, args: { column: "Revenue" }, startedAt: 1, endedAt: 101 },
    ];
    H.stream.replies.push("Pier 9 is closing the gap.");
    H.stream.extra.push({ steps: await realStepsFromTools(cards), toolsUsed: ["read_file", "web_search", "excel_query"] });
    render(<ChatPage />);
    const el = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(el, { target: { value: "compare them" } });
    fireEvent.keyDown(el, { key: "Enter" });
    // The save that carries the reply (wait for THAT, not for any save).
    await waitFor(() => {
      const saved = H.api.puts.at(-1)?.body as { messages?: { content?: string }[] } | undefined;
      expect(saved?.messages?.some((m) => m.content === "Pier 9 is closing the gap.")).toBe(true);
    });
    const body = H.api.puts.at(-1)!.body as { messages: { role: string; steps?: TurnStep[] }[] };
    const steps = body.messages.find((m) => m.role === "assistant")!.steps!;
    expect(steps).toEqual([
      { name: "read_file", ok: true, ms: 400, target: "sales-harbor-st.xlsx" },
      { name: "web_search", ok: true, ms: 200 },
      { name: "excel_query", ok: true, ms: 100 },
    ]);
    expect(Object.keys(steps[0]).at(-1)).toBe("target");
    const wire = JSON.stringify(H.api.puts.map((p) => p.body));
    expect(wire).not.toContain(KEY);
    expect(wire).not.toContain("Users\\\\me");

    const reply = (await screen.findByText("Pier 9 is closing the gap.")).closest('[data-testid="reply"]') as HTMLElement;
    const work = within(reply).getByTestId("work-line");
    fireEvent.click(within(work).getByRole("button", { expanded: false }));
    const rows = within(work).getAllByTestId("work-row");
    expect(rows.map((r) => r.textContent)).toEqual([
      "Read sales-harbor-st.xlsx0.4 s",
      "Searched the webweb_search0.2 s",
      "Ran excel_query0.1 s",
    ]);
    expect(visibleAndHover(reply)).not.toContain(KEY);
  });
});
