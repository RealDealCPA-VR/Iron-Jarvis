/**
 * Route disclosure survives the SSE decode (v1.165.0).
 *
 * The done-frame now carries `route` (server-side truth about who answered and
 * why), and the decode layer is where such fields have QUIETLY DIED before:
 * `denied_tools` and `usage` were decoded from the frame and then dropped on
 * the floor when the result object was assembled, so the page could never show
 * a denial. These tests pin the pass-through for all three.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { decodeSSE, useChatStream } from "@/lib/useChatStream";

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

  // Doors (v1.199.0): the decoder WHITELISTS fields, so this is exactly the
  // layer where denied_tools and usage once died silently. Pin the pass-through
  // so the streaming lane — the lane users watch — can never lose its doors.
  it("passes doors through verbatim, and absence stays absent", () => {
    const doors = [{ href: "/workflows", label: "Open the canvas" }];
    const ev = done({ doors });
    expect(ev && ev.type === "done" && ev.doors).toEqual(doors);
    const bare = done({ provider: "mock" });
    expect(bare && bare.type === "done" && bare.doors).toBeUndefined();
  });

  // Adapted (v1.202.0): the envelope's adaptation disclosure — same
  // whitelist hazard as doors, pinned the same way.
  it("passes adapted through verbatim", () => {
    const adapted = { model: "qwen3:4b", changes: ["tool_cap:4"] };
    const ev = done({ adapted });
    expect(ev && ev.type === "done" && ev.adapted).toEqual(adapted);
  });

  it("adapted null (the daemon's unbent-turn value) decodes to absent", () => {
    // The daemon sends the key on EVERY turn — null when nothing bent — so
    // null must land as "nothing to show", identically to absence.
    const ev = done({ adapted: null });
    expect(ev && ev.type === "done" && ev.adapted).toBeUndefined();
    const bare = done({ provider: "mock" });
    expect(bare && bare.type === "done" && bare.adapted).toBeUndefined();
  });

  it("drops a malformed adapted instead of crashing the stream", () => {
    const ev = done({ adapted: "tool_cap:4" });
    expect(ev && ev.type === "done" && ev.adapted).toBeUndefined();
    const ev2 = done({ adapted: { model: "x" } }); // no changes array
    expect(ev2 && ev2.type === "done" && ev2.adapted).toBeUndefined();
  });
});

/**
 * The done-frame has TWO drop points: the whitelist decoder above, and the
 * result-object assembly in the hook's `run` — where `denied_tools`/`usage`
 * were decoded and then dropped on the floor until v1.165.0. The decode pin
 * cannot see that second layer (a deleted `doors: ev.doors` merge line left
 * every prior test green — measured), so this drives the REAL hook over a
 * mocked SSE fetch and asserts doors arrive on the resolved result.
 */
describe("useChatStream carries doors onto the resolved result", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("run() resolves with the done frame's doors and adapted", async () => {
    const doors = [{ href: "/schedules", label: "See your schedule" }];
    // Adapted rides the SAME frame (v1.202.0): the hook's merge is one
    // object literal, and a deleted `adapted: ev.adapted` line would leave
    // every decode-level pin green — this is the mutation check for it.
    const adapted = { model: "qwen3:4b", changes: ["tool_cap:4"] };
    const frame =
      `event: done\n` +
      `data: ${JSON.stringify({ reply: "made it", doors, adapted })}\n\n`;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(frame));
        controller.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(body, { status: 200 })),
    );

    const { result } = renderHook(() => useChatStream());
    let settled: Awaited<ReturnType<typeof result.current.run>> | null = null;
    await act(async () => {
      settled = await result.current.run({ messages: [] });
    });
    expect(settled).not.toBeNull();
    expect(settled!.reply).toBe("made it");
    expect(settled!.doors).toEqual(doors);
    expect(settled!.adapted).toEqual(adapted);
  });
});
