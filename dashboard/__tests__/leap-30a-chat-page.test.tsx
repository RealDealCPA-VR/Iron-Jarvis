/**
 * Idea 30-a on the CHAT PAGE itself (v1.547.0, review A7): a saved reply's
 * figure opens its file beside the chat at its place.
 *
 * The chain under test, end to end through the real page, the real Markdown
 * renderer and the real DocPreview (only the transport is mocked):
 * - the page answers SourceLink's OPEN_PLACE_EVENT (its window listener);
 * - it passes the place (`at`) to DocPreview: a PDF figure asks for the page
 *   picture by the record's id;
 * - a second figure in the SAME file at another place remounts the preview
 *   (its key carries the place), so it loads the other sheet;
 * - Copy message copies the words without the links.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    gets: [] as string[],
    getResponses: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => "tok",
  get: async (path: string) => {
    H.gets.push(path);
    const r = H.getResponses[path];
    if (r !== undefined) return r;
    const prefix = Object.keys(H.getResponses).find((k) => k.endsWith("*") && path.startsWith(k.slice(0, -1)));
    if (prefix) return H.getResponses[prefix];
    throw new H.FakeApiError(`unmocked GET ${path}`, 404);
  },
  post: async () => ({}),
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false, text: "", tools: [], approval: null, phase: null,
    run: async () => ({ reply: "done" }), abort: () => {},
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
    supported: true, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
    readAloud: () => {}, readingKey: null,
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", async () => await vi.importActual("react-markdown"));

import ChatPage from "@/app/chat/page";

const AT = "2026-11-20T08:20:00";
const PDF = "C:\\Clients\\statement.pdf";
const BOOK = "C:\\Clients\\budget.xlsx";
const REPLY =
  "Wages were $[48,210.00](#source/src_aaaa1111aaaa1111); rent [1,250](#source/src_bbbb2222bbbb2222) " +
  "and travel [7,300](#source/src_cccc3333cccc3333).";
const THREAD = {
  id: "t1",
  title: "Wages",
  messages: [
    { role: "user", content: "What were the wages, rent and travel?", at: AT },
    { role: "assistant", content: REPLY, at: AT },
  ],
};

function mark(id: string, path: string, locator: Record<string, number | string>, shown: string, key: string) {
  return {
    id, path, name: path.split("\\").pop(), kind: path.endsWith(".pdf") ? "pdf" : "sheet", locator, shown, key,
    context: `the line with ${shown}`, present: true, changed: false,
  };
}

let copied: string[] = [];

beforeEach(() => {
  H.gets.length = 0;
  copied = [];
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: async (t: string) => void copied.push(t) },
  });
  H.getResponses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/chat/threads/t1": THREAD,
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
    "/sources/src_aaaa1111aaaa1111": {
      marks: [mark("src_aaaa1111aaaa1111", PDF, { page: 7, line: 3 }, "48,210.00", "48210")],
    },
    "/sources/src_bbbb2222bbbb2222": {
      marks: [mark("src_bbbb2222bbbb2222", BOOK, { sheet: "Summary", cell: "C2" }, "1,250", "1250")],
    },
    "/sources/src_cccc3333cccc3333": {
      marks: [mark("src_cccc3333cccc3333", BOOK, { sheet: "Detail", cell: "B2" }, "7,300", "7300")],
    },
    [`/documents/preview?path=${encodeURIComponent(PDF)}`]: {
      kind: "pdf", name: "statement.pdf", path: PDF, suffix: ".pdf",
    },
    [`/documents/preview?path=${encodeURIComponent(BOOK)}&sheet=Summary`]: {
      kind: "sheet", name: "budget.xlsx", path: BOOK, suffix: ".xlsx", sheets: ["Summary", "Detail"],
      sheet: "Summary", rows: [["Item", "Q2", "Q3"], ["Rent", "1200", "1,250"]], first_row: 1,
    },
    [`/documents/preview?path=${encodeURIComponent(BOOK)}&sheet=Detail`]: {
      kind: "sheet", name: "budget.xlsx", path: BOOK, suffix: ".xlsx", sheets: ["Summary", "Detail"],
      sheet: "Detail", rows: [["Item", "Amount"], ["Travel", "7,300"]], first_row: 1,
    },
    "/documents/siblings*": { files: [] },
    "/documents/places*": { places: [] },
  };
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function openThread() {
  window.history.replaceState({}, "", "/chat?thread=t1");
  render(<ChatPage />);
  await screen.findByText(/Wages were/);
}

function figure(text: string): HTMLElement {
  const hit = screen.getAllByTestId("source-link").find((b) => b.textContent === text);
  if (!hit) throw new Error(`no figure ${text}`);
  return hit;
}

describe("a saved reply's figure opens its file beside the chat (idea 30-a)", () => {
  it("opens the PDF at its page, asked for by the record's id", async () => {
    await openThread();
    fireEvent.click(figure("48,210.00"));
    const img = await screen.findByTestId("doc-preview-page-image");
    expect(img.getAttribute("src")).toBe(
      "http://127.0.0.1:8787/documents/page-image?mark=src_aaaa1111aaaa1111&token=tok",
    );
    expect(screen.getByTestId("doc-preview-place")).toHaveTextContent(
      "Read from page 7, line 3 of statement.pdf.",
    );
    // Opened: the press says nothing beside the figure.
    expect(H.gets).toContain("/sources/src_aaaa1111aaaa1111");
  });

  it("a second figure in the same file at another place opens there (the preview remounts)", async () => {
    await openThread();
    fireEvent.click(figure("1,250"));
    const first = await screen.findByTestId("doc-preview-lit-cell");
    expect(first).toHaveTextContent("1,250");
    expect(H.gets).toContain(`/documents/preview?path=${encodeURIComponent(BOOK)}&sheet=Summary`);
    fireEvent.click(figure("7,300"));
    await waitFor(() =>
      expect(H.gets).toContain(`/documents/preview?path=${encodeURIComponent(BOOK)}&sheet=Detail`),
    );
    await waitFor(() => expect(screen.getByTestId("doc-preview-lit-cell")).toHaveTextContent("7,300"));
    expect(screen.getByTestId("doc-preview-place")).toHaveTextContent("Read from cell B2 on Detail of budget.xlsx.");
  });

  it("Copy message copies the words without the links", async () => {
    await openThread();
    const row = screen.getAllByTestId("reply-actions").at(-1)!;
    fireEvent.click(within(row).getByRole("button", { name: "Copy message" }));
    await waitFor(() => expect(copied).toHaveLength(1));
    expect(copied[0]).toBe("Wages were $48,210.00; rent 1,250 and travel 7,300.");
  });
});
