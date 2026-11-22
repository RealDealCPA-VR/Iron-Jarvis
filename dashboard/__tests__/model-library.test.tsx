/**
 * Next leaps 43-a (R1.7): Model servers › Models — the model library.
 *
 *  - The tab is on the Model servers page (`?view=models`), not a page of
 *    its own; on it the servers are not polled.
 *  - Each row carries its fit mark (Fits this PC (64 GB) / Tight / Too big)
 *    and its size and memory in words.
 *  - Download shows the exact size and the free space after FIRST; nothing
 *    starts until Start download. A refusal (under a tenth of the drive free)
 *    has no Start.
 *  - Progress arrives by events and Cancel stops it.
 *  - The search box asks Hugging Face only after you type, and says so.
 *  - Chat's download card shows the same progress.
 *
 * The REAL ModelLibrary, page and chat card; only the transport is mocked.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const hooks = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    library: {} as Record<string, unknown>,
    size: {} as unknown,
    downloads: [] as unknown[],
    events: [] as { id: string; type: string; payload: Record<string, unknown>; session_id: null; ts: string }[],
    polled: [] as (string | null)[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: hooks.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  sseUrl: (p: string) => p,
  wsUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  get: vi.fn((path: string) => {
    hooks.gets.push(path);
    if (path.startsWith("/fleet/library")) return Promise.resolve(hooks.library);
    if (path === "/fleet/downloads") return Promise.resolve({ downloads: hooks.downloads });
    return Promise.resolve({});
  }),
  post: vi.fn((path: string, body?: unknown) => {
    hooks.posts.push({ path, body });
    if (path.endsWith("/models/size")) {
      return hooks.size instanceof Error ? Promise.reject(hooks.size) : Promise.resolve(hooks.size);
    }
    if (path.endsWith("/models/download")) {
      return Promise.resolve({
        download: { id: "dl1", node_id: "box", model: "qwen3:8b", name: "Qwen3 8B", state: "downloading",
                    bytes_total: 5_200_000_000, bytes_done: 0, percent: 0 },
      });
    }
    if (path.endsWith("/cancel")) {
      return Promise.resolve({
        download: { id: "dl1", node_id: "box", model: "qwen3:8b", name: "Qwen3 8B", state: "stopped",
                    bytes_total: 5_200_000_000, bytes_done: 1_300_000_000,
                    error: "Stopped: you pressed Cancel. Ollama keeps the part already downloaded; Download again carries on from there." },
      });
    }
    return Promise.resolve({ ok: true });
  }),
  put: vi.fn(() => Promise.resolve({})),
  patch: vi.fn(() => Promise.resolve({})),
  del: vi.fn(() => Promise.resolve({})),
}));
vi.mock("@/lib/useApi", () => {
  const read = (_path: string | null) => ({ data: null, error: null, loading: false, reload: () => {} });
  // Only the POLLED reads are recorded: what the page asks every two seconds.
  const polled = (path: string | null) => {
    hooks.polled.push(path);
    return read(path);
  };
  return { useApi: read, usePolledApi: polled };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: hooks.events, connected: true }) }));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, unauthorized: false, requestError: false, checking: false, health: null, refresh: () => {} }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {}, prefetch: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/fleet",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

import { ModelLibrary, SEARCH_WAIT_MS } from "@/components/fleet/ModelLibrary";
import { ConfigCards } from "@/components/chat/ConfigCards";
import { decodeConfigCards } from "@/lib/configCards";
import { decodeDownload, foldDownload, progressWords } from "@/lib/modelLibrary";
import FleetPage from "@/app/fleet/page";

const SERVER = { id: "box", label: "Ollama on this PC", kind: "ollama", this_pc: true, downloads: true, download_why: "" };
function row(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    name: id,
    good_at: `${id} is good at things`,
    model: id,
    source: "list",
    size_bytes: 5_200_000_000,
    size_words: "about 5.2 GB",
    memory_words: "about 7.0 GB",
    fit: "fits",
    fit_words: "Fits this PC (64 GB)",
    installed: false,
    can_download: true,
    ...over,
  };
}

beforeEach(() => {
  hooks.gets.length = 0;
  hooks.posts.length = 0;
  hooks.polled.length = 0;
  hooks.events = [];
  hooks.downloads = [];
  hooks.library = {
    servers: [SERVER],
    server: SERVER,
    summary: "This PC has 64 GB of memory and 412 GB free on the drive Ollama uses. 2 of the 3 models below fit.",
    models: [
      row("qwen3:8b", { name: "Qwen3 8B" }),
      row("qwen3-next:80b", { fit: "tight", fit_words: "Tight" }),
      row("qwen3.5:122b", { fit: "too_big", fit_words: "Too big", installed: false }),
      row("llama3.2", { installed: true, can_download: false }),
    ],
    search: { words: "", models: [], why: "" },
    checked: "2026-11-22",
  };
  hooks.size = {
    model: "qwen3:8b",
    name: "Qwen3 8B",
    source: "Ollama's library",
    size_words: "5.2 GB",
    size_from: "measured",
    free_after_words: "407 GB free after",
    refusal: "",
    can_start: true,
    partial_words: "Ollama keeps the part already downloaded; Download again carries on from there.",
  };
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("Model servers › Models", () => {
  it("each row has its fit mark, its size and memory in words, and the summary says what this PC has", async () => {
    render(<ModelLibrary />);
    expect((await screen.findByTestId("library-summary")).textContent).toMatch(/^This PC has 64 GB of memory/);
    expect(screen.getByTestId("library-fit-qwen3:8b").textContent).toBe("Fits this PC (64 GB)");
    expect(screen.getByTestId("library-fit-qwen3:8b").getAttribute("data-status")).toBe("fits");
    expect(screen.getByTestId("library-fit-qwen3-next:80b").textContent).toBe("Tight");
    expect(screen.getByTestId("library-fit-qwen3.5:122b").textContent).toBe("Too big");
    expect(screen.getByTestId("library-row-qwen3:8b").textContent).toMatch(/download about 5\.2 GB · memory about 7\.0 GB/);
    expect(screen.getByTestId("library-installed-llama3.2")).toBeTruthy();
    expect(screen.queryByTestId("library-download-llama3.2")).toBeNull();
  });

  it("Download shows the exact size and the free space after first; only Start download starts it", async () => {
    render(<ModelLibrary />);
    fireEvent.click(await screen.findByTestId("library-download-qwen3:8b"));
    const size = await screen.findByTestId("library-size-qwen3:8b");
    expect(size.textContent).toMatch(/Qwen3 8B from Ollama's library: 5\.2 GB \(measured just now\) · 407 GB free after\./);
    expect(hooks.posts).toEqual([{ path: "/fleet/nodes/box/models/size", body: { model: "qwen3:8b" } }]);
    // Nothing started yet.
    expect(hooks.posts.some((p) => p.path.endsWith("/models/download"))).toBe(false);
    fireEvent.click(within(size).getByTestId("library-start-qwen3:8b"));
    await waitFor(() =>
      expect(hooks.posts).toContainEqual({ path: "/fleet/nodes/box/models/download", body: { model: "qwen3:8b" } }),
    );
    expect(await screen.findByTestId("library-download-row-dl1")).toBeTruthy();
  });

  it("a download that would leave less than a tenth of the drive free has no Start, only its words", async () => {
    hooks.size = {
      ...(hooks.size as object),
      refusal: "Not downloaded: qwen3:8b needs 5.2 GB, which would leave 4.0 GB free on the drive Ollama uses, less than a tenth of it (10 GB). Make room on that drive first.",
      can_start: true,
    };
    render(<ModelLibrary />);
    fireEvent.click(await screen.findByTestId("library-download-qwen3:8b"));
    expect((await screen.findByTestId("library-refusal-qwen3:8b")).textContent).toMatch(/less than a tenth/);
    expect(screen.queryByTestId("library-start-qwen3:8b")).toBeNull();
  });

  it("progress arrives by events and Cancel stops it", async () => {
    hooks.downloads = [
      { id: "dl1", node_id: "box", model: "qwen3:8b", name: "Qwen3 8B", state: "downloading",
        bytes_total: 5_200_000_000, bytes_done: 0, percent: 0 },
    ];
    const { rerender } = render(<ModelLibrary />);
    const r = await screen.findByTestId("library-download-row-dl1");
    hooks.events = [{
      id: "e1", type: "model.download.progress", session_id: null, ts: "",
      payload: { id: "dl1", node_id: "box", model: "qwen3:8b", bytes_done: 2_600_000_000, bytes_total: 5_200_000_000, percent: 50 },
    }];
    rerender(<ModelLibrary />);
    await waitFor(() => expect(within(r).getByRole("progressbar").getAttribute("aria-valuenow")).toBe("50"));
    expect(within(r).getByTestId("library-download-row-dl1-words").textContent).toBe("2.6 GB of 5.2 GB · 50%");
    fireEvent.click(within(r).getByTestId("library-download-row-dl1-cancel"));
    await waitFor(() => expect(hooks.posts).toContainEqual({ path: "/fleet/downloads/dl1/cancel", body: {} }));
    const stopped = await screen.findByTestId("library-download-row-dl1");
    await waitFor(() => expect(stopped.getAttribute("data-state")).toBe("stopped"));
    expect(stopped.textContent).toMatch(/Ollama keeps the part already downloaded/);
    expect(within(stopped).queryByRole("button", { name: /Cancel/ })).toBeNull();
  });

  it("the search box asks Hugging Face only after you type, and says only your words are sent", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<ModelLibrary />);
    const box = await screen.findByTestId("library-search");
    expect(document.body.textContent).toMatch(/Only the words you type here are sent to Hugging Face/);
    expect(hooks.gets.filter((g) => g.includes("q="))).toEqual([]);
    fireEvent.change(box, { target: { value: "qwen coder" } });
    expect(hooks.gets.filter((g) => g.includes("q="))).toEqual([]);
    await act(async () => {
      vi.advanceTimersByTime(SEARCH_WAIT_MS + 10);
    });
    await waitFor(() => expect(hooks.gets.filter((g) => g.includes("q="))).toEqual(["/fleet/library?node=box&q=qwen+coder"]));
  });

  it("a finished download says it is in the model menu only when its server is in the model menu", async () => {
    const done = decodeDownload({ id: "dl1", node_id: "box", model: "qwen3:8b", name: "Qwen3 8B", state: "done",
                                  bytes_total: 5_200_000_000, bytes_done: 5_200_000_000 })!;
    expect(progressWords({ ...done, server: "Ollama on this PC", in_menu: true })).toBe(
      "Downloaded, 5.2 GB. It is in the model menu now.",
    );
    expect(progressWords({ ...done, server: "Desk Ollama", in_menu: false })).toBe("Downloaded, 5.2 GB. It is on Desk Ollama now.");
    expect(progressWords({ ...done, server: "", in_menu: false })).toBe("Downloaded, 5.2 GB.");
    // A progress event says nothing of where it lands: what the row said is kept.
    const running = [decodeDownload({ id: "dl1", node_id: "box", state: "downloading", bytes_total: 10, bytes_done: 1,
                                      server: "Ollama on this PC", in_menu: true })!];
    const moved = foldDownload(running, { id: "dl1", node_id: "box", bytes_done: 5, bytes_total: 10, percent: 50 });
    expect(moved[0]).toMatchObject({ percent: 50, server: "Ollama on this PC", in_menu: true });

    // On the page: a saved server the model menu does not list.
    hooks.downloads = [
      { id: "dl1", node_id: "box", model: "qwen3:8b", name: "Qwen3 8B", state: "downloading",
        bytes_total: 5_200_000_000, bytes_done: 0, percent: 0, server: "Desk Ollama", in_menu: false },
    ];
    const { rerender } = render(<ModelLibrary />);
    expect((await screen.findByTestId("library-download-row-dl1")).getAttribute("data-state")).toBe("downloading");
    hooks.events = [{
      id: "e2", type: "model.download.ended", session_id: null, ts: "",
      payload: { id: "dl1", node_id: "box", model: "qwen3:8b", name: "Qwen3 8B", state: "done",
                 bytes_total: 5_200_000_000, bytes_done: 5_200_000_000, percent: 100, error: "",
                 server: "Desk Ollama", in_menu: false },
    }];
    rerender(<ModelLibrary />);
    await waitFor(() => expect(screen.getByTestId("library-download-row-dl1").getAttribute("data-state")).toBe("done"));
    const words = screen.getByTestId("library-download-row-dl1-words").textContent;
    expect(words).toBe("Downloaded, 5.2 GB. It is on Desk Ollama now.");
    expect(words).not.toMatch(/model menu/);
  });

  it("with no model server yet the list still shows what fits this PC, with nothing to download into", async () => {
    hooks.library = {
      ...hooks.library,
      servers: [],
      server: null,
      summary: "No model server yet. Install Ollama on this PC (ollama.com/download) or add a model server on Connections to download one of these. This PC has 31 GB of memory.",
      models: [row("qwen3:8b", { name: "Qwen3 8B", can_download: false }), row("qwen3.5:122b", { fit: "too_big", fit_words: "Too big", can_download: false })],
    };
    render(<ModelLibrary />);
    expect((await screen.findByTestId("library-summary")).textContent).toMatch(/^No model server yet\. Install Ollama/);
    expect(screen.getByTestId("library-fit-qwen3:8b").textContent).toBe("Fits this PC (64 GB)");
    expect(screen.getByTestId("library-fit-qwen3.5:122b").textContent).toBe("Too big");
    expect(screen.queryByTestId("library-download-qwen3:8b")).toBeNull();
    expect(screen.queryByTestId("library-search")).toBeNull();
  });

  it("is a tab on Model servers: Models reads the library and stops polling the servers", async () => {
    window.history.replaceState(null, "", "/fleet?view=models");
    render(<FleetPage />);
    expect(await screen.findByTestId("model-library")).toBeTruthy();
    const tab = screen.getByTestId("fleet-view-models");
    expect(tab.getAttribute("aria-selected")).toBe("true");
    // The servers' two-second read stops while Models is shown (G7).
    expect(hooks.polled[hooks.polled.length - 1]).toBeNull();
    expect(hooks.polled.length).toBeGreaterThan(0);
    fireEvent.click(screen.getByTestId("fleet-view-servers"));
    await waitFor(() => expect(screen.queryByTestId("model-library")).toBeNull());
    expect(window.location.search).toBe("");
    expect(hooks.polled).toContain("/fleet");
    window.history.replaceState(null, "", "/");
  });
});

describe("chat's download card", () => {
  it("decodes only a well-formed download card and shows the same progress row with Cancel", async () => {
    const cards = decodeConfigCards([
      { kind: "download", id: "dl1", node_id: "box", model: "qwen3:8b", name: "Qwen3 8B", server: "Ollama on this PC",
        source: "Ollama's library", size_words: "5.2 GB", free_after_words: "407 GB free after", bytes_total: 5_200_000_000 },
      { kind: "download", id: "", node_id: "box" },
    ]);
    expect(cards).toHaveLength(1);
    hooks.downloads = [
      { id: "dl1", node_id: "box", model: "qwen3:8b", name: "Qwen3 8B", state: "downloading",
        bytes_total: 5_200_000_000, bytes_done: 1_300_000_000, percent: 25 },
    ];
    render(<ConfigCards cards={cards} onSettle={() => {}} />);
    const card = await screen.findByTestId("download-card");
    expect(card.textContent).toMatch(/Qwen3 8B from Ollama's library into Ollama on this PC: 5\.2 GB, 407 GB free after\./);
    await waitFor(() => expect(within(card).getByTestId("download-card-row-words").textContent).toBe("1.3 GB of 5.2 GB · 25%"));
    expect(within(card).getByRole("link", { name: /Open Model servers › Models/ }).getAttribute("href")).toBe("/fleet?view=models");
    expect(within(card).getByTestId("download-card-row-cancel")).toBeTruthy();
  });
});
