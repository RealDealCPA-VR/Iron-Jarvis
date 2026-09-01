import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

/**
 * Build says what each pane's agent is doing (v1.217.0).
 *
 * Adapted from herdr — a terminal multiplexer for coding agents whose framing
 * is "never hunt for the stuck one" — and it keeps the two rules that make the
 * feature trustworthy rather than merely present:
 *
 *   `blocked`  an approval or question is on screen; it is waiting on YOU.
 *   `unknown`  we cannot classify it, and that must never read as finished.
 *
 * The second is already this app's law elsewhere: the roster's liveness note
 * says a missing signal "is NOT 'free' — it is 'no claim'". These tests exist
 * mostly to keep `unknown` from quietly acquiring a reassuring badge.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  PaneStateChip,
  PaneStateSummary,
  resolveState,
} from "@/components/terminal/PaneState";

afterEach(cleanup);

describe("resolveState — the idle/done split lives on the client", () => {
  it("keeps a settled pane the user has seen as 'ready'", () => {
    expect(resolveState("idle", false)).toBe("idle");
  });

  it("calls it 'finished' when the pane settled unwatched", () => {
    // herdr's distinction: the same underlying ready state reached while you
    // were looking elsewhere is a different thing to be told. The daemon
    // cannot know it — seen-ness is a fact about this browser — so the
    // downgrade happens here, reusing the page's existing unseen tracking.
    expect(resolveState("idle", true)).toBe("done");
  });

  it("never invents a state for a pane it has no answer about", () => {
    expect(resolveState(undefined, false)).toBe("unknown");
    expect(resolveState(null, true)).toBe("unknown");
    // …and unseen-ness does not upgrade an unknown into a finish.
    expect(resolveState("unknown", true)).toBe("unknown");
  });

  it("leaves working and blocked alone whether or not they were seen", () => {
    expect(resolveState("working", true)).toBe("working");
    expect(resolveState("blocked", true)).toBe("blocked");
  });
});

describe("PaneStateChip", () => {
  it("renders NOTHING for unknown", () => {
    // A chip saying "unknown" would sit on every plain shell as noise, and on
    // a pane we genuinely cannot read it would look like an answer.
    const { container } = render(<PaneStateChip state="unknown" />);
    expect(container.textContent).toBe("");
  });

  it("says the state in words, not only in colour", () => {
    render(<PaneStateChip state="blocked" />);
    expect(screen.getByTestId("pane-state-blocked").textContent).toContain("needs you");
    cleanup();
    render(<PaneStateChip state="done" />);
    expect(screen.getByTestId("pane-state-done").textContent).toContain("finished");
    cleanup();
    render(<PaneStateChip state="working" />);
    expect(screen.getByTestId("pane-state-working").textContent).toContain("working");
    cleanup();
    render(<PaneStateChip state="idle" />);
    expect(screen.getByTestId("pane-state-idle").textContent).toContain("ready");
  });

  it("carries the evidence line in its title, not just a verdict", () => {
    render(
      <PaneStateChip state="blocked" cli="claude" line="Edit src/app.py? 1. Yes" />,
    );
    const title = screen.getByTestId("pane-state-blocked").getAttribute("title") ?? "";
    expect(title).toContain("claude");
    expect(title).toContain("Edit src/app.py?");
  });
});

describe("PaneStateSummary — never hunt for the stuck one", () => {
  const panes = [
    { id: "t1", name: "builder", state: "blocked" as const },
    { id: "t2", name: null, state: "working" as const },
    { id: "t3", name: "tester", state: "done" as const },
  ];

  it("leads with the panes that need a human, and each one is a button", () => {
    const onFocus = vi.fn();
    render(<PaneStateSummary panes={panes} onFocus={onFocus} />);
    const strip = screen.getByTestId("pane-summary");
    expect(strip.textContent).toContain("1 pane needs you");
    fireEvent.click(screen.getByTestId("focus-blocked-t1"));
    expect(onFocus).toHaveBeenCalledWith("t1");
  });

  it("counts the quieter states without shouting them", () => {
    render(<PaneStateSummary panes={panes} onFocus={vi.fn()} />);
    const strip = screen.getByTestId("pane-summary");
    expect(strip.textContent).toContain("1 working");
    expect(strip.textContent).toContain("1 finished");
  });

  it("renders nothing when there is nothing to say", () => {
    // "0 blocked" is not a status, and a strip that is always present becomes
    // furniture the user stops reading.
    const { container } = render(
      <PaneStateSummary
        panes={[{ id: "t1", state: "idle" }, { id: "t2", state: "unknown" }]}
        onFocus={vi.fn()}
      />,
    );
    expect(container.textContent).toBe("");
  });

  it("pluralises the thing the user is being interrupted for", () => {
    render(
      <PaneStateSummary
        panes={[
          { id: "a", state: "blocked" },
          { id: "b", state: "blocked" },
        ]}
        onFocus={vi.fn()}
      />,
    );
    expect(screen.getByTestId("pane-summary").textContent).toContain("2 panes need you");
  });
});


/* ---- source pins (jsdom cannot run xterm — the house idiom) ------------------ */

describe("the pane identity has a way IN (source-pinned)", () => {
  const pane = readFileSync(
    join(process.cwd(), "components", "terminal", "TerminalPane.tsx"),
    "utf8",
  );
  const page = readFileSync(
    join(process.cwd(), "app", "terminals", "page.tsx"),
    "utf8",
  );

  it("names a pane from the header, and tells the daemon", () => {
    // Agents address panes by name and the summary strip lists them by name,
    // so a name only an API caller can set is not a shipped feature. The
    // rename lives in the header itself — the name IS the header.
    // Anchored on the header's conditional drag class (v1.218.0 made the
    // handle conditional, which moved the old literal), and closed at the
    // model select — a window that spans only the header row.
    const header = pane.slice(
      pane.indexOf('draggable ? "ij-term-drag cursor-move"'),
      pane.indexOf("Per-pane AI model"),
    );
    expect(header).toContain('data-testid="pane-name"');
    expect(header).toContain("setRenaming(true)");
    expect(pane).toContain("patch(`/terminals/${info.id}`, { name: next })");
  });

  it("abandons a rename on Escape rather than committing it", () => {
    // A field you cannot back out of is one people stop clicking.
    expect(pane).toContain('if (e.key === "Escape")');
    expect(pane).toContain("setDraftName(paneName || \"\");");
  });

  it("reports the launched CLI, so the classifier stops guessing", () => {
    // `launchCli` types the command into an ALREADY RUNNING shell, so the
    // daemon never learns what started. Without this the classifier's
    // "the catalog knows what it started" path is unreachable and every
    // launched CLI is sniffed out of the scrollback instead.
    const launch = pane.slice(pane.indexOf("function launchCli"), pane.indexOf("Pending screen snippets"));
    expect(launch).toContain("setPaneCli(cli.id)");
    expect(launch).toContain("patch(`/terminals/${info.id}`, { agent_cli: cli.id })");
  });

  it("echoes a rename locally instead of waiting on the 2.5s poll", () => {
    // The poll is the source of truth; without a local echo the header snaps
    // back to the shell name for up to 2.5s and reads as a failed save.
    expect(page).toContain("paneOverrides[t.id]?.name ?? act?.name");
    expect(page).toContain("paneOverrides[t.id]?.cli ?? act?.agent_cli");
    // …and the strip the user scans must agree with the pane it points at.
    expect(page).toContain("name: paneOverrides[t.id]?.name ?? a?.name ?? null,");
  });
});
