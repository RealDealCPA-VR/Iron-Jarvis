// @vitest-environment jsdom
/**
 * The Improvements page (v1.495.0, vision plan leap 5, Phase 1): one list of
 * every suggestion that waits for the user's answer. The page decides nothing
 * itself: it presses the address each suggestion carries, and only when that
 * address is one of the suggestion routes this app has.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  inbox: {} as unknown,
  gets: [] as string[],
  posts: [] as { path: string; body: unknown }[],
  getError: null as Error | null,
  postError: null as Error | null,
  postAnswer: { status: "ok" } as unknown,
  holdPost: null as null | Promise<void>,
  holdGet: null as null | Promise<void>,
  events: [] as { id: string; type: string }[],
  eventTypes: null as string[] | null,
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    get: vi.fn(async (path: string) => {
      H.gets.push(path);
      if (H.holdGet) await H.holdGet;
      if (H.getError) throw H.getError;
      return JSON.parse(JSON.stringify(H.inbox));
    }),
    post: vi.fn(async (path: string, body: unknown) => {
      H.posts.push({ path, body });
      if (H.holdPost) await H.holdPost;
      if (H.postError) throw H.postError;
      return H.postAnswer;
    }),
  };
});

vi.mock("@/lib/useEvents", () => ({
  useEvents: (_max: number, opts?: { types?: string[] }) => {
    H.eventTypes = opts?.types ?? null;
    return { events: H.events, connected: true };
  },
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import type React from "react";
import {
  ARMED_MS,
  EVENT_REREAD_MS,
  ImprovementsInbox,
  PRESS_AGAIN,
  SECOND_PRESS_AFTER_MS,
} from "@/components/improvements/ImprovementsInbox";
import {
  IMPROVEMENT_EVENT_TYPES,
  NOTHING_WAITING_TITLE,
  OFFER_ONLY_NOTE,
  TAKEN_OFF,
  acceptedWords,
  answerNote,
  decodeInbox,
  groupsToShow,
  isDecisionPath,
  stillWaiting,
  waitingWords,
} from "@/lib/improvements";
import { OFFER_DISMISS_PREFIX, offerKey } from "@/lib/goalOffers";
import { NON_RAIL_ENTRIES, labelForPath } from "@/lib/nav";
import { SIDEBAR_HREFS, surfaceFor, ungrouped } from "@/lib/surfaces";
import { readFileSync } from "node:fs";
import { join } from "node:path";

function action(label: string, path: string, more: Record<string, unknown> = {}) {
  return { label, method: "POST", path, body: {}, note: "", confirm: false, blocked: "", ...more };
}

function wire(kind: string, ref: string, more: Record<string, unknown> = {}) {
  return {
    id: `${kind}:${ref}`,
    kind,
    ref,
    title: `Title of ${ref}`,
    about: "",
    why: "",
    evidence: [],
    created_at: "2026-11-14T10:00:00",
    detail: null,
    accept: action("Accept", `/coach/proposals/${ref}/accept`),
    decline: action("Decline", `/coach/proposals/${ref}/decline`),
    home: { href: "/skills", label: "Open where it lives" },
    extra: {},
    ...more,
  };
}

function inboxOf(items: ReturnType<typeof wire>[], more: Record<string, unknown> = {}) {
  const kinds = [...new Set(items.map((i) => i.kind))];
  return {
    items,
    groups: kinds.map((k) => ({
      kind: k,
      label: `Group ${k}`,
      count: items.filter((i) => i.kind === k).length,
      shown: items.filter((i) => i.kind === k).length,
    })),
    total: items.length,
    unreadable: [],
    ...more,
  };
}

const PREF = wire("preference", "p1", {
  title: "Keep answers short",
  why: "You asked for this more than once.",
  evidence: ["You said, in a chat: “shorter please”"],
  accept: action("Keep", "/memory/preferences/p1/keep", { note: "Keeping it makes Iron Jarvis follow it." }),
  decline: action("Not this", "/memory/preferences/p1/decline"),
  home: { href: "/memory#prefs-suggested", label: "Open where it lives" },
});
const SKILL = wire("skill", "s1", {
  title: "New skill: month-end-close",
  detail: { mode: "text", before: "", after: "# Month end\n\nSteps.", label: "The skill", cut: false },
  accept: action("Approve", "/skills/proposals/s1/approve", { note: "Approving saves it as a skill." }),
  decline: action("Reject", "/skills/proposals/s1/reject"),
});
const WANTS = wire("wants", "w1", {
  title: "Tidy the downloads folder",
  accept: action("Approve and run", "/proposals/w1/approve", { confirm: true, note: "Approving starts it now." }),
  decline: action("Reject", "/proposals/w1/reject", { confirm: true, note: "Rejecting is final." }),
  home: { href: "/autonomy", label: "Open where it lives" },
});
const OFFER = wire("unasked", "g1:web_fetch", {
  title: "Let the goal “Watch the rates” use web_fetch without asking",
  accept: null,
  decline: null,
  home: { href: "/autonomy", label: "Open where it lives" },
  extra: { goal_id: "g1", tool: "web_fetch", args_hash: "" },
});

/** Run with a clock the test moves (`tick(ms)`), then put the real one back. */
async function withClock(run: (tick: (ms: number) => void) => Promise<void>) {
  const realNow = Date.now;
  let now = realNow();
  Date.now = () => now;
  try {
    await run((ms) => {
      now += ms;
    });
  } finally {
    Date.now = realNow;
  }
}

async function shown(items: ReturnType<typeof wire>[], more: Record<string, unknown> = {}) {
  H.inbox = inboxOf(items, more);
  const view = render(<ImprovementsInbox />);
  await waitFor(() => expect(H.gets.length).toBeGreaterThan(0));
  return view;
}

beforeEach(() => {
  H.inbox = inboxOf([]);
  H.gets = [];
  H.posts = [];
  H.getError = null;
  H.postError = null;
  H.postAnswer = { status: "ok" };
  H.holdPost = null;
  H.holdGet = null;
  H.events = [];
  H.eventTypes = null;
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the list", () => {
  it("reads the one inbox address and shows every suggestion under its group", async () => {
    await shown([PREF, SKILL, WANTS]);
    expect(await screen.findByText("Keep answers short")).toBeTruthy();
    expect(H.gets).toEqual(["/improvements"]);
    expect(screen.getByTestId("improvements-group-preference").textContent).toContain("Group preference · 1");
    expect(screen.getByTestId("improvements-group-skill")).toBeTruthy();
    expect(screen.getByTestId("improvements-count").textContent).toBe("3 suggestions are waiting for you.");
    const card = screen.getByTestId("improvement-preference:p1");
    expect(within(card).getByText("You asked for this more than once.")).toBeTruthy();
    expect(within(card).getByTestId("improvement-evidence").textContent).toContain("shorter please");
    expect(within(card).getByTestId("improvement-does").textContent).toBe("Keeping it makes Iron Jarvis follow it.");
  });

  it("each suggestion keeps its own words on its buttons", async () => {
    await shown([PREF, SKILL]);
    const pref = await screen.findByTestId("improvement-preference:p1");
    expect(within(pref).getByTestId("improvement-accept").textContent).toBe("Keep");
    expect(within(pref).getByTestId("improvement-decline").textContent).toBe("Not this");
    const skill = screen.getByTestId("improvement-skill:s1");
    expect(within(skill).getByTestId("improvement-accept").textContent).toBe("Approve");
    expect(within(skill).getByTestId("improvement-decline").textContent).toBe("Reject");
    expect(within(pref).getByTestId("improvement-home").getAttribute("href")).toBe("/memory#prefs-suggested");
  });

  it("says nothing is waiting only when the list was read and is empty", async () => {
    H.holdGet = new Promise(() => {});
    render(<ImprovementsInbox />);
    await waitFor(() => expect(H.gets.length).toBe(1));
    expect(screen.queryByText(NOTHING_WAITING_TITLE)).toBeNull();
    cleanup();
    H.holdGet = null;
    await shown([]);
    expect(await screen.findByText(NOTHING_WAITING_TITLE)).toBeTruthy();
    expect(screen.queryByTestId("improvements-count")).toBeNull();
  });

  it("a list that could not be read says so and can be read again", async () => {
    H.getError = Object.assign(new Error("boom"), { status: 500 });
    render(<ImprovementsInbox />);
    const line = await screen.findByTestId("improvements-failed");
    expect(line.textContent).toContain("Your suggestions couldn't load.");
    expect(screen.queryByText(NOTHING_WAITING_TITLE)).toBeNull();
    H.getError = null;
    H.inbox = inboxOf([PREF]);
    fireEvent.click(within(line).getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Keep answers short")).toBeTruthy();
    expect(screen.queryByTestId("improvements-failed")).toBeNull();
  });

  it("a store that could not be read is named, never shown as nothing waiting", async () => {
    await shown([], { unreadable: [{ kind: "skill", label: "Skill drafts" }] });
    const warn = await screen.findByTestId("improvements-unreadable");
    expect(warn.textContent).toContain("Skill drafts");
    expect(screen.queryByText(NOTHING_WAITING_TITLE)).toBeNull();
  });

  it("a kind that could not be read is named even when other suggestions are listed", async () => {
    await shown([PREF], { unreadable: [{ kind: "skill", label: "Skill drafts" }] });
    expect(await screen.findByTestId("improvement-preference:p1")).toBeTruthy();
    expect(screen.getByTestId("improvements-unreadable").textContent).toContain("Skill drafts");
  });

  it("a suggestion this page could not read still counts as waiting", async () => {
    H.inbox = {
      items: [{ ...SKILL, title: "" }],
      groups: [{ kind: "skill", label: "Skill drafts", count: 1, shown: 1 }],
      total: 1,
      unreadable: [],
    };
    render(<ImprovementsInbox />);
    expect((await screen.findByTestId("improvements-more-skill")).textContent).toContain("1 more not shown here.");
    expect(screen.queryByText(NOTHING_WAITING_TITLE)).toBeNull();
    expect(screen.getByTestId("improvements-count").textContent).toBe("1 suggestion is waiting for you.");
  });

  it("says how many more are waiting than are listed", async () => {
    H.inbox = { ...inboxOf([SKILL]), groups: [{ kind: "skill", label: "Skill drafts", count: 34, shown: 1 }], total: 34 };
    render(<ImprovementsInbox />);
    const more = await screen.findByTestId("improvements-more-skill");
    expect(more.textContent).toContain("33 more not shown here.");
    expect(screen.getByTestId("improvements-count").textContent).toBe("34 suggestions are waiting for you.");
  });

  it("the whole text is drawn only once it is opened, a change as added and removed lines", async () => {
    const edit = wire("coach", "c1", {
      detail: { mode: "diff", before: "Read the return.", after: "Read the return.\nName the form.", label: "The agent's instructions", cut: true },
    });
    await shown([edit]);
    const card = await screen.findByTestId("improvement-coach:c1");
    expect(within(card).queryByTestId("improvement-detail-body")).toBeNull();
    const details = within(card).getByTestId("improvement-detail") as HTMLDetailsElement;
    expect(details.textContent).toContain("See the change: The agent's instructions");
    details.open = true;
    fireEvent(details, new Event("toggle"));
    const body = await within(card).findByTestId("improvement-detail-body");
    expect(body.getAttribute("data-mode")).toBe("diff");
    expect(body.textContent).toContain("+ Name the form.");
    expect(body.textContent).toContain("  Read the return.");
    expect(card.textContent).toContain("Shortened here.");
  });
});

describe("answering", () => {
  it("Accept presses the suggestion's own address, says what happened and reads the list again", async () => {
    await shown([PREF, SKILL]);
    const card = await screen.findByTestId("improvement-preference:p1");
    H.inbox = inboxOf([SKILL]);
    fireEvent.click(within(card).getByTestId("improvement-accept"));
    await waitFor(() => expect(screen.getByTestId("improvements-note").textContent).toBe(acceptedWords("preference")));
    expect(H.posts).toEqual([{ path: "/memory/preferences/p1/keep", body: {} }]);
    await waitFor(() => expect(screen.queryByTestId("improvement-preference:p1")).toBeNull());
    expect(H.gets.length).toBe(2);
    expect(screen.getByTestId("improvement-skill:s1")).toBeTruthy();
  });

  it("Decline presses the suggestion's own decline and takes it off the list", async () => {
    await shown([SKILL]);
    const card = await screen.findByTestId("improvement-skill:s1");
    H.inbox = inboxOf([]);
    fireEvent.click(within(card).getByTestId("improvement-decline"));
    await waitFor(() => expect(screen.getByTestId("improvements-note").textContent).toBe(TAKEN_OFF));
    expect(H.posts).toEqual([{ path: "/skills/proposals/s1/reject", body: {} }]);
    await waitFor(() => expect(screen.queryByTestId("improvement-skill:s1")).toBeNull());
  });

  it("an answer is off the list at once, before the list is read again", async () => {
    await shown([PREF, SKILL]);
    const card = await screen.findByTestId("improvement-preference:p1");
    H.holdGet = new Promise(() => {}); // the re-read never answers
    fireEvent.click(within(card).getByTestId("improvement-accept"));
    await waitFor(() => expect(screen.queryByTestId("improvement-preference:p1")).toBeNull());
    expect(screen.getByTestId("improvement-skill:s1")).toBeTruthy();
    expect(screen.getByTestId("improvements-count").textContent).toBe("1 suggestion is waiting for you.");
  });

  it("a refusal is said in the route's own words and the suggestion stays", async () => {
    await shown([SKILL]);
    const card = await screen.findByTestId("improvement-skill:s1");
    H.postError = new Error("A skill called month-end-close already exists.");
    fireEvent.click(within(card).getByTestId("improvement-accept"));
    const alert = await within(card).findByRole("alert");
    expect(alert.textContent).toBe("A skill called month-end-close already exists.");
    expect(screen.getByTestId("improvement-skill:s1")).toBeTruthy();
    expect(screen.getByTestId("improvements-note").textContent).toBe("");
    expect(H.gets.length).toBe(1);
    // The buttons work again.
    expect((within(card).getByTestId("improvement-accept") as HTMLButtonElement).disabled).toBe(false);
  });

  it("an accept that does something at once takes two presses", async () => {
    await withClock(async (tick) => {
      await shown([WANTS]);
      const card = await screen.findByTestId("improvement-wants:w1");
      const accept = within(card).getByTestId("improvement-accept");
      fireEvent.click(accept);
      expect(accept.textContent).toBe(PRESS_AGAIN);
      expect(H.posts).toEqual([]);
      tick(SECOND_PRESS_AFTER_MS + 50);
      fireEvent.click(accept);
      await waitFor(() => expect(H.posts).toEqual([{ path: "/proposals/w1/approve", body: {} }]));
      await waitFor(() => expect(screen.getByTestId("improvements-note").textContent).toBe(acceptedWords("wants")));
    });
  });

  it("a double click is one press, not two", async () => {
    await withClock(async (tick) => {
      await shown([WANTS]);
      const card = await screen.findByTestId("improvement-wants:w1");
      const accept = within(card).getByTestId("improvement-accept");
      fireEvent.click(accept);
      tick(60);
      fireEvent.click(accept);
      expect(H.posts).toEqual([]);
      expect(accept.getAttribute("data-armed")).toBe("true");
      tick(SECOND_PRESS_AFTER_MS);
      fireEvent.click(accept);
      await waitFor(() => expect(H.posts.length).toBe(1));
    });
  });

  it("a decline that is final takes two presses too", async () => {
    await withClock(async (tick) => {
      await shown([WANTS]);
      const card = await screen.findByTestId("improvement-wants:w1");
      const decline = within(card).getByTestId("improvement-decline");
      fireEvent.click(decline);
      expect(decline.textContent).toBe(PRESS_AGAIN);
      expect(H.posts).toEqual([]);
      tick(SECOND_PRESS_AFTER_MS + 50);
      fireEvent.click(decline);
      await waitFor(() => expect(H.posts).toEqual([{ path: "/proposals/w1/reject", body: {} }]));
    });
  });

  it("a first press that is not followed up goes back to one press", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await shown([WANTS]);
    const card = await screen.findByTestId("improvement-wants:w1");
    const accept = within(card).getByTestId("improvement-accept");
    fireEvent.click(accept);
    expect(accept.getAttribute("data-armed")).toBe("true");
    await act(async () => {
      vi.advanceTimersByTime(ARMED_MS + 50);
    });
    expect(accept.getAttribute("data-armed")).toBe("false");
    expect(accept.textContent).toBe("Approve and run");
    expect(H.posts).toEqual([]);
  });

  it("an ordinary decline is one press", async () => {
    await shown([PREF]);
    const card = await screen.findByTestId("improvement-preference:p1");
    fireEvent.click(within(card).getByTestId("improvement-decline"));
    await waitFor(() => expect(H.posts).toEqual([{ path: "/memory/preferences/p1/decline", body: {} }]));
  });

  it("says what the suggestion's own route said when it accepted", async () => {
    const habit = wire("habit", "h1", { accept: action("Accept", "/coach/proposals/h1/accept") });
    await shown([habit]);
    const card = await screen.findByTestId("improvement-habit:h1");
    H.postAnswer = { status: "accepted", note: "Team notes are turned off, so no model is shown the habit until you turn them on." };
    H.inbox = inboxOf([]);
    fireEvent.click(within(card).getByTestId("improvement-accept"));
    await waitFor(() =>
      expect(screen.getByTestId("improvements-note").textContent).toBe(
        "Team notes are turned off, so no model is shown the habit until you turn them on.",
      ),
    );
  });

  it("a note from a decline's answer is not shown as if something was accepted", async () => {
    await shown([PREF]);
    const card = await screen.findByTestId("improvement-preference:p1");
    H.postAnswer = { note: "something" };
    fireEvent.click(within(card).getByTestId("improvement-decline"));
    await waitFor(() => expect(screen.getByTestId("improvements-note").textContent).toBe(TAKEN_OFF));
  });

  it("a suggestion answered somewhere else is read again and leaves, with the route's words kept", async () => {
    await shown([SKILL, PREF]);
    const card = await screen.findByTestId("improvement-skill:s1");
    H.postError = Object.assign(new Error("That draft was already rejected."), { status: 409 });
    H.inbox = inboxOf([PREF]);
    fireEvent.click(within(card).getByTestId("improvement-accept"));
    await waitFor(() => expect(screen.queryByTestId("improvement-skill:s1")).toBeNull());
    expect(H.gets.length).toBe(2);
    expect(screen.getByTestId("improvements-note").textContent).toBe("That draft was already rejected.");
    expect(screen.getByTestId("improvement-preference:p1")).toBeTruthy();
  });

  it("an answer that did not take shows the suggestion again", async () => {
    await shown([PREF]);
    const card = await screen.findByTestId("improvement-preference:p1");
    // The route answered, but the list still holds it.
    fireEvent.click(within(card).getByTestId("improvement-accept"));
    await waitFor(() => expect(H.gets.length).toBe(2));
    expect(await screen.findByTestId("improvement-preference:p1")).toBeTruthy();
  });

  it("a new press clears the last refusal while it is out", async () => {
    await shown([SKILL]);
    const card = await screen.findByTestId("improvement-skill:s1");
    H.postError = new Error("Not now.");
    fireEvent.click(within(card).getByTestId("improvement-accept"));
    expect((await within(card).findByRole("alert")).textContent).toBe("Not now.");
    H.postError = null;
    let release: () => void = () => {};
    H.holdPost = new Promise<void>((r) => (release = r));
    fireEvent.click(within(card).getByTestId("improvement-accept"));
    await waitFor(() => expect(within(card).queryByRole("alert")).toBeNull());
    expect((within(card).getByTestId("improvement-accept") as HTMLButtonElement).disabled).toBe(true);
    await act(async () => {
      release();
    });
  });

  it("a suggestion Iron Jarvis cannot carry out says why and cannot be accepted", async () => {
    const blocked = wire("tool", "t1", {
      accept: action("Approve & create", "/capability/proposals/t1/approve", {
        confirm: true,
        blocked: "Iron Jarvis cannot add this kind for you yet.",
      }),
      decline: action("Reject", "/capability/proposals/t1/reject"),
    });
    await shown([blocked]);
    const card = await screen.findByTestId("improvement-tool:t1");
    expect(within(card).getByTestId("improvement-blocked").textContent).toBe("Iron Jarvis cannot add this kind for you yet.");
    const accept = within(card).getByTestId("improvement-accept") as HTMLButtonElement;
    expect(accept.disabled).toBe(true);
    fireEvent.click(accept);
    expect(H.posts).toEqual([]);
    expect((within(card).getByTestId("improvement-decline") as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("an offer to stop asking", () => {
  it("is listed with a link and no buttons", async () => {
    await shown([OFFER]);
    const card = await screen.findByTestId("improvement-unasked:g1:web_fetch");
    expect(within(card).queryByTestId("improvement-accept")).toBeNull();
    expect(within(card).queryByTestId("improvement-decline")).toBeNull();
    expect(within(card).getByTestId("improvement-offer-only").textContent).toBe(OFFER_ONLY_NOTE);
    expect(within(card).getByTestId("improvement-home").getAttribute("href")).toBe("/autonomy");
  });

  it("is not listed once this browser said Not now on the Autonomy page", async () => {
    window.localStorage.setItem(`${OFFER_DISMISS_PREFIX}${offerKey("g1", "web_fetch")}`, "1");
    await shown([OFFER, PREF]);
    expect(await screen.findByTestId("improvement-preference:p1")).toBeTruthy();
    expect(screen.queryByTestId("improvement-unasked:g1:web_fetch")).toBeNull();
    expect(screen.queryByTestId("improvements-group-unasked")).toBeNull();
    expect(screen.getByTestId("improvements-count").textContent).toBe("1 suggestion is waiting for you.");
  });

  it("an exact offer is set aside by its own key only", () => {
    const exact = decodeInbox(inboxOf([{ ...OFFER, extra: { goal_id: "g1", tool: "web_fetch", args_hash: "abc" } }])).items[0];
    window.localStorage.setItem(`${OFFER_DISMISS_PREFIX}${offerKey("g1", "web_fetch")}`, "1");
    expect(stillWaiting(exact)).toBe(true);
    window.localStorage.setItem(`${OFFER_DISMISS_PREFIX}${offerKey("g1", "web_fetch", "abc")}`, "1");
    expect(stillWaiting(exact)).toBe(false);
  });
});

describe("when it reads", () => {
  it("reads again when Iron Jarvis says a suggestion was made, a burst as one read", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const view = await shown([PREF]);
    expect(H.eventTypes).toEqual(IMPROVEMENT_EVENT_TYPES);
    expect(H.gets.length).toBe(1);
    H.events = [{ id: "e1", type: "coach.proposal" }];
    view.rerender(<ImprovementsInbox />);
    H.events = [{ id: "e2", type: "coach.proposal" }, { id: "e1", type: "coach.proposal" }];
    view.rerender(<ImprovementsInbox />);
    await act(async () => {
      vi.advanceTimersByTime(EVENT_REREAD_MS + 50);
    });
    await waitFor(() => expect(H.gets.length).toBe(2));
  });

  it("never reads on a clock", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await shown([PREF]);
    await act(async () => {
      vi.advanceTimersByTime(10 * 60 * 1000);
    });
    expect(H.gets.length).toBe(1);
  });

  it("reads again when the window is looked at again, but not right after a read", async () => {
    const realNow = Date.now;
    let now = realNow();
    Date.now = () => now;
    try {
      await shown([PREF]);
      await screen.findByText("Keep answers short");
      fireEvent(window, new Event("focus"));
      expect(H.gets.length).toBe(1);
      now += 60_000;
      fireEvent(window, new Event("focus"));
      await waitFor(() => expect(H.gets.length).toBe(2));
    } finally {
      Date.now = realNow;
    }
  });

  it("does not read while the window is hidden", async () => {
    await withClock(async (tick) => {
      await shown([PREF]);
      await screen.findByText("Keep answers short");
      tick(60_000);
      const spy = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
      try {
        fireEvent(document, new Event("visibilitychange"));
        fireEvent(window, new Event("focus"));
        expect(H.gets.length).toBe(1);
      } finally {
        spy.mockRestore();
      }
      fireEvent(document, new Event("visibilitychange"));
      await waitFor(() => expect(H.gets.length).toBe(2));
    });
  });

  it("a later read that fails keeps the list and says the newest could not be read", async () => {
    await withClock(async (tick) => {
      await shown([PREF]);
      await screen.findByText("Keep answers short");
      H.getError = Object.assign(new Error("boom"), { status: 500 });
      tick(60_000);
      fireEvent(window, new Event("focus"));
      const stale = await screen.findByTestId("improvements-stale");
      expect(stale.textContent).toContain("The newest suggestions couldn't load.");
      expect(screen.getByTestId("improvement-preference:p1")).toBeTruthy();
      expect(screen.queryByTestId("improvements-failed")).toBeNull();
    });
  });

  it("an older read that answers late never replaces a newer one", async () => {
    let release: () => void = () => {};
    H.holdGet = new Promise<void>((r) => (release = r));
    H.inbox = inboxOf([PREF]);
    render(<ImprovementsInbox />);
    await waitFor(() => expect(H.gets.length).toBe(1));
    // A second read starts and answers first with the newer list.
    H.holdGet = null;
    H.inbox = inboxOf([SKILL]);
    const realNow = Date.now;
    const later = realNow() + 60_000;
    Date.now = () => later;
    try {
      fireEvent(window, new Event("focus"));
      expect(await screen.findByTestId("improvement-skill:s1")).toBeTruthy();
    } finally {
      Date.now = realNow;
    }
    H.inbox = inboxOf([PREF]);
    await act(async () => {
      release();
    });
    expect(screen.getByTestId("improvement-skill:s1")).toBeTruthy();
    expect(screen.queryByTestId("improvement-preference:p1")).toBeNull();
  });
});

describe("what comes off the wire is checked", () => {
  it("a decision is only ever one of the suggestion routes", () => {
    for (const ok of [
      "/coach/proposals/abc/accept",
      "/coach/proposals/abc/decline",
      "/build/library/x1/accept",
      "/skills/proposals/s/approve",
      "/skills/proposals/s/reject",
      "/capability/proposals/c/approve",
      "/memory/review/m/dismiss",
      "/memory/preferences/p/keep",
      "/proposals/w/approve",
    ]) {
      expect(isDecisionPath(ok), ok).toBe(true);
    }
    for (const bad of [
      "/settings/values",
      "/shutdown",
      "/coach/proposals/abc/accept/../../../shutdown",
      "/proposals/x\\..\\..\\reviews\\session_1/approve",
      "/proposals/../approve",
      "/proposals/./approve",
      "/proposals/%2e%2e/approve",
      "/proposals/a%2Fb/approve",
      "/proposals/a b/approve",
      "/proposals/a\tb/approve",
      "/proposals/w/approve\n",
      "/coach/proposals/a/b/accept",
      "/proposals/w/approve?x=1",
      "/proposals//approve",
      "//evil.example/proposals/w/approve",
      "https://evil.example/proposals/w/approve",
      "/memory/preferences/p/delete",
      "/skills/proposals/s/approve#x",
      "proposals/w/approve",
      "",
    ]) {
      expect(isDecisionPath(bad), bad).toBe(false);
    }
  });

  it("a suggestion whose decision names another address has no button for it", async () => {
    const odd = wire("skill", "s9", {
      accept: action("Approve", "/settings/values"),
      decline: { ...action("Reject", "/skills/proposals/s9/reject"), method: "DELETE" },
    });
    await shown([odd]);
    const card = await screen.findByTestId("improvement-skill:s9");
    expect(within(card).queryByTestId("improvement-accept")).toBeNull();
    expect(within(card).queryByTestId("improvement-decline")).toBeNull();
    expect(within(card).getByTestId("improvement-home")).toBeTruthy();
  });

  it("a link out of the app is dropped", () => {
    const out = decodeInbox(
      inboxOf([
        wire("skill", "a", { home: { href: "https://evil.example/x", label: "Open" } }),
        wire("skill", "b", { home: { href: "//evil.example/x", label: "Open" } }),
        wire("skill", "c", { home: { href: "/\\evil.example", label: "Open" } }),
        wire("skill", "e", { home: { href: "/\t/evil.example/x", label: "Open" } }),
        wire("skill", "f", { home: { href: "/skills\\..\\x", label: "Open" } }),
        wire("skill", "g", { home: { href: "/a\n/b", label: "Open" } }),
        wire("skill", "d", { home: { href: "/skills", label: "Open" } }),
        wire("skill", "h", { home: { href: "/agents?view=team&agent=custom%3Atax-reader", label: "Open" } }),
      ]),
    );
    expect(out.items.map((i) => i.home?.href ?? null)).toEqual([
      null, null, null, null, null, null, "/skills", "/agents?view=team&agent=custom%3Atax-reader",
    ]);
  });

  it("rubbish is dropped, never drawn", () => {
    const out = decodeInbox({
      items: [null, 7, "x", { id: "", kind: "skill", title: "t" }, { id: "a", kind: "skill", title: "  " }, wire("skill", "ok")],
      groups: [null, { kind: "" }, { kind: "skill", label: "Skill drafts", count: -3, shown: "2" }],
      total: "many",
      unreadable: [{ kind: "" }, { kind: "tool" }],
    });
    expect(out.items.map((i) => i.id)).toEqual(["skill:ok"]);
    expect(out.groups).toEqual([{ kind: "skill", label: "Skill drafts", count: 0, shown: 0 }]);
    expect(out.total).toBe(0);
    expect(out.unreadable).toEqual([{ kind: "tool", label: "tool" }]);
    expect(decodeInbox(null)).toEqual({ items: [], groups: [], total: 0, unreadable: [] });
  });

  it("the groups keep the order they came in and count what is still waiting", () => {
    const inbox = decodeInbox(inboxOf([SKILL, PREF, wire("skill", "s2")]));
    const g = groupsToShow(inbox, new Set(["skill:s1"]));
    expect(g.map((x) => [x.kind, x.items.map((i) => i.id)])).toEqual([
      ["skill", ["skill:s2"]],
      ["preference", ["preference:p1"]],
    ]);
    expect(groupsToShow(inbox, new Set(["skill:s1", "skill:s2"])).map((x) => x.kind)).toEqual(["preference"]);
  });

  it("reads an accept's own note only when it is words", () => {
    expect(answerNote({ note: "  Saved, but not shown yet.  " })).toBe("Saved, but not shown yet.");
    for (const none of [null, undefined, "x", 7, [], {}, { note: 3 }, { note: "" }, [{ note: "x" }]]) {
      expect(answerNote(none)).toBe("");
    }
  });

  it("counts in plain words", () => {
    expect(waitingWords(0)).toBe("");
    expect(waitingWords(1)).toBe("1 suggestion is waiting for you.");
    expect(waitingWords(2)).toBe("2 suggestions are waiting for you.");
  });
});

describe("where the page lives", () => {
  it("is findable from Everything and Ctrl K, and is not a sidebar row", () => {
    const entry = NON_RAIL_ENTRIES.find((e) => e.href === "/improvements");
    expect(entry?.label).toBe("Improvements");
    expect((entry?.blurb ?? "").length).toBeLessThanOrEqual(120); // one line in Ctrl K
    expect(entry?.aliases).toContain("suggestions");
    expect(surfaceFor("/improvements")?.group).toBe("knowledge");
    expect(ungrouped()).toEqual([]);
    expect(SIDEBAR_HREFS as readonly string[]).not.toContain("/improvements");
    expect(labelForPath("/improvements")).toBe("Improvements");
  });

  it("the page's own title is the name the app calls it by", () => {
    const src = readFileSync(join(process.cwd(), "app/improvements/page.tsx"), "utf8").replace(/\r\n/g, "\n");
    expect(src).toContain('<PageHeader title="Improvements"');
  });

  it("the Autonomy page and this page use one key for Not now", () => {
    const src = readFileSync(join(process.cwd(), "app/autonomy/page.tsx"), "utf8").replace(/\r\n/g, "\n");
    expect(src).toContain('from "@/lib/goalOffers"');
    expect(src).not.toContain('"ij_goal_offer_dismissed:"');
    expect(OFFER_DISMISS_PREFIX).toBe("ij_goal_offer_dismissed:");
    expect(offerKey("g", "t")).toBe("g:t");
    expect(offerKey("g", "t", "h")).toBe("g:t:h");
  });
});
