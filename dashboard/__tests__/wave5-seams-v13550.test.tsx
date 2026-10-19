/**
 * v1.355.0 integrate — Wave 5's seams, fed with the REAL daemon's words
 * (`fixtures/wave5-seams-v13550.json`, written by
 * tests/test_integrate_wave5_v13550.py; regenerate with
 * IJ_REGEN_WAVE5_SEAMS=1):
 *
 *  (a) G29 x G28 — one failed turn, both lanes: the stream's error frame goes
 *      through the REAL `useChatStream` (run() over a mocked SSE fetch) and
 *      the POST lane's 502 body through the REAL `ApiError` + the page's
 *      `postFailureFields`; both give the same headline, the same folded
 *      technical text and the same press (lib/failureActions).
 *  (c) G32 x G28 — a background job nothing answered: the banner says the
 *      sentence the job's own summary ends with.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, renderHook, screen } from "@testing-library/react";

const EV = vi.hoisted(() => ({ events: [] as unknown[] }));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: EV.events, connected: true }) }));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import { ApiError } from "@/lib/api";
import { useChatStream } from "@/lib/useChatStream";
import {
  failureSentence,
  postFailureFields,
  turnErrorTechnical,
  turnErrorText,
} from "@/lib/failureLine";
import { failureAction, failureFacts } from "@/lib/failureActions";
import { ProviderDowngradeBanner, downgradeNotice } from "@/components/ProviderDowngradeBanner";

const read = (p: string) => readFileSync(p, "utf-8").replace(/\r\n/g, "\n");
const SEAMS = JSON.parse(read(join(__dirname, "fixtures", "wave5-seams-v13550.json"))) as {
  signed_out: { stream: Record<string, string>; post: Record<string, string> };
  refused_key: { stream: Record<string, string>; post: Record<string, string> };
  banner: {
    event: { type: string; session_id: string; payload: Record<string, string> };
    run_summary: string;
  };
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  EV.events = [];
});

function sse(frame: Record<string, unknown>) {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(`event: error\ndata: ${JSON.stringify(frame)}\n\n`));
      controller.close();
    },
  });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 200 })));
}

async function streamFailure(frame: Record<string, unknown>): Promise<unknown> {
  sse(frame);
  const { result } = renderHook(() => useChatStream());
  let caught: unknown = null;
  await act(async () => {
    try {
      await result.current.run({ messages: [] });
    } catch (e) {
      caught = e;
    }
  });
  return caught;
}

describe("(a) one failed turn reads the same on the stream and the POST lane", () => {
  for (const [key, provider, kind] of [
    ["signed_out", "claude-cli", "sign_in"],
    ["refused_key", "openrouter", "key"],
  ] as const) {
    it(`${key}: the same headline, folded details and press on both lanes`, async () => {
      const { stream, post } = SEAMS[key];
      const se = await streamFailure(stream);
      expect(se).toBeTruthy();
      const viaPost = postFailureFields(new ApiError(post.detail, 502, false, post));

      // One sentence: the daemon's title + fix, never `detail` alone.
      const sentence = failureSentence(stream.title, stream.fix);
      expect(sentence).not.toBe("");
      expect(turnErrorText(se)).toBe(sentence);
      expect(turnErrorText(viaPost)).toBe(sentence);
      // The server's own words are the fold (hover / Details), on both lanes.
      expect(turnErrorTechnical(se)).toBe(stream.technical);
      expect(turnErrorTechnical(viaPost)).toBe(stream.technical);
      // One press.
      expect(failureFacts(viaPost)).toEqual(failureFacts(se));
      expect(failureFacts(se)).toEqual({ title: stream.title, fixKind: kind, fixProvider: provider });
      expect(failureAction(viaPost, null, "")?.kind).toBe(kind);
      expect(failureAction(se as { fixKind?: string; fixProvider?: string }, null, "")?.kind).toBe(kind);
    });
  }

  it("a POST failure with no words in its body keeps its message (an older daemon)", () => {
    const plain = postFailureFields(new ApiError("the provider's own detail", 502, false, { detail: "x" }));
    expect(turnErrorText(plain)).toBe("the provider's own detail");
    expect(turnErrorTechnical(plain)).toBe("");
    expect(failureAction(plain, null, "")).toBeNull();
    // Offline: no body at all.
    expect(turnErrorText(postFailureFields(new ApiError("Iron Jarvis isn't responding.", 0)))).toBe(
      "Iron Jarvis isn't responding.",
    );
  });
});

describe("(c) the banner says the background job's own sentence", () => {
  const { event, run_summary } = SEAMS.banner;

  it("downgradeNotice reads failure_words' title + fix, the run summary's words", () => {
    const n = downgradeNotice(event as never);
    expect(n?.kind).toBe("refused");
    const sentence = failureSentence(event.payload.title, event.payload.fix);
    expect(n?.detail).toBe(sentence);
    expect(run_summary).toBe(`The run failed: ${sentence}`);
    expect(n?.detail).not.toContain(event.payload.requested); // never the raw id
  });

  it("the banner on the page says it, with what was not affected and the job", () => {
    EV.events = [{ ...event, id: "ev1", created_at: "2026-10-19T10:00:00Z" }];
    render(<ProviderDowngradeBanner />);
    const text = document.body.textContent ?? "";
    expect(text).toContain(failureSentence(event.payload.title, event.payload.fix));
    expect(screen.getByTestId("provider-downgrade-still").textContent).toContain(
      "Nothing was changed in that step.",
    );
    expect(screen.getByTestId("provider-downgrade-job").getAttribute("href")).toContain("sess_wave5");
  });

  it("an older daemon's event (no title) keeps the reason clause", () => {
    const old = { ...event, payload: { ...event.payload, title: undefined, fix: undefined } };
    expect(downgradeNotice(old as never)?.detail).toBe("Office Spark: not connected.");
  });
});
