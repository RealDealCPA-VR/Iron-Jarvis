/**
 * Wave 3 (SPEED), v1.311.0 — review fix round: the cached paint is not a
 * licence for a late GET to overwrite what the user did on it.
 *
 * Plain words: reopening a conversation (or coming back to Chat) now paints
 * the copy this window last saw at once and asks the daemon behind it. The
 * pane looks loaded, so a user can send before that answer lands — and the
 * answer used to be laid over the screen anyway: the message just sent
 * vanished while its reply streamed, the stored setup was re-applied over the
 * live turn, and a copy ending on the user's own (durably saved) question
 * left "This didn't get a reply." + Retry under a turn that succeeded.
 *
 * Also here: the TTS feed rewinds on the stream's explicit `reset` signal
 * (W3-1), not on "the text got shorter" — a rewrite whose first token is
 * longer than the discarded text never looks shorter.
 *
 * Harness: the v1.285.0 chat-page mocks with `@/lib/apiCache` REAL (the
 * first mount's GET fills the cache the second mount paints from), every GET
 * recorded and holdable, and a `stream.run` the test drives by hand.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
  }
  type OnToken = (delta: string, full: string) => void;
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      gets: [] as string[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      responses: {} as Record<string, unknown>,
      holds: new Map<string, { promise: Promise<unknown>; release: () => void }>(),
    },
    run: {
      bodies: [] as unknown[],
      release: null as null | (() => void),
      /** When set, the turn is scripted (no hold): it gets the callbacks. */
      script: null as null | ((onToken?: OnToken, onReset?: () => void) => { reply: string }),
    },
    tts: {
      resetStream: vi.fn(),
      speakMore: vi.fn(),
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.api.gets.push(path);
    const held = H.api.holds.get(path);
    if (held) await held.promise;
    const r = H.api.responses[path];
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async () => ({}),
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));
vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async (
      body: unknown,
      onToken?: (delta: string, full: string) => void,
      onReset?: () => void,
    ) => {
      H.run.bodies.push(body);
      if (H.run.script) return { ...H.run.script(onToken, onReset), tools_used: [] };
      await new Promise<void>((r) => {
        H.run.release = r;
      });
      return { reply: "Noted.", tools_used: [] };
    },
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
}));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false, reason: null, engine: null, listening: false, processing: false,
    transcript: "", interim: "", error: null, start: () => {}, stop: () => {}, reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: true, enabled: true, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: H.tts.resetStream,
    speakMore: H.tts.speakMore, cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { __resetApiCache } from "@/lib/apiCache";

const STAMP = "2026-10-01T10:00:00Z";
const ASK = "probe question xyz";
const T9 = {
  id: "t9",
  title: "Ledger",
  updated_at: STAMP,
  messages: [
    { role: "user", content: "total the ledger please" },
    { role: "assistant", content: "The ledger totals 1.2M." },
  ],
};

function hold(path: string): () => void {
  let release!: () => void;
  const promise = new Promise<void>((r) => {
    release = r;
  });
  H.api.holds.set(path, { promise, release });
  return () => {
    H.api.holds.delete(path);
    release();
  };
}

async function settle(): Promise<void> {
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

/** Open Ledger once (fills this window's cache + remembered thread), leave
 *  Chat, and come back with the thread's GET HELD: the pane is the paint. */
async function returnWithGetHeld(): Promise<() => void> {
  const first = render(<ChatPage />);
  fireEvent.click(await screen.findByTitle("Ledger"));
  await screen.findByText("The ledger totals 1.2M.");
  first.unmount();
  const release = hold("/chat/threads/t9");
  render(<ChatPage />);
  await screen.findByText("The ledger totals 1.2M.");
  await waitFor(() => expect(H.api.gets.filter((p) => p === "/chat/threads/t9").length).toBe(2));
  return release;
}

async function sendOnThePaint(): Promise<void> {
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: ASK } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(H.run.bodies.length).toBe(1));
  expect(screen.queryByText(ASK)).not.toBeNull();
}

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.puts.length = 0;
  H.api.holds.clear();
  H.run.bodies.length = 0;
  H.run.release = null;
  H.run.script = null;
  H.tts.resetStream.mockClear();
  H.tts.speakMore.mockClear();
  H.api.responses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": {
      threads: [{ id: "t9", title: "Ledger", updated_at: STAMP, messages: 2 }],
    },
    "/chat/threads/t9": T9,
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  __resetApiCache();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("a send on the cached paint survives the late thread GET", () => {
  it("the bubble stays mid-turn, the reply lands, and no Retry is left under a turn that succeeded", async () => {
    const releaseGet = await returnWithGetHeld();
    await sendOnThePaint();
    // The durable-at-send PUT landed first: the disk copy ends on the user's
    // own question — the shape the CL5 open path reads as "never answered".
    H.api.responses["/chat/threads/t9"] = {
      ...T9,
      updated_at: "2026-10-01T10:05:00Z",
      messages: [...T9.messages, { role: "user", content: ASK }],
    };
    await act(async () => {
      releaseGet();
    });
    await settle();
    // Mid-turn: the question is still on screen (today: wiped by the copy).
    expect(screen.queryByText(ASK)).not.toBeNull();

    await act(async () => {
      H.run.release?.();
    });
    await waitFor(() => expect(screen.queryByText("Noted.")).not.toBeNull());
    await settle();
    expect(screen.queryByText(ASK)).not.toBeNull();
    // The successful turn is not re-labelled as one that got no reply.
    expect(screen.queryByText(/didn.t get a reply/)).toBeNull();
    expect(screen.queryByTitle("Re-send the last message")).toBeNull();
    // Every save of this turn went into the painted thread, never a new one.
    const saves = H.api.puts.map((p) => p.path);
    expect(saves.length).toBeGreaterThan(0);
    expect(saves.every((p) => p === "/chat/threads/t9")).toBe(true);
    const last = H.api.puts[H.api.puts.length - 1].body.messages as { content: string }[];
    expect(last.map((m) => m.content)).toEqual([...T9.messages.map((m) => m.content), ASK, "Noted."]);
  });

  it("a NEWER disk copy keeps the paint's stamp, so the save re-bases onto it instead of clobbering it", async () => {
    const releaseGet = await returnWithGetHeld();
    await sendOnThePaint();
    // Another window appended a turn while this one was away.
    H.api.responses["/chat/threads/t9"] = {
      ...T9,
      updated_at: "2026-10-01T11:00:00Z",
      messages: [...T9.messages, { role: "user", content: "from the other window" }],
    };
    await act(async () => {
      releaseGet();
    });
    await settle();
    await act(async () => {
      H.run.release?.();
    });
    await waitFor(() => expect(screen.queryByText("Noted.")).not.toBeNull());
    await settle();
    // Folding the newer stamp into the box would make the end save
    // unconditional against content this window never saw (CL3 lost).
    const stamps = H.api.puts.map((p) => p.body.if_updated_at);
    expect(stamps.length).toBeGreaterThan(0);
    expect(stamps.every((s) => s === STAMP)).toBe(true);
  });

  it("a turn that FINISHED before the GET answers keeps its bubble and its reply (no busy flag left to lean on)", async () => {
    const releaseGet = await returnWithGetHeld();
    await sendOnThePaint();
    await act(async () => {
      H.run.release?.();
    });
    await waitFor(() => expect(screen.queryByText("Noted.")).not.toBeNull());
    // Only the durable-at-send save had reached the disk when the GET was read.
    H.api.responses["/chat/threads/t9"] = {
      ...T9,
      updated_at: "2026-10-01T10:05:00Z",
      messages: [...T9.messages, { role: "user", content: ASK }],
    };
    await act(async () => {
      releaseGet();
    });
    await settle();
    expect(screen.queryByText("Noted.")).not.toBeNull();
    expect(screen.queryByText(ASK)).not.toBeNull();
    expect(screen.queryByText(/didn.t get a reply/)).toBeNull();
  });

  it("a GET that FAILS after a send on the paint does not wipe the turn (its own save re-creates a gone thread)", async () => {
    const releaseGet = await returnWithGetHeld();
    await sendOnThePaint();
    H.api.responses["/chat/threads/t9"] = new H.FakeApiError("Thread not found", 404);
    await act(async () => {
      releaseGet();
    });
    await settle();
    expect(screen.queryByText(ASK)).not.toBeNull();
    await act(async () => {
      H.run.release?.();
    });
    await waitFor(() => expect(screen.queryByText("Noted.")).not.toBeNull());
    expect(screen.queryByText(ASK)).not.toBeNull();
    expect(screen.queryByText("Thread not found")).toBeNull();
  });

  it("CONTROL: a GET that fails with nobody acting takes the dead paint back to a fresh conversation", async () => {
    const releaseGet = await returnWithGetHeld();
    H.api.responses["/chat/threads/t9"] = new H.FakeApiError("Thread not found", 404);
    await act(async () => {
      releaseGet();
    });
    await waitFor(() => expect(screen.queryByText("The ledger totals 1.2M.")).toBeNull());
  });

  it("a setup change made on the paint is not undone by the stored setup the GET brings back", async () => {
    const releaseGet = await returnWithGetHeld();
    H.api.responses["/chat/threads/t9"] = { ...T9, setup: { approval_mode: "always_ask" } };
    const posture = screen.getByLabelText("Approval mode") as HTMLSelectElement;
    fireEvent.change(posture, { target: { value: "yolo" } });
    expect(posture.value).toBe("yolo");
    await act(async () => {
      releaseGet();
    });
    await settle();
    expect((screen.getByLabelText("Approval mode") as HTMLSelectElement).value).toBe("yolo");
    // The edit was saved into the painted thread, not a new one.
    expect(H.api.puts.map((p) => p.path)).toEqual(["/chat/threads/t9"]);
  });

  it("CONTROL: a GET that answers at once still lays a newer copy over the paint", async () => {
    // Anti-vacuity for the guard: with nobody acting, an unheld answer is
    // still applied over the paint (a guard that always held would keep the
    // stale copy here).
    const first = render(<ChatPage />);
    fireEvent.click(await screen.findByTitle("Ledger"));
    await screen.findByText("The ledger totals 1.2M.");
    first.unmount();
    H.api.responses["/chat/threads/t9"] = {
      ...T9,
      updated_at: "2026-10-01T11:00:00Z",
      messages: [...T9.messages, { role: "assistant", content: "added in the other window" }],
    };
    render(<ChatPage />);
    await screen.findByText("added in the other window");
  });

  it("CONTROL: with no action on the paint, the GET's newer copy still replaces it (and CL5's Retry still shows)", async () => {
    const releaseGet = await returnWithGetHeld();
    H.api.responses["/chat/threads/t9"] = {
      ...T9,
      updated_at: "2026-10-01T11:00:00Z",
      messages: [...T9.messages, { role: "user", content: "asked in the other window" }],
      setup: { approval_mode: "always_ask" },
    };
    await act(async () => {
      releaseGet();
    });
    await screen.findByText("asked in the other window");
    await screen.findByText(/didn.t get a reply/);
    // ...and the thread's stored setup is restored as on any open.
    expect((screen.getByLabelText("Approval mode") as HTMLSelectElement).value).toBe("always_ask");
    expect(H.api.puts.length).toBe(0);
  });
});

describe("the paint carries the conversation's SETUP, not the reset defaults", () => {
  // Review fix round (BLOCKING): the paint moves the save box onto the real
  // thread before its GET answers, and the daemon REPLACES a stored setup
  // wholesale on PUT. A paint that left the setup on the open's reset
  // defaults turned one posture change into a PUT of empties that wiped the
  // conversation's tools, folder, documents and posture.
  const STORED = {
    tools: ["read_file"],
    workspace_dir: "C:/work/ledger",
    documents: ["C:/work/ledger/a.pdf"],
    approval_mode: "always_ask",
  };
  beforeEach(() => {
    H.api.responses["/chat/threads/t9"] = { ...T9, setup: STORED };
  });

  it("the posture painted from the cache is the stored one, before the GET answers", async () => {
    const releaseGet = await returnWithGetHeld();
    expect((screen.getByLabelText("Approval mode") as HTMLSelectElement).value).toBe("always_ask");
    await act(async () => {
      releaseGet();
    });
  });

  it("an edit on the paint PUTs a setup that still carries the tools, the folder and the documents", async () => {
    const releaseGet = await returnWithGetHeld();
    fireEvent.change(screen.getByLabelText("Approval mode"), { target: { value: "yolo" } });
    await waitFor(() => expect(H.api.puts.length).toBe(1));
    const put = H.api.puts[0];
    expect(put.path).toBe("/chat/threads/t9");
    const setup = put.body.setup as Record<string, unknown>;
    expect(setup).toBeTruthy();
    expect(setup.approval_mode).toBe("yolo");
    expect(setup.tools).toEqual(["read_file"]);
    expect(setup.workspace_dir).toBe("C:/work/ledger");
    expect(setup.documents).toEqual(["C:/work/ledger/a.pdf"]);
    await act(async () => {
      releaseGet();
    });
    await settle();
    // The acted branch keeps the user's screen: still yolo, still armed.
    expect((screen.getByLabelText("Approval mode") as HTMLSelectElement).value).toBe("yolo");
  });

  it("a save's setup rides this window's cached copy, so the next paint is not older than the disk", async () => {
    const releaseGet = await returnWithGetHeld();
    fireEvent.change(screen.getByLabelText("Approval mode"), { target: { value: "yolo" } });
    await waitFor(() => expect(H.api.puts.length).toBe(1));
    await act(async () => {
      releaseGet();
    });
    await settle();
    cleanup();
    // Third visit, GET held again: the paint must show the SAVED posture.
    hold("/chat/threads/t9");
    render(<ChatPage />);
    await screen.findByText("The ledger totals 1.2M.");
    expect((screen.getByLabelText("Approval mode") as HTMLSelectElement).value).toBe("yolo");
  });
});

describe("the TTS feed rewinds on the stream's reset signal (W3-1)", () => {
  it("a rewrite LONGER than the discarded text still restarts the voice counter", async () => {
    H.run.script = (onToken, onReset) => {
      onToken?.("Ho", "Ho");
      onReset?.();
      onToken?.("Hello friend", "Hello friend");
      return { reply: "Hello friend" };
    };
    render(<ChatPage />);
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "hola" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await screen.findByText("Hello friend");
    // Once at the turn's first token, once at the reset. An inference from
    // "the text got shorter" counted 1 here: "Hello friend" outgrows "Ho".
    expect(H.tts.resetStream).toHaveBeenCalledTimes(2);
    const order = [...H.tts.resetStream.mock.invocationCallOrder, ...H.tts.speakMore.mock.invocationCallOrder].sort(
      (a, b) => a - b,
    );
    const kinds = order.map((n) => (H.tts.resetStream.mock.invocationCallOrder.includes(n) ? "reset" : "more"));
    // reset -> "Ho" -> reset -> the rewrite (then the final flush).
    expect(kinds.slice(0, 4)).toEqual(["reset", "more", "reset", "more"]);
    expect(H.tts.speakMore.mock.calls[1][0]).toBe("Hello friend");
  });

  it("CONTROL: a turn with no reset rewinds once, at its first token", async () => {
    H.run.script = (onToken) => {
      onToken?.("Hello ", "Hello ");
      onToken?.("friend", "Hello friend");
      return { reply: "Hello friend" };
    };
    render(<ChatPage />);
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "hola" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await screen.findByText("Hello friend");
    expect(H.tts.resetStream).toHaveBeenCalledTimes(1);
  });
});

describe("the paint carries the conversation's PROJECT (review v1.311.0)", () => {
  // The paint moved the save box onto the real thread but left projectIdRef
  // null until /projects or the thread GET answered: an edit on the paint
  // PUT `project_id: null` (the daemon reads that as "untag"), and a send on
  // the paint went out without the project (no grounding).
  beforeEach(() => {
    H.api.responses["/projects"] = { projects: [{ id: "p2", name: "Books", status: "active" }] };
    H.api.responses["/chat/threads"] = {
      threads: [{ id: "t9", title: "Ledger", updated_at: STAMP, messages: 2, project_id: "p2" }],
    };
    H.api.responses["/chat/threads/t9"] = { ...T9, project_id: "p2" };
  });

  async function returnWithProjectsAndGetHeld(): Promise<() => void> {
    const first = render(<ChatPage />);
    fireEvent.click(await screen.findByTitle("Ledger"));
    await screen.findByText("The ledger totals 1.2M.");
    first.unmount();
    const releaseProjects = hold("/projects");
    const releaseGet = hold("/chat/threads/t9");
    render(<ChatPage />);
    await screen.findByText("The ledger totals 1.2M.");
    return () => {
      releaseGet();
      releaseProjects();
    };
  }

  it("an edit on the paint keeps the thread's project on the PUT", async () => {
    const release = await returnWithProjectsAndGetHeld();
    fireEvent.change(screen.getByLabelText("Approval mode"), { target: { value: "yolo" } });
    await waitFor(() => expect(H.api.puts.length).toBe(1));
    expect(H.api.puts[0].path).toBe("/chat/threads/t9");
    expect(H.api.puts[0].body.project_id).toBe("p2");
    await act(async () => {
      release();
    });
  });

  it("a send on the paint carries the project, so the turn is grounded in it", async () => {
    const release = await returnWithProjectsAndGetHeld();
    await sendOnThePaint();
    expect((H.run.bodies[0] as Record<string, unknown>).project_id).toBe("p2");
    await act(async () => {
      H.run.release?.();
      release();
    });
  });
});
