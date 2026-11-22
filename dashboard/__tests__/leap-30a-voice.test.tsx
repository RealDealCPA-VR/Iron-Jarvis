/**
 * Idea 30-a with voice replies on (second review): a figure's link is never
 * read out, and the end of a streamed turn reads no sentence twice.
 *
 * The stream carries the model's plain words; the done frame carries the
 * same words with their figures linked ("[1,250](#source/src_…)"). The voice
 * counts what it already read in characters, so the last flush must be fed
 * the words without the links or it starts too early and reads the record's
 * id aloud. The real hook, with the browser's speech stubbed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => "tok",
  get: async () => ({}),
  post: async () => ({}),
  put: async () => ({}),
  del: async () => ({}),
}));

import { READ_ALOUD_STORAGE_KEY } from "@/lib/deviceSettings";
import { useTTS } from "@/lib/useTTS";

let spoken: string[] = [];

function stubSpeech() {
  const synth = {
    getVoices: () => [],
    addEventListener: () => {},
    removeEventListener: () => {},
    cancel: vi.fn(),
    speak: vi.fn((u: { text: string }) => void spoken.push(u.text)),
    pending: false,
  };
  Object.defineProperty(window, "speechSynthesis", { value: synth, configurable: true });
  (globalThis as unknown as { SpeechSynthesisUtterance: unknown }).SpeechSynthesisUtterance = class {
    constructor(public text: string) {}
  };
}

const PLAIN = "Total wages were 48,210.00 for the year. Rent was 1,250 in Q3.";
const LINKED =
  "Total wages were [48,210.00](#source/src_aaaa1111aaaa1111) for the year. " +
  "Rent was [1,250](#source/src_1c2d3e4f5a6b7c8d) in Q3.";

beforeEach(() => {
  spoken = [];
  window.localStorage.clear();
  window.localStorage.setItem(READ_ALOUD_STORAGE_KEY, "1"); // voice replies on
  stubSpeech();
});
afterEach(() => cleanup());

describe("voice replies and figure links (idea 30-a)", () => {
  it("the last flush of a streamed reply reads the rest once, with no link", () => {
    const { result } = renderHook(() => useTTS());
    expect(result.current.enabled).toBe(true);
    act(() => {
      result.current.resetStream();
      // Mid-stream: the model's plain words, the last sentence not yet ended.
      result.current.speakMore(PLAIN.slice(0, PLAIN.indexOf(" in Q3")), false);
    });
    const before = spoken.length;
    expect(before).toBeGreaterThan(0);
    act(() => result.current.speakMore(LINKED, true)); // the done frame's reply
    const flushed = spoken.slice(before);
    expect(flushed).toEqual(["Rent was 1,250 in Q3."]);
    expect(spoken.join(" ")).not.toContain("source");
    expect(spoken.join(" ").match(/the year/g)).toHaveLength(1);
  });

  it("a whole reply spoken at once reads the figures, never their links", () => {
    const { result } = renderHook(() => useTTS());
    act(() => result.current.speak(LINKED));
    expect(spoken.join(" ")).not.toContain("source");
    expect(spoken.join(" ")).toContain("Rent was 1,250 in Q3.");
  });
});
