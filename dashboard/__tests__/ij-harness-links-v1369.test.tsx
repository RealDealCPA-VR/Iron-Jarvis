/**
 * A Build session opens in Build (v1.369.0, IJ harness plan Phase 6).
 *
 * An IJ harness session's calls, findings and events carry the session id
 * `build:<thread id>`. Before this release every row holding one linked
 * `/sessions/build:<id>` (a page that does not exist for it), and a Build
 * session waiting on an approval read "A chat is waiting" and opened
 * `/chat?thread=`, where a Build session never shows. Pinned here, through
 * each surface's own mapper:
 *
 *   - lib/buildLinks (the one rule) and lib/missionLinks (its caller);
 *   - the bell: the waiting row (`surface: "build"`), a trust / context row,
 *     and Phase 5's `build.account_limit` / `build.resumed` events;
 *   - Activity's safety findings and the time-travel feed;
 *   - the reply's receipt naming the account a turn ran on.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  BUILD_SESSION_EVENT,
  announceBuildSessionLink,
  buildLimitHeadline,
  buildSessionHref,
  buildThreadOf,
  isBuildSessionId,
  sessionLinkFor,
  sessionOfEvent,
  sessionOfHref,
} from "@/lib/buildLinks";
import { sessionHref } from "@/lib/missionLinks";
import { chatAskHref, chatAskRowWords, parseChatAsk, toActivity } from "@/components/NotificationBell";
import { findingLink } from "@/components/SafetyChecks";
import { TurnReceipt, accountWords } from "@/components/chat/TurnReceipt";
import type { IJEvent } from "@/lib/types";

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

function ev(type: string, payload: Record<string, unknown>, session_id: string | null = null): IJEvent {
  return { id: `e-${type}`, type, ts: "2026-10-21T12:00:00Z", session_id, payload } as unknown as IJEvent;
}

describe("the one rule", () => {
  it("a build:<id> session id opens Build on that session; any other its run page", () => {
    expect(buildSessionHref("bs 1")).toBe("/chat?view=code&session=bs%201");
    expect(buildThreadOf("build:bs_1")).toBe("bs_1");
    expect(buildThreadOf("build:")).toBeNull();
    expect(buildThreadOf("sess_1")).toBeNull();
    expect(isBuildSessionId("build:bs_1")).toBe(true);
    expect(sessionLinkFor("build:bs_1")).toBe("/chat?view=code&session=bs_1");
    expect(sessionLinkFor("sess_1")).toBe("/sessions/sess_1");
  });

  it("missionLinks routes a Build session's row to Build too", () => {
    expect(sessionHref({ id: "build:bs_1" })).toBe("/chat?view=code&session=bs_1");
    expect(sessionHref({ id: "sess_1" })).toBe("/sessions/sess_1");
  });
});

describe("the bell", () => {
  it("a Build session waiting for an OK reads as the session and opens it in Build", () => {
    const ask = parseChatAsk({ id: "chat:bs_1", kind: "chat", thread_id: "bs_1", surface: "build" })!;
    expect(ask.build).toBe(true);
    expect(chatAskHref(ask)).toBe("/chat?view=code&session=bs_1");
    expect(chatAskRowWords(ask)).toEqual({
      title: "A coding session is waiting for your OK",
      open: "Open the session",
    });
    // A chat's row keeps its words and its address.
    const chat = parseChatAsk({ id: "chat:c1", kind: "chat", thread_id: "c1" })!;
    expect(chat.build).toBeUndefined();
    expect(chatAskHref(chat)).toBe("/chat?thread=c1");
    expect(chatAskRowWords(chat).title).toBe("A chat is waiting for your OK");
  });

  it("a trust or context row of a Build session links to Build, never /sessions/build:…", () => {
    expect(toActivity(ev("trust.lowered", { session_id: "build:bs_1", reason: "flagged" }))!.href).toBe(
      "/chat?view=code&session=bs_1",
    );
    expect(toActivity(ev("context.blocked", { count: 1, source: "a page" }, "build:bs_1"))!.href).toBe(
      "/chat?view=code&session=bs_1",
    );
  });

  it("an account limit counts on the badge, names the account and opens the session", () => {
    const item = toActivity(
      ev("build.account_limit", { thread_id: "bs_1", title: "Fix the nav", account_name: "Work", kind: "usage" }, "build:bs_1"),
    )!;
    expect(item).toMatchObject({
      href: "/chat?view=code&session=bs_1",
      title: "The Work account reached its limit",
      body: "Fix the nav",
      attention: true,
    });
    // Not a reset: its own words.
    expect(toActivity(ev("build.account_limit", { account_name: "Work", kind: "signin" }, "build:bs_1"))!.title).toBe(
      "The Work account needs to sign in again",
    );
  });

  it("a resume is good news, said once, and does not count on the badge", () => {
    const item = toActivity(ev("build.resumed", { account_name: "Work", title: "Fix the nav" }, "build:bs_1"))!;
    expect(item.href).toBe("/chat?view=code&session=bs_1");
    expect(item.title).toBe("A coding session resumed on the Work account");
    expect(item.attention).toBeUndefined();
  });

  it("the headline's other kinds", () => {
    expect(buildLimitHeadline("missing", "Work")).toBe("The Work account was removed");
    expect(buildLimitHeadline("disabled", "")).toBe("A coding session's account is turned off");
    expect(buildLimitHeadline("busy", "Work")).toBe("The Work account is busy right now");
  });
});

describe("Activity", () => {
  it("a safety finding in a Build session opens the session in Build", () => {
    expect(findingLink("ironjarvis", "build:bs_1")).toEqual({
      href: "/chat?view=code&session=bs_1",
      label: "Open the coding session",
    });
    expect(findingLink("ironjarvis", "chat:t1")).toEqual({ href: "/chat", label: "Open Chat" });
  });

  it("the time-travel feed links a session through the same rule", () => {
    const src = readFileSync(join(process.cwd(), "components", "TimeTravelFeed.tsx"), "utf8").replace(/\r\n/g, "\n");
    expect(src).toContain('import { sessionLinkFor } from "@/lib/buildLinks";');
    expect(src).toContain("href={sessionLinkFor(e.session_id)}");
    expect(src).not.toMatch(/href=\{`\/sessions\/\$\{e\.session_id\}`\}/);
  });
});

describe("the receipt names the account a Build turn ran on", () => {
  it("quiet, beside who answered; the note on hover when earlier thinking was left out", () => {
    expect(accountWords({ account: { id: "work", name: "Work" } })).toEqual({ text: "on the Work account", note: "" });
    expect(accountWords({ account: null })).toBeNull();
    expect(accountWords({ account: { id: "x", name: " " } })).toBeNull();
    render(
      <TurnReceipt
        route={{
          provider: "claude-cli",
          model: "opus",
          reason: "default",
          requested: "",
          account: { id: "work", name: "Work", note: "Sent again without the earlier thinking." },
        }}
      />,
    );
    expect(screen.getByTestId("turn-account")).toHaveTextContent("on the Work account");
    expect(screen.getByTestId("turn-account-note")).toHaveAttribute("title", "Sent again without the earlier thinking.");
  });

  it("a chat reply (no account) says nothing about one", () => {
    render(<TurnReceipt route={{ provider: "claude-cli", model: "opus", reason: "default", requested: "" }} />);
    expect(screen.queryByTestId("turn-account")).toBeNull();
  });
});

describe("a session link pressed while Build is already open (v1.369.0 review)", () => {
  // Next keeps the page's state on a same-page navigation that only changes
  // the query: the address said the session, the screen did not. The press
  // announces it instead.
  it("reads the session off a Build link, and nothing else", () => {
    expect(sessionOfHref(buildSessionHref("bs 1"))).toBe("bs 1");
    expect(sessionOfHref("/terminals?focus=t1")).toBeNull();
    expect(sessionOfHref("/sessions/x?session=y")).toBeNull();
    expect(sessionOfHref("/chat?thread=t")).toBeNull();
    expect(sessionOfHref(null)).toBeNull();
  });

  // v1.406.0 (part 1, moved on purpose): the sessions are the chat page's
  // Code view, so a link is announced on the CHAT page, never on Build.
  it("announces only on the chat page, with the session's id", () => {
    const seen: (string | null)[] = [];
    const on = (e: Event) => seen.push(sessionOfEvent(e));
    window.addEventListener(BUILD_SESSION_EVENT, on);
    try {
      window.history.replaceState(null, "", "/terminals");
      expect(announceBuildSessionLink(buildSessionHref("bs_1"))).toBe(false);
      window.history.replaceState(null, "", "/chat?view=code&session=bs_0");
      expect(announceBuildSessionLink("/terminals?focus=t1")).toBe(false);
      expect(announceBuildSessionLink(buildSessionHref("bs_1"))).toBe(true);
    } finally {
      window.removeEventListener(BUILD_SESSION_EVENT, on);
    }
    expect(seen).toEqual(["bs_1"]);
  });

  it("the bell's dropdown announces a pressed Build row (one capture handler for every row)", () => {
    const src = readFileSync(join(process.cwd(), "components", "NotificationBell.tsx"), "utf8").replace(/\r\n/g, "\n");
    const open = src.indexOf("{open && (");
    expect(open).toBeGreaterThan(-1);
    // v1.406.0 review (moved on purpose): the handler reads the href once and
    // also announces a chat-page view link (chat-code-review-v1406).
    const head = src.slice(open, open + 1500);
    expect(head).toMatch(/onClickCapture=\{\(e\) => \{[\s\S]*closest\?\.\("a"\)[\s\S]*const href = a\.getAttribute\("href"\)[\s\S]*announceBuildSessionLink\(href\)/);
  });
});
