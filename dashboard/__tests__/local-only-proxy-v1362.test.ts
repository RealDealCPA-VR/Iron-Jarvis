/**
 * v1.362.0: a model proxy on the user's network can send ONE name to the
 * cloud. The daemon now marks each `/models` row by where that name really
 * goes (`on_this_network`), and the chat's "send to the cloud?" card asks the
 * same question the daemon's lock does:
 *  - a listed row decides for itself (frontier: ask; glm: no ask);
 *  - a model the list does not show (the proxy's hidden default "fleet")
 *    goes without asking only when EVERY row of its provider stays.
 */
import { describe, expect, it } from "vitest";

import { cloudAskFor } from "@/lib/localOnly";

const ROWS = [
  { provider: "custom", model: "glm-5.3-flash", kind: "local", available: true, on_this_network: true },
  { provider: "custom", model: "glm-5.3-flash-think", kind: "local", available: true, on_this_network: true },
  { provider: "custom", model: "frontier", kind: "local", available: true, on_this_network: false },
  { provider: "fleet-rtx", model: "brain", kind: "local", available: true, on_this_network: true },
];

describe("local only: a proxy's cloud-routed name", () => {
  it("asks for the cloud-routed row and never for a local one on the same server", () => {
    expect(cloudAskFor("custom", "frontier", ROWS)).toEqual({
      provider: "custom",
      model: "frontier",
      name: expect.any(String),
    });
    expect(cloudAskFor("custom", "glm-5.3-flash", ROWS)).toBeNull();
    expect(cloudAskFor("custom", "glm-5.3-flash-think", ROWS)).toBeNull();
  });

  it("asks for a name the list does not show when its server also sends a name to the cloud", () => {
    expect(cloudAskFor("custom", "fleet", ROWS)?.model).toBe("fleet");
    expect(cloudAskFor("custom", "", ROWS)?.provider).toBe("custom");
  });

  it("does not ask for an unlisted name on a server whose every row stays", () => {
    expect(cloudAskFor("fleet-rtx", "qwen3.6:27b", ROWS)).toBeNull();
  });
});
