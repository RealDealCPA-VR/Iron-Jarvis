/**
 * v1.429.0 — Pi in Build › Code offers what Pi in a terminal offers: every
 * model Pi's own `/model` lists, grouped by provider; only the thinking levels
 * the chosen model takes; Pi's own `/` commands (and, typed, a one-line note
 * for the ones only Pi's terminal has); its extensions, templates and skills.
 *
 * THE CONTRACT: Pi's row is the one the REAL daemon lists after one background
 * read of a recorded pi 1.3.0 catalog (tests/test_build_app_pi_models_v1429.py,
 * IJ_REGEN_BUILD_APP_PI_MODELS=1). Only the network (lib/api), the stream reader
 * and the folder tree are stand-ins.
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  getCount: {} as Record<string, number>,
  posts: [] as { path: string; body: unknown }[],
  streams: [] as { path: string; body: unknown }[],
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: FakeApiError,
    get: (path: string) => {
      const key = path.replace(/\?limit=1$/, "");
      H.getCount[key] = (H.getCount[key] ?? 0) + 1;
      const r = H.gets[key];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(typeof r === "function" ? (r as () => unknown)() : r);
    },
    post: (path: string, body?: unknown) => {
      H.posts.push({ path, body });
      return Promise.resolve({});
    },
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* (path: string, body: unknown) {
    H.streams.push({ path, body });
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: ({ onSelect }: { onSelect: (p: string) => void }) => (
    <button type="button" data-testid="stub-pick-folder" onClick={() => onSelect("C:\\work\\shop")}>
      pick
    </button>
  ),
}));

import { BuildCodeView, TOOLS_REREAD_MS, TOOLS_REREAD_TRIES } from "@/components/buildCode/BuildCodeView";
import { LAST_TOOL_KEY } from "@/components/buildCode/StartComposer";
import {
  COMMAND_SOURCE_WORDS,
  decodeCodingTools,
  decodeCommands,
  effortsFor,
  modelGroups,
  slashMenuRows,
} from "@/lib/buildApp";

const REAL = join(process.cwd(), "__tests__", "fixtures", "build-app-pi-models-v1429.json");
const PI = (JSON.parse(readFileSync(REAL, "utf8")) as { pi: Record<string, unknown> }).pi;

const sess = (id: string) => ({
  id,
  harness: "pi",
  folder: "C:\\work\\shop",
  title: `Session ${id}`,
  account: "",
  model: "",
  effort: "",
  mode: "own",
  harness_session_id: "01a1b2c3-1111-7222-8333-444444444444",
  created_at: "2026-11-04T09:00:00Z",
  updated_at: "2026-11-04T09:00:00Z",
  archived_at: null,
  last_error: "",
});

function seed(pi: Record<string, unknown> = PI, sessions: Record<string, unknown>[] = []) {
  H.gets = {
    "/build/app/harnesses": { harnesses: [pi] },
    "/build/app/sessions": { sessions },
    "/editor/options": { editors: [], preferred: "" },
  };
}

beforeEach(() => {
  H.posts = [];
  H.streams = [];
  H.getCount = {};
  seed();
  window.localStorage.clear();
  window.localStorage.setItem(LAST_TOOL_KEY, "pi");
});

afterEach(() => cleanup());

/* ======================================================= the contract */

describe("Pi's row from the real daemon", () => {
  it("keeps every model with its provider, the default's name and each model's own levels", () => {
    const [pi] = decodeCodingTools({ harnesses: [PI] });
    expect(pi.defaultName).toBe("Think Big");
    expect(modelGroups(pi.models).map((g) => [g.group, g.models.map((m) => m.label)])).toEqual([
      ["standin-a", ["Think Big", "Plain Model"]],
      ["standin-b", ["Think Small"]],
    ]);
    expect(effortsFor(pi, "")).toEqual(["off", "minimal", "low", "medium", "high", "xhigh"]);
    expect(effortsFor(pi, "standin-b/think-small")).toEqual(["low", "medium", "high"]);
    expect(effortsFor(pi, "standin-a/plain")).toEqual([]);
  });

  it("names each command's kind, and keeps the note of one only Pi's terminal has", () => {
    const [pi] = decodeCodingTools({ harnesses: [PI] });
    const by = Object.fromEntries(pi.commands.map((c) => [c.name, c]));
    expect(by.compact.source).toBe("pi");
    expect(by.hello.source).toBe("extension");
    expect(by.review.source).toBe("prompt");
    expect(by["skill:hello-skill"].source).toBe("skill");
    expect(by.settings.note).toMatch(/Pi's terminal/);
    expect(decodeCommands(PI.commands)?.find((c) => c.name === "new")?.note).toBe("Start another session with New session.");
    expect(COMMAND_SOURCE_WORDS.prompt).toBe("template");
  });

  it("the menu shows a note row only once its name is typed", () => {
    const [pi] = decodeCodingTools({ harnesses: [PI] });
    expect(slashMenuRows(pi.commands, "", 99).some((c) => c.note)).toBe(false);
    expect(slashMenuRows(pi.commands, "", 99).map((c) => c.name)).toContain("skill:hello-skill");
    expect(slashMenuRows(pi.commands, "set", 99).map((c) => c.name)).toEqual(["settings"]);
    expect(slashMenuRows(pi.commands, "co", 99).map((c) => c.name)).toEqual(["compact", "copy"]);
  });
});

/* ======================================================= the start composer */

async function startStage() {
  render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
  await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Pi"));
  // v1.431.0 (the release gate): the chip is drawn in the same commit that
  // queues the composer's "new tool: reset its choices" step, which React
  // runs later; a pick made before it runs is wiped. Let React finish first.
  await act(async () => {});
}

describe("starting a Pi session", () => {
  it("the model menu groups Pi's models by provider and names Pi's default", async () => {
    await startStage();
    const pick = screen.getByTestId("code-start-model") as HTMLSelectElement;
    const groups = Array.from(pick.querySelectorAll("optgroup")).map((g) => [
      g.label,
      Array.from(g.querySelectorAll("option")).map((o) => o.textContent),
    ]);
    expect(groups).toEqual([
      ["standin-a", ["Think Big", "Plain Model"]],
      ["standin-b", ["Think Small"]],
    ]);
    expect(pick.options[0].value).toBe("");
    expect(pick.options[0].textContent).toBe("Pi's default (Think Big)");
  });

  it("the effort menu offers only what the chosen model takes, and hides for one that takes none", async () => {
    await startStage();
    const levels = () =>
      Array.from((screen.getByTestId("code-start-effort") as HTMLSelectElement).options).map((o) => o.value);
    expect(levels()).toEqual(["", "off", "minimal", "low", "medium", "high", "xhigh"]);
    fireEvent.change(screen.getByTestId("code-start-model"), { target: { value: "standin-b/think-small" } });
    await waitFor(() => expect(levels()).toEqual(["", "low", "medium", "high"]));
    fireEvent.change(screen.getByTestId("code-start-model"), { target: { value: "standin-a/plain" } });
    await waitFor(() => expect(screen.queryByTestId("code-start-effort")).toBeNull());
  });

  it("says when Pi has no model it can use yet", async () => {
    seed({ ...PI, models: [{ id: "", label: "Pi's default" }], models_note: "Pi has no model it can use yet." });
    await startStage();
    expect(screen.getByTestId("code-start-models-note").textContent).toBe("Pi has no model it can use yet.");
  });

  it(
    "while Pi's list is being read, the page looks again a few times — never more",
    async () => {
      let reads = 0;
      H.gets["/build/app/harnesses"] = () => {
        reads += 1;
        return reads === 1
          ? { harnesses: [{ ...PI, models: [{ id: "", label: "Pi's default" }], models_reading: true }] }
          : { harnesses: [PI] };
      };
      await startStage();
      expect(screen.getByTestId("code-start-models-note").textContent).toBe("Reading Pi's model list…");
      await waitFor(() => expect(screen.getByTestId("code-start-model").querySelectorAll("optgroup").length).toBe(2), {
        timeout: TOOLS_REREAD_MS + 3000,
      });
      expect(reads).toBe(2);
      expect(screen.queryByTestId("code-start-models-note")).toBeNull();
      // The list came: nothing is read again.
      await new Promise((r) => setTimeout(r, TOOLS_REREAD_MS + 500));
      expect(reads).toBe(2);
    },
    TOOLS_REREAD_MS + 8000,
  );
});

/* v1.450.0 (Wave D, item 40): the re-reads have an end. */
describe("a model list that never arrives", () => {
  it(
    "after the last re-read the box says it couldn't read the list, and Try again asks again",
    async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      try {
        let reads = 0;
        let ready = false;
        H.gets["/build/app/harnesses"] = () => {
          reads += 1;
          return ready
            ? { harnesses: [PI] }
            : { harnesses: [{ ...PI, models: [{ id: "", label: "Pi's default" }], models_reading: true }] };
        };
        await startStage();
        expect(screen.getByTestId("code-start-models-note").textContent).toBe("Reading Pi's model list…");
        for (let i = 0; i < TOOLS_REREAD_TRIES; i++) {
          await act(async () => {
            await vi.advanceTimersByTimeAsync(TOOLS_REREAD_MS + 10);
          });
        }
        await waitFor(() =>
          expect(screen.getByTestId("code-start-models-note").getAttribute("data-state")).toBe("gave-up"),
        );
        expect(reads).toBe(TOOLS_REREAD_TRIES + 1);
        expect(screen.getByTestId("code-start-models-note").textContent).toBe("Couldn't read Pi's model list. Try again");
        // Nothing more is read by itself.
        await act(async () => {
          await vi.advanceTimersByTimeAsync(TOOLS_REREAD_MS * 3);
        });
        expect(reads).toBe(TOOLS_REREAD_TRIES + 1);
        // Try again re-arms the re-reads; the list arrives.
        ready = true;
        fireEvent.click(screen.getByTestId("code-start-models-retry"));
        await waitFor(() => expect(screen.queryByTestId("code-start-models-note")).toBeNull());
        expect(reads).toBe(TOOLS_REREAD_TRIES + 2);
      } finally {
        vi.useRealTimers();
      }
    },
    20_000,
  );
});

/* ======================================================= in a session */

async function inSession() {
  seed(PI, [sess("p1")]);
  H.gets["/build/app/sessions/p1"] = {
    session: sess("p1"),
    events: [],
    running: null,
    pending: [],
    commands: PI.commands,
  };
  render(<BuildCodeView initialSession="p1" onOpenedTerminal={() => {}} />);
  return (await screen.findByTestId("code-box")) as HTMLTextAreaElement;
}

describe("Pi's `/` menu in a session", () => {
  it("lists Pi's own commands, then the user's, each with its kind", async () => {
    const box = await inSession();
    fireEvent.change(box, { target: { value: "/" } });
    const menu = await screen.findByTestId("code-slash-menu");
    const rows = within(menu).getAllByRole("option");
    expect(rows[0].textContent).toContain("/model");
    expect(within(rows[0]).getByTestId("code-command-source").textContent).toBe("Pi");
    const review = within(menu).getByTestId("code-command-review");
    expect(within(review).getByTestId("code-command-source").textContent).toBe("template");
    expect(within(menu).queryByTestId("code-command-settings")).toBeNull();
  });

  it("a command only Pi's terminal has says why when picked, and types nothing", async () => {
    const box = await inSession();
    fireEvent.change(box, { target: { value: "/sett" } });
    const row = await screen.findByTestId("code-command-settings");
    expect(row.textContent).toContain("Pi's terminal");
    fireEvent.click(row);
    expect(screen.getByTestId("code-note").textContent).toMatch(/^\/settings: That is one of Pi's own screens/);
    expect(box.value).toBe("/sett");
    expect(H.streams).toEqual([]);
  });

  it("the model chip in a session is grouped by provider too", async () => {
    await inSession();
    const chip = screen.getByTestId("code-model-chip") as HTMLSelectElement;
    expect(Array.from(chip.querySelectorAll("optgroup")).map((g) => g.label)).toEqual(["standin-a", "standin-b"]);
  });
});
