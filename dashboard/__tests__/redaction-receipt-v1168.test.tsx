/**
 * Redaction receipt v1.168.0 — "Compare to original" in DocPreview.
 *
 * The engine writes its output as `<stem>.redacted<suffix>` beside the source
 * (pinned server-side in tests/test_redaction_receipt_v1168.py), so the
 * preview panel can offer the original: both files go through
 * GET /documents/read (same extractor both sides — the preview payload
 * carries no text for pdf/docx), the existing diffLines machinery renders
 * what changed, and the removed-PII counts come from the placeholder tokens
 * the engine wrote into the redacted copy — counted by re-reading the
 * written file, never repeated from a tool's claim.
 *
 * What carries weight here:
 *  - redactionSourcePath infers the source by VALUE (multi-dot names, no-ext
 *    names, both slashes, case) and refuses non-matching names;
 *  - redactionMarkers counts the engine's exact vocabulary and NOTHING else
 *    ([NOPE] is document text), block runs counted per RUN not per char;
 *  - markerSummary is value-asserted including the honesty clause;
 *  - the row exists ONLY for convention-named files; clicking fetches BOTH
 *    /documents/read URLs (exact query strings asserted) and renders exact
 *    diff kinds + texts, badges with exact counts, markers highlighted;
 *  - a missing original fails HONESTLY (the server's message, a hint, no
 *    diff rows) instead of a blank panel;
 *  - a payload at the 20k read cap carries the clipped-window disclaimer,
 *    below the cap carries none.
 */

import { describe, expect, it, vi, afterEach } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  DocPreview,
  markerDelta,
  markerSummary,
  redactionMarkers,
  redactionSourcePath,
  type PreviewData,
} from "@/components/chat/DocPreview";
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

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

describe("redactionSourcePath", () => {
  it("strips .redacted from the convention name, keeping dir + extension", () => {
    expect(redactionSourcePath("C:\\docs\\organizer.redacted.txt")).toBe(
      "C:\\docs\\organizer.txt",
    );
    expect(redactionSourcePath("/w/report.redacted.pdf")).toBe("/w/report.pdf");
  });

  it("keeps every other dot in a multi-dot name", () => {
    expect(redactionSourcePath("C:\\w\\K-1.v2.redacted.pdf")).toBe(
      "C:\\w\\K-1.v2.pdf",
    );
  });

  it("handles an extension-less source (Path.suffix was empty)", () => {
    expect(redactionSourcePath("C:\\w\\notes.redacted")).toBe("C:\\w\\notes");
  });

  it("is case-insensitive, mirroring Windows filenames", () => {
    expect(redactionSourcePath("C:\\w\\SCAN.REDACTED.PDF")).toBe(
      "C:\\w\\SCAN.PDF",
    );
  });

  it("returns null for anything not named by the convention", () => {
    expect(redactionSourcePath("C:\\w\\report.pdf")).toBeNull();
    // "redacted" as the whole stem is not `<stem>.redacted` — nothing to strip.
    expect(redactionSourcePath("C:\\w\\redacted.txt")).toBeNull();
    // double extension after .redacted is not the engine's single-suffix form
    expect(redactionSourcePath("C:\\w\\x.redacted.tar.gz")).toBeNull();
    expect(redactionSourcePath("C:\\w\\unredacted.txt")).toBeNull();
  });
});

describe("redactionMarkers", () => {
  it("counts the engine's label tags by VALUE and ignores look-alikes", () => {
    const m = redactionMarkers(
      "SSN: [SSN] spouse [SSN]\nEmail: [EMAIL]\nStatus: [NOPE] [ssn]",
    );
    // lowercase [ssn] is not what the engine writes; [NOPE] is document text
    expect(m.categories).toEqual({ SSN: 2, EMAIL: 1 });
    expect(m.blocks).toBe(0);
  });

  it("counts █ RUNS (one per redacted value), not characters", () => {
    const m = redactionMarkers("SSN: █████████ and ████\nplain line");
    expect(m.blocks).toBe(2);
    expect(m.categories).toEqual({});
  });

  it("a clean file has no markers at all", () => {
    expect(redactionMarkers("nothing to see")).toEqual({
      categories: {},
      blocks: 0,
    });
  });
});

describe("markerDelta", () => {
  it("subtracts the original's markers per category and drops zeroed ones", () => {
    const delta = markerDelta(
      { categories: { SSN: 3, EMAIL: 1 }, blocks: 4 },
      { categories: { SSN: 3 }, blocks: 1 },
    );
    // SSN 3-3 → gone entirely (a zero badge would still read as a removal)
    expect(delta).toEqual({ categories: { EMAIL: 1 }, blocks: 3 });
  });

  it("clamps below zero — an original with MORE markers claims nothing", () => {
    const delta = markerDelta(
      { categories: { SSN: 1 }, blocks: 0 },
      { categories: { SSN: 4, EMAIL: 2 }, blocks: 5 },
    );
    expect(delta).toEqual({ categories: {}, blocks: 0 });
  });

  it("a clean original leaves the redacted copy's counts untouched", () => {
    const red = { categories: { SSN: 2, DOB: 1 }, blocks: 3 };
    expect(markerDelta(red, { categories: {}, blocks: 0 })).toEqual(red);
  });
});

describe("markerSummary", () => {
  it("names every category count and the honesty clause, verbatim", () => {
    expect(markerSummary({ categories: { SSN: 3, ADDRESS: 2 }, blocks: 0 })).toBe(
      "Removed: 3 × SSN, 2 × ADDRESS — counted by re-reading the redacted file itself.",
    );
  });

  it("black-style blocks get their own count", () => {
    expect(markerSummary({ categories: {}, blocks: 5 })).toBe(
      "Removed: 5 × blacked-out (█) — counted by re-reading the redacted file itself.",
    );
  });

  it("no markers → empty string (the caller shows the no-marker wording)", () => {
    expect(markerSummary({ categories: {}, blocks: 0 })).toBe("");
  });
});

// ---------------------------------------------------------------------------
// The panel: row presence, fetches, diff, badges, errors, clip honesty
// ---------------------------------------------------------------------------

const ORIGINAL =
  "Taxpayer: Robert J. Alvarez\nSSN: 412-88-7391\nEmail: r.alvarez@northwindcpa.com\nPlain line";
const REDACTED =
  "Taxpayer: Robert J. Alvarez\nSSN: [SSN]\nEmail: [EMAIL]\nPlain line";

/** Route the mocked GET for one redacted-file preview + both read calls. */
function mockRoutes(opts: {
  path: string;
  content: string;
  reads: Record<string, string | Error>;
}) {
  vi.mocked(get).mockImplementation(async (p: string) => {
    if (p.startsWith("/documents/places")) return { places: [] } as never;
    if (p.startsWith("/documents/preview")) {
      const name = opts.path.split(/[\\/]/).pop() ?? opts.path;
      return {
        kind: "text",
        name,
        path: opts.path,
        suffix: ".txt",
        content: opts.content,
      } as PreviewData as never;
    }
    if (p.startsWith("/documents/read?path=")) {
      const q = decodeURIComponent(p.slice("/documents/read?path=".length));
      const hit = opts.reads[q];
      if (hit === undefined) throw new Error(`unexpected read ${q}`);
      if (hit instanceof Error) throw hit;
      return { path: q, text: hit, note: "" } as never;
    }
    throw new Error(`unexpected GET ${p}`);
  });
}

const RED_PATH = "C:\\uploads\\organizer.redacted.txt";
const SRC_PATH = "C:\\uploads\\organizer.txt";

describe("DocPreview compare-to-original", () => {
  it("no row at all for a file the convention does not name", async () => {
    mockRoutes({ path: "C:\\uploads\\organizer.txt", content: ORIGINAL, reads: {} });
    render(<DocPreview path="C:\uploads\organizer.txt" onClose={vi.fn()} />);
    await screen.findByText(/Plain line/);
    expect(
      screen.queryByRole("button", { name: /Compare to original/ }),
    ).not.toBeInTheDocument();
  });

  it("fetches BOTH files via /documents/read and renders the exact diff + badges", async () => {
    mockRoutes({
      path: RED_PATH,
      content: REDACTED,
      reads: { [SRC_PATH]: ORIGINAL, [RED_PATH]: REDACTED },
    });
    render(<DocPreview path={RED_PATH} onClose={vi.fn()} />);
    // The row names the inferred original next to the toggle.
    const toggle = await screen.findByRole("button", {
      name: /Compare to original/,
    });
    expect(
      screen.getByText("a redacted copy of organizer.txt"),
    ).toBeInTheDocument();

    fireEvent.click(toggle);
    await waitFor(() =>
      expect(screen.queryByText("Reading both files…")).not.toBeInTheDocument(),
    );
    // Both reads went out, with the exact query strings.
    const readCalls = vi
      .mocked(get)
      .mock.calls.map(([p]) => String(p))
      .filter((p) => p.startsWith("/documents/read"));
    expect(readCalls).toEqual([
      `/documents/read?path=${encodeURIComponent(SRC_PATH)}`,
      `/documents/read?path=${encodeURIComponent(RED_PATH)}`,
    ]);

    // Diff by VALUE: unchanged lines same, the PII lines removed→added.
    expect(
      screen.getAllByTestId("cmp-same").map((el) => el.textContent),
    ).toEqual(["  Taxpayer: Robert J. Alvarez", "  Plain line"]);
    expect(
      screen.getAllByTestId("cmp-removed").map((el) => el.textContent),
    ).toEqual(["− SSN: 412-88-7391", "− Email: r.alvarez@northwindcpa.com"]);
    expect(
      screen.getAllByTestId("cmp-added").map((el) => el.textContent),
    ).toEqual(["+ SSN: [SSN]", "+ Email: [EMAIL]"]);

    // Category badges with exact counts, markers highlighted in added lines.
    expect(
      screen.getAllByTestId("redaction-badge").map((el) => el.textContent),
    ).toEqual(["SSN × 1", "EMAIL × 1"]);
    expect(
      screen.getAllByTestId("redaction-marker").map((el) => el.textContent),
    ).toEqual(["[SSN]", "[EMAIL]"]);

    // The header states the counts + the honesty clause; nothing clipped here.
    expect(
      screen.getByText(
        /Removed: 1 × SSN, 1 × EMAIL — counted by re-reading the redacted file itself\./,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/Compared over the first/),
    ).not.toBeInTheDocument();

    // The toggle offers the way back to the plain preview.
    fireEvent.click(screen.getByRole("button", { name: /Hide comparison/ }));
    expect(screen.queryByTestId("cmp-added")).not.toBeInTheDocument();
    expect(screen.getByText(/Plain line/)).toBeInTheDocument();
  });

  it("black-style output counts █ runs and says so", async () => {
    const redactedBlack =
      "Taxpayer: Robert J. Alvarez\nSSN: ███████████\nEmail: ██████████████████████████\nPlain line";
    mockRoutes({
      path: RED_PATH,
      content: redactedBlack,
      reads: { [SRC_PATH]: ORIGINAL, [RED_PATH]: redactedBlack },
    });
    render(<DocPreview path={RED_PATH} onClose={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: /Compare to original/ }),
    );
    expect(
      await screen.findByText(
        /Removed: 2 × blacked-out \(█\) — counted by re-reading the redacted file itself\./,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getAllByTestId("redaction-badge").map((el) => el.textContent),
    ).toEqual(["█ × 2"]);
  });

  it("a missing original fails honestly — server message + hint, no diff rows", async () => {
    const { ApiError } = await import("@/lib/api");
    mockRoutes({
      path: RED_PATH,
      content: REDACTED,
      reads: {
        [SRC_PATH]: new ApiError(`cannot read: no such file: ${SRC_PATH}`, 400),
        [RED_PATH]: REDACTED,
      },
    });
    render(<DocPreview path={RED_PATH} onClose={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: /Compare to original/ }),
    );
    expect(
      await screen.findByText(/Couldn't compare: cannot read: no such file/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/organizer\.txt may have been moved, renamed, or deleted/),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("cmp-added")).not.toBeInTheDocument();
    expect(screen.queryByTestId("cmp-removed")).not.toBeInTheDocument();
  });

  it("a payload at the 20k read cap carries the clipped-window disclaimer", async () => {
    // Exactly at the cap — the daemon clips at text[:20000], so length 20000
    // is the "may be clipped" signal the panel keys on.
    const bigOriginal = "x".repeat(20_000);
    mockRoutes({
      path: RED_PATH,
      content: REDACTED,
      reads: { [SRC_PATH]: bigOriginal, [RED_PATH]: REDACTED },
    });
    render(<DocPreview path={RED_PATH} onClose={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: /Compare to original/ }),
    );
    expect(
      await screen.findByText(
        /Compared over the first 20,000 extracted characters of each file only\./,
      ),
    ).toBeInTheDocument();
  });

  it("remove-style output (no markers) keeps the diff but says no markers were found", async () => {
    const redactedRemove = "Taxpayer: Robert J. Alvarez\nSSN: \nEmail: \nPlain line";
    mockRoutes({
      path: RED_PATH,
      content: redactedRemove,
      reads: { [SRC_PATH]: ORIGINAL, [RED_PATH]: redactedRemove },
    });
    render(<DocPreview path={RED_PATH} onClose={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: /Compare to original/ }),
    );
    expect(
      await screen.findByText(/No new redaction markers in this file's extracted text/),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("redaction-badge")).not.toBeInTheDocument();
    // The diff still tells the story by value.
    expect(
      screen.getAllByTestId("cmp-removed").map((el) => el.textContent),
    ).toEqual(["− SSN: 412-88-7391", "− Email: r.alvarez@northwindcpa.com"]);
  });

  it("a refresh drops the fetched comparison — a stale diff must not survive new bytes", async () => {
    mockRoutes({
      path: RED_PATH,
      content: REDACTED,
      reads: { [SRC_PATH]: ORIGINAL, [RED_PATH]: REDACTED },
    });
    render(<DocPreview path={RED_PATH} onClose={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: /Compare to original/ }),
    );
    await screen.findAllByTestId("cmp-added");
    fireEvent.click(screen.getByRole("button", { name: "Refresh preview" }));
    await screen.findByText(/Plain line/);
    // Back on the plain preview, comparison off; toggling again REFETCHES.
    expect(screen.queryByTestId("cmp-added")).not.toBeInTheDocument();
    const readsBefore = vi
      .mocked(get)
      .mock.calls.filter(([p]) => String(p).startsWith("/documents/read")).length;
    fireEvent.click(
      screen.getByRole("button", { name: /Compare to original/ }),
    );
    await screen.findAllByTestId("cmp-added");
    const readsAfter = vi
      .mocked(get)
      .mock.calls.filter(([p]) => String(p).startsWith("/documents/read")).length;
    expect(readsAfter).toBe(readsBefore + 2);
  });

  it("a Refresh MID-FETCH invalidates the in-flight comparison — stale bytes never land", async () => {
    // The reads resolve only when the test says so, so the fetch straddles a
    // Refresh — the real window is seconds long (scanned-PDF OCR fallback).
    // Without the generation guard the old Promise resolves AFTER the reset,
    // repopulates compare, and the next toggle shows the stale diff without
    // refetching. Assert: the stale payload never renders, and re-toggling
    // issues two FRESH reads.
    const release: Array<() => void> = [];
    let defer = true;
    vi.mocked(get).mockImplementation(async (p: string) => {
      if (p.startsWith("/documents/places")) return { places: [] } as never;
      if (p.startsWith("/documents/preview")) {
        return {
          kind: "text",
          name: "organizer.redacted.txt",
          path: RED_PATH,
          suffix: ".txt",
          content: REDACTED,
        } as PreviewData as never;
      }
      if (p.startsWith("/documents/read?path=")) {
        const q = decodeURIComponent(p.slice("/documents/read?path=".length));
        const payload = {
          path: q,
          text: q === SRC_PATH ? (defer ? "STALE ORIGINAL" : ORIGINAL) : REDACTED,
          note: "",
        };
        if (defer)
          return new Promise((resolve) => {
            release.push(() => resolve(payload as never));
          }) as never;
        return payload as never;
      }
      throw new Error(`unexpected GET ${p}`);
    });
    render(<DocPreview path={RED_PATH} onClose={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: /Compare to original/ }),
    );
    expect(screen.getByText("Reading both files…")).toBeInTheDocument();
    // Refresh while both reads are still in flight.
    fireEvent.click(screen.getByRole("button", { name: "Refresh preview" }));
    await screen.findByText(/Plain line/);
    // NOW the pre-refresh reads resolve — too late; the panel must drop them.
    defer = false;
    await act(async () => {
      release.splice(0).forEach((fn) => fn());
    });
    expect(screen.queryByText(/STALE ORIGINAL/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("cmp-added")).not.toBeInTheDocument();
    // Toggling again refetches (2 fresh reads) instead of showing the stale
    // result instantly — the exact failure mode of a missing guard.
    fireEvent.click(
      screen.getByRole("button", { name: /Compare to original/ }),
    );
    await screen.findAllByTestId("cmp-added");
    const readCalls = vi
      .mocked(get)
      .mock.calls.map(([p]) => String(p))
      .filter((p) => p.startsWith("/documents/read"));
    expect(readCalls).toHaveLength(4); // 2 abandoned + 2 fresh
    expect(screen.queryByText(/STALE ORIGINAL/)).not.toBeInTheDocument();
    expect(
      screen.getAllByTestId("cmp-removed").map((el) => el.textContent),
    ).toEqual(["− SSN: 412-88-7391", "− Email: r.alvarez@northwindcpa.com"]);
  });

  it("reopening onto a cached error RETRIES instead of re-showing the stale failure", async () => {
    const { ApiError } = await import("@/lib/api");
    // mockRoutes closes over this object, so mutating it between clicks
    // models the original being restored to disk after a failed read.
    const reads: Record<string, string | Error> = {
      [SRC_PATH]: new ApiError(`cannot read: no such file: ${SRC_PATH}`, 400),
      [RED_PATH]: REDACTED,
    };
    mockRoutes({ path: RED_PATH, content: REDACTED, reads });
    render(<DocPreview path={RED_PATH} onClose={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: /Compare to original/ }),
    );
    await screen.findByText(/Couldn't compare: cannot read: no such file/);
    fireEvent.click(screen.getByRole("button", { name: /Hide comparison/ }));
    reads[SRC_PATH] = ORIGINAL; // the file came back
    fireEvent.click(
      screen.getByRole("button", { name: /Compare to original/ }),
    );
    // A fresh fetch, a real diff — not the cached error again.
    await screen.findAllByTestId("cmp-added");
    expect(screen.queryByText(/Couldn't compare/)).not.toBeInTheDocument();
    const readCalls = vi
      .mocked(get)
      .mock.calls.filter(([p]) => String(p).startsWith("/documents/read")).length;
    expect(readCalls).toBe(4); // 2 failed-attempt reads + 2 retry reads
  });

  it("names the REDACTED copy in the hint when THAT side's read failed", async () => {
    const { ApiError } = await import("@/lib/api");
    mockRoutes({
      path: RED_PATH,
      content: REDACTED,
      reads: {
        [SRC_PATH]: ORIGINAL,
        [RED_PATH]: new ApiError(`cannot read: no such file: ${RED_PATH}`, 400),
      },
    });
    render(<DocPreview path={RED_PATH} onClose={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: /Compare to original/ }),
    );
    await screen.findByText(/Couldn't compare: cannot read: no such file/);
    // The hint blames the file that ACTUALLY failed — the redacted copy —
    // not the inferred original.
    expect(
      screen.getByText(
        /organizer\.redacted\.txt may have been moved, renamed, or deleted/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/organizer\.txt may have been moved/),
    ).not.toBeInTheDocument();
  });

  it("a 403 policy refusal says so — never 'moved, renamed, or deleted'", async () => {
    const { ApiError } = await import("@/lib/api");
    mockRoutes({
      path: RED_PATH,
      content: REDACTED,
      reads: {
        [SRC_PATH]: new ApiError("path is not readable under the file policy", 403),
        [RED_PATH]: REDACTED,
      },
    });
    render(<DocPreview path={RED_PATH} onClose={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: /Compare to original/ }),
    );
    await screen.findByText(/Couldn't compare: path is not readable/);
    expect(
      screen.getByText(/file policy refused to read organizer\.txt/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/moved, renamed, or deleted/),
    ).not.toBeInTheDocument();
  });

  it("re-redacting an already-redacted copy badges only the NEW markers", async () => {
    // The engine's own convention applied twice: x.redacted.txt →
    // x.redacted.redacted.txt, and the greedy client regex pairs them.
    // Pass one already wrote [SSN]; pass two only redacted the email — the
    // badges must not claim an SSN removal this pass never made.
    const srcOnce = "C:\\uploads\\organizer.redacted.txt";
    const redTwice = "C:\\uploads\\organizer.redacted.redacted.txt";
    const passOne = "Taxpayer: Robert J. Alvarez\nSSN: [SSN]\nEmail: r.alvarez@northwindcpa.com\nPlain line";
    const passTwo = "Taxpayer: Robert J. Alvarez\nSSN: [SSN]\nEmail: [EMAIL]\nPlain line";
    mockRoutes({
      path: redTwice,
      content: passTwo,
      reads: { [srcOnce]: passOne, [redTwice]: passTwo },
    });
    render(<DocPreview path={redTwice} onClose={vi.fn()} />);
    const toggle = await screen.findByRole("button", {
      name: /Compare to original/,
    });
    expect(
      screen.getByText("a redacted copy of organizer.redacted.txt"),
    ).toBeInTheDocument();
    fireEvent.click(toggle);
    await screen.findAllByTestId("cmp-added");
    // Only the SECOND pass's marker is badged and summarized.
    expect(
      screen.getAllByTestId("redaction-badge").map((el) => el.textContent),
    ).toEqual(["EMAIL × 1"]);
    expect(
      screen.getByText(
        /Removed: 1 × EMAIL — counted by re-reading the redacted file itself\./,
      ),
    ).toBeInTheDocument();
    // The diff agrees: the [SSN] line is unchanged, so no SSN removal claim.
    expect(
      screen.getAllByTestId("cmp-same").map((el) => el.textContent),
    ).toContain("  SSN: [SSN]");
  });
});
