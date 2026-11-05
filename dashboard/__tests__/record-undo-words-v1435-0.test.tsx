/**
 * Wave 3 group G review — a record card's Undo never claims more than it did.
 *
 * The Undo of a made project ARCHIVES it when work was filed in it (it is not
 * gone), and an Undo the daemon REFUSES (the user changed the record again
 * since) says why — it used to read "is back to how it was" and "Couldn't undo
 * that — try again" (a refusal that will repeat). The daemon's own words for
 * these come from settings/records_projects.py (tests/
 * test_records_projects_review_v14350.py pins them).
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    gets: {} as Record<string, unknown>,
    posts: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.gets[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string) => {
    const r = H.posts[path];
    if (r instanceof Error) throw r;
    return r ?? {};
  },
  put: async () => ({}),
  del: async () => ({}),
}));

import { ConfigCards } from "@/components/chat/ConfigCards";
import { decodeConfigCards, undoAnswerNote, undoRefusalWords, type ConfigCard } from "@/lib/configCards";

afterEach(() => {
  cleanup();
  for (const k of Object.keys(H.gets)) delete H.gets[k];
  for (const k of Object.keys(H.posts)) delete H.posts[k];
});

const made: ConfigCard = {
  kind: "change",
  title: "Project created",
  key: "project.p1",
  label: "Project “Brown”",
  old: null,
  new: "no folder, focused",
  change_id: "rec_1",
};

describe("a record card's Undo says what it really did", () => {
  it("a made project the Undo ARCHIVED is never said to be back to how it was", async () => {
    H.gets["/config/changes/rec_1"] = { action_id: "tool_1", undone: false };
    H.posts["/undo/tool_1"] = {
      output: "undo: archived the project 'Brown' (work was already filed in it, so it was kept)",
    };
    const onSettle = vi.fn();
    const { rerender } = render(<ConfigCards cards={[made]} onSettle={onSettle} />);
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(onSettle).toHaveBeenCalled());
    const settled = onSettle.mock.calls[0][1] as ConfigCard;
    rerender(<ConfigCards cards={[settled]} onSettle={onSettle} />);
    const line = screen.getByTestId("config-card-undone").textContent ?? "";
    expect(line).not.toContain("back to how it was");
    expect(line).toBe("Undone — Archived the project 'Brown' (work was already filed in it, so it was kept).");
    // ...and it survives the message being saved and read back.
    expect(decodeConfigCards([settled])[0]).toMatchObject({ undone: true, undone_note: expect.stringContaining("kept") });
  });

  it("an ordinary Undo keeps the card's own words", async () => {
    H.gets["/config/changes/rec_1"] = { action_id: "tool_1", undone: false };
    H.posts["/undo/tool_1"] = { output: "undo: removed the project 'Brown'" };
    const onSettle = vi.fn();
    const { rerender } = render(<ConfigCards cards={[made]} onSettle={onSettle} />);
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(onSettle).toHaveBeenCalled());
    rerender(<ConfigCards cards={[onSettle.mock.calls[0][1]]} onSettle={onSettle} />);
    expect(screen.getByTestId("config-card-undone").textContent).toBe("Undone — Project “Brown” is back to how it was.");
  });

  it("an Undo the daemon refuses says why, never 'try again'", async () => {
    H.gets["/config/changes/rec_1"] = { action_id: "tool_1", undone: false };
    H.posts["/undo/tool_1"] = new H.FakeApiError(
      "undo failed: The project “Brown” was changed again after this change (its name), so Undo would undo that too; change it on the Projects page instead.",
      409,
    );
    const onSettle = vi.fn();
    render(<ConfigCards cards={[made]} onSettle={onSettle} />);
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(
      "The project “Brown” was changed again after this change (its name), so Undo would undo that too; change it on the Projects page instead.",
    );
    expect(onSettle).not.toHaveBeenCalled();
  });

  it("any other failure keeps the card's own line", async () => {
    H.gets["/config/changes/rec_1"] = { action_id: "tool_1", undone: false };
    H.posts["/undo/tool_1"] = new H.FakeApiError("boom", 500);
    render(<ConfigCards cards={[made]} onSettle={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Couldn't undo that — try again, or use Activity.");
  });

  it("the two readers, on their own", () => {
    expect(undoAnswerNote("undo: the agent 'intake' was already back as it was")).toBe(
      "The agent 'intake' was already back as it was.",
    );
    expect(undoAnswerNote("undo: restored the schedule 'nightly'")).toBe("");
    expect(undoAnswerNote(undefined)).toBe("");
    expect(undoRefusalWords(new H.FakeApiError("undo failed: nope", 500))).toBe("");
    expect(undoRefusalWords({ status: 409, message: "action already undone" })).toBe("Action already undone.");
  });
});
