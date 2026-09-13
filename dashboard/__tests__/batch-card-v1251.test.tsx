/**
 * v1.251.0 (C-04) — a folder of documents becomes ONE summary sheet, offered.
 *
 * `batch_documents` has existed since v1.133.0 and almost nobody has run it.
 * Not because it is broken: `tools/autoselect` deliberately keeps it OUT of
 * auto-arming on cost grounds (about one model call per document) and leaves it
 * "one click away in the '+' menu". A click nobody knows about never happens.
 *
 * So the app now OFFERS it, on the real page, for the folder this conversation
 * is already pointed at. Pinned here with only the transport mocked:
 *  - a folder with more than a handful of documents gets a card stating the
 *    COUNT and the SPEND before anything is pressed;
 *  - a small folder gets no card at all (one or two files are quicker to
 *    attach and ask about — an offer there is noise);
 *  - pressing it runs the batch against THAT folder, with the conversation's
 *    own folder as the output location;
 *  - the files tick by as they are read (`batch.file_done`), because a silent
 *    screen for several minutes is indistinguishable from a hang;
 *  - the sheet it makes lands on the Files rail, which is how a made file is
 *    ever found again;
 *  - a folder the daemon refuses gets no card, silently.
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
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      posts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: { bodies: [] as Record<string, unknown>[] },
    /** The live event window the page hands to its cards. */
    events: [] as { id: string; type: string; session_id: string | null; ts: string; payload: Record<string, unknown> }[],
    /** Consumers of the mocked hook, so a new window can re-render them the
     *  way the real one does (v1.255.0 — see the mock below). */
    listeners: new Set<() => void>(),
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    if (typeof r === "function") return (r as (b: Record<string, unknown>) => unknown)(body);
    return r ?? {};
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

// The page reads its event window through this hook; the test drives it.
//
// A FRESH ARRAY EVERY CALL, deliberately: the real EventsHub hands each render
// a new window, and the card derives its progress with a `useMemo` keyed on
// `events`. Returning the same array identity would memoize the first (empty)
// window forever, so pushing an event and re-rendering showed nothing — which
// is exactly what the first cut of this file measured.
// A NEW WINDOW RE-RENDERS ITS CONSUMER, because the real hook does (v1.255.0).
// `lib/useEvents` holds `useState` and calls `setEvents` from the hub's `frame`
// callback, so an arriving `batch.file_done` re-renders the page by itself. This
// mock used to be a pure function with no state, which cannot do that — so the
// test forced a re-render by TYPING, and that stopped working the moment
// v1.250.0 (S-05) moved the composer into its own store precisely so typing
// does NOT re-render the page. The card then kept its first, empty window and
// rendered "Starting on 7 documents…" forever. Subscribing here is both closer
// to the real hook and independent of whatever else happens to re-render.
vi.mock("@/lib/useEvents", async () => {
  const { useEffect, useReducer } = await import("react");
  return {
    useEvents: () => {
      const [, bump] = useReducer((n: number) => n + 1, 0);
      useEffect(() => {
        H.listeners.add(bump);
        return () => {
          H.listeners.delete(bump);
        };
      }, []);
      return { events: [...H.events], connected: true };
    },
  };
});
vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async (body: Record<string, unknown>) => {
      H.stream.bodies.push(body);
      return { reply: "done" };
    },
    abort: () => {},
  }),
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
    supported: false, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";

const FOLDER = "C:\\Users\\me\\Documents\\Northwind client docs";
const SHEET = `${FOLDER}\\batch\\northwind\\Batch Summary.xlsx`;

/** A preview answer shaped like the daemon's. */
function previewOf(count: number, extra: Record<string, unknown> = {}) {
  return {
    folder: FOLDER,
    count,
    estimate_calls: count + 1,
    cap: 25,
    truncated: false,
    files: Array.from({ length: count }, (_, i) => ({
      name: `doc${i + 1}.pdf`,
      path: `${FOLDER}\\doc${i + 1}.pdf`,
    })),
    skipped: [],
    ...extra,
  };
}

function fileDone(index: number, total: number, name: string, status = "extracted") {
  H.events.unshift({
    id: `e${index}-${status}`,
    type: "batch.file_done",
    session_id: "chat",
    ts: new Date().toISOString(),
    payload: { folder: FOLDER, index, total, name, status },
  });
  // Deliver it, the way the hub does — inside act, since this updates state.
  act(() => {
    for (const listener of H.listeners) listener();
  });
}

beforeEach(() => {
  H.api.posts.length = 0;
  H.stream.bodies.length = 0;
  H.events.length = 0;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  H.api.postResponses["/documents/batch/preview"] = previewOf(7);
  H.api.postResponses["/documents/batch"] = {
    output: "batch over the folder: 7 extracted",
    report: { processed: 7, cached: 0, failed: [], skipped: [] },
    created_paths: [SHEET],
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  // The folder this conversation is pointed at — the page restores it from here.
  window.localStorage.setItem("ij_chat_workspace", FOLDER);
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const card = () => screen.queryByTestId("batch-suggest-card");
const previewCalls = () => H.api.posts.filter((p) => p.path === "/documents/batch/preview");
const runCalls = () => H.api.posts.filter((p) => p.path === "/documents/batch");

describe("a folder of documents is offered as one summary sheet", () => {
  it("states the count and the spend before anything is pressed", async () => {
    render(<ChatPage />);

    const el = await screen.findByTestId("batch-suggest-card");
    // The number the batch will really process, and the spend as a shape.
    expect(el).toHaveTextContent("7 documents");
    expect(el).toHaveTextContent("about 8 model calls");
    // It asked about THIS folder.
    await waitFor(() => expect(previewCalls()).toHaveLength(1));
    expect(previewCalls()[0].body).toMatchObject({ folder: FOLDER });
    // Nothing has run yet: the click is the consent.
    expect(runCalls()).toHaveLength(0);
  });

  it("says nothing about a folder with only a couple of documents", async () => {
    H.api.postResponses["/documents/batch/preview"] = previewOf(2);
    render(<ChatPage />);

    await waitFor(() => expect(previewCalls()).toHaveLength(1));
    await Promise.resolve();
    expect(card()).not.toBeInTheDocument();
  });

  it("says nothing when the daemon refuses the folder", async () => {
    H.api.postResponses["/documents/batch/preview"] = new H.FakeApiError("not a folder", 404);
    render(<ChatPage />);

    await waitFor(() => expect(previewCalls()).toHaveLength(1));
    expect(card()).not.toBeInTheDocument();
  });

  it("runs the batch against that folder when pressed, and shows each file as it is read", async () => {
    render(<ChatPage />);
    await screen.findByTestId("batch-suggest-card");

    fireEvent.click(screen.getByRole("button", { name: /Make the summary sheet/i }));

    await waitFor(() => expect(runCalls()).toHaveLength(1));
    expect(runCalls()[0].body).toMatchObject({
      folder: FOLDER,
      // The sheet lands where this conversation keeps its files.
      workspace_dir: FOLDER,
      session_id: "chat",
      max_files: 25,
    });
  });

  it("ticks the files off as the daemon reports them", async () => {
    // A run that is still going: the page renders progress from the events.
    let resolveRun: (v: unknown) => void = () => {};
    H.api.postResponses["/documents/batch"] = () =>
      new Promise((res) => {
        resolveRun = res;
      });

    render(<ChatPage />);
    await screen.findByTestId("batch-suggest-card");
    fireEvent.click(screen.getByRole("button", { name: /Make the summary sheet/i }));
    await waitFor(() => expect(runCalls()).toHaveLength(1));

    fileDone(1, 7, "doc1.pdf");
    fileDone(2, 7, "doc2.pdf");
    // `fileDone` already delivered those two through the mocked hub, which
    // re-renders its consumer the way the real `useEvents` does — the typing
    // below is NOT what carries the window in (it was, until v1.250.0 moved the
    // composer into its own store so that keystrokes stop re-rendering the
    // page). It stays because it is worth pinning that typing while a run is in
    // flight changes nothing about the progress, and because those words are
    // what the card reads AT THE PRESS for the sheet's instructions.
    fireEvent.change(await screen.findByPlaceholderText(/Message Iron Jarvis/), {
      target: { value: "per client please" },
    });

    // v1.258.2: BatchSuggestCard is loaded on demand since v1.258.0 (S-03), so the
    // card arrives one import-resolution after its gate opens; findBy's default
    // 1 s ceiling missed it on a loaded machine. 8 s is under the 15 s per-test
    // budget, so a missing card still reports THIS message rather than a timeout.
    const progress = await screen.findByTestId("batch-progress", {}, { timeout: 8000 });
    expect(progress).toHaveTextContent("Reading 2 of 7: doc2.pdf");

    resolveRun({ output: "done", report: { processed: 7 }, created_paths: [SHEET] });
  });

  it("puts the sheet it made on the Files rail", async () => {
    render(<ChatPage />);
    await screen.findByTestId("batch-suggest-card");
    fireEvent.click(screen.getByRole("button", { name: /Make the summary sheet/i }));

    await waitFor(() => expect(runCalls()).toHaveLength(1));
    // The card reports what happened in plain words...
    await waitFor(() =>
      expect(screen.getByTestId("batch-suggest-card")).toHaveTextContent(
        /Summary sheet made from 7 documents/i,
      ),
    );
    // ...and the file is reachable: making a file OPENS the panel itself
    // (setWorkspaceOpenPersisted), so there is no collapsed-panel button left
    // to click — the rail is already on screen with the sheet on it.
    expect(
      await screen.findByRole("button", { name: "Remove Batch Summary.xlsx from this chat" }),
    ).toBeInTheDocument();
  });

  it("can be dismissed, and stays dismissed for that folder", async () => {
    render(<ChatPage />);
    await screen.findByTestId("batch-suggest-card");

    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    await waitFor(() => expect(card()).not.toBeInTheDocument());
    expect(runCalls()).toHaveLength(0);
  });
});
