/**
 * Chat actions v1.168.0 — undo where you look + promote to knowledge.
 *
 * What carries weight here:
 *  - the PATH JOIN (normalizeFsPath / joinUndoByPath): workspace + relative
 *    path from a GET /undo row must land on the exact absolute string the
 *    rail/receipt display, across separator flavours — asserted by VALUE;
 *  - rows that cannot be matched (no workspace, no path) are SKIPPED, so no
 *    file ever grows an undo button the journal cannot back;
 *  - the confirm wording says what will actually happen: a created file's
 *    undo REMOVES it — "restore" wording there would confirm a deletion;
 *  - ArtifactsRail: undo renders only for matched rows, greys (not vanishes)
 *    with the honest reason when not undoable, calls onUndo with the ACTION
 *    ID + FULL path, and surfaces a rejection instead of swallowing it;
 *  - ArtifactsRail promote: disabled-with-reason when no project is bound,
 *    check only after the promise RESOLVED, server error surfaced;
 *  - ArtifactsRail clears the promote check-flash timer on unmount — a timer
 *    outliving the rail fires setState on an unmounted component;
 *  - TurnReceipt: the undo affordance sits under the file chip, same rules;
 *  - revertedActionIds: an undo performed on ANOTHER surface (Timeline page,
 *    second window) reaches this page via action.reverted frames;
 *  - source hygiene: chat/page.tsx carries no control bytes — a literal NUL
 *    once made git classify the whole file as BINARY (EOL normalization off,
 *    diff = "Binary files differ", blame destroyed).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  ArtifactsRail,
  confirmUndoPrompt,
  joinUndoByPath,
  normalizeFsPath,
  revertedActionIds,
  type UndoRowLike,
} from "@/components/chat/ArtifactsRail";
import { TurnReceipt } from "@/components/chat/TurnReceipt";

// The rail's Open action posts through the shared api module; mocked at the
// seam (mirroring the real ApiError shape) so tests stay offline.
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return { post: vi.fn(async () => ({ ok: true })), ApiError };
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const WS = "C:\\Users\\VR\\.ironjarvis\\home\\uploads";
const MEMO = `${WS}\\memo.txt`;

describe("normalizeFsPath — the join identity", () => {
  it("unifies separators to '/' and strips trailing ones — by VALUE", () => {
    expect(normalizeFsPath("C:\\ws\\memo.txt")).toBe("C:/ws/memo.txt");
    expect(normalizeFsPath("C:/ws/memo.txt")).toBe("C:/ws/memo.txt");
    expect(normalizeFsPath("C:\\ws\\out\\")).toBe("C:/ws/out");
    expect(normalizeFsPath("/home/vr/x.md/")).toBe("/home/vr/x.md");
  });

  it("collapses duplicate separators (a workspace joined with '/'+rel)", () => {
    expect(normalizeFsPath("C:\\ws\\/memo.txt")).toBe("C:/ws/memo.txt");
    expect(normalizeFsPath("C:/ws//out///a.md")).toBe("C:/ws/out/a.md");
  });

  it("preserves a UNC lead and a bare root", () => {
    expect(normalizeFsPath("\\\\server\\share\\a.txt")).toBe(
      "//server/share/a.txt",
    );
    expect(normalizeFsPath("/")).toBe("/");
  });

  it("is case-SENSITIVE — same policy as the rail's dedupe", () => {
    expect(normalizeFsPath("C:/ws/A.pdf")).not.toBe(normalizeFsPath("c:/ws/a.pdf"));
  });
});

describe("joinUndoByPath — journal rows to absolute paths", () => {
  const row = (over: Partial<UndoRowLike>): UndoRowLike => ({
    action_id: "tool_x",
    kind: "file_restore",
    undoable: true,
    path: "memo.txt",
    workspace: WS,
    ...over,
  });

  it("joins workspace + relative path onto the displayed absolute path", () => {
    const map = joinUndoByPath([row({})]);
    const got = map.get(normalizeFsPath(MEMO));
    expect(got?.action_id).toBe("tool_x");
    // the SAME key regardless of which separator flavour the display uses
    expect(map.get(normalizeFsPath("C:/Users/VR/.ironjarvis/home/uploads/memo.txt")))
      .toBe(got);
  });

  it("a relative path with forward slashes joins under a Windows workspace", () => {
    const map = joinUndoByPath([
      row({ action_id: "tool_sub", path: "out/report.md" }),
    ]);
    expect(map.get(normalizeFsPath(`${WS}\\out\\report.md`))?.action_id).toBe(
      "tool_sub",
    );
  });

  it("newest first wins: the FIRST row per path is kept (GET /undo order)", () => {
    const map = joinUndoByPath([
      row({ action_id: "tool_new" }),
      row({ action_id: "tool_old" }),
    ]);
    expect(map.size).toBe(1);
    expect(map.get(normalizeFsPath(MEMO))?.action_id).toBe("tool_new");
  });

  it("SKIPS rows it cannot match: null path, null workspace, blank id", () => {
    const map = joinUndoByPath([
      row({ action_id: "tool_nopath", path: null }), // setting_restore / files_delete
      row({ action_id: "tool_nows", workspace: null }), // pre-v1.166.3 row
      row({ action_id: "", path: "a.txt" }),
    ]);
    expect(map.size).toBe(0);
  });

  it("tolerates null/undefined input", () => {
    expect(joinUndoByPath(null).size).toBe(0);
    expect(joinUndoByPath(undefined).size).toBe(0);
  });
});

describe("confirmUndoPrompt — says what will actually happen", () => {
  it("a created file's undo REMOVES it — the wording must say so", () => {
    expect(confirmUndoPrompt("file_delete", "report.md")).toBe(
      "Undo this write? report.md was created by the chat and will be removed.",
    );
    expect(confirmUndoPrompt("files_delete", "a.png")).toBe(
      "Undo this write? a.png was created by the chat and will be removed.",
    );
  });

  it("an overwrite's undo restores prior content", () => {
    expect(confirmUndoPrompt("file_restore", "memo.txt")).toBe(
      "Undo this write? memo.txt will be restored to its content from before the write.",
    );
    // unknown/absent kind falls to the restore wording, never the removal one
    expect(confirmUndoPrompt(undefined, "memo.txt")).toContain("restored");
  });
});

describe("ArtifactsRail — Undo this write", () => {
  const undoState = {
    actionId: "tool_undo1",
    undoable: true,
    kind: "file_restore",
  };

  it("renders NO undo button when undoFor/onUndo are absent (pre-v1.168.0 rail)", () => {
    render(<ArtifactsRail items={[{ path: MEMO }]} onPreview={vi.fn()} />);
    expect(
      screen.queryByRole("button", { name: /Undo the write/ }),
    ).not.toBeInTheDocument();
  });

  it("renders undo ONLY for matched rows — an unmatched file gets none", () => {
    const other = `${WS}\\other.txt`;
    render(
      <ArtifactsRail
        items={[{ path: MEMO }, { path: other }]}
        onPreview={vi.fn()}
        undoFor={(p) => (p === MEMO ? undoState : null)}
        onUndo={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Undo the write to memo.txt" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Undo the write to other.txt" }),
    ).not.toBeInTheDocument();
  });

  it("click calls onUndo with the ACTION ID and the FULL path", async () => {
    const onUndo = vi.fn(async () => {});
    render(
      <ArtifactsRail
        items={[{ path: MEMO }]}
        onPreview={vi.fn()}
        undoFor={() => undoState}
        onUndo={onUndo}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Undo the write to memo.txt" }),
    );
    await waitFor(() => expect(onUndo).toHaveBeenCalledTimes(1));
    expect(onUndo).toHaveBeenCalledWith("tool_undo1", MEMO);
  });

  it("a matched-but-not-undoable row GREYS with the honest reason, not vanishes", () => {
    render(
      <ArtifactsRail
        items={[{ path: MEMO }]}
        onPreview={vi.fn()}
        undoFor={() => ({
          actionId: "tool_done",
          undoable: false,
          reason: "already undone",
        })}
        onUndo={vi.fn()}
      />,
    );
    const btn = screen.getByRole("button", {
      name: "Undo the write to memo.txt",
    });
    expect(btn).toBeDisabled();
    expect(btn.getAttribute("title")).toBe("Can't undo: already undone");
  });

  it("a rejected onUndo surfaces the error — a failed undo never looks done", async () => {
    const onUndo = vi.fn(async () => {
      throw new Error("target changed since the action — refusing to undo");
    });
    render(
      <ArtifactsRail
        items={[{ path: MEMO }]}
        onPreview={vi.fn()}
        undoFor={() => undoState}
        onUndo={onUndo}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Undo the write to memo.txt" }),
    );
    expect(
      await screen.findByText(
        "target changed since the action — refusing to undo",
      ),
    ).toBeInTheDocument();
  });

  it("undo click never leaks into onPreview", () => {
    const onPreview = vi.fn();
    render(
      <ArtifactsRail
        items={[{ path: MEMO }]}
        onPreview={onPreview}
        undoFor={() => undoState}
        onUndo={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Undo the write to memo.txt" }),
    );
    expect(onPreview).not.toHaveBeenCalled();
  });
});

describe("ArtifactsRail — Add to project knowledge", () => {
  it("renders NO promote button when onPromote is absent", () => {
    render(<ArtifactsRail items={[{ path: MEMO }]} onPreview={vi.fn()} />);
    expect(
      screen.queryByRole("button", { name: /project knowledge/ }),
    ).not.toBeInTheDocument();
  });

  it("no project bound → disabled with the honest reason as title", () => {
    render(
      <ArtifactsRail
        items={[{ path: MEMO }]}
        onPreview={vi.fn()}
        onPromote={vi.fn()}
        promoteDisabledReason="bind this chat to a project first"
      />,
    );
    const btn = screen.getByRole("button", {
      name: "Add memo.txt to project knowledge",
    });
    expect(btn).toBeDisabled();
    expect(btn.getAttribute("title")).toBe("bind this chat to a project first");
  });

  it("click calls onPromote with the FULL path; check only after RESOLVE", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const onPromote = vi.fn(() => gate);
    render(
      <ArtifactsRail
        items={[{ path: MEMO }]}
        onPreview={vi.fn()}
        onPromote={onPromote}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Add memo.txt to project knowledge" }),
    );
    expect(onPromote).toHaveBeenCalledWith(MEMO);
    // Still pending — a check now would claim a promote that hasn't happened.
    expect(screen.queryByTestId("promoted-check")).not.toBeInTheDocument();
    release();
    expect(await screen.findByTestId("promoted-check")).toBeInTheDocument();
  });

  it("a rejected onPromote surfaces the server's error and shows NO check", async () => {
    const onPromote = vi.fn(async () => {
      throw new Error("no such project");
    });
    render(
      <ArtifactsRail
        items={[{ path: MEMO }]}
        onPreview={vi.fn()}
        onPromote={onPromote}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Add memo.txt to project knowledge" }),
    );
    expect(await screen.findByText("no such project")).toBeInTheDocument();
    expect(screen.queryByTestId("promoted-check")).not.toBeInTheDocument();
  });

  it("clears the check-flash timer on unmount — no setState after the rail is gone", async () => {
    // Thread switch / last-doc dismissal unmounts the rail while the 1600 ms
    // check-flash timer is still armed; the cleanup must clear it (the same
    // pattern PromoteKnowledgeButton uses in chat/page.tsx).
    const setSpy = vi.spyOn(window, "setTimeout");
    const onPromote = vi.fn(async () => {});
    const { unmount } = render(
      <ArtifactsRail
        items={[{ path: MEMO }]}
        onPreview={vi.fn()}
        onPromote={onPromote}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Add memo.txt to project knowledge" }),
    );
    await screen.findByTestId("promoted-check"); // the timer is armed now
    // The flash timer is the component's only 1600 ms timeout (copy uses 1400).
    const armed = setSpy.mock.calls
      .map((call, i) => ({ delay: call[1], id: setSpy.mock.results[i].value }))
      .filter((c) => c.delay === 1600)
      .pop();
    expect(armed).toBeDefined();
    const clearSpy = vi.spyOn(window, "clearTimeout");
    unmount();
    expect(clearSpy).toHaveBeenCalledWith(armed!.id);
    clearSpy.mockRestore();
    setSpy.mockRestore();
  });
});

describe("revertedActionIds — an undo elsewhere reaches this page", () => {
  const frame = (
    id: string,
    type: string,
    payload?: Record<string, unknown> | null,
  ) => ({ id, type, payload });

  it("collects action_ids from frames newer than the boundary (newest first)", () => {
    const events = [
      frame("e3", "action.reverted", { action_id: "tool_c" }),
      frame("e2", "tool.executed", { tool: "write_file" }),
      frame("e1", "action.reverted", { action_id: "tool_a" }),
    ];
    expect(revertedActionIds(events, null)).toEqual(["tool_c", "tool_a"]);
  });

  it("STOPS at the boundary id — old frames are never re-processed", () => {
    const events = [
      frame("e3", "action.reverted", { action_id: "tool_new" }),
      frame("e2", "action.reverted", { action_id: "tool_old" }),
    ];
    expect(revertedActionIds(events, "e2")).toEqual(["tool_new"]);
    // boundary === newest frame → the rerun-from-a-dep-change case: no-op.
    expect(revertedActionIds(events, "e3")).toEqual([]);
  });

  it("skips frames without a usable string action_id — no id, no marking", () => {
    const events = [
      frame("e4", "action.reverted", { action_id: "" }),
      frame("e3", "action.reverted", { action_id: 42 }),
      frame("e2", "action.reverted", null),
      frame("e1", "action.reverted", {}),
    ];
    expect(revertedActionIds(events, null)).toEqual([]);
  });

  it("empty stream → empty result", () => {
    expect(revertedActionIds([], null)).toEqual([]);
  });
});

describe("source hygiene — chat/page.tsx stays a TEXT file to git", () => {
  it("carries no NUL/control bytes (a NUL once flipped the file to binary)", () => {
    // The regression: key={`${previewNonce}\x00${previewPath}`} embedded a
    // literal 0x00 — invisible in an editor, harmless at runtime, but git
    // classifies a NUL-carrying blob as BINARY: EOL normalization turns off,
    // `git diff` says "Binary files differ", and blame is destroyed.
    // vitest runs with cwd = dashboard/ (vitest.config resolves "@" there).
    const src = readFileSync(join(process.cwd(), "app", "chat", "page.tsx"), "utf-8");
    // Every C0 control byte except \t \n \r is a corruption, not code.
    // eslint-disable-next-line no-control-regex
    expect(src).not.toMatch(/[\x00-\x08\x0b\x0c\x0e-\x1f]/);
    // The intended separator is the printable ':' — the key template survives.
    expect(src).toContain("key={`${previewNonce}:${previewPath}`}");
  });
});

describe("TurnReceipt — undo under the file chip", () => {
  const DOC = `${WS}\\k1-redacted.pdf`;

  function expand() {
    fireEvent.click(screen.getByRole("button", { expanded: false }));
  }

  it("a matched document grows an undo affordance; unmatched does not", () => {
    const other = `${WS}\\untracked.pdf`;
    render(
      <TurnReceipt
        documents={[DOC, other]}
        undoFor={(p) =>
          p === DOC
            ? { actionId: "tool_r1", undoable: true, kind: "file_restore" }
            : null
        }
        onUndo={vi.fn()}
      />,
    );
    expand();
    expect(
      screen.getByRole("button", { name: "Undo the write to k1-redacted.pdf" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Undo the write to untracked.pdf" }),
    ).not.toBeInTheDocument();
  });

  it("click calls onUndo with the ACTION ID and the FULL path", async () => {
    const onUndo = vi.fn(async () => {});
    render(
      <TurnReceipt
        documents={[DOC]}
        undoFor={() => ({ actionId: "tool_r1", undoable: true })}
        onUndo={onUndo}
      />,
    );
    expand();
    fireEvent.click(
      screen.getByRole("button", { name: "Undo the write to k1-redacted.pdf" }),
    );
    await waitFor(() => expect(onUndo).toHaveBeenCalledTimes(1));
    expect(onUndo).toHaveBeenCalledWith("tool_r1", DOC);
  });

  it("not-undoable greys with the honest reason; chip itself still opens", () => {
    const onOpen = vi.fn();
    render(
      <TurnReceipt
        documents={[DOC]}
        onOpenDocument={onOpen}
        undoFor={() => ({
          actionId: "tool_r1",
          undoable: false,
          reason: "already undone",
        })}
        onUndo={vi.fn()}
      />,
    );
    expand();
    const undoBtn = screen.getByRole("button", {
      name: "Undo the write to k1-redacted.pdf",
    });
    expect(undoBtn).toBeDisabled();
    expect(undoBtn.getAttribute("title")).toBe("Can't undo: already undone");
    fireEvent.click(screen.getByRole("button", { name: "k1-redacted.pdf" }));
    expect(onOpen).toHaveBeenCalledWith(DOC);
  });

  it("a rejected onUndo shows the error inline under the file row", async () => {
    render(
      <TurnReceipt
        documents={[DOC]}
        undoFor={() => ({ actionId: "tool_r1", undoable: true })}
        onUndo={vi.fn(async () => {
          throw new Error("undo failed: RevertConflict");
        })}
      />,
    );
    expand();
    fireEvent.click(
      screen.getByRole("button", { name: "Undo the write to k1-redacted.pdf" }),
    );
    expect(
      await screen.findByText("undo failed: RevertConflict"),
    ).toBeInTheDocument();
  });

  it("without undoFor/onUndo the receipt renders exactly as before — no undo", () => {
    render(<TurnReceipt documents={[DOC]} />);
    expand();
    expect(
      screen.queryByRole("button", { name: /Undo the write/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "k1-redacted.pdf" }),
    ).toBeInTheDocument();
  });
});
