/**
 * Review of Wave B lane B2 (v1.449.0): the Activity headline's payload
 * detail, after the independent review. Each case was red on the builder's
 * cut.
 *
 * 1. A run's `task` and a preference's `text` are the USER'S OWN WORDS, and
 *    this is a CPA firm's PC: "Email bob@firm.com the K-1, ssn 123-45-6789"
 *    reached the headline whole (the builder's guard knew paths, URLs, key
 *    prefixes, hex ids and JSON — not an e-mail, an SSN, a card, a JWT, a PEM
 *    block, "password hunter2", `key=AQ.…`, or `user:pass@host`). A value is
 *    DROPPED when the dashboard's own mask would change it or it carries any
 *    of those shapes; the headline alone is still words.
 * 2. A provider the names table does not know was shown as its RAW id
 *    ("A model failed to answer · fleet-custom") — the builder's own rule
 *    says never an id. The user's own server reads "your model server".
 * 3. A model id was dropped as a "token" when long ("claude-sonnet-4-5-
 *    20250929") and shown raw when short; it reads the menu's name now.
 * 4. A status or verdict read "completed with failures" where every other
 *    surface says "Part failed" (lib/statusWords, the v1.351.0 one table).
 */
import { describe, expect, it } from "vitest";

import { eventHeadline, providerDetail, safeDetailValue } from "@/lib/eventWords";

const BS = String.fromCharCode(92);

describe("the headline never carries personal data or a credential", () => {
  it.each([
    ["an e-mail", "bob@firm.com"],
    ["an e-mail inside a sentence", "send it to bob@firm.com please"],
    ["a user:pass@host", "user:pass@host.com"],
    ["an SSN", "my SSN is 123-45-6789"],
    ["an EIN", "EIN 12-3456789 for the LLC"],
    ["a card number", "card 4111 1111 1111 1111"],
    ["a phone number", "call 555-123-4567 first"],
    ["a JWT", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcDEF123"],
    ["a PEM block", "-----BEGIN PRIVATE KEY-----"],
    ["a password with its value", "password hunter2"],
    ["a token with its value", "token: hermes-secret-2024"],
    ["a bearer value", "Bearer abcdef"],
    ["an authorization header", "Authorization: Bearer x"],
    ["a Google key in a sentence", "use key AIzaSyA1234567890abcdefGHIJKL please"],
    ["a Google key of the newer shape", "key=AQ.Ab8RN6I1234567890"],
    ["an AWS key in a sentence", "the key AKIAIOSFODNN7EXAMPLE works"],
    ["a Slack token in a sentence", "use xoxb-123-abc for it"],
    ["an Anthropic key in a sentence", "use sk-ant-api03-" + "x".repeat(40) + " for it"],
    // Caught by the dashboard's own mask alone (no @, no credential word).
    ["a curl -u user:pass", "run curl -u bob:hunter2 for the sync"],
    ["a --user user:pass", "sync with --user bob:hunter2 nightly"],
  ])("%s is dropped", (_what, value) => {
    expect(safeDetailValue(value)).toBe("");
  });

  it("plain words still pass", () => {
    expect(safeDetailValue("Rename the 2024 Q3 scans")).toBe("Rename the 2024 Q3 scans");
    expect(safeDetailValue("Draft the brief for Monday")).toBe("Draft the brief for Monday");
    expect(safeDetailValue("bad signature")).toBe("bad signature");
  });

  it("a task carrying an e-mail or an SSN leaves the headline alone", () => {
    expect(eventHeadline("session.completed", { task: "Email bob@firm.com the K-1, ssn 123-45-6789" })).toBe(
      "Session finished",
    );
    expect(eventHeadline("preference.suggested", { text: "always sign as vr@firm.com" })).toBe(
      "Suggested remembering a preference",
    );
    expect(eventHeadline("assignment.blocked", { title: "t", assignee: "a", blocked_reason: "failed: C:" + BS + "x" })).toBe(
      "A queued task was blocked after repeated failures · t · a",
    );
  });
});

describe("a provider and a model are named, never an id", () => {
  it("a fleet node or the user's own endpoint is 'your model server'", () => {
    expect(providerDetail("fleet-custom")).toBe("your model server");
    expect(providerDetail("fleet-spark-2")).toBe("your model server");
    // The slots the names table knows keep its words.
    expect(providerDetail("custom")).toBe("your model server");
    expect(providerDetail("ollama")).toBe("Ollama");
    expect(eventHeadline("provider.failed", { provider: "fleet-custom", error: "boom" })).toBe(
      "A model failed to answer · your model server",
    );
  });

  it("a known provider reads its plain name; an unknown id adds nothing", () => {
    expect(providerDetail("claude-cli")).toBe("Claude Code");
    expect(providerDetail("mock")).toBe("Demo model (scripted)");
    expect(providerDetail("zz-unknown-provider")).toBe("");
    expect(eventHeadline("provider.failed", { provider: "zz-unknown-provider" })).toBe("A model failed to answer");
  });

  it("a model reads the menu's name", () => {
    expect(eventHeadline("provider.routed", { provider: "claude-cli", model: "claude-sonnet-4-5-20250929" })).toBe(
      "Picked a model for this turn · Claude Code · Sonnet 4.5",
    );
    expect(eventHeadline("envelope.adapted", { model: "claude-opus-4-8" })).toBe(
      "The request was adapted to the model's limits · Opus 4.8",
    );
  });
});

describe("a status or verdict is the one status vocabulary", () => {
  it("says Part failed, Needs you and Done like every badge", () => {
    expect(eventHeadline("workflow.completed", { workflow: "wf", status: "completed_with_failures" })).toBe(
      "Workflow finished · wf · Part failed",
    );
    expect(eventHeadline("build.turn_finished", { title: "t", outcome: "needs_you" })).toBe(
      "A coding session's reply finished · t · Needs you",
    );
    expect(eventHeadline("batch.file_done", { status: "completed" })).toBe(
      "A document in a batch was processed · Done",
    );
    expect(eventHeadline("computeruse.run_finished", { status: "failed" })).toBe(
      "A computer-use run finished · Failed",
    );
  });

  it("an unknown value is shown as itself, never dropped into silence", () => {
    expect(eventHeadline("workflow.completed", { workflow: "wf", status: "brand_new" })).toBe(
      "Workflow finished · wf · Brand new",
    );
  });
});
