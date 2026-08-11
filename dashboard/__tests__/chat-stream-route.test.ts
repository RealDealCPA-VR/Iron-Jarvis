/**
 * Route disclosure survives the SSE decode (v1.165.0).
 *
 * The done-frame now carries `route` (server-side truth about who answered and
 * why), and the decode layer is where such fields have QUIETLY DIED before:
 * `denied_tools` and `usage` were decoded from the frame and then dropped on
 * the floor when the result object was assembled, so the page could never show
 * a denial. These tests pin the pass-through for all three.
 */

import { describe, expect, it } from "vitest";
import { decodeSSE } from "@/lib/useChatStream";

function done(data: Record<string, unknown>) {
  return decodeSSE("done", JSON.stringify({ reply: "ok", ...data }));
}

describe("decodeSSE carries the accountability fields", () => {
  it("passes route through verbatim", () => {
    const ev = done({
      route: { requested: "", provider: "fleet-custom", model: "fleet", reason: "default" },
    });
    expect(ev && ev.type === "done" && ev.route?.provider).toBe("fleet-custom");
    expect(ev && ev.type === "done" && ev.route?.reason).toBe("default");
  });

  it("drops a malformed route instead of crashing the stream", () => {
    // A frame from an older daemon (or a proxy mangling JSON) must not take
    // down the whole turn — the field is optional everywhere downstream.
    const ev = done({ route: "fleet-custom" });
    expect(ev && ev.type === "done" && ev.route).toBeUndefined();
    const ev2 = done({ route: { reason: "default" } }); // no provider
    expect(ev2 && ev2.type === "done" && ev2.route).toBeUndefined();
  });

  it("still decodes denied_tools and usage", () => {
    const ev = done({
      denied_tools: ["shell"],
      usage: { input_tokens: 10, output_tokens: 5 },
    });
    expect(ev && ev.type === "done" && ev.denied_tools).toEqual(["shell"]);
    expect(ev && ev.type === "done" && ev.usage?.output_tokens).toBe(5);
  });

  it("a frame without route still decodes (old daemon compatibility)", () => {
    const ev = done({ provider: "mock" });
    expect(ev && ev.type === "done" && ev.provider).toBe("mock");
    expect(ev && ev.type === "done" && ev.route).toBeUndefined();
  });
});
