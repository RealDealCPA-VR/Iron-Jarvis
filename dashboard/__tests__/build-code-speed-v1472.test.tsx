/**
 * v1.472.0: Build › Code does only the new work per streamed frame.
 *
 * - The fold is RESUMABLE (lib/buildApp foldConversationFrom): fed one more
 *   event at a time, it must give exactly what a whole fold of the same list
 *   gives — checked after every event of every recorded scenario of all six
 *   tools — and fold each event ONCE (it folded the whole session again on
 *   every frame, quadratic over a long reply).
 * - A list that is not the old one plus more folds whole (a read again, the
 *   echo of a question replacing the drawn one), and a helper's helper that
 *   becomes known late folds whole too.
 * - applyEvents copies the list once per frame, not once per event.
 * - A streaming reply parses only its tail; the panes are not drawn again
 *   for a streamed word.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import {
  decodeCodeEvent,
  foldConversation,
  foldConversationFrom,
  type CodeEvent,
  type FoldMemo,
} from "@/lib/buildApp";
import { applyEvents } from "@/components/buildCode/BuildCodeView";
import { CodeConversation } from "@/components/buildCode/CodeConversation";

afterEach(() => cleanup());

function scenarios(file: string): Record<string, CodeEvent[]> {
  const raw = JSON.parse(readFileSync(join(__dirname, "fixtures", file), "utf8").replace(/\r\n/g, "\n")) as {
    scenarios: Record<string, unknown[]>;
  };
  const out: Record<string, CodeEvent[]> = {};
  for (const [name, evs] of Object.entries(raw.scenarios)) {
    out[name] = evs.map((e) => decodeCodeEvent(e)).filter((e): e is CodeEvent => !!e);
  }
  return out;
}

const FILES = [
  "build-app-events-v1409.json",
  "build-app-events-v1411.json",
  "build-app-events-v1413.json",
  "build-app-events-v1416.json",
  "build-app-events-v1439.json",
];

/** Feed `events` one at a time the way the page does (a new list each time,
 *  the same event objects), checking each step against a whole fold. */
function feedAndCompare(events: readonly CodeEvent[]): FoldMemo | null {
  let memo: FoldMemo | null = null;
  let list: CodeEvent[] = [];
  for (const ev of events) {
    list = [...list, ev];
    const step = foldConversationFrom(memo, list);
    memo = step.memo;
    expect(step.conversation).toEqual(foldConversation(list));
  }
  return memo;
}

describe("the resumable fold", () => {
  for (const file of FILES) {
    it(`matches a whole fold after every event of ${file}`, () => {
      const all = scenarios(file);
      expect(Object.keys(all).length).toBeGreaterThan(5);
      for (const evs of Object.values(all)) feedAndCompare(evs);
    });
  }

  it("folds each event once, however long the reply", () => {
    const evs: CodeEvent[] = [
      decodeCodeEvent({ type: "user", at: "2026-11-11T10:00:00Z", text: "go", files: [] })!,
    ];
    for (let i = 0; i < 2000; i += 1) {
      evs.push(decodeCodeEvent({ type: "text", at: "2026-11-11T10:00:01Z", item: "t1", delta: `w${i} ` })!);
    }
    let memo: FoldMemo | null = null;
    let list: CodeEvent[] = [];
    let firstState: unknown = null;
    for (const ev of evs) {
      list = [...list, ev];
      memo = foldConversationFrom(memo, list).memo;
      firstState ??= memo!.state;
    }
    // One fold all along, never started over...
    expect(memo!.state).toBe(firstState);
    // ...and every event folded exactly once (a whole fold per frame would be
    // 2001·2002/2 ≈ two million).
    expect(memo!.state.version).toBe(evs.length);
    const text = foldConversationFrom(memo, list).conversation.items.find((it) => it.kind === "text");
    expect(text && text.kind === "text" ? text.text.startsWith("w0 w1 ") : false).toBe(true);
  });

  it("folds whole when the list is not the old one plus more", () => {
    const all = scenarios("build-app-events-v1409.json");
    const evs = Object.values(all).find((x) => x.length > 6)!;
    const first = foldConversationFrom(null, evs.slice(0, 5));
    // The echo of a question takes the place of the drawn one: a new object
    // in the middle of the list.
    const swapped = [...evs.slice(0, 5)];
    swapped[2] = { ...swapped[2] } as CodeEvent;
    const again = foldConversationFrom(first.memo, swapped);
    expect(again.conversation).toEqual(foldConversation(swapped));
    expect(again.memo!.state).not.toBe(first.memo!.state);
  });

  it("folds whole when a helper's helper becomes known after its events", () => {
    const at = "2026-11-11T10:00:00Z";
    const ev = (o: Record<string, unknown>) => decodeCodeEvent({ at, ...o })!;
    const base = { name: "Agent", input: "", output: "", path: "", line: null, diff: "", status: "running" };
    const evs = [
      ev({ type: "user", text: "go", files: [] }),
      ev({ type: "tool", item: "h1", kind: "task", title: "Helper one", ...base }),
      // A step of h2 arrives before h2's own row says h2 sits in h1.
      ev({ type: "tool", item: "s1", kind: "read", title: "Read a.py", parent: "h2", ...base }),
      ev({ type: "tool", item: "h2", kind: "task", title: "Helper two", parent: "h1", ...base }),
      ev({ type: "text", item: "t1", delta: "done" }),
    ];
    expect(evs.every(Boolean)).toBe(true);
    expect(evs[2].type === "tool" && evs[2].parent).toBe("h2");
    const memo = feedAndCompare(evs);
    // The late helper broke event-by-event folding (the frame that brought
    // it folded whole and kept nothing)...
    const upTo = foldConversationFrom(foldConversationFrom(null, evs.slice(0, 3)).memo, evs.slice(0, 4));
    expect(upTo.memo).toBeNull();
    expect(upTo.conversation).toEqual(foldConversation(evs.slice(0, 4)));
    // ...and the next frame starts over and still matches.
    const more = [...evs, ev({ type: "text", item: "t1", delta: " now" })];
    expect(foldConversationFrom(memo, more).conversation).toEqual(foldConversation(more));
  });
});

describe("applyEvents", () => {
  it("adds a burst in order, puts the question's echo in place, drops a twice-settled ask", () => {
    const at = "2026-11-11T10:00:00Z";
    const optimistic = decodeCodeEvent({ type: "user", at, text: "hi", files: [] })!;
    const prev: CodeEvent[] = [optimistic];
    const echo = decodeCodeEvent({ type: "user", at, text: "hi", files: [] })!;
    const r1 = decodeCodeEvent({ type: "resolved", at, request_id: "a1", decision: "allow" })!;
    const r2 = decodeCodeEvent({ type: "resolved", at, request_id: "a1", decision: "allow" })!;
    const t = decodeCodeEvent({ type: "text", at, item: "t", delta: "x" })!;
    const out = applyEvents(prev, [echo, t, r1, r2], { current: optimistic });
    expect(out).toEqual([echo, t, r1]);
    expect(out[0]).toBe(echo);
    expect(prev).toEqual([optimistic]);
    expect(applyEvents(prev, [], { current: null })).toBe(prev);
  });
});

describe("a streaming reply", () => {
  it("draws the settled part and the tail, and the whole reply once it is done", () => {
    const text = "First paragraph, settled.\n\nSecond, still being written";
    const live = { kind: "text" as const, key: "t-1", text, done: false };
    const view = render(<CodeConversation items={[live]} folder="" />);
    const box = screen.getByTestId("code-text");
    expect(box.textContent).toContain("First paragraph, settled.");
    expect(box.textContent).toContain("Second, still being written");
    expect(box.querySelectorAll("p")).toHaveLength(2);
    view.rerender(<CodeConversation items={[{ ...live, done: true }]} folder="" />);
    expect(screen.getByTestId("code-text").querySelectorAll("p")).toHaveLength(2);
  });
});
