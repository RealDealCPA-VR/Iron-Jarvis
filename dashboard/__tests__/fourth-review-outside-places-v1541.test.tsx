/**
 * Fourth fix plan, group 32, C-PG-1: "Sending outside this PC".
 *
 * Settings › Permissions & ledger's "What Jarvis can reach" block gains one
 * row that lists every live place outside this PC Iron Jarvis sends to
 * (GET /settings/access `sends`, built by daemon/routes/access_summary.py):
 * each place by its kind, its name and its host, how it is asked, and a
 * "Change" link to the page that changes it. A fact that cannot be read says
 * so; it never reads as "None set up".
 *
 * The REAL AccessSummary; only the network (lib/api) is a double.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  }
  return { FakeApiError, gets: {} as Record<string, unknown> };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  get: async (path: string) => {
    const r = H.gets[path.split("?")[0]];
    if (r instanceof Error) throw r;
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return JSON.parse(JSON.stringify(r));
  },
  put: async () => ({}),
  post: async () => ({}),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/settings",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

import {
  AccessSummary,
  SENDS_MODELS_LINE,
  SENDS_NONE,
  SENDS_TOO_OLD,
  SENDS_UNREADABLE,
} from "@/components/settings/AccessSummary";
import { __resetApiCache } from "@/lib/apiCache";

const W = { timeout: 5_000 };

/** One live place of every kind, in the daemon's own shape and words. */
const PLACES = [
  { kind: "model_server", kind_label: "Model server", name: "DGX", where: "192.168.1.20", ask: "once",
    asked: "Approved when it was added: it gets your chats when it answers them. Pointing it somewhere new asks every time.",
    href: "/fleet" },
  { kind: "speech_server", kind_label: "Speech-to-text server", name: "Voice: speech-to-text server address",
    where: "speech.example.com", ask: "once", asked: "Approved when it was set: it gets what you dictate. Pointing it somewhere new asks every time.",
    href: "/settings?focus=voice_transcribe_base_url" },
  { kind: "web_service", kind_label: "Web service", name: "Weather", where: "api.weather.example.org", ask: "every_time",
    asked: "Reading from it follows its approval setting (Ask first); anything else asks every time.", href: "/connections" },
  { kind: "extension", kind_label: "Extension", name: "Notes online", where: "mcp.example.net", ask: "own_setting",
    asked: "Its tools follow their approval setting (Ask first). Pointing it somewhere new asks every time.", href: "/tools" },
  { kind: "app", kind_label: "App using Iron Jarvis", name: "Claude desktop", where: "Claude desktop", ask: "once",
    asked: "Approved when you connected it: what it looks up (titles, folders, file names, short summaries) goes to Claude's model with the rest of that chat; client work and Local only work are never shared.",
    href: "/chat" },
  { kind: "memory_base", kind_label: "Memory base", name: "team-notes", where: "Notion", ask: "once",
    asked: "Approved when it was added: searches and what Jarvis saves there go without asking.", href: "/memory" },
  { kind: "webhook", kind_label: "Webhook", name: "deploys", where: "hooks.example.com", ask: "once",
    asked: "Approved when it was set up: its events go out without asking. Pointing it somewhere new asks every time.",
    href: "/webhooks" },
  { kind: "destination", kind_label: "Destination", name: "team-alerts", where: "Slack", ask: "own_setting",
    asked: "The alerts you picked go there without asking; a message from chat or an agent follows its approval setting (Ask first).",
    href: "/channels" },
];

function seed(access: unknown) {
  H.gets = {
    "/settings/access": access,
    "/connectors": { connectors: [] },
    "/grants": { grants: [] },
  };
}

function draw() {
  render(<AccessSummary values={{}} defs={[]} />);
}

beforeEach(() => {
  __resetApiCache();
  window.localStorage.clear();
});
afterEach(() => cleanup());

describe("Sending outside this PC", () => {
  it("lists every live place with its kind, name, host, how it is asked and a link to its page", async () => {
    seed({ reads: { limited: false, folders: [] }, writes: { chat_files_dir: "" }, sends: { places: PLACES, unread: [] } });
    draw();
    const row = await screen.findByTestId("access-sends", {}, W);
    expect(row.textContent).toMatch(/^Sending outside this PC/);
    await waitFor(() => expect(within(row).getAllByRole("listitem")).toHaveLength(PLACES.length), W);
    for (const p of PLACES) {
      const item = within(row).getByTestId(`access-send-${p.kind}`);
      const text = item.textContent ?? "";
      expect(text, p.kind).toContain(`${p.kind_label} ${p.name}`);
      if (p.where !== p.name) expect(text, p.kind).toContain(`(${p.where})`);
      expect(text, p.kind).toContain(p.asked);
      const link = within(item).getByRole("link", { name: `Change ${p.kind_label} ${p.name}` });
      expect(link.getAttribute("href"), p.kind).toBe(p.href);
      expect(link.textContent).toBe("Change");
    }
    // The app's name is not said twice.
    expect(within(row).getByTestId("access-send-app").textContent).not.toContain("(Claude desktop)");
    expect(row.textContent).toContain(SENDS_MODELS_LINE);
    expect(row.textContent).not.toContain(SENDS_NONE);
    // The retired word for an extension is never on screen.
    expect(row.textContent).not.toMatch(/tool server/i);
  });

  it("nothing set up says so, and says where the AI services are", async () => {
    seed({ reads: { limited: false, folders: [] }, writes: { chat_files_dir: "" }, sends: { places: [], unread: [] } });
    draw();
    const row = await screen.findByTestId("access-sends", {}, W);
    await waitFor(() => expect(row.textContent).toContain(`${SENDS_NONE} ${SENDS_MODELS_LINE}`), W);
    expect(within(row).queryByRole("list")).toBeNull();
  });

  it("a kind that could not be read is named; it never reads as none", async () => {
    seed({
      reads: { limited: false, folders: [] },
      writes: { chat_files_dir: "" },
      sends: { places: [PLACES[6]], unread: ["memory_base", "extension"] },
    });
    draw();
    const row = await screen.findByTestId("access-sends", {}, W);
    await waitFor(() =>
      expect(within(row).getByTestId("access-sends-unread").textContent).toContain(
        "Couldn’t read: memory bases, extensions.",
      ),
      W,
    );
    expect(within(row).getByTestId("access-send-webhook")).toBeTruthy();
    seed({ reads: { limited: false, folders: [] }, writes: { chat_files_dir: "" }, sends: { places: [], unread: ["webhook"] } });
    cleanup();
    __resetApiCache();
    draw();
    const again = await screen.findByTestId("access-sends", {}, W);
    await waitFor(() => expect(again.textContent).toContain("Couldn’t read: webhooks."), W);
    expect(again.textContent).not.toContain(SENDS_NONE);
  });

  it("the route that cannot be read, or an older Iron Jarvis, says so", async () => {
    seed(new H.FakeApiError("boom", 500));
    draw();
    const row = await screen.findByTestId("access-sends", {}, W);
    await waitFor(() => expect(row.textContent).toContain(SENDS_UNREADABLE), W);
    expect(row.textContent).not.toContain(SENDS_NONE);
    cleanup();
    __resetApiCache();
    seed({ reads: { limited: false, folders: [] }, writes: { chat_files_dir: "" } });
    draw();
    const old = await screen.findByTestId("access-sends", {}, W);
    await waitFor(() => expect(old.textContent).toContain(SENDS_TOO_OLD), W);
    expect(old.textContent).not.toContain(SENDS_NONE);
  });
});
