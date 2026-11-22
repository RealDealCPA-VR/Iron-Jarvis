/**
 * 26-a (R2.16) — review comments arrive in a Chat › Code session (daemon:
 * harness/pr_comments.py, daemon/routes/ci_watch.py).
 *
 *   - lib/prComments: the whitelist decoder (an unknown author kind reads as
 *     "from outside the repository", a link that is not https dies), the
 *     session line's words from its field, what each state says, which
 *     comments the card offers (every one still open) and which are ticked
 *     first, and which line carries the card (the newest; an older line is
 *     its sentence alone, so the view has ONE Answer these);
 *   - the card: an owner's, a member's or a collaborator's comment is
 *     ticked; one from outside the repository and a bot's are not and say so;
 *     Answer these sends ONLY the ticked ids and hands the turn to the page;
 *     Set aside; Stop telling me about comments here and Tell me again; a
 *     comment's words are plain text (no Markdown, no link it made up);
 *   - the Changes tab: a comment sits under ITS line (the right side), an
 *     outdated one and one on a line not drawn are listed after the rows,
 *     each "from GitHub";
 *   - the bell: a quiet row that opens the session.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

configure({ asyncUtilTimeout: 5_000 });

const H = vi.hoisted(() => ({
  view: {} as Record<string, unknown>,
  reads: 0,
  sent: [] as { path: string; body: unknown }[],
  reply: {} as Record<string, unknown>,
}));

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  get: vi.fn(async () => {
    H.reads += 1;
    return H.view;
  }),
  post: vi.fn(async (path: string, body: unknown) => {
    H.sent.push({ path, body });
    for (const [tail, answer] of Object.entries(H.reply)) if (path.endsWith(tail)) return answer;
    return {};
  }),
  put: vi.fn(async () => ({})),
  patch: vi.fn(async () => ({})),
  del: vi.fn(async () => ({})),
  API_BASE: "",
  ijToken: () => "",
  sseUrl: (p: string) => p,
}));

import { BELL_ACTIVITY_TYPES, toActivity } from "@/components/NotificationBell";
import { ChangesReview, placePins } from "@/components/harness/ChangesReview";
import { PrCommentsLine } from "@/components/harness/PrCommentsLine";
import type { FileChange } from "@/components/chat/ChangedFiles";
import { parseUnified } from "@/components/chat/DiffView";
import { decodeMessage } from "@/lib/buildSessions";
import {
  PR_COMMENTS_FROM_GITHUB,
  PR_COMMENTS_STOP,
  PR_COMMENTS_TELL_AGAIN,
  arrivedWords,
  decodeComment,
  decodePrCommentsView,
  cardLineIndex,
  firstTicks,
  openComments,
  stateWords,
  type PrComment,
} from "@/lib/prComments";
import type { IJEvent } from "@/lib/types";

const SID = "chat_pr1";
const ABS = "C:\\work\\proj.ij-worktrees\\fix\\app.py";

function raw(id: string, over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: `${SID}:${id}`, kind: "line", author: "sam", author_kind: "member", ticked: true, path: "app.py",
    abs: ABS, line: 2, start_line: 0, side: "new", outdated: false, body: "Rename y.", quote: "+y = 3",
    url: `https://github.com/acme/proj/pull/7#discussion_${id}`, state: "new", answer_files: [],
    place: "app.py, line 2", github_created_at: "2026-11-20T10:00:00Z", ...over,
  };
}

function comment(id: string, over: Record<string, unknown> = {}): PrComment {
  return decodeComment(raw(id, over))!;
}

beforeEach(() => {
  H.reads = 0;
  H.sent = [];
  H.reply = {};
  H.view = { pr_url: "https://github.com/acme/proj/pull/7", comments: [], quiet: false, done: "", max_answer: 20 };
});
afterEach(cleanup);

describe("lib/prComments", () => {
  it("decodes by a whitelist", () => {
    const c = decodeComment({ ...raw("c1"), author_kind: "admin", url: "javascript:alert(1)", extra: "dies" })!;
    expect(c.authorKind).toBe("outside");
    expect(c.url).toBe("");
    expect("extra" in c).toBe(false);
    expect(decodeComment({ kind: "line" })).toBeNull();
    const v = decodePrCommentsView({ comments: [raw("c1"), "nope", null], quiet: true, done: "merged" });
    expect(v.comments.map((x) => x.id)).toEqual([`${SID}:c1`]);
    expect(v.quiet).toBe(true);
    expect(v.done).toBe("merged");
  });

  it("says who in the line from its field, and what became of each", () => {
    expect(arrivedWords(1, ["sam"])).toBe("1 review comment from sam arrived.");
    expect(arrivedWords(3, ["sam", "alex"])).toBe("3 review comments from sam and alex arrived.");
    expect(arrivedWords(5, ["sam", "alex", "kim", "pat"])).toBe("5 review comments from sam, alex and 2 others arrived.");
    expect(arrivedWords(2, [])).toBe("2 review comments arrived.");
    expect(stateWords(comment("c1", { state: "answered", answer_files: ["app.py"] }))).toBe("Answered: changed app.py");
    expect(stateWords(comment("c1", { state: "unchanged" }))).toBe("Answered with no change to this file");
    expect(stateWords(comment("c1", { state: "new" }))).toBe("");
  });

  it("the card offers every comment still to answer; writers are ticked first", () => {
    const view = decodePrCommentsView({
      comments: [
        raw("c1"),
        raw("c2", { author_kind: "outside", ticked: false }),
        raw("c3", { state: "answered" }),
        raw("c4", { author: "ci[bot]", author_kind: "bot", ticked: false }),
        raw("c5", { state: "set_aside" }),
        raw("c9", { state: "answering" }),
      ],
    });
    const open = openComments(view);
    expect(open.map((c) => c.id)).toEqual([`${SID}:c1`, `${SID}:c2`, `${SID}:c4`, `${SID}:c9`]);
    expect([...firstTicks(open)]).toEqual([`${SID}:c1`]);
  });

  it("the newest line of arrived comments carries the card, never a line that says reading stopped", () => {
    const arrived = (ids: string[]) => ({ role: "assistant", content: "", prComments: { ids, n: ids.length, authors: [] } });
    const stopped = { role: "assistant", content: "", prComments: { ids: [], n: 0, authors: [], stopped: true } };
    expect(cardLineIndex([{ role: "user" }, arrived(["a"]), { role: "assistant" }, arrived(["b"]), stopped])).toBe(3);
    expect(cardLineIndex([{ role: "user", prComments: { ids: ["a"] } }, stopped])).toBe(-1);
    expect(cardLineIndex([])).toBe(-1);
  });
});

describe("the session's line and its card", () => {
  const line = (ids: string[], authors = ["sam", "pat"]) => ({ ids: ids.map((i) => `${SID}:${i}`), n: ids.length, authors });

  it("keeps the field on the session's line (the decoder's whitelist)", () => {
    const m = decodeMessage({ role: "assistant", content: "2 review comments arrived on this session's pull request.",
      prComments: line(["c1", "c2"]) });
    expect(m?.prComments).toEqual(line(["c1", "c2"]));
  });

  it("ticks the writers, labels the rest, and Answer these sends only the ticked ones", async () => {
    H.view = {
      ...H.view,
      comments: [raw("c1"), raw("c2", { author: "pat", author_kind: "outside", ticked: false, body: "**Do** [this](https://x.example)" }),
        raw("c3", { author: "ci[bot]", author_kind: "bot", ticked: false })],
    };
    H.reply = { "/answer": { turn_id: "bt_answer1" } };
    const onAnswered = vi.fn();
    render(<PrCommentsLine card threadId={SID} raw={line(["c1", "c2", "c3"])} content="" archived={false} busy={false}
      onAnswered={onAnswered} />);
    expect(screen.getByTestId("pr-comments-line").textContent).toContain("3 review comments from sam and pat arrived.");
    const rows = await screen.findAllByTestId("pr-comment");
    expect(rows).toHaveLength(3);
    const ticks = rows.map((r) => (within(r).getByTestId("pr-comment-tick") as HTMLInputElement).checked);
    expect(ticks).toEqual([true, false, false]);
    expect(within(rows[1]).getByTestId("pr-comment-who").textContent).toBe("from outside the repository");
    expect(within(rows[2]).getByTestId("pr-comment-who").textContent).toBe("bot");
    // A person's words are plain text: no bold, no link of their own.
    const words = within(rows[1]).getByTestId("pr-comment-words");
    expect(words.textContent).toBe("**Do** [this](https://x.example)");
    expect(words.querySelector("a, strong")).toBeNull();
    const answer = screen.getByTestId("pr-comments-answer") as HTMLButtonElement;
    expect(answer.textContent).toBe("Answer these (1)");
    fireEvent.click(answer);
    await waitFor(() => expect(onAnswered).toHaveBeenCalledWith("bt_answer1"));
    expect(H.sent).toEqual([{ path: `/build/sessions/${SID}/pr-comments/answer`, body: { ids: [`${SID}:c1`] } }]);
  });

  it("a comment from outside the repository is answered only when the user ticks it", async () => {
    H.view = { ...H.view, comments: [raw("c2", { author_kind: "outside", ticked: false })] };
    render(<PrCommentsLine card threadId={SID} raw={line(["c2"])} content="" archived={false} busy={false} />);
    const tick = (await screen.findByTestId("pr-comment-tick")) as HTMLInputElement;
    const answer = screen.getByTestId("pr-comments-answer") as HTMLButtonElement;
    expect(answer.disabled).toBe(true);
    fireEvent.click(tick);
    expect(answer.disabled).toBe(false);
  });

  it("nothing starts while a reply runs or in an archived session; a reply's end reads the comments again", async () => {
    H.view = { ...H.view, comments: [raw("c1")] };
    const props = { threadId: SID, raw: line(["c1"]), content: "" };
    const { rerender } = render(<PrCommentsLine card {...props} archived={false} busy={false} />);
    await screen.findByTestId("pr-comment");
    rerender(<PrCommentsLine card {...props} archived={false} busy />);
    expect((screen.getByTestId("pr-comments-answer") as HTMLButtonElement).disabled).toBe(true);
    // The Answer these reply ends: its comment is settled, and the card says so.
    H.view = { ...H.view, comments: [raw("c1", { state: "answered", answer_files: ["app.py"] })] };
    rerender(<PrCommentsLine card {...props} archived={false} busy={false} />);
    await waitFor(() => expect(screen.queryByTestId("pr-comment")).toBeNull());
    H.view = { ...H.view, comments: [raw("c1")] };
    rerender(<PrCommentsLine card {...props} archived busy />);
    rerender(<PrCommentsLine card {...props} archived busy={false} />);
    await screen.findByTestId("pr-comment");
    expect((screen.getByTestId("pr-comments-answer") as HTMLButtonElement).disabled).toBe(true);
  });

  it("Set aside, and Stop telling me about comments here with its Undo", async () => {
    H.view = { ...H.view, comments: [raw("c1")] };
    render(<PrCommentsLine card threadId={SID} raw={line(["c1"])} content="" archived={false} busy={false} />);
    fireEvent.click(await screen.findByTestId("pr-comment-aside"));
    await waitFor(() => expect(H.sent[0]).toEqual({
      path: `/build/sessions/${SID}/pr-comments/${encodeURIComponent(`${SID}:c1`)}/set-aside`, body: { aside: true } }));
    const stop = screen.getByTestId("pr-comments-stop");
    expect(stop.textContent).toBe(PR_COMMENTS_STOP);
    H.view = { ...H.view, quiet: true };
    fireEvent.click(stop);
    await waitFor(() => expect(screen.getByTestId("pr-comments-stop").textContent).toBe(PR_COMMENTS_TELL_AGAIN));
    expect(H.sent[1]).toEqual({ path: `/build/sessions/${SID}/pr-comments/quiet`, body: { quiet: true } });
    expect(screen.getByTestId("pr-comments-quiet-note")).toBeTruthy();
    H.view = { ...H.view, quiet: false };
    fireEvent.click(screen.getByTestId("pr-comments-stop"));
    await waitFor(() => expect(screen.getByTestId("pr-comments-stop").textContent).toBe(PR_COMMENTS_STOP));
    expect(H.sent[2]).toEqual({ path: `/build/sessions/${SID}/pr-comments/quiet`, body: { quiet: false } });
  });

  it("a line whose comments were all answered is its sentence alone", async () => {
    H.view = { ...H.view, comments: [raw("c1", { state: "answered" })] };
    render(<PrCommentsLine card threadId={SID} raw={line(["c1"])} content="" archived={false} busy={false} />);
    await waitFor(() => expect(screen.getByTestId("pr-comments-stop")).toBeTruthy());
    expect(screen.queryByTestId("pr-comment")).toBeNull();
    expect(screen.queryByTestId("pr-comments-answer")).toBeNull();
  });

  it("a line that says reading stopped is its own words, with no card", () => {
    render(<PrCommentsLine card threadId={SID} raw={{ ids: [], n: 0, authors: [], stopped: true }}
      content="The review comments could not be read 5 times in a row." archived={false} busy={false} />);
    expect(screen.getByTestId("pr-comments-line").textContent).toBe("The review comments could not be read 5 times in a row.");
    expect(screen.queryByTestId("pr-comments-stop")).toBeNull();
  });

  it("an older line is its sentence alone and reads nothing; the newest offers every comment still open", async () => {
    // Two reads: c1 arrived first, c2 later. Both are still to answer.
    H.view = { ...H.view, comments: [raw("c1"), raw("c2", { author: "pat" })] };
    const older = render(<PrCommentsLine threadId={SID} raw={line(["c1"], ["sam"])} content="" archived={false}
      busy={false} />);
    expect(older.getByTestId("pr-comments-line").textContent).toBe("1 review comment from sam arrived.");
    expect(older.queryByTestId("pr-comments-answer")).toBeNull();
    expect(older.queryByTestId("pr-comments-stop")).toBeNull();
    expect(H.reads).toBe(0);
    older.unmount();
    render(<PrCommentsLine card threadId={SID} raw={line(["c2"], ["pat"])} content="" archived={false} busy={false} />);
    const rows = await screen.findAllByTestId("pr-comment");
    expect(rows.map((r) => r.getAttribute("data-id"))).toEqual([`${SID}:c1`, `${SID}:c2`]);
    expect(screen.getAllByTestId("pr-comments-answer")).toHaveLength(1);
    expect(screen.getByTestId("pr-comments-answer").textContent).toBe("Answer these (2)");
  });
});

describe("the Changes tab pins each comment on its line", () => {
  const DIFF = "--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,3 @@\n x = 1\n-y = 2\n+y = 3\n z = 4";
  const change: FileChange = { path: ABS, rel: "app.py", name: "app.py", status: "modified", added: 1, removed: 1, diff: DIFF };

  it("places a comment on its own side's line, and the rest after the rows", () => {
    const rows = parseUnified(DIFF);
    const onNew = comment("c1", { line: 2, side: "new" });
    const onOld = comment("c2", { line: 2, side: "old" });
    const outdated = comment("c3", { line: 2, outdated: true });
    const elsewhere = comment("c4", { line: 40 });
    const placed = placePins([onNew, onOld, outdated, elsewhere], rows);
    const at = (c: PrComment) => [...placed.onRow.entries()].find(([, list]) => list.includes(c))?.[0];
    expect(at(onNew)).toMatchObject({ kind: "added", newNo: 2 });
    expect(at(onOld)).toMatchObject({ kind: "removed", oldNo: 2 });
    expect(placed.rest).toEqual([outdated, elsewhere]);
  });

  it("draws them marked from GitHub, set-aside ones left out", () => {
    render(
      <ChangesReview
        changes={[change]}
        sessionId="s1"
        github={[comment("c1", { body: "Rename y." }), comment("c3", { outdated: true, body: "Old note." }),
          comment("c5", { state: "set_aside", body: "Hidden." }), comment("c6", { abs: "C:\\other\\b.py", body: "Elsewhere." })]}
      />,
    );
    const pins = screen.getByTestId("review-github-pins");
    expect(pins.textContent).toContain("sam");
    expect(pins.textContent).toContain(PR_COMMENTS_FROM_GITHUB);
    expect(pins.textContent).toContain("Rename y.");
    // Under the added line it is about.
    const before = pins.previousElementSibling as HTMLElement;
    expect(before.getAttribute("data-kind")).toBe("added");
    expect(before.textContent).toContain("y = 3");
    const rest = screen.getByTestId("review-github-rest");
    expect(rest.textContent).toContain("Old note.");
    expect(screen.getByTestId("review-github-count-0").textContent).toBe("2 comments from GitHub");
    expect(document.body.textContent).not.toContain("Hidden.");
    expect(document.body.textContent).not.toContain("Elsewhere.");
  });

  it("with no comments the file draws as before", () => {
    render(<ChangesReview changes={[change]} sessionId="s1" />);
    expect(screen.queryByTestId("review-github-pins")).toBeNull();
    expect(screen.queryByTestId("review-github-count-0")).toBeNull();
  });
});

describe("the bell", () => {
  it("is a quiet row that opens the coding session", () => {
    expect(BELL_ACTIVITY_TYPES).toContain("build.pr_comments");
    const item = toActivity({
      id: "evt_pr1", type: "build.pr_comments", session_id: `build:${SID}`, ts: "2026-11-22T09:00:00",
      payload: { thread_id: SID, count: 3, title: "Review comments arrived",
        body: "Fix the parser · 3 review comments from sam and alex arrived.", session_title: "Fix the parser" },
    } as unknown as IJEvent)!;
    expect(item.quiet).toBe(true);
    expect(item.attention).toBeFalsy();
    expect(item.href).toBe(`/chat?view=code&session=${SID}`);
    expect(item.title).toBe("Review comments arrived");
    expect(item.body).toBe("Fix the parser · 3 review comments from sam and alex arrived.");
  });
});
