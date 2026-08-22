/**
 * DOORS under a chat reply (v1.199.0).
 *
 * What these tests are really guarding:
 *   - the strip renders EXACTLY what the daemon says — the doors are server
 *     truth (executed-ok tools, deduped, capped at 4 SERVER-side), so the
 *     client must not slice, reorder, or derive its own. The cap is therefore
 *     deliberately NOT tested here: handing the strip 6 doors must render 6
 *     pills, because a client that "helpfully" re-caps would silently mask a
 *     daemon regression.
 *   - a message with no doors (every pre-v1.199.0 message) renders literally
 *     NOTHING — silence, not a fallback.
 *   - a click tallies into `ironjarvis.doors.usage`, a key SEPARATE from the
 *     nav's `ironjarvis.overview.usage`, so the emergent-surface metric can
 *     tell a door-opened subsystem from a nav-opened one.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DoorsStrip } from "@/components/chat/DoorsStrip";

// Next's Link needs a router context in a real app; the established test
// idiom is a plain anchor that keeps href + onClick behaviour.
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const DOOR_KEY = "ironjarvis.doors.usage";
const NAV_KEY = "ironjarvis.overview.usage";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
});

describe("DoorsStrip — rendering", () => {
  it("renders a pill link per door with the daemon's href and label", () => {
    render(
      <DoorsStrip
        doors={[
          { href: "/workflows", label: "Workflows" },
          { href: "/memory", label: "Memory" },
        ]}
      />,
    );
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAttribute("href", "/workflows");
    expect(links[0]).toHaveTextContent("Workflows");
    expect(links[1]).toHaveAttribute("href", "/memory");
    expect(links[1]).toHaveTextContent("Memory");
  });

  it("renders EXACTLY what it is given — no client-side cap or reorder", () => {
    // The 4-door cap is SERVER-side. Six doors in must be six pills out, in
    // the given order — a strip that quietly re-capped would hide a daemon
    // regression behind a client courtesy.
    const six = [
      { href: "/workflows", label: "Workflows" },
      { href: "/memory", label: "Memory" },
      { href: "/documents", label: "Documents" },
      { href: "/creative", label: "Creative" },
      { href: "/terminals", label: "Terminals" },
      { href: "/agents", label: "Agents" },
    ];
    render(<DoorsStrip doors={six} />);
    const hrefs = screen
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(six.map((d) => d.href));
  });

  it("renders literally nothing for an absent, null, or empty doors field", () => {
    // Every message persisted before v1.199.0 lands here — silence, never a
    // fallback row.
    const cases: ({ href: string; label: string }[] | null | undefined)[] = [
      undefined,
      null,
      [],
    ];
    for (const doors of cases) {
      const { container, unmount } = render(<DoorsStrip doors={doors} />);
      expect(container.firstChild).toBeNull();
      unmount();
    }
  });

  it("skips a degenerate entry without an href instead of a dead pill", () => {
    render(
      <DoorsStrip
        doors={[
          { href: "", label: "nowhere" },
          { href: "/memory", label: "" }, // blank label falls back to href
        ]}
      />,
    );
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/memory");
    expect(links[0]).toHaveTextContent("/memory");
  });
});

describe("DoorsStrip — the local door tally", () => {
  it("records a click into ironjarvis.doors.usage, per href", () => {
    render(
      <DoorsStrip
        doors={[
          { href: "/workflows", label: "Workflows" },
          { href: "/memory", label: "Memory" },
        ]}
      />,
    );
    fireEvent.click(screen.getByText("Workflows"));
    fireEvent.click(screen.getByText("Workflows"));
    fireEvent.click(screen.getByText("Memory"));
    expect(JSON.parse(window.localStorage.getItem(DOOR_KEY) ?? "{}")).toEqual({
      "/workflows": 2,
      "/memory": 1,
    });
  });

  it("keeps the door tally OUT of the nav's usage key", () => {
    // The whole point of the second key: a subsystem reached through a door
    // must be distinguishable from one reached through the sidebar.
    render(<DoorsStrip doors={[{ href: "/workflows", label: "Workflows" }]} />);
    fireEvent.click(screen.getByText("Workflows"));
    expect(window.localStorage.getItem(NAV_KEY)).toBeNull();
    expect(
      JSON.parse(window.localStorage.getItem(DOOR_KEY) ?? "{}"),
    ).toEqual({ "/workflows": 1 });
  });
});

describe("chat page — lane parity at the workflow-DRAFT exit (source shape)", () => {
  // A full render test of the chat page's POST fallback lane needs the whole
  // api/stream/events harness — too heavy to earn its keep here. This pins
  // the defect the cheap way instead: BOTH draft exits must spread their
  // full receipt object onto the assistant message. The POST lane once
  // spread only toolsUsed/viaProvider/workflowRun, two lines under its own
  // MIRROR NOTE, so a turn that earned a door/route/document in round 0 and
  // crystallized a draft in round 1 kept them on the stream lane and
  // silently lost them on the fallback lane. Deleting either spread turns
  // this red (mutation-checked by construction: the strings below are the
  // spreads themselves).
  it("both workflow-draft exits carry the full receipt onto the message", () => {
    const src = readFileSync(
      join(__dirname, "..", "app", "chat", "page.tsx"),
      "utf8",
    );
    // Stream lane: the draft-exit message spreads `...receipt`.
    const streamExit = src.match(
      /if \(workflowDraft\) \{[\s\S]*?setMessages\(done\);/,
    );
    expect(streamExit, "stream lane draft exit not found").toBeTruthy();
    expect(streamExit![0]).toContain("...receipt,");
    // POST fallback lane: the draft-exit message spreads `...receiptPost`.
    const postExit = src.match(
      /if \(res\.workflow_draft\) \{[\s\S]*?setMessages\(done\);/,
    );
    expect(postExit, "POST lane draft exit not found").toBeTruthy();
    expect(postExit![0]).toContain("...receiptPost,");
  });
});
