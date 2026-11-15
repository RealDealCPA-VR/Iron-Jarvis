// @vitest-environment jsdom
/**
 * Your other computers (v1.497.0, vision plan leap 7, Phase 1): pair two PCs
 * running Iron Jarvis with a six-digit code, in Settings › Connections.
 *
 * Pinned: the network step (turn on with one confirmation, restart included;
 * saved-not-applied; on, with the addresses), the code shown and its steps,
 * the join form and its words, the paired list (status line, Check, Remove),
 * the reads (mount + one Check, events, never a clock), and the two places
 * that point here (the by-hand door card, a paired remote-agent row).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const H = vi.hoisted(() => ({
  view: {} as unknown,
  gets: [] as string[],
  calls: [] as { method: string; path: string; body: unknown }[],
  answers: {} as Record<string, unknown>,
  errors: {} as Record<string, Error>,
  getError: null as Error | null,
  events: [] as { id: string; type: string }[],
  eventTypes: null as string[] | null,
  tidy: "restarting" as "restarting" | "refused" | "no-bridge",
  tidyCalls: 0,
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
    const key = `${method} ${path}`;
    if (H.errors[key]) throw H.errors[key];
    return JSON.parse(JSON.stringify(H.answers[key] ?? H.view));
  };
  return {
    ApiError,
    API_BASE: "http://test",
    ijToken: () => "tok-1",
    get: vi.fn(async (path: string) => {
      H.gets.push(path);
      if (H.getError) throw H.getError;
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

vi.mock("@/lib/desktopShell", () => ({
  requestTidyRestart: vi.fn(async () => {
    H.tidyCalls += 1;
    return H.tidy;
  }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import type React from "react";
import { OtherComputers } from "@/components/settings/OtherComputers";
import {
  ANSWERS_CANNOT_COME_BACK,
  FIREWALL_LINE,
  NEEDS_NETWORK,
  NETWORK_OFF,
  NETWORK_SAVED,
  NO_PEERS,
  OTHER_COMPUTERS_HREF,
  OTHER_COMPUTERS_SECTION,
  PAIRED_REMOTE_NOTE,
  PEER_EVENT_TYPES,
  RESTART_NO_BRIDGE,
  RESTART_REFUSED,
  TAILSCALE_LINE,
  codeSteps,
  decodePeers,
  peerStatusLine,
  removeTitle,
  typedCode,
} from "@/lib/otherComputers";

const OFF = { saved: false, applied: false, addresses: [] as string[] };
const ON = { saved: true, applied: true, addresses: ["192.168.1.20:8787", "100.101.1.2:8787"] };

function view(over: Record<string, unknown> = {}) {
  return { this_name: "desk", peers: [], code: null, network: OFF, ...over };
}

const DESK_PEER = {
  name: "laptop",
  their_name: "VALENTINO-LT",
  address: "http://192.168.1.21:8787",
  paired_at: "2026-11-14T10:00:00+00:00",
  last_ok_at: "2026-11-14T10:00:00+00:00",
  last_heard_at: null,
  last_error: "",
  last_error_at: null,
};

beforeEach(() => {
  H.view = view();
  H.gets = [];
  H.calls = [];
  H.answers = {};
  H.errors = {};
  H.getError = null;
  H.events = [];
  H.eventTypes = null;
  H.tidy = "restarting";
  H.tidyCalls = 0;
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

async function shown() {
  render(<OtherComputers />);
  return screen.findByTestId("other-computers-network");
}

function posted(path: string) {
  return H.calls.filter((c) => c.method === "POST" && c.path === path);
}

describe("the network step", () => {
  it("off: says why, and turns on with one confirmation, restart included", async () => {
    await shown();
    expect(screen.getByTestId("other-computers-network")).toHaveTextContent(NETWORK_OFF);
    const on = screen.getByTestId("other-computers-network-on");
    fireEvent.click(on);
    expect(H.calls.filter((c) => c.method === "PUT")).toHaveLength(0);
    fireEvent.click(screen.getByTestId("other-computers-network-on"));
    await waitFor(() => expect(H.tidyCalls).toBe(1));
    const puts = H.calls.filter((c) => c.method === "PUT");
    expect(puts).toEqual([{ method: "PUT", path: "/settings/values", body: { values: { lan_access: true } } }]);
  });

  it("in a browser tab it says to quit and reopen; a refused restart says where to press", async () => {
    H.tidy = "no-bridge";
    await shown();
    fireEvent.click(screen.getByTestId("other-computers-network-on"));
    fireEvent.click(screen.getByTestId("other-computers-network-on"));
    await waitFor(() => expect(screen.getByTestId("other-computers-note")).toHaveTextContent(RESTART_NO_BRIDGE));
    cleanup();
    H.tidy = "refused";
    await shown();
    fireEvent.click(screen.getByTestId("other-computers-network-on"));
    fireEvent.click(screen.getByTestId("other-computers-network-on"));
    await waitFor(() => expect(screen.getByTestId("other-computers-note")).toHaveTextContent(RESTART_REFUSED));
  });

  it("a failed save says so and restarts nothing", async () => {
    const { ApiError } = await import("@/lib/api");
    H.errors["PUT /settings/values"] = new ApiError("That setting is locked.", 409);
    await shown();
    fireEvent.click(screen.getByTestId("other-computers-network-on"));
    fireEvent.click(screen.getByTestId("other-computers-network-on"));
    expect(await screen.findByTestId("other-computers-problem")).toHaveTextContent("That setting is locked.");
    expect(H.tidyCalls).toBe(0);
  });

  it("saved but not in effect yet: says so and offers the restart", async () => {
    H.view = view({ network: { saved: true, applied: false, addresses: [] } });
    await shown();
    expect(screen.getByTestId("other-computers-network")).toHaveTextContent(NETWORK_SAVED);
    fireEvent.click(screen.getByTestId("other-computers-restart"));
    await waitFor(() => expect(H.tidyCalls).toBe(1));
  });

  it("on: lists the addresses another computer types, Tailscale and the firewall, and turns off with a confirmation", async () => {
    H.view = view({ network: ON });
    await shown();
    const list = screen.getByTestId("other-computers-addresses");
    expect(list).toHaveTextContent("192.168.1.20:8787");
    expect(list).toHaveTextContent("100.101.1.2:8787");
    const net = screen.getByTestId("other-computers-network");
    expect(net).toHaveTextContent(TAILSCALE_LINE);
    expect(net).toHaveTextContent(FIREWALL_LINE);
    fireEvent.click(screen.getByTestId("other-computers-network-off"));
    fireEvent.click(screen.getByTestId("other-computers-network-off"));
    await waitFor(() => expect(H.tidyCalls).toBe(1));
    expect(H.calls.find((c) => c.method === "PUT")?.body).toEqual({ values: { lan_access: false } });
  });
});

describe("showing a code", () => {
  it("needs the network first, and says so", async () => {
    await shown();
    const press = screen.getByTestId("other-computers-show-code");
    expect(press).toBeDisabled();
    expect(press).toHaveAttribute("title", NEEDS_NETWORK);
    expect(screen.getByTestId("other-computers-needs-network")).toHaveTextContent(NEEDS_NETWORK);
  });

  it("shows the code in two groups with the steps and when it ends; Cancel drops it", async () => {
    H.view = view({ network: ON });
    H.answers["POST /peers/code"] = view({
      network: ON,
      code: { code: "482917", shown: "482 917", expires_at: "2026-11-14T18:30:00+00:00" },
    });
    await shown();
    fireEvent.click(screen.getByTestId("other-computers-show-code"));
    const box = await screen.findByTestId("other-computers-code");
    expect(box).toHaveTextContent("482 917");
    expect(box).toHaveTextContent(codeSteps(ON.addresses));
    expect(box).toHaveTextContent("Works until");
    expect(posted("/peers/code")[0].body).toEqual({});
    H.answers["POST /peers/code/cancel"] = view({ network: ON });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByTestId("other-computers-code")).toBeNull());
  });

  it("sends this computer's name only when it was changed", async () => {
    H.view = view({ network: ON });
    await shown();
    fireEvent.change(screen.getByTestId("other-computers-this-name"), { target: { value: "front desk" } });
    fireEvent.click(screen.getByTestId("other-computers-show-code"));
    await waitFor(() => expect(posted("/peers/code")).toHaveLength(1));
    expect(posted("/peers/code")[0].body).toEqual({ this_name: "front desk" });
  });

  it("a code that runs out is read again at that moment, not on a clock", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const ends = new Date(Date.now() + 60_000).toISOString();
    H.view = view({ network: ON, code: { code: "482917", shown: "482 917", expires_at: ends } });
    await shown();
    const before = H.gets.length;
    await act(async () => {
      vi.advanceTimersByTime(30_000);
    });
    expect(H.gets.length).toBe(before);
    H.view = view({ network: ON });
    await act(async () => {
      vi.advanceTimersByTime(31_000);
    });
    await waitFor(() => expect(H.gets.length).toBe(before + 1));
    await waitFor(() => expect(screen.queryByTestId("other-computers-code")).toBeNull());
  });
});

describe("using a code", () => {
  async function openForm() {
    await shown();
    fireEvent.click(screen.getByTestId("other-computers-use-code"));
    return screen.findByTestId("other-computers-join");
  }

  it("Pair waits for an address and six digits, then sends them", async () => {
    H.answers["POST /peers/join"] = { ...view({ peers: [{ ...DESK_PEER, name: "desk" }] }), said: "Paired with desk." };
    await openForm();
    const pair = screen.getByTestId("other-computers-pair");
    expect(pair).toBeDisabled();
    fireEvent.change(screen.getByTestId("other-computers-address"), { target: { value: " 192.168.1.20 " } });
    fireEvent.change(screen.getByTestId("other-computers-code-input"), { target: { value: "482 91" } });
    expect(pair).toBeDisabled();
    fireEvent.change(screen.getByTestId("other-computers-code-input"), { target: { value: "482 917" } });
    fireEvent.change(screen.getByTestId("other-computers-call-it"), { target: { value: "office" } });
    expect(pair).toBeEnabled();
    fireEvent.click(pair);
    await waitFor(() => expect(screen.getByTestId("other-computers-note")).toHaveTextContent("Paired with desk."));
    expect(posted("/peers/join")[0].body).toEqual({
      address: "192.168.1.20",
      code: "482917",
      name: "office",
      this_name: "",
    });
    expect(screen.queryByTestId("other-computers-join")).toBeNull();
    expect(screen.getByTestId("peer-desk")).toBeInTheDocument();
  });

  it("a refusal is shown in the other computer's own words and the form keeps what was typed", async () => {
    const { ApiError } = await import("@/lib/api");
    H.errors["POST /peers/join"] = new ApiError("That code is not right. Check the six digits shown on that computer.", 409);
    await openForm();
    fireEvent.change(screen.getByTestId("other-computers-address"), { target: { value: "192.168.1.20" } });
    fireEvent.change(screen.getByTestId("other-computers-code-input"), { target: { value: "111111" } });
    fireEvent.click(screen.getByTestId("other-computers-pair"));
    expect(await screen.findByTestId("other-computers-problem")).toHaveTextContent("That code is not right.");
    expect(screen.getByTestId("other-computers-address")).toHaveValue("192.168.1.20");
  });

  it("says when this computer cannot be reached, so answers could not come back", async () => {
    await openForm();
    expect(screen.getByTestId("other-computers-no-answers")).toHaveTextContent(ANSWERS_CANNOT_COME_BACK);
    cleanup();
    H.view = view({ network: ON });
    await openForm();
    expect(screen.queryByTestId("other-computers-no-answers")).toBeNull();
  });
});

describe("the paired computers", () => {
  it("lists each with its name, address and when it was last heard from", async () => {
    H.view = view({ peers: [DESK_PEER] });
    await shown();
    const row = await screen.findByTestId("peer-laptop");
    expect(row).toHaveTextContent("@laptop");
    expect(row).toHaveTextContent("(VALENTINO-LT)");
    expect(row).toHaveTextContent("http://192.168.1.21:8787");
    expect(screen.getByTestId("peer-status-laptop")).toHaveTextContent(/^Last heard from /);
  });

  it("says nothing is paired yet when nothing is", async () => {
    await shown();
    expect(screen.getByTestId("other-computers-empty")).toHaveTextContent(NO_PEERS);
  });

  it("asks every paired computer once when the card opens, and never when none is paired", async () => {
    H.view = view({ peers: [DESK_PEER] });
    await shown();
    await waitFor(() => expect(posted("/peers/check")).toHaveLength(1));
    cleanup();
    H.calls = [];
    H.view = view();
    await shown();
    await new Promise((r) => setTimeout(r, 30));
    expect(posted("/peers/check")).toHaveLength(0);
  });

  it("Check asks that one computer; Remove takes two presses and says what happened", async () => {
    H.view = view({ peers: [DESK_PEER] });
    H.answers["POST /peers/laptop/check"] = { ok: true, peer: DESK_PEER };
    H.answers["DELETE /peers/laptop"] = { removed: true, told: true, said: "Removed. laptop removed this computer from its list too." };
    await shown();
    await screen.findByTestId("peer-laptop");
    fireEvent.click(screen.getByTestId("peer-check-laptop"));
    await waitFor(() => expect(posted("/peers/laptop/check")).toHaveLength(1));
    fireEvent.click(screen.getByTestId("peer-remove-laptop"));
    expect(H.calls.filter((c) => c.method === "DELETE")).toHaveLength(0);
    fireEvent.click(screen.getByTestId("peer-remove-laptop"));
    await waitFor(() =>
      expect(screen.getByTestId("other-computers-note")).toHaveTextContent("Removed. laptop removed this computer"),
    );
    expect(H.calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual(["/peers/laptop"]);
  });

  it("a name with characters a path cannot hold is sent encoded", async () => {
    H.view = view({ peers: [{ ...DESK_PEER, name: "a/b" }] });
    await shown();
    await screen.findByTestId("peer-a/b");
    fireEvent.click(screen.getByTestId("peer-check-a/b"));
    await waitFor(() => expect(posted("/peers/a%2Fb/check")).toHaveLength(1));
  });
});

describe("reading", () => {
  it("reads again when a pairing is made or ended elsewhere (a burst is one read)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { rerender } = render(<OtherComputers />);
    await screen.findByTestId("other-computers-network");
    expect(H.eventTypes).toEqual(PEER_EVENT_TYPES);
    const before = H.gets.length;
    H.events = [{ id: "e1", type: "peer.paired" }];
    rerender(<OtherComputers />);
    H.events = [{ id: "e2", type: "peer.paired" }];
    rerender(<OtherComputers />);
    await act(async () => {
      vi.advanceTimersByTime(700);
    });
    await waitFor(() => expect(H.gets.length).toBe(before + 1));
  });

  it("an older Iron Jarvis (404) shows nothing; a failed read says so", async () => {
    const { ApiError } = await import("@/lib/api");
    H.getError = new ApiError("Not Found", 404);
    const { container } = render(<OtherComputers />);
    await new Promise((r) => setTimeout(r, 30));
    expect(container).toBeEmptyDOMElement();
    cleanup();
    H.getError = new ApiError("boom", 500);
    render(<OtherComputers />);
    expect(await screen.findByText(/Your other computers couldn't load/)).toBeInTheDocument();
  });
});

describe("the words and shapes", () => {
  it("decodes rubbish safely and takes a code only as six digits", () => {
    expect(decodePeers(null)).toEqual({ thisName: "", peers: [], code: null, network: { saved: false, applied: false, addresses: [] } });
    expect(decodePeers({ code: { code: "12345" } }).code).toBeNull();
    expect(decodePeers({ code: { code: "12a456" } }).code).toBeNull();
    expect(decodePeers({ code: { code: "123456", expires_at: "x" } }).code?.shown).toBe("123 456");
    expect(decodePeers({ peers: [{ name: "" }, { name: "desk" }, 5] }).peers.map((p) => p.name)).toEqual(["desk"]);
    expect(decodePeers({ network: { applied: "yes", addresses: ["a", 3, ""] } }).network).toEqual({
      saved: false,
      applied: false,
      addresses: ["a"],
    });
  });

  it("a failed check newer than the last answer is said, with why", () => {
    const ago = (iso: string) => `at ${iso.slice(11, 16)}`;
    const peer = decodePeers({ peers: [DESK_PEER] }).peers[0];
    expect(peerStatusLine(peer, ago)).toEqual({ words: "Last heard from at 10:00.", warn: false });
    const failed = { ...peer, lastError: "No computer answered.", lastErrorAt: "2026-11-14T11:00:00+00:00" };
    expect(peerStatusLine(failed, ago)).toEqual({ words: "Did not answer at 11:00: No computer answered.", warn: true });
    const answeredSince = { ...failed, lastHeardAt: "2026-11-14T12:00:00+00:00" };
    expect(peerStatusLine(answeredSince, ago).warn).toBe(false);
    expect(peerStatusLine({ ...peer, lastOkAt: null }, ago).words).toBe("Not heard from yet.");
  });

  it("a typed code is six ASCII digits with spaces and dashes dropped", () => {
    expect(typedCode("482 917")).toBe("482917");
    expect(typedCode("482-917")).toBe("482917");
    expect(typedCode("48291")).toBe("");
    expect(typedCode("４８２９１７")).toBe("");
  });

  it("the steps name the address to type", () => {
    expect(codeSteps(["192.168.1.20:8787"])).toContain("this computer's address, 192.168.1.20:8787");
    expect(codeSteps(["a:1", "b:1"])).toContain("one of this computer's addresses: a:1 or b:1");
  });

  it("never says 'token' on screen: a computer is paired, and a pairing ends (VOCABULARY.md)", () => {
    const code = readFileSync(join(__dirname, "..", "lib/otherComputers.ts"), "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");
    expect(code).not.toMatch(/\btokens?\b/i);
    expect(NETWORK_OFF).not.toMatch(/token/i);
    expect(removeTitle("laptop")).toMatch(/^End the pairing with laptop on both computers\. /);
    expect(removeTitle("laptop")).not.toMatch(/token/i);
  });
});

const read = (rel: string) => readFileSync(join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");

describe("where it lives, and what points here", () => {
  it("is a Settings › Connections section that controls the network setting", () => {
    const src = read("components/settings/SettingsHome.tsx");
    expect(src).toContain(`id: "${OTHER_COMPUTERS_SECTION}"`);
    expect(src).toContain(`lan_access: "${OTHER_COMPUTERS_SECTION}"`);
    expect(OTHER_COMPUTERS_HREF).toBe("/settings?focus=lan_access");
  });

  it("the by-hand door card points at pairing", async () => {
    H.view = { enabled: false, caller: "", url: "" };
    const { AnotherJarvisCard } = await import("@/components/agents/AnotherJarvisCard");
    render(<AnotherJarvisCard />);
    const line = await screen.findByTestId("another-jarvis-pair-instead");
    expect(line.querySelector("a")).toHaveAttribute("href", OTHER_COMPUTERS_HREF);
  });

  it("a paired remote agent's row offers no Edit, Delete or message-back switch, and says where it is changed", async () => {
    const { RemoteAgentsSection } = await import("@/components/agents/SetupCard");
    const paired = {
      name: "desk",
      base_url: "http://192.168.1.20:8787/agents/task-door/task",
      kind: "http-task",
      enabled: true,
      has_credential: true,
      inbound_enabled: true,
      paired: true,
    };
    render(
      <RemoteAgentsSection remotes={[paired]} faces={{}} facesSupported={false} onChanged={() => {}} onFaceChanged={() => {}} />,
    );
    const chip = screen.getByTestId("remote-paired");
    expect(chip).toHaveAttribute("href", OTHER_COMPUTERS_HREF);
    expect(chip).toHaveAttribute("title", PAIRED_REMOTE_NOTE);
    expect(screen.queryByTestId("inbound-toggle")).toBeNull();
    expect(screen.queryByTestId("inbound-rotate")).toBeNull();
    expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Delete/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Test/ })).toBeInTheDocument();
    cleanup();
    render(
      <RemoteAgentsSection
        remotes={[{ ...paired, paired: false }]}
        faces={{}}
        facesSupported={false}
        onChanged={() => {}}
        onFaceChanged={() => {}}
      />,
    );
    expect(screen.queryByTestId("remote-paired")).toBeNull();
    expect(screen.getByRole("button", { name: /Edit/ })).toBeInTheDocument();
  });
});
