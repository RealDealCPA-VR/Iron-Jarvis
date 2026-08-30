/**
 * THE CHAT MODULE FILLS THE APP (v1.215.0).
 *
 * Reported: "I want the threads card on the left to span the height of the
 * screen in a similar manner to how you did it in the agents module. We can
 * also put the chat title in the card on the left just like in agents. As for
 * the buttons that sit above the chat window to the right, they can be
 * contained in the chat card at the top right and fixed so scrolling doesnt
 * remove them, then the cards and be pushed up making the chat seem more
 * minimalistic just like the agents module."
 *
 * MEASURED IN A REAL BROWSER against a production build, 1440x900:
 *
 *   room / rail / chat card   all y=56, bottom=884   (three columns, one line)
 *   main scrolls              false                  (only the panes do)
 *   <h1>                      "Chat", inside the rail, count 1
 *   controls                  in the card, not in the transcript, y=57
 *   after scrolling 3546px    controls y=57, composer y=793, both visible
 *   narrow (430px)            no horizontal overflow, no auto-scroll
 *
 * THIS FILE PINS THE SOURCE, which is the house idiom for this page and worth
 * restating: the chat page is the app's largest component and no suite renders
 * it (see chat-consent-v1192, approval-mode-v1188). The behaviour above was
 * verified by driving the real app; what is defended here is the structure
 * that produced those numbers, because every one of them is one careless
 * class-string away from silently regressing.
 *
 * THE DEFECT THIS FOUND, worth keeping because it is invisible from the JSX:
 * `<Card pad={false}>` wraps its children in an unstyled `<div>` (`ui.tsx`).
 * Declaring `flex flex-col` on the Card therefore gives the column exactly ONE
 * child — that wrapper — which sizes to its content. Measured before the fix:
 * an 828px card whose content stopped at 740, with 144px of dead space under
 * the composer. The card is a `card-surface` section now, so the header, the
 * transcript and the composer are direct children and `flex-1` has something
 * to divide.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// LINE ENDINGS NORMALISED. The working tree is CRLF on this machine, so a
// pin written against a bare newline matches nothing and passes for free —
// the same "green for the wrong reason" this file exists to prevent.
const LF = String.fromCharCode(10);
const CRLF = String.fromCharCode(13) + LF;
const SRC = readFileSync(resolve(process.cwd(), "app/chat/page.tsx"), "utf-8")
  .split(CRLF)
  .join(LF);

/** Source with block and line comments stripped — the file explains itself at
 *  length, and a rule must not pass because a comment happens to mention it. */
const CODE = SRC.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

describe("the module fills the app", () => {
  it("the row is the height of the window, and stretches its columns", () => {
    expect(CODE).toContain('data-testid="chat-room"');
    expect(CODE).toMatch(/md:h-\[calc\(100vh-4\.5rem\)\]/);
    // `items-stretch`, not `items-start`: three columns can only take the
    // row's height if the row lets them.
    expect(CODE).toContain("md:items-stretch");
    expect(CODE).not.toContain("md:items-start");
  });

  it("all three columns take that height", () => {
    // The thread rail, the conversation column, and the project panel.
    expect(CODE).toMatch(/md:block md:h-full md:w-60/); // threads
    expect(CODE).toMatch(/md:h-full md:w-\[var\(--rail-w\)\]/); // project panel
    expect(CODE).toMatch(/flex min-w-0 flex-1 flex-col gap-3 md:min-h-0/); // chat
    // The project panel's body was pinned to `60vh`, which ended it somewhere
    // other than where the other two ended.
    expect(CODE).not.toContain("md:h-[60vh]");
  });
});

describe("the title moved into the thread card", () => {
  it("renders ModuleTitle, not a PageHeader", () => {
    expect(CODE).toContain("ModuleTitle");
    expect(CODE).not.toContain("<PageHeader");
    expect(SRC).not.toContain('import { PageHeader }');
  });

  it("puts it inside the rail, above the Threads caption", () => {
    const rail = CODE.indexOf('data-testid="chat-thread-rail"');
    const title = CODE.indexOf('title="Chat"');
    const caption = CODE.indexOf("Threads\n");
    expect(rail).toBeGreaterThan(-1);
    expect(title).toBeGreaterThan(rail);
    expect(caption).toBeGreaterThan(title);
  });

  it("hands it the module's one description", () => {
    expect(CODE).toContain("const CHAT_HINT =");
    expect(CODE).toMatch(/hint=\{CHAT_HINT\}/);
    // The standing blurb that used to print under the header is gone; its
    // unique content (drop files anywhere) lives in the hint.
    expect(CODE).not.toContain("Answers come back in seconds");
    expect(SRC).toMatch(/drop them anywhere on the page/);
  });
});

describe("the controls are in the card, and cannot be scrolled away", () => {
  it("the card is a flex column of DIRECT children, not a <Card>", () => {
    // See the header: Card's inner wrapper div would be the column's only
    // child and would size to its content.
    expect(CODE).toContain('data-testid="chat-card"');
    expect(CODE).toMatch(
      /card-surface relative flex h-full min-h-0 flex-col overflow-hidden/,
    );
    // No <Card> left in this file at all — the rails are sections too.
    expect(CODE).not.toContain("<Card");
  });

  it("the controls sit in a shrink-0 header ABOVE the transcript", () => {
    const bar = CODE.indexOf("{chatActions}");
    const transcript = CODE.indexOf("ref={scrollRef}");
    expect(bar).toBeGreaterThan(-1);
    expect(transcript).toBeGreaterThan(bar);
    const header = CODE.slice(CODE.lastIndexOf("<div", bar), bar);
    expect(header).toContain("shrink-0");
    expect(header).toContain("justify-end"); // "at the top right"
    // Not sticky: there is no scroll to stick against, and a sticky header
    // inside an overflow-hidden card with a backdrop-filter is the kind of
    // thing this codebase has been bitten by.
    expect(header).not.toContain("sticky");
  });

  it("the transcript is the only thing in the card that scrolls", () => {
    const transcript = CODE.indexOf("ref={scrollRef}");
    const cls = CODE.slice(transcript, transcript + 400);
    expect(cls).toMatch(/min-h-0 flex-1[\s\S]*overflow-y-auto/);
    // The guessed viewport fraction is gone at md and up: it left dead space
    // under short conversations and a second scrollbar under long ones.
    expect(cls).toContain("md:max-h-none");
    expect(CODE).not.toContain("min-h-[24rem]");
  });
});

describe("one door to a new chat", () => {
  it("the rail carries it and the card's controls do not", () => {
    // The duplication the user called out in the Agents module, arriving here
    // the moment both surfaces were on screen at once.
    // Matched as a BUTTON LABEL, not as the two words. A trailing
    // `// "New chat" happened mid-flight` comment survives the line-comment
    // strip — that regex only takes comments which START a line, and widening
    // it would eat every `https://` in the file. A label cannot be a comment.
    const doors = CODE.match(/New chat\s*<\/button>/g) ?? [];
    expect(doors).toHaveLength(1);
    const rail = CODE.indexOf('data-testid="chat-thread-rail"');
    const card = CODE.indexOf('data-testid="chat-card"');
    const door = CODE.search(/New chat\s*<\/button>/);
    expect(door).toBeGreaterThan(rail);
    expect(door).toBeLessThan(card);
  });
});

describe("the conditional panels moved in with the conversation", () => {
  it("offline and the persona editor are inside the column, not above the row", () => {
    // Left in the page flow, either one appearing would push a 100vh-tall
    // layout off the bottom of the window.
    const row = CODE.indexOf('data-testid="chat-room"');
    expect(CODE.indexOf("{offline && (")).toBeGreaterThan(row);
    expect(CODE.indexOf("{personaEditorOpen && (")).toBeGreaterThan(row);
  });
});
