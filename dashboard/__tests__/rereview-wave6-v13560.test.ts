/**
 * v1.356.0 re-review (G75): the chat header's Project button said
 * "Pick a project (or just a folder). Armed file tools run there" — the one
 * "arm" left on the chat page after the wave moved every other control to
 * Turn on / Turn off and every folder hover to "Jarvis works on files in that
 * folder". Source pin (the button lives in the ~14,000-line chat page; its
 * title is a plain string), CRLF-normalised at the reader.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

function read(rel: string): string {
  return readFileSync(join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");
}

describe("the chat's Project button speaks without 'armed'", () => {
  it("says what the folder is for, in the folder hovers' own words", () => {
    const page = read("app/chat/page.tsx");
    const at = page.indexOf('data-testid="chat-project-button"');
    expect(at).toBeGreaterThan(0);
    const button = page.slice(at, at + 1200);
    expect(button).toContain("Pick a project (or just a folder). Jarvis works on files in that folder.");
    expect(button).not.toMatch(/\barmed\b/i);
  });

  it("no user-facing 'Armed' / 'Disarm' string is left on the chat page", () => {
    const page = read("app/chat/page.tsx");
    const visible = page
      .split("\n")
      .filter((l) => !/^\s*(\/\/|\*|\/\*|\{\/\*)/.test(l))
      .join("\n");
    // One line at a time: a multi-line JSX comment is not a string.
    expect(visible).not.toMatch(/["`>][^"`<\n]*\b(Armed|Disarm)\b[^"`<\n]*["`<]/);
  });
});
