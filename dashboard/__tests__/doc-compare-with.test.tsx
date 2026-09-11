/**
 * "Compare with…" in DocPreview (C-07).
 *
 * The redaction receipt (v1.168.0) already compared a file with ONE fixed other
 * side. This is the same machinery — the same `/documents/read` pair, the same
 * diffLines view — pointed at a document the USER picks, so "compare these two"
 * is answerable from the panel the files are already in.
 *
 * The candidate list comes from GET /documents/siblings (the file's own folder,
 * which since v1.244.0 is the conversation's file list) UNIONED with an optional
 * `compareCandidates` prop, so no page has to be edited for the feature to be
 * reachable.
 *
 * What carries weight here:
 *  - no row at all when there is nothing to compare with;
 *  - the picker offers the folder's files by NAME and never this file itself;
 *  - picking one fetches BOTH files (exact query strings, other side first) and
 *    renders the diff;
 *  - the wording names the OTHER file and says which colour is which — a
 *    file-vs-file diff must not be described as a redaction;
 *  - NO redaction badges in file mode: the same marker counting would otherwise
 *    claim a redaction pass that never ran;
 *  - the two rows coexist on a redacted file without either claiming the
 *    other's state.
 */

import { describe, expect, it, vi, afterEach } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { DocPreview, type PreviewData } from "@/components/chat/DocPreview";
import { get } from "@/lib/api";

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

const DIR = "C:\\Users\\VR\\Documents\\Iron Jarvis\\2026-09-11 Northwind";
const THIS_FILE = `${DIR}\\engagement 2025.docx`;
const OTHER = `${DIR}\\engagement 2024.docx`;
const THIRD = `${DIR}\\fees.xlsx`;

const THIS_TEXT =
  "Engagement Letter\nClient: Northwind Consulting LLC\nOur fee for the 2025 return is $3,000\nPrepared by Blue Harbor Advisors.";
const OTHER_TEXT =
  "Engagement Letter\nClient: Northwind Consulting LLC\nOur fee for the 2025 return is $1,250\nPrepared by Blue Harbor Advisors.";

function mockRoutes(opts: {
  path?: string;
  content?: string;
  siblings?: string[] | Error;
  reads?: Record<string, string | Error>;
}) {
  const path = opts.path ?? THIS_FILE;
  vi.mocked(get).mockImplementation(async (p: string) => {
    if (p.startsWith("/documents/places")) return { places: [] } as never;
    if (p.startsWith("/documents/siblings")) {
      if (opts.siblings instanceof Error) throw opts.siblings;
      return {
        files: (opts.siblings ?? []).map((f) => ({
          path: f,
          name: f.split(/[\\/]/).pop(),
        })),
        truncated: false,
      } as never;
    }
    if (p.startsWith("/documents/preview")) {
      return {
        kind: "text",
        name: path.split(/[\\/]/).pop() ?? path,
        path,
        suffix: ".txt",
        content: opts.content ?? THIS_TEXT,
      } as PreviewData as never;
    }
    if (p.startsWith("/documents/read?path=")) {
      const q = decodeURIComponent(p.slice("/documents/read?path=".length));
      const hit = (opts.reads ?? {})[q];
      if (hit === undefined) throw new Error(`unexpected read ${q}`);
      if (hit instanceof Error) throw hit;
      return { path: q, text: hit, note: "" } as never;
    }
    throw new Error(`unexpected GET ${p}`);
  });
}

const READS = { [OTHER]: OTHER_TEXT, [THIS_FILE]: THIS_TEXT };

describe("DocPreview compare-with", () => {
  it("offers no row when the folder holds nothing comparable", async () => {
    mockRoutes({ siblings: [] });
    render(<DocPreview path={THIS_FILE} onClose={vi.fn()} />);
    await screen.findByText(/Prepared by Blue Harbor Advisors\./);
    expect(screen.queryByTestId("compare-with")).not.toBeInTheDocument();
  });

  it("a failed siblings lookup is simply no candidates, not an error", async () => {
    mockRoutes({ siblings: new Error("boom") });
    render(<DocPreview path={THIS_FILE} onClose={vi.fn()} />);
    // The preview itself is unaffected — the panel's own job still works.
    await screen.findByText(/Prepared by Blue Harbor Advisors\./);
    expect(screen.queryByTestId("compare-with")).not.toBeInTheDocument();
    expect(screen.queryByText(/Couldn't compare/)).not.toBeInTheDocument();
  });

  it("picking a document diffs the two files and says which colour is which", async () => {
    mockRoutes({ siblings: [OTHER, THIRD], reads: READS });
    render(<DocPreview path={THIS_FILE} onClose={vi.fn()} />);

    fireEvent.click(await screen.findByTestId("compare-with"));

    // The picker names the folder's files, and never this file itself.
    const select = screen.getByTestId("compare-pick") as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.textContent);
    expect(options).toEqual([
      "Pick a document…",
      "engagement 2024.docx",
      "fees.xlsx",
    ]);
    expect(options).not.toContain("engagement 2025.docx");

    fireEvent.change(select, { target: { value: OTHER } });
    await waitFor(() =>
      expect(screen.queryByText("Reading both files…")).not.toBeInTheDocument(),
    );

    // BOTH files were read, the picked one first (it is the "before" side).
    const readCalls = vi
      .mocked(get)
      .mock.calls.map(([p]) => String(p))
      .filter((p) => p.startsWith("/documents/read"));
    expect(readCalls).toEqual([
      `/documents/read?path=${encodeURIComponent(OTHER)}`,
      `/documents/read?path=${encodeURIComponent(THIS_FILE)}`,
    ]);

    // The difference, by value: the fee line only.
    expect(
      screen.getAllByTestId("cmp-removed").map((el) => el.textContent),
    ).toEqual(["− Our fee for the 2025 return is $1,250"]);
    expect(
      screen.getAllByTestId("cmp-added").map((el) => el.textContent),
    ).toEqual(["+ Our fee for the 2025 return is $3,000"]);

    // The wording names the other file and maps the colours to it — the
    // redaction sentence would be a lie here.
    expect(
      screen.getByText(/Comparing .* with engagement 2024\.docx\./),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Red lines are engagement 2024\.docx; green lines are this file/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/counted by re-reading the redacted file itself/),
    ).not.toBeInTheDocument();
  });

  it("shows NO redaction badges for a file-vs-file comparison", async () => {
    // Both documents contain the engine's marker vocabulary — a K-1 that really
    // says "[SSN]" in its text. Counting those here would badge removals no
    // redaction pass made.
    const a = "Name: Alvarez\nSSN: [SSN]\nFee: 100";
    const b = "Name: Alvarez\nSSN: [SSN]\nFee: 250";
    mockRoutes({
      content: b,
      siblings: [OTHER],
      reads: { [OTHER]: a, [THIS_FILE]: b },
    });
    render(<DocPreview path={THIS_FILE} onClose={vi.fn()} />);

    fireEvent.click(await screen.findByTestId("compare-with"));
    fireEvent.change(screen.getByTestId("compare-pick"), {
      target: { value: OTHER },
    });
    await screen.findAllByTestId("cmp-added");

    expect(screen.queryByTestId("redaction-badge")).not.toBeInTheDocument();
    expect(
      screen.getAllByTestId("cmp-added").map((el) => el.textContent),
    ).toEqual(["+ Fee: 250"]);
  });

  it("the button turns into the way back out, naming the file on screen", async () => {
    mockRoutes({ siblings: [OTHER], reads: READS });
    render(<DocPreview path={THIS_FILE} onClose={vi.fn()} />);

    fireEvent.click(await screen.findByTestId("compare-with"));
    fireEvent.change(screen.getByTestId("compare-pick"), {
      target: { value: OTHER },
    });
    await screen.findAllByTestId("cmp-added");

    const back = screen.getByTestId("compare-with");
    expect(back).toHaveTextContent("Hide comparison with engagement 2024.docx");
    fireEvent.click(back);
    expect(screen.queryByTestId("cmp-added")).not.toBeInTheDocument();
    expect(
      screen.getByText(/Prepared by Blue Harbor Advisors\./),
    ).toBeInTheDocument();
  });

  it("candidates handed in by the page are merged with the folder's, deduped", async () => {
    mockRoutes({ siblings: [OTHER], reads: READS });
    render(
      <DocPreview
        path={THIS_FILE}
        onClose={vi.fn()}
        // One repeat of a sibling (different case, as Windows would report it)
        // and one file the page knows about that the folder scan did not return.
        compareCandidates={[OTHER.toUpperCase(), THIRD, THIS_FILE]}
      />,
    );

    fireEvent.click(await screen.findByTestId("compare-with"));
    const options = Array.from(
      (screen.getByTestId("compare-pick") as HTMLSelectElement).options,
    ).map((o) => o.textContent);

    // THIS_FILE is dropped, the duplicate appears once, both sources are there.
    expect(options.filter((o) => o?.toLowerCase() === "engagement 2024.docx")).toHaveLength(1);
    expect(options).toContain("fees.xlsx");
    expect(options).not.toContain("engagement 2025.docx");
  });

  it("on a redacted file the two comparisons coexist without lying about each other", async () => {
    const RED = `${DIR}\\organizer.redacted.txt`;
    const SRC = `${DIR}\\organizer.txt`;
    const redText = "Taxpayer: Alvarez\nSSN: [SSN]";
    const srcText = "Taxpayer: Alvarez\nSSN: 412-88-7391";
    mockRoutes({
      path: RED,
      content: redText,
      siblings: [OTHER],
      reads: { [SRC]: srcText, [RED]: redText, [OTHER]: OTHER_TEXT },
    });
    render(<DocPreview path={RED} onClose={vi.fn()} />);

    // Both affordances are present.
    const receipt = await screen.findByRole("button", {
      name: /Compare to original/,
    });
    expect(screen.getByTestId("compare-with")).toBeInTheDocument();

    // The receipt badges its removals...
    fireEvent.click(receipt);
    await screen.findAllByTestId("redaction-badge");
    expect(
      screen.getByText(/counted by re-reading the redacted file itself/),
    ).toBeInTheDocument();

    // ...and switching to a picked file drops the redaction framing entirely.
    fireEvent.click(screen.getByTestId("compare-with"));
    fireEvent.change(screen.getByTestId("compare-pick"), {
      target: { value: OTHER },
    });
    await waitFor(() =>
      expect(screen.queryByTestId("redaction-badge")).not.toBeInTheDocument(),
    );
    expect(
      screen.getByText(/Red lines are engagement 2024\.docx/),
    ).toBeInTheDocument();
    // The receipt button no longer claims to be showing anything.
    expect(
      screen.getByRole("button", { name: /Compare to original/ }),
    ).toBeInTheDocument();
  });
});
