/**
 * Compaction inspect (v1.169.0) — the chip + card that make the standing
 * summary readable again.
 *
 * WHAT THESE TESTS GUARD:
 *   - the chip renders NOTHING unless the server said a summary stands
 *     (found: true) — it must never guess off the context gauge;
 *   - the card shows the summary VERBATIM and the removed claims under a
 *     clearly-labeled section;
 *   - the four stripped states are each honest: claims listed; "nothing was
 *     stripped" only when stripped === 0; a positive count with no recorded
 *     text says "not recorded" — NEVER the empty state (that would be a small
 *     lie about the exact thing the card exists to surface); and a PARTIAL
 *     list (stripped > claims listed — the producer caps recorded texts at 20
 *     while the count stays full) carries an explicit "and N more" tail
 *     rather than reading as the whole story.
 */

import { describe, expect, it, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  CompactionCard,
  CompactionChip,
  chipLabel,
  strippedNote,
  truncatedNote,
  type CompactionInfo,
} from "@/components/chat/CompactionCard";

afterEach(() => {
  cleanup();
});

const STANDING: CompactionInfo = {
  found: true,
  summary:
    "# Earlier in this conversation (compacted)\nGOAL:\n- reconcile the Q2 ledger\nDONE:\n- ran write_document",
  covers: 12,
  stripped: 2,
  stripped_claims: ["C:/gone/a.py", '"never said this"'],
  trigger: "manual",
  provider: "acme",
  model: "acme-1",
  created_at: "2026-08-12T10:00:00+00:00",
};

describe("strippedNote — the three honest states", () => {
  it("is null when claims are listed (the list renders instead)", () => {
    expect(strippedNote(2, ["C:/gone/a.py"])).toBeNull();
  });

  it("reports the empty state only at zero", () => {
    expect(strippedNote(0, [])).toBe(
      "Nothing was stripped — every checkable claim was corroborated by the record.",
    );
  });

  it("says NOT RECORDED for a count without text — never the empty state", () => {
    const note = strippedNote(3, []);
    expect(note).toBe(
      "3 claims were removed, but the removed text was not recorded for this summary.",
    );
    expect(note).not.toContain("Nothing was stripped");
  });

  it("singularizes the not-recorded count", () => {
    expect(strippedNote(1, [])).toBe(
      "1 claim was removed, but the removed text was not recorded for this summary.",
    );
  });
});

describe("truncatedNote — the fourth honest state (a PARTIAL claims list)", () => {
  it("is silent when the list is complete or empty", () => {
    expect(truncatedNote(2, 2)).toBeNull(); // complete list
    expect(truncatedNote(0, 0)).toBeNull(); // nothing stripped
    expect(truncatedNote(3, 0)).toBeNull(); // count-only — strippedNote's job
    expect(truncatedNote(2, 3)).toBeNull(); // never a negative "more"
  });

  it("says how many claims beyond the listed ones were removed", () => {
    expect(truncatedNote(25, 20)).toBe(
      "…and 5 more claims were removed — the removed text was not recorded beyond the 20 listed.",
    );
  });

  it("singularizes a single unrecorded extra", () => {
    expect(truncatedNote(21, 20)).toBe(
      "…and 1 more claim was removed — the removed text was not recorded beyond the 20 listed.",
    );
  });
});

describe("chipLabel", () => {
  it("counts and pluralizes", () => {
    expect(chipLabel(12)).toBe("12 older messages summarized");
    expect(chipLabel(1)).toBe("1 older message summarized");
  });
});

describe("CompactionChip", () => {
  it("renders literally nothing without a found summary", () => {
    const none = render(<CompactionChip info={null} onView={() => {}} />);
    expect(none.container.firstChild).toBeNull();
    const notFound = render(
      <CompactionChip info={{ found: false }} onView={() => {}} />,
    );
    expect(notFound.container.firstChild).toBeNull();
  });

  it("says how many messages the summary replaces and opens on view", () => {
    const onView = vi.fn();
    render(<CompactionChip info={STANDING} onView={onView} />);
    expect(screen.getByText(/12 older messages summarized/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "view" }));
    expect(onView).toHaveBeenCalledTimes(1);
  });
});

describe("CompactionCard", () => {
  it("shows the summary verbatim and the removed claims, labeled", () => {
    render(<CompactionCard info={STANDING} onClose={() => {}} />);
    // The summary body, exactly as the model reads it.
    expect(
      screen.getByText(/reconcile the Q2 ledger/),
    ).toBeInTheDocument();
    // The clearly-labeled removed section, with each claim's text.
    expect(
      screen.getByText("Removed because the record could not corroborate it"),
    ).toBeInTheDocument();
    expect(screen.getByText("C:/gone/a.py")).toBeInTheDocument();
    expect(screen.getByText('"never said this"')).toBeInTheDocument();
    // Attribution footer: who wrote it and why it ran.
    expect(
      screen.getByText(/compacted at your request/),
    ).toBeInTheDocument();
    expect(screen.getByText(/written by acme · acme-1/)).toBeInTheDocument();
  });

  it("shows the empty state when nothing was stripped", () => {
    render(
      <CompactionCard
        info={{ ...STANDING, stripped: 0, stripped_claims: [] }}
        onClose={() => {}}
      />,
    );
    expect(
      screen.getByText(
        "Nothing was stripped — every checkable claim was corroborated by the record.",
      ),
    ).toBeInTheDocument();
  });

  it("says NOT RECORDED for a positive count with no claim text", () => {
    render(
      <CompactionCard
        info={{ ...STANDING, stripped: 3, stripped_claims: [] }}
        onClose={() => {}}
      />,
    );
    expect(
      screen.getByText(
        "3 claims were removed, but the removed text was not recorded for this summary.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Nothing was stripped/)).toBeNull();
  });

  it("tells the user when the listed claims are only PART of what was removed", () => {
    // The producer records at most 20 claim texts while `stripped` keeps the
    // full count — 20 rows with no tail would read as the whole story.
    const many = Array.from({ length: 20 }, (_, i) => `C:/gone/file-${i}.py`);
    render(
      <CompactionCard
        info={{ ...STANDING, stripped: 25, stripped_claims: many }}
        onClose={() => {}}
      />,
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(20);
    expect(
      screen.getByText(
        "…and 5 more claims were removed — the removed text was not recorded beyond the 20 listed.",
      ),
    ).toBeInTheDocument();
  });

  it("shows no truncation tail when the claims list is complete", () => {
    render(<CompactionCard info={STANDING} onClose={() => {}} />);
    expect(screen.queryByText(/more claim/)).toBeNull();
  });

  it("closes on the close button and on Escape", () => {
    const onClose = vi.fn();
    render(<CompactionCard info={STANDING} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    // DISPATCHED ON `document` since v1.216.1. The card is portalled through
    // `Modal` now, whose Escape listener is on document — and a keydown
    // dispatched directly ON window never reaches a document listener, because
    // window sits ABOVE document in the propagation path. Real keystrokes
    // target the focused element and bubble document → window, so document is
    // the honest place both to listen and to dispatch.
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("labels the auto trigger honestly", () => {
    render(
      <CompactionCard info={{ ...STANDING, trigger: "auto" }} onClose={() => {}} />,
    );
    expect(
      screen.getByText(/compacted automatically at the context ceiling/),
    ).toBeInTheDocument();
  });

  it("filters blank claim entries rather than rendering empty rows", () => {
    render(
      <CompactionCard
        info={{ ...STANDING, stripped: 1, stripped_claims: ["  ", "C:/x/y.md"] }}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("C:/x/y.md")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });
});
