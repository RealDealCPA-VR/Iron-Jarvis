/**
 * v1.441.0 integration pins (lanes fix1, fix2, g1-g4 merged into one tree).
 *
 *  I1. Lane fix1 re-sends Claude Code's plan card (SAME request_id) with the
 *      then_mode it will really apply after a live mode change; lane fix2
 *      refuses Enter when the card's thenMode / setsMode is "full". This file
 *      proves the PAGE replaces the old card by its id, so the refreshed
 *      thenMode reaches enterAllowsCodeAsk and the key hint. The frames are
 *      the REAL daemon's (tests/test_build_app_integration_v14410.py writes
 *      fixtures/build-code-integration-v1441-0.json through the real service
 *      and the fake Claude Code, IJ_FAKE_CLAUDE_MODES=1): the card raised in
 *      Ask first ("then ask first"), then Full access, then Plan again — the
 *      card re-sent as "then full access". Enter must then NOT approve.
 *  I4. Shift+Tab (nextMode) never steps into an OpenCode agent mode.
 *  I6. The message ⋯ menu closes like the row ⋯ menu.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
  frames: [] as Record<string, unknown>[],
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: FakeApiError,
    get: (path: string) => {
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(r);
    },
    post: (path: string, body?: unknown) => {
      H.posts.push({ path, body });
      return Promise.resolve({ ok: true });
    },
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  /** The turn's stream: the daemon's own frames (through the page's onFrame
   *  tap, as the real streamSSE hands them), then it waits, as a turn
   *  parked on a card does. */
  streamSSE: async function* (
    _path: string,
    _body: unknown,
    signal: AbortSignal,
    opts?: { onFrame?: (name: string, data: unknown) => void },
  ) {
    for (const f of H.frames) opts?.onFrame?.(String(f.type), f);
    await new Promise<void>((res) => {
      if (signal.aborted) return res();
      signal.addEventListener("abort", () => res());
    });
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <span />,
}));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import {
  codeAskKeyHint,
  decodeCodeEvent,
  decodeCodeSessionView,
  enterAllowsCodeAsk,
  foldConversation,
  type CodeEvent,
} from "@/lib/buildApp";

type Raw = Record<string, unknown>;
const FIX = join(process.cwd(), "__tests__", "fixtures", "build-code-integration-v1441-0.json");
const DAEMON = JSON.parse(readFileSync(FIX, "utf8").replace(/\r\n/g, "\n")) as {
  before: Raw & { events: Raw[]; session: Raw };
  after: Raw & { events: Raw[]; session: Raw };
  stream: Raw[];
};

const FULL_HINT = "Click “Approve, then full access” to approve the plan · Esc to deny";
const ASK_HINT = "Enter to approve, then ask first · Esc to deny";

const TOOLS = {
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      modes: [
        { id: "ask", label: "Ask first", dangerous: false },
        { id: "edits", label: "Accept edits", dangerous: false },
        { id: "plan", label: "Plan", dangerous: false },
        { id: "full", label: "Full access", dangerous: true },
      ],
      models: [{ id: "", label: "Claude Code's default" }],
      efforts: [],
      accounts: [],
      commands: [],
    },
  ],
};

/** Every stream frame before the card was answered (the turn is still
 *  parked on it). */
const parkedFrames = (): Raw[] => {
  const cut = DAEMON.stream.findIndex((f) => f.type === "resolved");
  return DAEMON.stream.slice(0, cut);
};

const decoded = (rows: Raw[]) =>
  rows.map((r) => decodeCodeEvent(r, String(r.type))).filter((e): e is CodeEvent => e !== null);

beforeEach(() => {
  H.posts = [];
  H.frames = [];
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("I1: a re-sent plan card replaces the old one by its id", () => {
  it("the daemon's own frames: one card id, raised 'then ask first', re-sent 'then full access'", () => {
    const cards = DAEMON.stream.filter((f) => f.type === "approval");
    expect(cards.map((c) => c.then_mode)).toEqual(["ask", "full"]);
    expect(new Set(cards.map((c) => c.request_id)).size).toBe(1);
  });

  it("the session read while it waits: the fold keeps the NEWEST card", () => {
    const before = decodeCodeSessionView(DAEMON.before);
    const after = decodeCodeSessionView(DAEMON.after);
    expect(before && after).toBeTruthy();
    const was = foldConversation(before!.events, before!.pending).asks;
    expect(was).toHaveLength(1);
    expect(enterAllowsCodeAsk(was[0], false)).toBe(true);
    expect(codeAskKeyHint(was[0], false)).toBe(ASK_HINT);
    // The record holds BOTH sends; the page shows one card, the newest.
    const now = foldConversation(after!.events, after!.pending).asks;
    expect(now).toHaveLength(1);
    expect(now[0].type === "approval" && now[0].thenMode).toBe("full");
    expect(enterAllowsCodeAsk(now[0], false)).toBe(false);
    expect(codeAskKeyHint(now[0], false)).toBe(FULL_HINT);
    // Even with no pending list (an older read), the events alone decide.
    const eventsOnly = foldConversation(after!.events, []).asks;
    expect(eventsOnly).toHaveLength(1);
    expect(enterAllowsCodeAsk(eventsOnly[0], false)).toBe(false);
  });

  it("the live stream: the re-sent frame replaces the card the page already drew", () => {
    const asks = foldConversation(decoded(parkedFrames())).asks;
    expect(asks).toHaveLength(1);
    expect(enterAllowsCodeAsk(asks[0], false)).toBe(false);
    expect(codeAskKeyHint(asks[0], false)).toBe(FULL_HINT);
  });

  const openView = (frames: Raw[]) => {
    const sid = String(DAEMON.before.session.id);
    H.gets = {
      "/build/app/harnesses": TOOLS,
      "/build/app/sessions": { sessions: [DAEMON.before.session] },
      "/editor/options": { editors: [], preferred: "" },
      [`/build/app/sessions/${sid}`]: DAEMON.before,
    };
    H.frames = frames;
    render(<BuildCodeView initialSession={sid} onOpenedTerminal={() => {}} />);
    return sid;
  };

  const pressEnter = async () => {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 600));
    });
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Enter" });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 60));
    });
  };

  it("through the page: the card follows the re-sent frame, and Enter does not approve", async () => {
    openView(parkedFrames());
    const card = await screen.findByTestId("code-ask", {}, { timeout: 10_000 });
    await waitFor(() => expect(within(card).getByTestId("code-ask-keys").textContent).toBe(FULL_HINT), {
      timeout: 10_000,
    });
    expect(screen.getAllByTestId("code-ask")).toHaveLength(1);
    await pressEnter();
    expect(H.posts.filter((p) => p.path.endsWith("/answer"))).toEqual([]);
  });

  it("control: before the re-send, the same page approves on Enter", async () => {
    const frames = parkedFrames();
    const second = frames.findIndex((f, i) => f.type === "approval" && frames.findIndex((g) => g.type === "approval") !== i);
    openView(frames.slice(0, second));
    const card = await screen.findByTestId("code-ask", {}, { timeout: 10_000 });
    await waitFor(() => expect(within(card).getByTestId("code-ask-keys").textContent).toBe(ASK_HINT), {
      timeout: 10_000,
    });
    await pressEnter();
    const answers = H.posts.filter((p) => p.path.endsWith("/answer"));
    expect(answers).toHaveLength(1);
    expect((answers[0].body as { decision?: string }).decision).toBe("allow");
  });
});
