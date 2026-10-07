// v1.309.0 review — LiveMarkdown (the mission screen's live report) and chat's
// StreamingText are a LOCK-STEP PAIR. The mission screen cannot import
// StreamingText (it lives inside app/chat/page.tsx, a page module), so it
// keeps its own copy; this pin fails when either copy changes alone — the
// settled/tail split, the two renderers, or the caret classes.
//
// When StreamingText moves into components/chat/ (the finding's plan for a
// later wave), delete LiveMarkdown, import the shared one, and delete this pin.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");
/** CRLF-normalised at the READER (the Windows CI runner checks out with
 *  autocrlf — the v1.232.1 rule). */
const read = (rel: string) => readFileSync(join(ROOT, rel), "utf8").replace(/\r\n/g, "\n");

/** The body of `function <name>(` up to the next top-level `\n}\n`. */
function fnBody(src: string, name: string): string {
  const start = src.indexOf(`function ${name}(`);
  expect(start, `function ${name} not found`).toBeGreaterThanOrEqual(0);
  const end = src.indexOf("\n}\n", start);
  expect(end).toBeGreaterThan(start);
  return src.slice(start, end + 2);
}

/** Every `[&>*:last-child]:after:` class, in order. */
function caretClasses(text: string): string[] {
  return text.match(/\[&>\*:last-child\]:after:[^\s"']+(?:'')?\]?/g) ?? [];
}

const chat = read("app/chat/page.tsx");
const mission = read("components/agents/mission/MissionOutput.tsx");
const streaming = fnBody(chat, "StreamingText");
const live = fnBody(mission, "LiveMarkdown");

describe("LiveMarkdown stays in lock-step with chat's StreamingText", () => {
  it("both cut at settledSplit and render the settled head memoised, the tail live", () => {
    for (const [name, body] of [
      ["StreamingText", streaming],
      ["LiveMarkdown", live],
    ] as const) {
      expect(body, name).toContain("const cut = settledSplit(content);");
      expect(body, name).toContain("{cut > 0 && <MemoMarkdown content={content.slice(0, cut)} />}");
      expect(body, name).toContain("<Markdown content={cut > 0 ? content.slice(cut) : content} />");
    }
  });

  it("the caret classes are identical", () => {
    const chatCaret = caretClasses(streaming);
    // LiveMarkdown names its caret through STREAMING_CARET_CLASS
    expect(live).toContain("STREAMING_CARET_CLASS");
    const decl = mission.match(/const STREAMING_CARET_CLASS =\s*\n?\s*"([^"]+)";/);
    expect(decl, "STREAMING_CARET_CLASS declaration").not.toBeNull();
    const missionCaret = caretClasses(decl![1]);
    // anti-vacuity: the pattern really finds the caret (a dozen classes)
    expect(chatCaret.length).toBeGreaterThanOrEqual(10);
    expect(missionCaret).toEqual(chatCaret);
  });
});
