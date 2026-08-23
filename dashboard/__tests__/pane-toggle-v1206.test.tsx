/**
 * v1.206.0 — every Build pane gains a Terminal ⇄ Chat toggle.
 *
 * TWO survival invariants, one per layer, symmetric on purpose:
 *
 *  - THE PTY SURVIVES THE FLIP. The shell session, its WebSocket, and the
 *    scrollback live inside TerminalPane, so the chat view hides that layer
 *    with `visibility` — never display:none, never an unmount. visibility
 *    keeps the holder's real box, so the v1.190.0 fit-before-connect +
 *    ResizeObserver machinery measures true dimensions even while hidden;
 *    display:none would zero the holder and a (re)connect replay would wrap
 *    into a default-sized buffer no later fit can re-wrap.
 *
 *  - THE TURN SURVIVES THE FLIP (the BC1/D2 defect). PaneChat owns an
 *    in-flight turn's stream and thread saves; unmounting it on flip-back
 *    leaves the turn finishing in a dead closure whose late last-write-wins
 *    save can erase a newer turn, or orphans the first exchange if a remount
 *    races the CREATE save — and "check the terminal while the agent works"
 *    is this feature's most natural gesture. So once opened, PaneChat stays
 *    mounted and only visibility flips. Mounting is still LAZY on the first
 *    flip: a never-toggled pane renders no PaneChat at all.
 *
 * jsdom cannot render xterm, and PaneChat is a parallel build — so BOTH
 * dynamics are served by stubs via the next/dynamic mock, whose loader is
 * deliberately never invoked. What this file therefore pins is page.tsx's own
 * behavior: mounted-and-hidden both ways, lazy first mount, per-pane
 * persistence, the toggle sitting outside every drag-handle region, and the
 * pane (not the view) staying the unit the Files tab follows. The seams a
 * rendered test cannot reach are source-pinned (the house idiom — v1.163.0,
 * v1.190.0).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/* ---- api ------------------------------------------------------------------ */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    responses: {} as Record<string, unknown>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  get: (path: string) => {
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    }
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

/* ---- next/dynamic: ONE stub for both panes, loader NEVER invoked ---------- */
// TerminalPane would drag xterm into jsdom; PaneChat is being built in a
// parallel change. The page's two dynamics are told apart by their contract
// props: TerminalPane receives `info`, PaneChat receives `paneId` + `cwd`.
// The chat stub counts its MOUNTS — the D2 pin is "one mount, ever, per
// pane", because a remount cycle is exactly the data-eating defect.

const counters = vi.hoisted(() => ({
  chatMounts: 0,
  /** Everything typed through the per-pane writers, as `${paneId}:${text}`. */
  written: [] as string[],
  /** When false the stub writer reports a dead socket (write → false). */
  writerOk: true,
  registered: [] as string[],
  unregistered: [] as string[],
  /** What each Run click returned — null means the prop never arrived. */
  runResults: [] as Array<boolean | null>,
  /** The code block the stub's Run button hands over on the next click. */
  runPayload: "git status",
}));

vi.mock("next/dynamic", async () => {
  const { useEffect } = await import("react");
  function PaneChatStub({
    paneId,
    cwd,
    onRunCommand,
  }: {
    paneId: string;
    cwd: string;
    onRunCommand?: (cmd: string) => boolean;
  }) {
    useEffect(() => {
      counters.chatMounts += 1;
    }, []);
    return (
      <div data-testid="pane-chat">
        chat:{paneId}:{cwd}
        {/* Stands in for the per-code-block "Run in terminal" button the
            parallel PaneChat build renders — user-clicked, never model-fired. */}
        <button
          type="button"
          data-testid={`chat-run-${paneId}`}
          onClick={() => {
            counters.runResults.push(
              onRunCommand ? onRunCommand(counters.runPayload) : null,
            );
          }}
        >
          run
        </button>
      </div>
    );
  }
  function TerminalPaneStub({
    info,
    onWriterReady,
  }: {
    info: { id: string; shell: string; cwd: string };
    onWriterReady?: (write: ((text: string) => boolean) | null) => void;
  }) {
    // Mirrors the real pane's contract: register the writer on attach,
    // unregister with null on dispose; a dead socket writes nothing and
    // says so (false).
    useEffect(() => {
      counters.registered.push(info.id);
      onWriterReady?.((text: string) => {
        if (!counters.writerOk) return false;
        counters.written.push(`${info.id}:${text}`);
        return true;
      });
      return () => {
        onWriterReady?.(null);
        counters.unregistered.push(info.id);
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return (
      <div data-testid={`terminal-pane-${info.id}`}>
        {/* Mirrors the real pane's shape: the header IS the drag handle… */}
        <header className="ij-term-drag">{info.shell}</header>
        {/* …and this stands in for the xterm holder (the PTY-survival pin). */}
        <div data-testid={`xterm-${info.id}`} className="xterm" />
      </div>
    );
  }
  return {
    default: () => {
      function DynamicStub(props: Record<string, unknown>) {
        if (typeof props.paneId === "string") {
          return (
            <PaneChatStub
              paneId={props.paneId}
              cwd={String(props.cwd)}
              onRunCommand={props.onRunCommand as ((cmd: string) => boolean) | undefined}
            />
          );
        }
        return (
          <TerminalPaneStub
            info={props.info as { id: string; shell: string; cwd: string }}
            onWriterReady={
              props.onWriterReady as
                | ((write: ((text: string) => boolean) | null) => void)
                | undefined
            }
          />
        );
      }
      return DynamicStub;
    },
  };
});

/* ---- react-rnd: passthrough that exposes the drag contract ---------------- */

vi.mock("react-rnd", () => ({
  Rnd: ({
    children,
    onMouseDown,
    dragHandleClassName,
    cancel,
  }: {
    children?: React.ReactNode;
    onMouseDown?: () => void;
    dragHandleClassName?: string;
    cancel?: string;
  }) => (
    <div
      data-testid="rnd"
      data-drag-handle={dragHandleClassName}
      data-cancel={cancel}
      onMouseDown={onMouseDown}
    >
      {children}
    </div>
  ),
}));

/* ---- page chrome ----------------------------------------------------------- */

vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {actions}
    </div>
  ),
}));
vi.mock("@/components/ui", () => ({
  Card: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  OfflineHint: () => <div />,
  ErrorNote: ({ children }: { children?: React.ReactNode }) => <div role="alert">{children}</div>,
  Spinner: ({ label }: { label?: string }) => <div>{label}</div>,
  ConfirmButton: ({ label }: { label: string }) => <button type="button">{label}</button>,
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <div data-testid="directory-tree" />,
}));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => (
    <div data-testid="files-panel">{folder ?? "no-folder"}</div>
  ),
}));

import TerminalsPage from "@/app/terminals/page";

/* ---- fixtures -------------------------------------------------------------- */

const term = (id: string, cwd: string) => ({
  id,
  cwd,
  shell: "pwsh",
  argv: [],
  cols: 120,
  rows: 30,
  alive: true,
  exit_code: null,
  created_at: "2026-08-23T00:00:00Z",
});

function seedApi(terminals: unknown[]) {
  api.responses = {
    "/terminals": { terminals },
    "/terminals/shells": { shells: [] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
  };
}

const chatBtn = (id: string) => screen.getByRole("button", { name: `Chat view for pane ${id}` });
const termBtn = (id: string) =>
  screen.getByRole("button", { name: `Terminal view for pane ${id}` });

async function renderPage(firstId = "t1") {
  render(<TerminalsPage />);
  await screen.findByTestId(`terminal-pane-${firstId}`);
}

beforeEach(() => {
  localStorage.clear();
  counters.chatMounts = 0;
  counters.written = [];
  counters.writerOk = true;
  counters.registered = [];
  counters.unregistered = [];
  counters.runResults = [];
  counters.runPayload = "git status";
  seedApi([term("t1", "C:\\proj\\alpha"), term("t2", "C:\\proj\\beta")]);
});

afterEach(() => {
  cleanup();
});

/* ---- rendered behavior ------------------------------------------------------ */

describe("default view", () => {
  it("every pane starts in terminal view, mounts NO chat, and writes NOTHING to storage", async () => {
    await renderPage();
    expect(screen.getByTestId("terminal-pane-t1")).toBeInTheDocument();
    expect(screen.getByTestId("terminal-pane-t2")).toBeInTheDocument();
    // The laziness pin: before any toggle there is no PaneChat node at all —
    // a never-toggled pane pays nothing for this feature.
    expect(screen.queryByTestId("pane-chat")).toBeNull();
    expect(screen.queryByTestId("chat-layer-t1")).toBeNull();
    expect(counters.chatMounts).toBe(0);
    // Both toggle states render, terminal pressed.
    expect(termBtn("t1")).toHaveAttribute("aria-pressed", "true");
    expect(chatBtn("t1")).toHaveAttribute("aria-pressed", "false");
    // Zero change for existing users: no key until an explicit toggle.
    expect(localStorage.getItem("ij.pane.view.t1")).toBeNull();
    expect(localStorage.getItem("ij.pane.view.t2")).toBeNull();
  });
});

describe("the flip keeps the PTY alive", () => {
  it("chat view keeps the terminal MOUNTED (hidden via visibility), and flips back to the same node", async () => {
    await renderPage();
    fireEvent.click(chatBtn("t1"));

    // Chat is up, with the exact contract props.
    expect(screen.getByTestId("chat-layer-t1")).toHaveStyle({ visibility: "visible" });
    expect(screen.getByTestId("pane-chat")).toHaveTextContent("chat:t1:C:\\proj\\alpha");

    // THE PIN: the xterm container is STILL IN THE DOM — the pane was hidden,
    // not unmounted. Unmounting disposes the terminal and closes its WS.
    expect(screen.getByTestId("xterm-t1")).toBeInTheDocument();
    expect(screen.getByTestId("term-layer-t1")).toHaveStyle({ visibility: "hidden" });

    // The other pane is untouched.
    expect(screen.queryByTestId("chat-layer-t2")).toBeNull();
    expect(screen.getByTestId("term-layer-t2")).toHaveStyle({ visibility: "visible" });

    // Flip back: same terminal node, live scrollback, chat merely hidden.
    fireEvent.click(termBtn("t1"));
    expect(screen.getByTestId("xterm-t1")).toBeInTheDocument();
    expect(screen.getByTestId("term-layer-t1")).toHaveStyle({ visibility: "visible" });
    expect(screen.getByTestId("chat-layer-t1")).toHaveStyle({ visibility: "hidden" });
  });
});

describe("the flip keeps the TURN alive (BC1/D2)", () => {
  it("once opened, PaneChat is NEVER unmounted — flips hide it, and flipping back is the SAME instance", async () => {
    await renderPage();
    fireEvent.click(chatBtn("t1"));
    expect(counters.chatMounts).toBe(1);

    // Mid-turn gesture: check the terminal while the agent works…
    fireEvent.click(termBtn("t1"));
    // …the chat layer is hidden, NOT unmounted: the in-flight turn keeps its
    // live closure, so its save can never race a newer turn from the grave.
    expect(screen.getByTestId("pane-chat")).toBeInTheDocument();
    expect(screen.getByTestId("chat-layer-t1")).toHaveStyle({ visibility: "hidden" });

    // …and back. Still ONE mount, ever: no remount cycle, no orphaned thread.
    fireEvent.click(chatBtn("t1"));
    expect(screen.getByTestId("chat-layer-t1")).toHaveStyle({ visibility: "visible" });
    expect(counters.chatMounts).toBe(1);

    // Repeat flips stay free of remounts.
    fireEvent.click(termBtn("t1"));
    fireEvent.click(chatBtn("t1"));
    expect(counters.chatMounts).toBe(1);
  });
});

describe("persistence", () => {
  it("persists per pane id and restores on a fresh mount", async () => {
    await renderPage();
    fireEvent.click(chatBtn("t1"));
    expect(localStorage.getItem("ij.pane.view.t1")).toBe("chat");
    expect(localStorage.getItem("ij.pane.view.t2")).toBeNull();

    // A whole new mount (leave Build, come back): t1 restores chat, t2 stays
    // terminal with no chat mounted.
    cleanup();
    counters.chatMounts = 0;
    render(<TerminalsPage />);
    await screen.findByTestId("chat-layer-t1");
    expect(screen.getByTestId("pane-chat")).toHaveTextContent("chat:t1:C:\\proj\\alpha");
    expect(screen.queryByTestId("chat-layer-t2")).toBeNull();
    // …and the hidden terminal is still mounted underneath.
    expect(screen.getByTestId("xterm-t1")).toBeInTheDocument();

    // Flipping back persists too — and the restored chat stays mounted.
    fireEvent.click(termBtn("t1"));
    expect(localStorage.getItem("ij.pane.view.t1")).toBe("terminal");
    expect(screen.getByTestId("pane-chat")).toBeInTheDocument();
    expect(counters.chatMounts).toBe(1);
  });
});

describe("the toggle never starts a drag", () => {
  it("sits OUTSIDE every ij-term-drag region, as buttons the Rnd cancel exempts", async () => {
    await renderPage();
    const toggle = screen.getByTestId("pane-view-toggle-t1");
    // Not inside the drag handle: a mousedown here can never begin a drag.
    expect(toggle.closest(".ij-term-drag")).toBeNull();
    // Both controls are <button>s — matched by react-rnd's `cancel` selector.
    const buttons = toggle.querySelectorAll("button");
    expect(buttons.length).toBe(2);
    const rnd = screen.getAllByTestId("rnd")[0];
    expect(rnd.getAttribute("data-cancel")).toContain("button");
    expect(rnd.getAttribute("data-drag-handle")).toBe("ij-term-drag");

    // In chat view the pane must STILL drag by its header — the chat header
    // carries the handle class — while the toggle stays outside it.
    fireEvent.click(chatBtn("t1"));
    const chatHeader = screen
      .getByTestId("chat-layer-t1")
      .querySelector("header.ij-term-drag");
    expect(chatHeader).not.toBeNull();
    expect(screen.getByTestId("pane-view-toggle-t1").closest(".ij-term-drag")).toBeNull();
  });
});

describe("a pane without a cwd", () => {
  it("disables Chat with a reason, and ignores a stale chat key", async () => {
    seedApi([term("t1", "C:\\proj\\alpha"), term("t3", "")]);
    // Even a stale persisted "chat" must not strand a cwd-less pane in a chat
    // view that has nothing to ground itself in — and must not mount PaneChat.
    localStorage.setItem("ij.pane.view.t3", "chat");
    await renderPage();
    await screen.findByTestId("terminal-pane-t3");

    const btn = chatBtn("t3");
    expect(btn).toBeDisabled();
    expect(btn.getAttribute("title")).toMatch(/no cwd/i);
    expect(screen.queryByTestId("chat-layer-t3")).toBeNull();
    expect(counters.chatMounts).toBe(0);
    // A pane WITH a cwd on the same canvas is unaffected.
    expect(chatBtn("t1")).not.toBeDisabled();
  });
});

describe("the Files tab follows the PANE, not the view", () => {
  it("a chat-view pane is still focusable and its cwd drives the Files tab", async () => {
    await renderPage();
    // Initial focus lands on t1 → the Files tab auto-follows its cwd.
    await waitFor(() =>
      expect(screen.getByTestId("files-panel")).toHaveTextContent("C:\\proj\\alpha"),
    );

    // Flip t2 to chat, then focus it (mousedown anywhere in the pane bubbles
    // to the Rnd wrapper exactly as in the real canvas).
    fireEvent.click(chatBtn("t2"));
    fireEvent.mouseDown(screen.getByTestId("chat-layer-t2"));
    await waitFor(() =>
      expect(screen.getByTestId("files-panel")).toHaveTextContent("C:\\proj\\beta"),
    );
  });
});

describe("run-in-pane plumbing (BC2)", () => {
  it("panes register their writers on attach and unregister on dispose", async () => {
    await renderPage();
    expect(counters.registered).toEqual(expect.arrayContaining(["t1", "t2"]));
    expect(counters.unregistered).toEqual([]);
    cleanup();
    expect(counters.unregistered).toEqual(expect.arrayContaining(["t1", "t2"]));
  });

  it("a clicked Run writes the command + Enter and flips the pane so the user WATCHES it", async () => {
    await renderPage();
    fireEvent.click(chatBtn("t1"));
    expect(screen.getByTestId("chat-layer-t1")).toHaveStyle({ visibility: "visible" });

    fireEvent.click(screen.getByTestId("chat-run-t1"));
    // Single line, unchanged shape: the line + "\r" — the byte xterm emits
    // for the Enter key, the only byte ConPTY treats as Enter (the v1.194
    // snippet path deliberately omits it; a clicked Run submits).
    expect(counters.written).toEqual(["t1:git status\r"]);
    // The agreed contract: the caller is TOLD the write landed…
    expect(counters.runResults).toEqual([true]);
    // …and the pane flips to terminal view so the run is watched, with the
    // chat hidden — not unmounted (D2 holds straight through a run).
    expect(screen.getByTestId("term-layer-t1")).toHaveStyle({ visibility: "visible" });
    expect(screen.getByTestId("chat-layer-t1")).toHaveStyle({ visibility: "hidden" });
    expect(screen.getByTestId("pane-chat")).toBeInTheDocument();
    expect(counters.chatMounts).toBe(1);
  });

  it("a fence ending in a blank line still EXECUTES — \\n is NOT Enter in ConPTY", async () => {
    // The BC2 live repro: code ending "\n" used to skip the "\r" entirely —
    // cmd.exe left the command typed-but-unexecuted, PS 5.1 opened a ">>"
    // continuation — while runInPane returned true and flipped the pane
    // "so the user watches it run". Nothing ran. Trailing blank lines are
    // dropped and the last real line still gets its Enter.
    counters.runPayload = "npm test\n";
    await renderPage();
    fireEvent.click(chatBtn("t1"));
    fireEvent.click(screen.getByTestId("chat-run-t1"));
    // Ends with "\r", NOTHING after it.
    expect(counters.written).toEqual(["t1:npm test\r"]);
    expect(counters.runResults).toEqual([true]);
    expect(screen.getByTestId("term-layer-t1")).toHaveStyle({ visibility: "visible" });
  });

  it("multi-line blocks hand over PER LINE — each reviewed line gets its own Enter", async () => {
    // The cmd.exe WELD (live repro): conhost drops mid-block LFs, so a blob
    // ending in one "\r" executed "echo AAA111echo BBB222" — an unreviewed
    // concatenation. Per-line "\r" is also what PSReadLine expects: it is
    // byte-identical to a human typing, so an open construct still buffers.
    counters.runPayload = "echo AAA\necho BBB\n";
    await renderPage();
    fireEvent.click(chatBtn("t1"));
    fireEvent.click(screen.getByTestId("chat-run-t1"));
    expect(counters.written).toEqual(["t1:echo AAA\recho BBB\r"]);
    expect(counters.runResults).toEqual([true]);
  });

  it("an all-blank block refuses: false, nothing written, NO flip", async () => {
    counters.runPayload = "\n  \n\n";
    await renderPage();
    fireEvent.click(chatBtn("t1"));
    fireEvent.click(screen.getByTestId("chat-run-t1"));
    expect(counters.runResults).toEqual([false]);
    expect(counters.written).toEqual([]);
    expect(screen.getByTestId("chat-layer-t1")).toHaveStyle({ visibility: "visible" });
    expect(localStorage.getItem("ij.pane.view.t1")).toBe("chat");
  });

  it("a disconnected writer refuses: false, nothing written, NO flip", async () => {
    counters.writerOk = false; // the stub's socket is down
    await renderPage();
    fireEvent.click(chatBtn("t1"));
    fireEvent.click(screen.getByTestId("chat-run-t1"));

    // false — not null (null would mean the prop never reached PaneChat).
    expect(counters.runResults).toEqual([false]);
    expect(counters.written).toEqual([]);
    // The user is NOT dumped onto a dead terminal: still in chat view, and
    // nothing was persisted as a flip.
    expect(screen.getByTestId("chat-layer-t1")).toHaveStyle({ visibility: "visible" });
    expect(screen.getByTestId("term-layer-t1")).toHaveStyle({ visibility: "hidden" });
    expect(localStorage.getItem("ij.pane.view.t1")).toBe("chat");
  });
});

/* ---- source pins (seams a rendered test cannot reach) ----------------------- */

describe("page.tsx source pins", () => {
  const page = readFileSync(join(process.cwd(), "app", "terminals", "page.tsx"), "utf8");

  it("hides the terminal with VISIBILITY — never display:none, never an unmount", () => {
    // The exact expression: visibility keeps the holder's real box, so the
    // v1.190.0 fit-before-connect machinery measures true dimensions even
    // while hidden. display:none zeroes the holder and a reconnect replay
    // wraps into a default-sized buffer no later fit can re-wrap.
    expect(page).toContain('visibility: view === "chat" ? "hidden" : "visible"');
    // The terminal layer never toggles `display`, and <TerminalPane> is not
    // conditionally rendered on the view.
    const layer = page.slice(page.indexOf("term-layer-"), page.indexOf("<TerminalPane"));
    expect(layer).not.toContain("display");
    expect(page).not.toMatch(/view === "terminal" &&/);
  });

  it("hides the chat the SAME way — lazy first mount, then visibility only", () => {
    // The mirror of the terminal pin (BC1/D2): once opened, the chat layer is
    // gated on chatOpened — a sticky bit — and only its visibility follows
    // the view. `view === "chat" && (` alone would be the unmount defect.
    expect(page).toMatch(/\(view === "chat" \|\| chatOpened\[t\.id\]\) && \(/);
    expect(page).toContain('visibility: view === "chat" ? "visible" : "hidden"');
  });

  it("renders PaneChat to the agreed contract, dynamically like TerminalPane", () => {
    expect(page).toContain('import("@/components/terminal/PaneChat")');
    expect(page).toContain("paneId={t.id}");
    expect(page).toContain("cwd={t.cwd}");
    // The BC2 contract: PaneChat is told whether its Run click landed.
    expect(page).toContain("onRunCommand={(cmd) => runInPane(t.id, cmd)}");
    // Browser-only, like every xterm-adjacent surface on this page.
    const dyn = page.slice(page.indexOf("const PaneChat = dynamic"));
    expect(dyn.slice(0, 400)).toContain("ssr: false");
  });

  it("runInPane hands over PER LINE, refuses empties, and flips only on success", () => {
    const run = page.slice(page.indexOf("const runInPane"));
    // Refusal precedes the flip: a dead pane returns false and the view is
    // untouched — never a flip onto a terminal that got nothing.
    const refuse = run.indexOf("if (!ok) return false;");
    const flip = run.indexOf('setPaneView(id, "terminal")');
    expect(refuse).toBeGreaterThan(-1);
    expect(flip).toBeGreaterThan(refuse);
    // EVERY line is terminated by "\r" — the byte xterm emits for Enter, the
    // only byte ConPTY treats as Enter ("\n" is not; and in cmd.exe conhost
    // drops mid-block LFs, welding a blob into one unreviewed command —
    // both defects live-verified by the BC2 review).
    expect(run).toContain("${line}\\r");
    expect(run).not.toContain("${cmd}\\r"); // the blob shape is the defect
    // Trailing blank lines are dropped; an all-blank block refuses, no flip,
    // in ONE write so a dying socket can't hand over half a block.
    expect(run).toContain("if (lines.length === 0) return false;");
    // The per-line choice is DOCUMENTED honestly where the code lives.
    expect(page).toContain("verbatim PER LINE");
    // Writers live in a ref registry that forgets a disposed pane.
    expect(page).toContain("paneWriters.current[id]");
    expect(page).toMatch(/else delete paneWriters\.current\[id\];/);
  });

  it("persists under the per-pane key, and only on an explicit toggle", () => {
    expect(page).toContain("ij.pane.view.");
    // The seed path READS storage; the only WRITE lives in setPaneView.
    const writes = page.match(/localStorage\.setItem\(paneViewKey/g) ?? [];
    expect(writes.length).toBe(1);
  });

  it("keeps the button exemption on the Rnd, so toggle clicks cannot drag", () => {
    expect(page).toMatch(/cancel="button/);
  });
});

describe("TerminalPane's one-shot focus steal (source-pinned)", () => {
  const pane = readFileSync(
    join(process.cwd(), "components", "terminal", "TerminalPane.tsx"),
    "utf8",
  );

  it("skips the focus grab while the holder is hidden, and still consumes the shot", () => {
    // A pane restored straight into chat view keeps its terminal mounted
    // under visibility:hidden — an invisible PTY grabbing keystrokes is a
    // keylogger-shaped bug in the desktop app. offsetParent misses
    // visibility:hidden; checkVisibility with the visibility option sees it.
    const shot = pane.indexOf("if (!focusedOnce)");
    expect(shot).toBeGreaterThan(-1);
    const block = pane.slice(shot, shot + 1400);
    expect(block).toContain("checkVisibility");
    expect(block).toContain("visibilityProperty: true");
    // The shot is consumed BEFORE the visibility gate: a later reconnect must
    // never become a surprise focus steal mid-interaction (focusedOnce's
    // original job).
    const consume = block.indexOf("focusedOnce = true;");
    const gate = block.indexOf("if (holderVisible) term?.focus();");
    expect(consume).toBeGreaterThan(-1);
    expect(gate).toBeGreaterThan(consume);
    // No API on an odd runtime ⇒ default VISIBLE (focus behaves as before).
    expect(block).toContain(": true;");
  });

  it("exposes the v1.194 snippet write mechanism as an HONEST writer (BC2)", () => {
    // Registered on attach, unregistered with null on dispose.
    expect(pane).toContain("onWriterReady?.(writeToShell)");
    expect(pane).toContain("onWriterReady?.(null)");
    // The writer is the snippet path's mechanism — raw text on the attach WS,
    // read through wsRef at CALL time so reconnects are covered — and refuses
    // (false) on a closed/absent socket instead of pretending it typed.
    const writer = pane.slice(
      pane.indexOf("const writeToShell"),
      pane.indexOf("onWriterReady?.(writeToShell)"),
    );
    expect(writer).toContain("wsRef.current");
    expect(writer).toContain("readyState !== WebSocket.OPEN) return false");
    expect(writer).toContain("live.send(text)");
    expect(writer).toContain("return true");
  });
});
