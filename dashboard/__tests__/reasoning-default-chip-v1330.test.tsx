/**
 * v1.330.0 - the reasoning chip says the level the model will ACTUALLY use.
 *
 * With nothing picked the chip read "Reasoning", so the user could not tell
 * what level the model would run at. The daemon's catalog row now carries
 * `reasoning_default` (the vendor's documented default for that exact model,
 * "" = unknown). The chip says it in plain words; the menu's "send nothing"
 * row names it ("Default (Medium)"; v1.330.0 W11 leads with "Default" so it
 * never reads as a twin of the explicit "Medium" row); an unknown default keeps "Reasoning" and the
 * tooltip says the model decides. What is SENT is unchanged: a pick sends that
 * level, the default row sends nothing. Header, mocks and helpers are the
 * reasoning-level (v1.263.0) harness verbatim.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
      posts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: { bodies: [] as Record<string, unknown>[] },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    if (typeof r === "function") return (r as (b: Record<string, unknown>) => unknown)(body);
    return r ?? {};
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
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
    run: async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      H.stream.bodies.push(body);
      await new Promise<void>((r) => setTimeout(r, 0));
      onDelta("done", "done");
      return { reply: "done" };
    },
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
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
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import {
  defaultWords,
  reasoningChipLabel,
  reasoningDefaultOf,
  reasoningDefaultOption,
  reasoningTitle,
} from "@/lib/reasoningChip";

const LEVELS = ["low", "medium", "high"];
const CATALOG = {
  models: [
    { provider: "openai", model: "gpt-5", available: true, kind: "api", reasoning: LEVELS, reasoning_default: "medium" },
    { provider: "openai", model: "gpt-5.1", available: true, kind: "api", reasoning: LEVELS, reasoning_default: "off" },
    { provider: "openai", model: "gpt-5.9-auto", available: true, kind: "api", reasoning: LEVELS, reasoning_default: "auto" },
    { provider: "openai", model: "gpt-5-codex", available: true, kind: "api", reasoning: LEVELS, reasoning_default: "" },
    // An older daemon: no field at all.
    { provider: "openai", model: "o3", available: true, kind: "api", reasoning: LEVELS },
    // A word this build has no label for reads as unknown, never as made up.
    { provider: "openai", model: "gpt-7", available: true, kind: "api", reasoning: LEVELS, reasoning_default: "extreme" },
  ],
};

beforeEach(() => {
  H.api.posts.length = 0;
  H.stream.bodies.length = 0;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = {
    "/models": CATALOG,
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
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function pickModel(model: string) {
  await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.click(await screen.findByTitle("Switch model"));
  const providerRow = (await screen.findAllByText(/^openai$/i))
    .map((el) => el.closest("button"))
    .find((b) => b?.hasAttribute("aria-expanded")) as HTMLButtonElement;
  fireEvent.click(providerRow);
  const modelRow = (await screen.findByText(model)).closest("button") as HTMLButtonElement;
  fireEvent.click(modelRow);
}

async function send(text: string) {
  const box = await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.change(box, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(H.stream.bodies.length).toBeGreaterThan(0));
  return H.stream.bodies[H.stream.bodies.length - 1];
}

function chipWords() {
  return screen.getByTestId("reasoning-chip-words").textContent;
}

/** Pick a model and wait until the chip shows the words asserted. */
async function chipFor(model: string, words: string) {
  await pickModel(model);
  await waitFor(() => expect(chipWords()).toBe(words));
  return screen.getByTestId("reasoning-level") as HTMLSelectElement;
}

describe("the words, one table (lib/reasoningChip)", () => {
  it.each([
    // [picked, default, chip, default row]
    ["", "medium", "Medium", "Default (Medium)"],
    ["", "low", "Low", "Default (Low)"],
    ["", "high", "High", "Default (High)"],
    ["", "off", "Thinking off", "Default (Thinking off)"],
    ["", "auto", "Auto", "Default (Auto)"],
    ["", "", "Reasoning", "Default"],
    ["high", "medium", "High", "Default (Medium)"],
    ["low", "off", "Low", "Default (Thinking off)"],
    ["medium", "", "Medium", "Default"],
  ])("picked %j, default %j: chip %j, row %j", (picked, def, chip, row) => {
    expect(reasoningChipLabel(picked, def)).toBe(chip);
    expect(reasoningDefaultOption(def)).toBe(row);
  });

  it("reads only the words it knows; anything else is unknown", () => {
    expect(reasoningDefaultOf({ reasoning_default: "Medium" })).toBe("medium");
    expect(reasoningDefaultOf({ reasoning_default: "off" })).toBe("off");
    expect(reasoningDefaultOf({ reasoning_default: "extreme" })).toBe("");
    expect(reasoningDefaultOf({ reasoning_default: 3 })).toBe("");
    expect(reasoningDefaultOf({})).toBe("");
    expect(reasoningDefaultOf(undefined)).toBe("");
    expect(defaultWords("none")).toBe("");
  });

  it("the tooltip names the default, or says plainly the model decides", () => {
    expect(reasoningTitle("medium")).toContain("runs at medium, its own default");
    expect(reasoningTitle("off")).toContain("does not think first");
    expect(reasoningTitle("auto")).toContain("decides for itself how much to think");
    expect(reasoningTitle("")).toMatch(/With nothing picked the model decides\.$/);
  });
});

describe("the chip on the page says the level the model will use", () => {
  it("a documented default: the chip says it, the menu marks it, nothing extra is sent", async () => {
    render(<ChatPage />);
    const select = await chipFor("gpt-5", "Medium");
    expect(select.value).toBe("");
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["", "low", "medium", "high"]);
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
      "Default (Medium)",
      "Low",
      "Medium",
      "High",
    ]);
    expect(select.getAttribute("title")).toContain("runs at medium, its own default");
    // The default row sends NOTHING: byte-identical to before the field.
    const body = await send("hello");
    expect("reasoning" in body).toBe(false);
    expect(body.model).toBe("gpt-5");
  });

  it("a pick still sends exactly that level, and the chip says the pick", async () => {
    render(<ChatPage />);
    const select = await chipFor("gpt-5", "Medium");
    fireEvent.change(select, { target: { value: "high" } });
    await waitFor(() => expect(chipWords()).toBe("High"));
    const body = await send("think hard");
    expect(body.reasoning).toBe("high");
  });

  it("picking the default row again sends nothing", async () => {
    render(<ChatPage />);
    const select = await chipFor("gpt-5", "Medium");
    fireEvent.change(select, { target: { value: "low" } });
    await waitFor(() => expect(chipWords()).toBe("Low"));
    fireEvent.change(select, { target: { value: "" } });
    await waitFor(() => expect(chipWords()).toBe("Medium"));
    const body = await send("hello");
    expect("reasoning" in body).toBe(false);
  });

  it("a model that does not think unless asked reads 'Thinking off'", async () => {
    render(<ChatPage />);
    const select = await chipFor("gpt-5.1", "Thinking off");
    expect(select.options[0].textContent).toBe("Default (Thinking off)");
    expect(select.getAttribute("title")).toContain("does not think first");
    // The menu still offers the three levels.
    expect(Array.from(select.options).slice(1).map((o) => o.value)).toEqual(LEVELS);
  });

  it("a model that decides for itself reads 'Auto'", async () => {
    render(<ChatPage />);
    const select = await chipFor("gpt-5.9-auto", "Auto");
    expect(select.options[0].textContent).toBe("Default (Auto)");
  });

  it.each(["gpt-5-codex", "o3", "gpt-7"])(
    "an unknown default (%s) keeps 'Reasoning' and says the model decides",
    async (model) => {
      render(<ChatPage />);
      const select = await chipFor(model, "Reasoning");
      expect(select.options[0].textContent).toBe("Default");
      expect(select.getAttribute("title")).toBe(
        "How hard the model thinks before answering. Higher is slower and costs more. With nothing picked the model decides.",
      );
      const body = await send("hello");
      expect("reasoning" in body).toBe(false);
    },
  );

  it("the chip words are hidden from a screen reader; the select carries the name and value", async () => {
    render(<ChatPage />);
    const select = await chipFor("gpt-5", "Medium");
    expect(screen.getByTestId("reasoning-chip-words").getAttribute("aria-hidden")).toBe("true");
    expect(screen.getByRole("combobox", { name: "Reasoning level" })).toBe(select);
  });
});
