import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

/**
 * v1.168.0 P2 — job-files-handover: a finished session HANDS OVER its files.
 *
 * What is being guarded, each with a silent failure mode:
 *
 *  - `sessionFileRows` joins the result's workspace-RELATIVE lists to the
 *    ABSOLUTE `documents` mirror BY INDEX (`documents[i]` ==
 *    `(created+changed)[i]` — agents/outcome.py builds it exactly so). Getting
 *    that join wrong previews the WRONG FILE, which looks perfectly plausible.
 *  - the fallback join uses the workspace's own separator flavor — a "/" glued
 *    onto `C:\...` builds a mixed path the daemon 400s on.
 *  - the panel renders NOTHING for a session without files — a permanent empty
 *    "Files" box on every session page is noise, not handover.
 *  - the footer states the REAL totals when the server cap clipped the list —
 *    a capped list that looks complete is the silent-truncation lie.
 *  - TeamTree chips hang each file off the CHILD whose ledger journaled it,
 *    and "+N more" comes from the child's `*_total`s, not the clipped list.
 */

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

/* ---- Session detail page harness (mirrors team-tree-v1166's) ------------- */
const pageApi = vi.hoisted(() => ({
  detail: null as unknown,
  events: [] as unknown[],
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
  useEvents: () => ({ events: pageApi.events }),
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
// The real DocPreview fetches /documents/preview on mount; the page test pins
// the WIRING (row click → preview receives that exact path), not the panel.
vi.mock("@/components/chat/DocPreview", () => ({
  DocPreview: ({ path }: { path: string }) => (
    <div data-testid="doc-preview">{path}</div>
  ),
  appLabelFor: () => "app",
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

import {
  SessionFiles,
  sessionFileRows,
  handoverNote,
  joinWorkspace,
  fileDownloadHref,
  type SessionResult,
} from "@/components/sessions/SessionFiles";
import { TeamTree, type TeamResponse } from "@/components/sessions/TeamTree";
import SessionDetailPage from "@/app/sessions/[id]/page";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  api.calls = [];
  api.responses = {};
  pageApi.detail = null;
  pageApi.events = [];
});

/* ---------------------------------------------------------------- fixtures */

const WS = "C:\\Users\\VR\\.ironjarvis\\sessions\\s-1";
const ABS_REPORT = `${WS}\\report.md`;
const ABS_DATA = `${WS}\\out\\data.xlsx`;
const ABS_NOTES = `${WS}\\notes.txt`;

const RESULT: SessionResult = {
  found: true,
  session_id: "s-1",
  files_created: ["report.md", "out\\data.xlsx"],
  files_changed: ["notes.txt"],
  files_created_total: 2,
  files_changed_total: 1,
  documents: [ABS_REPORT, ABS_DATA, ABS_NOTES],
  revertable: 2,
  reverted: 0,
};

/* ------------------------------------------------- sessionFileRows (pure) */

describe("sessionFileRows", () => {
  it("maps documents[i] to (created+changed)[i] and tags each row's change kind", () => {
    const rows = sessionFileRows(RESULT, WS);
    expect(rows).toEqual([
      { path: ABS_REPORT, rel: "report.md", change: "created" },
      { path: ABS_DATA, rel: "out\\data.xlsx", change: "created" },
      { path: ABS_NOTES, rel: "notes.txt", change: "changed" },
    ]);
  });

  it("falls back to a workspace join for entries past the documents cap", () => {
    const rows = sessionFileRows(
      { ...RESULT, documents: [ABS_REPORT] },
      WS,
    );
    expect(rows[0].path).toBe(ABS_REPORT);
    expect(rows[1].path).toBe(`${WS}\\out\\data.xlsx`);
    expect(rows[2].path).toBe(`${WS}\\notes.txt`);
  });

  it("a relative entry with NO documents and NO workspace passes through (honest, still labeled)", () => {
    const rows = sessionFileRows(
      { found: true, files_created: ["a.md"], documents: [] },
      "",
    );
    expect(rows).toEqual([{ path: "a.md", rel: "a.md", change: "created" }]);
  });

  it("an outside-workspace ABSOLUTE rel passes through untouched", () => {
    const rows = sessionFileRows(
      {
        found: true,
        files_created: ["D:\\elsewhere\\out.pdf"],
        documents: [],
      },
      WS,
    );
    expect(rows[0].path).toBe("D:\\elsewhere\\out.pdf");
  });

  it("empty entries are skipped, not rendered as blank rows", () => {
    const rows = sessionFileRows(
      { found: true, files_created: ["", "a.md"], documents: ["", ABS_REPORT] },
      WS,
    );
    expect(rows).toEqual([
      { path: ABS_REPORT, rel: "a.md", change: "created" },
    ]);
  });
});

describe("joinWorkspace", () => {
  it("uses backslash for a Windows root", () => {
    expect(joinWorkspace("C:\\ws", "a.md")).toBe("C:\\ws\\a.md");
  });
  it("uses forward slash for a posix root", () => {
    expect(joinWorkspace("/home/vr/ws", "a/b.md")).toBe("/home/vr/ws/a/b.md");
  });
  it("strips trailing separators before joining", () => {
    expect(joinWorkspace("C:\\ws\\", "a.md")).toBe("C:\\ws\\a.md");
    expect(joinWorkspace("/home/vr/ws/", "a.md")).toBe("/home/vr/ws/a.md");
  });
});

/* --------------------------------------------------- handoverNote (pure) */

describe("handoverNote", () => {
  it("states the real created/changed totals and their provenance", () => {
    expect(handoverNote(RESULT, 3)).toBe(
      "2 created · 1 changed — from the session's tool ledger",
    );
  });

  it("a capped list says how much of it is shown (honest truncation)", () => {
    // The clause is "showing N of M" — a COUNT, never "the first N": caps are
    // per kind, so the shown rows are NOT the first N of the combined list
    // (with 120 created + 4 changed, rows = first 50 created + ALL 4 changed).
    const capped: SessionResult = {
      found: true,
      files_created: ["a", "b", "c"],
      files_created_total: 120,
      files_changed: [],
      files_changed_total: 4,
    };
    expect(handoverNote(capped, 3)).toBe(
      "120 created · 4 changed — from the session's tool ledger · showing 3 of 124",
    );
    expect(handoverNote(capped, 3)).not.toContain("first");
  });

  it("reverted actions are called out, singular and plural", () => {
    expect(handoverNote({ ...RESULT, reverted: 1 }, 3)).toContain(
      "1 action reverted",
    );
    expect(handoverNote({ ...RESULT, reverted: 2 }, 3)).toContain(
      "2 actions reverted",
    );
  });
});

/* ------------------------------------------------------- fileDownloadHref */

describe("fileDownloadHref", () => {
  it("builds the /documents/file URL with token and the server-side download flag", () => {
    expect(fileDownloadHref(ABS_REPORT)).toBe(
      `http://api.test/documents/file?path=${encodeURIComponent(
        ABS_REPORT,
      )}&token=tok&download=1`,
    );
  });
});

/* --------------------------------------------------- SessionFiles render */

describe("SessionFiles", () => {
  it("renders each ledger file with its basename; row click previews the ABSOLUTE path", async () => {
    api.responses["/sessions/s-1/result"] = RESULT;
    const onPreview = vi.fn();
    render(
      <SessionFiles
        sessionId="s-1"
        workspacePath={WS}
        active={false}
        onPreview={onPreview}
      />,
    );
    expect(await screen.findByText("report.md")).toBeInTheDocument();
    expect(screen.getByText("data.xlsx")).toBeInTheDocument();
    expect(screen.getByText("notes.txt")).toBeInTheDocument();
    // The rail's own header counts the rows.
    expect(screen.getByText("3 files")).toBeInTheDocument();

    fireEvent.click(screen.getByTitle(ABS_DATA));
    expect(onPreview).toHaveBeenCalledWith(ABS_DATA);
  });

  it("each row's download anchor carries the tokened attachment URL", async () => {
    api.responses["/sessions/s-1/result"] = RESULT;
    render(
      <SessionFiles
        sessionId="s-1"
        workspacePath={WS}
        active={false}
        onPreview={() => {}}
      />,
    );
    const a = (await screen.findByLabelText(
      "Download report.md",
    )) as HTMLAnchorElement;
    expect(a.getAttribute("href")).toBe(
      `http://api.test/documents/file?path=${encodeURIComponent(
        ABS_REPORT,
      )}&token=tok&download=1`,
    );
    expect(a.getAttribute("download")).toBe("report.md");
  });

  it("renders the honest footer with the ledger totals", async () => {
    api.responses["/sessions/s-1/result"] = RESULT;
    render(
      <SessionFiles
        sessionId="s-1"
        workspacePath={WS}
        active={false}
        onPreview={() => {}}
      />,
    );
    expect(
      await screen.findByText(
        "2 created · 1 changed — from the session's tool ledger",
      ),
    ).toBeInTheDocument();
  });

  it("renders NOTHING for a session without files (no empty box)", async () => {
    api.responses["/sessions/s-empty/result"] = {
      found: true,
      files_created: [],
      files_changed: [],
      documents: [],
    };
    const { container } = render(
      <SessionFiles
        sessionId="s-empty"
        workspacePath={WS}
        active={false}
        onPreview={() => {}}
      />,
    );
    await waitFor(() =>
      expect(api.calls).toContain("/sessions/s-empty/result"),
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders NOTHING when the result says found:false", async () => {
    api.responses["/sessions/s-gone/result"] = { found: false };
    const { container } = render(
      <SessionFiles
        sessionId="s-gone"
        workspacePath=""
        active={false}
        onPreview={() => {}}
      />,
    );
    await waitFor(() => expect(api.calls).toContain("/sessions/s-gone/result"));
    expect(container.firstChild).toBeNull();
  });

  it("renders NOTHING when the endpoint is unreachable (absent beats an error box)", async () => {
    const { container } = render(
      <SessionFiles
        sessionId="s-err"
        workspacePath=""
        active={false}
        onPreview={() => {}}
      />,
    );
    await waitFor(() => expect(api.calls).toContain("/sessions/s-err/result"));
    expect(container.firstChild).toBeNull();
  });

  it("polls (~8s) while the session is active, and only then", async () => {
    vi.useFakeTimers();
    api.responses["/sessions/s-1/result"] = RESULT;
    render(
      <SessionFiles
        sessionId="s-1"
        workspacePath={WS}
        active
        onPreview={() => {}}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(
      api.calls.filter((c) => c === "/sessions/s-1/result").length,
    ).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });
    expect(
      api.calls.filter((c) => c === "/sessions/s-1/result").length,
    ).toBe(2);
  });

  it("does NOT poll for a finished session", async () => {
    vi.useFakeTimers();
    api.responses["/sessions/s-1/result"] = RESULT;
    render(
      <SessionFiles
        sessionId="s-1"
        workspacePath={WS}
        active={false}
        onPreview={() => {}}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(
      api.calls.filter((c) => c === "/sessions/s-1/result").length,
    ).toBe(1);
  });

  it("a reloadNonce bump refetches a FINISHED session's result (undo must not leave stale counts)", async () => {
    api.responses["/sessions/s-1/result"] = RESULT;
    const { rerender } = render(
      <SessionFiles
        sessionId="s-1"
        workspacePath={WS}
        active={false}
        onPreview={() => {}}
        reloadNonce={0}
      />,
    );
    expect(
      await screen.findByText(
        "2 created · 1 changed — from the session's tool ledger",
      ),
    ).toBeInTheDocument();

    // The Time-travel feed undid the notes.txt write; the ledger now says so.
    api.responses["/sessions/s-1/result"] = {
      ...RESULT,
      files_changed: [],
      files_changed_total: 0,
      documents: [ABS_REPORT, ABS_DATA],
      reverted: 1,
    };
    rerender(
      <SessionFiles
        sessionId="s-1"
        workspacePath={WS}
        active={false}
        onPreview={() => {}}
        reloadNonce={1}
      />,
    );
    expect(
      await screen.findByText(
        "2 created · 1 action reverted — from the session's tool ledger",
      ),
    ).toBeInTheDocument();
    // The reverted file's row is gone — no preview offer for a deleted file.
    expect(screen.queryByText("notes.txt")).toBeNull();
  });
});

/* ------------------------------------------- TeamTree per-delegate files */

function teamWithChild(childId: string): TeamResponse {
  return {
    found: true,
    session_id: "s-root",
    children: [
      {
        id: childId,
        task: `task for ${childId}`,
        agent_type: "coder",
        provider: "mock",
        model: "mock-model",
        status: "completed",
        workspace_path: "C:\\w",
        summary: "",
        created_at: "2026-08-12T10:00:00Z",
        finished_at: null,
        parent_run_id: "r-root",
      },
    ],
    runs: [
      {
        id: "r-root",
        session_id: "s-root",
        parent_id: null,
        agent_type: "supervisor",
        state: "completed",
      },
    ],
  };
}

describe("TeamTree file chips (v1.168.0)", () => {
  it("hangs a child's ledger files off ITS node; chip click previews the absolute path", async () => {
    api.responses["/sessions/s-root/team"] = teamWithChild("s-child");
    api.responses["/sessions/s-child/result"] = {
      found: true,
      files_created: ["workbook.xlsx"],
      files_changed: [],
      files_created_total: 1,
      files_changed_total: 0,
      documents: ["C:\\w\\workbook.xlsx"],
    };
    const onPreviewFile = vi.fn();
    render(
      <TeamTree sessionId="s-root" active={false} onPreviewFile={onPreviewFile} />,
    );

    expect(await screen.findByTestId("team-files")).toBeInTheDocument();
    expect(screen.getByText("workbook.xlsx")).toBeInTheDocument();
    const chip = screen.getByTitle("C:\\w\\workbook.xlsx");
    expect(chip.tagName).toBe("BUTTON");
    fireEvent.click(chip);
    expect(onPreviewFile).toHaveBeenCalledWith("C:\\w\\workbook.xlsx");
  });

  it('"+N more" uses the child\'s REAL totals, not the clipped list length', async () => {
    api.responses["/sessions/s-root/team"] = teamWithChild("s-many");
    api.responses["/sessions/s-many/result"] = {
      found: true,
      files_created: ["a.md", "b.md", "c.md", "d.md", "e.md", "f.md"],
      files_changed: [],
      files_created_total: 40,
      files_changed_total: 0,
      documents: [
        "C:\\w\\a.md",
        "C:\\w\\b.md",
        "C:\\w\\c.md",
        "C:\\w\\d.md",
        "C:\\w\\e.md",
        "C:\\w\\f.md",
      ],
    };
    render(
      <TeamTree sessionId="s-root" active={false} onPreviewFile={() => {}} />,
    );
    await screen.findByTestId("team-files");
    // 5 chips shown, and the remainder counts against the ledger total (40).
    expect(screen.getByText("e.md")).toBeInTheDocument();
    expect(screen.queryByText("f.md")).toBeNull();
    expect(screen.getByText("+35 more")).toBeInTheDocument();
  });

  it("without an onPreviewFile handler, chips are labels, never dead buttons", async () => {
    api.responses["/sessions/s-root/team"] = teamWithChild("s-child");
    api.responses["/sessions/s-child/result"] = {
      found: true,
      files_created: ["workbook.xlsx"],
      files_changed: [],
      documents: ["C:\\w\\workbook.xlsx"],
    };
    render(<TeamTree sessionId="s-root" active={false} />);
    await screen.findByTestId("team-files");
    expect(screen.getByTitle("C:\\w\\workbook.xlsx").tagName).toBe("SPAN");
  });

  it("a child whose result cannot load still renders in the tree — just without chips", async () => {
    api.responses["/sessions/s-root/team"] = teamWithChild("s-child");
    // /sessions/s-child/result is unmocked → rejects.
    render(
      <TeamTree sessionId="s-root" active={false} onPreviewFile={() => {}} />,
    );
    expect(await screen.findByText("task for s-child")).toBeInTheDocument();
    expect(screen.queryByTestId("team-files")).toBeNull();
  });

  it("a transient result failure on one poll keeps the last-good chips; only a SUCCESSFUL empty clears them", async () => {
    vi.useFakeTimers();
    api.responses["/sessions/s-root/team"] = teamWithChild("s-child");
    api.responses["/sessions/s-child/result"] = {
      found: true,
      files_created: ["workbook.xlsx"],
      files_changed: [],
      files_created_total: 1,
      files_changed_total: 0,
      documents: ["C:\\w\\workbook.xlsx"],
    };
    render(
      <TeamTree sessionId="s-root" active onPreviewFile={() => {}} />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("workbook.xlsx")).toBeInTheDocument();

    // Poll 2: the child's result endpoint hiccups (unmocked → rejects). The
    // chips already shown must survive — setFiles merges, never replaces.
    delete api.responses["/sessions/s-child/result"];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });
    expect(screen.getByText("workbook.xlsx")).toBeInTheDocument();

    // Poll 3: a SUCCESSFUL response says the ledger holds no files — THAT is
    // the one answer allowed to clear the entry.
    api.responses["/sessions/s-child/result"] = {
      found: true,
      files_created: [],
      files_changed: [],
      documents: [],
    };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });
    expect(screen.queryByText("workbook.xlsx")).toBeNull();
    expect(screen.queryByTestId("team-files")).toBeNull();
  });
});

/* --------------------------------------------- session detail page wiring */

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
      workspace_path: WS,
      summary: "",
      origin: null,
      created_at: "2026-08-12T10:00:00Z",
      finished_at: "2026-08-12T10:05:00Z",
      ...over,
    },
    transcript: { runs: [], tools: [] },
  };
}

describe("session detail page (P2 wiring)", () => {
  it("renders the origin chip in the detail header when the session has an origin", async () => {
    setDetail("s-1", { origin: "job:agents" });
    render(<SessionDetailPage params={fakeParams("s-1")} />);
    await act(async () => {});
    const chip = screen.getByTestId("origin-chip");
    expect(chip.textContent).toBe("job:agents");
  });

  it("no origin → no chip (a guessed 'user' chip would be a lie)", async () => {
    setDetail("s-1");
    render(<SessionDetailPage params={fakeParams("s-1")} />);
    await act(async () => {});
    expect(screen.queryByTestId("origin-chip")).toBeNull();
  });

  it("shows the files panel and swaps in DocPreview for the clicked row's path", async () => {
    setDetail("s-1");
    api.responses["/sessions/s-1/result"] = RESULT;
    render(<SessionDetailPage params={fakeParams("s-1")} />);

    expect(await screen.findByTestId("session-files")).toBeInTheDocument();
    expect(screen.queryByTestId("doc-preview")).toBeNull();

    fireEvent.click(screen.getByTitle(ABS_REPORT));
    const preview = await screen.findByTestId("doc-preview");
    expect(preview.textContent).toBe(ABS_REPORT);
  });

  it("a session with no files shows no files panel at all", async () => {
    setDetail("s-1");
    api.responses["/sessions/s-1/result"] = {
      found: true,
      files_created: [],
      files_changed: [],
      documents: [],
    };
    render(<SessionDetailPage params={fakeParams("s-1")} />);
    await waitFor(() => expect(api.calls).toContain("/sessions/s-1/result"));
    expect(screen.queryByTestId("session-files")).toBeNull();
  });

  it("a tool.executed ledger event (an undo from Time-travel) refetches the FINISHED session's files panel", async () => {
    setDetail("s-1"); // status "completed" — the panel does not poll on its own
    api.responses["/sessions/s-1/result"] = RESULT;
    const { rerender } = render(<SessionDetailPage params={fakeParams("s-1")} />);
    expect(await screen.findByTestId("session-files")).toBeInTheDocument();
    const before = api.calls.filter(
      (c) => c === "/sessions/s-1/result",
    ).length;

    // The undo lands as a ledger row → tool.executed on the event stream; the
    // server's result now carries the reverted count and drops the file.
    api.responses["/sessions/s-1/result"] = {
      ...RESULT,
      files_changed: [],
      files_changed_total: 0,
      documents: [ABS_REPORT, ABS_DATA],
      reverted: 1,
    };
    pageApi.events = [
      {
        id: "e-undo",
        type: "tool.executed",
        session_id: "s-1",
        ts: "2026-08-12T10:06:00Z",
        payload: { tool: "undo", ok: true, mode: "auto" },
      },
    ];
    rerender(<SessionDetailPage params={fakeParams("s-1")} />);

    await waitFor(() =>
      expect(
        api.calls.filter((c) => c === "/sessions/s-1/result").length,
      ).toBe(before + 1),
    );
    // The refetched footer calls out the revert; the stale row is gone.
    expect(
      await screen.findByText(
        "2 created · 1 action reverted — from the session's tool ledger",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("notes.txt")).toBeNull();
  });
});
