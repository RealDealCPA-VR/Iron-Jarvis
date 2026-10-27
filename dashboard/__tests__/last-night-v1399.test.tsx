/**
 * v1.399.0 (night shift N2) — Coding sessions › LAST NIGHT
 * (components/harness/LastNightList, lib/nightShift, the sidebar's Last night
 * row, the bell's one row and the `?night=` link).
 *
 *  - ONE summary per night, read from GET /build/night/summaries (the daemon's
 *    shape: fixtures/last-night-v1399.json): failed and needs-you first, each
 *    row's branch, state, tests ("not trusted" when the night said so), files,
 *    time, usage per account (list price on a plan, money per use, free), what
 *    needs the user; the main-folder change UN-FOLDED;
 *  - the buttons are the session's own git machinery: Open, Changes (opens
 *    the session on its Changes pane), Push, Open a pull request (the branch's
 *    own prefill), and Throw away — TWO presses, the folder to the trash, the
 *    branch kept;
 *  - older nights below, newest first, folded; a link names one open;
 *  - the sidebar's Last night row and a `?night=` link open the view;
 *  - the bell row (one per night, counted) opens it.
 */
import React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    summaries: null as unknown,
    calls: [] as [string, string, unknown][],
    fail: {} as Record<string, Error>,
  };
});
vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) => {
    api.calls.push(["GET", p, undefined]);
    if (api.fail[p]) return Promise.reject(api.fail[p]);
    if (p === "/build/night/summaries") return Promise.resolve(api.summaries);
    if (p === "/build/night") return Promise.resolve({ tasks: [], window: {}, limits: {}, emergency_stop: false });
    if (p === "/build/sessions") return Promise.resolve(SESSIONS);
    if (p === "/build/sessions/bs_fail/git") {
      return Promise.resolve({ pr_draft: { title: "Fix the parser", body: "Committed by Iron Jarvis." } });
    }
    return Promise.reject(new api.ApiError(`unmocked ${p}`, 404));
  },
  post: (p: string, b?: unknown) => {
    api.calls.push(["POST", p, b]);
    if (api.fail[p]) return Promise.reject(api.fail[p]);
    if (p.endsWith("/git/pr")) return Promise.resolve({ url: "https://github.com/acme/parser/pull/9", number: 9 });
    if (p.endsWith("/git/discard")) {
      return Promise.resolve({ moved: true, note: "The worktree is in Iron Jarvis's trash and the branch ij/fix-the-parser-77aa is kept." });
    }
    return Promise.resolve({});
  },
  patch: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({ ok: true }),
}));
vi.mock("@/lib/useModels", () => ({
  useModels: () => ({ models: [], usable: [], data: { models: [] }, loading: false, error: null, reload: () => {} }),
}));
vi.mock("@/components/harness/HarnessSession", () => ({
  HarnessSession: ({ threadId }: { threadId: string }) => <div data-testid="session-stub" data-id={threadId} />,
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));

const SESSIONS = {
  folders: [
    {
      path: "C:\\work\\shop", name: "shop", is_project: false, project_id: null,
      sessions: [{ id: "s1", title: "Add a cart", updated_at: "2026-10-26T10:00:00", running: false, waiting: false, archived: false }],
    },
  ],
};

import { LastNightList } from "@/components/harness/LastNightList";
import { IjHarnessView } from "@/components/harness/IjHarnessView";
import { toActivity } from "@/components/NotificationBell";
import { nightOfHref, BUILD_NIGHT_EVENT, announceBuildSessionLink, lastNightHref } from "@/lib/buildLinks";
import { decodeRow, decodeSummary, filesWords, ranWords, testsWords, usageLine } from "@/lib/nightShift";
import { providerDisplay } from "@/lib/onboarding";
import type { IJEvent } from "@/lib/types";

function fixture(): { nights: unknown[] } {
  const file = path.join(__dirname, "fixtures", "last-night-v1399.json");
  return JSON.parse(readFileSync(file, "utf-8").replace(/\r\n/g, "\n"));
}

const terminal = { find: () => null, open: async () => null, render: () => null, close: () => {} };

async function shown(onOpen = vi.fn(), focusNight: string | null = null) {
  render(<LastNightList onOpenSession={onOpen} focusNight={focusNight} />);
  await screen.findByTestId("last-night-rows-2026-10-26");
  return onOpen;
}

beforeEach(() => {
  api.calls = [];
  api.fail = {};
  api.summaries = fixture();
  localStorage.clear();
});
afterEach(() => cleanup());

describe("Last night: the summary, read from the records", () => {
  it("lists the newest night as Last night, failed and needs-you first, each row's facts in words", async () => {
    await shown();
    expect(screen.getByRole("heading", { level: 2 }).textContent).toContain("Last night · Monday, October 26");
    expect(screen.getByTestId("last-night-counts").textContent).toBe("3 tasks: 1 failed, 1 needs you, 1 done.");
    const rows = within(screen.getByTestId("last-night-rows-2026-10-26")).getAllByRole("listitem")
      .filter((li) => li.dataset.state);
    expect(rows.map((r) => r.dataset.state)).toEqual(["failed", "needs_you", "done"]);
    // The ONE status table's words.
    expect(screen.getByTestId("last-night-state-night_fail").textContent).toBe("Failed");
    expect(screen.getByTestId("last-night-state-night_needs").textContent).toBe("Needs you");
    expect(screen.getByTestId("last-night-state-night_done").textContent).toBe("Done");
    // Branch, time, tests, files, the commit.
    const fail = screen.getByTestId("last-night-task-night_fail");
    expect(fail.textContent).toContain("parser · ij/fix-the-parser-77aa · 2 min");
    expect(screen.getByTestId("last-night-failure-night_fail").textContent).toBe("The model's server went away.");
    expect(screen.getByTestId("last-night-facts-night_fail").textContent).toBe(
      "No tests ran · 1 file changed (+4 −1) · Committed 77aa66b",
    );
    expect(screen.getByTestId("last-night-commit-night_fail").textContent).toBe(
      "Its work so far is committed on ij/fix-the-parser-77aa, so nothing is lost.",
    );
    expect(screen.getByTestId("last-night-facts-night_needs").textContent).toContain("Tests passed, but not trusted");
    expect(screen.getByTestId("last-night-facts-night_done").textContent).toBe(
      "Tests passed · 2 files changed (+3 −1) · Committed 1a2b3c4",
    );
    // Usage per account: the account's name, else the provider's; the
    // daemon's own words (list price on a plan, money per use, free).
    expect(screen.getByTestId("last-night-usage-night_fail").textContent).toBe(
      "Work Max: 12,345 tokens · ≈$0.42 at list price (your plan)",
    );
    expect(screen.getByTestId("last-night-usage-night_needs").textContent).toBe(
      `${providerDisplay("ollama")}: 1,000 tokens · free`,
    );
    expect(screen.getByTestId("last-night-usage-night_done").textContent).toBe(
      `${providerDisplay("openai")}: 5,500 tokens · $0.05`,
    );
  });

  it("what needs you is said open, and a main folder that changed is NEVER folded", async () => {
    await shown();
    const main = screen.getByTestId("last-night-main-changed-night_needs");
    expect(main.textContent).toBe(
      "Your main folder C:\\work\\shop changed while this task ran: a file changed (README.md).",
    );
    expect(main.closest("details")).toBeNull();
    expect(main.querySelector("svg")).not.toBeNull(); // an icon, never hue alone
    const needs = screen.getByTestId("last-night-needs-night_needs");
    expect(needs.closest("details")).toBeNull();
    expect(needs.textContent).toContain("Needs your OK: run a command");
    // The rest is folded under "What happened".
    expect(screen.getByTestId("last-night-notes-night_needs").tagName).toBe("DETAILS");
    expect(screen.queryByTestId("last-night-main-changed-night_done")).toBeNull();
  });

  it("Open and Changes open the task's session (Changes on its Changes pane)", async () => {
    const onOpen = await shown();
    fireEvent.click(screen.getByTestId("last-night-open-night_needs"));
    expect(onOpen).toHaveBeenLastCalledWith("bs_needs");
    fireEvent.click(screen.getByTestId("last-night-changes-night_fail"));
    expect(onOpen).toHaveBeenLastCalledWith("bs_fail", { changes: true });
  });

  it("Push and Open a pull request go through the session's own git routes", async () => {
    await shown();
    fireEvent.click(screen.getByTestId("last-night-push-night_fail"));
    await waitFor(() =>
      expect(api.calls).toContainEqual(["POST", "/build/sessions/bs_fail/git/push", {}]),
    );
    await screen.findByText("Pushed ij/fix-the-parser-77aa.");
    fireEvent.click(screen.getByTestId("last-night-pr-night_fail"));
    await screen.findByText("Opened a pull request: https://github.com/acme/parser/pull/9");
    // The branch's own prefill (GET …/git pr_draft), never words made up here.
    expect(api.calls).toContainEqual(["GET", "/build/sessions/bs_fail/git", undefined]);
    expect(api.calls).toContainEqual([
      "POST", "/build/sessions/bs_fail/git/pr", { title: "Fix the parser", body: "Committed by Iron Jarvis." },
    ]);
    // A pushed branch with a pull request offers neither again — its link.
    expect(screen.queryByTestId("last-night-push-night_done")).toBeNull();
    expect(screen.queryByTestId("last-night-pr-night_done")).toBeNull();
    expect(screen.getByTestId("last-night-pr-link-night_done").getAttribute("href")).toBe(
      "https://github.com/acme/calc/pull/7",
    );
  });

  it("a refused push says Iron Jarvis's own words", async () => {
    api.fail["/build/sessions/bs_fail/git/push"] = new Error(
      "This repository has no remote to push to. Add one with git, then push again.",
    );
    await shown();
    fireEvent.click(screen.getByTestId("last-night-push-night_fail"));
    expect((await screen.findByTestId("last-night-problem-night_fail")).textContent).toBe(
      "This repository has no remote to push to. Add one with git, then push again.",
    );
  });

  it("Throw away takes TWO presses: the folder to the trash, the branch kept", async () => {
    await shown();
    const throwAway = within(screen.getByTestId("last-night-throw-night_fail")).getByRole("button");
    expect(throwAway.textContent).toBe("Throw away");
    fireEvent.click(throwAway);
    expect(throwAway.textContent).toBe("Move its folder to the trash?");
    expect(api.calls.some(([, p]) => p.endsWith("/git/discard"))).toBe(false); // one press: nothing
    fireEvent.click(throwAway);
    await waitFor(() =>
      expect(api.calls).toContainEqual(["POST", "/build/sessions/bs_fail/git/discard", { confirm: true }]),
    );
    expect((await screen.findByTestId("last-night-said-night_fail")).textContent).toBe(
      "The worktree is in Iron Jarvis's trash and the branch ij/fix-the-parser-77aa is kept.",
    );
  });

  it("older nights are listed below, newest first, folded — a link names one open", async () => {
    await shown();
    const older = screen.getByTestId("last-night-older-2026-10-25");
    expect(older.tagName).toBe("DETAILS");
    expect((older as HTMLDetailsElement).open).toBe(false);
    expect(older.querySelector("summary")?.textContent).toBe("Sunday, October 25 · 1 task: 1 stopped");
    // Its folder was thrown away: no buttons but Open, and it says so.
    expect(within(older).getByTestId("last-night-removed-night_old").textContent).toBe(
      "Folder in the trash; the branch is kept.",
    );
    expect(within(older).queryByTestId("last-night-throw-night_old")).toBeNull();
    expect(within(older).getByTestId("last-night-commit-night_old").textContent).toBe(
      "Nothing changed, so nothing was committed.",
    );
    cleanup();
    await shown(vi.fn(), "2026-10-25");
    expect((screen.getByTestId("last-night-older-2026-10-25") as HTMLDetailsElement).open).toBe(true);
  });

  it("no night yet says where the summary comes from", async () => {
    api.summaries = { nights: [], keep: 14 };
    render(<LastNightList onOpenSession={vi.fn()} />);
    expect((await screen.findByTestId("last-night-empty")).textContent).toBe(
      "No night has ended yet. Queue tasks in Tonight; their summary is here in the morning.",
    );
  });

  it("an older Iron Jarvis (404) says so in words", async () => {
    api.fail["/build/night/summaries"] = new api.ApiError("Not Found", 404);
    render(<LastNightList onOpenSession={vi.fn()} />);
    expect(await screen.findByText("This version of Iron Jarvis has no Last night summary yet.")).toBeTruthy();
  });

  it("the keyboard: every button is a real button in the tab order", async () => {
    await shown();
    const row = screen.getByTestId("last-night-task-night_fail");
    const buttons = within(row).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual([
      "Open", "Changes", "Push", "Open a pull request", "Throw away",
    ]);
    for (const b of buttons) expect(b.getAttribute("tabindex")).not.toBe("-1");
    buttons[1].focus();
    expect(document.activeElement).toBe(buttons[1]);
  });
});

describe("Last night: the decoders and words", () => {
  it("decode only what the daemon promises", () => {
    expect(decodeRow({ no: "id" })).toBeNull();
    const row = decodeRow({ id: "x", state: "exploded", usage: [{ kind: "weird", input_tokens: 1.7 }, 5], tests: "maybe" })!;
    expect([row.state, row.tests, row.usage.length, row.usage[0].kind, row.usage[0].inputTokens]).toEqual([
      "stopped", "", 1, "", 1,
    ]);
    expect(row.session).toEqual({ exists: false, archived: false, hasBranch: false, removed: false, pushed: false, prUrl: "", merged: false });
    expect(decodeSummary({ night: "" })).toBeNull();
  });

  it("time, tests, files and usage in words", () => {
    expect([ranWords(45), ranWords(612), ranWords(3900)]).toEqual(["45 s", "10 min", "1 h 5 min"]);
    expect(testsWords({ tests: "failed", testsTrusted: true })).toBe("Tests failed");
    expect(filesWords({ files: 0, added: 0, removed: 0 })).toBe("No files changed");
    expect(
      usageLine({ account: "", provider: "", model: "", inputTokens: 0, outputTokens: 0, costUsd: null, kind: "", words: "" }, () => ""),
    ).toBe("Your model");
  });
});

describe("Last night: where it opens", () => {
  it("the sidebar's Last night row opens the view in the middle; Changes opens the session on its Changes pane", async () => {
    window.history.replaceState(null, "", "/terminals");
    render(<IjHarnessView initialSession={null} terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />);
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "s1"));
    fireEvent.click(screen.getByTestId("harness-last-night"));
    expect(screen.getByTestId("harness-last-night")).toHaveAttribute("aria-current", "page");
    await screen.findByTestId("last-night-task-night_fail");
    expect(screen.queryByTestId("session-stub")).toBeNull();
    fireEvent.click(screen.getByTestId("last-night-changes-night_fail"));
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "bs_fail"));
    expect(localStorage.getItem("ij.build.pane")).toBe("changes");
    expect(screen.queryByTestId("last-night")).toBeNull();
  });

  it("a ?night= link opens Last night, and a second press of the link opens it again", async () => {
    window.history.replaceState(null, "", "/terminals?night=2026-10-26");
    const view = render(
      <IjHarnessView initialSession={null} nightLink={{ night: "2026-10-26", n: 1 }} terminals={[]}
        onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />,
    );
    await screen.findByTestId("last-night-task-night_fail");
    expect(window.location.search).toBe("?night=2026-10-26"); // its own address stays
    fireEvent.click(screen.getByTestId("last-night-open-night_done"));
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "bs_done"));
    view.rerender(
      <IjHarnessView initialSession={null} nightLink={{ night: "2026-10-26", n: 2 }} terminals={[]}
        onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />,
    );
    await screen.findByTestId("last-night-task-night_fail");
  });

  it("the bell's row opens Last night on that night, and a press on Build announces it", () => {
    expect(lastNightHref("2026-10-26")).toBe("/terminals?night=2026-10-26");
    expect(nightOfHref("/terminals?night=2026-10-26")).toBe("2026-10-26");
    expect(nightOfHref("/terminals?session=x")).toBeNull();
    const item = toActivity({
      id: "e1", type: "build.night_summary", ts: "2026-10-27T11:00:30",
      payload: { night: "2026-10-26", title: "Last night: 1 done", body: "Done: Make add() add", counts: { done: 1 } },
    } as unknown as IJEvent)!;
    expect([item.href, item.title, item.body, item.attention, item.quiet]).toEqual([
      "/terminals?night=2026-10-26", "Last night: 1 done", "Done: Make add() add", true, undefined,
    ]);
    expect(toActivity({ id: "e2", type: "build.night_summary", ts: "", payload: {} } as unknown as IJEvent)).toBeNull();
    window.history.replaceState(null, "", "/terminals");
    const heard: string[] = [];
    const on = (e: Event) => heard.push((e as CustomEvent<{ id: string }>).detail.id);
    window.addEventListener(BUILD_NIGHT_EVENT, on);
    act(() => {
      expect(announceBuildSessionLink("/terminals?night=2026-10-26")).toBe(true);
    });
    window.removeEventListener(BUILD_NIGHT_EVENT, on);
    expect(heard).toEqual(["2026-10-26"]);
  });
});
