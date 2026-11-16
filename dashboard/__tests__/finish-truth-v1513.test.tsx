/**
 * v1.513.0 (second review, group 13 part A) — the finish check's work line
 * never says more than the check proved.
 *
 *  - D7: a review that reached nobody never reads "reviewed by": a reviewer
 *    that ended without a report is "review failed"; one whose answer did not
 *    count (low trust, a withheld report) is "review not counted", and the
 *    hover says why.
 *  - S3: a reply in a folder whose own settings turned the check off reads
 *    "Not checked: …" (never nothing, never "no tests found"), and the
 *    Session tab's instructions say the folder turned the check off.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({ gets: {} as Record<string, unknown> }));
vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) => (p in api.gets ? Promise.resolve(api.gets[p]) : Promise.reject(new Error(`unmocked ${p}`))),
  post: (p: string) => Promise.resolve(p === "/chat/changes" ? { changes: [] } : {}),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

import { replyLineParts } from "@/components/harness/HarnessMessageRow";
import { SessionRail } from "@/components/harness/HarnessSessionRail";
import { decodeFinish, decodeFinishReview, decodeSessionView, type BuildSessionView } from "@/lib/buildSessions";
import { decodeSessionInstructions } from "@/lib/sessionRail";
import { FINISH_OFF_WORDS, finishTitle, finishWarn, finishWords, reviewWords } from "@/lib/stepWords";

const LOW_WHY =
  "its answer was not counted: the reviewer read something that looked like an instruction, so its run was at low trust";

function view(messages: unknown[]): BuildSessionView {
  const v = decodeSessionView({ id: "s1", title: "t", folder: "C:\\work\\shop", folder_name: "shop",
    session_id: "build:s1", messages });
  if (!v) throw new Error("bad fixture");
  return v;
}

const QUESTION = { role: "user", content: "Set X to 2", at: "2026-11-16T10:00:00" };

beforeEach(() => {
  api.gets = { "/build/sessions/s1/programs": { programs: [], others: 0 } };
});
afterEach(cleanup);

describe("D7: 'reviewed by' only over a review that reached the reply", () => {
  it("a review whose answer did not count says so, and the hover says why", () => {
    const rv = decodeFinishReview({ state: "not_counted", member: "codex", model: "", verdict: "approved", why: LOW_WHY });
    expect(rv).not.toBeNull();
    expect(rv!.state).toBe("not_counted");
    expect(rv!.verdict).toBe("approved"); // decoded as sent; the words never use it
    expect(reviewWords(rv!)).toBe("review not counted");
    const rec = decodeFinish({ tests: "passed", command: "pytest", removed: 0, kept: 0, points: 0, unmet: 0,
      after: "", review: { state: "not_counted", member: "codex", model: "", verdict: "", why: LOW_WHY } });
    expect(finishWords(rec!)).toBe("Checked: tests passed, review not counted");
    expect(finishWords(rec!)).not.toContain("reviewed by");
    expect(finishTitle(rec!)).toContain(`Review: ${LOW_WHY}`);
  });

  it("a reviewer that ended without a report is a failed review", () => {
    const rec = decodeFinish({ tests: "none", command: "", removed: 0, kept: 0, points: 0, unmet: 0, after: "",
      review: { state: "failed", member: "codex", model: "", verdict: "", why: "it ended without a report" } });
    expect(finishWords(rec!)).toBe("Checked: no tests found, review failed");
    expect(finishTitle(rec!)).toContain("Review: it ended without a report");
  });

  it("an unknown state is still dropped", () => {
    expect(decodeFinishReview({ state: "lovely", member: "codex" })).toBeNull();
  });
});

describe("S3: a folder that turned the check off is said, never silent", () => {
  it("the work line says the reply was not checked, with the warn mark", () => {
    const rec = decodeFinish({ off: "folder" });
    expect(rec).not.toBeNull();
    expect(finishWords(rec!)).toBe(FINISH_OFF_WORDS);
    expect(FINISH_OFF_WORDS).toBe("Not checked: this folder's own settings turned the check off");
    expect(finishWarn(rec!)).toBe(true);
    expect(finishTitle(rec!)).toContain("did not check this reply's work");
    // Nothing else is claimed.
    expect(finishWords(rec!)).not.toMatch(/tests|reviewed/);
  });

  it("the reply's one line under it carries those words", () => {
    const v = view([QUESTION, { role: "assistant", content: "Done.", at: "2026-11-16T10:00:05", finish: { off: "folder" } }]);
    const parts = replyLineParts(v.messages[1]);
    const fin = parts.find((p) => p.key === "finish");
    expect(fin?.text).toBe(FINISH_OFF_WORDS);
    expect(fin?.warn).toBe(true);
  });

  it("the Session tab's instructions say the folder turned the check off", async () => {
    expect(decodeSessionInstructions({ text: "x", finish_off: "This folder turned the finish check off." }).finishOff)
      .toBe("This folder turned the finish check off.");
    expect(decodeSessionInstructions({ text: "x" }).finishOff).toBe("");
    api.gets["/build/sessions/s1/harness"] = {
      text: "the instructions", cut: false, note: "", layers: ["default"], rule_files: [], mode: "act",
      finish_off: "This folder turned the finish check off.",
    };
    const v = view([QUESTION]);
    render(<SessionRail view={v} messages={v.messages} modelWords="Sonnet 4.5" refreshKey={0} onOpenChanges={() => {}} />);
    expect(screen.queryByTestId("session-rail-instructions-finish-off")).toBeNull();
    fireEvent.click(screen.getByTestId("session-rail-instructions-show"));
    await waitFor(() =>
      expect(screen.getByTestId("session-rail-instructions-finish-off").textContent).toBe(
        "This folder turned the finish check off.",
      ),
    );
  });
});
