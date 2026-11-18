/**
 * v1.535.0 (fourth review C-DT-2, owner decision D5): deleting a chat stops
 * the reply it is writing (the daemon's delete_thread), so the armed Delete
 * says so when the chat has one running, in both delete menus.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { DELETE_ARMED_WORDS, DELETE_STOPS_REPLY_WORDS, deleteArmedWords } from "@/lib/deleteWords";

describe("the armed Delete says it stops a running reply", () => {
  it("adds the line only when a reply is running", () => {
    expect(deleteArmedWords(false)).toBe("Delete for good? Press again");
    expect(deleteArmedWords(true)).toBe(`${DELETE_ARMED_WORDS} ${DELETE_STOPS_REPLY_WORDS}`);
    expect(deleteArmedWords(true)).toContain("Deleting it also stops the reply it is writing.");
  });

  it("both delete menus use it, with the chat's own running state", () => {
    const page = readFileSync(join(__dirname, "..", "app", "chat", "page.tsx"), "utf-8");
    expect(page).toContain('{deleteArmedId === threadId ? deleteArmedWords(busy) : "Delete chat"}');
    expect(page).toContain('{deleteArmedId === mt.id ? deleteArmedWords(mt.running === true) : "Delete chat"}');
    expect(page).not.toContain('"Delete for good? Press again" : "Delete chat"');
  });
});
