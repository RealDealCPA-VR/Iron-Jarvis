/**
 * v1.357.0 integrate — Wave 7's seams, the dashboard half.
 *
 * `__tests__/fixtures/wave7-seams-v13570.json` holds what the REAL daemon
 * sends when a task another computer running Iron Jarvis sent (lane 7A's
 * task door, origin `remote:<caller>`) ends badly: its `session.completed`
 * event and the title the phone/desktop alert gave it
 * (tests/test_integrate_wave7_v13570.py drives a real door task and fails
 * when the daemon stops sending it; regenerate with IJ_REGEN_WAVE7_SEAMS=1).
 * This file feeds that same event through the REAL bell, the REAL origin
 * chip (Sessions, Running now, the session page) and the REAL approval
 * words, so the two lanes' words (7A's origin, 7C's vocabulary) cannot drift.
 *
 * Plus: (b) the low-trust sentence names the web services lane 7B's
 * `rest_call` reaches, and (c) the vocabulary scan reaches every file this
 * wave added or changed.
 *
 * Only the network (lib/api), the event socket and the desktop notification
 * hook are stubbed.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("@/lib/api", () => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: MockApiError,
    get: vi.fn(async () => ({})),
    post: vi.fn(async () => ({})),
    put: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
    API_BASE: "",
    ijToken: () => "",
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: vi.fn(),
  }),
}));

import { toActivity, unattendedDoor } from "@/components/NotificationBell";
import OriginChip, { originLabel } from "@/components/sessions/OriginChip";
import { runSubject } from "@/lib/approvalWords";
import { REMOTE_DEFAULT_CALLER } from "@/lib/remoteTask";
import { LOW_TRUST_CANNOT, LOW_TRUST_FAMILIES } from "@/lib/trustWords";
import type { IJEvent } from "@/lib/types";
import { retiredHits, scannedFiles } from "./helpers/retiredWords";

const ROOT = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(ROOT, rel), "utf-8").replace(/\r\n/g, "\n");

const FIXTURE = JSON.parse(read("__tests__/fixtures/wave7-seams-v13570.json")) as {
  remote_completed: { type: string; session_id: string; payload: Record<string, unknown> };
  phone_title: string;
  default_caller: string;
};

afterEach(() => cleanup());

/* ============================================ (a) G61 x G76: one way to say it */

describe("a task another computer sent reads one way on every surface", () => {
  const ev: IJEvent = {
    id: "ev_remote",
    ts: "2026-10-19T12:00:00Z",
    type: FIXTURE.remote_completed.type,
    session_id: FIXTURE.remote_completed.session_id,
    payload: FIXTURE.remote_completed.payload,
  };
  const origin = String(FIXTURE.remote_completed.payload.origin);

  it("the fixture is the daemon's real failed door task (anti-vacuity)", () => {
    expect(origin).toBe("remote:laptop");
    expect(FIXTURE.remote_completed.payload.status).toBe("failed");
    expect(FIXTURE.default_caller).toBe(REMOTE_DEFAULT_CALLER);
  });

  it("the bell rings for it in the phone alert's own words, and opens the run", () => {
    expect(unattendedDoor(origin)).toEqual({ kind: "remote", name: "laptop" });
    const item = toActivity(ev);
    expect(item).not.toBeNull();
    expect(item!.title).toBe(FIXTURE.phone_title);
    expect(item!.attention).toBe(true);
    expect(item!.body).toBe(FIXTURE.remote_completed.payload.summary);
    expect(item!.href).toBe(`/sessions/${FIXTURE.remote_completed.session_id}`);
    // A clean finish is a quiet row, never a ring (control on the same door).
    const ok = toActivity({
      ...ev,
      payload: { ...ev.payload, status: "completed", ok: true, outcome: "completed" },
    });
    expect(ok!.title).toBe("A task from laptop finished");
    expect(ok!.quiet).toBe(true);
  });

  it("the origin chip says who sent it (Sessions, Running now, the session page)", () => {
    render(<OriginChip origin={origin} />);
    const chip = screen.getByTestId("origin-chip");
    expect(chip.textContent).toBe("From laptop");
    expect(chip.getAttribute("title")).toBe("Started by: remote:laptop");
    expect(originLabel("remote:")).toBe(`From ${FIXTURE.default_caller}`);
    // The session page mounts that chip from the run's own origin.
    expect(read("app/sessions/[id]/page.tsx")).toContain("<OriginChip origin={session.origin} />");
  });

  it("an ask from it (the bell's ask sentence) names it the same way", () => {
    expect(runSubject(origin)).toBe("A task from laptop");
    expect(runSubject("remote:")).toBe(`A task from ${FIXTURE.default_caller}`);
  });
});

/* ============================================ (b) G84 x G77: the low-trust words */

describe("the low-trust sentence names what rest_call reaches", () => {
  it("a family says 'your web services' and the one sentence says it", () => {
    const fam = LOW_TRUST_FAMILIES.find((f) => f.tools.includes("rest_call"));
    expect(fam?.words).toBe("your web services");
    expect(LOW_TRUST_CANNOT).toContain("can't call your web services");
  });
});

/* ============================================ (c) the scan reaches this wave */

describe("the vocabulary scan reaches every file this wave added or changed", () => {
  const FILES = [
    "components/agents/AnotherJarvisCard.tsx",
    "components/agents/SetupCard.tsx",
    "components/connections/RestHookups.tsx",
    "components/settings/pages/ToolsPage.tsx",
    "components/settings/pages/ConnectionsPage.tsx",
    "components/chat/TurnReceipt.tsx",
    "lib/anotherJarvis.ts",
    "lib/remoteTask.ts",
    "lib/trustWords.ts",
    "lib/toolWords.ts",
    "lib/extensionWords.ts",
    "app/chat/page.tsx",
  ];

  it("each is scanned and carries no retired word a user reads", () => {
    const scanned = new Set(scannedFiles());
    for (const f of FILES) {
      expect(scanned.has(f), f).toBe(true);
      const hits = retiredHits(f)
        // vocabulary.test.ts ALLOW: an agent's job description, not a unit of work.
        .filter((h) => !(f === "components/agents/SetupCard.tsx" && h.text.includes("Job card")))
        .map((h) => `${h.file}:${h.line} [${h.word}] ${h.text.slice(0, 110)}`);
      expect(hits, f).toEqual([]);
    }
  });

  it("anti-vacuity: the same scan flags a retired word planted in one of them", () => {
    const planted = `${read("lib/remoteTask.ts")}\nexport const PLANTED = "The daemon took the job.";\n`;
    const words = retiredHits("lib/remoteTask.ts", planted).map((h) => h.word);
    expect(words).toEqual(expect.arrayContaining(["daemon"]));
  });
});
