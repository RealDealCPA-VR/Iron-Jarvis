/**
 * v1.400.8 — the Tonight form's "Try it on several models" is the coding
 * session box's own grouped list (the user, after v1.400.7 grouped the box's
 * Try on: "give the Tonight night-task form's try-on checkboxes the same
 * grouped-by-provider list").
 *
 *  - one row per provider, in lib/tryOnMenu's order (your own hardware first),
 *    each marked where it runs; no checkbox until a provider is opened, and it
 *    opens IN PLACE, under its row, inside the form (never a pop-out fixed to
 *    the window: the form sits in a scrolling column);
 *  - every model says local or cloud; no model twice; the demo model never;
 *  - a pick SENDS its catalog pair (the daemon refuses any other); 2 or 3;
 *  - the find box finds across providers and its Enter never sends the form;
 *    Escape closes an open provider and leaves the form alone;
 *  - editing a task whose saved picks the list no longer shows (the gateway's
 *    fleet twin) keeps them in sight, removable.
 *
 * The catalog is the user's (helpers/tryOnCatalog).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return { ApiError, list: null as unknown, calls: [] as [string, string, unknown][] };
});
vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) => {
    api.calls.push(["GET", p, undefined]);
    if (p === "/build/night") return Promise.resolve(api.list);
    return Promise.reject(new api.ApiError(`unmocked ${p}`, 404));
  },
  post: (p: string, b?: unknown) => {
    api.calls.push(["POST", p, b]);
    if (p === "/build/night") return Promise.resolve({ id: "new", state: "queued", words: (b as { words: string }).words });
    return Promise.resolve({});
  },
  patch: (p: string, b?: unknown) => {
    api.calls.push(["PATCH", p, b]);
    return Promise.resolve({ id: "q1", state: "queued" });
  },
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({ ok: true }),
}));
vi.mock("@/lib/useModels", async () => {
  const { CATALOG } = await import("./helpers/tryOnCatalog");
  const usable = CATALOG.filter((m) => m.available !== false);
  return {
    useModels: () => ({ models: CATALOG, usable, data: { models: CATALOG }, loading: false, error: null, reload: () => {} }),
  };
});

import { TonightList } from "@/components/harness/TonightList";
import { CATALOG, catalogPairs } from "./helpers/tryOnCatalog";
import { tryOnGroups } from "@/lib/tryOnMenu";

function task(id: string, over: Record<string, unknown> = {}) {
  return {
    id, folder: "C:\\work\\shop", words: `Task ${id}`, provider: "", model: "", team: false, try_on: [],
    position: 1, state: "queued", thread_id: "", turn_id: "", branch: "", notes: [],
    created_at: "2026-10-28T08:00:00Z", updated_at: "2026-10-28T08:00:00Z", started_at: null, ended_at: null,
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

async function openForm() {
  render(<TonightList folders={["C:\\work\\shop"]} onOpenSession={() => {}} />);
  await screen.findByTestId("tonight-window");
  fireEvent.click(screen.getByTestId("tonight-add"));
  fireEvent.change(screen.getByTestId("tonight-folder"), { target: { value: "C:\\work\\shop" } });
  fireEvent.change(screen.getByTestId("tonight-words"), { target: { value: "Fix the parser" } });
  fireEvent.change(screen.getByTestId("tonight-how"), { target: { value: "try" } });
  return screen.getByTestId("tonight-try-on");
}
const groupRows = (box: HTMLElement) => within(box).getAllByTestId("harness-try-on-group");
const group = (box: HTMLElement, id: string) => groupRows(box).find((g) => g.dataset.group === id)!;
const rows = (box: HTMLElement) => within(box).queryAllByTestId("harness-try-on-row") as HTMLInputElement[];
function tick(box: HTMLElement, provider: string, model: string): HTMLInputElement {
  const g = group(box, provider === "anthropic" ? "claude-cli" : provider === "openai" ? "codex-cli" : provider);
  if (g.getAttribute("aria-expanded") !== "true") fireEvent.click(g);
  const check = rows(box).find((c) => c.dataset.provider === provider && c.dataset.model === model)!;
  fireEvent.click(check);
  return check;
}
const lastBody = (method: string) => {
  const sent = api.calls.filter(([m, p]) => m === method && p.startsWith("/build/night"));
  return sent[sent.length - 1]?.[2] as { try_on: { provider: string; model: string }[] } | undefined;
};

beforeEach(() => {
  api.calls = [];
  api.list = listOf([]);
});
afterEach(() => cleanup());

describe("Tonight's try-on list is the box's grouped list", () => {
  it("one row per provider, in the Try on list's order, each marked where it runs; no checkbox until one is opened", async () => {
    const box = await openForm();
    const expected = tryOnGroups(CATALOG, false);
    expect(groupRows(box).map((g) => g.dataset.group)).toEqual(expected.map((g) => g.id));
    expect(groupRows(box).map((g) => g.dataset.group)).toEqual(["fleet-rtx6000ada", "custom", "claude-cli", "codex-cli"]);
    expect(within(box).getAllByTestId("harness-try-on-group-place").map((b) => b.textContent)).toEqual([
      "local",
      "local + cloud",
      "cloud · on your plan",
      "cloud · on your plan",
    ]);
    expect(rows(box)).toHaveLength(0);
    // The gateway is named once; the demo model and the keyless APIs are no rows.
    expect(within(box).getByText("Your model server")).toBeTruthy();
    for (const gone of ["mock", "anthropic", "openai", "fleet-custom"]) {
      expect(groupRows(box).some((g) => g.dataset.group === gone)).toBe(false);
    }
  });

  it("a provider opens in place, under its row inside the form; every model says local or cloud", async () => {
    const box = await openForm();
    fireEvent.click(group(box, "custom"));
    const panel = within(box).getByTestId("harness-try-on-panel");
    // Under its own row, in the form's flow — not a pop-out fixed to the window.
    expect(panel.parentElement).toBe(group(box, "custom").parentElement);
    expect(panel.style.position).toBe("");
    expect(box.contains(panel)).toBe(true);
    expect(group(box, "custom").getAttribute("aria-expanded")).toBe("true");
    expect(rows(box).map((r) => r.dataset.model)).toEqual([
      "glm-5.3-flash",
      "frontier",
      "deepseek/deepseek-v4.1-flash",
      "~z-ai/glm-flash-latest",
    ]);
    expect(within(panel).getAllByTestId("harness-try-on-place").map((p) => p.textContent)).toEqual([
      "local",
      "cloud",
      "cloud",
      "cloud",
    ]);
    expect(within(panel).getByText("On your network")).toBeTruthy();
    expect(within(panel).getByText("Passed on to the cloud")).toBeTruthy();
    // One provider open at a time.
    fireEvent.click(group(box, "claude-cli"));
    expect(within(box).getAllByTestId("harness-try-on-panel")).toHaveLength(1);
    expect(rows(box).every((r) => r.dataset.provider === "claude-cli" || r.dataset.provider === "anthropic")).toBe(true);
  });

  it("picks send their catalog pairs, are listed on top with where they run, and stop at three", async () => {
    const box = await openForm();
    tick(box, "anthropic", "claude-opus-4-8");
    expect(within(box).getByTestId("harness-try-on-group-picked").textContent).toBe("1 picked");
    tick(box, "custom", "glm-5.3-flash");
    tick(box, "codex-cli", "subscription");
    const picked = within(box).getByTestId("harness-try-on-picked");
    expect(within(picked).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Opus 4.8 · Claude Code",
      "glm-5.3-flash · Your model server",
      expect.stringMatching(/ · Codex$/),
    ]);
    // A fourth is refused in place, and says why.
    fireEvent.click(group(box, "fleet-rtx6000ada"));
    const fourth = rows(box)[0];
    expect(fourth.disabled).toBe(true);
    expect(fourth.closest("label")!.getAttribute("title")).toBe("You picked 3 already. Untick one to pick this.");
    fireEvent.click(screen.getByTestId("tonight-submit"));
    await waitFor(() => expect(lastBody("POST")).toBeTruthy());
    const body = lastBody("POST")!;
    expect(body.try_on).toEqual([
      { provider: "anthropic", model: "claude-opus-4-8" },
      { provider: "custom", model: "glm-5.3-flash" },
      { provider: "codex-cli", model: "subscription" },
    ]);
    for (const p of body.try_on) expect(catalogPairs.has(`${p.provider}::${p.model}`)).toBe(true);
  });

  it("a pick is removed from the list on top, and from its own checkbox", async () => {
    const box = await openForm();
    tick(box, "anthropic", "claude-opus-4-8");
    tick(box, "custom", "glm-5.3-flash");
    fireEvent.click(within(box).getByRole("button", { name: "Do not try it on Opus 4.8" }));
    tick(box, "custom", "glm-5.3-flash");
    expect(within(box).queryByTestId("harness-try-on-picked")).toBeNull();
    tick(box, "fleet-rtx6000ada", "Qwen3.8-27B (RTX)");
    tick(box, "codex-cli", "subscription");
    fireEvent.click(screen.getByTestId("tonight-submit"));
    await waitFor(() => expect(lastBody("POST")).toBeTruthy());
    expect(lastBody("POST")!.try_on).toEqual([
      { provider: "fleet-rtx6000ada", model: "Qwen3.8-27B (RTX)" },
      { provider: "codex-cli", model: "subscription" },
    ]);
  });

  it("the find box finds across providers, and its Enter never sends the form", async () => {
    const box = await openForm();
    const find = within(box).getByTestId("harness-try-on-filter");
    fireEvent.change(find, { target: { value: "glm" } });
    expect(rows(box).map((r) => `${r.dataset.provider}::${r.dataset.model}`)).toEqual([
      "custom::glm-5.3-flash",
      "custom::~z-ai/glm-flash-latest",
    ]);
    expect(within(box).getAllByText("Your model server").length).toBeGreaterThan(0);
    // Enter in a box inside a form would send it: not this one.
    const enter = fireEvent.keyDown(find, { key: "Enter" });
    expect(enter).toBe(false); // the default (sending the form) was prevented
    fireEvent.change(find, { target: { value: "no such model" } });
    expect(within(box).getByText("No model matches.")).toBeTruthy();
    expect(api.calls.some(([m]) => m === "POST")).toBe(false);
  });

  it("Escape closes an open provider and gives it the focus; the form stays", async () => {
    const box = await openForm();
    fireEvent.click(group(box, "claude-cli"));
    const check = rows(box)[0];
    check.focus();
    fireEvent.keyDown(check, { key: "Escape" });
    expect(within(box).queryByTestId("harness-try-on-panel")).toBeNull();
    expect(document.activeElement).toBe(group(box, "claude-cli"));
    fireEvent.keyDown(group(box, "claude-cli"), { key: "Escape" });
    expect(screen.getByTestId("tonight-form")).toBeTruthy();
    expect(screen.getByTestId("tonight-try-on")).toBeTruthy();
  });

  it("nothing in the list sends the form: every button is type=button", async () => {
    const box = await openForm();
    tick(box, "anthropic", "claude-opus-4-8");
    fireEvent.click(group(box, "custom"));
    const buttons = within(box).getAllByRole("button");
    expect(buttons.length).toBeGreaterThan(4);
    for (const b of buttons) expect(b).toHaveAttribute("type", "button");
    fireEvent.click(group(box, "custom"));
    expect(api.calls.some(([m]) => m === "POST")).toBe(false);
  });

  it("editing a task keeps its saved picks in sight, the one the list no longer shows too, and removable", async () => {
    api.list = listOf([
      task("q1", {
        try_on: [
          { provider: "claude-cli", model: "claude-opus-5-5" },
          // Saved before v1.400.7 merged the gateway's two ways in: the list
          // shows the slot's pair now, not the fleet twin's.
          { provider: "fleet-custom", model: "glm-5.3-flash" },
        ],
      }),
    ]);
    render(<TonightList folders={["C:\\work\\shop"]} onOpenSession={() => {}} />);
    await screen.findByTestId("tonight-task-q1");
    fireEvent.click(screen.getByTestId("tonight-edit-q1"));
    const box = screen.getByTestId("tonight-try-on");
    const picked = within(box).getByTestId("harness-try-on-picked");
    const items = within(picked).getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(items[0]).toBe("Opus 5.5 · Claude Code");
    expect(items[1]).toMatch(/glm-5\.3-flash/);
    expect(within(box).getByTestId("harness-try-on-group-picked").textContent).toBe("1 picked");
    // The saved twin counts toward three, and goes with its own button.
    const twin = within(picked).getAllByRole("button")[1];
    fireEvent.click(twin);
    tick(box, "custom", "glm-5.3-flash");
    fireEvent.click(screen.getByTestId("tonight-submit"));
    await waitFor(() => expect(lastBody("PATCH")).toBeTruthy());
    expect(lastBody("PATCH")!.try_on).toEqual([
      { provider: "claude-cli", model: "claude-opus-5-5" },
      { provider: "custom", model: "glm-5.3-flash" },
    ]);
  });
});
