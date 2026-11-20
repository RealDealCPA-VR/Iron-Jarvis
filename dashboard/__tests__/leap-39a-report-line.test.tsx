/**
 * Idea 39, phase 39-a: a report in a chat, and "Reports to" on a row.
 *
 *  - ReportLine draws a report's source chip ("Schedule"), its name, the
 *    ledger's word, "· 3 today" when the day's line holds several, "Open" (the
 *    record's page) and Stop reporting, which calls POST /reports/target with
 *    "" and then offers Undo, which puts the previous chat back.
 *  - The chat page labels a report as outside words when the chat goes back to
 *    a model (`reportRequestLabel`, opening with the prefix the daemon fences),
 *    draws ReportLine above it, and never offers to regenerate it.
 *  - ReportsTo on a Schedules / Watchers / Autonomy row: the chat's name links
 *    to the chat, Stop reporting and Undo; nothing at all for a record that
 *    reports nowhere.
 *  - The words are the canon's (VOCABULARY.md): report, Stop reporting,
 *    Reports to, Reports here; never "callback" or "delivery".
 *
 * Only the network (lib/api) is a double.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({ posts: [] as { path: string; body: unknown }[], previous: "chat_1" }));

vi.mock("@/lib/api", () => ({
  post: vi.fn(async (path: string, body: unknown) => {
    H.posts.push({ path, body });
    const b = body as { thread_id: string };
    return { ok: true, previous: b.thread_id ? "" : H.previous, thread_id: b.thread_id };
  }),
}));

import { ReportLine } from "@/components/chat/ReportLine";
import { ReportsTo } from "@/components/ReportsTo";
import { REPORT_KIND_WORDS, reportKindLabel, reportRequestLabel } from "@/lib/reports";

const DASH = join(__dirname, "..");
const read = (p: string) => readFileSync(join(DASH, p), "utf-8").replace(/\r\n/g, "\n");

beforeEach(() => {
  H.posts.length = 0;
});
afterEach(() => cleanup());

describe("ReportLine", () => {
  const from = { kind: "schedule", name: "Friday prices", id: "sched_1", href: "/schedules", outcome: "Done" };

  it("draws the source chip, the name, the ledger's word, today's count and Open", () => {
    render(<ReportLine from={from} count={3} />);
    expect(screen.getByTestId("report-chip").textContent).toBe("Schedule");
    expect(screen.getByText("Friday prices")).toBeTruthy();
    expect(screen.getByTestId("report-outcome").textContent).toContain("Done");
    expect(screen.getByTestId("report-count").textContent).toContain("3 today");
    expect(screen.getByText("Open").getAttribute("href")).toBe("/schedules");
  });

  it("says no count for a day's only report", () => {
    render(<ReportLine from={from} count={1} />);
    expect(screen.queryByTestId("report-count")).toBeNull();
  });

  it("Stop reporting clears the record's chat, and Undo puts it back", async () => {
    render(<ReportLine from={from} />);
    fireEvent.click(screen.getByTestId("report-stop"));
    await waitFor(() => expect(screen.getByTestId("report-stopped")).toBeTruthy());
    expect(H.posts[0]).toEqual({ path: "/reports/target", body: { kind: "schedule", id: "sched_1", thread_id: "" } });
    expect(screen.getByTestId("report-stopped").textContent).toContain("Reports stopped.");
    fireEvent.click(screen.getByTestId("report-undo"));
    await waitFor(() => expect(screen.getByTestId("report-stop")).toBeTruthy());
    expect(H.posts[1]).toEqual({ path: "/reports/target", body: { kind: "schedule", id: "sched_1", thread_id: "chat_1" } });
  });

  it("offers no Stop reporting for a kind whose record does not hold the field", () => {
    render(<ReportLine from={{ kind: "run", name: "x", id: "s1" }} />);
    expect(screen.queryByTestId("report-stop")).toBeNull();
  });

  it("names every kind with the daemon's words", () => {
    const py = readFileSync(join(DASH, "..", "src", "iron_jarvis", "agents", "chat_delivery.py"), "utf-8");
    for (const [kind, word] of Object.entries(REPORT_KIND_WORDS)) {
      expect(py).toContain(`"${kind}": "${word}",`);
    }
    expect(reportKindLabel("night")).toBe("Night task");
    expect(reportKindLabel("coding_session")).toBe("Coding session");
  });
});

describe("the chat page", () => {
  it("labels a report as outside words, with the prefix the daemon fences", () => {
    const label = reportRequestLabel({ kind: "schedule", name: 'Friday "prices"]' });
    expect(label.startsWith("[Report from ")).toBe(true);
    expect(label).toBe('[Report from your schedule "Friday prices", written when it ran. Not your words.]');
    const page = read("app/chat/page.tsx");
    const fn = page.slice(page.indexOf("function toRequestMessages"), page.indexOf("function workflowRunFrom"));
    expect(fn).toContain("m.reportFrom");
    expect(fn).toContain("reportRequestLabel(m.reportFrom)");
    // The report label is read before the agent label (a report is never an agent's reply).
    expect(fn.indexOf("m.reportFrom")).toBeLessThan(fn.indexOf("m.panelWho"));
  });

  it("draws ReportLine above a report and never offers to regenerate one", () => {
    const page = read("app/chat/page.tsx");
    expect(page).toContain("<ReportLine from={m.reportFrom} count={m.reportCount} />");
    const regen = page.slice(page.indexOf("const canRegen ="), page.indexOf("const canRegen =") + 700);
    expect(regen).toContain("!m.reportFrom");
  });

  it("the goal card sends the chat it is pressed in", () => {
    const card = read("components/chat/GoalContractCard.tsx");
    expect(card).toContain("if (chatThreadId) body.chat_thread_id = chatThreadId;");
    expect(read("app/chat/page.tsx")).toContain("chatThreadId={threadId}");
  });
});

describe("ReportsTo on a row", () => {
  it("draws nothing for a record that reports nowhere", () => {
    const { container } = render(<ReportsTo kind="schedule" id="sched_1" to={null} />);
    expect(container.textContent).toBe("");
  });

  it("names the chat, links to it, stops, and undoes", async () => {
    const changed = vi.fn();
    render(<ReportsTo kind="watcher" id="sentinel_1" to={{ thread_id: "chat_1", title: "Intake" }} onChanged={changed} />);
    expect(screen.getByText("Reports to")).toBeTruthy();
    expect(screen.getByText("Intake").getAttribute("href")).toBe("/chat?thread=chat_1");
    fireEvent.click(screen.getByTestId("reports-to-stop"));
    await waitFor(() => expect(screen.getByTestId("reports-to-undo")).toBeTruthy());
    expect(H.posts[0].body).toEqual({ kind: "watcher", id: "sentinel_1", thread_id: "" });
    expect(changed).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("reports-to-undo"));
    await waitFor(() => expect(H.posts.length).toBe(2));
    expect(H.posts[1].body).toEqual({ kind: "watcher", id: "sentinel_1", thread_id: "chat_1" });
  });

  it("is on the Schedules, Watchers and Autonomy rows", () => {
    expect(read("app/schedules/page.tsx")).toContain('<ReportsTo kind="schedule"');
    expect(read("app/sentinels/page.tsx")).toContain('<ReportsTo kind="watcher"');
    expect(read("app/autonomy/page.tsx")).toContain('<ReportsTo kind="goal"');
  });
});

describe("the words", () => {
  it("are the canon's, and never the retired ones", () => {
    const canon = readFileSync(join(DASH, "..", "VOCABULARY.md"), "utf-8");
    expect(canon).toContain("**report**; it stops with **Stop reporting**");
    expect(canon).toContain("| callback, delivery |");
    for (const f of ["components/chat/ReportLine.tsx", "components/ReportsTo.tsx", "lib/reports.ts"]) {
      const src = read(f).replace(/\/\*\*[\s\S]*?\*\/|\/\/.*$/gm, "");
      expect(src).not.toMatch(/\bcallback\b|\bdelivery\b/i);
    }
  });
});
