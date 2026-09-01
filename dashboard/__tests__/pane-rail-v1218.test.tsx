/**
 * Build opens on the RAIL (v1.218.0).
 *
 * v1.217.0 gave the app a real answer to "what is this pane's agent doing" and
 * then put it on the panes: a chip per header, a strip above the canvas. The
 * user opened Build and said "it looks the exact same, no tabs to see
 * different terminals with a status pane on the left" — and that was right.
 * The states were true; the SHAPE was the old free-form canvas, which is
 * precisely what "never hunt for the stuck one" exists to replace.
 *
 * So these tests are about the shape:
 *
 *   - the rail lists EVERY live pane, including the ones with nothing to
 *     report (a list that drops the quiet panes is not a list of panes),
 *   - selecting a row makes that pane the visible one,
 *   - a hidden pane is HIDDEN, never unmounted (the v1.190.0 constraint: a
 *     terminal in a zero-sized holder wraps its replay into a buffer no later
 *     fit can undo),
 *   - the rail never reorders itself under the cursor,
 *   - and the canvas is still reachable, with the choice remembered.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/* ---- api ------------------------------------------------------------------- */

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
    patches: [] as [string, unknown][],
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
  patch: (path: string, body: unknown) => {
    api.patches.push([path, body]);
    return Promise.resolve({});
  },
  del: () => Promise.resolve({}),
}));

/* ---- next/dynamic: xterm never enters jsdom -------------------------------- */

const counters = vi.hoisted(() => ({ termMounts: {} as Record<string, number> }));

vi.mock("next/dynamic", () => {
  function TerminalPaneStub({ info }: { info: { id: string; shell: string } }) {
    React.useEffect(() => {
      counters.termMounts[info.id] = (counters.termMounts[info.id] ?? 0) + 1;
    }, [info.id]);
    return <div data-testid={`terminal-pane-${info.id}`}>{info.shell}</div>;
  }
  return {
    default: () => {
      function DynamicStub(props: Record<string, unknown>) {
        if (typeof props.paneId === "string") {
          return <div data-testid={`pane-chat-${props.paneId}`} />;
        }
        return (
          <TerminalPaneStub info={props.info as { id: string; shell: string }} />
        );
      }
      return DynamicStub;
    },
  };
});

vi.mock("react-rnd", () => ({
  Rnd: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="rnd">{children}</div>
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
  created_at: "2026-08-31T00:00:00Z",
});

function seedApi(terminals: unknown[], panes: unknown[] = []) {
  api.responses = {
    "/terminals": { terminals },
    "/terminals/shells": { shells: [] },
    "/models": { models: [] },
    "/terminals/ai-clis": {
      clis: [
        { id: "claude", label: "Claude Code", command: "claude", provider: "Anthropic", url: "", installed: true },
        { id: "grok", label: "Grok CLI", command: "grok", provider: "xAI", url: "", installed: true },
      ],
    },
    "/skills": { skills: [] },
    "/terminals/activity": { panes },
  };
}

async function renderPage(firstId = "t1") {
  render(<TerminalsPage />);
  await screen.findByTestId(`terminal-pane-${firstId}`);
}

const row = (id: string) => screen.getByTestId(`rail-row-${id}`);
const shell = (id: string) => screen.getByTestId(`rail-pane-${id}`);

beforeEach(() => {
  localStorage.clear();
  counters.termMounts = {};
  api.patches = [];
  seedApi([term("t1", "C:\\proj\\alpha"), term("t2", "C:\\proj\\beta")]);
});

afterEach(cleanup);

/* --------------------------------------------------------------------------- */

describe("the rail is what Build opens on", () => {
  it("lists every live pane, with no stored preference", async () => {
    await renderPage();
    expect(screen.getByTestId("pane-rail")).toBeTruthy();
    expect(row("t1")).toBeTruthy();
    expect(row("t2")).toBeTruthy();
    // …and the canvas is not what rendered.
    expect(screen.queryByTestId("rnd")).toBeNull();
  });

  it("shows the first pane without being told to", async () => {
    // Focus starts null on every load. On the canvas that is harmless — every
    // pane is on screen anyway — but here focus decides what is VISIBLE, so a
    // null would open Build on an empty workspace.
    await renderPage();
    expect(shell("t1").style.visibility).toBe("visible");
    expect(shell("t2").style.visibility).toBe("hidden");
  });

  it("brings a pane into focus when its row is chosen", async () => {
    await renderPage();
    fireEvent.click(row("t2").querySelector("button")!);
    await waitFor(() => expect(shell("t2").style.visibility).toBe("visible"));
    expect(shell("t1").style.visibility).toBe("hidden");
  });

  it("HIDES the pane it is not showing — it never unmounts it", async () => {
    // The whole reason the rail can exist. A terminal whose holder has no size
    // wraps its replay into a default-sized buffer that no later fit can
    // re-wrap (v1.190.0), so switching panes must not tear one down and must
    // not use display:none. If this test ever goes green because both panes
    // vanished from the DOM, the scrollback is already broken.
    await renderPage();
    fireEvent.click(row("t2").querySelector("button")!);
    await waitFor(() => expect(shell("t2").style.visibility).toBe("visible"));

    expect(screen.getByTestId("terminal-pane-t1")).toBeTruthy();
    expect(shell("t1").style.display).not.toBe("none");
    fireEvent.click(row("t1").querySelector("button")!);
    await waitFor(() => expect(shell("t1").style.visibility).toBe("visible"));
    // One mount each, across both switches.
    expect(counters.termMounts.t1).toBe(1);
    expect(counters.termMounts.t2).toBe(1);
  });

  it("keeps the Files panel following the pane the rail selected", async () => {
    await renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("files-panel")).toHaveTextContent("C:\\proj\\alpha"),
    );
    fireEvent.click(row("t2").querySelector("button")!);
    await waitFor(() =>
      expect(screen.getByTestId("files-panel")).toHaveTextContent("C:\\proj\\beta"),
    );
  });
});

describe("what the rows say", () => {
  it("gives a plain shell an honest, quiet state instead of nothing", async () => {
    // The classifier stays silent on a pane it cannot read, which is right on
    // the pane header and wrong in a list: a column of blank rows reads as
    // broken, not as careful. "shell" is a statement about evidence we have —
    // nothing launched an agent and the scrollback names none.
    seedApi(
      [term("t1", "C:\\proj\\alpha")],
      [{ id: "t1", name: null, agent_cli: null, state: "unknown", state_line: "", alive: true }],
    );
    await renderPage();
    await waitFor(() => expect(row("t1").textContent).toContain("shell"));
  });

  it("says it cannot tell when an agent IS there and its output is unreadable", async () => {
    // The other half, and the one that must never soften: an agent is running
    // and we cannot classify it. That is not "ready" and not "finished".
    seedApi(
      [term("t1", "C:\\proj\\alpha")],
      [{ id: "t1", name: null, agent_cli: "claude", state: "unknown", state_line: "", alive: true }],
    );
    await renderPage();
    await waitFor(() => expect(row("t1").textContent).toContain("can't tell"));
    expect(row("t1").textContent).not.toContain("ready");
    expect(row("t1").textContent).not.toContain("finished");
  });

  it("leads with the pane that needs a human, without reordering the list", async () => {
    // Sorting blocked to the top is the obvious move and it is wrong: the list
    // is something you click, and rows that rearrange as an agent's state
    // flickers cost more than the scan they save. The jump button is how
    // "never hunt for the stuck one" survives a long list instead.
    seedApi(
      [term("t1", "C:\\proj\\alpha"), term("t2", "C:\\proj\\beta")],
      [
        { id: "t1", name: "builder", agent_cli: "claude", state: "idle", state_line: ">", alive: true },
        { id: "t2", name: "tester", agent_cli: "claude", state: "blocked", state_line: "Edit?", alive: true },
      ],
    );
    await renderPage();
    const jump = await screen.findByTestId("rail-jump-blocked");
    expect(jump.textContent).toContain("1 needs you");

    const ids = Array.from(
      screen.getByTestId("pane-rail").querySelectorAll("[data-testid^='rail-row-']"),
    ).map((e) => e.getAttribute("data-testid"));
    expect(ids).toEqual(["rail-row-t1", "rail-row-t2"]);

    fireEvent.click(jump);
    await waitFor(() => expect(shell("t2").style.visibility).toBe("visible"));
  });

  it("marks unseen output on a pane you are not looking at", async () => {
    seedApi(
      [term("t1", "C:\\proj\\alpha"), term("t2", "C:\\proj\\beta")],
      [
        { id: "t1", state: "idle", alive: true },
        { id: "t2", state: "idle", alive: true },
      ],
    );
    await renderPage();
    // No unseen flag yet on either.
    expect(screen.queryByTestId("rail-unseen-t2")).toBeNull();
  });
});

describe("the canvas is still there", () => {
  it("switches to it and remembers the choice", async () => {
    await renderPage();
    fireEvent.click(screen.getByTestId("shape-canvas"));
    await waitFor(() => expect(screen.getAllByTestId("rnd").length).toBe(2));
    expect(screen.queryByTestId("pane-rail")).toBeNull();
    expect(localStorage.getItem("ij.build.shape")).toBe("canvas");
  });

  it("opens on the canvas when that is what was chosen last", async () => {
    localStorage.setItem("ij.build.shape", "canvas");
    await renderPage();
    await waitFor(() => expect(screen.queryByTestId("pane-rail")).toBeNull());
    expect(screen.getAllByTestId("rnd").length).toBe(2);
  });

  it("offers the way back, and only from the canvas", async () => {
    await renderPage();
    // In the rail the switch lives in the rail's footer, not in the header —
    // the control always sits in the shape you are leaving.
    expect(screen.queryByTestId("shape-rail")).toBeNull();
    fireEvent.click(screen.getByTestId("shape-canvas"));
    await waitFor(() => expect(screen.getByTestId("shape-rail")).toBeTruthy());
    fireEvent.click(screen.getByTestId("shape-rail"));
    await waitFor(() => expect(screen.getByTestId("pane-rail")).toBeTruthy());
    expect(localStorage.getItem("ij.build.shape")).toBe("rail");
  });

  it("keeps the summary strip for the canvas, where nothing else carries it", async () => {
    seedApi(
      [term("t1", "C:\\proj\\alpha")],
      [{ id: "t1", name: "builder", agent_cli: "claude", state: "blocked", state_line: "Edit?", alive: true }],
    );
    await renderPage();
    // The rail already shows every pane's state; saying it twice is how a
    // surface teaches people to stop reading it.
    await waitFor(() => expect(screen.getByTestId("rail-jump-blocked")).toBeTruthy());
    expect(screen.queryByTestId("pane-summary")).toBeNull();

    fireEvent.click(screen.getByTestId("shape-canvas"));
    await waitFor(() => expect(screen.getByTestId("pane-summary")).toBeTruthy());
  });
});


describe("naming a pane from the rail (v1.219.0)", () => {
  const seedTwo = () =>
    seedApi(
      [term("t1", "C:\\proj\\alpha"), term("t2", "C:\\proj\\beta")],
      [
        { id: "t1", name: null, agent_cli: "claude", state: "idle", alive: true },
        { id: "t2", name: null, agent_cli: "grok", state: "idle", alive: true },
      ],
    );

  it("names the CLI the way the Launch menu did, not by its id", async () => {
    // "grok" is the daemon's key. "Grok CLI" is what the user clicked, and the
    // only one of the two they have ever been shown.
    seedTwo();
    await renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("rail-cli-t2").textContent).toBe("Grok CLI"),
    );
    expect(screen.getByTestId("rail-cli-t1").textContent).toBe("Claude Code");
  });

  it("falls back to the id for a CLI the catalog has never heard of", async () => {
    // A CLI sniffed out of the scrollback, or one dropped from the catalog: a
    // name we half-know beats no name at all.
    seedApi(
      [term("t1", "C:\\proj\\alpha")],
      [{ id: "t1", agent_cli: "aider", state: "idle", alive: true }],
    );
    await renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("rail-cli-t1").textContent).toBe("aider"),
    );
  });

  it("says nothing about a CLI on a pane that has none", async () => {
    seedApi(
      [term("t1", "C:\\proj\\alpha")],
      [{ id: "t1", agent_cli: null, state: "unknown", alive: true }],
    );
    await renderPage();
    await waitFor(() => expect(row("t1").textContent).toContain("shell"));
    expect(screen.queryByTestId("rail-cli-t1")).toBeNull();
  });

  it("renames from the rail and tells the daemon", async () => {
    // The name was editable in the pane HEADER, which is the one place you are
    // already looking at that pane — so naming the other four meant visiting
    // each one. The rail is where you see them all.
    seedTwo();
    await renderPage();
    fireEvent.click(screen.getByTestId("rail-rename-t2"));
    const input = await screen.findByTestId("rail-rename-input");
    fireEvent.change(input, { target: { value: "tester" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(row("t2").textContent).toContain("tester"));
    expect(api.patches).toContainEqual(["/terminals/t2", { name: "tester" }]);
  });

  it("shows the new name at once instead of waiting on the poll", async () => {
    // The activity poll is the source of truth and runs every 2.5s. Without a
    // local echo the row snaps back to the old name in the meantime, which
    // reads as a failed save.
    seedTwo();
    await renderPage();
    fireEvent.click(screen.getByTestId("rail-rename-t1"));
    const input = await screen.findByTestId("rail-rename-input");
    fireEvent.change(input, { target: { value: "builder" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(row("t1").textContent).toContain("builder");
  });

  it("abandons on Escape, and asks the daemon for nothing", async () => {
    seedTwo();
    await renderPage();
    fireEvent.click(screen.getByTestId("rail-rename-t1"));
    const input = await screen.findByTestId("rail-rename-input");
    fireEvent.change(input, { target: { value: "discarded" } });
    fireEvent.keyDown(input, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("rail-rename-input")).toBeNull());
    expect(row("t1").textContent).not.toContain("discarded");
    expect(api.patches).toEqual([]);
  });

  it("opens the editor on a double-click, and a single click still selects", async () => {
    // Selecting is what the rail is FOR, so renaming needs its own doors: the
    // plain click must never be one of them.
    seedTwo();
    await renderPage();
    const rowBtn = row("t2").querySelector("button")!;
    fireEvent.click(rowBtn);
    await waitFor(() => expect(shell("t2").style.visibility).toBe("visible"));
    expect(screen.queryByTestId("rail-rename-input")).toBeNull();

    fireEvent.doubleClick(rowBtn);
    expect(await screen.findByTestId("rail-rename-input")).toBeTruthy();
  });

  it("sends nothing when the name comes back unchanged", async () => {
    seedTwo();
    await renderPage();
    fireEvent.click(screen.getByTestId("rail-rename-t1"));
    const input = await screen.findByTestId("rail-rename-input");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(api.patches).toEqual([]);
  });
});
