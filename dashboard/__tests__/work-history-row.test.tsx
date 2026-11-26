/**
 * Next leaps 13-b (build-out plan R2.10, idea 13 "Ask the record"): the rows
 * under a reply that looked up earlier work.
 *
 * Pinned here:
 *  - lib/workHistory: the rows are the `work_history` step's OWN result (the
 *    finished frame's `data.rows`, decoded by useChatStream), read as
 *    untrusted input: an address outside the app, a relative path, a
 *    control character or a made-up status word is dropped; one row per
 *    piece, at most 20; nothing from any other tool;
 *  - the row: pressing it opens its chat (the row's own `link`); Files opens
 *    the one file in the panel, or lists several to open; Decisions is a
 *    quiet link to where the recap is read; nothing is drawn when the step
 *    returned no rows;
 *  - the real chat page: a reply whose turn ran the step shows the rows and
 *    SAVES them on the reply; a reopened chat (a reply the daemon wrote home
 *    included) draws them again from what was saved;
 *  - the Memory page: `?work=<id>` opens that piece's folder and brings the
 *    piece into view, by the piece's id (no folder in the address).
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      gets: [] as string[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: {
      bodies: [] as Record<string, unknown>[],
      tools: [] as unknown[],
      /** done: the reply finishes; fail: it fails after the lookup with
       *  partial text; hold: it streams until Stop (review finding 3). */
      mode: "done" as "done" | "fail" | "hold",
      streaming: false,
      text: "",
      version: 0,
      listeners: new Set<() => void>(),
      settle: null as null | ((r: Record<string, unknown>) => void),
      bump() {
        this.version += 1;
        for (const l of [...this.listeners]) l();
      },
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  flattenDetail: (d: unknown) => String(d),
  get: async (path: string) => {
    H.api.gets.push(path);
    const r = H.api.getResponses[path];
    if (typeof r === "function") return (r as () => unknown)();
    if (r instanceof Error) throw r;
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string) => H.api.postResponses[path] ?? {},
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/useChatStream")>();
  const React = await import("react");
  const subscribe = (cb: () => void) => {
    H.stream.listeners.add(cb);
    return () => {
      H.stream.listeners.delete(cb);
    };
  };
  return {
    ...real,
    StreamError: H.FakeStreamError,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    streamSSE: async function* () {},
    useChatStream: () => {
      React.useSyncExternalStore(subscribe, () => H.stream.version);
      return {
        streaming: H.stream.streaming,
        text: H.stream.text,
        tools: H.stream.tools,
        approval: null,
        run: async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
          H.stream.bodies.push(body);
          await new Promise<void>((r) => setTimeout(r, 0));
          if (H.stream.mode === "fail") {
            const e = new H.FakeStreamError("The model stopped answering.", 502);
            e.committed = true;
            e.partial = "I found three pieces of work in March, then";
            throw e;
          }
          if (H.stream.mode === "hold") {
            H.stream.streaming = true;
            H.stream.text = "I found three pieces of work in March so far";
            H.stream.bump();
            return new Promise<Record<string, unknown>>((ok) => {
              H.stream.settle = ok;
            });
          }
          onDelta("Three pieces of work in March.", "Three pieces of work in March.");
          return { reply: "Three pieces of work in March." };
        },
        abort: () => {
          const settle = H.stream.settle;
          H.stream.settle = null;
          H.stream.streaming = false;
          H.stream.bump();
          settle?.({ reply: "" });
        },
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
    supported: false, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
const DAEMON = {
  online: true,
  checking: false,
  health: { default_provider: "mock", default_model: "mock-1", providers: [] },
  refresh: () => {},
};
vi.mock("@/lib/daemon", () => ({ useDaemon: () => DAEMON }));
const MODELS = {
  models: [{ provider: "claude-cli", model: "opus", label: "Opus 5.5", available: true }],
  usable: [{ provider: "claude-cli", model: "opus", label: "Opus 5.5", available: true }],
  loading: false,
};
vi.mock("@/lib/useModels", () => ({ useModels: () => MODELS }));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="tree" /> }));

import ChatPage from "@/app/chat/page";
import { WorkHistoryRow } from "@/components/chat/WorkHistoryRow";
import { WorkByFolder } from "@/components/memory/WorkByFolder";
import { sseEventFrom, upsertTool, type ToolCard } from "@/lib/useChatStream";
import {
  PIECE_GONE_WORDS,
  WORK_ROW_DECISIONS,
  WORK_ROW_FILES,
  decodeWorkRows,
  extractWorkRows,
  workPiecePath,
} from "@/lib/workHistory";
import { WORK_FOLDERS_PATH, workEntriesPath } from "@/lib/workRecord";

const BAKER = "D:\\Clients\\Baker";

/** Three March pieces, as the daemon's `screen_rows` sends them. */
function wireRows(): Record<string, unknown>[] {
  return [
    {
      id: "chat:t3", kind: "chat", title: "Bank feed review", when: "2026-03-29T15:00:00+00:00",
      status: "still open", open: 1, link: "/chat?thread=t3", files: [], paths: [], files_total: 0,
      decisions: "/clients/client_00000000000d", times: 1,
    },
    {
      id: "chat:t2", kind: "chat", title: "Baker sales tax filing", when: "2026-03-17T15:00:00+00:00",
      status: "", open: 0, link: "/chat?thread=t2", files: ["march.xlsx", "notes.docx"],
      paths: [`${BAKER}\\2026\\march.xlsx`, `${BAKER}\\2026\\notes.docx`], files_total: 2, decisions: "", times: 1,
    },
    {
      id: "run:s1", kind: "run", title: "Baker payroll setup", when: "2026-03-04T15:00:00+00:00",
      status: "finished", open: 0, link: "/sessions/s1", files: ["payroll.xlsx"],
      paths: [`${BAKER}\\payroll.xlsx`], files_total: 1, decisions: "/memory?work=run%3As1", times: 1,
    },
  ];
}

function doneCard(rows = wireRows(), name = "work_history"): ToolCard {
  const ev = sseEventFrom("tool_call", { id: "c1", name, status: "finished", ok: true, output: "…", data: { rows } });
  return upsertTool([], ev as Parameters<typeof upsertTool>[1])[0];
}

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.tools = [];
  H.stream.mode = "done";
  H.stream.streaming = false;
  H.stream.text = "";
  H.stream.settle = null;
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
  H.api.postResponses["/documents/folder-check"] = { problem: null, checked: true };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/* ----------------------------------------------------------- lib/workHistory */

describe("the rows are the step's own result", () => {
  it("a finished work_history frame carries its rows onto the step card", () => {
    const card = doneCard();
    expect(card.workRows?.map((r) => r.title)).toEqual(["Bank feed review", "Baker sales tax filing", "Baker payroll setup"]);
    expect(extractWorkRows([card]).map((r) => r.link)).toEqual(["/chat?thread=t3", "/chat?thread=t2", "/sessions/s1"]);
  });

  it("no other step, a step that did not run, or a started frame gives rows", () => {
    expect(doneCard(wireRows(), "history_search").workRows).toBeUndefined();
    expect(extractWorkRows([doneCard(wireRows(), "history_search")])).toEqual([]);
    expect(extractWorkRows([{ ...doneCard(), ok: false }])).toEqual([]);
    const started = sseEventFrom("tool_call", { id: "c1", name: "work_history", status: "started", data: { rows: wireRows() } });
    expect((started as { workRows?: unknown }).workRows).toBeUndefined();
    expect(extractWorkRows([])).toEqual([]);
  });

  it("drops what is not a row and every address outside the app", () => {
    const rows = decodeWorkRows([
      { ...wireRows()[0], link: "https://example.com/x", decisions: "//elsewhere/y" },
      { ...wireRows()[1], paths: ["relative\\file.xlsx", "D:\\ok\\b.xlsx", "D:\\bad\u0007.xlsx"], status: "great" },
      { ...wireRows()[2], link: "/sessions/s1\\..\\x" },
      { title: "no id" },
      { id: "chat:t2", title: "a repeat of t2" },
      "not a row",
    ]);
    expect(rows.map((r) => r.id)).toEqual(["chat:t3", "chat:t2", "run:s1"]);
    expect(rows[0].link).toBe("");
    expect(rows[0].decisions).toBe("");
    expect(rows[1].paths).toEqual(["D:\\ok\\b.xlsx"]);
    expect(rows[1].status).toBe("");
    expect(rows[2].link).toBe("");
    expect(decodeWorkRows("nope")).toEqual([]);
    const many = Array.from({ length: 30 }, (_, i) => ({ id: `chat:${i}`, title: `Piece ${i}` }));
    expect(decodeWorkRows(many)).toHaveLength(20);
  });
});

/* ------------------------------------------------------------------ the row */

describe("the row under the reply", () => {
  it("pressing a row opens its chat; Files and Decisions are quiet links", () => {
    const opened: string[] = [];
    render(<WorkHistoryRow rows={decodeWorkRows(wireRows())} onOpenFile={(p) => opened.push(p)} />);
    const rows = screen.getAllByTestId("work-history-row");
    expect(rows).toHaveLength(3);
    const open = rows.map((r) => within(r).getByTestId("work-history-open").getAttribute("href"));
    expect(open).toEqual(["/chat?thread=t3", "/chat?thread=t2", "/sessions/s1"]);
    expect(within(rows[0]).getByTestId("work-history-status").textContent).toBe("still open");
    expect(within(rows[0]).getByText(/Mar \d+, 2026/)).toBeTruthy();
    // Decisions: the client's page, or the Memory page at the piece.
    expect(within(rows[0]).getByTestId("work-history-decisions").getAttribute("href")).toBe("/clients/client_00000000000d");
    expect(within(rows[0]).getByTestId("work-history-decisions").textContent).toBe(WORK_ROW_DECISIONS);
    expect(within(rows[2]).getByTestId("work-history-decisions").getAttribute("href")).toBe("/memory?work=run%3As1");
    expect(within(rows[1]).queryByTestId("work-history-decisions")).toBeNull();
    // No files: no Files.
    expect(within(rows[0]).queryByTestId("work-history-files")).toBeNull();
    // One file: Files opens it in the panel.
    fireEvent.click(within(rows[2]).getByTestId("work-history-files"));
    expect(opened).toEqual([`${BAKER}\\payroll.xlsx`]);
    // Several: Files lists them, each opens.
    const files = within(rows[1]).getByTestId("work-history-files");
    expect(files.textContent).toBe(WORK_ROW_FILES);
    fireEvent.click(files);
    expect(files.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(within(within(rows[1]).getByTestId("work-history-file-list")).getByText("notes.docx"));
    expect(opened).toEqual([`${BAKER}\\payroll.xlsx`, `${BAKER}\\2026\\notes.docx`]);
  });

  it("draws nothing when the step returned no rows", () => {
    const { container } = render(<WorkHistoryRow rows={[]} onOpenFile={() => {}} />);
    expect(container.innerHTML).toBe("");
    render(<WorkHistoryRow rows={undefined} />);
    expect(screen.queryByTestId("work-history-rows")).toBeNull();
  });

  it("a row with nowhere to go is plain words, and Files needs a place to open files", () => {
    const [row] = decodeWorkRows([{ ...wireRows()[2], link: "" }]);
    render(<WorkHistoryRow rows={[row]} />);
    expect(screen.queryByTestId("work-history-open")).toBeNull();
    expect(screen.getByText("Baker payroll setup")).toBeTruthy();
    expect(screen.queryByTestId("work-history-files")).toBeNull();
  });
});

/* ------------------------------------------------------------ the chat page */

async function box(): Promise<HTMLTextAreaElement> {
  return (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
}

describe("the real chat page", () => {
  it("a reply whose turn looked up earlier work shows the rows and saves them", async () => {
    H.stream.tools = [doneCard()];
    render(<ChatPage />);
    const input = await box();
    fireEvent.change(input, { target: { value: "what did we do for Baker in March?" } });
    fireEvent.keyDown(input, { key: "Enter" });
    const rows = await screen.findByTestId("work-history-rows");
    expect(within(rows).getAllByTestId("work-history-open").map((a) => a.getAttribute("href"))).toEqual([
      "/chat?thread=t3", "/chat?thread=t2", "/sessions/s1",
    ]);
    await waitFor(() => expect(H.api.puts.some((p) => p.path.startsWith("/chat/threads/"))).toBe(true));
    const saved = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/")).at(-1)!.body;
    const reply = (saved.messages as { role: string; workRows?: { id: string }[] }[]).filter((m) => m.role === "assistant").at(-1)!;
    expect(reply.workRows?.map((r) => r.id)).toEqual(["chat:t3", "chat:t2", "run:s1"]);
  });

  /** The last saved assistant line (each save carries the whole chat). */
  const savedReply = () => {
    const saved = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/")).at(-1)?.body;
    const lines = (saved?.messages ?? []) as { role: string; content?: string; workRows?: { id: string }[] }[];
    return lines.filter((m) => m.role === "assistant").at(-1);
  };

  it("a reply that failed after the lookup keeps and saves its rows (review finding 3)", async () => {
    H.stream.tools = [doneCard()];
    H.stream.mode = "fail";
    render(<ChatPage />);
    const input = await box();
    fireEvent.change(input, { target: { value: "what did we do for Baker in March?" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(savedReply()?.workRows?.map((r) => r.id)).toEqual(["chat:t3", "chat:t2", "run:s1"]));
    expect(savedReply()?.content).toBe("I found three pieces of work in March, then");
    const rows = await screen.findByTestId("work-history-rows");
    expect(within(rows).getAllByTestId("work-history-row")).toHaveLength(3);
  });

  it("a reply stopped with words keeps and saves its rows (review finding 3)", async () => {
    H.stream.tools = [doneCard()];
    H.stream.mode = "hold";
    render(<ChatPage />);
    const input = await box();
    fireEvent.change(input, { target: { value: "what did we do for Baker in March?" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.click(await screen.findByTitle("Stop this turn"));
    await waitFor(() => expect(savedReply()?.workRows?.map((r) => r.id)).toEqual(["chat:t3", "chat:t2", "run:s1"]));
    expect(savedReply()?.content).toBe("I found three pieces of work in March so far");
  });

  it("a turn with no such step draws no rows", async () => {
    H.stream.tools = [doneCard(wireRows(), "history_search")];
    render(<ChatPage />);
    const input = await box();
    fireEvent.change(input, { target: { value: "what did we decide?" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByText("Three pieces of work in March.");
    await act(async () => {});
    expect(screen.queryByTestId("work-history-rows")).toBeNull();
  });

  it("a reopened chat draws them again from what was saved, a reply written home included", async () => {
    H.api.getResponses["/chat/threads/t9"] = {
      id: "t9",
      title: "Baker",
      updated_at: "S1",
      messages: [
        { role: "user", content: "what did we do for Baker in March?" },
        // As durable_turns.reply_line writes it: the daemon's own row shape.
        { role: "assistant", content: "Three pieces of work in March.", savedBy: "daemon",
          workRows: [...wireRows(), { id: "chat:x", title: "Outside", link: "javascript:alert(1)" }] },
      ],
      setup: {},
    };
    window.history.replaceState({}, "", "/chat?thread=t9");
    render(<ChatPage />);
    const rows = await screen.findByTestId("work-history-rows");
    const links = within(rows).getAllByTestId("work-history-open").map((a) => a.getAttribute("href"));
    expect(links).toEqual(["/chat?thread=t3", "/chat?thread=t2", "/sessions/s1"]);
    expect(within(rows).getByText("Outside")).toBeTruthy(); // its words, never its address
    expect(rows.innerHTML).not.toContain("javascript:");
  });
});

/* ------------------------------------------------------- the Memory page */

describe("Decisions on the Memory page", () => {
  const entries = {
    folder: BAKER,
    name: "Baker",
    entries: [
      { id: "chat:t1", kind: "chat", ref: "t1", title: "Older piece", folder: BAKER, files: [], link: "/chat?thread=t1" },
      { id: "run:s1", kind: "run", ref: "s1", title: "Baker payroll setup", folder: BAKER, files: [], link: "/sessions/s1",
        recap: { lines: ["Decided: payroll runs on Fridays"], written_at: null, current: true } },
    ],
    total: 2,
  };

  it("?work=<id> opens that piece's folder and brings the piece into view", async () => {
    H.api.getResponses[WORK_FOLDERS_PATH] = {
      folders: [{ folder: "C:\\Other", name: "Other", count: 1 }, { folder: BAKER, name: "Baker", count: 2 }],
      building: false, forgotten: [],
    };
    H.api.getResponses[workPiecePath("run:s1")] = { id: "run:s1", folder: "d:/clients/baker/", name: "Baker" };
    H.api.getResponses[workEntriesPath(BAKER)] = entries;
    window.history.replaceState({}, "", "/memory?work=run%3As1");
    render(<WorkByFolder />);
    const focused = await waitFor(() => {
      const el = document.querySelector('[data-testid="work-item"][data-focused="true"]');
      expect(el).toBeTruthy();
      return el as HTMLElement;
    });
    expect(focused.textContent).toContain("Baker payroll setup");
    expect(focused.textContent).toContain("payroll runs on Fridays");
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    // Asked by the piece's id; no folder in the address.
    expect(H.api.gets).toContain("/work-record/piece?id=run%3As1");
    expect(window.location.search).not.toMatch(/Clients|Baker/i);
  });

  it("a piece no longer on record says so, and opens nothing", async () => {
    H.api.getResponses[WORK_FOLDERS_PATH] = { folders: [{ folder: BAKER, name: "Baker", count: 2 }], building: false, forgotten: [] };
    H.api.getResponses[workPiecePath("chat:gone")] = new H.FakeApiError(PIECE_GONE_WORDS, 404);
    window.history.replaceState({}, "", "/memory?work=chat%3Agone");
    render(<WorkByFolder />);
    expect(await screen.findByText(PIECE_GONE_WORDS)).toBeTruthy();
    expect(H.api.gets.some((p) => p.startsWith("/work-record/entries"))).toBe(false);
  });

  it("without ?work= nothing is asked and nothing opens", async () => {
    H.api.getResponses[WORK_FOLDERS_PATH] = { folders: [{ folder: BAKER, name: "Baker", count: 2 }], building: false, forgotten: [] };
    window.history.replaceState({}, "", "/memory");
    render(<WorkByFolder />);
    await screen.findAllByTestId("work-folder");
    await act(async () => {});
    expect(H.api.gets.some((p) => p.startsWith("/work-record/piece"))).toBe(false);
    expect(H.api.gets.some((p) => p.startsWith("/work-record/entries"))).toBe(false);
  });
});
