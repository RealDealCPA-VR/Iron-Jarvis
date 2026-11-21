/**
 * 10-a (build-out plan R2.3): the phone chat on the PC shows a file sent from
 * the phone as a chip.
 *
 * The daemon writes the user's line of a phone chat itself
 * (`comm/threads.CommThreadStore.append`) and, since 10-a, puts the files the
 * phone sent on it with the desk's own fields, `attachmentNames` and
 * `attachmentPaths`. The page draws those as the same chip a dropped file
 * gets (`AttachmentFooter`), with no phone-only code. The page for real,
 * transports mocked (the harness of third-plan-g18-reply-home).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

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
    stalled = false;
  }
  return {
    FakeApiError,
    FakeStreamError,
    api: { getResponses: {} as Record<string, unknown> },
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
  put: async () => ({ id: "t1", title: "t" }),
  del: async () => ({}),
}));
vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    textStore: { get: () => "", subscribe: () => () => {} },
    tools: [],
    approval: null,
    run: async () => ({ reply: "" }),
    abort: () => {},
  }),
}));
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
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";

const FOLDER = "C:\\Users\\me\\Documents\\Iron Jarvis\\2026-11-20 From your phone";

function resetApi() {
  for (const k of Object.keys(H.api.getResponses)) delete H.api.getResponses[k];
  H.api.getResponses["/models"] = { models: [] };
  H.api.getResponses["/chat/personas"] = { personas: [] };
  H.api.getResponses["/chat/threads"] = { threads: [] };
  H.api.getResponses["/settings"] = { settings: {} };
  H.api.getResponses["/projects"] = { projects: [] };
  H.api.getResponses["/agents/mentionable"] = { agents: [] };
  H.api.getResponses["/skills"] = { skills: [] };
  H.api.getResponses["/workflows"] = { workflows: [] };
  H.api.getResponses["/tools"] = { tools: [] };
  H.api.getResponses["/undo?session_id=chat"] = { actions: [] };
  H.api.getResponses["/chat/approvals/pending"] = { approvals: [] };
}

function phoneChat(id: string, messages: Record<string, unknown>[]) {
  H.api.getResponses[`/chat/threads/${id}`] = {
    id,
    title: "Val on Telegram",
    messages,
    owner: "daemon",
    comm_channel: "telegram",
    comm_display: "Val",
    updated_at: "2026-11-20T10:00:00Z",
  };
  window.history.replaceState({}, "", `/chat?thread=${id}`);
}

beforeEach(() => {
  resetApi();
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("10-a: a file from the phone shows as a chip in the phone chat", () => {
  it("the user's line carries the photo's chip under its words", async () => {
    phoneChat("tP", [
      {
        role: "user",
        content: "add this to the March list",
        attachmentNames: ["photo.jpg"],
        attachmentPaths: [`${FOLDER}\\photo.jpg`],
      },
      { role: "assistant", content: "Added to the March list." },
    ]);
    render(<ChatPage />);

    expect(await screen.findByText("Added to the March list.")).toBeInTheDocument();
    const words = await screen.findByText("add this to the March list");
    const chip = await screen.findByText("photo.jpg");
    // The chip sits in the same bubble as the words it came with.
    expect(words.closest("div")?.parentElement?.textContent ?? "").toContain("photo.jpg");
    expect(chip).toBeInTheDocument();
    // Names only on screen: the PC's folder path is not printed in the chat.
    expect(screen.queryByText(new RegExp("From your phone\\\\photo"))).toBeNull();
  });

  it("several files are several chips; a line with none has no chip", async () => {
    phoneChat("tQ", [
      { role: "user", content: "what is the rent?" },
      { role: "assistant", content: "Send me the lease." },
      {
        role: "user",
        content: "(Sent with no message: lease.pdf, page-2.jpg)",
        attachmentNames: ["lease.pdf", "page-2.jpg"],
        attachmentPaths: [`${FOLDER}\\lease.pdf`, `${FOLDER}\\page-2.jpg`],
      },
    ]);
    render(<ChatPage />);

    expect(await screen.findByText("what is the rent?")).toBeInTheDocument();
    expect(await screen.findByText("lease.pdf")).toBeInTheDocument();
    expect(screen.getByText("page-2.jpg")).toBeInTheDocument();
    expect(screen.getAllByText(/^(lease\.pdf|page-2\.jpg)$/)).toHaveLength(2);
  });
});
