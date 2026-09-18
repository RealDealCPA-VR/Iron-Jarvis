/**
 * v1.275.0 — "Retry with the default model" is offered in exactly one case.
 *
 * A failed turn on an EXPLICIT pick whose provider is known-down, while the
 * default provider is known-up. Anything less certain leaves the plain Retry:
 * a provider the user did not choose is never picked for them (v1.162.0).
 */
import { describe, expect, it } from "vitest";

import { canRetryWithDefault, providerOf } from "@/lib/providerFallback";

const health = (byProvider: Record<string, boolean>, defaultProvider = "custom") => ({ byProvider, defaultProvider });

describe("providerOf", () => {
  it("reads the provider off an explicit pick and nothing off the default", () => {
    expect(providerOf("openai::gpt-5")).toBe("openai");
    expect(providerOf("")).toBe("");
    expect(providerOf("no-separator")).toBe("");
  });
});

describe("canRetryWithDefault", () => {
  it("offers the default when the pick is down and the default is up", () => {
    expect(canRetryWithDefault("openai::gpt-5", health({ openai: false, custom: true }))).toBe(true);
  });
  it("never on the default itself", () => {
    expect(canRetryWithDefault("", health({ custom: false }))).toBe(false);
    expect(canRetryWithDefault("custom::fleet", health({ custom: false }))).toBe(false);
  });
  it("not when the pick is merely unknown, and not when the default is unknown or down", () => {
    expect(canRetryWithDefault("openai::gpt-5", health({ custom: true }))).toBe(false);
    expect(canRetryWithDefault("openai::gpt-5", health({ openai: false }))).toBe(false);
    expect(canRetryWithDefault("openai::gpt-5", health({ openai: false, custom: false }))).toBe(false);
  });
  it("not with no default provider at all", () => {
    expect(canRetryWithDefault("openai::gpt-5", health({ openai: false, custom: true }, ""))).toBe(false);
  });
});
