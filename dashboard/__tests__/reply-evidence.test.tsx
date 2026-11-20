/**
 * R2.15 (phase 27-a, idea 27 "Show me it works"): before and after pictures
 * under a coding reply that changed a screen.
 *
 *  - the reply line's `evidence` is whitelisted (lib/replyPictures);
 *  - Before and After sit side by side on a wide screen and STACK at 390 px
 *    (one column below 640 px), each with its address and time, and open full
 *    screen; the daemon's own sentences ("The page looks the same before and
 *    after", why a picture is missing) are shown as sent;
 *  - D-27.1: the NEWEST reply, while it has no after picture, shows ONE quiet
 *    press, Show me (never an earlier reply: the page is pictured as it is
 *    now; never over an after it has), which posts the session's Browser
 *    address for that reply; the first one
 *    ever offers the automatic setting once, naming its cost, answered through
 *    the one settings writer; a refusal is the daemon's own sentence, nothing
 *    invented;
 *  - "show me it works" typed alone presses that same Show me.
 *
 * Drawn through the REAL HarnessMessageRow.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    calls: [] as { method: string; path: string; body?: unknown }[],
    answer: {} as unknown,
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const value = method === "POST" ? api.answer : {};
    if (value instanceof Error) return Promise.reject(value);
    return Promise.resolve(value ?? {});
  };
  return {
    ApiError: api.ApiError,
    API_BASE: "http://d",
    ijToken: () => "tok",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => answer("GET", p),
    post: (p: string, b?: unknown) => answer("POST", p, b),
    put: (p: string, b?: unknown) => answer("PUT", p, b),
    patch: (p: string, b?: unknown) => answer("PATCH", p, b),
    del: (p: string) => answer("DELETE", p),
  };
});

import { HarnessMessageRow, type HarnessRowHandlers } from "@/components/harness/HarnessMessageRow";
import type { BuildMessage } from "@/lib/buildSessions";
import { decodeMessage } from "@/lib/buildSessions";
import {
  GONE_WORDS,
  OFFER_WORDS,
  SHOW_ME,
  decodePictures,
  isShowMeOnly,
  pictureSrc,
  requestShowMe,
  showMeReply,
} from "@/lib/replyPictures";

const ROOT = join(__dirname, "..");
const src = (p: string) => readFileSync(join(ROOT, p), "utf8").replace(/\r\n/g, "\n");

const NOT_ASKED =
  'No before picture: one is taken only when your message asks for it ("show me it works") or when pictures by themselves are on.';
const BEFORE = { id: "before", label: "Before", where: "localhost:5173/cart", at: "2026-11-20T10:02:00+00:00", sha: "aaaaaaaaaaaaaaaa" };
const AFTER = { id: "after", label: "After", where: "localhost:5173/cart", at: "2026-11-20T10:03:00+00:00", sha: "bbbbbbbbbbbbbbbb" };
const MARK = { kind: "pictures", screen: true, items: [], note: "" };

const h: HarnessRowHandlers = {
  onRewound: () => {},
  onBranch: () => {},
  onAttemptKept: () => {},
  onKeepGoing: () => {},
  onRetry: () => {},
  onOpenRewind: () => {},
  onOpenDocument: () => {},
};

function reply(evidence: unknown, turnId = "t1"): BuildMessage {
  return {
    role: "assistant",
    content: "Moved the button.",
    turnId,
    at: "2026-11-20T10:03:00",
    route: { provider: "mock", model: "mock-1", reason: "mock" },
    evidence,
  } as BuildMessage;
}

function row(m: BuildMessage, extra: { busy?: boolean; archived?: boolean; newest?: boolean } = {}) {
  return render(
    <HarnessMessageRow
      m={m}
      index={1}
      threadId="s1"
      sessionId="build:s1"
      archived={!!extra.archived}
      busy={!!extra.busy}
      isPending={false}
      branching={null}
      newest={extra.newest ?? true}
      carriedOn={false}
      canRewind
      prevUserAt="2026-11-20T10:00:00"
      teamRoster={{ lead: "", workers: [], reviewer: "" } as never}
      models={[]}
      health={null as never}
      provider="mock"
      undoFor={() => null}
      onUndo={async () => {}}
      h={h}
    />,
  );
}

beforeEach(() => {
  api.calls = [];
  api.answer = {};
  window.localStorage.setItem("ij.build.preview.s1", "http://localhost:5173/cart?tab=2");
});
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("the line's pictures, whitelisted", () => {
  it("keeps the two known pictures in order and drops everything else", () => {
    const got = decodePictures({
      kind: "pictures",
      screen: true,
      note: "n",
      folder: "C:\\home\\reply-evidence\\s1\\t1",
      items: [AFTER, { id: "evil", label: "x" }, { ...BEFORE, label: "Whatever", sha: "<script>aa" }, AFTER],
    });
    expect(got).toEqual({
      items: [
        { ...BEFORE, sha: "scriptaa".replace(/[^0-9a-f]/gi, "") },
        AFTER,
      ],
      note: "n",
      folder: "C:\\home\\reply-evidence\\s1\\t1",
    });
    expect(decodePictures({ kind: "pictures", items: [] })).toBeNull(); // never marked
    expect(decodePictures(null)).toBeNull();
    // The session's own line decoder keeps the key.
    expect(decodeMessage({ role: "assistant", content: "x", evidence: MARK })?.evidence).toEqual(MARK);
  });

  it("loads a picture from its own reply, with the token in the query and the hash", () => {
    expect(pictureSrc("s 1", "t1", AFTER as never)).toBe(
      "http://d/build/sessions/s%201/evidence/t1/after?v=bbbbbbbbbbbbbbbb&token=tok",
    );
  });
});

describe("before and after under the reply", () => {
  it("side by side on a wide screen, stacked at 390 px, with address and time, and no Show me", () => {
    row(reply({ kind: "pictures", screen: true, items: [BEFORE, AFTER], note: "" }));
    const strip = screen.getByTestId("reply-pictures-strip");
    // One column below 640 px (a phone at 390 px), two from there.
    expect(strip.className).toContain("grid-cols-1");
    expect(strip.className).toContain("sm:grid-cols-2");
    const pics = within(strip).getAllByRole("img");
    expect(pics.map((p) => p.getAttribute("alt"))).toEqual(["Before: localhost:5173/cart", "After: localhost:5173/cart"]);
    expect(pics[0].getAttribute("src")).toContain("/build/sessions/s1/evidence/t1/before?v=aaaaaaaaaaaaaaaa");
    expect(screen.getByTestId("reply-picture-caption-before").textContent).toMatch(/^Before · localhost:5173\/cart · \d/);
    expect(screen.queryByTestId("reply-pictures-show-me")).toBeNull();
    // A press opens it full screen; Left/Right walk the two.
    fireEvent.click(screen.getByTestId("reply-picture-open-after"));
    const box = screen.getByTestId("media-lightbox");
    expect(within(box).getByTestId("media-lightbox-count").textContent).toBe("2 of 2");
    expect(within(box).queryByTestId("media-lightbox-open")).toBeNull(); // no address with the token leaves
  });

  it("says the daemon's own sentence when the page looks the same", () => {
    row(reply({ kind: "pictures", screen: true, items: [BEFORE, AFTER], note: "The page looks the same before and after." }));
    expect(screen.getByTestId("reply-pictures-note").textContent).toBe("The page looks the same before and after.");
  });

  it("a picture that is no longer kept says so, never a broken image", () => {
    row(reply({ kind: "pictures", screen: true, items: [BEFORE, AFTER], note: "" }));
    fireEvent.error(within(screen.getByTestId("reply-picture-before")).getByRole("img"));
    expect(screen.getByTestId("reply-picture-gone-before").textContent).toBe(GONE_WORDS);
    expect(within(screen.getByTestId("reply-picture-after")).getByRole("img")).toBeTruthy();
  });

  it("a reply that changed no screen shows nothing at all", () => {
    row(reply(undefined));
    expect(screen.queryByTestId("reply-pictures")).toBeNull();
  });
});

describe("Show me (only when you ask)", () => {
  it("is one quiet press under a reply that changed a screen; it posts the Browser's address for that reply", async () => {
    api.answer = { evidence: { kind: "pictures", screen: true, items: [AFTER], note: NOT_ASKED }, offer: false };
    row(reply(MARK));
    expect(screen.queryByTestId("reply-pictures-strip")).toBeNull(); // nothing taken unasked
    const btn = screen.getByTestId("reply-pictures-show-me");
    expect(btn.textContent).toBe(SHOW_ME);
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByTestId("reply-picture-after")).toBeTruthy());
    expect(api.calls).toEqual([
      { method: "POST", path: "/build/sessions/s1/evidence", body: { turn_id: "t1", preview_url: "http://localhost:5173/cart?tab=2" } },
    ]);
    expect(screen.getByTestId("reply-pictures-note").textContent).toBe(NOT_ASKED);
    expect(screen.queryByTestId("reply-pictures-show-me")).toBeNull(); // it has its after now
    expect(screen.queryByTestId("reply-pictures-offer")).toBeNull(); // not offered: the daemon said no
  });

  it("the first one offers the automatic setting once, naming its cost, answered by the settings writer", async () => {
    api.answer = { evidence: { kind: "pictures", screen: true, items: [AFTER], note: NOT_ASKED }, offer: true };
    row(reply(MARK));
    fireEvent.click(screen.getByTestId("reply-pictures-show-me"));
    const offer = await screen.findByTestId("reply-pictures-offer");
    expect(offer.textContent).toContain(OFFER_WORDS);
    expect(OFFER_WORDS).toContain("invisible browser");
    expect(OFFER_WORDS).toContain("twice");
    fireEvent.click(screen.getByTestId("reply-pictures-offer-yes"));
    await waitFor(() => expect(screen.getByTestId("reply-pictures-offer-done").textContent).toBe(
      "Turned on. Settings can turn it off.",
    ));
    expect(api.calls.at(-1)).toEqual({ method: "PUT", path: "/settings/values", body: { values: { reply_pictures: true } } });
  });

  it("Not now is the setting off, and says Show me stays", async () => {
    api.answer = { evidence: { kind: "pictures", screen: true, items: [AFTER], note: "" }, offer: true };
    row(reply(MARK));
    fireEvent.click(screen.getByTestId("reply-pictures-show-me"));
    fireEvent.click(await screen.findByTestId("reply-pictures-offer-no"));
    await waitFor(() => expect(screen.getByTestId("reply-pictures-offer-done").textContent).toBe(
      "Not turned on. Show me is always here.",
    ));
    expect(api.calls.at(-1)).toEqual({ method: "PUT", path: "/settings/values", body: { values: { reply_pictures: false } } });
  });

  it("a refusal is the daemon's own sentence, and no picture is drawn", async () => {
    api.answer = new api.ApiError("Open the page in the Browser first, then press Show me.", 409);
    row(reply(MARK));
    fireEvent.click(screen.getByTestId("reply-pictures-show-me"));
    const err = await screen.findByTestId("reply-pictures-error");
    expect(err.textContent).toBe("Open the page in the Browser first, then press Show me.");
    expect(err.getAttribute("role")).toBe("alert");
    expect(screen.queryByTestId("reply-pictures-strip")).toBeNull();
    expect(screen.getByTestId("reply-pictures-show-me")).toBeTruthy(); // still there to try again
  });

  it("waits while a reply runs, and is not offered in an archived session", () => {
    const busy = row(reply(MARK), { busy: true });
    expect((screen.getByTestId("reply-pictures-show-me") as HTMLButtonElement).disabled).toBe(true);
    busy.unmount();
    row(reply(MARK), { archived: true });
    expect(screen.queryByTestId("reply-pictures-show-me")).toBeNull();
  });

  it("a reply whose before was taken but whose after failed still offers Show me", () => {
    row(reply({ kind: "pictures", screen: true, items: [BEFORE], note: "No after picture: the page did not answer in time." }));
    expect(screen.getByTestId("reply-picture-before")).toBeTruthy();
    expect(screen.getByTestId("reply-pictures-show-me")).toBeTruthy();
  });

  it("is never offered under an earlier reply: it pictures the page as it is now", () => {
    // Review (blocking): an earlier reply's After would show the later
    // replies' changes as its own.
    row(reply(MARK), { newest: false });
    expect(screen.getByTestId("reply-pictures")).toBeTruthy();
    expect(screen.queryByTestId("reply-pictures-show-me")).toBeNull();
    expect(requestShowMe("s1", "t1")).toBe(false); // the typed words do not reach it either
    expect(api.calls).toEqual([]);
  });

  it("a reply that has its after picture keeps it: no press, typed or not", () => {
    row(reply({ kind: "pictures", screen: true, items: [BEFORE, AFTER], note: "" }));
    expect(screen.queryByTestId("reply-pictures-show-me")).toBeNull();
    expect(requestShowMe("s1", "t1")).toBe(false);
    expect(api.calls).toEqual([]);
  });
});

describe('"show me it works" typed alone', () => {
  it("reads only the words alone as the press", () => {
    for (const yes of ["show me it works", "Show me it works!", "please show me that it works", "show me before and after."]) {
      expect(isShowMeOnly(yes)).toBe(true);
    }
    for (const no of ["make it blue and show me it works", "show me the code", "it works", ""]) {
      expect(isShowMeOnly(no)).toBe(false);
    }
  });

  it("presses Show me under the newest reply, only when it changed a screen and has no after", async () => {
    api.answer = { evidence: { kind: "pictures", screen: true, items: [AFTER], note: "" }, offer: false };
    const user = { role: "user", content: "x" };
    const carryOn = { role: "user", content: "carry on", continuation: true };
    expect(showMeReply([reply(MARK, "t0"), user, reply(MARK, "t1")] as never)).toBe("t1");
    // The daemon's own hidden "carry on" question is not drawn, so the reply
    // before it is still the one that ends the conversation on screen.
    expect(showMeReply([user, reply(MARK, "t1"), carryOn] as never)).toBe("t1");
    // A later reply that changed no screen: the earlier one is not the newest.
    expect(showMeReply([reply(MARK, "t1"), user, reply(undefined, "t2")] as never)).toBeNull();
    // A question after it (stopped before a word): no reply ends the conversation.
    expect(showMeReply([reply(MARK, "t1"), user] as never)).toBeNull();
    // The newest has its after picture: kept, nothing to press.
    expect(showMeReply([user, reply({ ...MARK, items: [AFTER] }, "t1")] as never)).toBeNull();
    expect(showMeReply([])).toBeNull();
    row(reply(MARK, "t1"));
    expect(requestShowMe("s1", "t9")).toBe(false); // not on screen: an ordinary message
    expect(requestShowMe("s1", "t1")).toBe(true);
    await waitFor(() => expect(screen.getByTestId("reply-picture-after")).toBeTruthy());
    expect(api.calls[0].path).toBe("/build/sessions/s1/evidence");
  });

  it("the session's box hands the words to that press, and to nothing else", () => {
    const s = src("components/harness/HarnessSession.tsx");
    const at = s.indexOf("isShowMeOnly(text)");
    expect(at).toBeGreaterThan(0);
    const block = s.slice(at - 200, at + 400);
    expect(block).toContain("fromBox && !command && !picked");
    expect(block).toContain("requestShowMe(threadId, turn)");
    // After the busy check: a note while it works stays a note.
    expect(s.indexOf("if (text) await steer(text);")).toBeLessThan(at);
  });
});
