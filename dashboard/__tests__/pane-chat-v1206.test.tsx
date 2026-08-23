/**
 * v1.206.0 — Build-chat: the pane-bound chat room's engine (PaneChat).
 *
 * One LEAN chat component bound to a working directory, mounted inside a
 * Build/terminals pane. What must hold:
 *
 *  - every turn is GROUNDED: the /chat/stream body carries workspace_dir=cwd
 *    and auto_tools (the pane's whole point);
 *  - attachments upload first (/documents/upload, base64), then RIDE the next
 *    turn as paths;
 *  - the engine pick lands `provider` in the body AND persists into the
 *    thread's setup snapshot;
 *  - cwd under a project root (case-insensitive, segment boundary) passes
 *    project_id — and never when the cwd is outside every root;
 *  - one thread per pane: the id persists in localStorage ij.pane.thread.<id>,
 *    the first save PUTs "new" (with the "Build: <folder>" title), later saves
 *    PUT the real id, and a reopened thread restores its transcript + engine;
 *  - autosaves MERGE the setup — keys the pane does not manage (tools, skill,
 *    approval_mode…) ride forward verbatim, never clobbered with empties;
 *  - the receipt/doors under a reply are the done frame's server truth;
 *  - a stream error renders AS an error (partial kept, marked interrupted);
 *    daemon-down is the OfflineHint, with the composer disabled.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

// ------------------------------------------------------------- module mocks

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
      /** Thread ids whose GET fails with a NON-404 (db locked, offline). */
      failThreads: new Set<string>(),
      projects: [] as { id: string; name: string; root?: string }[],
      nextThreadId: "th_1",
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.api.gets.push(path);
    if (path === "/projects") return { projects: H.api.projects };
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    if (m) {
      const id = decodeURIComponent(m[1]);
      if (H.api.failThreads.has(id)) throw new H.FakeApiError("db locked", 500);
      const t = H.api.threads[id];
      if (!t) throw new H.FakeApiError("thread not found", 404);
      return t;
    }
    throw new H.FakeApiError(`unexpected GET ${path}`, 500);
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    if (path === "/documents/upload")
      return { path: `C:/up/${body.filename}`, name: body.filename };
    return { ok: true };
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    const id = m && m[1] !== "new" ? m[1] : H.api.nextThreadId;
    return { id, title: (body?.title as string) ?? "t" };
  },
}));

const S = vi.hoisted(() => {
  class FakeStreamError extends Error {
    status: number;
    committed: boolean;
    offline: boolean;
    partial: string;
    constructor(
      message: string,
      status = 0,
      committed = false,
      offline = false,
      partial = "",
    ) {
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
      result: { reply: "ok" } as Record<string, unknown>,
      reject: null as Error | null,
      streaming: false,
      text: "",
      tools: [] as Record<string, unknown>[],
      // A mid-turn approval frame the hook is currently holding (BC1 D1).
      approval: null as Record<string, unknown> | null,
    },
  };
});

vi.mock("@/lib/useChatStream", () => ({
  StreamError: S.FakeStreamError,
  useChatStream: () => ({
    streaming: S.stream.streaming,
    text: S.stream.text,
    tools: S.stream.tools,
    approval: S.stream.approval,
    run: async (body: Record<string, unknown>) => {
      S.stream.bodies.push(body);
      if (S.stream.reject) throw S.stream.reject;
      return S.stream.result;
    },
    abort: () => {},
  }),
}));

const D = vi.hoisted(() => ({
  daemon: {
    online: true,
    checking: false,
    providers: [
      { provider: "claude-cli", available: true, class: "cli" },
      { provider: "lmstudio", available: true, class: "local" },
      { provider: "openai", available: false, class: "api" },
    ],
  },
}));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: D.daemon.online,
    unauthorized: false,
    requestError: false,
    checking: D.daemon.checking,
    refresh: () => {},
    health: {
      status: "ok",
      version: "test",
      default_provider: "mock",
      default_model: "m",
      providers: D.daemon.providers,
    },
  }),
}));

// The markdown pipeline is not under test here (house idiom).
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import { PaneChat } from "@/components/terminal/PaneChat";
import {
  engineOptions,
  mergeSetup,
  paneTitle,
  paneThreadKey,
  projectForCwd,
} from "@/components/terminal/paneChatCore";

const CWD = "C:\\work\\demo";

beforeEach(() => {
  H.api.gets = [];
  H.api.posts = [];
  H.api.puts = [];
  H.api.threads = {};
  H.api.failThreads = new Set();
  H.api.projects = [];
  H.api.nextThreadId = "th_1";
  S.stream.bodies = [];
  S.stream.result = { reply: "ok" };
  S.stream.reject = null;
  S.stream.streaming = false;
  S.stream.text = "";
  S.stream.tools = [];
  S.stream.approval = null;
  D.daemon.online = true;
  D.daemon.checking = false;
  window.localStorage.clear();
});
afterEach(() => cleanup());

async function typeAndSend(text: string) {
  const box = screen.getByLabelText("Message");
  await waitFor(() => expect(box).toBeEnabled());
  fireEvent.change(box, { target: { value: text } });
  fireEvent.click(screen.getByLabelText("Send"));
}

describe("grounding: every turn carries the pane's folder", () => {
  it("POSTs workspace_dir=cwd + auto_tools, messages verbatim", async () => {
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("fix the failing test");
    await waitFor(() => expect(S.stream.bodies.length).toBe(1));
    const body = S.stream.bodies[0];
    expect(body.workspace_dir).toBe(CWD);
    expect(body.auto_tools).toBe(true);
    expect(body.messages).toEqual([
      { role: "user", content: "fix the failing test" },
    ]);
    // Default engine = provider OMITTED (the daemon routes), never "".
    expect("provider" in body).toBe(false);
  });
});

describe("attachments: upload first, then ride the next turn", () => {
  it("drop → /documents/upload (base64) → chip → path in the turn body", async () => {
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const surface = screen.getByTestId("pane-chat");
    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    fireEvent.drop(surface, {
      dataTransfer: { types: ["Files"], files: [file] },
    });
    // The upload carries the contract fields: filename + bare base64.
    await waitFor(() => {
      const up = H.api.posts.find((p) => p.path === "/documents/upload");
      expect(up).toBeTruthy();
      expect(up?.body.filename).toBe("notes.txt");
      expect(up?.body.content_b64).toBe("aGVsbG8="); // "hello"
    });
    // The chip is removable, and visible before sending.
    expect(screen.getByText("notes.txt")).toBeInTheDocument();
    await typeAndSend("summarize this");
    await waitFor(() => expect(S.stream.bodies.length).toBe(1));
    expect(S.stream.bodies[0].attachments).toEqual(["C:/up/notes.txt"]);
    // Consumed by the turn — the chip must not ride a SECOND turn too.
    await waitFor(() =>
      expect(screen.queryByLabelText("Remove notes.txt")).not.toBeInTheDocument(),
    );
  });

  it("a paste that carries TEXT is never claimed as an image (the Excel rule)", () => {
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const surface = screen.getByTestId("pane-chat");
    const file = new File([new Uint8Array([1, 2])], "grid.png", {
      type: "image/png",
    });
    fireEvent.paste(surface, {
      clipboardData: {
        files: [file],
        getData: (t: string) => (t === "text/plain" ? "Q1\t1234" : ""),
      },
    });
    expect(H.api.posts.filter((p) => p.path === "/documents/upload")).toEqual([]);
  });

  it("a text-free image paste uploads", async () => {
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const surface = screen.getByTestId("pane-chat");
    const file = new File([new Uint8Array([1, 2])], "snip.png", {
      type: "image/png",
    });
    fireEvent.paste(surface, {
      clipboardData: { files: [file], getData: () => "" },
    });
    await waitFor(() =>
      expect(
        H.api.posts.some(
          (p) => p.path === "/documents/upload" && p.body.filename === "snip.png",
        ),
      ).toBe(true),
    );
  });
});

describe("engine picker", () => {
  it("lists Default + AVAILABLE providers with the login labels", async () => {
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const select = screen.getByLabelText("Engine");
    const labels = Array.from(select.querySelectorAll("option")).map(
      (o) => o.textContent,
    );
    expect(labels).toEqual(["Default", "Claude (your login)", "lmstudio"]);
    // openai is in /health but NOT available — it must not be offered.
    expect(labels).not.toContain("openai");
  });

  it("a pick rides the body as `provider` and persists into the thread setup", async () => {
    render(<PaneChat paneId="p1" cwd={CWD} />);
    fireEvent.change(screen.getByLabelText("Engine"), {
      target: { value: "claude-cli" },
    });
    await typeAndSend("hello");
    await waitFor(() => expect(S.stream.bodies.length).toBe(1));
    expect(S.stream.bodies[0].provider).toBe("claude-cli");
    // A fresh pick carries no model pin — never a stale or empty one.
    expect("model" in S.stream.bodies[0]).toBe(false);
    await waitFor(() => expect(H.api.puts.length).toBe(1));
    const setup = H.api.puts[0].body.setup as Record<string, unknown>;
    expect(setup.provider).toBe("claude-cli");
    expect(setup.model).toBe(""); // no stored model for a fresh pick
    expect(setup.workspace_dir).toBe(CWD);
  });
});

describe("project grounding (context spine)", () => {
  it("cwd under a project root passes project_id and shows the chip", async () => {
    H.api.projects = [
      { id: "pr1", name: "Demo", root: "c:\\WORK" }, // case differs on purpose
      { id: "pr2", name: "Other", root: "D:\\other" },
    ];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    // Wait for the CHIP (the thing asserted), not just the fetch.
    await waitFor(() =>
      expect(screen.getByTestId("pane-chat-project")).toHaveTextContent("Demo"),
    );
    await typeAndSend("what is this project?");
    await waitFor(() => expect(S.stream.bodies.length).toBe(1));
    expect(S.stream.bodies[0].project_id).toBe("pr1");
    // ...and the thread save tags into the same project.
    await waitFor(() => expect(H.api.puts.length).toBe(1));
    expect(H.api.puts[0].body.project_id).toBe("pr1");
  });

  it("a cwd OUTSIDE every root sends no project_id (plain folder grounding)", async () => {
    H.api.projects = [{ id: "pr1", name: "Demo", root: "C:\\work\\demo-app" }];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await waitFor(() => expect(H.api.gets).toContain("/projects"));
    await typeAndSend("hi");
    await waitFor(() => expect(S.stream.bodies.length).toBe(1));
    expect("project_id" in S.stream.bodies[0]).toBe(false);
    expect(screen.queryByTestId("pane-chat-project")).not.toBeInTheDocument();
  });

  it("projectForCwd: segment boundary, case-insensitive, most specific wins", () => {
    const projects = [
      { id: "a", name: "A", root: "C:\\work" },
      { id: "b", name: "B", root: "C:\\work\\demo" },
      { id: "c", name: "C", root: "" },
    ];
    // "C:\work" must NOT claim "C:\workshop".
    expect(projectForCwd("C:\\workshop\\x", projects)).toBeNull();
    expect(projectForCwd("c:\\WORK\\demo\\sub", projects)?.id).toBe("b");
    expect(projectForCwd("C:\\work\\other", projects)?.id).toBe("a");
    expect(projectForCwd("C:\\work\\demo", projects)?.id).toBe("b");
  });
});

describe("one thread per pane", () => {
  it("first save PUTs 'new' with the Build title; the real id lands in localStorage; the SECOND save reuses it", async () => {
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("turn one");
    await waitFor(() => expect(H.api.puts.length).toBe(1));
    expect(H.api.puts[0].path).toBe("/chat/threads/new");
    expect(H.api.puts[0].body.title).toBe("Build: demo");
    // The id persists per paneId — the pane finds its room again.
    await waitFor(() =>
      expect(window.localStorage.getItem("ij.pane.thread.p1")).toBe("th_1"),
    );
    expect(paneThreadKey("p1")).toBe("ij.pane.thread.p1");
    // Second turn: the serialized chain writes the REAL id, never "new" again.
    await typeAndSend("turn two");
    await waitFor(() => expect(H.api.puts.length).toBe(2));
    expect(H.api.puts[1].path).toBe("/chat/threads/th_1");
    expect("title" in H.api.puts[1].body).toBe(false); // rename-safe
  });

  it("a stored thread reloads: transcript + engine restore; setup keys the pane does not manage ride forward verbatim", async () => {
    window.localStorage.setItem("ij.pane.thread.p9", "th_9");
    H.api.threads["th_9"] = {
      id: "th_9",
      title: "Build: demo",
      messages: [
        { role: "user", content: "earlier question" },
        { role: "assistant", content: "earlier answer" },
      ],
      setup: {
        tools: ["shell"],
        skill: "deploy",
        approval_mode: "yolo",
        provider: "lmstudio",
        model: "qwen-14b",
        workspace_dir: "C:\\old\\place",
      },
    };
    render(<PaneChat paneId="p9" cwd={CWD} />);
    await waitFor(() =>
      expect(screen.getByText("earlier answer")).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Engine")).toHaveValue("lmstudio");
    await typeAndSend("continue");
    await waitFor(() => expect(S.stream.bodies.length).toBe(1));
    expect(S.stream.bodies[0].provider).toBe("lmstudio");
    // BC1 D5: the pinned MODEL rides with its provider — a pinned
    // lmstudio::qwen-14b thread must not silently run the default model.
    expect(S.stream.bodies[0].model).toBe("qwen-14b");
    // BC1 D1: the thread's armed set rides too.
    expect(S.stream.bodies[0].tools).toEqual(["shell"]);
    // BC1 D4: the stored consent posture rides — yolo must not re-ask here.
    expect(S.stream.bodies[0].approval_mode).toBe("yolo");
    // The whole history rides the wire (stateless backend).
    expect((S.stream.bodies[0].messages as unknown[]).length).toBe(3);
    await waitFor(() => expect(H.api.puts.length).toBe(1));
    expect(H.api.puts[0].path).toBe("/chat/threads/th_9");
    const setup = H.api.puts[0].body.setup as Record<string, unknown>;
    // THE GUARD: unmanaged keys never clobbered by the pane's autosave.
    expect(setup.tools).toEqual(["shell"]);
    expect(setup.skill).toBe("deploy");
    expect(setup.approval_mode).toBe("yolo");
    // Managed keys: cwd rebinds, provider unchanged keeps its stored model.
    expect(setup.workspace_dir).toBe(CWD);
    expect(setup.provider).toBe("lmstudio");
    expect(setup.model).toBe("qwen-14b");
  });

  it("mergeSetup clears the stored model only when the provider actually changed", () => {
    const base = { provider: "lmstudio", model: "qwen-14b", tools: ["shell"] };
    expect(mergeSetup(base, CWD, "lmstudio").model).toBe("qwen-14b");
    expect(mergeSetup(base, CWD, "claude-cli").model).toBe("");
    expect(mergeSetup(base, CWD, "claude-cli").tools).toEqual(["shell"]);
    expect(mergeSetup(null, CWD, "").workspace_dir).toBe(CWD);
    expect(paneTitle("C:\\work\\demo\\")).toBe("Build: demo");
  });
});

describe("receipt + doors: the done frame's server truth renders", () => {
  it("route/adapted/denied land in the TurnReceipt and doors in the DoorsStrip", async () => {
    S.stream.result = {
      reply: "All done.",
      route: { requested: "", provider: "mock", reason: "mock" },
      adapted: { model: "qwen-3b", changes: ["tool_cap:4"] },
      tools_used: ["read_file"],
      deniedTools: ["shell"],
      doors: [{ href: "/workflows", label: "Workflows" }],
    };
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("do the thing");
    // The dishonesty case, visible WITHOUT expanding — the exact fabrication
    // disclosure the receipt exists for.
    await waitFor(() =>
      expect(
        screen.getByText(/mock answer — no real model ran/i),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText(/adapted to qwen-3b/i)).toBeInTheDocument();
    expect(screen.getByText(/1 blocked/i)).toBeInTheDocument();
    const door = screen.getByRole("link", { name: /Workflows/i });
    expect(door).toHaveAttribute("href", "/workflows");
    // ...and the saved bubble carries the same server truth verbatim.
    await waitFor(() => expect(H.api.puts.length).toBe(1));
    const saved = H.api.puts[0].body.messages as Record<string, unknown>[];
    expect(saved[1].route).toEqual({
      requested: "",
      provider: "mock",
      reason: "mock",
    });
    expect(saved[1].doors).toEqual([{ href: "/workflows", label: "Workflows" }]);
  });
});

describe("honest failure states", () => {
  it("a stream error renders AS an error; the streamed partial is kept and marked interrupted; the failed turn still saves", async () => {
    S.stream.reject = new S.FakeStreamError(
      "provider exploded",
      500,
      true,
      false,
      "half an answ",
    );
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await typeAndSend("try this");
    await waitFor(() =>
      expect(screen.getByText(/provider exploded/i)).toBeInTheDocument(),
    );
    // The partial the user watched appear is kept — but never looks complete.
    expect(screen.getByText("half an answ")).toBeInTheDocument();
    expect(
      screen.getByText(/interrupted — this answer is incomplete/i),
    ).toBeInTheDocument();
    // The user bubble is not lost.
    expect(screen.getByText("try this")).toBeInTheDocument();
    // FAILED turns save too (the chat page's own rule) — nothing is lost to a
    // pane close.
    await waitFor(() => expect(H.api.puts.length).toBe(1));
    const saved = H.api.puts[0].body.messages as Record<string, unknown>[];
    expect(saved.length).toBe(2);
    expect(saved[1].interrupted).toBe(true);
  });

  it("daemon down = the app's OfflineHint, composer disabled — no fabricated empty state", async () => {
    D.daemon.online = false;
    render(<PaneChat paneId="p1" cwd={CWD} />);
    expect(
      screen.getByText(/Daemon offline or unreachable/i),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toBeDisabled();
    expect(screen.getByLabelText("Send")).toBeDisabled();
  });

  it("a DELETED thread (404) starts fresh: key cleared, composer usable", async () => {
    window.localStorage.setItem("ij.pane.thread.p1", "th_gone");
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await waitFor(() =>
      expect(window.localStorage.getItem("ij.pane.thread.p1")).toBeNull(),
    );
    await waitFor(() => expect(screen.getByLabelText("Message")).toBeEnabled());
  });

  it("a thread that FAILS to load (non-404) blocks sending — a save would PUT two bubbles over the stored transcript — and Retry recovers", async () => {
    window.localStorage.setItem("ij.pane.thread.p1", "th_boom");
    H.api.failThreads.add("th_boom");
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await waitFor(() =>
      expect(
        screen.getByText(/Couldn't load this pane's conversation/i),
      ).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Message")).toBeDisabled();
    expect(screen.getByLabelText("Send")).toBeDisabled();
    // The key is NOT cleared — the conversation still exists server-side.
    expect(window.localStorage.getItem("ij.pane.thread.p1")).toBe("th_boom");
    // The daemon recovers → Retry loads the transcript and unblocks.
    H.api.failThreads.delete("th_boom");
    H.api.threads["th_boom"] = {
      id: "th_boom",
      messages: [
        { role: "user", content: "before the outage" },
        { role: "assistant", content: "still here" },
      ],
    };
    fireEvent.click(screen.getByRole("button", { name: /retry loading/i }));
    await waitFor(() =>
      expect(screen.getByText("still here")).toBeInTheDocument(),
    );
    await waitFor(() => expect(screen.getByLabelText("Message")).toBeEnabled());
  });
});

describe("mid-turn approval (BC1 D1)", () => {
  const APPROVAL = {
    id: "apr_1",
    callId: "c1",
    tool: "shell",
    args: { command: "npm test" },
  };

  it("the pause is VISIBLE: the card renders the verbatim command; Deny POSTs exactly that decision", async () => {
    S.stream.approval = APPROVAL;
    render(<PaneChat paneId="p1" cwd={CWD} />);
    expect(screen.getByTestId("chat-approval-card")).toBeInTheDocument();
    // The payload, verbatim — approving a command you cannot read is not a
    // decision.
    expect(screen.getByText("npm test")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^deny$/i }));
    await waitFor(() => {
      const dec = H.api.posts.find((p) => p.path === "/chat/approvals/apr_1");
      expect(dec?.body).toEqual({ decision: "deny" });
    });
  });

  it("'Allow for this conversation' POSTs the grant, arms the tool for LATER turns, and persists it into setup.tools", async () => {
    S.stream.approval = APPROVAL;
    render(<PaneChat paneId="p1" cwd={CWD} />);
    fireEvent.click(
      screen.getByRole("button", { name: /allow for this conversation/i }),
    );
    await waitFor(() => {
      const dec = H.api.posts.find((p) => p.path === "/chat/approvals/apr_1");
      expect(dec?.body).toEqual({ decision: "conversation" });
    });
    // The resolved frame clears the hook's approval (real-hook behaviour).
    S.stream.approval = null;
    await typeAndSend("run it again");
    await waitFor(() => expect(S.stream.bodies.length).toBe(1));
    // The grant rides the next turn — "stops asking here" made true.
    expect(S.stream.bodies[0].tools).toEqual(["shell"]);
    // ...and the turn's save persists it into the thread setup.
    await waitFor(() => expect(H.api.puts.length).toBe(1));
    const setup = H.api.puts[0].body.setup as Record<string, unknown>;
    expect(setup.tools).toEqual(["shell"]);
  });
});

describe("approval posture rides the body (BC1 D4)", () => {
  it("a stored always_ask thread sends approval_mode on pane turns", async () => {
    window.localStorage.setItem("ij.pane.thread.pa", "th_a");
    H.api.threads["th_a"] = {
      id: "th_a",
      messages: [
        { role: "user", content: "x" },
        { role: "assistant", content: "strict thread" },
      ],
      setup: { approval_mode: "always_ask" },
    };
    render(<PaneChat paneId="pa" cwd={CWD} />);
    await waitFor(() =>
      expect(screen.getByText("strict thread")).toBeInTheDocument(),
    );
    await typeAndSend("careful now");
    await waitFor(() => expect(S.stream.bodies.length).toBe(1));
    // The consent the user set in /chat is NOT downgraded to the default.
    expect(S.stream.bodies[0].approval_mode).toBe("always_ask");
  });

  it("a fresh pane (no stored posture) OMITS approval_mode — the daemon default rules", async () => {
    render(<PaneChat paneId="pb" cwd={CWD} />);
    await typeAndSend("fresh");
    await waitFor(() => expect(S.stream.bodies.length).toBe(1));
    expect("approval_mode" in S.stream.bodies[0]).toBe(false);
  });
});

describe("model pin (BC1 D5)", () => {
  it("switching provider clears the stale model pin from body AND setup", async () => {
    window.localStorage.setItem("ij.pane.thread.pm", "th_m");
    H.api.threads["th_m"] = {
      id: "th_m",
      messages: [
        { role: "user", content: "x" },
        { role: "assistant", content: "pinned thread" },
      ],
      setup: { provider: "lmstudio", model: "qwen-14b" },
    };
    render(<PaneChat paneId="pm" cwd={CWD} />);
    await waitFor(() =>
      expect(screen.getByText("pinned thread")).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText("Engine"), {
      target: { value: "claude-cli" },
    });
    await typeAndSend("switch it up");
    await waitFor(() => expect(S.stream.bodies.length).toBe(1));
    expect(S.stream.bodies[0].provider).toBe("claude-cli");
    // qwen-14b belongs to lmstudio — it must not ride the new provider.
    expect("model" in S.stream.bodies[0]).toBe(false);
    await waitFor(() => expect(H.api.puts.length).toBeGreaterThanOrEqual(1));
    const last = H.api.puts[H.api.puts.length - 1];
    const setup = last.body.setup as Record<string, unknown>;
    expect(setup.provider).toBe("claude-cli");
    expect(setup.model).toBe("");
  });
});

describe("save-time setup refresh (BC1 D3)", () => {
  it("a setup changed in /chat between mount and save SURVIVES the pane's save", async () => {
    window.localStorage.setItem("ij.pane.thread.ps", "th_s");
    H.api.threads["th_s"] = {
      id: "th_s",
      messages: [
        { role: "user", content: "x" },
        { role: "assistant", content: "shared thread" },
      ],
      setup: { tools: ["shell"], provider: "", model: "" },
    };
    render(<PaneChat paneId="ps" cwd={CWD} />);
    await waitFor(() =>
      expect(screen.getByText("shared thread")).toBeInTheDocument(),
    );
    // THE REVIEWER'S FLOW: while the pane sits open, the user changes this
    // thread's setup in /chat — different tools, a skill, a posture, a pin.
    (H.api.threads["th_s"] as Record<string, unknown>).setup = {
      tools: ["web_search"],
      skill: "review",
      approval_mode: "always_ask",
      provider: "lmstudio",
      model: "qwen-14b",
    };
    await typeAndSend("go");
    await waitFor(() => expect(H.api.puts.length).toBe(1));
    const setup = H.api.puts[0].body.setup as Record<string, unknown>;
    // The FOREIGN change survives — mount-time state is not resurrected over
    // it ("shell" is gone because /chat disarmed it; the pane made no grant).
    expect(setup.tools).toEqual(["web_search"]);
    expect(setup.skill).toBe("review");
    expect(setup.approval_mode).toBe("always_ask");
    // The untouched picker ADOPTS the thread's new pick too.
    expect(setup.provider).toBe("lmstudio");
    expect(setup.model).toBe("qwen-14b");
    // The pane still overwrites the one key it owns.
    expect(setup.workspace_dir).toBe(CWD);
  });
});

describe("engineOptions (pure)", () => {
  it("filters to available, labels the login CLIs, dedupes", () => {
    expect(
      engineOptions([
        { provider: "claude-cli", available: true, class: "cli" },
        { provider: "claude-cli", available: true, class: "cli" },
        { provider: "codex-cli", available: true, class: "cli" },
        { provider: "openai", available: false, class: "api" },
      ]),
    ).toEqual([
      { id: "claude-cli", label: "Claude (your login)" },
      { id: "codex-cli", label: "Codex (your login)" },
    ]);
    expect(engineOptions(undefined)).toEqual([]);
  });
});
