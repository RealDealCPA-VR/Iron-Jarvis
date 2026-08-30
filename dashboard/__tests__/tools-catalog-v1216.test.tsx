import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

/**
 * THE TOOLS PAGE, REWORKED FROM A UX REVIEW (v1.216.0).
 *
 * The review's headline: "Right now everything looks available-to-add… Users
 * cannot answer 'what is live right now?' without hunting", and "every card is:
 * icon, title, snake_case id, sentence, disclosure, + Add" — four questions
 * flattened into one shape, with risk indistinguishable from convenience.
 *
 * These tests cover the pieces that carry the meaning: the risk vocabulary,
 * the status chips, the filter predicate, and the two dialogs that now stand
 * between a click and a grant. The page itself is exercised through the
 * existing suites (tool-suite-shell-injection-v1192 drives Add end to end and
 * still asserts that exactly the shown argv reaches the daemon).
 *
 * ONE CORRECTION TO THE REVIEW, recorded because it changed the design: the
 * grant was NOT default-on. `mcp_auto_approve` defaults to False and
 * `mcp_call` is "ask". What the page did was subtler — it rendered
 * `auto_approve_effective` (global OR any-server) in a checkbox labelled as
 * the global, so ONE extension with its own grant made the blanket switch look
 * armed. The panel below shows the two facts separately.
 */

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  post: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

import { RiskChips, SourceChip, StatusChip } from "@/components/tools/chips";
import {
  CAPABILITY_ORDER,
  RICHER_PACK,
  VERIFY_PACK_ID,
  isOfficial,
  packCaps,
  suiteCaps,
} from "@/components/tools/meta";
import { EMPTY_FILTERS, FilterBar, filtersActive } from "@/components/tools/FilterBar";
import { EnableDialog } from "@/components/tools/EnableDialog";
import { PermissionsPanel } from "@/components/tools/PermissionsPanel";

afterEach(cleanup);

/* ------------------------------------------------------------------ risk --- */

describe("risk is a fact on the card, not a guess", () => {
  it("names what each built-in actually reaches", () => {
    // "Opening a browser or zipping a folder is not the same as DNS lookup."
    expect(suiteCaps("dns_lookup")).toEqual(["network"]);
    expect(suiteCaps("open_url")).toEqual(["browser"]);
    expect(suiteCaps("zip_folder")).toEqual(["read", "write"]);
    expect(suiteCaps("disk_free")).toEqual(["system"]);
  });

  it("claims NOTHING for a pack this build has never seen", () => {
    // The catalog is served by the daemon and can grow past this dashboard. A
    // keyword guess would be confidently wrong exactly where it matters, so an
    // unknown id gets no chips rather than a reassuring "read".
    expect(packCaps("filesystem")).toEqual(["read", "write"]);
    expect(packCaps("playwright")).toEqual(["network", "browser"]);
    expect(packCaps("some-pack-shipped-later")).toEqual([]);
  });

  it("orders chips loosest → most reaching, always the same way", () => {
    const caps = packCaps("box");
    expect(caps).toEqual(CAPABILITY_ORDER.filter((c) => caps.includes(c)));
  });

  it("renders a chip per capability, with a word and not only a colour", () => {
    render(<RiskChips caps={["read", "write"]} />);
    expect(screen.getByTestId("risk-read").textContent).toContain("read");
    expect(screen.getByTestId("risk-write").textContent).toContain("write");
  });

  it("renders nothing at all when a pack reaches nothing", () => {
    const { container } = render(<RiskChips caps={packCaps("sequentialthinking")} />);
    expect(container.textContent).toBe("");
  });
});

/* ---------------------------------------------------------------- status --- */

describe("status is a word, not a colour", () => {
  it("says which state it is in", () => {
    render(<StatusChip status="added" />);
    expect(screen.getByTestId("status-added").textContent).toContain("Enabled");
    cleanup();
    render(<StatusChip status="available" />);
    expect(screen.getByTestId("status-available").textContent).toContain("Not added");
  });

  it("promotes a missing runtime to a real state, naming it", () => {
    // "Promote runtime to a real state: 'Python not installed — Install uv'
    // instead of a gray needs Python (uv) pill."
    render(<StatusChip status="blocked" needs="Node" />);
    expect(screen.getByTestId("status-blocked").textContent).toContain("Needs Node");
  });

  it("marks community packs and leaves official ones quiet", () => {
    render(<SourceChip official={false} />);
    expect(screen.getByTestId("source-community").textContent).toBe("community");
    cleanup();
    render(<SourceChip official />);
    expect(screen.getByTestId("source-official").textContent).toBe("official");
    expect(isOfficial("reference")).toBe(true);
    expect(isOfficial("integration")).toBe(false);
  });
});

/* --------------------------------------------------------------- filters --- */

describe("filters", () => {
  it("start clear, and say when they are not", () => {
    expect(filtersActive(EMPTY_FILTERS)).toBe(false);
    expect(filtersActive({ ...EMPTY_FILTERS, caps: ["write"] })).toBe(true);
    expect(filtersActive({ ...EMPTY_FILTERS, q: "git" })).toBe(true);
  });

  it("toggle off when pressed twice — every chip is a toggle, not a mode", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <FilterBar
        value={EMPTY_FILTERS}
        onChange={onChange}
        counts={{ builtin: 8, extension: 8 }}
      />,
    );
    fireEvent.click(screen.getByTestId("filter-added"));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: "added" }),
    );
    rerender(
      <FilterBar
        value={{ ...EMPTY_FILTERS, status: "added" }}
        onChange={onChange}
        counts={{ builtin: 8, extension: 8 }}
      />,
    );
    fireEvent.click(screen.getByTestId("filter-added"));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ status: "all" }));
  });

  it("says which control it is, since the title bar also has a search", () => {
    render(
      <FilterBar
        value={EMPTY_FILTERS}
        onChange={vi.fn()}
        counts={{ builtin: 8, extension: 8 }}
      />,
    );
    expect(screen.getByLabelText("Filter tools and extensions")).toBeTruthy();
  });

  it("reports what is left visible, so an empty grid reads as a filter", () => {
    render(
      <FilterBar
        value={{ ...EMPTY_FILTERS, caps: ["browser"] }}
        onChange={vi.fn()}
        counts={{ builtin: 1, extension: 1 }}
      />,
    );
    expect(screen.getByTestId("tool-filters").textContent).toContain(
      "1 built-in · 1 extension",
    );
  });
});

/* ---------------------------------------------------- one model, two depths */

describe("the two catalogs are one model", () => {
  it("points a thin built-in at the richer pack that covers it", () => {
    // "People will add both and not know why."
    expect(RICHER_PACK.list_dir.name).toBe("Files & folders");
    expect(RICHER_PACK.http_get.name).toBe("Fetch web pages");
    expect(RICHER_PACK.git_status.name).toBe("Git repositories");
  });

  it("keeps the connection test out of the capability list", () => {
    // "'Demo / connection test' is useful; pin it as a Verify setup action at
    // the top of Extensions, not as a peer of Long-term memory."
    expect(VERIFY_PACK_ID).toBe("everything");
  });
});

/* ------------------------------------------------- the consequence preview */

describe("adding is a decision, not a click", () => {
  const plan = {
    kind: "extension" as const,
    title: "Files & folders",
    id: "filesystem",
    summary: "Read and write files inside a folder you choose.",
    caps: ["read", "write"] as const,
    needs: "Node",
    official: true,
    offerAutoApprove: true,
    fields: [{ key: "ph:<folder>", label: "<folder>", kind: "path" as const }],
  };

  it("says what the agent will be able to do, in words", () => {
    render(
      <EnableDialog
        plan={{ ...plan, caps: [...plan.caps] }}
        busy={false}
        onCancel={vi.fn()}
        onEnable={vi.fn()}
      />,
    );
    const d = screen.getByTestId("enable-dialog");
    expect(within(d).getByText(/reads files/)).toBeTruthy();
    expect(within(d).getByText(/writes files/)).toBeTruthy();
    expect(within(d).getByText(/runs through/)).toBeTruthy();
    expect(within(d).getByText(/Every agent in this fleet/)).toBeTruthy();
  });

  it("collects the folder BEFORE enabling, and will not enable without it", () => {
    const onEnable = vi.fn();
    render(
      <EnableDialog
        plan={{ ...plan, caps: [...plan.caps] }}
        busy={false}
        onCancel={vi.fn()}
        onEnable={onEnable}
      />,
    );
    const confirm = screen.getByTestId("enable-confirm") as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("enable-field-ph:<folder>"), {
      target: { value: "C:\\work" },
    });
    expect((screen.getByTestId("enable-confirm") as HTMLButtonElement).disabled).toBe(
      false,
    );
    fireEvent.click(screen.getByTestId("enable-confirm"));
    expect(onEnable).toHaveBeenCalledWith({ "ph:<folder>": "C:\\work" }, false);
  });

  it("defaults to ASK, and passes the choice back when changed", () => {
    // "Default ask each time for new plugins."
    const onEnable = vi.fn();
    render(
      <EnableDialog
        plan={{ ...plan, caps: [], fields: [] }}
        busy={false}
        onCancel={vi.fn()}
        onEnable={onEnable}
      />,
    );
    expect((screen.getByTestId("enable-ask") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByTestId("enable-allow"));
    fireEvent.click(screen.getByTestId("enable-confirm"));
    expect(onEnable).toHaveBeenCalledWith({}, true);
  });

  it("offers no allow-without-asking for a built-in, which has no such flag", () => {
    render(
      <EnableDialog
        plan={{
          kind: "builtin",
          title: "Zip up a folder",
          id: "zip_folder",
          summary: "Bundle a folder into a .zip.",
          caps: ["read", "write"],
        }}
        busy={false}
        onCancel={vi.fn()}
        onEnable={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("enable-allow")).toBeNull();
  });
});

/* ----------------------------------------------------------- permissions --- */

describe("permissions stop conflating one extension with all of them", () => {
  const rows = [
    { name: "fetch", autoApprove: false, tools: 1 },
    { name: "brave", autoApprove: true, tools: 3 },
  ];

  it("shows the per-extension grants by name", () => {
    render(
      <PermissionsPanel
        rows={rows}
        globalOn={false}
        busyKey={null}
        onToggleServer={vi.fn()}
        onSetGlobal={vi.fn()}
      />,
    );
    expect(screen.getByTestId("perm-fetch").textContent).toContain("Asks first");
    expect(screen.getByTestId("perm-brave").textContent).toContain("Allowed");
    // THE BUG: one granted extension must not make the blanket switch read on.
    expect(screen.getByTestId("perm-global").textContent).toBe("Off");
  });

  it("says the global is what makes an extension allowed, when it is", () => {
    render(
      <PermissionsPanel
        rows={rows}
        globalOn
        busyKey={null}
        onToggleServer={vi.fn()}
        onSetGlobal={vi.fn()}
      />,
    );
    expect(screen.getByTestId("perm-fetch").textContent).toContain("(global)");
    expect(screen.getByTestId("perm-global").textContent).toBe("On");
  });

  it("keeps the blast-radius essay OFF the page until it is being agreed to", () => {
    render(
      <PermissionsPanel
        rows={rows}
        globalOn={false}
        busyKey={null}
        onToggleServer={vi.fn()}
        onSetGlobal={vi.fn()}
      />,
    );
    // "Do not put the blast-radius essay inline."
    const panel = screen.getByTestId("permissions-panel");
    expect(panel.textContent).not.toMatch(/blanket|every one you connect later/);
    expect(panel.textContent).toContain("New extensions ask before running");
  });

  it("turning the global ON needs a confirm; turning it OFF does not", () => {
    // "Label it like a dangerous setting… with a confirm dialog, not a casual
    // checkbox." Narrowing a permission is never the dangerous direction.
    const onSetGlobal = vi.fn();
    render(
      <PermissionsPanel
        rows={rows}
        globalOn={false}
        busyKey={null}
        onToggleServer={vi.fn()}
        onSetGlobal={onSetGlobal}
      />,
    );
    fireEvent.click(screen.getByTestId("perm-global"));
    expect(onSetGlobal).not.toHaveBeenCalled();
    const dialog = screen.getByTestId("perm-global-confirm");
    expect(dialog.textContent).toMatch(/every one you connect later/);
    fireEvent.click(screen.getByTestId("perm-global-confirm-yes"));
    expect(onSetGlobal).toHaveBeenCalledWith(true);

    // A FRESH RENDER, not a rerender after cleanup(): cleanup() unmounts the
    // tree the rerender handle belongs to, so rerendering into it puts nothing
    // on screen and the query below would pass for the wrong reason.
    onSetGlobal.mockClear();
    cleanup();
    render(
      <PermissionsPanel
        rows={rows}
        globalOn
        busyKey={null}
        onToggleServer={vi.fn()}
        onSetGlobal={onSetGlobal}
      />,
    );
    fireEvent.click(screen.getByTestId("perm-global"));
    expect(onSetGlobal).toHaveBeenCalledWith(false);
    expect(screen.queryByTestId("perm-global-confirm")).toBeNull();
  });

  it("a per-extension switch cannot be flipped while the global forces it", () => {
    render(
      <PermissionsPanel
        rows={[{ name: "fetch", autoApprove: false, tools: 1 }]}
        globalOn
        busyKey={null}
        onToggleServer={vi.fn()}
        onSetGlobal={vi.fn()}
      />,
    );
    expect((screen.getByTestId("perm-fetch") as HTMLButtonElement).disabled).toBe(true);
  });
});
