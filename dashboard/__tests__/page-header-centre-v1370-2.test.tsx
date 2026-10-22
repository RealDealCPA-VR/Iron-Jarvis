/**
 * v1.370.2 — PageHeader's `center` slot: a control in the header's exact
 * middle that does not move when the actions change. The user: Build's
 * Terminals | IJ harness switch "should be centered and fixed in both modes"
 * — it sat in the right-hand actions, so the Terminals view's Shell and New
 * terminal pushed it left and the IJ harness view let it slide right.
 *
 * jsdom has no layout, so these pin the STRUCTURE that makes it true: two
 * EQUAL side columns (`minmax(0,1fr)` each) around an `auto` middle, and an
 * actions column that keeps its height when it is empty. The pixel check
 * (the switch's centre = the header's centre, the same in both views) is in
 * e2e/ij-harness.spec.ts.
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PageHeader } from "@/components/PageHeader";

afterEach(() => cleanup());

describe("PageHeader's centre slot", () => {
  it("lays the header out as title | centre | actions with EQUAL side columns", () => {
    render(<PageHeader title="Build" center={<span data-testid="sw">switch</span>} actions={<button>New</button>} />);
    const row = screen.getByTestId("page-header-centred");
    expect(row.className).toContain("lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]");
    expect(screen.getByTestId("page-header-center")).toContainElement(screen.getByTestId("sw"));
    expect(screen.getByTestId("page-header-center").className).toContain("lg:justify-self-center");
    expect(screen.getByTestId("page-header-actions").className).toContain("lg:justify-self-end");
    // Order: title, centre, actions — the centre is never inside the actions.
    const kids = Array.from(row.children);
    expect(kids.indexOf(screen.getByTestId("page-header-center"))).toBe(1);
    expect(kids.indexOf(screen.getByTestId("page-header-actions"))).toBe(2);
  });

  it("keeps the actions column (and its height) when there are no actions, so the centre does not move", () => {
    render(<PageHeader title="Build" center={<span>switch</span>} actions={null} />);
    const actions = screen.getByTestId("page-header-actions");
    expect(actions).toBeInTheDocument();
    expect(actions.className).toContain("min-h-8");
  });

  it("without a centre, a page's header is exactly what it was", () => {
    render(<PageHeader title="Chat" actions={<button>New</button>} />);
    expect(screen.queryByTestId("page-header-centred")).toBeNull();
    expect(screen.queryByTestId("page-header-center")).toBeNull();
    expect(screen.getByRole("button", { name: "New" }).parentElement?.className).toBe("flex items-center gap-2");
  });
});
