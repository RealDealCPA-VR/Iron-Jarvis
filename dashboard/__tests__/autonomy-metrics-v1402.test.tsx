/**
 * v1.402.0 part A — how hands-off Jarvis is, the dashboard half.
 *
 * Pinned here:
 *  - lib/autonomyMetrics: the answer is decoded defensively and the three
 *    numbers are said in plain words ("Asked you 1.4 times per task (0.6
 *    could have been avoided)", "You nudged it 0.3 times per finished task",
 *    "62% of tasks finished with no help from you"); the system's own nudges
 *    are one quiet line, absent when there were none;
 *  - HandsOffCard: renders those lines from GET /autonomy/metrics, an empty
 *    line for no tasks, a "How this is counted" disclosure, and NOTHING at
 *    all on a 404 (an older daemon) or any other failed read;
 *  - the Autonomy page mounts it;
 *  - mergeContinuation counts the cut-off reply's Continue presses on the
 *    merged reply (`continuedPresses`), so the press is not lost.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return { FakeApiError, gets: [] as string[], responses: {} as Record<string, unknown> };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  wsUrl: (p: string) => p,
  sseUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  api: () => Promise.resolve({}),
  get: (path: string) => {
    api.gets.push(path);
    const r = api.responses[path];
    if (r instanceof api.FakeApiError) return Promise.reject(r);
    return Promise.resolve(r ?? {});
  },
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

import {
  amount,
  askedWords,
  decodeMetrics,
  handsOffWords,
  nudgedWords,
  systemWords,
} from "@/lib/autonomyMetrics";
import { HandsOffCard } from "@/components/autonomy/HandsOffCard";
import { mergeContinuation, type ContinuableMessage } from "@/lib/continueReply";

const PATH = "/autonomy/metrics?days=30";

function read(rel: string): string {
  return readFileSync(join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");
}

const SAMPLE = {
  days: 30,
  tasks: 50,
  successful: 40,
  hands_off: 31,
  hands_off_pct: 62,
  interruptions: { total: 70, avoidable: 30, per_task: 1.4, avoidable_per_task: 0.6, by_kind: {} },
  nudges: {
    human: 12,
    per_successful_task: 0.3,
    system: { total: 3, by_kind: { plan_run: 2, closing_question: 1 } },
  },
  by_surface: {},
  weekly: [],
};

afterEach(() => {
  cleanup();
  api.gets.length = 0;
  for (const k of Object.keys(api.responses)) delete api.responses[k];
});

describe("the words", () => {
  it("says the three numbers the way the user asked for them", () => {
    const m = decodeMetrics(SAMPLE)!;
    expect(askedWords(m)).toBe("Asked you 1.4 times per task (0.6 could have been avoided)");
    expect(nudgedWords(m)).toBe("You nudged it 0.3 times per finished task");
    expect(handsOffWords(m)).toBe("62% of tasks finished with no help from you");
    expect(systemWords(m)).toBe(
      "Jarvis also moved work on by itself 3 times (2 approved plans ran by themselves, " +
        "1 offer to do more it took up instead of asking).",
    );
  });

  it("says the quiet cases plainly and in the singular", () => {
    const m = decodeMetrics({
      ...SAMPLE,
      interruptions: { total: 0, avoidable: 0, per_task: 0, avoidable_per_task: 0 },
      nudges: { human: 0, per_successful_task: 0, system: { total: 0, by_kind: {} } },
    })!;
    expect(askedWords(m)).toBe("Never stopped to ask you");
    expect(nudgedWords(m)).toBe("You never had to nudge it on a finished task");
    expect(systemWords(m)).toBe("");
    const one = decodeMetrics({
      ...SAMPLE,
      interruptions: { total: 5, avoidable: 0, per_task: 1, avoidable_per_task: 0 },
      nudges: { human: 1, per_successful_task: 1, system: { total: 1, by_kind: { plan_run: 1 } } },
    })!;
    expect(askedWords(one)).toBe("Asked you 1 time per task (none could have been avoided)");
    expect(nudgedWords(one)).toBe("You nudged it 1 time per finished task");
    expect(systemWords(one)).toBe("Jarvis also moved work on by itself once (1 approved plan ran by itself).");
    expect(nudgedWords(decodeMetrics({ ...SAMPLE, successful: 0 })!)).toBe(
      "No finished tasks to count nudges on yet",
    );
  });

  it("decodes defensively: odd numbers read as 0, a non-answer is null", () => {
    expect(decodeMetrics(null)).toBeNull();
    expect(decodeMetrics({ detail: "Not Found" })).toBeNull();
    const m = decodeMetrics({ tasks: 3, interruptions: { total: -4, per_task: "x" }, nudges: "no" })!;
    expect(m.interruptions.total).toBe(0);
    expect(m.interruptions.per_task).toBe(0);
    expect(m.nudges.system.total).toBe(0);
    expect(amount(0.35)).toBe("0.4");
    expect(amount(2)).toBe("2");
  });
});

describe("HandsOffCard", () => {
  it("renders the three numbers, the quiet line and how they are counted", async () => {
    api.responses[PATH] = SAMPLE;
    render(<HandsOffCard />);
    expect(await screen.findByTestId("hands-off-pct")).toHaveTextContent(
      "62% of tasks finished with no help from you (31 of 50)",
    );
    expect(screen.getByTestId("hands-off-asked")).toHaveTextContent(
      "Asked you 1.4 times per task (0.6 could have been avoided)",
    );
    expect(screen.getByTestId("hands-off-nudged")).toHaveTextContent(
      "You nudged it 0.3 times per finished task",
    );
    expect(screen.getByTestId("hands-off-system")).toHaveTextContent("moved work on by itself 3 times");
    expect(screen.getByTestId("hands-off-how")).toHaveTextContent("How this is counted");
    expect(api.gets).toContain(PATH);
  });

  it("says when there is nothing to count yet", async () => {
    api.responses[PATH] = { ...SAMPLE, tasks: 0, successful: 0, hands_off: 0, hands_off_pct: 0,
      nudges: { ...SAMPLE.nudges, system: { total: 0, by_kind: {} } } };
    render(<HandsOffCard />);
    expect(await screen.findByTestId("hands-off-empty")).toHaveTextContent("No tasks in the last 30 days yet");
    expect(screen.queryByTestId("hands-off-pct")).toBeNull();
    expect(screen.queryByTestId("hands-off-system")).toBeNull();
  });

  it("says the daemon's own error sentence beside zeros", async () => {
    api.responses[PATH] = { ...SAMPLE, tasks: 0, error: "The numbers could not be read right now." };
    render(<HandsOffCard />);
    expect(await screen.findByTestId("hands-off-error")).toHaveTextContent("could not be read right now");
  });

  it("renders NOTHING on an older daemon (404) or any failed read", async () => {
    for (const status of [404, 0, 500]) {
      api.responses[PATH] = new api.FakeApiError("nope", status);
      const { container } = render(<HandsOffCard />);
      // Wait for the read to have happened, then for the effect to settle.
      await vi.waitFor(() => expect(api.gets).toContain(PATH));
      await new Promise((r) => setTimeout(r, 30));
      expect(container).toBeEmptyDOMElement();
      cleanup();
      api.gets.length = 0;
    }
  });

  it("uses tone tokens and whole-pixel sizes only", () => {
    const src = read("components/autonomy/HandsOffCard.tsx");
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(src).not.toMatch(/text-(emerald|rose|amber|red|green|sky)-\d/);
    expect(src).not.toMatch(/\[\d+\.\d+px\]/);
  });
});

describe("the Autonomy page", () => {
  it("mounts the card", () => {
    const src = read("app/autonomy/page.tsx");
    expect(src).toContain('import { HandsOffCard } from "@/components/autonomy/HandsOffCard";');
    expect(src).toContain("<HandsOffCard />");
  });
});

describe("a Continue press is counted on the merged reply", () => {
  it("adds one per press, carrying the cut reply's own count", () => {
    const once = mergeContinuation<ContinuableMessage>([
      { role: "user", content: "write" },
      { role: "assistant", content: "part one", interrupted: true },
      { role: "user", content: "Your last reply was cut off.", continuation: true },
      { role: "assistant", content: "part two" },
    ]);
    expect(once).toHaveLength(2);
    expect(once[1].continuedPresses).toBe(1);
    const twice = mergeContinuation<ContinuableMessage>([
      ...once,
      { role: "user", content: "Your last reply was cut off.", continuation: true },
      { role: "assistant", content: "part three" },
    ]);
    expect(twice).toHaveLength(2);
    expect(twice[1].content).toBe("part one part two part three");
    expect(twice[1].continuedPresses).toBe(2);
  });

  it("leaves a reply that was never continued without the field", () => {
    const list: ContinuableMessage[] = [
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello" },
    ];
    expect(mergeContinuation(list)[1].continuedPresses).toBeUndefined();
  });
});
