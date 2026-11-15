// @vitest-environment jsdom
/**
 * Your other computers, an independent review (v1.497.0). Each test pins a
 * defect the review found in the first cut of the card, red on that cut:
 *
 *  - "Restarting Iron Jarvis…" stayed on the card after Iron Jarvis had come
 *    back (an untrue sentence under a network section that now says it is on).
 *  - Pair was greyed out with nothing saying why (the Wave D rule: a disabled
 *    press says why, `lib/whyDisabled`).
 *  - With the setting saved on but Iron Jarvis not restarted yet, the hints
 *    under the code buttons and the join form still said "Turn on the setting
 *    above" — it IS on; what is missing is the restart.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  view: {} as unknown,
  calls: [] as { method: string; path: string; body: unknown }[],
  tidy: "restarting" as "restarting" | "refused" | "no-bridge",
  tidyCalls: 0,
  events: [] as { id: string; type: string }[],
  gets: 0,
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
    return JSON.parse(JSON.stringify(H.view));
  };
  return {
    ApiError,
    API_BASE: "http://test",
    ijToken: () => "tok-1",
    get: vi.fn(async () => {
      H.gets += 1;
      return JSON.parse(JSON.stringify(H.view));
    }),
    post: vi.fn(call("POST")),
    put: vi.fn(call("PUT")),
    patch: vi.fn(call("PATCH")),
    del: vi.fn(call("DELETE")),
  };
});

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: H.events, connected: true }),
}));

vi.mock("@/lib/desktopShell", () => ({
  requestTidyRestart: vi.fn(async () => {
    H.tidyCalls += 1;
    return H.tidy;
  }),
}));

import { OtherComputers } from "@/components/settings/OtherComputers";
import { DaemonEpochContext } from "@/lib/daemonEpoch";
import { ADDRESS_LABEL, CODE_LABEL, CODE_TRIED_OUT, PEER_EVENT_TYPES, RESTARTING } from "@/lib/otherComputers";

const SAVED = { saved: true, applied: false, addresses: [] as string[] };

const OFF = { saved: false, applied: false, addresses: [] as string[] };
const ON = { saved: true, applied: true, addresses: ["192.168.1.20:8787"] };

function view(over: Record<string, unknown> = {}) {
  return { this_name: "desk", peers: [], code: null, network: OFF, ...over };
}

beforeEach(() => {
  H.view = view();
  H.calls = [];
  H.tidy = "restarting";
  H.tidyCalls = 0;
  H.events = [];
  H.gets = 0;
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function card(epoch: number) {
  return (
    <DaemonEpochContext.Provider value={epoch}>
      <OtherComputers />
    </DaemonEpochContext.Provider>
  );
}

describe("after the restart the card asked for", () => {
  it("no longer says it is restarting once Iron Jarvis is back", async () => {
    const { rerender } = render(card(1));
    await screen.findByTestId("other-computers-network");
    fireEvent.click(screen.getByTestId("other-computers-network-on"));
    fireEvent.click(screen.getByTestId("other-computers-network-on"));
    await waitFor(() => expect(H.tidyCalls).toBe(1));
    expect(screen.getByTestId("other-computers-note")).toHaveTextContent(RESTARTING);
    // Iron Jarvis comes back with the network on: the daemon's epoch ticks.
    H.view = view({ network: ON });
    rerender(card(2));
    await waitFor(() =>
      expect(screen.getByTestId("other-computers-addresses")).toHaveTextContent("192.168.1.20:8787"),
    );
    expect(screen.getByTestId("other-computers-note")).not.toHaveTextContent(RESTARTING);
  });

  it("a refused restart keeps its own words through a reconnect", async () => {
    H.tidy = "refused";
    const { rerender } = render(card(1));
    await screen.findByTestId("other-computers-network");
    fireEvent.click(screen.getByTestId("other-computers-network-on"));
    fireEvent.click(screen.getByTestId("other-computers-network-on"));
    await waitFor(() => expect(H.tidyCalls).toBe(1));
    const said = screen.getByTestId("other-computers-note").textContent;
    expect(said).not.toBe("");
    rerender(card(2));
    await waitFor(() => expect(H.calls.length).toBeGreaterThan(0));
    expect(screen.getByTestId("other-computers-note").textContent).toBe(said);
  });
});

describe("Pair", () => {
  it("says why it is greyed out, and stops saying it once it can pair", async () => {
    render(card(1));
    await screen.findByTestId("other-computers-network");
    fireEvent.click(screen.getByTestId("other-computers-use-code"));
    const pair = await screen.findByTestId("other-computers-pair");
    expect(pair).toBeDisabled();
    const empty = pair.getAttribute("title") ?? "";
    expect(empty).toContain(ADDRESS_LABEL);
    expect(empty).toContain(CODE_LABEL);
    fireEvent.change(screen.getByTestId("other-computers-address"), { target: { value: "192.168.1.20" } });
    fireEvent.change(screen.getByTestId("other-computers-code-input"), { target: { value: "482 91" } });
    expect(pair).toBeDisabled();
    expect(pair.getAttribute("title") ?? "").toMatch(/six digits/);
    fireEvent.change(screen.getByTestId("other-computers-code-input"), { target: { value: "482 917" } });
    expect(pair).toBeEnabled();
    expect(pair.getAttribute("title")).toBeNull();
  });
});

describe("saved on, not restarted yet", () => {
  it("the hints ask for the restart, never to turn on what is already on", async () => {
    H.view = view({ network: SAVED });
    render(card(1));
    await screen.findByTestId("other-computers-network");
    const hint = screen.getByTestId("other-computers-needs-network").textContent ?? "";
    expect(hint).toMatch(/restart/i);
    expect(hint).not.toMatch(/turn on/i);
    expect(screen.getByTestId("other-computers-show-code").getAttribute("title") ?? "").toBe(hint);
    fireEvent.click(screen.getByTestId("other-computers-use-code"));
    const answers = (await screen.findByTestId("other-computers-no-answers")).textContent ?? "";
    expect(answers).toMatch(/restart/i);
    expect(answers).not.toMatch(/turn on/i);
  });

  it("with the setting off, the hints still say to turn it on", async () => {
    render(card(1));
    await screen.findByTestId("other-computers-network");
    expect(screen.getByTestId("other-computers-needs-network").textContent ?? "").toMatch(/turn on/i);
    fireEvent.click(screen.getByTestId("other-computers-use-code"));
    expect((await screen.findByTestId("other-computers-no-answers")).textContent ?? "").toMatch(/turn on/i);
  });
});

describe("a code typed wrong too often", () => {
  it("is taken off the screen and said, as soon as the other computer used up its tries", async () => {
    expect(PEER_EVENT_TYPES).toContain("peer.code_ended");
    H.view = view({ network: ON, code: { code: "482917", shown: "482 917", expires_at: "2099-01-01T00:00:00Z" } });
    const { rerender } = render(card(1));
    await screen.findByTestId("other-computers-code");
    const before = H.gets;
    // The daemon ended the code (five wrong tries) and says so.
    H.view = view({ network: ON });
    H.events = [{ id: "e1", type: "peer.code_ended" }];
    rerender(card(1));
    await waitFor(() => expect(H.gets).toBe(before + 1), { timeout: 3000 });
    await waitFor(() => expect(screen.queryByTestId("other-computers-code")).toBeNull());
    expect(screen.getByTestId("other-computers-note")).toHaveTextContent(CODE_TRIED_OUT);
  });
});
