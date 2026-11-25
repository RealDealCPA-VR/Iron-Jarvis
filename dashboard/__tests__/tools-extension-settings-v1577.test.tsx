/**
 * v1.577.0: an extension's own settings on the Tools page — who asks before
 * its tools run, trust its new tools, tools always included, start when
 * first used. Save sends PUT /mcp/servers/{name}/policy with ONLY what
 * changed; nothing changed = nothing sent; a locked extension cannot change
 * who asks.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({
  puts: [] as { path: string; body: unknown }[],
}));

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  put: async (path: string, body?: unknown) => {
    api.puts.push({ path, body });
    return { policy: body };
  },
}));

import { ExtensionSettings, policyPatch } from "@/components/tools/ExtensionSettings";

afterEach(() => {
  cleanup();
  api.puts.length = 0;
});

describe("policyPatch", () => {
  it("sends only what changed", () => {
    expect(policyPatch({ approval: "", pinned_tools: ["a"] }, { approval: "", pinned_tools: ["a"] })).toEqual({});
    expect(policyPatch({}, { approval: "server" })).toEqual({ approval: "server" });
    expect(policyPatch({ pinned_tools: ["a", "b"] }, { pinned_tools: ["b", "a"] })).toEqual({});
    expect(policyPatch({ trust_all: true }, { trust_all: false })).toEqual({ trust_all: false });
  });
});

describe("ExtensionSettings", () => {
  it("saves the mode and a pinned tool in one PUT", async () => {
    render(
      <ExtensionSettings name="books" policy={{ approval: "" }} toolNames={["enter_bill", "record_payment"]} />,
    );
    const save = screen.getByTestId("extension-settings-save-books") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("extension-approval-books"), { target: { value: "server" } });
    fireEvent.click(screen.getByTestId("extension-pin-books-record_payment"));
    expect(screen.getByText(/does not ask as well/)).toBeTruthy();
    fireEvent.click(save);
    await waitFor(() => expect(api.puts).toHaveLength(1));
    expect(api.puts[0]).toEqual({
      path: "/mcp/servers/books/policy",
      body: { approval: "server", pinned_tools: ["record_payment"] },
    });
    await waitFor(() => expect(screen.getByText(/applies to the next use/)).toBeTruthy());
  });

  it("a locked extension cannot change who asks or trust everything", () => {
    render(<ExtensionSettings name="m365" policy={{}} toolNames={[]} locked />);
    expect((screen.getByTestId("extension-approval-m365") as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByTestId("extension-trust-m365") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(/asks before each use/)).toBeTruthy();
  });
});
