/**
 * v1.420.0, Lane O's review follow-ups (for the owners of lib/mission.ts and
 * ConnectionsPage):
 *
 * - A remote that TOOK the job and reports back later is `waiting_remote` on
 *   the daemon's mission view; `lib/mission.decode` used to map any unknown
 *   status to "queued", so the card said "Queued" for a job another computer
 *   was working on. It is a status of its own now, worded by statusWords.
 * - The Connections row for a CLI that is not on this PC rendered an EMPTY
 *   anchor when the CLI has no install page (Grok: no link beats a wrong one).
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { decodeMission, statusWord } from "@/lib/mission";

const read = (p: string) => readFileSync(join(process.cwd(), p), "utf8").replace(/\r\n/g, "\n");

function view(status: string) {
  const v = decodeMission({
    found: true,
    session: { id: "s1", status: "active", outcome: null },
    coordinator: { name: "Jarvis", status: "working", waiting_on: null, steps: 1 },
    members: [{ agent: "hermes", kind: "remote", status, progress: { pct: null, label: "" }, waiting_on: null }],
    activity: [],
    deliverable: null,
  });
  if (!v) throw new Error("the view did not decode");
  return v;
}

describe("a remote that will report back is its own status (v1.420.0)", () => {
  it("decodes waiting_remote instead of reading it as queued", () => {
    const v = view("waiting_remote");
    expect(v.members[0]?.status).toBe("waiting_remote");
    expect(statusWord("waiting_remote")).toBe("Will report back");
    // the control: an unknown value still reads queued
    expect(view("made-up").members[0]?.status).toBe("queued");
  });

  it("the card draws it as working, not as needing the user", () => {
    const src = read("components/agents/mission/AgentCards.tsx");
    expect(src).toMatch(/status === "waiting_remote"\s*\?\s*"bg-accent\/60"/);
    expect(src).toContain('status === "working" || status === "waiting_you" || status === "waiting_remote"');
  });
});

describe("a CLI with no install page gets no empty link (v1.420.0)", () => {
  it("ConnectionsPage renders the anchor only with both a url and a label", () => {
    const src = read("components/settings/pages/ConnectionsPage.tsx");
    expect(src).toContain("{cli.installUrl && cli.installLabel ? (");
    const i = src.indexOf("{cli.installUrl && cli.installLabel ? (");
    const after = src.slice(i, i + 400);
    expect(after).toContain("<a href={cli.installUrl}");
  });
});
