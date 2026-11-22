/**
 * Quick Ask (36-a, third review): A HAND-OVER GOES WITH ITS PAGE.
 *
 * A door that carries the user's request stages it in sessionStorage, which is
 * per window: staged in Quick Ask's small window, the main window's page read
 * nothing and opened empty. lib/quickAnswer's `toMainWindow({path})` now sends
 * what this window staged for that page with it (desktop/main.js holds it
 * once; the main window's preload puts it in that window's storage), and a
 * workflow draft's "Open in editor" goes through `doorToMainWindow` in the
 * small window. The page-level door presses and the file presses are driven on
 * the real chat page in leap-36a-compact-chat.test.tsx; main.js's half in
 * tests/test_leap36a_answer_window.py.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  API_BASE: "",
  ijToken: () => "",
  get: (path: string) => Promise.reject(new Error(`unmocked GET ${path}`)),
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

const routerPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
}));

import { WorkflowDraftCard } from "@/components/chat/WorkflowDraftCard";
import { CODING_START_KEY } from "@/lib/codingStart";
import {
  CompactChatContext,
  MAIN_WINDOW_FILE_KEY,
  PENDING_WORKFLOW_KEY,
  doorToMainWindow,
  takeMainWindowFile,
  toMainWindow,
} from "@/lib/quickAnswer";

type Sent = { path?: string; thread?: string; carry?: { key: string; value: string } };
const W = window as unknown as { ironjarvis?: { isDesktop: boolean; quickAsk?: Record<string, unknown> } };
let toMain: ReturnType<typeof vi.fn<(t: unknown) => Promise<unknown>>>;

beforeEach(() => {
  toMain = vi.fn(async () => ({ ok: true }));
  W.ironjarvis = { isDesktop: true, quickAsk: { isAnswerWindow: true, toMain } };
  window.sessionStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  delete W.ironjarvis;
});

const draft = {
  name: "intake",
  description: "Sort new client mail",
  steps: [{ name: "Read", agent: "builder", task: "read the inbox" }],
} as never;

describe("a workflow draft's Open in editor", () => {
  it("in Quick Ask's window: the draft goes with the page to the main window", async () => {
    render(
      <CompactChatContext.Provider value={true}>
        <WorkflowDraftCard draft={draft} events={[]} />
      </CompactChatContext.Provider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /open in editor/i }));
    await waitFor(() => expect(toMain).toHaveBeenCalledTimes(1));
    const sent = toMain.mock.calls[0][0] as Sent;
    expect(sent.path).toBe("/workflows");
    expect(sent.carry?.key).toBe(PENDING_WORKFLOW_KEY);
    expect(JSON.parse(sent.carry!.value)).toEqual({
      name: "intake",
      description: "Sort new client mail",
      steps: [{ name: "Read", agent: "builder", task: "read the inbox" }],
    });
    // Not opened in this small window, and not left in its storage.
    expect(routerPush).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem(PENDING_WORKFLOW_KEY)).toBeNull();
  });

  it("in the main window, as before: staged in this window, the editor opens here", () => {
    W.ironjarvis!.quickAsk!.isAnswerWindow = false;
    render(<WorkflowDraftCard draft={draft} events={[]} />);
    fireEvent.click(screen.getByRole("button", { name: /open in editor/i }));
    expect(routerPush).toHaveBeenCalledWith("/workflows");
    expect(JSON.parse(window.sessionStorage.getItem(PENDING_WORKFLOW_KEY) || "{}").name).toBe("intake");
    expect(toMain).not.toHaveBeenCalled();
  });
});

describe("toMainWindow carries what this window staged", () => {
  it("only the hand-over of THAT page, and none for a page that reads none", async () => {
    window.sessionStorage.setItem(CODING_START_KEY, '{"text":"fix the test","folder":null,"at":1}');
    expect(await toMainWindow({ path: "/schedules" })).toBe(true);
    expect(toMain).toHaveBeenLastCalledWith({ path: "/schedules" });
    // The coding request waits for its own page, untouched.
    expect(window.sessionStorage.getItem(CODING_START_KEY)).not.toBeNull();
    expect(await toMainWindow({ path: "/chat?view=code" })).toBe(true);
    expect(toMain).toHaveBeenLastCalledWith({
      path: "/chat?view=code",
      carry: { key: CODING_START_KEY, value: '{"text":"fix the test","folder":null,"at":1}' },
    });
    expect(window.sessionStorage.getItem(CODING_START_KEY)).toBeNull();
  });

  it("puts it back when the desktop did not take it", async () => {
    toMain.mockImplementation(async () => ({ ok: false }));
    window.sessionStorage.setItem(CODING_START_KEY, '{"text":"fix the test","folder":null,"at":1}');
    expect(await toMainWindow({ path: "/chat?view=code" })).toBe(false);
    expect(window.sessionStorage.getItem(CODING_START_KEY)).toBe('{"text":"fix the test","folder":null,"at":1}');
  });

  it("a chat with a file names the file; a chat alone names nothing else", async () => {
    await toMainWindow({ thread: "t1" });
    expect(toMain).toHaveBeenLastCalledWith({ thread: "t1" });
    await toMainWindow({ thread: "t1", file: "C:\\work\\out.md" });
    const sent = toMain.mock.calls[1][0] as Sent;
    expect(sent.thread).toBe("t1");
    expect(sent.carry?.key).toBe(MAIN_WINDOW_FILE_KEY);
    expect(JSON.parse(sent.carry!.value)).toMatchObject({ thread: "t1", path: "C:\\work\\out.md" });
  });

  it("doorToMainWindow: a chat link stays here, and outside the desktop app nothing is taken", () => {
    const stage = vi.fn();
    expect(doorToMainWindow("/chat?thread=t2", stage)).toBe(false);
    expect(stage).not.toHaveBeenCalled();
    delete W.ironjarvis;
    expect(doorToMainWindow("/workflows", stage)).toBe(false);
    expect(stage).not.toHaveBeenCalled();
  });
});

describe("takeMainWindowFile (the main window's chat)", () => {
  const put = (r: Record<string, unknown>) => window.sessionStorage.setItem(MAIN_WINDOW_FILE_KEY, JSON.stringify(r));

  it("the file that came with THIS chat, once", () => {
    put({ thread: "t1", path: "C:\\work\\out.md", at: 1_000 });
    expect(takeMainWindowFile("t1", 2_000)).toBe("C:\\work\\out.md");
    expect(takeMainWindowFile("t1", 2_000)).toBeNull();
  });

  it("never another chat's, never a stale one, never a malformed one", () => {
    put({ thread: "t1", path: "C:\\work\\out.md", at: 1_000 });
    expect(takeMainWindowFile("t2", 2_000)).toBeNull();
    put({ thread: "t1", path: "C:\\work\\out.md", at: 1_000 });
    expect(takeMainWindowFile("t1", 1_000 + 3 * 60_000)).toBeNull();
    put({ thread: "t1", path: 7, at: 1_000 });
    expect(takeMainWindowFile("t1", 2_000)).toBeNull();
    window.sessionStorage.setItem(MAIN_WINDOW_FILE_KEY, "{not json");
    expect(takeMainWindowFile("t1", 2_000)).toBeNull();
  });
});
