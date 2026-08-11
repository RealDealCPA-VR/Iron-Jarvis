/**
 * The Artifacts rail — the conversation's files get a home.
 *
 * What carries weight here:
 *  - collectArtifacts dedupes with LAST occurrence winning (a file rewritten in
 *    turn 7 shows at the turn-7 position, tagged turn 7 — asserting the
 *    turnIndex VALUE is what kills a keep-first mutation);
 *  - onPreview and the copy button both receive the FULL absolute path, never
 *    the basename the row displays;
 *  - empty items render NOTHING (no empty-state chrome in the chat column);
 *  - an absent clipboard is a guarded no-op — no throw, and no fake check.
 */

import { describe, expect, it, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  ArtifactsRail,
  basename,
  collectArtifacts,
  fileKind,
  parentDir,
} from "@/components/chat/ArtifactsRail";
import { post, ApiError } from "@/lib/api";

// The rail's Open action posts to /documents/open through the shared api
// module; mock it at the seam (mirroring the real ApiError shape) so tests
// stay offline.
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return { post: vi.fn(async () => ({ ok: true, app: "Word" })), ApiError };
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const WIN = "C:\\Users\\VR\\.ironjarvis\\home\\uploads\\k1-redacted.pdf";
const POSIX = "/home/vr/out/notes.md";

function stubClipboard() {
  const writeText = vi.fn(async (_t: string) => {});
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  return writeText;
}

describe("collectArtifacts", () => {
  it("is stable for empty input", () => {
    expect(collectArtifacts([])).toEqual([]);
    expect(collectArtifacts([undefined, undefined])).toEqual([]);
    expect(collectArtifacts([[], []])).toEqual([]);
  });

  it("dedupes with LAST occurrence winning, ordered newest-first", () => {
    // a in turn 0 AND turn 3; b in turn 2; c in turn 3. Turn 1 wrote nothing.
    const a = "C:\\out\\a.pdf";
    const b = "C:\\out\\b.xlsx";
    const c = "C:\\out\\c.md";
    const got = collectArtifacts([[a], undefined, [b], [a, c]]);
    expect(got).toEqual([
      { path: c, turnIndex: 3 },
      { path: a, turnIndex: 3 }, // once, at the turn-3 position, tagged turn 3
      { path: b, turnIndex: 2 },
    ]);
  });

  it("skips undefined turns WITHOUT compacting turn indices", () => {
    // The path in slot 4 must be tagged 4, not 1 — turnIndex is the message
    // position the coordinator will scroll to, so a compacted index points at
    // the wrong turn.
    const got = collectArtifacts([["x.txt"], undefined, undefined, undefined, ["y.txt"]]);
    expect(got).toEqual([
      { path: "y.txt", turnIndex: 4 },
      { path: "x.txt", turnIndex: 0 },
    ]);
  });

  it("keeps Windows and posix paths verbatim, and drops blank entries", () => {
    const got = collectArtifacts([[WIN, "  "], [POSIX]]);
    expect(got.map((i) => i.path)).toEqual([POSIX, WIN]);
  });

  it("a trailing separator is the SAME file — one row, separator stripped", () => {
    // basename/parentDir already ignore trailing separators, so without this
    // normalization "a.pdf" and "a.pdf\" render as two identical-looking rows.
    const got = collectArtifacts([["C:\\out\\a.pdf"], ["C:\\out\\a.pdf\\"]]);
    expect(got).toEqual([{ path: "C:\\out\\a.pdf", turnIndex: 1 }]);
    // posix flavour too, and a root path made only of separators survives.
    expect(collectArtifacts([["/home/vr/x.md/"]])).toEqual([
      { path: "/home/vr/x.md", turnIndex: 0 },
    ]);
    expect(collectArtifacts([["/"]])).toEqual([{ path: "/", turnIndex: 0 }]);
  });

  it("POLICY: dedupe is case-SENSITIVE exact-string (matching the v1.153.2 block)", () => {
    // NTFS is case-insensitive, so C:\x\A.pdf and c:\x\a.pdf are one file on
    // the user's install — but the daemon's writing tools report one
    // consistent casing, threadDocs/preview/dismiss all key on the exact
    // string, and casefolding would wrongly merge distinct posix paths.
    // Decision: keep exact-string identity; a case-variant stays two rows.
    const got = collectArtifacts([["C:\\x\\A.pdf"], ["c:\\x\\a.pdf"]]);
    expect(got.map((i) => i.path)).toEqual(["c:\\x\\a.pdf", "C:\\x\\A.pdf"]);
  });

  it("same basename in different dirs = different files, both kept", () => {
    const got = collectArtifacts([["C:\\a\\report.pdf", "C:\\b\\report.pdf"]]);
    expect(got.map((i) => i.path)).toEqual([
      "C:\\b\\report.pdf",
      "C:\\a\\report.pdf",
    ]);
  });

  it("handles a big conversation (50 turns x 200 docs) in linear time", () => {
    // Correctness at scale: every turn re-mentions the same 200 paths, so the
    // result is exactly 200 items, all tagged with the LAST turn. The Map
    // delete+set pass is O(total entries); this completing instantly (vitest
    // default timeout) is the cheap regression guard against a quadratic
    // rewrite (e.g. Array.findIndex per entry).
    const docs = Array.from({ length: 200 }, (_, i) => `C:\\w\\f${i}.pdf`);
    const perTurn = Array.from({ length: 50 }, () => [...docs]);
    const got = collectArtifacts(perTurn);
    expect(got).toHaveLength(200);
    expect(got[0]).toEqual({ path: "C:\\w\\f199.pdf", turnIndex: 49 });
    expect(got[199]).toEqual({ path: "C:\\w\\f0.pdf", turnIndex: 49 });
  });
});

describe("path helpers", () => {
  it("basename handles both separators", () => {
    expect(basename(WIN)).toBe("k1-redacted.pdf");
    expect(basename(POSIX)).toBe("notes.md");
    expect(basename("plain.txt")).toBe("plain.txt");
  });

  it("parentDir is the location hint, empty when there is none", () => {
    expect(parentDir(WIN)).toBe("C:\\Users\\VR\\.ironjarvis\\home\\uploads");
    expect(parentDir(POSIX)).toBe("/home/vr/out");
    expect(parentDir("plain.txt")).toBe("");
  });

  it("fileKind buckets by extension, case-insensitively", () => {
    for (const p of ["a.pdf", "a.doc", "a.docx", "a.pptx", "A.PDF"]) {
      expect(fileKind(p)).toBe("doc");
    }
    for (const p of ["a.xls", "a.xlsx", "a.csv"]) expect(fileKind(p)).toBe("sheet");
    for (const p of ["a.png", "a.jpg", "a.webp", "a.tiff"]) {
      expect(fileKind(p)).toBe("image");
    }
    for (const p of ["a.md", "a.txt"]) expect(fileKind(p)).toBe("text");
    for (const p of ["a.py", "a.ts", "a.json", "a.xml"]) {
      expect(fileKind(p)).toBe("code");
    }
    // The pixio tools write generated media into the workspace.
    for (const p of ["a.mp4", "a.mov", "a.webm"]) expect(fileKind(p)).toBe("video");
    for (const p of ["a.mp3", "a.wav", "A.FLAC"]) expect(fileKind(p)).toBe("audio");
    expect(fileKind("a.xyz")).toBe("file"); // unknown → fallback
    expect(fileKind("noext")).toBe("file");
    expect(fileKind(".env")).toBe("file"); // a dotfile is not "env-type"
  });
});

describe("ArtifactsRail", () => {
  it("renders NOTHING when items is empty — no empty-state chrome", () => {
    const { container } = render(<ArtifactsRail items={[]} onPreview={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when every item is blank", () => {
    const { container } = render(
      <ArtifactsRail items={[{ path: "  " }]} onPreview={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows basename as the label, parent folder as the hint, full path as title", () => {
    render(<ArtifactsRail items={[{ path: WIN }]} onPreview={vi.fn()} />);
    expect(screen.getByText("k1-redacted.pdf")).toBeInTheDocument();
    expect(
      screen.getByText("C:\\Users\\VR\\.ironjarvis\\home\\uploads"),
    ).toBeInTheDocument();
    // The row's title is the FULL path — the hover answer to "where exactly?".
    expect(screen.getByTitle(WIN)).toBeInTheDocument();
    // A file-type icon is present in the row.
    expect(screen.getByTitle(WIN).querySelector("svg")).not.toBeNull();
  });

  it("row click calls onPreview with the FULL path, not the basename", () => {
    const onPreview = vi.fn();
    render(<ArtifactsRail items={[{ path: WIN }]} onPreview={onPreview} />);
    fireEvent.click(screen.getByTitle(WIN));
    expect(onPreview).toHaveBeenCalledTimes(1);
    expect(onPreview).toHaveBeenCalledWith(WIN);
  });

  it("copy writes the FULL absolute path and shows a check", async () => {
    const writeText = stubClipboard();
    render(<ArtifactsRail items={[{ path: WIN }]} onPreview={vi.fn()} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Copy path to k1-redacted.pdf" }),
    );
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(WIN));
    // The check only appears because the write RESOLVED.
    expect(await screen.findByTestId("copied-check")).toBeInTheDocument();
  });

  it("survives an absent clipboard: no throw, and no fake check", async () => {
    vi.stubGlobal("navigator", {}); // no clipboard at all
    render(<ArtifactsRail items={[{ path: WIN }]} onPreview={vi.fn()} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Copy path to k1-redacted.pdf" }),
    );
    // A check here would claim a copy that never happened.
    await waitFor(() =>
      expect(screen.queryByTestId("copied-check")).not.toBeInTheDocument(),
    );
  });

  it("shows no check when the clipboard write REJECTS", async () => {
    vi.stubGlobal("navigator", {
      clipboard: { writeText: vi.fn(async () => Promise.reject(new Error("denied"))) },
    });
    render(<ArtifactsRail items={[{ path: WIN }]} onPreview={vi.fn()} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Copy path to k1-redacted.pdf" }),
    );
    await waitFor(() =>
      expect(screen.queryByTestId("copied-check")).not.toBeInTheDocument(),
    );
  });

  it("newest first end-to-end: a path from turns 2 AND 7 renders once, on top", () => {
    // The full pipeline the coordinator will wire: per-turn documents →
    // collectArtifacts → rail. Turn 7 rewrote report.docx; it must be row 1.
    const report = "C:\\w\\report.docx";
    const other = "C:\\w\\summary.pdf";
    const perTurn: (string[] | undefined)[] = [];
    perTurn[2] = [report];
    perTurn[5] = [other];
    perTurn[7] = [report];
    render(
      <ArtifactsRail items={collectArtifacts(perTurn)} onPreview={vi.fn()} />,
    );
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("report.docx");
    expect(rows[1].textContent).toContain("summary.pdf");
  });

  it("dedupes items passed directly (first occurrence — the newest — wins)", () => {
    render(
      <ArtifactsRail
        items={[
          { path: WIN, turnIndex: 7 },
          { path: POSIX, turnIndex: 5 },
          { path: WIN, turnIndex: 2 }, // stale duplicate from an older turn
        ]}
        onPreview={vi.fn()}
      />,
    );
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("k1-redacted.pdf");
    expect(screen.getByText("2 files")).toBeInTheDocument();
  });

  it("header counts files, singular and plural", () => {
    const { unmount } = render(
      <ArtifactsRail items={[{ path: WIN }]} onPreview={vi.fn()} />,
    );
    expect(screen.getByText("1 file")).toBeInTheDocument();
    unmount();
    render(
      <ArtifactsRail
        items={[{ path: WIN }, { path: POSIX }]}
        onPreview={vi.fn()}
      />,
    );
    expect(screen.getByText("2 files")).toBeInTheDocument();
  });

  it("offers a close X only when onClose is provided", () => {
    const onClose = vi.fn();
    const { unmount } = render(
      <ArtifactsRail items={[{ path: WIN }]} onPreview={vi.fn()} onClose={onClose} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Close files panel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    unmount();
    render(<ArtifactsRail items={[{ path: WIN }]} onPreview={vi.fn()} />);
    expect(
      screen.queryByRole("button", { name: "Close files panel" }),
    ).not.toBeInTheDocument();
  });

  it("truncates long paths instead of overflowing the narrow rail", () => {
    render(<ArtifactsRail items={[{ path: WIN }]} onPreview={vi.fn()} />);
    const name = screen.getByText("k1-redacted.pdf");
    const dir = screen.getByText("C:\\Users\\VR\\.ironjarvis\\home\\uploads");
    expect(name.className).toContain("truncate");
    expect(dir.className).toContain("truncate");
    // min-w-0 down the flex chain is what lets `truncate` actually engage.
    expect(screen.getByTitle(WIN).className).toContain("min-w-0");
    expect(screen.getByRole("listitem").className).toContain("min-w-0");
  });

  it("Open posts the full path to /documents/open", async () => {
    render(<ArtifactsRail items={[{ path: WIN }]} onPreview={vi.fn()} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Open k1-redacted.pdf" }),
    );
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/documents/open", { path: WIN }),
    );
  });

  it("a failed Open reports the daemon's error instead of pretending", async () => {
    vi.mocked(post).mockRejectedValueOnce(
      new ApiError("That file is gone: k1-redacted.pdf", 404),
    );
    render(<ArtifactsRail items={[{ path: WIN }]} onPreview={vi.fn()} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Open k1-redacted.pdf" }),
    );
    expect(
      await screen.findByText("That file is gone: k1-redacted.pdf"),
    ).toBeInTheDocument();
  });

  it("dedupes a trailing-separator variant of the same path", () => {
    render(
      <ArtifactsRail
        items={[{ path: WIN, turnIndex: 7 }, { path: `${WIN}\\`, turnIndex: 2 }]}
        onPreview={vi.fn()}
      />,
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByText("1 file")).toBeInTheDocument();
  });

  it("copy and open clicks do NOT trigger onPreview (separate targets)", () => {
    stubClipboard();
    const onPreview = vi.fn();
    render(<ArtifactsRail items={[{ path: WIN }]} onPreview={onPreview} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Copy path to k1-redacted.pdf" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Open k1-redacted.pdf" }));
    expect(onPreview).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// The OPTIONAL affordances that let the rail REPLACE the v1.153.2 inline
// "Files in this chat" block without regressions: downloadHref (the block's
// per-row download anchor) and onDismiss (the thread-doc ×). The contract:
// both absent → rendering identical to the base rail (the buttons simply do
// not exist); both present → full path in, basename only for display/download
// naming, and neither click leaks into onPreview.
// ---------------------------------------------------------------------------
describe("ArtifactsRail optional download + dismiss", () => {
  const HREF = "http://127.0.0.1:8787/documents/file?path=x&token=t";

  it("renders NEITHER control when the props are absent — identical to the base rail", () => {
    render(<ArtifactsRail items={[{ path: WIN }]} onPreview={vi.fn()} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Remove .* from this chat/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("Download k1-redacted.pdf"),
    ).not.toBeInTheDocument();
  });

  it("download anchor: href from the callback (FULL path in), download attr = basename", () => {
    const downloadHref = vi.fn(() => HREF);
    render(
      <ArtifactsRail
        items={[{ path: WIN }]}
        onPreview={vi.fn()}
        downloadHref={downloadHref}
      />,
    );
    expect(downloadHref).toHaveBeenCalledWith(WIN); // never the basename
    const a = screen.getByRole("link", { name: "Download k1-redacted.pdf" });
    expect(a.getAttribute("href")).toBe(HREF);
    // The browser must save under the file's own name, not the URL's tail.
    expect(a.getAttribute("download")).toBe("k1-redacted.pdf");
  });

  it("dismiss calls onDismiss with the FULL path — a basename would remove nothing", () => {
    const onDismiss = vi.fn();
    render(
      <ArtifactsRail
        items={[{ path: WIN }, { path: POSIX }]}
        onPreview={vi.fn()}
        onDismiss={onDismiss}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Remove notes.md from this chat" }),
    );
    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(onDismiss).toHaveBeenCalledWith(POSIX);
  });

  it("neither download nor dismiss leaks a click into onPreview", () => {
    const onPreview = vi.fn();
    render(
      <ArtifactsRail
        items={[{ path: WIN }]}
        onPreview={onPreview}
        downloadHref={() => HREF}
        onDismiss={vi.fn()}
      />,
    );
    fireEvent.click(
      screen.getByRole("link", { name: "Download k1-redacted.pdf" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Remove k1-redacted.pdf from this chat" }),
    );
    expect(onPreview).not.toHaveBeenCalled();
  });

  it("dismiss and download STOP propagation — an ancestor click target never fires", () => {
    // Today the preview button is a sibling, so nothing above the row listens
    // — but the coordinator may wrap rows in a click target tomorrow, and
    // "save/remove this file" must never also mean "open the preview". This
    // ancestor listener is what makes a deleted stopPropagation observable
    // (mutation M8 survived the sibling-only tests).
    const ancestor = vi.fn();
    render(
      <div onClick={ancestor}>
        <ArtifactsRail
          items={[{ path: WIN }]}
          onPreview={vi.fn()}
          downloadHref={() => HREF}
          onDismiss={vi.fn()}
        />
      </div>,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Remove k1-redacted.pdf from this chat" }),
    );
    fireEvent.click(
      screen.getByRole("link", { name: "Download k1-redacted.pdf" }),
    );
    expect(ancestor).not.toHaveBeenCalled();
  });

  it("both controls are keyboard-reachable (real button + real hrefed anchor)", () => {
    render(
      <ArtifactsRail
        items={[{ path: WIN }]}
        onPreview={vi.fn()}
        downloadHref={() => HREF}
        onDismiss={vi.fn()}
      />,
    );
    const dismiss = screen.getByRole("button", {
      name: "Remove k1-redacted.pdf from this chat",
    });
    expect(dismiss.tagName).toBe("BUTTON"); // native focus + Enter/Space
    dismiss.focus();
    expect(dismiss).toHaveFocus();
    const a = screen.getByRole("link", { name: "Download k1-redacted.pdf" });
    // An anchor without href is skipped by tab order — the href is what makes
    // it keyboard-reachable at all.
    expect(a.getAttribute("href")).toBeTruthy();
    a.focus();
    expect(a).toHaveFocus();
  });
});
