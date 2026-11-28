// @vitest-environment jsdom
/**
 * Next leaps 03-a (build-out plan R3.1, idea 03): suggestions from Iron
 * Jarvis's own record of your work.
 *
 *  - the bell: a suggestion from the record (`autonomy.proposed` with
 *    `source: "notice"`) is a QUIET row opening Improvements, the suggestion's
 *    own words under it; a watcher's keeps its name;
 *  - Improvements: under "Things Iron Jarvis noticed in your work", each has
 *    its facts and THREE presses: Yes, No, Stop suggesting this kind;
 *  - Yes on "pick it up" opens a new chat in that folder (the route's door);
 *    Stop posts the reject route with `stop_kind`, and says what it turned off.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  inbox: {} as unknown,
  posts: [] as { path: string; body: unknown }[],
  postAnswer: {} as unknown,
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
    API_BASE: "",
    ijToken: () => "",
    sseUrl: (p: string) => p,
    get: vi.fn(async (path: string) => {
      if (path === "/improvements/night") return { offer: null, night: null };
      return JSON.parse(JSON.stringify(H.inbox));
    }),
    post: vi.fn(async (path: string, body: unknown) => {
      H.posts.push({ path, body });
      return H.postAnswer;
    }),
    put: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
  };
});

vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import type React from "react";
import { ImprovementsInbox } from "@/components/improvements/ImprovementsInbox";
import { NOTICED_ROW_TITLE, toActivity } from "@/components/NotificationBell";
import { decodeInbox, doorNav, noticeDoor, proposalSourceWords } from "@/lib/improvements";
import type { IJEvent } from "@/lib/types";

const ev = (type: string, payload: Record<string, unknown>): IJEvent =>
  ({ id: `e_${Math.random()}`, type, ts: "2026-11-27T03:00:00", session_id: null, payload }) as IJEvent;

const DOOR = "/chat?new=1&folder=D%3A%5CClients%5CHartleys";

function act(label: string, path: string, more: Record<string, unknown> = {}) {
  return { label, method: "POST", path, body: {}, note: "", confirm: false, blocked: "", ...more };
}

const NOTICED = {
  id: "noticed:prop_1",
  kind: "noticed",
  ref: "prop_1",
  title: "“Check the duplicate invoice” has been open for 9 days in Hartleys. Pick it up?",
  about: "An open item that has waited",
  why: "An open item in Hartleys has waited 9 days.",
  evidence: ["From: Bank reconciliation (chat, Nov 18, 2026)", "Open since Nov 18, 2026",
             "No work in Hartleys since Nov 18, 2026"],
  created_at: "2026-11-27T10:00:00",
  detail: null,
  accept: act("Yes", "/proposals/prop_1/approve", { note: "Opens a new chat in Clients › Hartleys. Nothing starts until you send." }),
  decline: act("No", "/proposals/prop_1/reject", { note: "Iron Jarvis won't suggest this again." }),
  stop: act("Stop suggesting this kind", "/proposals/prop_1/reject", {
    body: { stop_kind: true },
    note: "Iron Jarvis stops suggesting open items that have waited a while. You can turn them back on in Settings › Automation.",
  }),
  home: { href: "/chat?thread=t1", label: "Open where it lives" },
  extra: { notice_kind: "open_item", how: "chat", door: DOOR },
};

function inboxOf(items: unknown[]) {
  return {
    items,
    groups: items.length ? [{ kind: "noticed", label: "Things Iron Jarvis noticed in your work", count: items.length, shown: items.length }] : [],
    total: items.length,
    unreadable: [],
  };
}

let went: string[] = [];
const realGo = doorNav.go;

beforeEach(() => {
  H.inbox = inboxOf([NOTICED]);
  H.posts = [];
  H.postAnswer = { status: "ok" };
  went = [];
  doorNav.go = (href: string) => {
    went.push(href);
  };
});
afterEach(() => {
  cleanup();
  doorNav.go = realGo;
  vi.clearAllMocks();
});

describe("the bell", () => {
  it("a suggestion from the record is a quiet row opening Improvements, in its own words", () => {
    const row = toActivity(
      ev("autonomy.proposed", { source: "notice", kind: "open_item", proposal_id: "prop_1", title: NOTICED.title }),
    )!;
    expect(row.title).toBe(NOTICED_ROW_TITLE);
    expect(row.body).toBe(NOTICED.title);
    expect(row.href).toBe("/improvements");
    expect(row.quiet).toBe(true);
    expect(row.attention).toBeFalsy();
  });

  it("a watcher's suggestion keeps the watcher's name and opens Improvements now", () => {
    const row = toActivity(
      ev("autonomy.proposed", { source: "notice", kind: "new_files", sentinel: "scans", title: "Your watcher scans saw 2 new or changed files in Intake" }),
    )!;
    expect(row.title).toBe("Your watcher scans noticed changes · Review");
    expect(row.href).toBe("/improvements");
    expect(row.quiet).toBe(true);
  });
});

describe("Improvements", () => {
  it("lists it with its facts and three presses", async () => {
    render(<ImprovementsInbox />);
    const card = await screen.findByTestId("improvement-noticed:prop_1");
    expect(within(card).getByText(NOTICED.title)).toBeTruthy();
    const facts = within(card).getByTestId("improvement-evidence");
    expect(facts.textContent).toContain("From: Bank reconciliation (chat, Nov 18, 2026)");
    expect(within(card).getByTestId("improvement-accept").textContent).toBe("Yes");
    expect(within(card).getByTestId("improvement-decline").textContent).toBe("No");
    expect(within(card).getByTestId("improvement-stop").textContent).toBe("Stop suggesting this kind");
  });

  it("Yes opens a new chat in that folder, from the route's own door", async () => {
    H.postAnswer = { status: "opened", door: DOOR, session_id: null, note: "A new chat opens in that folder. Nothing starts until you send." };
    render(<ImprovementsInbox />);
    const card = await screen.findByTestId("improvement-noticed:prop_1");
    fireEvent.click(within(card).getByTestId("improvement-accept"));
    await waitFor(() => expect(went).toEqual([DOOR]));
    expect(H.posts).toEqual([{ path: "/proposals/prop_1/approve", body: {} }]);
    expect(await screen.findByText("A new chat opens in that folder. Nothing starts until you send.")).toBeTruthy();
  });

  it("No is a plain reject and opens nothing", async () => {
    render(<ImprovementsInbox />);
    const card = await screen.findByTestId("improvement-noticed:prop_1");
    fireEvent.click(within(card).getByTestId("improvement-decline"));
    await waitFor(() => expect(H.posts).toEqual([{ path: "/proposals/prop_1/reject", body: {} }]));
    expect(went).toEqual([]);
  });

  it("Stop suggesting this kind posts stop_kind and says what it turned off", async () => {
    H.postAnswer = { status: "rejected", stopped_kind: "open_item", note: "Iron Jarvis won't suggest open items that have waited a while again. Turn them back on in Settings › Automation." };
    render(<ImprovementsInbox />);
    const card = await screen.findByTestId("improvement-noticed:prop_1");
    fireEvent.click(within(card).getByTestId("improvement-stop"));
    await waitFor(() => expect(H.posts).toEqual([{ path: "/proposals/prop_1/reject", body: { stop_kind: true } }]));
    expect(await screen.findByText(/won't suggest open items that have waited a while again/)).toBeTruthy();
    expect(went).toEqual([]);
  });

  it("a door of any other shape is never opened", async () => {
    expect(noticeDoor({ door: DOOR })).toBe(DOOR);
    expect(noticeDoor({ door: "https://example.test/x" })).toBe("");
    expect(noticeDoor({ door: "//evil.test/chat?new=1&folder=a" })).toBe("");
    expect(noticeDoor({ door: "/chat?new=1&folder=a&ask=send%20it" })).toBe("");
    expect(noticeDoor({ door: "/settings" })).toBe("");
    expect(noticeDoor(null)).toBe("");
  });

  it("a Stop whose address is not a suggestion route is dropped", () => {
    const bad = { ...NOTICED, stop: act("Stop suggesting this kind", "/settings") };
    const [it] = decodeInbox(inboxOf([bad])).items;
    expect(it.stop).toBeNull();
    const [good] = decodeInbox(inboxOf([NOTICED])).items;
    expect(good.stop?.body).toEqual({ stop_kind: true });
  });

  it("the Autonomy page names where a suggestion came from in plain words, never the engine's", async () => {
    expect(proposalSourceWords("notice")).toBe("Noticed in your work");
    expect(proposalSourceWords("sentinel")).toBe("Watcher");
    expect(proposalSourceWords("deliberation")).toBe("From your goals");
    expect(proposalSourceWords("some_new_engine_name")).toBe("");
    expect(proposalSourceWords(undefined)).toBe("");
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const page = readFileSync(join(__dirname, "..", "app", "autonomy", "page.tsx"), "utf8").replace(/\r\n/g, "\n");
    expect(page).not.toMatch(/>\{p\.source\}</);
    expect(page).toContain("proposalSourceWords(p.source)");
  });
});
