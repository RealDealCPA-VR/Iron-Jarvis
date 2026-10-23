/**
 * v1.378.0 review — a refusal's line on the RUN PAGE and the chat's live
 * agent progress (stepLabel, the one event -> line renderer).
 *
 * The router ledgers a refusal as `provider.failed` with `kind: "refusal"`,
 * and stepLabel printed every provider.failed as "Provider claude-cli failed:
 * ProviderRefusal: claude-cli: Anthropic's safety check declined to answer
 * (stop_reason refusal, cat…" — the vendor's own words on a run's timeline.
 * A refusal now reads as what it is, by the provider's plain name; every
 * other failure keeps its line (chat-notices-v1329 pins those).
 */

import { describe, expect, it } from "vitest";
import { stepLabel } from "@/components/chat/stepLabel";
import type { IJEvent } from "@/lib/types";

function ev(type: string, payload: Record<string, unknown>): IJEvent {
  return { id: 1, type, payload, session_id: "s1", created_at: "2026-10-23T10:00:00" } as unknown as IJEvent;
}

const RAW = "ProviderRefusal: claude-cli: Anthropic's safety check declined to answer (stop_reason refusal, category cyber)";

describe("v1.378.0 review: a refusal on a run's timeline", () => {
  it("is the provider declining, by its plain name — never the vendor's words", () => {
    const line = stepLabel(ev("provider.failed", { provider: "claude-cli", error: RAW, kind: "refusal", category: "cyber" }));
    expect(line).toBe("Claude Code declined to answer");
    expect(line).not.toMatch(/stop_reason|ProviderRefusal|claude-cli/);
  });

  it("after words reached the screen, it declined to finish", () => {
    expect(
      stepLabel(ev("provider.failed", { provider: "claude-cli", error: RAW, kind: "refusal", partial: true })),
    ).toBe("Claude Code declined to finish the answer");
  });

  it("an unknown provider still reads as a sentence", () => {
    expect(stepLabel(ev("provider.failed", { error: RAW, kind: "refusal" }))).toBe("The model declined to answer");
  });

  it("control: an ordinary failure keeps its line", () => {
    expect(stepLabel(ev("provider.failed", { provider: "openai", error: "boom" }))).toBe("Provider openai failed: boom");
  });
});

describe("v1.378.0 review: the done frame's `refused` is decoded (whitelisted)", () => {
  it("a real true decodes; anything else leaves the key absent", async () => {
    const { decodeSSE } = await import("@/lib/useChatStream");
    const yes = decodeSSE("done", JSON.stringify({ reply: "x", refused: true })) as { refused?: boolean };
    expect(yes?.refused).toBe(true);
    const no = decodeSSE("done", JSON.stringify({ reply: "x", refused: "yes" })) as { refused?: boolean };
    expect(no && "refused" in no).toBe(false);
  });
});
