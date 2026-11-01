/**
 * v1.422.0 (Wave 2, B3) REVIEW: the line under a reply a restart cut off
 * says WHY it did not carry on by itself. The daemon's reasons are phrases
 * ("a newer message was sent") — except when the coding tool's own refusal
 * said why: then it is a whole sentence ("This conversation is open in a Build
 * terminal — …."), which read "because This … here.." before.
 */

import { describe, expect, it } from "vitest";
import { cutWords } from "@/lib/buildCodeCarry";

describe("cutWords", () => {
  it("follows 'because' with a reason phrase", () => {
    expect(cutWords({ turn: "t1", resumable: true, whyNot: "a newer message was sent", waiting: false }))
      .toBe("It did not carry on by itself because a newer message was sent.");
  });

  it("never puts a whole sentence after 'because' (no capital mid-sentence, no double stop)", () => {
    const sentence =
      "This conversation is open in a Build terminal — exit Claude Code there first (type /exit), then send your message here.";
    const got = cutWords({ turn: "t1", resumable: true, whyNot: sentence, waiting: false });
    expect(got).not.toMatch(/because This/);
    expect(got).not.toMatch(/\.\.$/);
    expect(got).toBe(`It did not carry on by itself: ${sentence}`);
  });
});
