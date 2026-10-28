/**
 * The user's catalog, as GET /models lists it, for the "try it on several
 * models" pins (v1.400.7 Try on, v1.400.8 Tonight): the v1.376.0 fold
 * fixture's REAL rows for Claude Code + a keyless Anthropic and Codex + a
 * keyless OpenAI; the user's LiteLLM gateway reached two ways (the `custom`
 * config slot and the fleet node the registry derives from it,
 * `fleet-custom`), serving glm-5.3-flash on the network and passing
 * OpenRouter names on to the cloud; the RTX box as a fleet node of its own;
 * and the demo model, which is never offered.
 */

import fold from "../fixtures/harness-model-fold-v1376.json";
import type { ModelOption } from "@/lib/types";

export const local = (provider: string, model: string, extra: Partial<ModelOption> = {}): ModelOption => ({
  provider,
  model,
  available: true,
  kind: "local",
  on_this_network: true,
  inherited_from: null,
  ...extra,
});
export const routedOut = (provider: string, model: string, extra: Partial<ModelOption> = {}): ModelOption =>
  local(provider, model, { on_this_network: false, ...extra });

/** The user's own servers, as GET /models lists them. */
export const GATEWAY: ModelOption[] = [
  // Listed before the gateway's own model: the list still puts local first.
  routedOut("custom", "frontier"),
  local("custom", "glm-5.3-flash"),
  // The hidden "fleet" alias: unlisted, on a gateway with a cloud upstream —
  // the daemon reads it as cloud (v1.362.0).
  routedOut("custom", "fleet"),
  local("custom", "glm-5.3-flash-think"),
  routedOut("custom", "deepseek/deepseek-v4.1-flash"),
  routedOut("custom", "~z-ai/glm-flash-latest"),
  // The registry's fleet node for the same slot: one row per deployment, the
  // other names in `aliases` (fleet/picker.py), the registry's default label.
  local("fleet-custom", "glm-5.3-flash", {
    name: "Custom endpoint",
    source: "endpoint",
    aliases: ["glm-5.3-flash-think", "vision"],
  } as Partial<ModelOption>),
  routedOut("fleet-custom", "frontier", { name: "Custom endpoint", source: "endpoint" }),
  routedOut("fleet-custom", "deepseek/deepseek-v4.1-flash", { name: "Custom endpoint", source: "endpoint" }),
  local("fleet-rtx6000ada", "Qwen3.8-27B (RTX)", { name: "RTX6000ada", source: "endpoint", size_b: 27 }),
];

export const CATALOG: ModelOption[] = [
  ...(fold.models as unknown as ModelOption[]),
  ...GATEWAY,
  { provider: "mock", model: "mock-1", available: true },
];

export const catalogPairs = new Set(CATALOG.map((m) => `${m.provider}::${m.model}`));
