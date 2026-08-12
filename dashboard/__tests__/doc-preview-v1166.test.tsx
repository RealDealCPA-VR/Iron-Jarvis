/**
 * DocPreview v1.166.0 — image previews, truncation honesty, the "Changes"
 * diff, and the ArtifactsRail cap note.
 *
 * What carries weight here:
 *  - the image kind renders pixels from GET /documents/file WITHOUT the
 *    download flag (P1 serves pdf/images inline now), while the Download
 *    button carries `&download=1` on EVERY kind — the exact URLs are asserted;
 *  - truncation footers name the REAL extent (rows shown OF total_rows, chars
 *    shown OF total_chars) and fall back to the legacy wording when the daemon
 *    is older and sends no totals — asserting both strings kills a mutation
 *    that drops either branch;
 *  - the Changes toggle appears ONLY when a re-preview genuinely differs from
 *    the last-viewed payload, the diff lines carry the right kinds and TEXTS,
 *    and switching workbook sheets is NOT a "change" (snapshot key is
 *    path+sheet);
 *  - diffLines/snapshotLines are pure and value-asserted (LCS order included);
 *  - the rail's cap footer shows at the cap, not below it, never without it;
 *  - a failed image load is RETRIED by Refresh (imgError resets in load);
 *  - the diff over a truncated payload carries the clipped disclaimer;
 *  - the chat page's rail downloadHref appends &download=1 and a multi-doc
 *    turn never tears down an already-open preview (source pins — the page
 *    itself is too big to render here; the vocabulary suite set the pattern).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi, afterEach } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  DocPreview,
  diffLines,
  snapshotLines,
  type PreviewData,
} from "@/components/chat/DocPreview";
import { ArtifactsRail } from "@/components/chat/ArtifactsRail";
import { get } from "@/lib/api";

// Mock the api seam (mirroring the real ApiError shape) so tests stay offline.
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    get: vi.fn(),
    post: vi.fn(async () => ({ ok: true, app: "Word" })),
    ApiError,
    API_BASE: "http://api.test",
    ijToken: () => "tok",
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/** Route the mocked GET: places is always empty; preview payloads come from
 *  the callback (which sees the raw query string, e.g. to read `sheet=`). */
function mockGets(preview: (query: string) => PreviewData) {
  vi.mocked(get).mockImplementation(async (p: string) => {
    if (p.startsWith("/documents/places")) return { places: [] } as never;
    if (p.startsWith("/documents/preview")) return preview(p) as never;
    throw new Error(`unexpected GET ${p}`);
  });
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

describe("diffLines", () => {
  it("identical inputs are all `same`, in order", () => {
    expect(diffLines(["a", "b"], ["a", "b"])).toEqual([
      { kind: "same", text: "a" },
      { kind: "same", text: "b" },
    ]);
    expect(diffLines([], [])).toEqual([]);
  });

  it("a replaced line reads removed-then-added between unchanged context", () => {
    expect(diffLines(["a", "b", "c"], ["a", "x", "c"])).toEqual([
      { kind: "same", text: "a" },
      { kind: "removed", text: "b" },
      { kind: "added", text: "x" },
      { kind: "same", text: "c" },
    ]);
  });

  it("pure additions and pure removals keep every line, tagged correctly", () => {
    expect(diffLines([], ["a", "b"])).toEqual([
      { kind: "added", text: "a" },
      { kind: "added", text: "b" },
    ]);
    expect(diffLines(["a", "b"], [])).toEqual([
      { kind: "removed", text: "a" },
      { kind: "removed", text: "b" },
    ]);
  });

  it("an insertion in the middle keeps the surrounding lines as `same`", () => {
    expect(diffLines(["a", "c"], ["a", "b", "c"])).toEqual([
      { kind: "same", text: "a" },
      { kind: "added", text: "b" },
      { kind: "same", text: "c" },
    ]);
  });

  it("degrades to remove-all/add-all past the size guard — coarse, never wrong", () => {
    const prev = Array.from({ length: 1501 }, (_, i) => `p${i}`);
    const next = ["n0"];
    const got = diffLines(prev, next);
    expect(got).toHaveLength(1502);
    expect(got.slice(0, 1501).every((l) => l.kind === "removed")).toBe(true);
    expect(got[1501]).toEqual({ kind: "added", text: "n0" });
  });
});

describe("snapshotLines", () => {
  const base = { name: "x", path: "C:\\x", suffix: ".x" };

  it("text and markdown split on newlines (values, not just counts)", () => {
    expect(
      snapshotLines({ ...base, kind: "text", content: "a\nb" }),
    ).toEqual(["a", "b"]);
    expect(
      snapshotLines({ ...base, kind: "markdown", content: "# t\nbody" }),
    ).toEqual(["# t", "body"]);
  });

  it("sheets serialize one TAB-joined line per row", () => {
    expect(
      snapshotLines({
        ...base,
        kind: "sheet",
        rows: [
          ["a", "b"],
          ["1", "2"],
        ],
      }),
    ).toEqual(["a\tb", "1\t2"]);
  });

  it("kinds with no stable text form return null — no false 'unchanged'", () => {
    expect(snapshotLines({ ...base, kind: "pdf" })).toBeNull();
    expect(snapshotLines({ ...base, kind: "html", html: "<p>x</p>" })).toBeNull();
    expect(snapshotLines({ ...base, kind: "image" })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Image previews + the download flag
// ---------------------------------------------------------------------------

describe("DocPreview image kind", () => {
  const P = "C:\\out\\chart.png";
  const FILE_URL = `http://api.test/documents/file?path=${encodeURIComponent(P)}&token=tok`;

  it("renders <img> from /documents/file WITHOUT the download flag; Download carries it", async () => {
    mockGets(() => ({ kind: "image", name: "chart.png", path: P, suffix: ".png" }));
    render(<DocPreview path={P} onClose={vi.fn()} />);
    const img = await screen.findByAltText("chart.png");
    // The inline pixel fetch — forcing attachment here would break the render.
    expect(img.getAttribute("src")).toBe(FILE_URL);
    expect(img.getAttribute("src")).not.toContain("download=1");
    // The header Download anchor is a REAL download on every kind (v1.166.0).
    const a = screen.getByLabelText("Download chart.png");
    expect(a.getAttribute("href")).toBe(`${FILE_URL}&download=1`);
  });

  it("a failed image load shows honest error text instead of a broken icon", async () => {
    mockGets(() => ({ kind: "image", name: "chart.png", path: P, suffix: ".png" }));
    render(<DocPreview path={P} onClose={vi.fn()} />);
    fireEvent.error(await screen.findByAltText("chart.png"));
    expect(
      screen.getByText(/Couldn.t load this image/),
    ).toBeInTheDocument();
    expect(screen.queryByAltText("chart.png")).not.toBeInTheDocument();
  });

  it("Refresh retries a failed image — the error state does not stick", async () => {
    mockGets(() => ({ kind: "image", name: "chart.png", path: P, suffix: ".png" }));
    render(<DocPreview path={P} onClose={vi.fn()} />);
    fireEvent.error(await screen.findByAltText("chart.png"));
    expect(screen.getByText(/Couldn.t load this image/)).toBeInTheDocument();
    // The agent finishes writing the PNG; the user hits Refresh. load() must
    // clear imgError so the <img> re-mounts for a fresh attempt — without the
    // reset the panel refetched the JSON and stayed stuck on the error text.
    fireEvent.click(screen.getByRole("button", { name: "Refresh preview" }));
    const img = await screen.findByAltText("chart.png");
    expect(img.getAttribute("src")).toBe(FILE_URL);
    expect(
      screen.queryByText(/Couldn.t load this image/),
    ).not.toBeInTheDocument();
  });

  it("the Download anchor carries &download=1 for non-image kinds too", async () => {
    const TXT = "C:\\out\\notes.txt";
    mockGets(() => ({
      kind: "text",
      name: "notes.txt",
      path: TXT,
      suffix: ".txt",
      content: "hello",
    }));
    render(<DocPreview path={TXT} onClose={vi.fn()} />);
    await screen.findByText("hello");
    expect(
      screen.getByLabelText("Download notes.txt").getAttribute("href"),
    ).toBe(
      `http://api.test/documents/file?path=${encodeURIComponent(TXT)}&token=tok&download=1`,
    );
  });
});

// ---------------------------------------------------------------------------
// Truncation honesty
// ---------------------------------------------------------------------------

describe("DocPreview truncation footers", () => {
  it("sheet footer names rows shown OF total_rows when the daemon reports it", async () => {
    const P = "C:\\out\\big.xlsx";
    mockGets(() => ({
      kind: "sheet",
      name: "big.xlsx",
      path: P,
      suffix: ".xlsx",
      sheets: ["S"],
      sheet: "S",
      rows: [
        ["h1", "h2"],
        ["a", "b"],
        ["c", "d"],
      ],
      truncated: true,
      total_rows: 4112,
    }));
    render(<DocPreview path={P} onClose={vi.fn()} />);
    expect(
      await screen.findByText(
        `Showing the first 3 of ${(4112).toLocaleString()} rows — open in Excel for the full sheet.`,
      ),
    ).toBeInTheDocument();
  });

  it("sheet footer keeps the legacy wording when total_rows is absent (old daemon)", async () => {
    const P = "C:\\out\\legacy.xlsx";
    mockGets(() => ({
      kind: "sheet",
      name: "legacy.xlsx",
      path: P,
      suffix: ".xlsx",
      sheets: ["S"],
      sheet: "S",
      rows: [["a"]],
      truncated: true,
    }));
    render(<DocPreview path={P} onClose={vi.fn()} />);
    expect(
      await screen.findByText(
        "Showing the first 80 rows — open in Excel for the full sheet.",
      ),
    ).toBeInTheDocument();
  });

  it("no sheet footer at all when nothing was truncated", async () => {
    const P = "C:\\out\\small.xlsx";
    mockGets(() => ({
      kind: "sheet",
      name: "small.xlsx",
      path: P,
      suffix: ".xlsx",
      sheets: ["S"],
      sheet: "S",
      rows: [["a"]],
      truncated: false,
      total_rows: 1,
    }));
    render(<DocPreview path={P} onClose={vi.fn()} />);
    await screen.findByText("a");
    expect(screen.queryByText(/Showing the first/)).not.toBeInTheDocument();
  });

  it("text footer names chars shown OF total_chars when clipped", async () => {
    const P = "C:\\out\\huge.txt";
    mockGets(() => ({
      kind: "text",
      name: "huge.txt",
      path: P,
      suffix: ".txt",
      content: "abc",
      truncated: true,
      total_chars: 20000,
    }));
    render(<DocPreview path={P} onClose={vi.fn()} />);
    expect(
      await screen.findByText(
        `Preview clipped — showing 3 of ${(20000).toLocaleString()} characters; open the file for everything.`,
      ),
    ).toBeInTheDocument();
  });

  it("text footer keeps the legacy wording without total_chars", async () => {
    const P = "C:\\out\\old.txt";
    mockGets(() => ({
      kind: "text",
      name: "old.txt",
      path: P,
      suffix: ".txt",
      content: "abc",
      truncated: true,
    }));
    render(<DocPreview path={P} onClose={vi.fn()} />);
    expect(
      await screen.findByText("Preview clipped — open the file for everything."),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// The "Changes" toggle (A7)
// ---------------------------------------------------------------------------

describe("DocPreview Changes toggle", () => {
  function textPayload(path: string, content: string): PreviewData {
    const name = path.split(/[\\/]/).pop() ?? path;
    return { kind: "text", name, path, suffix: ".md", content };
  }

  it("no toggle on first view, toggle after a differing re-preview, exact diff lines", async () => {
    const P = "C:\\out\\changes-a.md";
    let content = "alpha\nbeta";
    mockGets(() => textPayload(P, content));
    render(<DocPreview path={P} onClose={vi.fn()} />);
    await screen.findByText(/alpha/);
    // First viewing: nothing to compare against yet.
    expect(
      screen.queryByRole("button", { name: "Changes" }),
    ).not.toBeInTheDocument();

    // The file changes on disk; the user hits Refresh.
    content = "alpha\ngamma";
    fireEvent.click(screen.getByRole("button", { name: "Refresh preview" }));
    const toggle = await screen.findByRole("button", { name: "Changes" });
    expect(
      screen.getByText("this file changed since you last previewed it"),
    ).toBeInTheDocument();

    fireEvent.click(toggle);
    // The diff view: exact kinds AND texts (prefix included — what users read).
    expect(screen.getByTestId("diff-same").textContent).toBe("  alpha");
    expect(screen.getByTestId("diff-removed").textContent).toBe("− beta");
    expect(screen.getByTestId("diff-added").textContent).toBe("+ gamma");
    // An UNtruncated payload carries no clipped disclaimer in the header.
    expect(
      screen.queryByText(/Compared over the clipped preview/),
    ).not.toBeInTheDocument();
    // Toggled on, the button offers the way back.
    const back = screen.getByRole("button", { name: "Current" });
    fireEvent.click(back);
    expect(screen.getByText(/gamma/)).toBeInTheDocument();
    expect(screen.queryByTestId("diff-added")).not.toBeInTheDocument();
  });

  it("an UNCHANGED re-preview offers no toggle", async () => {
    const P = "C:\\out\\changes-b.md";
    mockGets(() => textPayload(P, "stable"));
    render(<DocPreview path={P} onClose={vi.fn()} />);
    await screen.findByText("stable");
    // Make the refresh OBSERVABLE before asserting: the initial load already
    // called get, so a bare "get was called" waitFor is vacuously true and the
    // final assertions could run before the refetch resolved — letting a
    // mutant that flags EVERY re-preview as changed slip through. Count the
    // preview calls, wait for the count to grow AND for the loader to clear.
    const previewCalls = () =>
      vi
        .mocked(get)
        .mock.calls.filter(([p]) => String(p).startsWith("/documents/preview"))
        .length;
    const before = previewCalls();
    fireEvent.click(screen.getByRole("button", { name: "Refresh preview" }));
    await waitFor(() => expect(previewCalls()).toBeGreaterThan(before));
    await waitFor(() =>
      expect(screen.queryByText("Loading preview…")).not.toBeInTheDocument(),
    );
    await screen.findByText("stable");
    expect(
      screen.queryByRole("button", { name: "Changes" }),
    ).not.toBeInTheDocument();
  });

  it("the snapshot survives closing the panel — module-level 'last previewed'", async () => {
    const P = "C:\\out\\changes-c.md";
    mockGets(() => textPayload(P, "v1"));
    const first = render(<DocPreview path={P} onClose={vi.fn()} />);
    await screen.findByText("v1");
    first.unmount();
    // Reopened later, after the file changed: the previous viewing still counts.
    mockGets(() => textPayload(P, "v2"));
    render(<DocPreview path={P} onClose={vi.fn()} />);
    expect(
      await screen.findByRole("button", { name: "Changes" }),
    ).toBeInTheDocument();
  });

  it("switching workbook sheets is NOT a change (snapshot keyed per sheet)", async () => {
    const P = "C:\\out\\wb.xlsx";
    const rowsBySheet: Record<string, string[][]> = {
      A: [["a1"]],
      B: [["b1"]],
    };
    mockGets((q) => {
      const m = /sheet=([^&]+)/.exec(q);
      const sheet = m ? decodeURIComponent(m[1]) : "A";
      return {
        kind: "sheet",
        name: "wb.xlsx",
        path: P,
        suffix: ".xlsx",
        sheets: ["A", "B"],
        sheet,
        rows: rowsBySheet[sheet],
      };
    });
    render(<DocPreview path={P} onClose={vi.fn()} />);
    await screen.findByText("a1");
    // Tab over to B: different rows, but a different SHEET — not a change.
    fireEvent.click(screen.getByRole("button", { name: "B" }));
    await screen.findByText("b1");
    expect(
      screen.queryByRole("button", { name: "Changes" }),
    ).not.toBeInTheDocument();
    // But sheet B genuinely changing IS one.
    rowsBySheet.B = [["b2"]];
    fireEvent.click(screen.getByRole("button", { name: "Refresh preview" }));
    await screen.findByText("b2");
    expect(
      await screen.findByRole("button", { name: "Changes" }),
    ).toBeInTheDocument();
  });

  it("the diff over a CLIPPED text preview says so — edits past the window are invisible", async () => {
    const P = "C:\\out\\changes-clip.md";
    let content = "alpha\nbeta";
    mockGets(() => ({
      ...textPayload(P, content),
      truncated: true,
      total_chars: 40000,
    }));
    render(<DocPreview path={P} onClose={vi.fn()} />);
    await screen.findByText(/alpha/);
    content = "alpha\ngamma"; // 11 chars — the disclaimer names the WINDOW size
    fireEvent.click(screen.getByRole("button", { name: "Refresh preview" }));
    fireEvent.click(await screen.findByRole("button", { name: "Changes" }));
    expect(
      screen.getByText(
        /Compared over the clipped preview only — changes past the first 11 characters are not shown\./,
      ),
    ).toBeInTheDocument();
  });

  it("the clipped-diff disclaimer counts ROWS for sheets", async () => {
    const P = "C:\\out\\changes-clip.xlsx";
    let rows = [["a1", "a2"]];
    mockGets(() => ({
      kind: "sheet",
      name: "changes-clip.xlsx",
      path: P,
      suffix: ".xlsx",
      sheets: ["S"],
      sheet: "S",
      rows,
      truncated: true,
      total_rows: 4000,
    }));
    render(<DocPreview path={P} onClose={vi.fn()} />);
    await screen.findByText("a1");
    rows = [["b1", "b2"]];
    fireEvent.click(screen.getByRole("button", { name: "Refresh preview" }));
    fireEvent.click(await screen.findByRole("button", { name: "Changes" }));
    expect(
      screen.getByText(
        /Compared over the clipped preview only — changes past the first 1 rows are not shown\./,
      ),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// chat/page.tsx wiring pins (source scans — the page is too large to render
// in vitest; the vocabulary suite established this technique for page-level
// guarantees the component tests cannot reach)
// ---------------------------------------------------------------------------

describe("chat page wiring", () => {
  const pageSrc = () =>
    readFileSync(join(__dirname, "..", "app", "chat", "page.tsx"), "utf-8");

  it("the rail's downloadHref appends &download=1 — the anchor's `download` attr is ignored cross-origin", () => {
    const src = pageSrc();
    const start = src.indexOf("downloadHref={(p)");
    expect(start, "page.tsx should wire ArtifactsRail downloadHref").toBeGreaterThan(-1);
    const block = src.slice(start, src.indexOf("}}", start) + 2);
    expect(block).toContain("/documents/file?path=");
    // Value-asserted: the flag sits OUTSIDE the token ternary, so it is sent
    // with and without a token — once P1 serves pdf/images inline, this flag
    // is the only thing that makes the rail's anchor an actual download.
    expect(block).toContain('${tok ? `&token=${encodeURIComponent(tok)}` : ""}&download=1');
  });

  it("a multi-doc turn keeps an already-open preview (no unconditional null)", () => {
    const src = pageSrc();
    const start = src.indexOf("function showDocPreview");
    expect(start).toBeGreaterThan(-1);
    const block = src.slice(start, src.indexOf("\n  }", start));
    // Functional update: several docs KEEP whatever preview is showing
    // (never tear one down), one doc auto-opens as it always has.
    expect(block).toContain(
      "setPreviewPath((cur) => (docs.length > 1 ? cur : last))",
    );
    expect(block).not.toContain("setPreviewPath(docs.length > 1 ? null : last)");
  });
});

// ---------------------------------------------------------------------------
// ArtifactsRail download anchor (value-asserted with the page-shaped URL)
// ---------------------------------------------------------------------------

describe("ArtifactsRail download anchor", () => {
  it("carries the callback's URL verbatim, download flag included", () => {
    const href = (p: string) =>
      `http://api.test/documents/file?path=${encodeURIComponent(p)}&token=tok&download=1`;
    render(
      <ArtifactsRail
        items={[{ path: "C:\\w\\report.pdf" }]}
        onPreview={vi.fn()}
        downloadHref={href}
      />,
    );
    const a = screen.getByLabelText("Download report.pdf");
    expect(a.getAttribute("href")).toBe(href("C:\\w\\report.pdf"));
    expect(a.getAttribute("href")).toContain("&download=1");
    expect(a.getAttribute("download")).toBe("report.pdf");
  });
});

// ---------------------------------------------------------------------------
// ArtifactsRail cap note (threadDocs keeps the newest 30 since v1.166.0)
// ---------------------------------------------------------------------------

describe("ArtifactsRail cap note", () => {
  const NOTE = "Showing the latest 30 files — older ones rolled off this list.";
  const items = (n: number) =>
    Array.from({ length: n }, (_, i) => ({ path: `C:\\w\\f${i}.pdf` }));

  it("shows the quiet line when the deduped rows REACH the cap", () => {
    render(<ArtifactsRail items={items(30)} onPreview={vi.fn()} cap={30} />);
    expect(screen.getByText(NOTE)).toBeInTheDocument();
  });

  it("stays silent below the cap", () => {
    render(<ArtifactsRail items={items(29)} onPreview={vi.fn()} cap={30} />);
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument();
  });

  it("duplicates do not count toward the cap — deduped rows do", () => {
    // 30 raw items but only 29 distinct files → below the cap, no note.
    const dup = [...items(29), { path: "C:\\w\\f0.pdf" }];
    render(<ArtifactsRail items={dup} onPreview={vi.fn()} cap={30} />);
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument();
  });

  it("no cap prop (or cap 0) → never a note, matching the pre-cap rail", () => {
    const { unmount } = render(
      <ArtifactsRail items={items(31)} onPreview={vi.fn()} />,
    );
    expect(screen.queryByText(/rolled off/)).not.toBeInTheDocument();
    unmount();
    render(<ArtifactsRail items={items(31)} onPreview={vi.fn()} cap={0} />);
    expect(screen.queryByText(/rolled off/)).not.toBeInTheDocument();
  });
});
