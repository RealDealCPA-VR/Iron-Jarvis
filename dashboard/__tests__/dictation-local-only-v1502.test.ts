// v1.502.0 (north-star plan 2.12): dictation is the chat's own words, so a clip
// sent to the daemon carries the chat's Local only switch and its id. The
// daemon then refuses a speech service off this network by name
// (tests/test_north_star_voice_v1502.py pins that side).
//
// A source pin on purpose: the hook needs a microphone, a MediaRecorder and an
// audio graph to run, none of which jsdom has. What must not regress is the
// two lines below. CI checks out with CRLF, so line ends are normalized.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

function source(rel: string): string {
  return readFileSync(resolve(__dirname, "..", rel), "utf-8").replace(/\r\n/g, "\n");
}

describe("dictation carries the chat's Local only posture", () => {
  it("sends local_only and thread_id with every clip", () => {
    const hook = source("lib/useDictation.ts");
    const call = hook.slice(hook.indexOf('post<{ text: string }>("/voice/transcribe"'));
    const body = call.slice(0, call.indexOf("});"));
    expect(body).toContain("local_only: chatRef.current?.localOnly === true");
    expect(body).toContain('thread_id: chatRef.current?.threadId || ""');
  });

  it("the chat page hands its switch and its chat to the one dictation engine", () => {
    const page = source("app/chat/page.tsx");
    expect(page).toContain('useDictation("en-US", { localOnly, threadId })');
    // One engine for the composer mic and Voice Chat: never a second, bare one.
    expect(page.match(/useDictation\(/g)?.length).toBe(1);
  });
});
