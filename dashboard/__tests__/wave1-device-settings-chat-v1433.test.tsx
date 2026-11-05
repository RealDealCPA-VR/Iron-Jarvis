/**
 * v1.433.0 (Wave 1 B, decision D1): the chat page follows this device's
 * "Chat persona" and "Auto tools in chat" rows, and its own controls save
 * them.
 *
 * The rows are read by lib/deviceSettings (the layout's bridge calls
 * `refreshDeviceSettings` on load and on a `config.changed` naming a
 * `device.*` key — wave1-device-settings-v1433 drives that half). Here the
 * page half: a changed row reaches the open page live (Auto tools at once;
 * the persona for a NEW chat, while an open conversation keeps its own),
 * and a press on the Auto tools switch or the persona picker PUTs the row.
 *
 * Harness: the composer-card-v1326 mocks (ChatPage rendered for real; an
 * unmocked GET answers {}).
 */

import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    api: {
      gets: [] as string[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      responses: {} as Record<string, unknown>,
    },
    device: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (path: string) => {
    H.api.gets.push(path);
    if (path.startsWith("/settings/device")) return { device_id: "dev_x", values: { ...H.device } };
    const r = H.api.responses[path];
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async () => ({}),
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: () => Promise.resolve({ reply: "Noted.", tools_used: [] }),
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: true, reason: null, engine: null, listening: false, processing: false,
    transcript: "", interim: "", error: null, start: () => {}, stop: () => {}, reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({
    byProvider: {}, signedOutByProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: ReactNode; href: string } & Record<string, unknown>) => (
    <a href={href} {...(rest as Record<string, never>)}>
      {children}
    </a>
  ),
}));
vi.mock("@/components/project/ProjectSurfaces", () => ({
  ProjectSurface: ({ view }: { view: string }) => <div data-testid="surface-stub">surface:{view}</div>,
}));

import ChatPage from "@/app/chat/page";
import { refreshDeviceSettings } from "@/lib/deviceSettings";

const AT = "2026-10-01T10:00:00Z";
const LEDGER = {
  id: "ta",
  title: "Ledger chat",
  updated_at: AT,
  persona: "assistant",
  messages: [
    { role: "user", content: "read the ledger" },
    { role: "assistant", content: "The ledger is read." },
  ],
};

const UNSET = {
  "device.theme": null,
  "device.approval_mode": null,
  "device.chat_persona": null,
  "device.auto_tools": null,
  "device.read_aloud": null,
};

const box = async () => (await screen.findByRole("textbox", { name: "Message" })) as HTMLTextAreaElement;
const toolbar = () => screen.getByTestId("composer-toolbar");
const devicePuts = () => H.api.puts.filter((p) => p.path === "/settings/device");

async function openMoreMenu(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole("button", { name: "More chat options" }));
  return await screen.findByRole("group", { name: "Chat options" });
}

async function personaSelect(): Promise<HTMLSelectElement> {
  const panel = await openMoreMenu();
  return (await within(panel).findByRole("combobox", { name: "Persona" })) as HTMLSelectElement;
}

/** Apply this device's rows as the layout's bridge does. */
async function deviceRowsChanged(values: Record<string, unknown>) {
  H.device = { ...UNSET, ...values };
  await act(async () => {
    await refreshDeviceSettings();
  });
}

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.puts.length = 0;
  H.device = { ...UNSET };
  H.api.responses = {
    "/models": { models: [] },
    "/chat/personas": {
      personas: [
        { name: "assistant", title: "Assistant" },
        { name: "analyst", title: "Analyst" },
      ],
    },
    "/chat/threads": { threads: [] },
    "/chat/threads/ta": LEDGER,
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = function scrollIntoView() {} as unknown as Element["scrollIntoView"];
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Auto tools in chat", () => {
  it("a chat change turns the switch off on the open page, live", async () => {
    render(<ChatPage />);
    await box();
    const chip = within(toolbar()).getByRole("button", { name: /^Tools: / });
    expect(chip.textContent).toContain("Auto tools");
    await deviceRowsChanged({ "device.auto_tools": false });
    await waitFor(() => expect(chip.textContent).toContain("Tools off"));
    expect(window.localStorage.getItem("ij_chat_auto_tools")).toBe("0");
    // Applied from the row: nothing is written back.
    expect(devicePuts()).toEqual([]);
    await deviceRowsChanged({ "device.auto_tools": true });
    await waitFor(() => expect(chip.textContent).toContain("Auto tools"));
  });

  it("pressing the switch saves this device's row", async () => {
    render(<ChatPage />);
    await box();
    const chip = within(toolbar()).getByRole("button", { name: /^Tools: / });
    fireEvent.click(chip);
    const menu = await screen.findByTestId("composer-tools-menu");
    fireEvent.click(within(menu).getByRole("switch", { name: /Auto tools/ }));
    await waitFor(() => expect(chip.textContent).toContain("Tools off"));
    await waitFor(() =>
      expect(devicePuts().map((p) => (p.body as { values?: unknown }).values)).toEqual([
        { "device.auto_tools": false },
      ]),
    );
  });
});

describe("Chat persona", () => {
  it("a new chat starts with the device's persona, live", async () => {
    render(<ChatPage />);
    await box();
    const sel = await personaSelect();
    expect(sel.value).toBe("assistant");
    await deviceRowsChanged({ "device.chat_persona": "analyst" });
    await waitFor(() => expect(sel.value).toBe("analyst"));
    expect(window.localStorage.getItem("ij_chat_persona")).toBe("analyst");
    expect(devicePuts()).toEqual([]);
  });

  it("an open conversation keeps its own persona; the stored default moves for the next new chat", async () => {
    window.history.replaceState({}, "", "/chat?thread=ta");
    render(<ChatPage />);
    await screen.findByText("The ledger is read.");
    await deviceRowsChanged({ "device.chat_persona": "analyst" });
    expect(window.localStorage.getItem("ij_chat_persona")).toBe("analyst");
    const sel = await personaSelect();
    expect(sel.value).toBe("assistant");
  });

  it("picking a persona saves this device's row", async () => {
    render(<ChatPage />);
    await box();
    const sel = await personaSelect();
    fireEvent.change(sel, { target: { value: "analyst" } });
    await waitFor(() =>
      expect(devicePuts().map((p) => (p.body as { values?: unknown }).values)).toEqual([
        { "device.chat_persona": "analyst" },
      ]),
    );
    expect(window.localStorage.getItem("ij_chat_persona")).toBe("analyst");
  });
});
