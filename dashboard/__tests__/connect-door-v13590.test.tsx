/**
 * v1.359.0 lane 9C (G41 remainder) — NAMING AN APP THAT IS NOT CONNECTED
 * ALWAYS OFFERS ITS CONNECT DOOR, whatever the model does.
 *
 * The daemon now reads the user's message itself: "find my tax files in
 * Google Drive" with Drive not connected ends the turn with the Directory's
 * "Connect Google Drive in Settings" door although the model never called
 * app_connect. The door rides the reply's existing `doors` field, so the
 * chat draws it with the SAME DoorsStrip as every other door — no new card,
 * no new look.
 *
 * The chain pinned here, end to end:
 *   the REAL daemon's done frame (the contract fixture written by
 *   tests/test_connect_door_v13590.py from the real lanes and a model that
 *   only answers in words; IJ_REGEN_CONNECT_DOOR=1 regenerates it)
 *   → the REAL stream decode (`useChatStream().run()` over an SSE fetch)
 *   → the REAL chat page settling that turn (transport mocked, the
 *     chat-failure-press-v13550 harness)
 *   → the REAL DoorsStrip under the reply, and the door saved with the
 *     message (so it survives a reload).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { DoorsStrip } from "@/components/chat/DoorsStrip";

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
    code = "";
    provider = "";
    model = "";
    title = "";
    fix = "";
    technical = "";
    fixKind = "";
    fixProvider = "";
  }
  const stream = {
    bodies: [] as Record<string, unknown>[],
    streaming: false,
    approval: null as null,
    version: 0,
    listeners: new Set<() => void>(),
    settle: null as null | { ok: (r: Record<string, unknown>) => void; fail: (e: unknown) => void },
    bump() {
      stream.version += 1;
      for (const l of [...stream.listeners]) l();
    },
    release(r: Record<string, unknown>) {
      const s = stream.settle;
      stream.settle = null;
      stream.streaming = false;
      stream.bump();
      s?.ok(r);
    },
  };
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
    },
    stream,
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
  post: async () => ({}),
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

// The PAGE's stream hook is the transport double; the decode itself is the
// real module (`vi.importActual` below), so the page is settled with exactly
// what the real hook resolves for the daemon's frame.
vi.mock("@/lib/useChatStream", async () => {
  const React = await import("react");
  const subscribe = (cb: () => void) => {
    H.stream.listeners.add(cb);
    return () => {
      H.stream.listeners.delete(cb);
    };
  };
  const run = (body: Record<string, unknown>) => {
    H.stream.bodies.push(body);
    H.stream.streaming = true;
    H.stream.bump();
    return new Promise<Record<string, unknown>>((ok, fail) => {
      H.stream.settle = { ok, fail };
    });
  };
  const abort = () => {
    if (H.stream.settle) H.stream.release({ reply: "" });
  };
  return {
    StreamError: H.FakeStreamError,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    useChatStream: () => {
      React.useSyncExternalStore(subscribe, () => H.stream.version);
      return {
        streaming: H.stream.streaming,
        text: "",
        tools: [],
        approval: H.stream.approval,
        phase: H.stream.streaming ? "working" : null,
        withFiles: false,
        startedAt: null,
        lastEventAt: null,
        run,
        abort,
      };
    },
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
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
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";

/** CRLF-normalised at the reader (this checkout is autocrlf). */
function read(rel: string): string {
  return readFileSync(join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");
}

type Door = { href: string; label: string };
const FIXTURE = JSON.parse(read("__tests__/fixtures/connect-door-v13590.json")) as {
  stream_done: { reply: string; doors: Door[]; tools_used: string[] };
};
const DRIVE_DOOR: Door = { href: "/settings?section=connections-apps", label: "Connect Google Drive in Settings" };

/** The daemon's done frame through the REAL stream decode. */
async function decodeDaemonFrame(): Promise<Record<string, unknown>> {
  const real = await vi.importActual<typeof import("@/lib/useChatStream")>("@/lib/useChatStream");
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(
        new TextEncoder().encode(`event: done\ndata: ${JSON.stringify(FIXTURE.stream_done)}\n\n`),
      );
      controller.close();
    },
  });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 200 })));
  const { result, unmount } = renderHook(() => real.useChatStream());
  let settled: Record<string, unknown> | null = null;
  await act(async () => {
    settled = (await result.current.run({ messages: [] })) as unknown as Record<string, unknown>;
  });
  unmount();
  vi.unstubAllGlobals();
  return settled!;
}

beforeEach(() => {
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.streaming = false;
  H.stream.settle = null;
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
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("G41: naming an app that is not connected offers its Connect door", () => {
  it("the daemon's frame carries the Directory's door, and the model never called app_connect", () => {
    expect(FIXTURE.stream_done.doors).toEqual([DRIVE_DOOR]);
    expect(FIXTURE.stream_done.tools_used).not.toContain("app_connect");
    // The address is the Directory's own section (the same one app_connect's
    // fix door and the Directory link use — one door, one address).
    expect(read("components/settings/SettingsHome.tsx")).toContain('id: "connections-apps"');
  });

  it("the real stream decode keeps the door exactly as the daemon sent it", async () => {
    const settled = await decodeDaemonFrame();
    expect(settled.doors).toEqual([DRIVE_DOOR]);
    expect(settled.reply).toBe(FIXTURE.stream_done.reply);
  });

  it("the chat page draws it under the reply with the real DoorsStrip and saves it with the message", async () => {
    const settled = await decodeDaemonFrame();
    render(<ChatPage />);
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "find my tax files in Google Drive" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(H.stream.settle).not.toBeNull());
    act(() => H.stream.release(settled));

    // The door, under the reply, pointing at the Directory.
    const link = await screen.findByRole("link", { name: DRIVE_DOOR.label });
    expect(link).toHaveAttribute("href", DRIVE_DOOR.href);
    expect(await screen.findByText(FIXTURE.stream_done.reply)).toBeInTheDocument();

    // The look stays the same: the pill every other door wears.
    const { container } = render(<DoorsStrip doors={[{ href: "/workflows", label: "Open the canvas" }]} />);
    const plain = container.querySelector("a");
    expect(plain).not.toBeNull();
    expect(link.className).toBe(plain!.className);

    // Saved with the message (the thread PUT round-trips it), so it survives
    // a reload.
    await waitFor(() => {
      const saved = H.api.puts
        .map((p) => (p.body?.messages as { role?: string; doors?: Door[] }[] | undefined) ?? [])
        .flat()
        .filter((m) => m.role === "assistant" && Array.isArray(m.doors));
      expect(saved.at(-1)?.doors).toEqual([DRIVE_DOOR]);
    });
  });
});
