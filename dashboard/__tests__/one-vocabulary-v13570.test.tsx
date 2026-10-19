/**
 * v1.357.0 (product readiness G76): ONE vocabulary.
 *
 * ACCEPTANCE: "The vocabulary test fails on any new user-visible 'daemon',
 * 'pack', 'mock' or 'pane', and the demo is called the same thing on every
 * surface."
 *
 * vocabulary.test.ts runs the scan over the real tree (and its probe proves
 * the reader reads). This file pins the rest through the REAL code:
 *
 * 1. The demo model has one set of words (lib/demoWords) and every surface
 *    that tells the user a reply was not real says them: the chat receipt,
 *    a teammate card, the live step line, the downgrade banner (which no
 *    longer echoes the daemon's 'mock'), the chip, the strip under the title
 *    bar, the provider label. A scan of every readable string then fails on
 *    any OTHER way of naming the demo ("demo mode", "offline demo").
 * 2. A task another Jarvis sent (origin `remote:<caller>`, lane 7A's task
 *    door) reads "From <caller>" on the chip and "A task from <caller>" in
 *    the bell and an ask's subject, keyed on the prefix only.
 * 3. The unit of work is a task: the origin chip, the home line and the
 *    background-task names say task, never job / assignment / Mission.
 * 4. Each of the four words the acceptance names, added new to a readable
 *    string, is a hit, in every shape a user reads (JSX text, a title, an
 *    aria-label, a thrown error's message, a ternary piece of a sentence).
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const D = vi.hoisted(() => ({ health: null as Record<string, unknown> | null }));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: D.health }),
}));

vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: false,
    permission: "default" as const,
    requestPermission: async () => "default" as const,
    notify: () => undefined,
  }),
}));

import {
  DEMO_ANSWER,
  DEMO_CHIP_TITLE,
  DEMO_MODEL,
  DEMO_MODEL_LABEL,
  DEMO_REPLIES,
  DEMO_RUNNING,
  DEMO_STATE,
} from "@/lib/demoWords";
import { providerDisplay } from "@/lib/onboarding";
import { routeWarning, TurnReceipt } from "@/components/chat/TurnReceipt";
import { mockLine } from "@/components/agents/mission/AgentCards";
import { stepLabel } from "@/components/chat/stepLabel";
import { DEMO_TRAP_DETAIL, downgradeNotice } from "@/components/ProviderDowngradeBanner";
import { MockChip } from "@/components/ui";
import { SimulatedBanner } from "@/components/SimulatedBanner";
import OriginChip, { originLabel } from "@/components/sessions/OriginChip";
import { runSubject } from "@/lib/approvalWords";
import { toActivity, unattendedDoor } from "@/components/NotificationBell";
import { homeLineItems } from "@/components/chat/HomeLine";
import { LOOP_LABELS } from "@/lib/loopWords";
import { REMOTE_DEFAULT_CALLER, remoteCaller, remoteTaskSubject } from "@/lib/remoteTask";
import type { IJEvent } from "@/lib/types";
import { readableCopy, retiredHits, scannedFiles } from "./helpers/retiredWords";

afterEach(() => {
  cleanup();
  D.health = null;
});

const ev = (type: string, payload: Record<string, unknown>, session_id: string | null = "s1"): IJEvent =>
  ({ id: "e1", type, ts: "2026-10-19T07:00:05+00:00", session_id, payload }) as IJEvent;

/** Any other way of naming the demo — the six names the audit counted. */
const OTHER_DEMO_NAMES = /\bmock\b|offline (?:mock|demo|model)|simulated|fabricated|demo mode\b/i;

/* ======================================================= 1. the demo's words */

describe("the demo is called the same thing on every surface", () => {
  it("the words themselves: one name, one sentence, one line under a reply", () => {
    expect(DEMO_MODEL).toBe("demo model");
    expect(DEMO_MODEL_LABEL).toBe("Demo model (scripted)");
    expect(DEMO_REPLIES).toBe("a scripted demo, not real answers");
    expect(DEMO_ANSWER).toBe("Demo answer. No AI model ran.");
    expect(DEMO_RUNNING).toBe("On the demo model. No AI model ran.");
    expect(DEMO_CHIP_TITLE).toBe("Ran on the built-in demo model: a scripted demo, not real answers.");
    for (const w of [DEMO_MODEL, DEMO_MODEL_LABEL, DEMO_REPLIES, DEMO_ANSWER, DEMO_RUNNING, DEMO_STATE, DEMO_CHIP_TITLE])
      expect(w, w).not.toMatch(OTHER_DEMO_NAMES);
  });

  it("the provider label, the receipt, a teammate card and the live step line say the same answer line", () => {
    expect(providerDisplay("mock")).toBe(DEMO_MODEL_LABEL);
    expect(routeWarning({ provider: "mock", reason: "mock" })).toBe(DEMO_ANSWER);
    expect(mockLine(true)).toBe(DEMO_ANSWER);
    expect(mockLine(false)).toBe(DEMO_RUNNING);
    expect(
      stepLabel(ev("provider.downgraded", { requested: "mock (default)", used: "mock", reason: "your default provider is 'mock'" })),
    ).toBe(DEMO_ANSWER);
  });

  it("the chat receipt shows it on the collapsed line", () => {
    render(<TurnReceipt route={{ provider: "mock", reason: "mock" }} />);
    expect(screen.getByText(DEMO_ANSWER)).toBeTruthy();
    expect(document.body.textContent ?? "").not.toMatch(OTHER_DEMO_NAMES);
  });

  it("the downgrade banner says it in the canon's words, never echoing the daemon's 'mock'", () => {
    // The router's own reason for this event names the provider id; the
    // banner must not pass it through.
    const n = downgradeNotice(
      ev("provider.downgraded", {
        requested: "mock (default)",
        used: "mock",
        reason: "your default provider is 'mock' but a real provider is connected. Make it your default on the Connections page.",
      }),
    )!;
    expect(n.kind).toBe("mock");
    expect(n.title).toBe(DEMO_ANSWER);
    expect(n.detail).toBe(DEMO_TRAP_DETAIL);
    expect(`${n.title} ${n.detail}`).toContain(DEMO_MODEL);
    expect(`${n.title} ${n.detail}`).not.toMatch(OTHER_DEMO_NAMES);
  });

  it("the chip says the name and its hover says the sentence", () => {
    render(<MockChip />);
    const chip = screen.getByText(DEMO_MODEL).closest("span")!;
    expect(chip.getAttribute("title")).toBe(DEMO_CHIP_TITLE);
  });

  it("the strip under the title bar says the same in both of its cases", () => {
    // 1. nothing connected.
    D.health = { default_provider: "mock", providers: [] };
    const { unmount } = render(<SimulatedBanner />);
    let strip = screen.getByTestId("simulated-banner");
    expect(strip.textContent).toContain(DEMO_STATE);
    expect(strip.textContent).toContain(`replies are ${DEMO_REPLIES}.`);
    expect(strip.textContent).not.toMatch(OTHER_DEMO_NAMES);
    unmount();
    // 2. a model is connected but nobody chose it.
    D.health = { default_provider: "mock", providers: [{ provider: "claude-cli", available: true }] };
    render(<SimulatedBanner />);
    strip = screen.getByTestId("simulated-banner");
    expect(strip.textContent).toContain(DEMO_STATE);
    expect(strip.textContent).toContain(`replies are ${DEMO_REPLIES}.`);
    expect(strip.textContent).not.toMatch(OTHER_DEMO_NAMES);
  });

  it("no readable string anywhere names the demo any other way", () => {
    // Every piece of copy that mentions the demo uses one of its forms.
    const CANON = /demo model|scripted demo|Demo answer|Demo replies/i;
    const off: string[] = [];
    for (const f of scannedFiles()) {
      for (const p of readableCopy(f)) {
        const t = p.text.replace(/\s+/g, " ").trim();
        if (/\bdemo\b/i.test(t) && !CANON.test(t)) off.push(`${f}:${p.line}: ${t.slice(0, 100)}`);
        if (/offline (?:mock|demo|model)|simulated mode|fabricated by/i.test(t)) off.push(`${f}:${p.line}: ${t.slice(0, 100)}`);
      }
    }
    expect(off).toEqual([]);
  });

  it("that scan reads what it claims (probe)", () => {
    const probe = [
      `export const A = () => (`,
      `  <p title="Skip, try demo mode">`,
      `    That was the offline demo.`,
      `    <span>{DEMO_ANSWER}</span>`,
      `  </p>`,
      `);`,
      `const ok = "Replies are a scripted demo, not real answers.";`,
    ].join("\n");
    const texts = readableCopy("probe.tsx", probe).map((p) => p.text.trim());
    expect(texts).toContain("Skip, try demo mode");
    expect(texts).toContain("That was the offline demo.");
    expect(texts).toContain("Replies are a scripted demo, not real answers.");
    const CANON = /demo model|scripted demo|Demo answer|Demo replies/i;
    expect(texts.filter((t) => /\bdemo\b/i.test(t) && !CANON.test(t))).toEqual([
      "Skip, try demo mode",
      "That was the offline demo.",
    ]);
  });
});

/* ===================================================== 2. another Jarvis */

describe("a task another Jarvis sent reads as who sent it", () => {
  it("the chip's words, keyed on the prefix", () => {
    expect(originLabel("remote:desk")).toBe("From desk");
    expect(originLabel("remote:Office PC")).toBe("From Office PC");
    // The daemon stamps its default caller when the sender names none.
    expect(originLabel("remote:another computer")).toBe("From another computer");
    expect(originLabel("remote:")).toBe(`From ${REMOTE_DEFAULT_CALLER}`);
    expect(remoteCaller("remote:  desk ")).toBe("desk");
    // Only the prefix decides: a name that merely starts with the word is
    // not one (and an unknown origin is still shown as it is).
    expect(originLabel("remotely:x")).toBe("remotely:x");
  });

  it("the chip shows the words and keeps the raw origin", () => {
    render(<OriginChip origin="remote:desk" />);
    const chip = screen.getByTestId("origin-chip");
    expect(chip.textContent).toBe("From desk");
    expect(chip.getAttribute("data-origin")).toBe("remote:desk");
    expect(chip.getAttribute("title")).toBe("Started by: remote:desk");
  });

  it("an ask's subject and the bell's row say 'A task from <caller>'", () => {
    expect(runSubject("remote:desk")).toBe("A task from desk");
    expect(remoteTaskSubject("")).toBe(`A task from ${REMOTE_DEFAULT_CALLER}`);
    expect(unattendedDoor("remote:desk")).toEqual({ kind: "remote", name: "desk" });
    expect(unattendedDoor("remotely:x")).toBeNull();

    // Nobody at this PC watches it: a failure rings like the other doors.
    const failed = toActivity(
      ev("session.completed", { status: "failed", ok: false, outcome: null, origin: "remote:desk", summary: "It broke." }),
    )!;
    expect(failed.title).toBe("A task from desk could not finish");
    expect(failed.attention).toBe(true);
    expect(failed.href).toBe("/sessions/s1");

    // A clean finish is a quiet row in the same words.
    const done = toActivity(
      ev("session.completed", { status: "completed", outcome: "completed", origin: "remote:desk", summary: "Done." }),
    )!;
    expect(done.title).toBe("A task from desk finished");
    expect(done.quiet).toBe(true);

    const nameless = toActivity(ev("session.completed", { status: "failed", ok: false, origin: "remote:" }))!;
    expect(nameless.title).toBe(`A task from ${REMOTE_DEFAULT_CALLER} could not finish`);
  });
});

/* ====================================================== 3. a unit of work */

describe("the unit of work is a task", () => {
  it("origins, asks, the home line and the background names say task / objective", () => {
    expect(originLabel("assignment:asg_123abc")).toBe("Queued task");
    expect(originLabel("job:mission")).toBe("Objective");
    expect(originLabel("job:mission-member")).toBe("Objective teammate");
    expect(originLabel("job:nightly")).toBe("Task · nightly");
    expect(runSubject("job:mission")).toBe("Your objective");
    expect(runSubject("job:mission-member")).toBe("A teammate on your objective");
    const base = { interrupted: 0, failingLoops: 0, failingPacks: 0, running: 0, waiting: 0 };
    expect(homeLineItems({ ...base, interrupted: 2 })[0].text).toBe("2 tasks were interrupted by the last restart.");
    expect(homeLineItems({ ...base, shortSchedules: 1 })[0].text).toBe("1 scheduled task needs a look");
    expect(LOOP_LABELS.dispatcher).toBe("Queued tasks");
    expect(toActivity(ev("assignment.requeued", { count: 2 }))!.title).toBe(
      "2 queued tasks picked back up after a restart",
    );
    expect(toActivity(ev("schedule.fired", { name: "brief" }))!.title).toBe("Scheduled task ran: brief");
  });
});

/* ===================================== 4. the four words the acceptance names */

describe("a NEW user-visible daemon, pack, mock or pane is a hit", () => {
  const shapes = (word: string) => [
    `export const A = () => <p>Open the ${word} now</p>;`,
    `export const B = () => <button title="Close this ${word}" />;`,
    `export const C = () => <input aria-label="Rename ${word}" />;`,
    `export function d() { throw new Error("The ${word} did not answer."); }`,
    `export const E = ({ n }: { n: number }) => <span>{n === 1 ? "${word}" : "${word}s"}</span>;`,
    `export const F = (n: number) => \`\${n} ${word}\${n === 1 ? "" : "s"} need you\`;`,
  ];

  it.each(["daemon", "pack", "mock", "pane"])("'%s' in every shape a user reads", (word) => {
    for (const src of shapes(word)) {
      const hits = retiredHits("probe.tsx", src);
      expect(hits.length, src).toBeGreaterThan(0);
      expect(hits.every((h) => h.word === word), src).toBe(true);
    }
  });

  it.each(["daemon", "pack", "mock", "pane"])("'%s' as an id, a key, a route or a comment is not", (word) => {
    const src = [
      `// the ${word} is fine in a comment`,
      `const id = "${word}-rail";`,
      `const route = "/mcp/${word}s";`,
      `const k = { "${word}": 1 };`,
      `const same = provider === "${word}";`,
      `export const A = () => <div data-testid="${word}-x" className="${word}" />;`,
    ].join("\n");
    expect(retiredHits("probe.tsx", src)).toEqual([]);
  });
});
