import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

/**
 * THE THREAD RAIL SHOWS THE AGENTS (v1.214.0).
 *
 * Reported: the left pane should show "the image of the related agent or
 * agents (layered as they are now)".
 *
 * The layering was already right — the rail has drawn an overlapping strip
 * since the round-table shipped. What it layered was `AgentAvatar`: a coloured
 * circle with the first LETTER of the name in it. Every other surface in the
 * app draws the agent — the roster, the kanban board, the transcript, the
 * @-mention popover — so the one list you scan to CHOOSE a conversation was
 * the only place an agent did not look like itself.
 *
 * `FaceStack` is that strip drawing `AgentFace`, which means the whole
 * precedence comes with it: a stored portrait beats a chosen face beats the
 * name-derived one. This file pins the two halves that are easy to lose — the
 * portrait actually being used, and the overflow count staying honest.
 */

import { FaceStack } from "@/components/agents/FaceStack";
import type { Participant } from "@/components/agents/identity";

const p = (name: string, source: Participant["source"] = "dynamic"): Participant => ({
  key: `${source}:${name}`,
  source,
  name,
  role: "",
});

afterEach(cleanup);

describe("FaceStack", () => {
  it("draws one face per participant", () => {
    render(<FaceStack participants={[p("analyst"), p("builder", "builtin")]} />);
    expect(screen.getAllByTestId("agent-face")).toHaveLength(2);
    expect(screen.getByTitle("analyst")).toBeTruthy();
    expect(screen.getByTitle("builder")).toBeTruthy();
  });

  it("uses the STORED PORTRAIT where the roster has one", () => {
    // The lookup is passed in, keyed by participant key, because the page
    // already holds the roster it comes from — a second fetch in here would be
    // one more list that can disagree with the rail beside it.
    const avatars = new Map<string, string | null>([
      ["dynamic:analyst", "http://test/agents/analyst/avatar?v=1&token=tok"],
      ["builtin:builder", null],
    ]);
    render(
      <FaceStack
        participants={[p("analyst"), p("builder", "builtin")]}
        avatarByKey={avatars}
      />,
    );
    const img = screen.getByAltText("analyst") as HTMLImageElement;
    expect(img.src).toContain("/agents/analyst/avatar");
    // The one with no portrait keeps the DRAWN face — never a broken image,
    // and never an <img> pointed at a route the daemon would 404.
    expect(screen.queryByAltText("builder")).toBeNull();
    expect(screen.getByRole("img", { name: "builder" }).tagName.toLowerCase()).toBe(
      "svg",
    );
  });

  it("names the role on the face when the panel gave it one", () => {
    render(<FaceStack participants={[{ ...p("analyst"), role: "critic" }]} />);
    expect(screen.getByTitle("analyst — critic")).toBeTruthy();
  });

  it("counts the overflow rather than growing the row", () => {
    // A rail row is one line; a nine-agent panel must not wrap it.
    const many = ["a", "b", "c", "d", "e", "f", "g"].map((n) => p(n));
    const { container } = render(<FaceStack participants={many} max={5} />);
    expect(screen.getAllByTestId("agent-face")).toHaveLength(5);
    expect(within(container).getByText("+2")).toBeTruthy();
  });

  it("shows no overflow marker when everyone fits", () => {
    const { container } = render(
      <FaceStack participants={[p("a"), p("b")]} max={5} />,
    );
    expect(within(container).queryByText(/^\+/)).toBeNull();
  });

  it("renders nothing at all for an empty panel", () => {
    const { container } = render(<FaceStack participants={[]} />);
    expect(container.querySelectorAll("[data-testid='agent-face']")).toHaveLength(0);
  });
});
