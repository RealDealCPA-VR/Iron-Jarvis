/**
 * v1.459.0 — two of the re-audit's open items (docs/PRODUCT-READINESS-RESULTS.md 0k).
 *
 * 1. A READ made through POST raised no banner. Wave F (v1.455.0) kept the
 *    "Part of this page didn't load" banner for GET/HEAD only, so a page read
 *    whose question is a body ("what did this reply change?", the Changes
 *    pane) failed silently on a server error. `api(…, {pageRead: true})` /
 *    `post(…, …, {pageRead: true})` marks such a read; ChangedFiles uses it.
 *    A press still never raises it.
 * 2. The web-service "Config (JSON)" box opened on "{}", so a Save dropped the
 *    saved address and how the key is sent. It now opens on what is saved
 *    (GET /integrations rows carry `config`) and says the sign-in settings are
 *    kept unless changed (the daemon keeps them too: routes/comm
 *    `merged_integration_config`).
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { api, onRequestErrorChange, post } from "@/lib/api";
import { CONFIG_KEPT_WORDS, RestHookups, savedConfigText } from "@/components/connections/RestHookups";
import type { Integration } from "@/lib/types";

const useApiState = vi.hoisted(() => ({ data: null as unknown }));
vi.mock("@/lib/useApi", () => ({
  useApi: () => ({ data: useApiState.data, error: null, loading: false, reload: () => {} }),
}));

function mockResponse(status: number, body: unknown = {}): Response {
  return { ok: status >= 200 && status < 300, status, statusText: "", json: async () => body } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("a read made through POST raises the banner; a press does not", () => {
  it("pageRead marks a POST as a read", async () => {
    let failing = false;
    const off = onRequestErrorChange((v) => (failing = v));
    vi.stubGlobal("fetch", vi.fn(async () => mockResponse(500, { detail: "internal error [err_00ff00ff]: boom" })));
    await post("/chat/changes", { since: "x" }).catch(() => {});
    expect(failing).toBe(false);
    await post("/chat/changes", { since: "x" }, { pageRead: true }).catch(() => {});
    expect(failing).toBe(true);
    off();
  });

  it("a 4xx on a POST read is the page's own business (no banner)", async () => {
    let failing = false;
    const off = onRequestErrorChange((v) => (failing = v));
    vi.stubGlobal("fetch", vi.fn(async () => mockResponse(422, { detail: "bad since" })));
    await api("/chat/changes", { method: "POST", pageRead: true }).catch(() => {});
    expect(failing).toBe(false);
    off();
  });

  it("pageRead never reaches fetch as an option", async () => {
    const f = vi.fn(async () => mockResponse(200, {}));
    vi.stubGlobal("fetch", f);
    await post("/chat/changes", { since: "x" }, { pageRead: true });
    const init = (f.mock.calls[0] as unknown as [string, Record<string, unknown>])[1];
    expect("pageRead" in init).toBe(false);
  });
});

describe("the web-service Config box opens on what is saved", () => {
  const saved: Integration = {
    id: "acme_crm",
    kind: "rest",
    display_name: "Acme CRM",
    enabled: true,
    configured: true,
    required_secrets: [],
    config: { base_url: "https://api.acme.test", auth_kind: "header", auth_header: "X-API-Key" },
  };

  it("savedConfigText shows the saved settings, else empty braces", () => {
    expect(JSON.parse(savedConfigText(saved.config))).toEqual(saved.config);
    expect(savedConfigText(undefined).replace(/\s/g, "")).toBe("{}");
    expect(savedConfigText({}).replace(/\s/g, "")).toBe("{}");
  });

  it("Configure opens the box with the saved address and the key's way, and says they are kept", () => {
    useApiState.data = { integrations: [saved] };
    render(<RestHookups />);
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));
    const box = screen.getByLabelText("Config (JSON)") as HTMLTextAreaElement;
    expect(JSON.parse(box.value)).toEqual(saved.config);
    expect(screen.getByTestId("integ-config-kept-acme_crm").textContent).toBe(CONFIG_KEPT_WORDS);
  });
});
