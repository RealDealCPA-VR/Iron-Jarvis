/**
 * v1.449.0 (readiness plan 0h.5 item 14): the Activity page words EVERY
 * event type, with the payload's own detail on the line.
 *
 * The mirror of tests/test_event_words_every_type_v1449.py: this half reads
 * `src/iron_jarvis/core/events.py` (the `EventType` members and
 * `OTHER_PUBLISHED_TYPES`) and fails on a type with no row in
 * lib/eventWords.EVENT_WORDS — and on a row for a type the daemon never
 * publishes. Then the detail reader: a webhook refusal names its address and
 * reason, a tool event names the tool in the card's words, a run names its
 * task; a path, a key, an id or a JSON blob never reaches the headline; the
 * feed's row reads `rowHeadline`.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  DETAIL_VALUE_MAX,
  EVENT_WORDS,
  eventDetail,
  eventHeadline,
  rowHeadline,
  safeDetailValue,
} from "@/lib/eventWords";

const ROOT = path.resolve(__dirname, "..", "..");
const read = (rel: string) => readFileSync(path.join(ROOT, rel), "utf8").replace(/\r\n/g, "\n");

function daemonEventTypes(): Set<string> {
  const src = read("src/iron_jarvis/core/events.py");
  const cls = src.slice(src.indexOf("class EventType:"), src.indexOf("\nOTHER_PUBLISHED_TYPES"));
  const members = [...cls.matchAll(/^\s+[A-Z][A-Z0-9_]+ = "([a-z_]+\.[a-z_.]+)"/gm)].map((m) => m[1]);
  const otherBlock = src.slice(src.indexOf("OTHER_PUBLISHED_TYPES: frozenset[str] = frozenset("));
  const other = [...otherBlock.slice(0, otherBlock.indexOf(")\n")).matchAll(/"([a-z_]+\.[a-z_.]+)"/g)].map(
    (m) => m[1],
  );
  return new Set([...members, ...other]);
}

describe("every event type has words", () => {
  it("the scan reads the daemon's event types at all", () => {
    const types = daemonEventTypes();
    expect(types.size).toBeGreaterThanOrEqual(100);
    expect(types.has("session.completed")).toBe(true);
    expect(types.has("webhook.rejected")).toBe(true);
    expect(types.has("workflow.waiting")).toBe(true);
  });

  it("every type the daemon publishes has a row (a new EventType with none fails here)", () => {
    const missing = [...daemonEventTypes()].filter((t) => !EVENT_WORDS[t]);
    expect(missing).toEqual([]);
  });

  it("no row names a type the daemon never publishes", () => {
    const types = daemonEventTypes();
    const stale = Object.keys(EVENT_WORDS).filter((t) => !types.has(t));
    expect(stale).toEqual([]);
  });

  it("every headline is a sentence in words, never the id", () => {
    for (const [type, words] of Object.entries(EVENT_WORDS)) {
      expect(words, type).not.toMatch(/\b[a-z_]+\.[a-z_]+\b/);
      expect(words, type).not.toMatch(/[_/\\]/);
      expect(words.charAt(0), type).toBe(words.charAt(0).toUpperCase());
    }
  });
});

describe("the payload's detail on the line", () => {
  it("a refused webhook names its address and the reason in words", () => {
    expect(eventHeadline("webhook.rejected", { slug: "github-push", reason: "bad_signature" })).toBe(
      "A webhook was refused · github-push · bad signature",
    );
    expect(eventHeadline("webhook.rejected", { slug: "github-push", reason: "unknown_slug" })).toBe(
      "A webhook was refused · github-push · unknown address",
    );
  });

  it("a tool event names the tool in the card's words, never the id", () => {
    expect(eventHeadline("trust.lowered", { tool: "web_fetch", category: "injection" })).toBe(
      "Trust was lowered after suspicious content · read a web page · injection",
    );
    expect(eventHeadline("grant.created", { tool: "write_file", id: "grant_1" })).toBe(
      "A standing approval was granted · save a file",
    );
    // A tool with no card words adds nothing (never the raw id).
    expect(eventHeadline("grant.created", { tool: "some_odd_tool" })).toBe("A standing approval was granted");
  });

  it("a run names its task, a queued task its title and agent", () => {
    expect(eventHeadline("session.completed", { task: "Rename the scans", status: "completed" })).toBe(
      "Session finished · Rename the scans",
    );
    expect(eventHeadline("assignment.created", { title: "Draft the brief", assignee: "builder", id: "as_1" })).toBe(
      "A task was queued for an agent · Draft the brief · builder",
    );
  });

  it("a failed model is named by its plain name; a rule or schedule by its name", () => {
    expect(eventHeadline("provider.failed", { provider: "mock", error: "boom" })).toBe(
      "A model failed to answer · Demo model (scripted)",
    );
    expect(eventHeadline("reflex.fired", { rule: "nightly-brief", source: "schedule" })).toBe(
      "A rule fired · nightly-brief",
    );
    expect(eventHeadline("sessions.interrupted", { count: 3, session_ids: ["a", "b", "c"] })).toBe(
      "A restart cut off running work · 3 runs",
    );
  });

  it("never a path, a key, an id, a URL or a JSON dump", () => {
    expect(safeDetailValue("C:\\Users\\VR\\taxes\\k1.pdf")).toBe("");
    expect(safeDetailValue("/home/vr/notes.md")).toBe("");
    expect(safeDetailValue("docs/plan.md")).toBe("");
    expect(safeDetailValue("sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789")).toBe("");
    expect(safeDetailValue("ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789")).toBe("");
    expect(safeDetailValue("3f9a1c2b4d5e6f70")).toBe("");
    expect(safeDetailValue("sess_3f9a1c2b4d5e")).toBe("");
    expect(safeDetailValue("https://example.com/hook?token=abc")).toBe("");
    expect(safeDetailValue('{"a": 1}')).toBe("");
    expect(safeDetailValue(42)).toBe("");
    // Plain words pass, whitespace folded, long ones cut.
    expect(safeDetailValue("  Rename   the\nscans ")).toBe("Rename the scans");
    const long = "word ".repeat(40).trim();
    expect(safeDetailValue(long).length).toBeLessThanOrEqual(DETAIL_VALUE_MAX);
    expect(safeDetailValue(long).endsWith("…")).toBe(true);
    // Through the headline too: a task that is a path is left out.
    expect(eventHeadline("session.completed", { task: "C:\\work\\run.bat" })).toBe("Session finished");
    expect(eventDetail("media.completed", { kind: "image", path: "C:\\x\\y.png" })).toEqual(["image"]);
  });

  it("a type with no payload keys of its own is the headline alone; an unknown type is empty", () => {
    expect(eventHeadline("memory.updated", { kind: "preference", id: "l1" })).toBe("Memory was updated");
    expect(eventHeadline("zzz.internal_thing", { a: 1 })).toBe("");
    expect(eventHeadline("", null)).toBe("");
  });

  it("a feed row keeps the ledger's own sentence for tokens and state changes", () => {
    expect(rowHeadline({ type: "llm.completed", summary: "mock/mock-1 · 120+40 tok", payload: {} })).toBe(
      "Demo model · 120+40 tok",
    );
    expect(rowHeadline({ type: "agent.state_changed", summary: "agent running→completed", payload: {} })).toBe(
      "Agent went from running to completed",
    );
    expect(rowHeadline({ type: "webhook.rejected", summary: "webhook.rejected", payload: { slug: "s1", reason: "bad_signature" } })).toBe(
      "A webhook was refused · s1 · bad signature",
    );
    // An entry with no type (an older daemon) still reads its summary in words.
    expect(rowHeadline({ summary: "session.completed", payload: {} })).toBe("Session finished");
    expect(rowHeadline({ summary: "zzz.internal_thing", payload: {} })).toBe("zzz.internal_thing");
  });

  it("the Time-travel feed's row reads rowHeadline", () => {
    const feed = read("dashboard/components/TimeTravelFeed.tsx");
    expect(feed).toMatch(/const said = rowHeadline\(e\);/);
  });
});
