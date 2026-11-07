import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * v1.443.0 — two lines the user can read.
 *
 * (1) The Slack row says what Iron Jarvis has HEARD. The user's report: Slack
 *     was "connected" and their messages went "into the void"; a connected
 *     socket that heard nothing looked exactly like one that works. The row
 *     now shows the daemon's `listening.words` (warn tone when nothing came,
 *     quiet once it hears), and the two-way note still wins when set.
 * (2) The browser card lists the recent downloads Iron Jarvis noticed, with
 *     "Show in folder" (the FOLDER, never the file — it could be a program),
 *     and draws nothing on an older daemon or an empty list.
 */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    responses: {} as Record<string, unknown>,
    posts: [] as Array<{ path: string; body: unknown }>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    return Promise.resolve(r);
  },
  post: (path: string, body: unknown) => {
    api.posts.push({ path, body });
    return Promise.resolve({ ok: true });
  },
  patch: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
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
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {actions}
    </div>
  ),
}));

import ChannelsPage from "@/components/settings/pages/NotificationsPage";
import { RecentDownloads, folderOf, siteHost, sizeWords } from "@/components/browser/RecentDownloads";

function seedChannels(row: Record<string, unknown>) {
  api.responses["/comm/channels"] = {
    channels: [
      {
        name: "hq",
        type: "slack",
        builtin: false,
        last_test_ok: true,
        last_test_at: "2026-11-07T10:00:00Z",
        events: [],
        inbound_enabled: true,
        chat_enabled: true,
        allowed_senders_count: 1,
        saved_fields: [],
        values: {},
        two_way_note: null,
        ...row,
      },
    ],
  };
  api.responses["/comm/channel-types"] = { types: [] };
}

afterEach(() => {
  cleanup();
  api.responses = {};
  api.posts = [];
});

describe("the Slack row says what it has heard (v1.443.0)", () => {
  it("warns when connected and Slack has sent nothing", async () => {
    seedChannels({
      listening: { connected_at: "2026-11-07T12:00:00Z", words: "Connected to Slack, but Slack hasn't sent Jarvis any message yet.", tone: "warn" },
    });
    render(<ChannelsPage />);
    const line = await screen.findByTestId("channel-listening-hq");
    expect(line.textContent).toContain("hasn't sent Jarvis any message yet");
    expect(line.className).toContain("text-tone-warn");
  });

  it("is quiet once it hears, and names the last message", async () => {
    seedChannels({
      listening: {
        connected_at: "2026-11-07T12:00:00Z",
        last_message_at: new Date(Date.now() - 120_000).toISOString(),
        words: "Listening. Jarvis answers your direct messages.",
        tone: "quiet",
      },
    });
    render(<ChannelsPage />);
    const line = await screen.findByTestId("channel-listening-hq");
    expect(line.className).not.toContain("text-tone-warn");
    expect(line.textContent).toMatch(/Last message from you:/);
  });

  it("leaves the stage to the two-way note when one is set", async () => {
    seedChannels({
      two_way_note: "Two-way is on, but no one is on the allowlist.",
      listening: { words: "Listening.", tone: "quiet" },
    });
    render(<ChannelsPage />);
    await screen.findByTestId("channel-two-way-note-hq");
    expect(screen.queryByTestId("channel-listening-hq")).toBeNull();
  });
});

describe("recent downloads on the browser card (v1.443.0)", () => {
  it("lists them and opens the FOLDER, never the file", async () => {
    api.responses["/browser/downloads?limit=5"] = {
      total: 7,
      downloads: [
        { id: "dl_7", name: "setup.exe", path: "C:\\Users\\VR\\Downloads\\setup.exe", site: "https://www.example.com/x", bytes: 2_200_000, at: new Date().toISOString(), exists: true },
        { id: "dl_6", name: "old.pdf", path: "C:\\Users\\VR\\Downloads\\old.pdf", exists: false },
      ],
    };
    render(<RecentDownloads />);
    await screen.findByTestId("browser-recent-downloads");
    expect(screen.getByTestId("browser-download-dl_7").textContent).toContain("from example.com");
    expect(screen.getByTestId("browser-download-dl_6").textContent).toContain("no longer there");
    expect(screen.queryByTestId("browser-download-folder-dl_6")).toBeNull();
    expect(screen.getByText(/5 more/)).toBeTruthy();
    fireEvent.click(screen.getByTestId("browser-download-folder-dl_7"));
    await waitFor(() => expect(api.posts.length).toBe(1));
    expect(api.posts[0]).toEqual({ path: "/documents/open", body: { path: "C:\\Users\\VR\\Downloads" } });
  });

  it("draws nothing on an older daemon or an empty list", async () => {
    const { container } = render(<RecentDownloads />);
    await new Promise((r) => setTimeout(r, 30));
    expect(container.innerHTML).toBe("");
    cleanup();
    api.responses["/browser/downloads?limit=5"] = { downloads: [], total: 0 };
    const again = render(<RecentDownloads />);
    await new Promise((r) => setTimeout(r, 30));
    expect(again.container.innerHTML).toBe("");
  });

  it("words sizes, hosts and folders", () => {
    expect(sizeWords(12)).toBe("12 bytes");
    expect(sizeWords(2048)).toBe("2 KB");
    expect(sizeWords(null)).toBe("");
    expect(siteHost("https://www.irs.gov/pub/x.pdf")).toBe("irs.gov");
    expect(siteHost("not a url")).toBe("");
    expect(folderOf("C:\\a\\b.pdf")).toBe("C:\\a");
    expect(folderOf("/home/u/b.pdf")).toBe("/home/u");
  });
});
