/**
 * v1.232.0 Wave 6, task 6D (the Badge capitalisation nit carried out of
 * Wave 1) — a chip that carries a sentence keeps its case.
 *
 * `Badge` applies CSS `capitalize` so a status word ("completed") reads
 * "Completed"; the Wave 1 amber chips carry sentences ("Completed · needs
 * you", "Waiting for you · shell") and rendered "Completed · Needs You" /
 * "Waiting For You · Shell". `Badge` now takes `keepCase`, which skips the
 * class; the outcome and waiting chips (SessionStatusBadge — the sessions
 * list, the session header, ProjectTasks — and the kanban card's own copy)
 * pass it. Every other Badge is unchanged, which the default case pins.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { Badge } from "@/components/ui";
import { SessionStatusBadge } from "@/components/sessions/SessionStatusBadge";

afterEach(() => cleanup());

function chipClass(text: RegExp | string): string {
  return screen.getByText(text).className;
}

describe("Badge keepCase", () => {
  it("a plain Badge still capitalises its status word", () => {
    render(<Badge value="completed" />);
    expect(chipClass("completed")).toMatch(/\bcapitalize\b/);
  });

  it("keepCase drops the capitalize class and nothing else", () => {
    render(<Badge value="Completed · needs you" tone="amber" keepCase />);
    const cls = chipClass("Completed · needs you");
    expect(cls).not.toMatch(/\bcapitalize\b/);
    expect(cls).toMatch(/rounded-full/);
  });

  it("the outcome chip keeps its case", () => {
    render(<SessionStatusBadge session={{ status: "completed", outcome: "needs_you" }} />);
    expect(chipClass("Completed · needs you")).not.toMatch(/\bcapitalize\b/);
  });

  it("the waiting chip keeps its case", () => {
    render(
      <SessionStatusBadge
        session={{
          status: "active",
          waiting_on: { approval_id: "apr_1", tool: "shell" },
        }}
      />,
    );
    expect(chipClass("Waiting for you · shell")).not.toMatch(/\bcapitalize\b/);
  });

  it("a plain status through SessionStatusBadge is still capitalised", () => {
    render(<SessionStatusBadge session={{ status: "completed", outcome: "completed" }} />);
    expect(chipClass("completed")).toMatch(/\bcapitalize\b/);
  });
});
