/**
 * v1.393.0 REVIEW — the Iron-Proxy card's Group choice.
 *
 * A pick saves the WHOLE setting (`PUT /settings/values {account_groups}`),
 * computed from the groups the card holds. Those used to be the last answer
 * of GET /iron-proxy alone, so a second pick made before the re-read landed
 * was computed from groups that did not have the first one yet — and saved
 * over it (the first account silently left its group, and its select snapped
 * back to the old group until the re-read). Now the card holds what it saved
 * until a newer answer arrives.
 *
 * The daemon is a small stand-in: PUT changes its groups; GET answers them,
 * held back by a gate the test opens (a re-read still in flight).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const server = vi.hoisted(() => ({
  groups: {} as Record<string, string[]>,
  puts: [] as unknown[],
  gets: 0,
  gate: null as Promise<void> | null,
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    cancelled = false;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  const snapshot = () => ({
    status: { enabled: true, running: true, owned: true, url: "", version: "0.6.0", error: null, bundled: true },
    accounts: [
      { id: "tsi", title: "TSI", provider: "anthropic", lane: "cli", order: 0, enabled: true, state: { status: "ready" } },
      { id: "home", title: "Home Claude", provider: "anthropic", lane: "cli", order: 1, enabled: true, state: { status: "ready" } },
      { id: "cx", title: "Work Codex", provider: "openai", lane: "cli", order: 0, enabled: true, state: { status: "ready" } },
    ],
    discovered: [],
    providers_used_by_jarvis: { anthropic: "claude-cli", openai: "codex-cli" },
    account_groups: JSON.parse(JSON.stringify(server.groups)),
  });
  return {
    ApiError,
    API_BASE: "http://127.0.0.1:8787",
    ijToken: () => null,
    get: async (p: string) => {
      if (p !== "/iron-proxy" && p !== "/iron-proxy?discover=0") return {};
      server.gets += 1;
      if (server.gate) await server.gate;
      return snapshot();
    },
    put: async (p: string, b: { values: { account_groups: Record<string, string[]> } }) => {
      server.puts.push(b);
      if (p === "/settings/values") server.groups = b.values.account_groups;
      return {};
    },
    post: async () => ({}),
    patch: async () => ({}),
    del: async () => ({}),
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/connections",
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null }));

import { IronProxyCard } from "@/components/connections/IronProxyCard";

const select = (id: string) => screen.getByTestId(`iron-proxy-group-${id}`) as HTMLSelectElement;

beforeEach(() => {
  server.groups = { Work: ["tsi"] };
  server.puts = [];
  server.gets = 0;
  server.gate = null;
});
afterEach(() => cleanup());

describe("two picks in a row", () => {
  it("the second pick keeps the first — even while the re-read is still on its way", async () => {
    render(<IronProxyCard />);
    await waitFor(() => expect(select("home")).toBeInTheDocument());
    let open!: () => void;
    server.gate = new Promise<void>((r) => {
      open = r;
    });
    fireEvent.change(select("home"), { target: { value: "Work" } });
    await waitFor(() => expect(server.puts).toHaveLength(1));
    // The saved pick shows at once (not the stale answer's "No group").
    await waitFor(() => expect(select("home").value).toBe("Work"));
    await waitFor(() => expect(select("cx").disabled).toBe(false));
    fireEvent.change(select("cx"), { target: { value: "Work" } });
    await waitFor(() => expect(server.puts).toHaveLength(2));
    expect(server.puts[1]).toEqual({ values: { account_groups: { Work: ["tsi", "home", "cx"] } } });
    await act(async () => {
      open();
      server.gate = null;
    });
    await waitFor(() => expect(select("cx").value).toBe("Work"));
    expect(select("home").value).toBe("Work");
  });

  it("what it saved gives way to a NEWER answer (a change made in Settings or another window)", async () => {
    render(<IronProxyCard />);
    await waitFor(() => expect(select("home")).toBeInTheDocument());
    fireEvent.change(select("home"), { target: { value: "Work" } });
    await waitFor(() => expect(server.puts).toHaveLength(1));
    const after = server.gets;
    await waitFor(() => expect(server.gets).toBeGreaterThan(after));
    // Changed elsewhere after this card's save; the card's next read says so.
    server.groups = { Home: ["home"] };
    await waitFor(() => expect(select("home").value).toBe("Home"), { timeout: 9000 });
  });
});

describe("the keyboard", () => {
  it("Escape closes the new group's name and gives the keyboard back to the Group choice", async () => {
    render(<IronProxyCard />);
    await waitFor(() => expect(select("home")).toBeInTheDocument());
    fireEvent.change(select("home"), { target: { value: "__new__" } });
    const input = await screen.findByTestId("iron-proxy-group-name-home");
    expect(document.activeElement).toBe(input);
    fireEvent.keyDown(input, { key: "Escape" });
    await waitFor(() => expect(document.activeElement).toBe(select("home")));
    expect(server.puts).toHaveLength(0);
  });

  it("Cancel and Save give it back too (Save once the save is over)", async () => {
    render(<IronProxyCard />);
    await waitFor(() => expect(select("home")).toBeInTheDocument());
    fireEvent.change(select("home"), { target: { value: "__new__" } });
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(document.activeElement).toBe(select("home")));
    fireEvent.change(select("home"), { target: { value: "__new__" } });
    const input = await screen.findByTestId("iron-proxy-group-name-home");
    fireEvent.change(input, { target: { value: "Home" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(server.puts).toHaveLength(1));
    await waitFor(() => expect(document.activeElement).toBe(select("home")));
    expect(select("home").value).toBe("Home");
  });
});
