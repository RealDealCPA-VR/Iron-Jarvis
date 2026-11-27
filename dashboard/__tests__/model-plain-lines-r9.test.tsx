/**
 * The intelligence plan, release 9: each of your own models reads, in plain
 * words, what it is good at — "Uses tools well. Holds about 12 pages at once.
 * Writes about 25 words a second." — and ONLY what was measured. A figure
 * nothing measured is never said; a model being guided says so.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

const hooks = vi.hoisted(() => ({ responses: {} as Record<string, unknown> }));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError: FakeApiError,
    get: vi.fn((path: string) => Promise.resolve(hooks.responses[path] ?? {})),
    post: vi.fn(() => Promise.resolve({ started: true })),
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));

import {
  SIZED_ONLY_LOCAL_LINE,
  GUIDED_CARD_LINE,
  MeasuredEndpoints,
  envelopeUrl,
  plainModelLines,
  type EnvelopeProfile,
} from "@/components/connections/EnvelopeCard";

afterEach(cleanup);

const ALL = [
  "tool_protocols.native",
  "tool_protocols.strict_json",
  "json_adherence",
  "holds_chars",
  "holds_more",
  "words_per_second",
];

function profile(over: Partial<EnvelopeProfile> = {}): EnvelopeProfile {
  return {
    model_id: "qwen3:8b",
    provider: "ollama",
    source: "probed",
    probed_at: "2026-11-27T03:00:00+00:00",
    tool_protocols: { native: 1, strict_json: 1 },
    holds_chars: 36000,
    holds_more: 0,
    words_per_second: 24.6,
    measured_fields: ALL,
    ...over,
  };
}

describe("plainModelLines", () => {
  it("says the three lines for a measured model", () => {
    expect(plainModelLines(profile())).toEqual([
      "Uses tools well.",
      "Holds about 12 pages at once.",
      "Writes about 25 words a second.",
    ]);
  });

  it("says 'at least' when a larger window was never tried", () => {
    expect(plainModelLines(profile({ holds_chars: 98304, holds_more: 1 }))[1]).toBe(
      "Holds at least 32 pages at once.",
    );
  });

  it("names the guided form and the floor honestly", () => {
    expect(plainModelLines(profile({ tool_protocols: { native: 0.33, strict_json: 1 } }))[0]).toBe(
      "Uses tools when guided step by step.",
    );
    expect(plainModelLines(profile({ tool_protocols: { native: 0, strict_json: 0.33 } }))[0]).toBe(
      "Does not use tools reliably.",
    );
    expect(plainModelLines(profile(), true)[0]).toBe(GUIDED_CARD_LINE);
  });

  it("says nothing that was not measured", () => {
    // The fields hold numbers, but no battery delivered them.
    expect(plainModelLines(profile({ measured_fields: [] }))).toEqual([]);
    // A refused native rung and an unscored strict rung: no tools line at all.
    expect(
      plainModelLines(profile({ measured_fields: ["holds_chars"], tool_protocols: { native: 0 } })),
    ).toEqual(["Holds about 12 pages at once."]);
    // A seeded profile is the server's own claim, never a measurement.
    expect(plainModelLines(profile({ source: "seeded", measured_fields: ["tool_protocols.native"] }))).toEqual([]);
    expect(plainModelLines(profile({ words_per_second: 0.4 }))[2]).toBe(
      "Writes less than one word a second.",
    );
    expect(plainModelLines(null)).toEqual([]);
  });
});

describe("the Model servers card", () => {
  it("shows the plain lines under the model's verdict", async () => {
    hooks.responses[envelopeUrl("ollama", "qwen3:8b")] = {
      trusted: false,
      profile: profile(),
      effective_window: { value: 16384, source: "endpoint" },
    };
    render(<MeasuredEndpoints entries={[{ provider: "ollama", model: "qwen3:8b", label: "This PC" }]} />);
    const line = await waitFor(() => screen.getByTestId("measured-ollama-qwen3:8b-plain"));
    expect(line.textContent).toBe(
      "Uses tools well. Holds about 12 pages at once. Writes about 25 words a second.",
    );
  });

  it("shows the guided line while the model is being guided", async () => {
    hooks.responses[envelopeUrl("ollama", "qwen3:8b")] = {
      trusted: false,
      guided: true,
      profile: profile(),
      effective_window: { value: 16384, source: "endpoint" },
    };
    render(<MeasuredEndpoints entries={[{ provider: "ollama", model: "qwen3:8b", label: "This PC" }]} />);
    const line = await waitFor(() => screen.getByTestId("measured-ollama-qwen3:8b-plain"));
    expect(line.textContent?.startsWith(GUIDED_CARD_LINE)).toBe(true);
    expect(line.textContent).not.toContain("Uses tools well.");
  });
});

describe("a model not proven to stay on your network", () => {
  it("says size and speed are measured only on this PC or your network", async () => {
    hooks.responses[envelopeUrl("custom", "fleet")] = {
      trusted: false,
      sized: false,
      profile: profile({ model_id: "fleet", provider: "custom", measured_fields: ["tool_protocols.native", "tool_protocols.strict_json"] }),
      effective_window: { value: 16384, source: "endpoint" },
    };
    render(<MeasuredEndpoints entries={[{ provider: "custom", model: "fleet", label: "Spark" }]} />);
    const line = await waitFor(() => screen.getByTestId("measured-custom-fleet-sized"));
    expect(line.textContent).toBe(SIZED_ONLY_LOCAL_LINE);
    expect(screen.getByTestId("measured-custom-fleet-plain").textContent).toBe("Uses tools well.");
  });

  it("says nothing of the kind for a model proven local", async () => {
    hooks.responses[envelopeUrl("ollama", "qwen3:8b")] = {
      trusted: false, sized: true, profile: profile(), effective_window: { value: 16384, source: "endpoint" },
    };
    render(<MeasuredEndpoints entries={[{ provider: "ollama", model: "qwen3:8b", label: "This PC" }]} />);
    await waitFor(() => screen.getByTestId("measured-ollama-qwen3:8b-plain"));
    expect(screen.queryByTestId("measured-ollama-qwen3:8b-sized")).toBeNull();
  });
});
