/**
 * Next leaps 12-a (build-out plan R2.5): coding work started from the phone
 * is on Coding sessions › Tonight like any night task, and its row says
 * "From your phone" (components/harness/TonightList, lib/nightShift). A row
 * queued on the PC says nothing of the kind; an older Iron Jarvis that sends
 * no field reads as not from the phone. Where the ending goes (the phone
 * chat) is never on the page.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

const api = vi.hoisted(() => ({ list: null as unknown }));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status = 0;
  }
  return {
    ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    get: (p: string) => (p === "/build/night" ? Promise.resolve(api.list) : Promise.reject(new ApiError(`unmocked ${p}`))),
    post: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({ ok: true }),
  };
});
vi.mock("@/lib/useModels", () => ({
  useModels: () => ({ models: [], usable: [], data: { models: [] }, loading: false, error: null, reload: () => {} }),
}));

import { TonightList } from "@/components/harness/TonightList";
import { FROM_PHONE_WORDS, decodeTask } from "@/lib/nightShift";

function task(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    folder: "C:\\code\\website",
    words: `Task ${id}`,
    provider: "",
    model: "",
    team: false,
    try_on: [],
    position: 1,
    state: "queued",
    thread_id: "",
    turn_id: "",
    branch: "",
    notes: [],
    created_at: "2026-11-25T15:00:00Z",
    updated_at: "2026-11-25T15:00:00Z",
    started_at: null,
    ended_at: null,
    ...over,
  };
}

function listOf(tasks: unknown[]) {
  return {
    tasks,
    window: { start: "23:00", end: "07:00", open: false, words: "Starts at 11:00 PM tonight.", next_start: null },
    limits: { at_once: 2, task_minutes: 90, card_wait_minutes: 15 },
    emergency_stop: false,
  };
}

beforeEach(() => {
  api.list = listOf([]);
});
afterEach(() => cleanup());

describe("Tonight: coding work from your phone", () => {
  it("says From your phone on a row the phone started, and nothing on the others", async () => {
    api.list = listOf([
      task("p1", {
        state: "running", thread_id: "s1", branch: "ij/fix-the-broken-contact-for-1a2b",
        asked_from: "comm:telegram", from_phone: true, start_now: true,
        // A field the page must never show, even if a daemon sent it.
        report_json: "{\"channel\":\"telegram\",\"sender_id\":\"777\"}",
      }),
      task("t1"),
    ]);
    render(<TonightList folders={["C:\\code\\website"]} onOpenSession={vi.fn()} />);
    await screen.findByTestId("tonight-window");
    const phoneRow = screen.getByTestId("tonight-task-p1");
    expect(within(phoneRow).getByTestId("tonight-from-phone-p1").textContent).toBe(" · From your phone");
    expect(within(phoneRow).getByText(/ij\/fix-the-broken-contact-for-1a2b/)).toBeTruthy();
    expect(screen.queryByTestId("tonight-from-phone-t1")).toBeNull();
    expect(screen.getByTestId("tonight-task-t1").textContent).not.toContain(FROM_PHONE_WORDS);
    expect(document.body.textContent).not.toContain("sender_id");
    expect(document.body.textContent).not.toContain("777");
  });

  it("decodes the two fields, and an older Iron Jarvis's row reads as not from the phone", () => {
    const fromPhone = decodeTask(task("p1", { from_phone: true, start_now: true }));
    expect(fromPhone?.fromPhone).toBe(true);
    expect(fromPhone?.startNow).toBe(true);
    const older = decodeTask(task("o1"));
    expect(older?.fromPhone).toBe(false);
    expect(older?.startNow).toBe(false);
    // Only a real true counts.
    expect(decodeTask(task("x", { from_phone: "yes" }))?.fromPhone).toBe(false);
  });
});
