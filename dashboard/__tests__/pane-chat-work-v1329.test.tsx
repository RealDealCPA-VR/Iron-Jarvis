/**
 * v1.329.0 (calm chat wave 5, S1) — the Build page side chat (PaneChat)
 * matches the main chat:
 *
 *  1. WORK LINE: a reply's tools, steps and thinking fold into the chat
 *     page's ONE quiet line ("Worked for 3.0 s · read 1 file"), and a running
 *     turn draws the chat page's live rows. No more pane-tool-card rows.
 *  2. A FINISHED TURN KEEPS what that line reads (steps, timing, thinking,
 *     the settle time), LAST on the reply (the receipt rule).
 *  3. FILES CHANGED: the chat page's "N files changed" line, only for a reply
 *     whose documents list paths, asked once when the reply is on screen;
 *     its Undo is the pane's own (the same honest confirm and journal row).
 *  4. PERMISSION CHIP: the chat page's chip in the composer card. The level
 *     rides every pane turn and is saved with the thread; a thread's stored
 *     level shows on the chip. In a narrow pane only the shield shows.
 *  5. TOKENS: no literal hues in the pane chat (Daylight does not remap them).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
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
  return {
    FakeApiError,
    api: {
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      threads: {} as Record<string, unknown>,
      undo: [] as unknown[],
      changes: null as unknown,
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => "",
  get: async (path: string) => {
    if (path === "/projects") return { projects: [] };
    if (path === "/undo?session_id=chat") return { actions: H.api.undo };
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
    if (path === "/chat/changes") return H.api.changes ?? { changes: [] };
    return { ok: true };
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    return { id: "th_1", title: "t" };
  },
}));

const S = vi.hoisted(() => ({
  stream: {
    streaming: false,
    tools: [] as unknown[],
    approval: null as Record<string, unknown> | null,
  },
  bodies: [] as Record<string, unknown>[],
  result: { reply: "ok" } as Record<string, unknown>,
}));

vi.mock("@/lib/useChatStream", () => ({
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  StreamError: class extends Error {},
  useChatStream: () => ({
    streaming: S.stream.streaming,
    text: "",
    tools: S.stream.tools,
    approval: S.stream.approval,
    run: async (body: Record<string, unknown>) => {
      S.bodies.push(body);
      return S.result;
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
    health: {
      status: "ok",
      version: "test",
      default_provider: "mock",
      default_model: "m",
      providers: [],
    },
  }),
}));

import { PaneChat } from "@/components/terminal/PaneChat";
import { workSummary } from "@/components/chat/WorkLine";
import { resetTurnChanges } from "@/lib/turnChanges";
import { PANE_COMPOSER_NARROW_PX, PANE_PERMISSION_COMPACT } from "@/components/terminal/paneChatLook";
import { paneTurnWindow, paneWorkInput, type PaneMessage } from "@/components/terminal/paneWork";

const CWD = "C:\\work\\demo";
const DOC = "C:\\work\\demo\\summary.md";
const T0 = Date.parse("2026-10-09T12:00:00Z");
const read = (...p: string[]) =>
  readFileSync(join(process.cwd(), ...p), "utf8").replace(/\r\n/g, "\n");

function withThread(messages: unknown[], setup: Record<string, unknown> | null = null) {
  window.localStorage.setItem("ij.pane.thread.p1", "th_1");
  H.api.threads.th_1 = { id: "th_1", messages, setup };
}

const CHANGES = {
  changes: [
    {
      path: DOC,
      rel: "summary.md",
      name: "summary.md",
      status: "modified",
      added: 6,
      removed: 2,
      diff: "--- a/summary.md\n+++ b/summary.md\n@@ -1 +1 @@\n-old\n+new\n",
      changed_since: false,
    },
  ],
  files: 1,
  added: 6,
  removed: 2,
  truncated_files: false,
};

/** An IntersectionObserver that says "on screen" the moment it watches. */
class SeenObserver {
  cb: (entries: { isIntersecting: boolean }[]) => void;
  constructor(cb: (entries: { isIntersecting: boolean }[]) => void) {
    this.cb = cb;
  }
  observe() {
    this.cb([{ isIntersecting: true }]);
  }
  disconnect() {}
  unobserve() {}
}

const origIO = (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver;
const origRO = (globalThis as { ResizeObserver?: unknown }).ResizeObserver;

beforeEach(() => {
  H.api.posts = [];
  H.api.puts = [];
  H.api.threads = {};
  H.api.undo = [];
  H.api.changes = null;
  S.stream.streaming = false;
  S.stream.tools = [];
  S.stream.approval = null;
  S.bodies = [];
  S.result = { reply: "ok" };
  window.localStorage.clear();
  resetTurnChanges();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = origIO;
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver = origRO;
});

async function send(text: string) {
  const box = screen.getByLabelText("Message");
  fireEvent.change(box, { target: { value: text } });
  await waitFor(() => expect(screen.getByLabelText("Send")).not.toBeDisabled());
  fireEvent.click(screen.getByLabelText("Send"));
}

/* -------------------------------------------------------- 1. the work line */

describe("the pane folds a reply's work into the chat page's line", () => {
  it("a reply with steps and timing shows 'Worked for … · read 2 files, changed 1 file', folded", async () => {
    withThread([
      { role: "user", content: "sum it", at: "2026-10-09T12:00:00.000Z" },
      {
        role: "assistant",
        content: "Done.",
        toolsUsed: ["read_file", "read_file", "edit_file"],
        steps: [
          { name: "read_file", ok: true, ms: 420, target: "harbor.xlsx" },
          { name: "read_file", ok: true, ms: 380, target: "pier.xlsx" },
          { name: "edit_file", ok: true, ms: 210, target: "summary.md" },
        ],
        timing: { startedAt: T0, firstTokenAt: null, endedAt: T0 + 12_000 },
      },
    ]);
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const line = await screen.findByTestId("work-line");
    expect(screen.getByTestId("pane-chat-transcript")).toContainElement(line);
    expect(within(line).getByTestId("work-summary").textContent).toBe(
      "Worked for 12 s · read 2 files, changed 1 file",
    );
    // Folded until pressed; then the rows say what was done to what.
    expect(within(line).queryByTestId("work-rows")).toBeNull();
    fireEvent.click(within(line).getByRole("button"));
    const rows = within(line).getByTestId("work-rows");
    expect(rows.textContent).toContain("Read harbor.xlsx");
    expect(rows.textContent).toContain("Changed summary.md");
    // The line sits ABOVE the reply, like the chat page's.
    const reply = screen.getByTestId("pane-reply");
    expect(line.compareDocumentPosition(reply) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("an older pane reply (tools only) still gets the line; a reply with no work gets none", async () => {
    withThread([
      { role: "user", content: "a" },
      { role: "assistant", content: "one", toolsUsed: ["read_file"] },
      { role: "user", content: "b" },
      { role: "assistant", content: "two" },
    ]);
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await screen.findAllByTestId("pane-reply");
    const lines = screen.getAllByTestId("work-line");
    expect(lines).toHaveLength(1);
    expect(within(lines[0]).getByTestId("work-summary").textContent).toBe("Read 1 file");
  });

  it("a running turn draws the chat page's live rows, not pane-tool-card boxes", async () => {
    S.stream.streaming = true;
    S.stream.tools = [
      { id: "c1", name: "read_file", status: "running", args: { path: "C:\\work\\demo\\harbor.xlsx" } },
    ];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const live = await screen.findByTestId("pane-chat-live");
    const rows = within(live).getByTestId("live-tool-rows");
    expect(rows.textContent).toContain("Reading harbor.xlsx");
    expect(screen.queryByTestId("pane-tool-card")).toBeNull();
  });
});

/* ----------------------------------------------- 2. what a turn keeps */

describe("a finished pane turn keeps what the lines read, last on the reply", () => {
  it("steps, timing, thinking and the settle time ride the save, after every older field", async () => {
    S.result = {
      reply: "Done.",
      tools_used: ["read_file"],
      documents: [DOC],
      steps: [{ name: "read_file", ok: true, ms: 300, target: "harbor.xlsx" }],
      timing: { startedAt: T0, firstTokenAt: T0 + 1000, endedAt: T0 + 3000 },
      thinking: "Look at the sheet first.",
      thinkingMs: 2400,
      truncated: false,
    };
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await send("sum it");
    await waitFor(() => expect(H.api.puts.length).toBeGreaterThan(0));
    const saved = H.api.puts[H.api.puts.length - 1].body.messages as PaneMessage[];
    const [user, reply] = saved;
    expect(typeof user.at).toBe("string");
    expect(reply.steps).toEqual([{ name: "read_file", ok: true, ms: 300, target: "harbor.xlsx" }]);
    expect(reply.timing).toEqual({ startedAt: T0, firstTokenAt: T0 + 1000, endedAt: T0 + 3000 });
    expect(reply.thinking).toBe("Look at the sheet first.");
    expect(reply.thinkingSeconds).toBe(2);
    expect(typeof reply.at).toBe("string");
    // The receipt rule: the new fields come AFTER every field the pane
    // already stored.
    const keys = Object.keys(reply);
    expect(keys.slice(keys.indexOf("documents") + 1)).toEqual([
      "at",
      "thinking",
      "thinkingSeconds",
      "steps",
      "timing",
    ]);
    // And the line reads them at once.
    await waitFor(() =>
      expect(screen.getByTestId("work-summary").textContent).toBe(workSummary(paneWorkInput(reply))),
    );
    expect(screen.getByTestId("work-summary").textContent).toBe("Worked for 3.0 s · read 1 file");
  });
});

/* -------------------------------------------------- 3. files changed */

describe("the chat page's 'files changed' line under a pane reply", () => {
  const writing = () =>
    withThread([
      { role: "user", content: "fix it", at: "2026-10-09T12:00:00.000Z" },
      {
        role: "assistant",
        content: "Fixed.",
        documents: [DOC],
        timing: { startedAt: T0, firstTokenAt: null, endedAt: T0 + 5000 },
        at: "2026-10-09T12:00:05.000Z",
      },
    ]);

  it("asks once, by the turn's window and files, when the reply is on screen", async () => {
    (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = SeenObserver;
    H.api.changes = CHANGES;
    writing();
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const line = await screen.findByTestId("changed-files-line");
    expect(line.textContent).toContain("1 file changed");
    const asks = H.api.posts.filter((p) => p.path === "/chat/changes");
    expect(asks).toHaveLength(1);
    expect(asks[0].body).toEqual({
      since: new Date(T0).toISOString(),
      until: new Date(T0 + 5000).toISOString(),
      paths: [DOC],
    });
  });

  it("a reply that wrote nothing never asks", async () => {
    (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = SeenObserver;
    withThread([
      { role: "user", content: "hi", at: "2026-10-09T12:00:00.000Z" },
      { role: "assistant", content: "Hello.", timing: { startedAt: T0, firstTokenAt: null, endedAt: T0 + 900 } },
    ]);
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await screen.findByTestId("pane-reply");
    expect(screen.queryByTestId("reply-changes")).toBeNull();
    expect(H.api.posts.filter((p) => p.path === "/chat/changes")).toHaveLength(0);
  });

  it("a reply with no timing asks by its question's send time (the chat page's fallback)", () => {
    const msgs: PaneMessage[] = [
      { role: "user", content: "fix", at: "2026-10-09T12:00:00.000Z" },
      { role: "assistant", content: "ok", documents: [DOC], at: "2026-10-09T12:00:09.000Z" },
    ];
    expect(paneTurnWindow(msgs, 1)).toEqual({
      since: "2026-10-09T12:00:00.000Z",
      until: "2026-10-09T12:00:09.000Z",
      paths: [DOC],
    });
    // An old pane reply (no timing, no times) has no window: nothing is asked.
    expect(paneTurnWindow([{ role: "user", content: "x" }, { role: "assistant", content: "y", documents: [DOC] }], 1)).toBeNull();
  });

  it("its Undo is the pane's own: the honest confirm, then the journal row", async () => {
    (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = SeenObserver;
    H.api.changes = CHANGES;
    H.api.undo = [
      { action_id: "act_1", kind: "file_restore", undoable: true, path: "summary.md", workspace: CWD },
    ];
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    writing();
    render(<PaneChat paneId="p1" cwd={CWD} />);
    fireEvent.click(await screen.findByTestId("changed-files-line"));
    const undo = await screen.findByTestId("changed-undo-0");
    fireEvent.click(undo);
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/undo/act_1")).toBe(true));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(String(confirm.mock.calls[0][0])).toContain("panes and Chat share one journal");
  });
});

/* ------------------------------------------------- 4. the permission chip */

describe("the permission chip in the pane's composer card", () => {
  it("sits inside the ONE composer card", async () => {
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const card = screen.getByTestId("pane-chat-composer");
    const chip = within(card).getByTestId("permission-chip");
    expect(chip.getAttribute("aria-label")).toBe("Permissions: Ask when risky");
  });

  it("shows the thread's stored level, and that level rides the turn", async () => {
    withThread([{ role: "user", content: "a" }, { role: "assistant", content: "b" }], {
      approval_mode: "always_ask",
    });
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await waitFor(() =>
      expect(screen.getByTestId("permission-chip").getAttribute("aria-label")).toBe(
        "Permissions: Ask first",
      ),
    );
    await send("go");
    await waitFor(() => expect(S.bodies).toHaveLength(1));
    expect(S.bodies[0].approval_mode).toBe("always_ask");
  });

  it("a pick rides the next turn and is saved with the thread at once", async () => {
    withThread([{ role: "user", content: "a" }, { role: "assistant", content: "b" }], null);
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await screen.findByTestId("pane-reply");
    fireEvent.click(screen.getByTestId("permission-chip"));
    const yolo = document.querySelector('[data-testid="permission-item"][data-mode="yolo"]');
    expect(yolo).not.toBeNull();
    fireEvent.click(yolo as Element);
    await waitFor(() =>
      expect(screen.getByTestId("permission-chip").getAttribute("aria-label")).toBe(
        "Permissions: Don't ask",
      ),
    );
    // Saved right away (an existing conversation): picking then closing the
    // pane must not lose it.
    await waitFor(() => expect(H.api.puts.length).toBeGreaterThan(0));
    const setup = H.api.puts[H.api.puts.length - 1].body.setup as Record<string, unknown>;
    expect(setup.approval_mode).toBe("yolo");
    // …and the next turn carries it.
    await send("go");
    await waitFor(() => expect(S.bodies).toHaveLength(1));
    expect(S.bodies[0].approval_mode).toBe("yolo");
  });

  it("in a narrow pane only the shield shows; a wide one keeps the words", async () => {
    const width = { px: 300 };
    class FakeResizeObserver {
      cb: (entries: { contentRect: { width: number } }[]) => void;
      constructor(cb: (entries: { contentRect: { width: number } }[]) => void) {
        this.cb = cb;
      }
      observe() {
        this.cb([{ contentRect: { width: width.px } }]);
      }
      disconnect() {}
      unobserve() {}
    }
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = FakeResizeObserver;
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const wrap = screen.getByTestId("pane-chat-permission");
    await waitFor(() => expect(wrap.getAttribute("data-compact")).toBe("true"));
    for (const cls of PANE_PERMISSION_COMPACT.split(" ")) expect(wrap.className).toContain(cls);
    // The level is still said to a screen reader and on hover.
    expect(within(wrap).getByTestId("permission-chip").getAttribute("aria-label")).toBe(
      "Permissions: Ask when risky",
    );
    cleanup();
    width.px = PANE_COMPOSER_NARROW_PX + 120;
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const wide = screen.getByTestId("pane-chat-permission");
    expect(wide.hasAttribute("data-compact")).toBe(false);
    expect(wide.className).not.toContain("permission-chip]>span]:hidden");
  });

  it("in a narrow pane 'Make this a project' is its icon only, so the folder name keeps its room", async () => {
    const width = { px: 300 };
    class FakeResizeObserver {
      cb: (entries: { contentRect: { width: number } }[]) => void;
      constructor(cb: (entries: { contentRect: { width: number } }[]) => void) {
        this.cb = cb;
      }
      observe() {
        this.cb([{ contentRect: { width: width.px } }]);
      }
      disconnect() {}
      unobserve() {}
    }
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = FakeResizeObserver;
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const btn = await screen.findByTestId("pane-chat-make-project");
    // The words are gone from the row; the name is still the button's.
    await waitFor(() => expect(within(btn).queryByTestId("pane-chat-make-project-words")).toBeNull());
    expect(btn.textContent).toBe("");
    expect(screen.getByRole("button", { name: "Make this a project" })).toBe(btn);
    expect(btn.getAttribute("title")).toContain("Creates a project rooted in this folder");
    // The folder name is still there, beside it.
    expect(screen.getByTitle(CWD).textContent).toBe("demo");
    cleanup();

    width.px = PANE_COMPOSER_NARROW_PX + 120;
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const wide = await screen.findByTestId("pane-chat-make-project");
    expect(within(wide).getByTestId("pane-chat-make-project-words").textContent).toBe(
      "Make this a project",
    );
    expect(wide.hasAttribute("aria-label")).toBe(false);
    expect(screen.getByRole("button", { name: "Make this a project" })).toBe(wide);
  });
});

/* ---------------------------------------------------------- 5. tokens */

describe("the pane chat uses tone tokens, not literal hues", () => {
  it("no rose / emerald / amber / red / green classes in PaneChat", () => {
    const src = read("components", "terminal", "PaneChat.tsx");
    expect(src).not.toMatch(/\b(?:text|bg|border|ring|fill|stroke)-(?:rose|emerald|amber|red|green|orange|yellow)-\d/);
    expect(src).toContain("text-tone-warn");
    expect(src).toContain("hover:text-tone-danger");
  });
});
