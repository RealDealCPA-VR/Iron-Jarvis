/**
 * v1.473.0: a long Build › Code session draws only its newest rows.
 *
 * - lib/codeWindow.windowStart: about ROW_WINDOW rows before the end, moved
 *   back to the start of a message when one is near, nothing folded for a
 *   handful of rows.
 * - CodeConversation draws from `from`; a reply's Copy still copies the
 *   whole reply (joined only when pressed, replyEndKeys == the old copy
 *   texts' keys on every recorded scenario).
 */
import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const copied = vi.hoisted(() => ({ texts: [] as string[] }));
vi.mock("@/components/settings/MaintenanceTools", () => ({
  copyPlain: async (t: string) => {
    copied.texts.push(t);
    return "copied";
  },
}));

import { decodeCodeEvent, foldConversation, type CodeEvent, type ConvItem } from "@/lib/buildApp";
import { MIN_HIDDEN, ROW_WINDOW, SNAP_ROWS, hiddenMessages, windowStart } from "@/lib/codeWindow";
import { CodeConversation, replyCopyTexts, replyEndKeys } from "@/components/buildCode/CodeConversation";

afterEach(() => {
  cleanup();
  copied.texts.length = 0;
});

const user = (n: number): ConvItem => ({ kind: "user", key: `u-${n}`, text: `q${n}`, files: [], at: `2026-11-11T10:${String(n % 60).padStart(2, "0")}:00Z` });
const text = (n: number, t = `answer ${n}`): ConvItem => ({ kind: "text", key: `t-${n}`, text: t, done: true });
const note = (n: number): ConvItem => ({ kind: "notice", key: `n-${n}`, text: `note ${n}` });

describe("windowStart", () => {
  it("folds nothing for a short conversation", () => {
    const items = Array.from({ length: ROW_WINDOW + MIN_HIDDEN - 1 }, (_, i) => note(i));
    expect(windowStart(items)).toBe(0);
  });

  it("cuts about one window before the end, at the start of a message when one is near", () => {
    const items: ConvItem[] = [];
    // Seven rows a turn, so the plain cut (one window before the end) is NOT
    // on a message and has to move back to one.
    for (let t = 0; t < 100; t += 1) items.push(user(t), ...[1, 2, 3, 4, 5].map((k) => note(t * 10 + k)), text(t));
    expect(items[items.length - ROW_WINDOW].kind).not.toBe("user");
    const from = windowStart(items);
    expect(items[from].kind).toBe("user");
    expect(items.length - from).toBeGreaterThanOrEqual(ROW_WINDOW);
    expect(items.length - from).toBeLessThanOrEqual(ROW_WINDOW + SNAP_ROWS);
    // The window before that, for "Show earlier".
    const earlier = windowStart(items, from);
    expect(earlier).toBeLessThan(from);
    expect(items[earlier].kind).toBe("user");
  });

  it("cuts exactly when one long reply has no message near the cut", () => {
    const items: ConvItem[] = [user(0), ...Array.from({ length: 1000 }, (_, i) => note(i))];
    expect(windowStart(items)).toBe(items.length - ROW_WINDOW);
    expect(hiddenMessages(items, items.length - ROW_WINDOW)).toBe(1);
  });
});

describe("Copy, joined only when pressed", () => {
  const FILES = ["build-app-events-v1409.json", "build-app-events-v1411.json", "build-app-events-v1413.json",
    "build-app-events-v1416.json", "build-app-events-v1439.json"];
  it("marks exactly the rows the old copy texts were kept for", () => {
    for (const f of FILES) {
      const raw = JSON.parse(readFileSync(join(__dirname, "fixtures", f), "utf8").replace(/\r\n/g, "\n")) as {
        scenarios: Record<string, unknown[]>;
      };
      for (const evs of Object.values(raw.scenarios)) {
        const items = foldConversation(evs.map((e) => decodeCodeEvent(e)).filter((e): e is CodeEvent => !!e)).items;
        expect([...replyEndKeys(items)].sort()).toEqual([...replyCopyTexts(items).keys()].sort());
      }
    }
  });

  it("copies the whole reply, parts drawn and parts folded away", async () => {
    // One reply in two parts; the first part sits above the first row drawn.
    const items: ConvItem[] = [user(1), text(1, "part one"), note(2), text(2, "part two")];
    render(<CodeConversation items={items} from={2} folder="" />);
    expect(screen.queryByText("part one")).toBeNull();
    expect(screen.getByText("part two")).toBeTruthy();
    fireEvent.click(screen.getByTestId("code-reply-copy"));
    await waitFor(() => expect(copied.texts).toEqual(["part one\n\npart two"]));
  });

  it("draws only the rows from `from`", () => {
    const items: ConvItem[] = [];
    for (let t = 0; t < 10; t += 1) items.push(user(t), text(t));
    render(<CodeConversation items={items} from={14} folder="" />);
    expect(screen.getAllByTestId("code-user")).toHaveLength(3);
    expect(screen.getAllByTestId("code-text")).toHaveLength(3);
  });
});
