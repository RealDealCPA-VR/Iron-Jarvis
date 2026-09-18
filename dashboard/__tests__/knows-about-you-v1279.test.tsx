/**
 * v1.279.0 — the Memory page opens on "What Jarvis knows about you".
 *
 * The page used to open on a search box over a store that is empty on most
 * installs. The card reads `/memory/overview` and says what is actually kept
 * — the profile, the preferences read into every conversation, what can be
 * searched — and, with nothing yet, how to give Jarvis something in one
 * sentence typed in chat. Task reflections are named as notes about past jobs,
 * because they are no longer read into conversations.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  overview: null as unknown,
  fail: false,
  paths: [] as string[],
}));

vi.mock("@/lib/api", () => ({
  get: async (path: string) => {
    H.paths.push(path);
    if (H.fail) throw new Error("older daemon");
    return H.overview;
  },
  post: async () => ({}),
}));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { KnowsAboutYou, countsLine, sourceWord, type MemoryOverview } from "@/components/memory/KnowsAboutYou";

const EMPTY: MemoryOverview = {
  profile: { filled: false, enabled: true, about_line: "", tone: "", writing_style: "" },
  preferences: [],
  lessons: { total: 0, reflections: 0, by_source: {} },
  bases: [{ name: "brain", kind: "markdown", notes: 0 }],
  working: { session: 0, project: 0, user: 0, org: 0 },
  history: { docs: 0, available: true },
  empty: true,
};

const FULL: MemoryOverview = {
  profile: { filled: true, enabled: true, about_line: "Goes by VR, a CPA in Florida.", tone: "neutral", writing_style: "narrative" },
  preferences: [
    { id: "l1", text: "Prefers short answers with numbered steps", source: "preference", weight: 5, created_at: "2026-09-18" },
    { id: "l2", text: "Wants research written in his own voice", source: "feedback", weight: 3, created_at: "2026-09-17" },
  ],
  lessons: { total: 25, reflections: 23, by_source: { preference: 1, feedback: 1, reflection: 23 } },
  bases: [
    { name: "brain", kind: "markdown", notes: 4 },
    { name: "hermes-brain", kind: "mcp", notes: null },
  ],
  working: { session: 0, project: 0, user: 2, org: 0 },
  history: { docs: 113, available: true },
  empty: false,
};

beforeEach(() => {
  H.overview = EMPTY;
  H.fail = false;
  H.paths.length = 0;
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("What Jarvis knows about you (v1.279.0)", () => {
  it("with nothing kept, says so and shows the one-sentence on-ramp and the profile door", async () => {
    render(<KnowsAboutYou />);
    const card = await screen.findByTestId("knows-about-you");
    expect(H.paths).toContain("/memory/overview");
    expect(screen.getByTestId("knows-onramp").textContent).toMatch(/From now on, keep answers short/);
    expect(card.textContent).toMatch(/No profile yet/);
    expect(screen.getByRole("link", { name: /Tell Jarvis who you are/ }).getAttribute("href")).toBe("/you");
    expect(screen.queryByTestId("knows-preferences")).toBeNull();
  });

  it("with a profile and preferences, lists them with where each came from, and names reflections as job notes", async () => {
    H.overview = FULL;
    render(<KnowsAboutYou />);
    const card = await screen.findByTestId("knows-about-you");
    expect(card.textContent).toContain("Goes by VR, a CPA in Florida.");
    expect(card.textContent).toContain("neutral, narrative");
    const items = screen.getByTestId("knows-preferences").querySelectorAll("li");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("Prefers short answers with numbered steps");
    expect(items[0].textContent).toContain("you said so");
    expect(items[1].textContent).toContain("from your feedback");
    expect(screen.queryByTestId("knows-onramp")).toBeNull();
    expect(card.textContent).toContain("4 notes");
    expect(card.textContent).toContain("1 remote base");
    expect(card.textContent).toContain("2 working memories");
    expect(card.textContent).toContain("113 past conversations searchable");
    expect(card.textContent).toContain("23 task reflections (notes about past jobs — not read into conversations)");
  });

  it("says nothing at all when the daemon cannot answer", async () => {
    H.fail = true;
    const { container } = render(<KnowsAboutYou />);
    await waitFor(() => expect(H.paths).toContain("/memory/overview"));
    await new Promise((r) => setTimeout(r, 10));
    expect(container.textContent).toBe("");
    expect(screen.queryByTestId("knows-about-you")).toBeNull();
  });

  it("a switched-off profile is said out loud", async () => {
    H.overview = { ...FULL, profile: { ...FULL.profile, enabled: false } };
    render(<KnowsAboutYou />);
    const card = await screen.findByTestId("knows-about-you");
    expect(card.textContent).toContain("profile is switched off");
  });
});

describe("the words", () => {
  it("names each lesson source the way the user would", () => {
    expect(sourceWord("preference")).toBe("you said so");
    expect(sourceWord("feedback")).toBe("from your feedback");
    expect(sourceWord("distilled")).toBe("learned over time");
    expect(sourceWord("user")).toBe("you wrote it");
    expect(sourceWord("")).toBe("lesson");
  });

  it("counts only what is there, singular and plural", () => {
    expect(countsLine(EMPTY)).toBe("");
    expect(
      countsLine({
        ...EMPTY,
        bases: [{ name: "brain", kind: "markdown", notes: 1 }],
        lessons: { total: 1, reflections: 1, by_source: { reflection: 1 } },
        history: { docs: 1, available: true },
      }),
    ).toBe("1 note · 1 past conversation searchable · 1 task reflection (notes about past jobs — not read into conversations)");
    // An unavailable history index is not counted, however many docs it claims.
    expect(countsLine({ ...EMPTY, history: { docs: 50, available: false } })).toBe("");
  });
});
