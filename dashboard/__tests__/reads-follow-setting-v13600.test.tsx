/**
 * v1.360.0 lane 10C (1): the v1.356.0 G81 REGRESSION closed. After the user
 * limits reading to chosen folders ("Folders Jarvis may read"), the chat's
 * approval chip and the Access summary's Approval level row still said "Reads
 * files anywhere on this PC without asking", right under a Reading files row
 * that named the chosen folders.
 *
 * Pinned here, through the REAL components (only the network is a double):
 *  - lib/permissionLevels: ONE decision (`readScope`) and the level's reading
 *    sentence (`readsLevelLine`) for every state, in both directions: a limit
 *    never reads "anywhere", no limit still does, and the old descriptions
 *    are byte for byte what they were for a caller that knows nothing;
 *  - the chip (the chat page's `#chat-approval-mode`): nothing is read on
 *    mount; reaching for it (pointer, focus, open) reads GET
 *    /settings/access once, and then its title and EVERY menu row carry the
 *    setting's sentence;
 *  - the Access summary: the Approval level row reads the same facts as its
 *    Reading files row and hands them to the chip (a chip drawn afterwards
 *    says the right thing before any read of its own).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  }
  return { FakeApiError, gets: {} as Record<string, unknown>, calls: [] as string[] };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  get: async (path: string) => {
    H.calls.push(path);
    const r = H.gets[path.split("?")[0]];
    if (r instanceof Error) throw r;
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return JSON.parse(JSON.stringify(r));
  },
  put: async () => ({}),
  post: async () => ({}),
}));

import { PermissionChip } from "@/components/chat/PermissionChip";
import { AccessSummary } from "@/components/settings/AccessSummary";
import {
  CHAT_LEVEL_KEY,
  PERMISSION_LEVELS,
  PERMISSION_MODES,
  READS_LINE,
  permissionLevel,
  readScope,
  readsLevelLine,
} from "@/lib/permissionLevels";
import { __resetReadFacts, READ_FACTS_PATH, READ_FACTS_TTL_MS, refreshReadFacts } from "@/lib/readFacts";
import { __resetApiCache } from "@/lib/apiCache";

const W = { timeout: 4000 };
const CHOSEN = { limited: true, folders: ["D:\\Clients"], chosen: ["D:\\Clients"], install: false };
const OPEN = { limited: false, folders: [], chosen: [], install: false };
const CHOSEN_LINE = "Reads files only in the folders you chose, without asking";

beforeEach(() => {
  H.calls = [];
  H.gets = {};
  __resetReadFacts();
  __resetApiCache();
  window.localStorage.clear();
});
afterEach(() => cleanup());

/* ------------------------------------------------------------------ words */

describe("one reading decision, one sentence per state", () => {
  it("the old descriptions are byte for byte what they were (a caller that knows nothing)", () => {
    expect(PERMISSION_LEVELS.always_ask.description).toBe(
      "Asks before it edits files, uses the internet or runs commands. Reads files anywhere on this PC without asking",
    );
    expect(PERMISSION_LEVELS.approve_for_me.description).toBe(
      "Edits files and searches the web on its own, asks before running commands. Reads files anywhere on this PC without asking",
    );
    expect(PERMISSION_LEVELS.yolo.description).toBe(
      "Runs commands and edits files without asking you first. Reads files anywhere on this PC without asking. Use with care",
    );
    for (const m of PERMISSION_MODES) expect(permissionLevel(m)).toEqual(PERMISSION_LEVELS[m]);
  });

  it("a limit the user chose is said in every level, and 'anywhere' never is", () => {
    const line = readsLevelLine(CHOSEN);
    expect(line).toBe(CHOSEN_LINE);
    for (const m of PERMISSION_MODES) {
      const d = permissionLevel(m, line).description;
      expect(d, m).toContain(CHOSEN_LINE);
      expect(d, m).not.toMatch(/anywhere/);
      expect(d, m).not.toMatch(/\n/);
    }
    expect(permissionLevel("yolo", line).description).toMatch(/Use with care$/);
    expect(permissionLevel("yolo", line).tone).toBe("warn");
    expect(readsLevelLine({ ...CHOSEN, install: true })).toBe(
      "Reads files only in the folders you chose (where this install allows), without asking",
    );
  });

  it("the other states: the install's list, nothing found, no limit, unknown", () => {
    expect(readsLevelLine({ limited: true, folders: ["E:\\Shared"] })).toBe(
      "Reads files only in the folders this install allows, without asking",
    );
    expect(readsLevelLine({ limited: true, folders: [] })).toMatch(/^Reads no files/);
    // CONTROL: no limit, and nothing known, keep the desktop default.
    expect(readsLevelLine(OPEN)).toBe(READS_LINE);
    expect(readsLevelLine(undefined)).toBe(READS_LINE);
    expect(readsLevelLine(null)).toBe(READS_LINE);
    expect(readScope({ limited: true, chosen: ["  "], folders: ["E:\\x"] }).kind).toBe("install");
    expect(readScope({ limited: true, chosen: ["D:\\a"], folders: [] }).kind).toBe("chosen");
  });
});

/* ------------------------------------------------------------------- chip */

function chip(value = "approve_for_me") {
  render(<PermissionChip value={value} onChange={vi.fn()} />);
  return screen.getByTestId("permission-chip");
}

describe("the chat's approval chip follows the setting", () => {
  it("reads nothing on mount; reaching for it reads the facts once, then the title says the limit", async () => {
    H.gets[READ_FACTS_PATH] = { reads: CHOSEN, writes: { chat_files_dir: "" } };
    const el = chip("always_ask");
    // Mounting reads nothing (a page that never touches the chip pays nothing).
    expect(H.calls).toEqual([]);
    expect(el.getAttribute("title")).toBe(PERMISSION_LEVELS.always_ask.description);
    fireEvent.pointerEnter(el);
    await waitFor(() => expect(el.getAttribute("title")).toContain(CHOSEN_LINE), W);
    expect(el.getAttribute("title")).not.toMatch(/anywhere/);
    expect(H.calls).toEqual([READ_FACTS_PATH]);
    // A second reach inside the freshness window asks nothing more. The read
    // starts a microtask after the reach (lib/readFacts calls `get` inside a
    // promise), so let it run before counting (review: the bare count passed
    // with the freshness check deleted).
    fireEvent.focus(el);
    fireEvent.pointerEnter(el);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(H.calls).toEqual([READ_FACTS_PATH]);
    // CONTROL: once the answer is older than the window, a reach asks again.
    refreshReadFacts(Date.now() + READ_FACTS_TTL_MS + 1);
    await waitFor(() => expect(H.calls).toEqual([READ_FACTS_PATH, READ_FACTS_PATH]), W);
  });

  it("opening the menu reads them too, and EVERY row then carries the setting's sentence", async () => {
    H.gets[READ_FACTS_PATH] = { reads: CHOSEN, writes: { chat_files_dir: "" } };
    const el = chip();
    fireEvent.click(el);
    const menu = screen.getByTestId("permission-menu");
    await waitFor(() => {
      const rows = within(menu).getAllByTestId("permission-item");
      expect(rows).toHaveLength(3);
      for (const r of rows) expect(r.textContent).toContain(CHOSEN_LINE);
    }, W);
    for (const r of within(menu).getAllByTestId("permission-item")) {
      expect(r.textContent).not.toMatch(/anywhere/);
    }
  });

  it("CONTROL: with no limit the chip still says anywhere after reading", async () => {
    H.gets[READ_FACTS_PATH] = { reads: OPEN, writes: { chat_files_dir: "" } };
    const el = chip();
    fireEvent.pointerEnter(el);
    await waitFor(() => expect(H.calls).toEqual([READ_FACTS_PATH]), W);
    await act(async () => {
      await Promise.resolve();
    });
    expect(el.getAttribute("title")).toBe(PERMISSION_LEVELS.approve_for_me.description);
  });

  it("an older Iron Jarvis (no route) leaves the words as they were and nothing breaks", async () => {
    H.gets[READ_FACTS_PATH] = new H.FakeApiError("Not Found", 404);
    const el = chip("yolo");
    fireEvent.click(el);
    await waitFor(() => expect(H.calls).toEqual([READ_FACTS_PATH]), W);
    const rows = within(screen.getByTestId("permission-menu")).getAllByTestId("permission-item");
    expect(rows[2].textContent).toContain(PERMISSION_LEVELS.yolo.description);
  });
});

/* --------------------------------------------------------- access summary */

function summary() {
  render(<AccessSummary values={{ readable_folders: "D:\\Clients" }} defs={[]} />);
  return screen.getByTestId("access-summary");
}

describe("the Access summary's Approval level row reads the Reading files row's facts", () => {
  beforeEach(() => {
    H.gets["/connectors"] = { connectors: [] };
    H.gets["/grants"] = { grants: [] };
  });

  it("a chosen limit: both rows name it, the level row never says anywhere", async () => {
    window.localStorage.setItem(CHAT_LEVEL_KEY, "always_ask");
    H.gets[READ_FACTS_PATH] = { reads: CHOSEN, writes: { chat_files_dir: "" } };
    const block = summary();
    const reads = within(block).getByTestId("access-reads");
    await waitFor(() => expect(reads.textContent).toContain("only in the folders you chose, without asking: D:\\Clients"), W);
    const level = within(block).getByTestId("access-level");
    expect(level.textContent).toContain(`${permissionLevel("always_ask", CHOSEN_LINE).description}.`);
    expect(level.textContent).not.toMatch(/anywhere/);
  });

  it("CONTROL: no limit, the level row keeps the anywhere sentence", async () => {
    H.gets[READ_FACTS_PATH] = { reads: OPEN, writes: { chat_files_dir: "" } };
    const block = summary();
    await waitFor(() => expect(within(block).getByTestId("access-reads").textContent).toContain(READS_LINE), W);
    expect(within(block).getByTestId("access-level").textContent).toContain(PERMISSION_LEVELS.approve_for_me.description);
  });

  it("hands what it read to the chip: a chip drawn afterwards already says the limit", async () => {
    H.gets[READ_FACTS_PATH] = { reads: CHOSEN, writes: { chat_files_dir: "" } };
    const block = summary();
    await waitFor(() => expect(within(block).getByTestId("access-reads").textContent).toContain("you chose"), W);
    cleanup();
    H.calls = [];
    const el = chip();
    expect(el.getAttribute("title")).toContain(CHOSEN_LINE);
    expect(H.calls).toEqual([]);
  });
});
