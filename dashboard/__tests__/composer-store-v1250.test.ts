/**
 * The composer store (v1.250.0, S-05).
 *
 * What these pin is not "a setter sets": it is the two properties that make
 * the store cheaper than the page state it replaces.
 *   - a write that changes NOTHING notifies nobody and keeps the same object,
 *     so a caret event that lands on the same offset (keyup after an ordinary
 *     character fires one) costs no render at all;
 *   - `get()` is stable by identity between real changes, which is the only
 *     reason `useSyncExternalStore` can subscribe the textarea without
 *     re-rendering it on every unrelated notification.
 * A store that allocated on every call would pass a naive "setter sets" test
 * and put the per-keystroke render back one layer down.
 */
import { describe, expect, it, vi } from "vitest";
import { createComposerStore } from "@/lib/composerStore";

describe("composer store", () => {
  it("starts empty", () => {
    const s = createComposerStore();
    expect(s.get()).toEqual({
      text: "",
      caret: 0,
      slashDismissed: false,
      atDismissed: false,
      skillIndex: 0,
    });
  });

  it("a keystroke lands text + caret together and reopens the / dropdown", () => {
    const s = createComposerStore();
    s.setSlashDismissed(true);
    const seen = vi.fn();
    s.subscribe(seen);

    s.type("summarise /rep", 14);

    expect(s.get().text).toBe("summarise /rep");
    expect(s.get().caret).toBe(14);
    expect(s.get().slashDismissed).toBe(false); // editing reopens it
    expect(seen).toHaveBeenCalledTimes(1); // ONE notification, not three
  });

  it("programmatic text puts the caret at the end unless told otherwise", () => {
    const s = createComposerStore();
    s.setText("draft the reply");
    expect(s.get().caret).toBe("draft the reply".length);
    s.setText("draft the reply", 5);
    expect(s.get().caret).toBe(5);
  });

  it("skillIndex takes a functional update (the ↑↓ handlers move relatively)", () => {
    const s = createComposerStore();
    s.setSkillIndex(3);
    s.setSkillIndex((i) => i + 1);
    expect(s.get().skillIndex).toBe(4);
    s.setSkillIndex((i) => Math.max(i - 2, 0));
    expect(s.get().skillIndex).toBe(2);
  });

  it("a write that changes nothing notifies nobody and keeps the SAME object", () => {
    const s = createComposerStore();
    s.type("hello", 5);
    const before = s.get();
    const seen = vi.fn();
    s.subscribe(seen);

    s.setCaret(5); // the caret is already 5 — a keyup after a plain character
    s.type("hello", 5); // the same text at the same offset
    s.setSlashDismissed(false);
    s.setSkillIndex((i) => i);

    expect(seen).not.toHaveBeenCalled();
    expect(s.get()).toBe(before); // identity held: no subscriber re-renders
  });

  it("a real change replaces the object exactly once per change", () => {
    const s = createComposerStore();
    const first = s.get();
    const seen = vi.fn();
    s.subscribe(seen);

    s.setCaret(2);
    const second = s.get();
    expect(second).not.toBe(first);
    expect(seen).toHaveBeenCalledTimes(1);

    s.setAtDismissed(true);
    expect(s.get()).not.toBe(second);
    expect(seen).toHaveBeenCalledTimes(2);
  });

  it("reset clears everything a sent message should not leave behind", () => {
    const s = createComposerStore();
    s.type("half a thought", 14);
    s.setSkillIndex(4);
    s.setAtDismissed(true);

    s.reset();

    expect(s.get()).toEqual({
      text: "",
      caret: 0,
      slashDismissed: false,
      atDismissed: false,
      skillIndex: 0,
    });
  });

  it("unsubscribe really stops the notifications", () => {
    const s = createComposerStore();
    const seen = vi.fn();
    const off = s.subscribe(seen);
    s.setCaret(1);
    off();
    s.setCaret(2);
    expect(seen).toHaveBeenCalledTimes(1);
  });
});
