// @vitest-environment jsdom
/**
 * Next leaps 41-a (build-out plan R1.5): a switch for each thing a paired
 * computer may do, on the Your other computers card.
 *
 * Pinned: one line per computer ("laptop may do nothing here yet.") that
 * opens into its switches, drawn OFF for a new pairing; a press sends ONLY
 * that switch (PATCH /peers/{name}/allows {key: value}); a switch whose thing
 * comes later is one quiet line, never a switch; what the OTHER computer lets
 * this one do is its own line; the once-note for a pairing from before the
 * switches (D-41.1) goes with one press; the card reads again on
 * `peer.allows_changed`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  view: {} as unknown,
  gets: [] as string[],
  calls: [] as { method: string; path: string; body: unknown }[],
  answers: {} as Record<string, unknown>,
  events: [] as { id: string; type: string }[],
  eventTypes: null as string[] | null,
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  const call = (method: string) => async (path: string, body?: unknown) => {
    H.calls.push({ method, path, body });
    return JSON.parse(JSON.stringify(H.answers[`${method} ${path}`] ?? H.view));
  };
  return {
    ApiError,
    API_BASE: "http://test",
    ijToken: () => "tok-1",
    get: vi.fn(async (path: string) => {
      H.gets.push(path);
      return JSON.parse(JSON.stringify(H.view));
    }),
    post: vi.fn(call("POST")),
    put: vi.fn(call("PUT")),
    patch: vi.fn(call("PATCH")),
    del: vi.fn(call("DELETE")),
  };
});

vi.mock("@/lib/useEvents", () => ({
  useEvents: (_max: number, opts?: { types?: string[] }) => {
    H.eventTypes = opts?.types ?? null;
    return { events: H.events, connected: true };
  },
}));

vi.mock("@/lib/desktopShell", () => ({ requestTidyRestart: vi.fn(async () => "restarting") }));

import { OtherComputers } from "@/components/settings/OtherComputers";
import { LATER_SWITCHES, PEER_EVENT_TYPES, decodePeers, switchLabel } from "@/lib/otherComputers";

/** The daemon's one table (peers/allows.SWITCHES), as GET /peers sends it. */
const SWITCHES = [
  { key: "may_send_tasks", label: "Send me tasks", short: "send tasks", ready: true, folders: "" },
  { key: "may_read_record", label: "Look up my work record", short: "look up your work record", ready: false, folders: "record_folders" },
  { key: "may_ask_back", label: "Ask me questions about tasks I send it", short: "ask you questions", ready: false, folders: "" },
  { key: "may_use_models", label: "Use my models", short: "use your models", ready: false, folders: "" },
];
const OFF = { may_send_tasks: false, may_read_record: false, may_ask_back: false, may_use_models: false };

function peer(over: Record<string, unknown> = {}) {
  return {
    name: "laptop",
    their_name: "laptop",
    address: "http://192.168.1.21:8787",
    paired_at: "2026-11-14T10:00:00+00:00",
    last_ok_at: "2026-11-14T10:00:00+00:00",
    last_heard_at: null,
    last_error: "",
    last_error_at: null,
    heard_from: "",
    allows: { ...OFF, record_folders: [], code_folders: [] },
    may_line: "laptop may do nothing here yet.",
    they_allow: { known: true, allows: [], older: false, at: "2026-11-14T10:00:00+00:00" },
    they_line: "laptop lets this computer do nothing yet.",
    kept_note: "",
    ...over,
  };
}

function view(peers: unknown[]) {
  return {
    this_name: "desk",
    peers,
    code: null,
    network: { saved: true, applied: true, addresses: ["192.168.1.20:8787"] },
    switches: SWITCHES,
  };
}

beforeEach(() => {
  H.view = view([peer()]);
  H.gets = [];
  H.calls = [];
  H.answers = {};
  H.events = [];
  H.eventTypes = null;
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function opened() {
  render(<OtherComputers />);
  const line = await screen.findByTestId("peer-may-laptop");
  return line;
}

function patches() {
  return H.calls.filter((c) => c.method === "PATCH");
}

describe("one line per computer that opens into its switches", () => {
  it("is folded at first, says what it may do, and opens into the switches drawn off", async () => {
    const line = await opened();
    expect(line).toHaveTextContent("laptop may do nothing here yet.");
    expect(line).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("peer-switches-laptop")).toBeNull();
    fireEvent.click(line);
    const box = await screen.findByTestId("peer-switches-laptop");
    const sw = within(box).getByRole("switch", { name: switchLabel("laptop", "Send me tasks") });
    expect(sw).toHaveAttribute("aria-checked", "false");
    expect(line).toHaveAttribute("aria-expanded", "true");
  });

  it("a switch whose thing comes later is one quiet line, never a switch", async () => {
    fireEvent.click(await opened());
    const box = await screen.findByTestId("peer-switches-laptop");
    expect(within(box).getAllByRole("switch")).toHaveLength(1);
    const later = screen.getByTestId("peer-later-laptop");
    expect(later).toHaveTextContent(
      `${LATER_SWITCHES} Look up my work record, Ask me questions about tasks I send it, Use my models.`,
    );
    expect(screen.queryByTestId("peer-switch-laptop-may_use_models")).toBeNull();
  });

  it("a press sends ONLY that switch, and the card reads again", async () => {
    // Two switches this version can turn on (as a later phase makes one
    // ready): the press on one sends that one alone.
    H.view = {
      ...view([peer({ allows: { ...OFF, may_read_record: true, record_folders: [], code_folders: [] } })]),
      switches: SWITCHES.map((s) => (s.key === "may_read_record" ? { ...s, ready: true } : s)),
    };
    H.answers["PATCH /peers/laptop/allows"] = { said: "laptop may: send tasks." };
    fireEvent.click(await opened());
    expect(screen.getAllByRole("switch")).toHaveLength(2);
    const gets = H.gets.length;
    fireEvent.click(await screen.findByTestId("peer-switch-laptop-may_send_tasks"));
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0]).toEqual({
      method: "PATCH",
      path: "/peers/laptop/allows",
      body: { may_send_tasks: true },
    });
    await waitFor(() => expect(screen.getByTestId("other-computers-note")).toHaveTextContent("laptop may: send tasks."));
    await waitFor(() => expect(H.gets.length).toBeGreaterThan(gets));
  });

  it("an ON switch is drawn on and its press turns it off", async () => {
    H.view = view([
      peer({ allows: { ...OFF, may_send_tasks: true, record_folders: [], code_folders: [] },
        may_line: "laptop may: send tasks." }),
    ]);
    const line = await opened();
    expect(line).toHaveTextContent("laptop may: send tasks.");
    fireEvent.click(line);
    const sw = await screen.findByTestId("peer-switch-laptop-may_send_tasks");
    expect(sw).toHaveAttribute("aria-checked", "true");
    fireEvent.click(sw);
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0].body).toEqual({ may_send_tasks: false });
  });
});

describe("the other side's line and the once-note", () => {
  it("says what the other computer lets this one do, on its own line", async () => {
    H.view = view([peer({ they_line: "laptop lets this computer: send tasks." })]);
    await opened();
    expect(screen.getByTestId("peer-they-laptop")).toHaveTextContent("laptop lets this computer: send tasks.");
  });

  it("a pairing from before the switches says so once, and OK takes it away", async () => {
    const note = "Paired before this version, so “Send me tasks” stays on for it, as before. Everything else starts off.";
    H.view = view([peer({ kept_note: note, may_line: "laptop may: send tasks." })]);
    await opened();
    expect(screen.getByTestId("peer-kept-laptop")).toHaveTextContent(note);
    fireEvent.click(screen.getByTestId("peer-kept-ok-laptop"));
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0].body).toEqual({ seen_note: true });
  });

  it("nothing is drawn by an older Iron Jarvis that sends no switches", async () => {
    H.view = { ...view([peer({ may_line: "" })]), switches: undefined };
    render(<OtherComputers />);
    await screen.findByTestId("peer-laptop");
    expect(screen.queryByTestId("peer-allows-laptop")).toBeNull();
  });
});

describe("reads", () => {
  it("reads again when what a paired computer may do changed", () => {
    expect(PEER_EVENT_TYPES).toContain("peer.allows_changed");
  });

  it("decodes the switches and the line defensively", () => {
    const v = decodePeers({
      ...view([peer({ allows: { may_send_tasks: "yes", may_use_models: true, junk: true } })]),
      switches: [...SWITCHES, { key: "drop_tables", label: "x", ready: true }, null],
    });
    expect(v.switches.map((s) => s.key)).toEqual(SWITCHES.map((s) => s.key));
    expect(v.peers[0].allows).toEqual({ may_use_models: true });
  });
});
