/**
 * v1.314.0 — UX wave 2 shared contracts (coordinator):
 *  - <Empty> takes a title, an action that is a link OR a press, a quiet
 *    secondary link and examples; an `{label, href}` caller is unchanged.
 *  - `providerDisplay` never shows the word "mock" to a user.
 *  - `originLabel` / <OriginChip> read in plain words and keep the raw origin.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Empty, MockChip } from "@/components/ui";
import OriginChip, { originLabel } from "@/components/sessions/OriginChip";
import { providerDisplay } from "@/lib/onboarding";

describe("Empty — the three-part empty state", () => {
  it("an href action is still a link (old callers unchanged)", () => {
    render(<Empty action={{ label: "Start a session", href: "/sessions" }}>Nothing yet.</Empty>);
    const a = screen.getByRole("link", { name: /Start a session/ });
    expect(a.getAttribute("href")).toBe("/sessions");
  });

  it("an onClick action is a real button that runs the page's own handler", () => {
    const open = vi.fn();
    render(
      <Empty title="Rules that start work" action={{ label: "Add your first reflex", onClick: open }}>
        A reflex starts a task when something happens.
      </Empty>,
    );
    expect(screen.getByText("Rules that start work")).toBeInTheDocument();
    const b = screen.getByRole("button", { name: /Add your first reflex/ });
    expect(b.getAttribute("type")).toBe("button");
    fireEvent.click(b);
    expect(open).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("examples and a secondary link render when given, and not otherwise", () => {
    const { rerender } = render(
      <Empty examples={["When an email arrives, summarise it"]} secondary={{ label: "Use Reflexes", href: "/reflex" }}>
        x
      </Empty>,
    );
    expect(screen.getByTestId("empty-examples")).toHaveTextContent("When an email arrives, summarise it");
    expect(screen.getByRole("link", { name: "Use Reflexes" }).getAttribute("href")).toBe("/reflex");
    rerender(<Empty>x</Empty>);
    expect(screen.queryByTestId("empty-examples")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });
});

describe("providerDisplay — no 'mock' in front of a user", () => {
  it("names the demo model honestly and passes known ids through friendly names", () => {
    expect(providerDisplay("mock")).toBe("Demo model (scripted)");
    expect(providerDisplay("mock")).not.toMatch(/mock/i);
    expect(providerDisplay("claude-cli")).toBe("Claude Code");
    expect(providerDisplay("auto")).toBe("Auto");
    expect(providerDisplay("some-new-thing")).toBe("some-new-thing");
    expect(providerDisplay("")).toBe("");
  });

  it("MockChip's visible words are 'demo model'", () => {
    render(<MockChip />);
    expect(screen.getByText(/demo model/)).toBeInTheDocument();
    expect(screen.queryByText(/offline mock/)).toBeNull(); // only in the title
  });
});

describe("originLabel / OriginChip — plain words, raw kept", () => {
  it.each([
    ["job:mission", "Mission"],
    ["job:mission-member", "Mission teammate"],
    ["schedule:nightly-brief", "Schedule · nightly-brief"],
    ["comm:email", "Message · email"],
    ["reflex:on-mail", "Reflex · on-mail"],
    ["assignment:asg_123abc", "Queued job"],
    ["project:project_c4078b", "Project task"],
    ["self_dev", "Self-development"],
    ["rerun", "Re-run"],
  ])("%s → %s", (raw, words) => {
    expect(originLabel(raw)).toBe(words);
  });

  it("an origin nobody has words for is shown as it is (no invented label)", () => {
    expect(originLabel("weird:thing")).toBe("weird:thing");
    expect(originLabel("")).toBe("");
  });

  it("the chip shows the words, keeps the raw origin in title + data-origin, and is not monospace", () => {
    render(<OriginChip origin="assignment:asg_123abc" />);
    const chip = screen.getByTestId("origin-chip");
    expect(chip.textContent).toBe("Queued job");
    expect(chip.getAttribute("data-origin")).toBe("assignment:asg_123abc");
    expect(chip.getAttribute("title")).toBe("Started by: assignment:asg_123abc");
    expect(chip.className.split(/\s+/)).not.toContain("font-mono");
  });
});
