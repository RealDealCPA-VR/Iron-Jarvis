/**
 * v1.369.0 (IJ harness Phase 6) — a Build session's right panes
 * (components/harness/HarnessPanes): plan, terminal, changes, browser, files.
 *
 *  - plan: the running turn's plan_update first, else a kept reply's, else
 *    the session's approved plan; "No plan yet" when none;
 *  - terminal: a normal Build terminal opened in the SESSION's folder through
 *    the page's own add path, drawn by the page's own body; once opened it
 *    stays MOUNTED behind another tab (visibility hidden, parked), never
 *    display:none and never an unmount;
 *  - changes: POST /chat/changes with the session's OWN journal
 *    (`session_id: build:<id>`);
 *  - browser: nothing to preview until there is an address; only this PC or
 *    the user's network opens; an address the session printed is offered;
 *    the paired browser is named, and "not paired" is said;
 *  - files: FilesPanel on the session's folder.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return { ApiError, gets: {} as Record<string, unknown>, posts: [] as [string, unknown][], calls: [] as string[] };
});
vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) => {
    api.calls.push(p);
    return p in api.gets ? Promise.resolve(api.gets[p]) : Promise.reject(new api.ApiError(`unmocked ${p}`, 404));
  },
  post: (p: string, b?: unknown) => {
    api.posts.push([p, b]);
    if (p === "/chat/changes") return Promise.resolve(api.gets["POST /chat/changes"] ?? { changes: [] });
    return Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder, variant }: { folder: string | null; variant?: string }) => (
    <div data-testid="files-panel" data-variant={variant}>
      {folder}
    </div>
  ),
}));

import { HarnessPanes, panePlan, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { decodeSessionView, type BuildSessionView } from "@/lib/buildSessions";
import type { TerminalInfo } from "@/lib/types";

const FOLDER = "C:\\work\\shop";

function view(over: Record<string, unknown> = {}): BuildSessionView {
  const v = decodeSessionView({
    id: "s1",
    title: "Cart",
    folder: FOLDER,
    folder_name: "shop",
    session_id: "build:s1",
    messages: [{ role: "user", content: "Add a cart", at: "2026-10-22T10:00:00" }],
    ...over,
  });
  if (!v) throw new Error("bad fixture");
  return v;
}

function terminalApi(): SessionTerminalApi & {
  opened: string[];
  closed: string[];
  renders: { id: string; parked: boolean }[];
} {
  let live: TerminalInfo[] = [];
  const opened: string[] = [];
  const closed: string[] = [];
  const renders: { id: string; parked: boolean }[] = [];
  return {
    opened,
    closed,
    renders,
    find: (id) => live.find((t) => t.id === id) ?? null,
    open: async (cwd) => {
      opened.push(cwd);
      const info = { id: "term1", cwd, shell: "pwsh", alive: true } as unknown as TerminalInfo;
      live.push(info);
      return info;
    },
    render: (info, { parked, onClose }) => {
      renders.push({ id: info.id, parked });
      return (
        <div data-testid="term-body" data-parked={String(parked)}>
          <button type="button" data-testid="term-x" onClick={onClose}>
            ×
          </button>
        </div>
      );
    },
    close: (id) => {
      closed.push(id);
      live = live.filter((t) => t.id !== id);
    },
  };
}

function renderPanes(v: BuildSessionView, t = terminalApi(), extra: Partial<React.ComponentProps<typeof HarnessPanes>> = {}) {
  render(
    <HarnessPanes
      view={v}
      livePlan={null}
      terminal={t}
      refreshKey={0}
      undoFor={() => null}
      onUndo={async () => {}}
      {...extra}
    />,
  );
  return t;
}

beforeEach(() => {
  api.gets = { "/browser/status": { paired: false } };
  api.posts = [];
  api.calls = [];
  localStorage.clear();
});
afterEach(() => cleanup());

describe("the plan pane", () => {
  it("prefers the running turn's plan, then a kept reply's, then the approved plan", () => {
    const approved = view({
      plan: { text: "1. model\n2. page", steps: [{ step: "model", status: "pending" }, { step: "page", status: "pending" }] },
    });
    expect(panePlan(approved, null)?.steps.map((s) => s.goal)).toEqual(["model", "page"]);
    const kept = view({
      plan: { text: "x", steps: [{ step: "old", status: "pending" }] },
      messages: [
        {
          role: "assistant",
          content: "ok",
          steps: [
            {
              name: "plan_update",
              ok: true,
              ms: 1,
              detail: { input: JSON.stringify({ plan: [{ step: "a", status: "completed" }, { step: "b", status: "in_progress" }] }) },
            },
          ],
        },
      ],
    });
    expect(panePlan(kept, null)?.steps.map((s) => [s.goal, s.state])).toEqual([
      ["a", "done"],
      ["b", "running"],
    ]);
    const live = { steps: [{ index: 0, goal: "live", state: "running" as const }], total: 1 };
    expect(panePlan(kept, live)).toBe(live);
  });

  it("says there is no plan yet, and shows the approved plan's text", () => {
    renderPanes(view());
    expect(screen.getByTestId("pane-plan")).toHaveTextContent("No plan yet.");
    cleanup();
    renderPanes(view({ plan: { text: "Do the model first.", steps: [{ step: "model", status: "pending" }] } }));
    expect(screen.getByTestId("plan-checklist")).toBeInTheDocument();
    expect(screen.getByText("The plan you approved")).toBeInTheDocument();
  });
});

describe("the terminal pane", () => {
  it("opens a normal Build terminal in the session's folder, then keeps it mounted and parked behind another tab", async () => {
    const t = renderPanes(view());
    fireEvent.click(screen.getByTestId("harness-pane-tab-terminal"));
    fireEvent.click(screen.getByTestId("pane-terminal-open"));
    expect(await screen.findByTestId("term-body")).toHaveAttribute("data-parked", "false");
    expect(t.opened).toEqual([FOLDER]);
    // Remembered for the session.
    expect(localStorage.getItem("ij.build.session.terminal.s1")).toBe("term1");
    // Another tab: the terminal stays MOUNTED, hidden with visibility, parked.
    fireEvent.click(screen.getByTestId("harness-pane-tab-files"));
    const layer = screen.getByTestId("pane-terminal-layer");
    expect(layer).toHaveStyle({ visibility: "hidden" });
    expect(layer.style.display).not.toBe("none");
    expect(screen.getByTestId("term-body")).toHaveAttribute("data-parked", "true");
    // Back: visible and unparked, the same body (never re-made).
    fireEvent.click(screen.getByTestId("harness-pane-tab-terminal"));
    expect(screen.getByTestId("pane-terminal-layer")).toHaveStyle({ visibility: "visible" });
    expect(screen.getByTestId("term-body")).toHaveAttribute("data-parked", "false");
    expect(t.opened).toEqual([FOLDER]);
  });

  it("the terminal's × asks first in the tab (never a native prompt); Cancel keeps it, two presses close it", async () => {
    const t = renderPanes(view());
    fireEvent.click(screen.getByTestId("harness-pane-tab-terminal"));
    fireEvent.click(screen.getByTestId("pane-terminal-open"));
    await screen.findByTestId("term-body");
    const native = vi.spyOn(window, "confirm");
    fireEvent.click(screen.getByTestId("term-x"));
    const ask = screen.getByTestId("pane-terminal-close");
    expect(ask).toHaveTextContent(`Close this terminal? It ends the live shell in ${FOLDER}.`);
    fireEvent.click(within(ask).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("pane-terminal-close")).toBeNull();
    expect(t.closed).toEqual([]);
    fireEvent.click(screen.getByTestId("term-x"));
    fireEvent.click(screen.getByRole("button", { name: "Close terminal" }));
    expect(t.closed).toEqual([]); // one press only arms it
    fireEvent.click(screen.getByRole("button", { name: "Confirm close" }));
    expect(t.closed).toEqual(["term1"]);
    expect(await screen.findByTestId("pane-terminal-open")).toBeInTheDocument();
    expect(native).not.toHaveBeenCalled();
  });

  it("offers to open one again when the remembered terminal is gone", () => {
    localStorage.setItem("ij.build.session.terminal.s1", "gone");
    renderPanes(view());
    fireEvent.click(screen.getByTestId("harness-pane-tab-terminal"));
    expect(screen.getByTestId("pane-terminal-open")).toBeInTheDocument();
  });
});

describe("the changes pane", () => {
  it("asks /chat/changes with the session's own journal and lists the files", async () => {
    api.gets["POST /chat/changes"] = {
      changes: [
        {
          path: `${FOLDER}\\cart.ts`,
          rel: "cart.ts",
          name: "cart.ts",
          status: "created",
          added: 3,
          removed: 0,
          diff: "+a\n+b\n+c",
        },
      ],
    };
    renderPanes(view());
    fireEvent.click(screen.getByTestId("harness-pane-tab-changes"));
    expect(await screen.findByTestId("changed-files")).toBeInTheDocument();
    const [, body] = api.posts.find(([p]) => p === "/chat/changes") ?? [];
    expect(body).toMatchObject({ session_id: "build:s1" });
  });

  it("says when nothing changed", async () => {
    renderPanes(view());
    fireEvent.click(screen.getByTestId("harness-pane-tab-changes"));
    expect(await screen.findByTestId("pane-changes-none")).toHaveTextContent("No files changed yet.");
  });
});

describe("the browser pane", () => {
  it("is honest when there is nothing to show, and offers an address the session printed", async () => {
    renderPanes(
      view({
        messages: [
          {
            role: "assistant",
            content: "Started it.",
            steps: [{ name: "shell", ok: true, ms: 5, detail: { output: "  VITE ready\n  Local: http://localhost:5173/\n" } }],
          },
        ],
      }),
    );
    fireEvent.click(screen.getByTestId("harness-pane-tab-browser"));
    expect(screen.getByTestId("preview-empty")).toHaveTextContent("Nothing to preview yet.");
    expect(screen.queryByTestId("preview-frame")).toBeNull();
    fireEvent.click(screen.getByTestId("preview-seen"));
    expect(screen.getByTestId("preview-frame")).toHaveAttribute("src", "http://localhost:5173/");
    // Not paired: nothing to say (v1.376.0 review: a Build session never uses
    // the paired browser, so there is nothing to set up for it).
    await waitFor(() => expect(api.calls.some((c) => c.includes("/browser/status"))).toBe(true));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30)); // the answer lands and renders
    });
    expect(screen.queryByTestId("preview-browser")).toBeNull();
  });

  it("opens only a page on this PC or the user's network, and names a paired browser", async () => {
    api.gets["/browser/status"] = { paired: true, browser_name: "Microsoft Edge", browser_version: "153" };
    renderPanes(view());
    fireEvent.click(screen.getByTestId("harness-pane-tab-browser"));
    fireEvent.change(screen.getByTestId("preview-address"), { target: { value: "https://example.com" } });
    fireEvent.click(screen.getByTestId("preview-open"));
    expect(screen.getByRole("alert")).toHaveTextContent("Only a page on this PC or your own network opens here");
    expect(screen.queryByTestId("preview-frame")).toBeNull();
    fireEvent.change(screen.getByTestId("preview-address"), { target: { value: "http://192.168.1.20:8080" } });
    fireEvent.click(screen.getByTestId("preview-open"));
    expect(screen.getByTestId("preview-frame")).toHaveAttribute("src", "http://192.168.1.20:8080");
    const paired = await screen.findByTestId("preview-browser");
    expect(paired).toHaveTextContent("Microsoft Edge 153");
    // v1.376.0 review: what is TRUE in a Build session — it does not use it.
    expect(paired).toHaveTextContent("A Build session does not use it");
    expect(paired).not.toHaveTextContent("browse the web");
  });
});

describe("the browser pane's remembered address (v1.369.0 review)", () => {
  it("a saved address on this PC opens again; a saved outside address never loads on its own", async () => {
    localStorage.setItem("ij.build.preview.s1", "http://localhost:3000/");
    renderPanes(view());
    fireEvent.click(screen.getByTestId("harness-pane-tab-browser"));
    expect(screen.getByTestId("preview-frame")).toHaveAttribute("src", "http://localhost:3000/");
    cleanup();
    localStorage.setItem("ij.build.preview.s1", "https://example.com/track");
    renderPanes(view());
    fireEvent.click(screen.getByTestId("harness-pane-tab-browser"));
    expect(screen.queryByTestId("preview-frame")).toBeNull();
    expect(screen.getByTestId("preview-empty")).toBeInTheDocument();
  });
});

describe("the files pane", () => {
  it("is the calm FilesPanel on the session's folder", () => {
    renderPanes(view());
    fireEvent.click(screen.getByTestId("harness-pane-tab-files"));
    const p = screen.getByTestId("files-panel");
    expect(p).toHaveTextContent(FOLDER);
    expect(p).toHaveAttribute("data-variant", "calm");
  });

  it("the tabs are a tablist the arrow keys walk", () => {
    renderPanes(view());
    const list = screen.getByTestId("harness-pane-tabs");
    expect(within(list).getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Plan",
      "Terminal",
      "Changes",
      "Browser",
      "Files",
    ]);
    fireEvent.keyDown(screen.getByTestId("harness-pane-tab-plan"), { key: "ArrowRight" });
    expect(screen.getByTestId("harness-pane-tab-terminal")).toHaveAttribute("aria-selected", "true");
  });
});

describe("waits are real", () => {
  it("the changes are read again when a turn ends (refreshKey)", async () => {
    const t = terminalApi();
    const { rerender } = render(
      <HarnessPanes view={view()} livePlan={null} terminal={t} refreshKey={0} undoFor={() => null} onUndo={async () => {}} />,
    );
    fireEvent.click(screen.getByTestId("harness-pane-tab-changes"));
    await waitFor(() => expect(api.posts.filter(([p]) => p === "/chat/changes")).toHaveLength(1));
    rerender(
      <HarnessPanes view={view()} livePlan={null} terminal={t} refreshKey={1} undoFor={() => null} onUndo={async () => {}} />,
    );
    await waitFor(() => expect(api.posts.filter(([p]) => p === "/chat/changes")).toHaveLength(2));
  });
});
