/**
 * v1.324.0 — apps that talk back (MCP elicitation, sampling, progress,
 * prompts), the dashboard half:
 *
 *  - the four stream frames decode with a WHITELIST (lib/mcpInteract) and the
 *    REAL useChatStream hook folds them: asks into `mcpAsks` (pending, then
 *    resolved with an outcome; still-open asks become "stopped" when the turn
 *    ends), progress onto the matching tool card only;
 *  - ElicitationCard: one control per field type, the server's checks run
 *    before sending, server field errors land under their field, keys never
 *    bubble, outcome words after answering;
 *  - SamplingCard: the words, the folded request, both buttons;
 *  - PackPromptForm: required args, insert, the flagged warning, errors;
 *  - the fetchers, with `@/lib/api` mocked (and `fetch` for the one raw call).
 *
 * The stream tests drive the REAL hook over a fake fetch body fed frame by
 * frame (the v1.323.0 harness).
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  answer: vi.fn(),
}));

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, API_BASE: "http://localhost", ijToken: () => "", get: H.get, post: H.post };
});

import {
  decodeSSE,
  sseEventFrom,
  useChatStream,
  type ChatStreamResult,
  type SSEEvent,
  type UseChatStream,
} from "@/lib/useChatStream";
import {
  answerElicitation,
  checkElicitationAnswer,
  decideSampling,
  fetchPackPrompts,
  fetchPackResources,
  getPackPrompt,
  type McpElicitationAsk,
  type McpField,
  type McpSamplingAsk,
  type PackPrompt,
} from "@/lib/mcpInteract";
import { ElicitationCard } from "@/components/chat/ElicitationCard";
import { SamplingCard } from "@/components/chat/SamplingCard";
import { PackPromptForm } from "@/components/chat/PackPromptForm";

// ------------------------------------------------------------------ transport

class Wire {
  private chunks: Uint8Array[] = [];
  private waiting: ((r: { value?: Uint8Array; done: boolean }) => void) | null = null;
  private ended = false;
  private enc = new TextEncoder();

  frame(event: string, data: unknown): void {
    this.deliver(this.enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
  }

  end(): void {
    this.ended = true;
    if (this.waiting) {
      const w = this.waiting;
      this.waiting = null;
      w({ done: true });
    }
  }

  private deliver(c: Uint8Array): void {
    if (this.waiting) {
      const w = this.waiting;
      this.waiting = null;
      w({ value: c, done: false });
    } else this.chunks.push(c);
  }

  response(): Response {
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      body: {
        getReader: () => ({
          read: () =>
            new Promise<{ value?: Uint8Array; done: boolean }>((res) => {
              const c = this.chunks.shift();
              if (c) res({ value: c, done: false });
              else if (this.ended) res({ done: true });
              else this.waiting = res;
            }),
          cancel: async () => undefined,
        }),
      },
    } as unknown as Response;
  }
}

let wire: Wire;

async function pump(): Promise<void> {
  for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
}

async function send(event: string, data: unknown): Promise<void> {
  await act(async () => {
    wire.frame(event, data);
    await pump();
  });
}

async function finish(): Promise<void> {
  await act(async () => {
    wire.end();
    await pump();
  });
}

let hook: UseChatStream | null = null;

function Owner() {
  hook = useChatStream();
  return null;
}

function start(): Promise<ChatStreamResult> {
  let p!: Promise<ChatStreamResult>;
  act(() => {
    p = hook!.run({ message: "q" });
  });
  p.catch(() => {});
  return p;
}

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    json: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  hook = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).includes("/chat/mcp/elicitations/")) return H.answer(url, init);
      wire = new Wire();
      return wire.response();
    }),
  );
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) =>
    setTimeout(() => cb(0), 0) as unknown as number,
  );
  vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  H.get.mockReset();
  H.post.mockReset();
  H.answer.mockReset();
});

// ------------------------------------------------------------------ fixtures

const ELICIT = {
  id: "e1",
  call_id: "c1",
  pack: "Weather",
  message: "Which city? <b>bold</b> **not markdown**",
  fields: [
    { name: "city", type: "string", title: "City", description: "Where you are", required: true, min_length: 2, max_length: 20 },
    { name: "email", type: "string", title: "Email", description: "", required: false, format: "email" },
    { name: "site", type: "string", title: "Website", description: "", required: false, format: "uri" },
    { name: "day", type: "string", title: "Day", description: "", required: false, format: "date" },
    { name: "when", type: "string", title: "When", description: "", required: false, format: "date-time" },
    { name: "temp", type: "number", title: "Temperature", description: "", required: false, minimum: -50, maximum: 60 },
    { name: "days", type: "integer", title: "Days", description: "", required: true, minimum: 1, maximum: 14 },
    { name: "metric", type: "boolean", title: "Use metric", description: "", required: false, default: true },
    { name: "unit", type: "string", title: "Unit", description: "", required: true, enum: ["c", "f"], enum_names: ["Celsius", "Fahrenheit"] },
    { name: "level", type: "integer", title: "Level", description: "", required: false, enum: [1, 2, 3] },
  ],
};

function elicitAsk(over: Partial<McpElicitationAsk> = {}): McpElicitationAsk {
  const ev = sseEventFrom("mcp_elicitation", ELICIT) as Extract<SSEEvent, { type: "mcp_elicitation" }>;
  return { ...ev.ask, ...over };
}

const SAMPLE = {
  id: "s1",
  call_id: "c1",
  pack: "Notes",
  system: "You summarise notes.",
  messages: [
    { role: "user", text: "Summarise this: buy milk" },
    { role: "assistant", text: "Earlier reply" },
  ],
  max_tokens: 400,
  model: "anthropic",
  model_id: "claude-sonnet-4-5",
  more: 2,
};

function sampleAsk(over: Partial<McpSamplingAsk> = {}): McpSamplingAsk {
  const ev = sseEventFrom("mcp_sampling", SAMPLE) as Extract<SSEEvent, { type: "mcp_sampling" }>;
  return { ...ev.ask, ...over };
}

// ------------------------------------------------------------------ decoding

describe("decode — the four frames are whitelisted", () => {
  it("mcp_elicitation keeps the promised fields only and drops what it cannot show", () => {
    const ev = decodeSSE(
      "mcp_elicitation",
      JSON.stringify({
        id: "e9",
        call_id: "c9",
        pack: "P",
        message: "m".repeat(2_500),
        extra: "nope",
        fields: [
          { name: "ok", type: "string", title: "OK", required: true, format: "email", secret: 1 },
          { name: "bad name!", type: "string" },
          { name: "nested", type: "object" },
          { name: "n", type: "number", format: "email", min_length: 3, minimum: "1", maximum: 9 },
          { name: "e", type: "string", enum: ["a", "b"], enum_names: ["A"] },
          { name: "ok", type: "boolean" },
          { name: "b", type: "boolean", required: "yes", default: "true" },
        ],
      }),
    ) as Extract<SSEEvent, { type: "mcp_elicitation" }>;
    expect(ev.type).toBe("mcp_elicitation");
    expect(Object.keys(ev.ask).sort()).toEqual(["callId", "fields", "id", "kind", "message", "pack"]);
    expect(ev.ask.message).toHaveLength(2_000);
    expect(ev.ask.fields.map((f) => f.name)).toEqual(["ok", "n", "e", "b"]);
    expect(ev.ask.fields[0]).toEqual({
      name: "ok",
      type: "string",
      title: "OK",
      description: "",
      required: true,
      format: "email",
    });
    // A number never carries a string format or length bounds; junk minimum dies.
    expect(ev.ask.fields[1]).toEqual({
      name: "n",
      type: "number",
      title: "n",
      description: "",
      required: false,
      maximum: 9,
    });
    // enum_names of the wrong length are dropped, the enum stays.
    expect(ev.ask.fields[2].enum).toEqual(["a", "b"]);
    expect(ev.ask.fields[2].enum_names).toBeUndefined();
    // Only a real true is required; a default of the wrong type dies.
    expect(ev.ask.fields[3].required).toBe(false);
    expect("default" in ev.ask.fields[3]).toBe(false);
    // No id, no frame.
    expect(sseEventFrom("mcp_elicitation", { message: "x", fields: [] })).toBeNull();
  });

  it("mcp_sampling clips texts, keeps 20 messages and counts the rest", () => {
    const msgs = Array.from({ length: 23 }, (_, i) => ({ role: "user", text: `m${i}` }));
    msgs.push({ role: "system", text: "dropped" } as never);
    const ev = sseEventFrom("mcp_sampling", {
      id: "s",
      call_id: "c",
      pack: "P",
      system: "s".repeat(3_000),
      messages: [{ role: "assistant", text: "t".repeat(5_000) }, ...msgs],
      max_tokens: 100,
      model: "openai",
      model_id: "gpt-x",
      more: 4,
    }) as Extract<SSEEvent, { type: "mcp_sampling" }>;
    expect(ev.ask.system).toHaveLength(2_000);
    expect(ev.ask.messages).toHaveLength(20);
    expect(ev.ask.messages[0].text).toHaveLength(4_000);
    expect(ev.ask.more).toBe(4 + 4);
    expect(ev.ask.maxTokens).toBe(100);
    expect(ev.ask.model).toBe("openai");
    expect(ev.ask.modelId).toBe("gpt-x");
    expect(sseEventFrom("mcp_sampling", { messages: [] })).toBeNull();
  });

  it("mcp_progress needs a call id and a numeric progress; total is a number or null", () => {
    expect(
      sseEventFrom("mcp_progress", { call_id: "c", pack: "P", progress: 3, total: "10", message: "x", junk: 1 }),
    ).toEqual({ type: "mcp_progress", call_id: "c", pack: "P", progress: 3, total: null, message: "x" });
    expect(sseEventFrom("mcp_progress", { call_id: "c", progress: "3" })).toBeNull();
    expect(sseEventFrom("mcp_progress", { progress: 3 })).toBeNull();
  });

  it("mcp_resolved keeps a known outcome; an unknown one reads as stopped", () => {
    expect(sseEventFrom("mcp_resolved", { id: "e1", kind: "sampling", outcome: "approved" })).toEqual({
      type: "mcp_resolved",
      id: "e1",
      kind: "sampling",
      outcome: "approved",
    });
    expect(sseEventFrom("mcp_resolved", { id: "e1", kind: "elicitation", outcome: "weird" })).toMatchObject({
      outcome: "stopped",
    });
    expect(sseEventFrom("mcp_resolved", { outcome: "accept" })).toBeNull();
  });
});

// ------------------------------------------------------------------ the hook

describe("useChatStream — app asks and progress", () => {
  it("folds progress onto the RIGHT tool card only, and never invents a card", async () => {
    render(<Owner />);
    const p = start();
    await send("tool_call", { id: "c1", name: "weather.lookup", status: "started" });
    await send("tool_call", { id: "c2", name: "notes.read", status: "started" });
    await send("mcp_progress", { call_id: "c2", pack: "Notes", progress: 2, total: 5, message: "Reading" });
    await send("mcp_progress", { call_id: "zz", pack: "Notes", progress: 1, total: null, message: "" });
    expect(hook!.tools.map((t) => t.id)).toEqual(["c1", "c2"]);
    expect(hook!.tools[0].progress).toBeUndefined();
    expect(hook!.tools[1].progress).toEqual({ progress: 2, total: 5, message: "Reading" });
    await send("mcp_progress", { call_id: "c2", pack: "Notes", progress: 4, total: 5, message: "Almost" });
    expect(hook!.tools[1].progress).toEqual({ progress: 4, total: 5, message: "Almost" });
    // The finished frame keeps the last report.
    await send("tool_call", { id: "c2", name: "notes.read", status: "finished", ok: true });
    expect(hook!.tools[1]).toMatchObject({ status: "done", progress: { progress: 4 } });
    await send("done", { reply: "ok" });
    await finish();
    const r = await p;
    expect(r.reply).toBe("ok");
    expect(r.steps.map((s) => s.name)).toEqual(["weather.lookup", "notes.read"]);
  });

  it("an ask is pending, then resolved by ITS id only, with the outcome kept", async () => {
    render(<Owner />);
    const p = start();
    expect(hook!.mcpAsks).toEqual([]);
    await send("tool_call", { id: "c1", name: "weather.lookup", status: "started" });
    await send("mcp_elicitation", ELICIT);
    expect(hook!.mcpAsks).toHaveLength(1);
    expect(hook!.mcpAsks![0]).toMatchObject({ kind: "elicitation", id: "e1", pack: "Weather" });
    expect(hook!.mcpAsks![0].outcome).toBeUndefined();
    await send("mcp_resolved", { id: "other", kind: "elicitation", outcome: "decline" });
    expect(hook!.mcpAsks![0].outcome).toBeUndefined();
    await send("mcp_resolved", { id: "e1", kind: "elicitation", outcome: "accept" });
    expect(hook!.mcpAsks![0].outcome).toBe("accept");
    await send("mcp_sampling", SAMPLE);
    expect(hook!.mcpAsks!.map((a) => [a.kind, a.id, a.outcome])).toEqual([
      ["elicitation", "e1", "accept"],
      ["sampling", "s1", undefined],
    ]);
    await send("done", { reply: "fine" });
    await finish();
    await p;
    // The turn ended: the still-open ask is "stopped", the answered one keeps its word.
    expect(hook!.mcpAsks!.map((a) => a.outcome)).toEqual(["accept", "stopped"]);
  });

  it("the done frame's resources receipt reaches the result, whitelisted per row", async () => {
    const raw = {
      reply: "r",
      resources: [
        { pack: "Files", uri: "file:///a.txt", ok: true, note: "", extra: 1 },
        { pack: "Files", uri: "file:///b.bin", ok: "yes", note: "That file could not be read." },
        { uri: "no pack", ok: true },
      ],
    };
    const ev = decodeSSE("done", JSON.stringify(raw)) as Extract<SSEEvent, { type: "done" }>;
    expect(ev.resources).toEqual([
      { pack: "Files", uri: "file:///a.txt", ok: true, note: "" },
      { pack: "Files", uri: "file:///b.bin", ok: false, note: "That file could not be read." },
    ]);
    // [] is kept ("none" is a fact); junk leaves the key absent.
    expect((decodeSSE("done", JSON.stringify({ reply: "r", resources: [] })) as { resources?: unknown }).resources).toEqual([]);
    expect("resources" in (sseEventFrom("done", { reply: "r", resources: "x" }) as object)).toBe(false);

    render(<Owner />);
    const p = start();
    await send("done", raw);
    await finish();
    const r = await p;
    expect(r.resources).toEqual(ev.resources);
  });

  it("the next turn starts with no asks", async () => {
    render(<Owner />);
    const p = start();
    await send("mcp_elicitation", ELICIT);
    await finish();
    await p;
    expect(hook!.mcpAsks).toHaveLength(1);
    const p2 = start();
    expect(hook!.mcpAsks).toEqual([]);
    await finish();
    await p2;
  });
});

// ------------------------------------------------------------------ the checks

describe("checkElicitationAnswer — the same checks the daemon runs", () => {
  // Built per test, so a broken decode fails THESE tests, not the collection.
  let fields: McpField[] = [];
  beforeEach(() => {
    fields = elicitAsk().fields;
  });
  const good = { city: "Paris", days: 3, unit: "c", metric: true };

  it("a good answer has no problems", () => {
    expect(checkElicitationAnswer(fields, good)).toEqual({});
    expect(
      checkElicitationAnswer(fields, {
        ...good,
        email: "a@b.co",
        site: "https://x.example",
        day: "2026-10-08",
        when: "2026-10-08T14:30:00Z",
        temp: -3.5,
        level: 2,
      }),
    ).toEqual({});
  });

  it("required, length, format, number, integer, enum and boolean problems", () => {
    expect(checkElicitationAnswer(fields, {})).toEqual({
      city: "Please fill this in.",
      days: "Please fill this in.",
      unit: "Please pick one.",
    });
    const e = checkElicitationAnswer(fields, {
      city: "P",
      email: "nope",
      site: "not a url",
      day: "2026-02-30",
      when: "tomorrow",
      temp: 99,
      days: 2.5,
      unit: "k",
      metric: "yes",
      level: 9,
    });
    expect(e).toEqual({
      city: "Use at least 2 characters.",
      email: "This must be an email address, like name@example.com.",
      site: "This must be a web address, like https://example.com.",
      day: "This must be a date.",
      when: "This must be a date and time.",
      temp: "This must be 60 or less.",
      days: "This must be a whole number.",
      unit: "Please pick one of the choices.",
      metric: "This must be yes or no.",
      level: "Please pick one of the choices.",
    });
    expect(checkElicitationAnswer(fields, { ...good, city: "x".repeat(21) }).city).toBe(
      "Use 20 characters or fewer.",
    );
    expect(checkElicitationAnswer(fields, { ...good, days: 0 }).days).toBe("This must be 1 or more.");
    expect(checkElicitationAnswer(fields, { ...good, days: "3" }).days).toBe("This must be a whole number.");
    expect(checkElicitationAnswer(fields, { ...good, city: 5 }).city).toBe("This must be text.");
  });
});

// ------------------------------------------------------------- ElicitationCard

function fieldBox(name: string): HTMLElement {
  return screen.getByTestId(`mcp-field-${name}`);
}

function fillGood() {
  fireEvent.change(screen.getByLabelText(/^City/), { target: { value: "Paris" } });
  fireEvent.change(screen.getByLabelText(/^Days/), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText(/^Unit/), { target: { value: "1" } });
}

describe("ElicitationCard", () => {
  it("says who is asking, shows the message as PLAIN text, and one control per field type", () => {
    render(<ElicitationCard ask={elicitAsk()} onAnswer={vi.fn()} />);
    expect(screen.getByText("Weather is asking")).toBeTruthy();
    const msg = screen.getByTestId("mcp-elicitation-message");
    expect(msg.textContent).toBe("Which city? <b>bold</b> **not markdown**");
    expect(msg.querySelector("b")).toBeNull();
    expect(msg.querySelector("strong")).toBeNull();

    const input = (re: RegExp) => screen.getByLabelText(re) as HTMLInputElement;
    expect(input(/^City/).type).toBe("text");
    expect(input(/^Email/).type).toBe("email");
    expect(input(/^Website/).type).toBe("url");
    expect(input(/^Day \(|^Day$/).type).toBe("date");
    expect(input(/^When/).type).toBe("datetime-local");
    const temp = input(/^Temperature/);
    expect(temp.type).toBe("number");
    expect(temp.min).toBe("-50");
    expect(temp.max).toBe("60");
    expect(temp.step).toBe("any");
    const days = input(/^Days/);
    expect(days.type).toBe("number");
    expect(days.step).toBe("1");
    const metric = input(/Use metric/);
    expect(metric.type).toBe("checkbox");
    expect(metric.checked).toBe(true); // the default
    const unit = screen.getByLabelText(/^Unit/) as HTMLSelectElement;
    expect(unit.tagName).toBe("SELECT");
    expect([...unit.options].map((o) => o.textContent)).toEqual(["Choose…", "Celsius", "Fahrenheit"]);
    const level = screen.getByLabelText(/^Level/) as HTMLSelectElement;
    expect([...level.options].map((o) => o.textContent)).toEqual(["Choose…", "1", "2", "3"]);

    // Required is marked in words and for assistive tech.
    expect(fieldBox("city").textContent).toContain("(required)");
    expect(input(/^City/).getAttribute("aria-required")).toBe("true");
    expect(fieldBox("email").textContent).not.toContain("(required)");
    expect(input(/^Email/).getAttribute("aria-required")).toBeNull();
    expect(screen.getByText("Only answer if you trust this app — never type a password here.")).toBeTruthy();
    for (const name of ["Send", "Decline", "Not now"]) expect(screen.getByRole("button", { name })).toBeTruthy();
  });

  it("runs the checks before sending: problems land under their fields and nothing is sent", () => {
    const onAnswer = vi.fn();
    render(<ElicitationCard ask={elicitAsk()} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onAnswer).not.toHaveBeenCalled();
    expect(fieldBox("city").textContent).toContain("Please fill this in.");
    expect(fieldBox("days").textContent).toContain("Please fill this in.");
    expect(fieldBox("unit").textContent).toContain("Please pick one.");
    fillGood();
    fireEvent.change(screen.getByLabelText(/^Email/), { target: { value: "not-an-email" } });
    fireEvent.change(screen.getByLabelText(/^Temperature/), { target: { value: "75" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onAnswer).not.toHaveBeenCalled();
    expect(fieldBox("email").textContent).toContain("This must be an email address");
    expect(fieldBox("temp").textContent).toContain("This must be 60 or less.");
    expect(fieldBox("city").textContent).not.toContain("Please fill this in.");
  });

  it("sends typed content, then says so in words and disables itself", async () => {
    const onAnswer = vi.fn(async () => ({ ok: true }));
    render(<ElicitationCard ask={elicitAsk()} onAnswer={onAnswer} />);
    fillGood();
    fireEvent.change(screen.getByLabelText(/^Temperature/), { target: { value: "21.5" } });
    fireEvent.change(screen.getByLabelText(/^Level/), { target: { value: "2" } });
    fireEvent.click(screen.getByLabelText(/Use metric/));
    fireEvent.change(screen.getByLabelText(/^When/), { target: { value: "2026-10-08T14:30" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(screen.getByTestId("mcp-elicitation-outcome").textContent).toBe("Sent. Weather has your answer."),
    );
    expect(onAnswer).toHaveBeenCalledTimes(1);
    const [action, content] = onAnswer.mock.calls[0] as unknown as [string, Record<string, unknown>];
    expect(action).toBe("accept");
    expect(content).toEqual({
      city: "Paris",
      days: 3,
      unit: "f",
      temp: 21.5,
      level: 3,
      metric: false,
      when: new Date("2026-10-08T14:30").toISOString(),
    });
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    expect((screen.getByLabelText(/^City/) as HTMLInputElement).closest("fieldset")!.disabled).toBe(true);
    expect(screen.getByTestId("mcp-elicitation-card").getAttribute("data-outcome")).toBe("accept");
  });

  it("shows the daemon's field errors under their fields and stays open", async () => {
    const onAnswer = vi.fn(async () => ({
      ok: false,
      errors: { city: "That city is not known.", ghost: "Something else is off." },
    }));
    render(<ElicitationCard ask={elicitAsk()} onAnswer={onAnswer} />);
    fillGood();
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(fieldBox("city").textContent).toContain("That city is not known."));
    expect(screen.getByText("Something else is off.")).toBeTruthy();
    expect(screen.queryByTestId("mcp-elicitation-outcome")).toBeNull();
    expect((screen.getByRole("button", { name: "Send" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("a plain failure shows its sentence and keeps the buttons", async () => {
    const onAnswer = vi.fn(async () => ({ ok: false, error: "This question already ended." }));
    render(<ElicitationCard ask={elicitAsk()} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByRole("button", { name: "Decline" }));
    await waitFor(() => expect(screen.getByText("This question already ended.")).toBeTruthy());
    expect((screen.getByRole("button", { name: "Decline" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("Decline and Not now send no content and say what happened", async () => {
    const onAnswer = vi.fn(async () => ({ ok: true }));
    const { unmount } = render(<ElicitationCard ask={elicitAsk()} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByRole("button", { name: "Decline" }));
    await waitFor(() =>
      expect(screen.getByTestId("mcp-elicitation-outcome").textContent).toBe("You said no. Weather was told."),
    );
    expect(onAnswer).toHaveBeenLastCalledWith("decline", undefined);
    unmount();
    render(<ElicitationCard ask={elicitAsk()} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    await waitFor(() =>
      expect(screen.getByTestId("mcp-elicitation-outcome").textContent).toBe(
        "Skipped for now. Weather was told you did not answer.",
      ),
    );
    expect(onAnswer).toHaveBeenLastCalledWith("cancel", undefined);
  });

  it("the daemon's outcome closes the card in words", () => {
    render(<ElicitationCard ask={elicitAsk({ outcome: "stopped" })} onAnswer={vi.fn()} />);
    expect(screen.getByTestId("mcp-elicitation-outcome").textContent).toBe(
      "This question ended before it was answered.",
    );
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    expect((screen.getByLabelText(/^City/) as HTMLInputElement).closest("fieldset")!.disabled).toBe(true);
  });

  it("keys typed inside never reach the page or the composer", () => {
    const parent = vi.fn();
    const doc = vi.fn();
    document.addEventListener("keydown", doc);
    try {
      render(
        <div onKeyDown={parent}>
          <ElicitationCard ask={elicitAsk()} onAnswer={vi.fn()} />
        </div>,
      );
      fireEvent.keyDown(screen.getByLabelText(/^City/), { key: "/" });
      fireEvent.keyDown(screen.getByLabelText(/^Unit/), { key: "Enter" });
      expect(parent).not.toHaveBeenCalled();
      expect(doc).not.toHaveBeenCalled();
      // Anti-vacuity: the same listeners DO hear a key outside the card.
      fireEvent.keyDown(document.body, { key: "/" });
      expect(doc).toHaveBeenCalledTimes(1);
    } finally {
      document.removeEventListener("keydown", doc);
    }
  });
});

// ------------------------------------------------------------- answerElicitation

describe("answerElicitation", () => {
  it("2xx is ok; the body carries content only on accept", async () => {
    H.answer.mockResolvedValue(jsonResponse(200, { ok: true }));
    expect(await answerElicitation("e 1", "accept", { city: "Paris" })).toEqual({ ok: true });
    const [url, init] = H.answer.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost/chat/mcp/elicitations/e%201");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ action: "accept", content: { city: "Paris" } });
    await answerElicitation("e1", "decline");
    expect(JSON.parse(String((H.answer.mock.calls[1] as [string, RequestInit])[1].body))).toEqual({
      action: "decline",
    });
  });

  it("a 400 with detail.errors maps to field errors; 404 and others to one sentence", async () => {
    H.answer.mockResolvedValueOnce(jsonResponse(400, { detail: { errors: { city: "Too short.", n: 3 } } }));
    expect(await answerElicitation("e1", "accept", {})).toEqual({ ok: false, errors: { city: "Too short." } });
    H.answer.mockResolvedValueOnce(jsonResponse(404, { detail: "unknown" }));
    expect(await answerElicitation("e1", "accept", {})).toEqual({
      ok: false,
      error: "This question already ended.",
    });
    H.answer.mockResolvedValueOnce(jsonResponse(400, { detail: "Bad action." }));
    expect(await answerElicitation("e1", "accept", {})).toEqual({ ok: false, error: "Bad action." });
    H.answer.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const off = await answerElicitation("e1", "accept", {});
    expect(off.ok).toBe(false);
    expect(off.error).toMatch(/not reachable/);
  });
});

// ------------------------------------------------------------------ SamplingCard

describe("SamplingCard", () => {
  it("names the app and the turn's own model, folds the request, offers both buttons", () => {
    render(<SamplingCard ask={sampleAsk()} onDecide={vi.fn()} />);
    expect(screen.getByTestId("mcp-sampling-title").textContent).toBe(
      "Notes wants to ask Anthropic (claude-sonnet-4-5) a question for its own use",
    );
    const details = screen.getByTestId("mcp-sampling-request").closest("details")!;
    expect(details.open).toBe(false);
    const req = screen.getByTestId("mcp-sampling-request").textContent!;
    expect(req).toContain("You summarise notes.");
    expect(req).toContain("Summarise this: buy milk");
    expect(req).toContain("Earlier reply");
    expect(req).toContain("…and 2 more messages");
    expect(screen.getByRole("button", { name: "Allow once" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Deny" })).toBeTruthy();
  });

  it("the demo model is named as such", () => {
    render(<SamplingCard ask={sampleAsk({ model: "mock", modelId: "" })} onDecide={vi.fn()} />);
    expect(screen.getByTestId("mcp-sampling-title").textContent).toBe(
      "Notes wants to ask Demo model (scripted) a question for its own use",
    );
  });

  it("Allow once and Deny each send their decision and say so", async () => {
    const onDecide = vi.fn(async () => true);
    const { unmount } = render(<SamplingCard ask={sampleAsk()} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: "Allow once" }));
    await waitFor(() =>
      expect(screen.getByTestId("mcp-sampling-outcome").textContent).toBe(
        "You allowed it. The answer goes to Notes.",
      ),
    );
    expect(onDecide).toHaveBeenLastCalledWith("approve");
    expect(screen.queryByRole("button", { name: "Allow once" })).toBeNull();
    unmount();
    render(<SamplingCard ask={sampleAsk()} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    await waitFor(() =>
      expect(screen.getByTestId("mcp-sampling-outcome").textContent).toBe("You said no. Notes was told."),
    );
    expect(onDecide).toHaveBeenLastCalledWith("deny");
  });

  it("a failed decision keeps the buttons and says so", async () => {
    const onDecide = vi.fn(async () => false);
    render(<SamplingCard ask={sampleAsk()} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: "Allow once" }));
    await waitFor(() => expect(screen.getByText("That did not go through. Try again.")).toBeTruthy());
    expect((screen.getByRole("button", { name: "Allow once" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByTestId("mcp-sampling-outcome")).toBeNull();
  });

  it("keys pressed on it never bubble", () => {
    const parent = vi.fn();
    render(
      <div onKeyDown={parent}>
        <SamplingCard ask={sampleAsk()} onDecide={vi.fn()} />
      </div>,
    );
    fireEvent.keyDown(screen.getByRole("button", { name: "Deny" }), { key: "Enter" });
    expect(parent).not.toHaveBeenCalled();
  });

  it("decideSampling posts the decision and reports success as a boolean", async () => {
    H.post.mockResolvedValueOnce({ ok: true });
    expect(await decideSampling("s1", "approve")).toBe(true);
    expect(H.post).toHaveBeenCalledWith("/chat/mcp/sampling/s1", { decision: "approve" });
    H.post.mockRejectedValueOnce(Object.assign(new Error("gone"), { status: 404 }));
    expect(await decideSampling("s1", "deny")).toBe(false);
  });
});

// ---------------------------------------------------------------- PackPromptForm

const PROMPT: PackPrompt = {
  pack: "Notes",
  name: "summarise",
  title: "Summarise a note",
  description: "Writes a short summary.",
  arguments: [
    { name: "note", title: "Note name", description: "Which note", required: true },
    { name: "tone", title: "", description: "", required: false },
  ],
};

describe("PackPromptForm", () => {
  it("one input per argument, required marked; a missing one stops the fetch", () => {
    const onInsert = vi.fn();
    render(<PackPromptForm prompt={PROMPT} onInsert={onInsert} />);
    expect(screen.getByText("Summarise a note")).toBeTruthy();
    expect(screen.getByText("From Notes")).toBeTruthy();
    const note = screen.getByLabelText(/^Note name/) as HTMLInputElement;
    expect(note.getAttribute("aria-required")).toBe("true");
    expect(screen.getByLabelText(/^Note name/).parentElement!.textContent).toContain("(required)");
    const tone = screen.getByLabelText(/^tone/) as HTMLInputElement;
    expect(tone.getAttribute("aria-required")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Use" }));
    expect(screen.getByText("Please fill this in.")).toBeTruthy();
    expect(H.post).not.toHaveBeenCalled();
    expect(onInsert).not.toHaveBeenCalled();
  });

  it("Use fetches the filled prompt and inserts its text; a clean result has no warning", async () => {
    H.post.mockResolvedValueOnce({ text: "Summarise groceries", messages: [], flagged: false });
    const onInsert = vi.fn();
    render(<PackPromptForm prompt={PROMPT} onInsert={onInsert} />);
    fireEvent.change(screen.getByLabelText(/^Note name/), { target: { value: "groceries" } });
    fireEvent.click(screen.getByRole("button", { name: "Use" }));
    await waitFor(() => expect(onInsert).toHaveBeenCalledWith("Summarise groceries"));
    expect(H.post).toHaveBeenCalledWith("/mcp/prompts/get", {
      pack: "Notes",
      name: "summarise",
      arguments: { note: "groceries" },
    });
    expect(screen.queryByTestId("pack-prompt-flagged")).toBeNull();
  });

  it("a flagged result is still inserted, with a warning line", async () => {
    H.post.mockResolvedValueOnce({ text: "Summarise [blocked]", messages: [], flagged: true });
    const onInsert = vi.fn();
    render(<PackPromptForm prompt={PROMPT} onInsert={onInsert} />);
    fireEvent.change(screen.getByLabelText(/^Note name/), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Use" }));
    await waitFor(() => expect(screen.getByTestId("pack-prompt-flagged")).toBeTruthy());
    expect(screen.getByTestId("pack-prompt-flagged").textContent).toBe(
      "Part of this prompt looked unsafe, so it was left out. Read it before you send.",
    );
    expect(onInsert).toHaveBeenCalledWith("Summarise [blocked]");
  });

  it("errors are one sentence and insert nothing", async () => {
    const onInsert = vi.fn();
    H.post.mockRejectedValueOnce(Object.assign(new Error("not found"), { status: 404 }));
    const { unmount } = render(<PackPromptForm prompt={PROMPT} onInsert={onInsert} />);
    fireEvent.change(screen.getByLabelText(/^Note name/), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Use" }));
    await waitFor(() => expect(screen.getByText("That prompt is no longer offered by Notes.")).toBeTruthy());
    unmount();
    H.post.mockRejectedValueOnce(Object.assign(new Error("Notes did not answer in time."), { status: 502 }));
    render(<PackPromptForm prompt={PROMPT} onInsert={onInsert} />);
    fireEvent.change(screen.getByLabelText(/^Note name/), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Use" }));
    await waitFor(() => expect(screen.getByText("Notes did not answer in time.")).toBeTruthy());
    expect(onInsert).not.toHaveBeenCalled();
  });

  it("keys typed inside never bubble", () => {
    const parent = vi.fn();
    render(
      <div onKeyDown={parent}>
        <PackPromptForm prompt={PROMPT} onInsert={vi.fn()} />
      </div>,
    );
    fireEvent.keyDown(screen.getByLabelText(/^Note name/), { key: "@" });
    expect(parent).not.toHaveBeenCalled();
  });
});

// ------------------------------------------------------------------ fetchers

describe("prompt and resource fetchers", () => {
  it("fetchPackPrompts decodes defensively; an older daemon (404) has none", async () => {
    H.get.mockResolvedValueOnce({
      prompts: [
        { pack: "Notes", name: "s", title: "S", description: "d", arguments: [{ name: "a", required: true, x: 1 }, { title: "no name" }] },
        { pack: "", name: "nopack" },
        "junk",
      ],
      failed: [{ pack: "Broken", error: "It did not start." }, { error: "no pack" }],
    });
    expect(await fetchPackPrompts()).toEqual({
      prompts: [
        {
          pack: "Notes",
          name: "s",
          title: "S",
          description: "d",
          arguments: [{ name: "a", title: "", description: "", required: true }],
        },
      ],
      failed: [{ pack: "Broken", error: "It did not start." }],
    });
    expect(H.get).toHaveBeenCalledWith("/mcp/prompts");
    H.get.mockRejectedValueOnce(Object.assign(new Error("404 Not Found"), { status: 404 }));
    expect(await fetchPackPrompts()).toEqual({ prompts: [], failed: [] });
    H.get.mockRejectedValueOnce(Object.assign(new Error("boom"), { status: 500 }));
    await expect(fetchPackPrompts()).rejects.toThrow("boom");
  });

  it("fetchPackResources sends q encoded and keeps only rows with a pack and a uri", async () => {
    H.get.mockResolvedValueOnce({
      resources: [
        { pack: "Files", uri: "file:///a.txt", name: "a.txt", title: "A", description: "", mime_type: "text/plain", y: 1 },
        { pack: "Files", name: "no uri" },
      ],
      failed: [],
    });
    const r = await fetchPackResources("a b&c");
    expect(H.get).toHaveBeenCalledWith("/mcp/resources?q=a%20b%26c");
    expect(r.resources).toEqual([
      { pack: "Files", uri: "file:///a.txt", name: "a.txt", title: "A", description: "", mime_type: "text/plain" },
    ]);
    H.get.mockResolvedValueOnce({ resources: [] });
    await fetchPackResources();
    expect(H.get).toHaveBeenLastCalledWith("/mcp/resources");
  });

  it("getPackPrompt keeps text, role-checked messages and a real-true flag only", async () => {
    H.post.mockResolvedValueOnce({
      text: "T",
      messages: [{ role: "user", text: "T" }, { role: "tool", text: "x" }],
      flagged: "yes",
    });
    expect(await getPackPrompt("Notes", "s", { a: "1" })).toEqual({
      text: "T",
      messages: [{ role: "user", text: "T" }],
      flagged: false,
    });
  });
});

