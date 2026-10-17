/**
 * v1.346.0 ("media in chat"), lane Y-dash: the chat UI stays exactly as it
 * is; the media simply appears there, the card says what a generation would
 * spend, and the bell says when one is done.
 *
 *  - an `approval` frame carrying `cost_note` survives the stream decoder's
 *    whitelist and the hook (`PendingApproval.costNote`); the card draws ONE
 *    quiet line (`approval-cost`) in the local-only line's style; a card with
 *    no quote renders today's text, byte for byte;
 *  - the bell subscribes to `media.completed` / `media.failed` and maps each
 *    to a row ("Your <kind> is ready" / "Your <kind> could not be made") that
 *    opens the chat the media was asked in (`/chat?thread=<id>`);
 *  - a reply holding the markdown image syntax with a LOCAL .mp4 path renders
 *    a <video> through the daemon's media route (and a .png an <img>, a .mp3
 *    an <audio>) — the existing MarkdownMedia path, nothing new;
 *  - a media path in a turn's `documents` lands in the files rail.
 */
import { act, cleanup, fireEvent, render, renderHook, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ASIDE } from "./helpers/dashGuard";

const H = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  put: vi.fn(),
  notify: vi.fn(),
  eventsRef: { current: [] as unknown[] },
}));

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return {
    ...real,
    API_BASE: "http://127.0.0.1:8787",
    ijToken: () => "tok-1",
    post: H.post,
    get: H.get,
    put: H.put,
  };
});

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: H.eventsRef.current, connected: true }),
}));

vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: H.notify,
  }),
}));

import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { ArtifactsRail, collectArtifacts, fileKind } from "@/components/chat/ArtifactsRail";
import { Markdown } from "@/components/Markdown";
import { BELL_EVENT_TYPES, NotificationBell, toActivity } from "@/components/NotificationBell";
import type { IJEvent } from "@/lib/types";
import { decodeSSE, useChatStream, type PendingApproval } from "@/lib/useChatStream";

const COST = "Generate 1 image on Pixio for 12 credits (your price)";
const BASE: PendingApproval = {
  id: "apr_m1",
  callId: "c1",
  tool: "generate_media",
  args: { kind: "image", prompt: "a red bicycle" },
};
const WITH_COST: PendingApproval = { ...BASE, costNote: COST };

const MP4 = "C:/Users/x/media/pixio/j1.mp4";
const PNG = "C:/Users/x/media/pixio/j2.png";
const MP3 = "C:/Users/x/media/pixio/j3.mp3";

beforeEach(() => {
  H.post.mockReset();
  H.post.mockResolvedValue({ ok: true });
  H.get.mockReset();
  H.get.mockResolvedValue({});
  H.put.mockReset();
  H.put.mockResolvedValue({ changed: [] });
  H.notify.mockReset();
  H.eventsRef.current = [];
  document.title = "Iron Jarvis";
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the approval card says what a generation would spend", () => {
  it("draws one quiet line with the daemon's words when cost_note is present", () => {
    render(<ApprovalCard approval={WITH_COST} />);
    const card = screen.getByTestId("chat-approval-card");
    const line = within(card).getByTestId("approval-cost");
    expect(line.textContent).toBe(COST);
    // The local-only line's style: no box, whole pixels, quiet.
    expect(line.className).not.toMatch(/\b(border|rounded|bg-)/);
    expect(line.className).not.toMatch(/\.5px|\[\d+\.\d+px\]/);
    expect(line.className).toContain("text-zinc-400");
    expect(line.textContent).not.toMatch(ASIDE);
    // It sits where the user decides: inside the card, before the waiting line.
    const waiting = within(card).getByTestId("approval-waiting");
    expect(line.compareDocumentPosition(waiting) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders nothing extra without one: the card's text is today's, byte for byte", () => {
    const { unmount } = render(<ApprovalCard approval={WITH_COST} />);
    const withText = screen.getByTestId("chat-approval-card").textContent ?? "";
    unmount();
    render(<ApprovalCard approval={BASE} />);
    const card = screen.getByTestId("chat-approval-card");
    expect(within(card).queryByTestId("approval-cost")).toBeNull();
    const plain = card.textContent ?? "";
    // The only difference between the two cards is the cost words.
    expect(withText).toBe(plain.replace("Waiting for you.", `${COST}Waiting for you.`));
    // The card's existing wording is pinned as it was before this wave.
    expect(plain).toContain("The assistant wants to");
    expect(plain).toContain("generate_media");
    expect(plain).toContain("Waiting for you.");
    expect(plain).toContain(
      "The turn is paused until you answer. “Once” runs only this call; “this conversation” stops asking for generate_media here; Deny refuses it and the assistant is told you declined.",
    );
    expect(plain).not.toContain("credits");
    // The answers are unchanged.
    expect(
      within(card)
        .getAllByRole("button")
        .map((b) => b.textContent?.trim()),
    ).toEqual(["Allow once", "Allow for this conversation", "Deny"]);
  });

  it("a local-only media card keeps its once-only shape AND shows the cost", () => {
    render(<ApprovalCard approval={{ ...WITH_COST, onceOnly: true }} />);
    const card = screen.getByTestId("chat-approval-card");
    expect(within(card).getByTestId("approval-cost").textContent).toBe(COST);
    expect(within(card).getByTestId("approval-local-only")).toBeTruthy();
    expect(
      within(card)
        .getAllByRole("button")
        .map((b) => b.textContent?.trim()),
    ).toEqual(["Allow once", "Deny"]);
  });
});

describe("the stream keeps cost_note", () => {
  it("the decoder keeps a non-empty string and drops anything else", () => {
    const frame = { id: "a", call_id: "c", tool: "generate_media" };
    const yes = decodeSSE("approval", JSON.stringify({ ...frame, cost_note: COST })) as Record<string, unknown>;
    expect(yes.cost_note).toBe(COST);
    for (const junk of [1, true, null, "", "   ", { words: COST }]) {
      const no = decodeSSE("approval", JSON.stringify({ ...frame, cost_note: junk })) as Record<string, unknown>;
      expect("cost_note" in no, JSON.stringify(junk)).toBe(false);
    }
    // The fields beside it still decode (nothing was displaced).
    const both = decodeSSE(
      "approval",
      JSON.stringify({ ...frame, cost_note: COST, once_only: true, can_always: true, args_hash: "h1" }),
    ) as Record<string, unknown>;
    expect(both.once_only).toBe(true);
    expect(both.can_always).toBe(true);
    expect(both.args_hash).toBe("h1");
  });

  async function approvalAfter(frame: Record<string, unknown>) {
    let close: () => void = () => {};
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(`event: approval\ndata: ${JSON.stringify(frame)}\n\n`));
        close = () => controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { status: 200 })));
    const { result } = renderHook(() => useChatStream());
    let run: Promise<unknown> = Promise.resolve();
    act(() => {
      run = result.current.run({ messages: [] }).catch(() => null);
    });
    await vi.waitFor(() => expect(result.current.approval?.id).toBe("apr_m9"));
    const seen = result.current.approval;
    await act(async () => {
      close();
      await run;
    });
    return seen!;
  }

  it("the real hook puts costNote on the pending approval, and only when sent", async () => {
    const frame = { id: "apr_m9", call_id: "c9", tool: "generate_media", args: { kind: "image" } };
    const got = await approvalAfter({ ...frame, cost_note: COST });
    expect(got.costNote).toBe(COST);
    expect(got.tool).toBe("generate_media");
    expect("costNote" in (await approvalAfter(frame))).toBe(false); // control
  });
});

function mediaEvent(type: "media.completed" | "media.failed", payload: Record<string, unknown>, id = "evt-media-1"): IJEvent {
  return {
    id,
    type,
    session_id: null,
    ts: "2026-10-17T09:00:00",
    payload,
  } as unknown as IJEvent;
}

const DONE = mediaEvent("media.completed", {
  job_id: "mj_1",
  kind: "video",
  provider: "pixio",
  thread_id: "chat_abc123",
  path: MP4,
  cost_note: "free (Maker: 4 left today)",
});
const FAILED = mediaEvent(
  "media.failed",
  {
    job_id: "mj_2",
    kind: "image",
    provider: "pixio",
    thread_id: "chat_abc123",
    error: "Pixio refused it: the prompt breaks its content policy",
  },
  "evt-media-2",
);

describe("the bell says when a generation is done", () => {
  it("subscribes to both media events", () => {
    expect(BELL_EVENT_TYPES).toContain("media.completed");
    expect(BELL_EVENT_TYPES).toContain("media.failed");
  });

  it("maps media.completed to 'Your <kind> is ready' opening that chat, the cost as the body", () => {
    const item = toActivity(DONE)!;
    expect(item).not.toBeNull();
    expect(item.title).toBe("Your video is ready");
    expect(item.href).toBe("/chat?thread=chat_abc123");
    expect(item.body).toBe("free (Maker: 4 left today)");
    expect(item.id).toBe("evt-media-1");
    expect(item.quiet).toBeFalsy(); // the user asked for the ping
    expect(item.title).not.toMatch(ASIDE);
  });

  it("maps media.failed to 'Your <kind> could not be made' with the reason, opening that chat", () => {
    const item = toActivity(FAILED)!;
    expect(item.title).toBe("Your image could not be made");
    expect(item.href).toBe("/chat?thread=chat_abc123");
    expect(item.body).toBe("Pixio refused it: the prompt breaks its content policy");
  });

  it("an unknown kind reads as media; no thread opens the chat page; the id is encoded", () => {
    expect(toActivity(mediaEvent("media.completed", { kind: "hologram", thread_id: "" }))!.title).toBe(
      "Your media is ready",
    );
    expect(toActivity(mediaEvent("media.completed", { kind: "audio" }))!.href).toBe("/chat");
    expect(toActivity(mediaEvent("media.failed", { kind: "music", thread_id: "a b/c" }))!.href).toBe(
      "/chat?thread=a%20b%2Fc",
    );
    expect(toActivity(mediaEvent("media.completed", { kind: "music" }))!.body).toBe("");
  });

  it("renders as Recent activity rows that open the chat, never counted as waiting on you", async () => {
    H.eventsRef.current = [FAILED, DONE];
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
    const ready = await screen.findByText("Your video is ready");
    const readyLink = ready.closest("a") as HTMLAnchorElement;
    expect(readyLink.getAttribute("href")).toBe("/chat?thread=chat_abc123");
    expect(readyLink.textContent).toContain("free (Maker: 4 left today)");
    const failed = screen.getByText("Your image could not be made");
    const failedLink = failed.closest("a") as HTMLAnchorElement;
    expect(failedLink.getAttribute("href")).toBe("/chat?thread=chat_abc123");
    expect(failedLink.textContent).toContain("content policy");
    expect(screen.queryByRole("button", { name: /\d+ notifications/ })).toBeNull();
  });

  it("pings the desktop with the ready words", async () => {
    H.eventsRef.current = [DONE];
    render(<NotificationBell />);
    await vi.waitFor(() => expect(H.notify).toHaveBeenCalled());
    expect(H.notify.mock.calls[0][0]).toBe("Your video is ready");
  });
});

describe("the media appears in the chat through the existing renderer", () => {
  it("a local .mp4 path in the image syntax renders a video through the daemon's media route", () => {
    render(<Markdown content={`Your video is ready.\n\n![video](${MP4})`} />);
    const video = document.querySelector("video");
    expect(video).not.toBeNull();
    const src = video!.getAttribute("src") ?? "";
    expect(src).toContain("http://127.0.0.1:8787/creative/file-by-path?path=");
    expect(src).toContain(encodeURIComponent(MP4));
    expect(src).toContain("token=tok-1");
    expect(video!.hasAttribute("controls")).toBe(true);
    expect(document.querySelector("img")).toBeNull();
    expect(screen.queryByTestId("remote-media-gate")).toBeNull();
    expect(screen.getByText("Your video is ready.")).toBeTruthy();
  });

  it("a local .png renders an img and a .mp3 an audio element, each through the same route", () => {
    render(<Markdown content={`![image](${PNG})\n\n![audio](${MP3})`} />);
    const img = document.querySelector("img");
    expect(img?.getAttribute("src") ?? "").toContain("/creative/file-by-path?path=");
    expect(img?.getAttribute("src") ?? "").toContain(encodeURIComponent(PNG));
    const audio = document.querySelector("audio");
    expect(audio?.getAttribute("src") ?? "").toContain(encodeURIComponent(MP3));
    expect(document.querySelector("video")).toBeNull();
  });

  it("a Windows backslash path to the media works too", () => {
    render(<Markdown content={"![out](C:\\Users\\x\\media\\pixio\\j1.mp4)"} />);
    expect(document.querySelector("video")).not.toBeNull();
  });
});

describe("the media lands in the files rail", () => {
  it("a media path in a turn's documents is a rail row of its kind", () => {
    const items = collectArtifacts([undefined, [MP4], [PNG]]);
    expect(items.map((i) => i.path)).toEqual([PNG, MP4]);
    expect(fileKind(MP4)).toBe("video");
    expect(fileKind(PNG)).toBe("image");
    expect(fileKind(MP3)).toBe("audio");
    const onPreview = vi.fn();
    render(<ArtifactsRail items={items} onPreview={onPreview} />);
    expect(screen.getByText("j1.mp4")).toBeTruthy();
    expect(screen.getByText("j2.png")).toBeTruthy();
    expect(screen.getByText("2 files")).toBeTruthy();
    fireEvent.click(screen.getByTitle(MP4));
    expect(onPreview).toHaveBeenCalledWith(MP4);
  });
});
