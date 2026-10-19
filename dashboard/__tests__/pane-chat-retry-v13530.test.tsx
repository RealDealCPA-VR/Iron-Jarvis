/**
 * v1.353.0 lane 3D (G51) — the Build pane's chat can retry and speaks chat's
 * receipt.
 *
 * Before: a failed pane turn showed its error and nothing else (the user
 * retyped the question); a reply's receipt named the provider, never the
 * model, where /chat says "answered by Opus 5.5"; and the same files were
 * listed three times (the changes line, the pane's own file cards, the
 * receipt).
 *
 * Now: a failed turn offers Retry, which re-sends the SAME message with the
 * SAME files (and a followed turn's Retry re-sends its question's own files);
 * the question goes back in an empty box; the receipt is the chat page's
 * inline one ("answered by <model>" from the shared catalog, steps, timing,
 * each file once with Open + Undo); the pane's file cards are gone.
 *
 * Driven through the REAL PaneChat, TurnReceipt, lib/answeredModel and
 * lib/useModels; only the network (lib/api), the stream hook and the daemon
 * hook are stubbed (the pane-chat-v1206 harness).
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
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
  return {
    FakeApiError,
    api: {
      gets: [] as string[],
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      threads: {} as Record<string, unknown>,
      models: [] as Record<string, unknown>[],
      turns: {} as Record<string, Record<string, unknown>[]>,
      undoRows: [] as Record<string, unknown>[],
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.api.gets.push(path);
    if (path === "/projects") return { projects: [] };
    if (path === "/models") return { models: H.api.models };
    if (path === "/undo?session_id=chat") return { actions: H.api.undoRows };
    const turnsFor = /^\/chat\/turns\?thread_id=([^&]+)/.exec(path);
    if (turnsFor) return { turns: H.api.turns[decodeURIComponent(turnsFor[1])] ?? [] };
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    if (m) {
      const t = H.api.threads[decodeURIComponent(m[1])];
      if (!t) throw new H.FakeApiError("thread not found", 404);
      return t;
    }
    throw new H.FakeApiError(`unexpected GET ${path}`, 500);
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    if (path === "/documents/upload") return { path: `C:/up/${body.filename}`, name: body.filename };
    return { ok: true };
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "th_1", title: "t" };
  },
}));

const S = vi.hoisted(() => {
  class FakeStreamError extends Error {
    status: number;
    committed: boolean;
    offline: boolean;
    partial: string;
    constructor(message: string, status = 0, committed = false, offline = false, partial = "") {
      super(message);
      this.name = "StreamError";
      this.status = status;
      this.committed = committed;
      this.offline = offline;
      this.partial = partial;
    }
  }
  return {
    FakeStreamError,
    stream: {
      bodies: [] as Record<string, unknown>[],
      opts: [] as { rejoin?: string }[],
      /** One outcome per run, in order: an Error rejects, an object resolves. */
      outcomes: [] as Array<Error | Record<string, unknown>>,
    },
  };
});

vi.mock("@/lib/useChatStream", () => ({
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  StreamError: S.FakeStreamError,
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async (body: Record<string, unknown>, _a?: unknown, _b?: unknown, opts?: { rejoin?: string }) => {
      S.stream.bodies.push(body);
      S.stream.opts.push(opts ?? {});
      const next = S.stream.outcomes.shift() ?? { reply: "ok" };
      if (next instanceof Error) throw next;
      return next;
    },
    abort: () => {},
  }),
}));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    refresh: () => {},
    health: { status: "ok", version: "test", default_provider: "mock", default_model: "m", providers: [] },
  }),
}));

vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import { PaneChat } from "@/components/terminal/PaneChat";
import { __resetApiCache } from "@/lib/apiCache";

const CWD = "C:\\work\\demo";
const ROUTE = { requested: "", provider: "claude-cli", model: "claude-opus-5-5", reason: "default" };

beforeEach(() => {
  __resetApiCache();
  H.api.gets = [];
  H.api.posts = [];
  H.api.puts = [];
  H.api.threads = {};
  H.api.models = [];
  H.api.turns = {};
  H.api.undoRows = [];
  S.stream.bodies = [];
  S.stream.opts = [];
  S.stream.outcomes = [];
  window.localStorage.clear();
});
afterEach(() => cleanup());

function box(): HTMLTextAreaElement {
  return screen.getByLabelText("Message") as HTMLTextAreaElement;
}

async function typeAndSend(text: string) {
  await waitFor(() => expect(box()).toBeEnabled());
  fireEvent.change(box(), { target: { value: text } });
  fireEvent.click(screen.getByLabelText("Send"));
}

async function attach(name: string) {
  const file = new File(["hello"], name, { type: "text/plain" });
  fireEvent.drop(screen.getByTestId("pane-chat"), {
    dataTransfer: { types: ["Files"], files: [file] },
  });
  await screen.findByRole("button", { name: `Remove ${name}` });
}

describe("a failed pane turn can be retried (G51)", () => {
  it("Retry re-sends the SAME message with the SAME files, and the reply lands", async () => {
    S.stream.outcomes = [
      new S.FakeStreamError("provider exploded", 500, true),
      { reply: "Fixed it.", route: ROUTE },
    ];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await waitFor(() => expect(box()).toBeEnabled());
    await attach("notes.txt");
    await typeAndSend("fix the failing test");
    // The failure is said, Retry is offered, the question is back in the box
    // and the chips were spent on the failed send.
    expect(await screen.findByText(/provider exploded/)).toBeInTheDocument();
    const retry = await screen.findByTestId("pane-chat-retry");
    expect(retry).toHaveTextContent("Retry");
    expect(retry).toHaveAttribute("title", "Re-send the last message");
    expect(box().value).toBe("fix the failing test");
    expect(screen.queryByRole("button", { name: "Remove notes.txt" })).not.toBeInTheDocument();

    fireEvent.click(retry);
    await screen.findByText("Fixed it.");
    expect(S.stream.bodies.length).toBe(2);
    const [first, second] = S.stream.bodies;
    // The same question, once (never a second copy of it), and the same files.
    expect(second.messages).toEqual(first.messages);
    expect((second.messages as unknown[]).length).toBe(1);
    expect(second.attachments).toEqual(["C:/up/notes.txt"]);
    expect(second.attachments).toEqual(first.attachments);
    // A fresh turn, not a rejoin of the failed one.
    expect(S.stream.opts[1]).toEqual({});
    expect(second.turn_id).not.toBe(first.turn_id);
    // The restored draft was the question being re-sent: retired.
    expect(box().value).toBe("");
    expect(screen.queryByTestId("pane-chat-retry")).not.toBeInTheDocument();
    expect(screen.queryByText(/provider exploded/)).not.toBeInTheDocument();
    // One question, one answer — the transcript never shows the question twice.
    expect(screen.getAllByTestId("pane-user-bubble")).toHaveLength(1);
  });

  it("Retry takes the interrupted partial off the screen while the new answer comes (review)", async () => {
    // The chat page's completeChat sets the history first, so a retried
    // answer never streams under the old cut-off one. Held: the retried run
    // does not answer until the test says so, so the in-between screen is
    // what is asserted.
    let release: (v: Record<string, unknown>) => void = () => {};
    const held = new Promise<Record<string, unknown>>((resolve) => {
      release = resolve;
    });
    S.stream.outcomes = [
      new S.FakeStreamError("the line dropped", 0, true, false, "Half an ans"),
      held as unknown as Record<string, unknown>,
    ];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("explain the build");
    expect(await screen.findByText(/interrupted — this answer is incomplete/i)).toBeInTheDocument();
    expect(screen.getByText("Half an ans")).toBeInTheDocument();
    fireEvent.click(await screen.findByTestId("pane-chat-retry"));
    // The retried run has been asked and has not answered yet.
    await waitFor(() => expect(S.stream.bodies.length).toBe(2));
    await waitFor(() => expect(screen.queryByText("Half an ans")).not.toBeInTheDocument());
    expect(screen.queryByText(/interrupted — this answer is incomplete/i)).not.toBeInTheDocument();
    // The question itself stays.
    expect(screen.getByTestId("pane-user-bubble")).toHaveTextContent("explain the build");
    release({ reply: "The whole answer." });
    await screen.findByText("The whole answer.");
    expect(screen.getAllByTestId("pane-user-bubble")).toHaveLength(1);
  });

  it("anything the user typed after the failure is kept when they press Retry", async () => {
    S.stream.outcomes = [new S.FakeStreamError("timed out", 0, false), { reply: "ok" }];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("first try");
    const retry = await screen.findByTestId("pane-chat-retry");
    fireEvent.change(box(), { target: { value: "a note for later" } });
    fireEvent.click(retry);
    await screen.findByText("ok");
    expect(box().value).toBe("a note for later");
    expect((S.stream.bodies[1].messages as Array<{ content: string }>)[0].content).toBe("first try");
  });

  it("a NEW send after a failure retires the Retry (it never re-sends a stale turn)", async () => {
    S.stream.outcomes = [new S.FakeStreamError("boom", 500, true), { reply: "second answer" }];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("one");
    await screen.findByTestId("pane-chat-retry");
    fireEvent.change(box(), { target: { value: "two" } });
    fireEvent.click(screen.getByLabelText("Send"));
    await screen.findByText("second answer");
    expect(screen.queryByTestId("pane-chat-retry")).not.toBeInTheDocument();
  });

  it("a FOLLOWED turn that fails re-sends its question's own files on Retry", async () => {
    window.localStorage.setItem("ij.pane.thread.p1", "th_9");
    const asked = new Date(Date.now() - 5_000).toISOString();
    H.api.threads["th_9"] = {
      id: "th_9",
      messages: [
        {
          role: "user",
          content: "summarise this",
          attachmentNames: ["report.pdf"],
          attachmentPaths: ["C:/up/report.pdf"],
          at: asked,
        },
      ],
      setup: {},
    };
    H.api.turns["th_9"] = [{ turn_id: "chat-live-1", status: "running", running: true, started_at: asked }];
    S.stream.outcomes = [new S.FakeStreamError("the turn was cut off", 0, true), { reply: "Summary." }];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const retry = await screen.findByTestId("pane-chat-retry");
    expect(S.stream.opts[0]).toEqual({ rejoin: "chat-live-1" });
    fireEvent.click(retry);
    await screen.findByText("Summary.");
    expect(S.stream.opts[1]).toEqual({});
    expect(S.stream.bodies[1].attachments).toEqual(["C:/up/report.pdf"]);
  });
});

describe("a pane reply speaks chat's receipt (G51)", () => {
  it("reads 'answered by <model>' with the catalog's own label, like /chat", async () => {
    // A label the id itself does not spell, so the name can only come from
    // the catalog (the id alone reads "Opus 5.5" through friendlyModelName).
    H.api.models = [{ provider: "claude-cli", model: "claude-opus-5-5", label: "Opus 5.5 on my plan", available: true }];
    S.stream.outcomes = [{ reply: "Done.", route: ROUTE }];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("go");
    await screen.findByText("Done.");
    await waitFor(() => expect(screen.getByTestId("turn-answered-by")).toHaveTextContent("answered by Opus 5.5 on my plan"));
    // The raw route stays in the title, a record.
    expect(screen.getByTestId("turn-answered-by").getAttribute("title")).toContain("claude-opus-5-5");
  });

  it("without a catalog row the model is still named, never only the provider", async () => {
    S.stream.outcomes = [{ reply: "Done.", route: ROUTE }];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("go");
    const who = await screen.findByTestId("turn-answered-by");
    expect(who.textContent ?? "").toMatch(/^answered by /);
    expect(who.textContent ?? "").toMatch(/opus/i);
  });

  it("files are listed ONCE: no pane file cards; the receipt counts it and opens onto steps, timing and the file", async () => {
    const DOC = "C:\\work\\demo\\out.md";
    S.stream.outcomes = [
      {
        reply: "Wrote it.",
        route: ROUTE,
        documents: [DOC],
        tools_used: ["write_file"],
        steps: [{ id: "s1", name: "write_file", ok: true, startedAt: 1000, endedAt: 1600 }],
        timing: { startedAt: 1000, firstTokenAt: 1200, endedAt: 2600 },
      },
    ];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("write the notes");
    await screen.findByText("Wrote it.");
    expect(screen.queryByTestId("pane-file-card")).not.toBeInTheDocument();
    const line = screen.getByTestId("turn-receipt");
    expect(line).toHaveTextContent("1 file");
    // At rest the file's NAME is not on screen a second time.
    expect(screen.queryAllByText("out.md")).toHaveLength(0);
    fireEvent.click(line);
    const detail = await screen.findByTestId("turn-receipt-detail");
    expect(within(detail).getAllByTestId("turn-doc")).toHaveLength(1);
    expect(within(detail).getByTestId("turn-doc")).toHaveTextContent("out.md");
    // The steps and the timing reached the receipt (the chat page passes both).
    expect(detail.textContent ?? "").toMatch(/write/i);
  });

  it("the receipt sits in a wrapping row so its detail opens on its own line (the calm inline shape)", async () => {
    S.stream.outcomes = [{ reply: "Done.", route: ROUTE }];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("go");
    await screen.findByText("Done.");
    const row = screen.getByTestId("pane-reply-receipt");
    expect(row.className).toContain("flex-wrap");
    expect(row).toContainElement(screen.getByTestId("turn-receipt"));
    // No box drawn around process detail.
    expect(row.className).not.toMatch(/\bborder\b|\bshadow\b|\bbg-/);
  });
});
