/**
 * 48-b (master plan R2.14; idea 48 "Scroll back forever") — an old
 * ROUND-TABLE's page scrolls back too.
 *
 * A room keeps its newest 400 messages live and, from this release, MOVES the
 * rest to its older messages instead of cutting them (`agents/threads._append`).
 * Its page (`/agents?thread=<id>`, where a history hit on a round-table and
 * chat's "open in Agents" link land) reads `GET /agents/threads/{id}`, which
 * says how many (`older`), and pages `GET /agents/threads/{id}/older`:
 *  - opening a long room makes ONE room read and NO older read, and the
 *    chat's own quiet "Earlier messages" line sits at its top; the page opens
 *    at its top, so the line being in view reads nothing: a press loads;
 *  - a press loads the 50 before, in order, above the live messages, drawn
 *    like them; a second press the 50 before those;
 *  - the top then says "Start of this conversation", or "Earlier messages
 *    were not kept before this version" when the daemon says the first kept
 *    message may not be the room's first (`start_kept`);
 *  - a failed read says so and offers Try again, never an empty top;
 *  - a room with nothing older draws no earlier block.
 *
 * Harness: the real RoomTranscript, only the transport (and the heavy
 * Markdown and face pieces) mocked.
 */
import React from "react";
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
    got: [] as string[],
    room: null as unknown,
    /** Answers every `/agents/threads/<id>/older?...` read. */
    older: null as null | ((before: number, limit: number) => unknown),
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  get: (path: string) => {
    api.got.push(path);
    const older = /^\/agents\/threads\/[^/]+\/older\?before=(-?\d+)&limit=(\d+)$/.exec(path);
    if (older && api.older) {
      const r = api.older(Number(older[1]), Number(older[2]));
      return r instanceof Error ? Promise.reject(r) : Promise.resolve(r);
    }
    if (/^\/agents\/threads\/[^/]+$/.test(path) && api.room) return Promise.resolve(api.room);
    return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
  },
}));
vi.mock("@/components/Markdown", () => ({ Markdown: ({ content }: { content: string }) => <div>{content}</div> }));
vi.mock("@/components/agents/AgentFace", () => ({ default: ({ name }: { name: string }) => <span>{name}</span> }));

import { RoomTranscript } from "@/components/agents/mission/RoomTranscript";

type Entry = Record<string, unknown>;

/** Message `i` of the room: the user, then the critic, in turn. */
function entry(i: number): Entry {
  return i % 2 === 0
    ? { who: "user", content: `asked ${i}`, at: "2026-10-05T10:00:00+00:00" }
    : { who: "builtin:critic", content: `answered ${i}`, at: "2026-10-05T10:01:00+00:00" };
}

const LIVE = 20;

function openRoom(older: number, startKept = true) {
  api.room = {
    id: "r1",
    title: "Pricing",
    project_id: "",
    participants: [{ key: "builtin:critic", source: "builtin", name: "critic", role: "critic" }],
    messages: Array.from({ length: LIVE }, (_, i) => entry(older + i)),
    message_count: LIVE,
    older,
  };
  api.older = (before, limit) => {
    const end = Math.max(0, Math.min(before, older));
    const start = Math.max(0, end - limit);
    const messages: Entry[] = [];
    for (let i = start; i < end; i++) messages.push(entry(i));
    return { id: "r1", total: older, offset: 0, first_seq: start, start_kept: startKept, messages };
  };
  return render(<RoomTranscript thread="r1" onRail={() => {}} />);
}

const olderReads = () => api.got.filter((p) => p.includes("/older"));
const roomReads = () => api.got.filter((p) => p === "/agents/threads/r1");
const said = () =>
  within(screen.getByTestId("room-transcript"))
    .getAllByTestId("room-entry")
    .map((e) => e.textContent ?? "");

// IntersectionObserver: jsdom has none. This one says at once that what it
// watches is in view (the page opens at its top: the line IS in view).
const reported: boolean[] = [];
class SeenIO {
  cb: IntersectionObserverCallback;
  constructor(cb: IntersectionObserverCallback) {
    this.cb = cb;
  }
  observe(el: Element) {
    reported.push(true);
    this.cb([{ target: el, isIntersecting: true } as unknown as IntersectionObserverEntry], this as unknown as IntersectionObserver);
  }
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

beforeEach(() => {
  api.got.length = 0;
  api.room = null;
  api.older = null;
  reported.length = 0;
  vi.stubGlobal("IntersectionObserver", SeenIO);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("scroll back in an old round-table (48-b)", () => {
  it("opens a long room with one room read and no older read, the line in view or not", async () => {
    openRoom(120);
    await screen.findByText("answered 139");
    expect(screen.getByTestId("older-more")).toHaveTextContent("Earlier messages");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    // The line was reported in view, and that read nothing.
    expect(reported.length).toBeGreaterThan(0);
    expect(roomReads()).toHaveLength(1);
    expect(olderReads()).toEqual([]);
    expect(said()).toHaveLength(LIVE);
  });

  it("a press loads the 50 before, in order, above the live messages and drawn like them", async () => {
    openRoom(120);
    await screen.findByText("answered 139");
    fireEvent.click(screen.getByTestId("older-more"));
    await screen.findByText("asked 70");
    expect(olderReads()).toEqual(["/agents/threads/r1/older?before=120&limit=50"]);
    const words = said();
    expect(words).toHaveLength(50 + LIVE);
    expect(words[0]).toContain("asked 70");
    expect(words[0]).toContain("You");
    expect(words[1]).toContain("critic");
    expect(words[49]).toContain("answered 119");
    expect(words[50]).toContain("asked 120");
    // Each message once.
    expect(new Set(words).size).toBe(words.length);
    // The earlier rows sit in the earlier block; the live ones do not.
    const block = screen.getByTestId("older-messages");
    expect(block.querySelectorAll('[data-older-seq] [data-testid="room-entry"]')).toHaveLength(50);
    // A second press: the 50 before those.
    fireEvent.click(screen.getByTestId("older-more"));
    await screen.findByText("asked 20");
    expect(olderReads()).toEqual([
      "/agents/threads/r1/older?before=120&limit=50",
      "/agents/threads/r1/older?before=70&limit=50",
    ]);
  });

  it("says 'Start of this conversation' once the first message is loaded", async () => {
    openRoom(30);
    await screen.findByText("answered 49");
    fireEvent.click(screen.getByTestId("older-more"));
    await screen.findByText("asked 0");
    const top = await screen.findByTestId("older-start");
    expect(top).toHaveTextContent("Start of this conversation");
    expect(screen.queryByTestId("older-more")).toBeNull();
    expect(said()).toHaveLength(30 + LIVE);
  });

  it("never claims the start of a room whose first messages may not have been kept", async () => {
    openRoom(30, false);
    await screen.findByText("answered 49");
    fireEvent.click(screen.getByTestId("older-more"));
    await screen.findByText("asked 0");
    const top = await screen.findByTestId("older-start");
    expect(top).toHaveTextContent("Earlier messages were not kept before this version");
    expect(top).not.toHaveTextContent("Start of this conversation");
  });

  it("a failed read says so and offers Try again, never an empty top", async () => {
    openRoom(120);
    await screen.findByText("answered 139");
    const real = api.older;
    api.older = () => new api.ApiError("the daemon is busy", 503);
    fireEvent.click(screen.getByTestId("older-more"));
    const failed = await screen.findByTestId("older-failed");
    expect(failed).toHaveTextContent("Could not load earlier messages.");
    expect(said()).toHaveLength(LIVE);
    api.older = real;
    fireEvent.click(within(failed).getByText("Try again"));
    await screen.findByText("asked 70");
    expect(screen.queryByTestId("older-failed")).toBeNull();
  });

  it("a room with nothing older draws no earlier block", async () => {
    openRoom(0);
    await screen.findByText("answered 19");
    await waitFor(() => expect(said()).toHaveLength(LIVE));
    expect(screen.queryByTestId("older-messages")).toBeNull();
    expect(screen.queryByTestId("older-more")).toBeNull();
  });
});
