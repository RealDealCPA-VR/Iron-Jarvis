/**
 * Harness gaps REVIEW — Everything › Status's Build star while a coding
 * session answers.
 *
 * Fix 1 lights the Build star for a coding session's running reply (it adds
 * to "in use", never to the open terminals). With no terminal open — the
 * coding sessions view needs none, so that is the usual case — the star's card
 * read "not set up yet · 1 use this week · 1 in use now": in use and "not set
 * up" in one line. A star that is in use (or was used) is never "not set up
 * yet".
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {} }),
  usePathname: () => "/everything",
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));

import { describe as describeNode } from "@/components/overview/SystemMap";

const node = (over: Record<string, unknown>) =>
  ({ key: "build", label: "Build", href: "/terminals", setup: 0, used: 0, live: 0, unit: "terminal", detail: "", attention: null, ...over }) as never;

describe("a star in use is never 'not set up yet'", () => {
  it("the Build star while a coding session answers with no terminal open", () => {
    const words = describeNode(node({ used: 1, live: 1 }));
    expect(words).not.toMatch(/not set up/);
    expect(words).toBe("1 use this week · 1 in use now");
  });

  it("CONTROL: a star with nothing set up, used or in use still says so", () => {
    expect(describeNode(node({}))).toBe("not set up yet");
    expect(describeNode(node({ setup: 2, live: 1 }))).toBe("2 terminals · 1 in use now");
  });
});
