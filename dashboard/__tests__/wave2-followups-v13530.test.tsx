/**
 * v1.353.0 integrate — three follow-ups from Wave 2, pinned through the real
 * chat page.
 *
 *  1. LOCAL ONLY ASKS UP FRONT FOR AN OFF-NETWORK ENDPOINT. A model server the
 *     user added as an endpoint is a "local"-class provider for routing, but
 *     when its address is a cloud server the daemon's `egress.
 *     stays_on_this_network` says it leaves the network and refuses it in a
 *     local-only chat. `GET /models` rows carry that answer as
 *     `on_this_network`; lib/localOnly now reads it, so the ask-first card
 *     shows BEFORE the send instead of a refusal after it. A row from an
 *     older daemon (no flag) falls back to the class, as before. The
 *     regenerate menu's privacy line reads the same answer.
 *  2. ONE DIAL FOR "APPROVALS IN CHAT". Settings › Permissions'
 *     `device.approval_mode` row was never read by the chat page. Now the
 *     page reads it once per visit (lib/approvalDefault) and a new chat
 *     starts from it; a press on the composer's permission chip writes it
 *     back. Not set changes nothing; a press made before the read lands wins.
 *  3. (BrowserPage's queue card words: browser-approval-copy-v13530.)
 *
 * Harness: the local-only-chat-v1332 mocks (transport hooks at the usual
 * seams; `stream.run` records each body and answers "Noted.").
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    body?: unknown;
    constructor(message: string, status = 500, _cancelled = false, body?: unknown) {
      super(message);
      this.status = status;
      this.name = "ApiError";
      if (body !== undefined) this.body = body;
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
    code = "";
    provider = "";
    model = "";
  }
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      gets: [] as string[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      posts: [] as { path: string; body: Record<string, unknown> }[],
      responses: {} as Record<string, unknown>,
    },
    run: { bodies: [] as Record<string, unknown>[] },
    health: { byProvider: {} as Record<string, boolean>, defaultProvider: "" },
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
    const r = H.api.responses[path];
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    return {};
  },
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
    run: async (body: Record<string, unknown>) => {
      H.run.bodies.push(body);
      return { reply: "Noted.", tools_used: [] };
    },
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false, reason: null, engine: null, listening: false, processing: false,
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
  useProviderHealth: () => ({ ...H.health, loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { cloudAskFor, staysOnThisNetwork } from "@/lib/localOnly";
import { DEVICE_APPROVAL_KEY, deviceApprovalFrom } from "@/lib/approvalDefault";
import { PERMISSION_MODES } from "@/lib/permissionLevels";
import { privacyLine, whereItRuns } from "@/components/chat/RegenerateMenu";
import type { ModelOption } from "@/lib/types";

/* ------------------------------------------------------------- fixtures */

/** A fleet node on the user's LAN: local, and the daemon says so. */
const LAN = {
  provider: "fleet-lan01",
  model: "lan-llama-8b",
  name: "Office box",
  kind: "local",
  on_this_network: true,
  available: true,
};
/** An endpoint the user pointed at a CLOUD server: the class says "local",
 *  the daemon's answer says it leaves the network. */
const OFFNET = {
  provider: "fleet-cloud01",
  model: "remote-gpt-large",
  name: "Rented server",
  kind: "local",
  on_this_network: false,
  available: true,
};
/** The same endpoint as an OLDER daemon lists it (no on_this_network). */
const OLD_LOCAL = {
  provider: "fleet-old01",
  model: "old-qwen-7b",
  name: "Old box",
  kind: "local",
  available: true,
};

const KEY = "ij_chat_approval_mode";
const DEVICE = "dev_followups01";
const DEVICE_PATH = `/settings/device?device_id=${DEVICE}`;

async function box(): Promise<HTMLTextAreaElement> {
  return (await screen.findByRole("textbox", { name: "Message" })) as HTMLTextAreaElement;
}

async function localOnlyOn(): Promise<void> {
  await box();
  fireEvent.click(await screen.findByTestId("local-only-chip"));
}

async function pickModel(words: string): Promise<void> {
  fireEvent.click(await screen.findByTitle("Switch model"));
  const filter = (await screen.findByTestId("model-filter")) as HTMLInputElement;
  fireEvent.change(filter, { target: { value: words } });
  fireEvent.keyDown(filter, { key: "Enter" });
  await waitFor(() => expect(screen.queryByTestId("model-filter")).toBeNull());
}

async function type(text: string): Promise<void> {
  const b = await box();
  fireEvent.change(b, { target: { value: text } });
  fireEvent.keyDown(b, { key: "Enter" });
}

async function permissionChip(): Promise<HTMLButtonElement> {
  await box();
  const el = document.getElementById("chat-approval-mode");
  expect(el, "the composer has no #chat-approval-mode").not.toBeNull();
  return el as HTMLButtonElement;
}

async function pickLevel(mode: string): Promise<void> {
  fireEvent.click(await permissionChip());
  const menu = screen.getByTestId("permission-menu");
  const item = within(menu)
    .getAllByTestId("permission-item")
    .find((i) => i.getAttribute("data-mode") === mode);
  expect(item, `no "${mode}" level in the menu`).toBeTruthy();
  fireEvent.click(item!);
}

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.puts.length = 0;
  H.api.posts.length = 0;
  H.run.bodies.length = 0;
  H.health = { byProvider: {}, defaultProvider: "" };
  H.api.responses = {
    "/models": { models: [LAN, OFFNET, OLD_LOCAL] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
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
  window.localStorage.setItem("ij_device_id", DEVICE);
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/* ======================================== 1. local only: on_this_network */

describe("1. a local-only chat asks up front for an endpoint the daemon says leaves the network", () => {
  it("an off-network endpoint: the ask-first card shows and NOTHING is sent", async () => {
    render(<ChatPage />);
    await localOnlyOn();
    await pickModel("remote-gpt");
    await type("summarize the payroll file");
    const card = await screen.findByTestId("cloud-ask-card");
    expect(within(card).getByText("This chat is local only")).toBeTruthy();
    expect(screen.getByTestId("cloud-ask-body").textContent).toMatch(/It will leave this PC\.$/);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(H.run.bodies).toHaveLength(0);
  });

  it("CONTROL: an endpoint on the user's network sends at once", async () => {
    render(<ChatPage />);
    await localOnlyOn();
    await pickModel("lan-llama");
    await type("tidy my notes");
    await waitFor(() => expect(H.run.bodies).toHaveLength(1));
    expect(H.run.bodies[0].provider).toBe("fleet-lan01");
    expect(H.run.bodies[0].local_only).toBe(true);
    expect("allow_cloud_once" in H.run.bodies[0]).toBe(false);
    expect(screen.queryByTestId("cloud-ask-card")).toBeNull();
  });

  it("CONTROL: an older daemon's row (no flag) falls back to its class", async () => {
    render(<ChatPage />);
    await localOnlyOn();
    await pickModel("old-qwen");
    await type("tidy my notes");
    await waitFor(() => expect(H.run.bodies).toHaveLength(1));
    expect(H.run.bodies[0].provider).toBe("fleet-old01");
    expect(screen.queryByTestId("cloud-ask-card")).toBeNull();
  });

  it("the helper: the daemon's answer decides; absent → the class; unlisted → asks", () => {
    expect(staysOnThisNetwork(LAN)).toBe(true);
    expect(staysOnThisNetwork(OFFNET)).toBe(false);
    expect(staysOnThisNetwork(OLD_LOCAL)).toBe(true);
    expect(staysOnThisNetwork(undefined)).toBe(false);
    // A cli row the daemon says leaves the network still asks.
    expect(staysOnThisNetwork({ provider: "claude-cli", model: "x", kind: "cli", on_this_network: false })).toBe(false);
    const rows = [LAN, OFFNET, OLD_LOCAL];
    expect(cloudAskFor("fleet-cloud01", "remote-gpt-large", rows)).toEqual({
      provider: "fleet-cloud01",
      model: "remote-gpt-large",
      name: expect.any(String),
    });
    expect(cloudAskFor("fleet-lan01", "lan-llama-8b", rows)).toBeNull();
    expect(cloudAskFor("fleet-old01", "old-qwen-7b", rows)).toBeNull();
  });

  it("the regenerate menu's privacy line reads the same answer", () => {
    const off = OFFNET as unknown as ModelOption;
    const lan = LAN as unknown as ModelOption;
    expect(whereItRuns(off)).toBe("api");
    expect(whereItRuns(lan)).toBe("local");
    expect(whereItRuns(OLD_LOCAL as unknown as ModelOption)).toBe("local");
    // After a local reply, trying again on the off-network endpoint says it leaves.
    expect(privacyLine(off, true)).toMatch(/it leaves this computer/);
    expect(privacyLine(lan, true)).toBe("");
  });
});

/* ======================================== 2. one dial: Approvals in chat */

describe("2. Settings' 'Approvals in chat' and the composer's permission chip are one dial", () => {
  it("the chat reads the device setting once and a new chat starts from it", async () => {
    window.localStorage.setItem(KEY, "approve_for_me");
    H.api.responses[DEVICE_PATH] = { device_id: DEVICE, values: { [DEVICE_APPROVAL_KEY]: "always_ask" } };
    render(<ChatPage />);
    const chip = await permissionChip();
    await waitFor(() => expect(chip.getAttribute("data-mode")).toBe("always_ask"));
    // It is the default now, for the next new chat too.
    expect(window.localStorage.getItem(KEY)).toBe("always_ask");
    expect(H.api.gets.filter((p) => p === DEVICE_PATH)).toHaveLength(1);
  });

  it("CONTROL: a setting never chosen ('not set') leaves the chip's own default", async () => {
    window.localStorage.setItem(KEY, "always_ask");
    H.api.responses[DEVICE_PATH] = { device_id: DEVICE, values: { [DEVICE_APPROVAL_KEY]: null } };
    render(<ChatPage />);
    const chip = await permissionChip();
    await waitFor(() => expect(H.api.gets).toContain(DEVICE_PATH));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 10));
    });
    expect(chip.getAttribute("data-mode")).toBe("always_ask");
    expect(window.localStorage.getItem(KEY)).toBe("always_ask");
  });

  it("a press on the chip saves the same setting (PUT /settings/device)", async () => {
    render(<ChatPage />);
    await pickLevel("yolo");
    await waitFor(() =>
      expect(H.api.puts.filter((p) => p.path === "/settings/device")).toEqual([
        { path: "/settings/device", body: { device_id: DEVICE, values: { [DEVICE_APPROVAL_KEY]: "yolo" } } },
      ]),
    );
    expect((await permissionChip()).getAttribute("data-mode")).toBe("yolo");
  });

  it("a press made before the read lands wins for this conversation", async () => {
    let land: (v: unknown) => void = () => {};
    H.api.responses[DEVICE_PATH] = new Promise((ok) => {
      land = ok;
    });
    render(<ChatPage />);
    await pickLevel("always_ask");
    const chip = await permissionChip();
    expect(chip.getAttribute("data-mode")).toBe("always_ask");
    await act(async () => {
      land({ device_id: DEVICE, values: { [DEVICE_APPROVAL_KEY]: "yolo" } });
      await new Promise((r) => setTimeout(r, 10));
    });
    expect(chip.getAttribute("data-mode")).toBe("always_ask");
  });

  it("the decoder takes only the three levels the chip knows", () => {
    for (const m of PERMISSION_MODES) {
      expect(deviceApprovalFrom({ values: { [DEVICE_APPROVAL_KEY]: m } })).toBe(m);
    }
    expect(deviceApprovalFrom({ values: { [DEVICE_APPROVAL_KEY]: "auto-approve" } })).toBeNull();
    expect(deviceApprovalFrom({ values: {} })).toBeNull();
    expect(deviceApprovalFrom(null)).toBeNull();
  });
});
