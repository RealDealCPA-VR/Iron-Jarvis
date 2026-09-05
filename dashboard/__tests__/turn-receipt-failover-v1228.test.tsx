/**
 * TurnReceipt failover wording (v1.228.0, audit Wave 2 / R2).
 *
 * On chat's DEFAULT route `requested` is "" by contract, so before this the
 * amber chip could only say "answered by claude-cli — failover": it never
 * named the user's own endpoint that was skipped, nor WHY. The daemon's
 * `route` object now carries `from` (the provider that failed) and `why`
 * (the router's derived word: "http 500" | "timeout" | ...), and the chip
 * reads "answered by claude-cli — fleet-rtx6000ada returned HTTP 500".
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { TurnReceipt, routeWarning, wordWhy } from "@/components/chat/TurnReceipt";

afterEach(() => {
  cleanup();
});

describe("routeWarning with from/why (v1.228.0)", () => {
  it("DEFAULT route: names the failed local default and the HTTP status", () => {
    expect(
      routeWarning({
        requested: "",
        provider: "claude-cli",
        reason: "failover",
        from: "fleet-rtx6000ada",
        why: "http 500",
      }),
    ).toBe("answered by claude-cli — fleet-rtx6000ada returned HTTP 500");
  });

  it("words every router token in plain language", () => {
    expect(wordWhy("http 429")).toBe("returned HTTP 429");
    expect(wordWhy("timeout")).toBe("didn't respond in time");
    expect(wordWhy("unreachable")).toBe("was unreachable");
    expect(wordWhy("interrupted")).toBe("dropped the connection");
    expect(wordWhy("transient error")).toBe("hit a transient error");
    expect(wordWhy("error")).toBe("returned an error");
    // An unknown token is the disclosure itself — passed through, never dropped.
    expect(wordWhy("quota exhausted")).toBe("quota exhausted");
    expect(wordWhy("")).toBe("");
  });

  it("from without why still names who failed", () => {
    expect(
      routeWarning({ requested: "", provider: "claude-cli", reason: "failover", from: "ollama" }),
    ).toBe("answered by claude-cli — failover from ollama");
  });

  it("from === provider is not a failover story — falls back to the old wording", () => {
    expect(
      routeWarning({ requested: "", provider: "claude-cli", reason: "failover", from: "claude-cli", why: "http 500" }),
    ).toBe("answered by claude-cli — failover");
  });

  it("pre-v1.228.0 messages (no from/why) keep their wording", () => {
    expect(
      routeWarning({ requested: "fleet-custom", provider: "claude-cli", reason: "failover" }),
    ).toBe("answered by claude-cli — failover from fleet-custom");
    expect(routeWarning({ requested: "", provider: "claude-cli", reason: "failover" })).toBe(
      "answered by claude-cli — failover",
    );
  });

  it("mock still outranks a failover that carries from/why", () => {
    expect(
      routeWarning({ provider: "mock", reason: "failover", from: "ollama", why: "http 500" }),
    ).toBe("mock answer — no real model ran");
  });

  it("from/why are ignored when the reason is not failover", () => {
    // A served-as-asked turn with stale/phantom fields must stay quiet.
    expect(
      routeWarning({ requested: "", provider: "ollama", reason: "default", from: "", why: "" }),
    ).toBeNull();
  });
});

describe("TurnReceipt renders the from/why story", () => {
  it("collapsed chip is amber and visible without expanding; expanded row repeats it", () => {
    render(
      <TurnReceipt
        route={{
          requested: "",
          provider: "claude-cli",
          model: "claude-fable-5",
          reason: "failover",
          from: "fleet-rtx6000ada",
          why: "http 500",
        }}
      />,
    );
    const chip = screen.getByText(/answered by claude-cli — fleet-rtx6000ada returned HTTP 500/);
    expect(chip.className).toMatch(/amber/);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    // The expanded row is its own span whose whole text is the from/why
    // story (exact match — the collapsed chip also CONTAINS it).
    expect(screen.getByText("— fleet-rtx6000ada returned HTTP 500")).toBeTruthy();
  });
});
