/**
 * v1.351.0 (G15) — an app connected from chat works in that chat and the
 * task continues: the page's half.
 *
 * ACCEPTANCE (as a new user would check it): ask to list your open GitHub
 * PRs, say "connect my GitHub", paste a token into the secure card, see
 * "GitHub connected · N tools" and a Continue button, and pressing it answers
 * the ORIGINAL question with GitHub on for the chat — no trip to the "+" menu.
 *
 * Driven through the REAL chat page, the REAL ConfigCards and the REAL
 * lib/appConnect; only the transport (lib/api, the stream hook) is mocked,
 * exactly as the v1.321.0 card suite does.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

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
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: {
      bodies: [] as Record<string, unknown>[],
      replies: [] as string[],
      extras: [] as Record<string, unknown>[],
      tools: [] as Record<string, unknown>[],
    },
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
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
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
    tools: H.stream.tools,
    approval: null,
    run: async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      H.stream.bodies.push(body);
      await new Promise<void>((r) => setTimeout(r, 0));
      const reply = H.stream.replies.shift() ?? "done";
      const extra = H.stream.extras.shift() ?? {};
      onDelta(reply, reply);
      return { reply, ...extra };
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
import { ConfigCards } from "@/components/chat/ConfigCards";
import { decodeConfigCards, type ConfigCard } from "@/lib/configCards";
import {
  appConnectIds,
  appIdOf,
  connectedFromSave,
  connectedWords,
  continueText,
  isConnectRequest,
  isGoAhead,
  taskBefore,
  waitingWords,
  withAppConnected,
  withConnectors,
} from "@/lib/appConnect";

const TOKEN = "ghp_PAGE0123456789abcdefNEVERSHOWN";
const QUESTION = "list my open GitHub PRs";
const GITHUB_CARD = {
  kind: "secret",
  name: "app.github",
  label: "GitHub personal access token",
  why: "to connect GitHub",
  request_id: "seq_gh",
};

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.replies.length = 0;
  H.stream.extras.length = 0;
  H.stream.tools = [];
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = {
    "/models": { models: [] },
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

async function send(text: string, reply: string, extra: Record<string, unknown> = {}) {
  const el = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  H.stream.replies.push(reply);
  H.stream.extras.push(extra);
  fireEvent.change(el, { target: { value: text } });
  fireEvent.keyDown(el, { key: "Enter" });
  await screen.findByText(reply);
}

function lastUser(body: Record<string, unknown> | undefined): string {
  const msgs = (body?.messages as { role: string; content: string }[] | undefined) ?? [];
  return [...msgs].reverse().find((m) => m.role === "user")?.content ?? "";
}

/** Ask, connect, paste the token — the card has been saved with `answer`. */
async function connectGitHub(answer: Record<string, unknown>) {
  H.api.postResponses["/config/secret"] = answer;
  render(<ChatPage />);
  await send(QUESTION, "I need GitHub connected to see your pull requests.");
  await send("connect my GitHub", "Paste your token on the card.", { configCards: [GITHUB_CARD] });
  const form = await screen.findByTestId("credential-card");
  fireEvent.change(form.querySelector("input") as HTMLInputElement, { target: { value: TOKEN } });
  fireEvent.click(screen.getByRole("button", { name: "Save securely" }));
  return screen.findByTestId("credential-card-saved");
}

describe("connect from chat, then carry on (G15)", () => {
  it("THE ACCEPTANCE: the card says 'GitHub connected · 26 tools', and Continue answers the original question with GitHub on", async () => {
    const saved = await connectGitHub({ status: "stored", action_id: "tool_9", app: "github", app_name: "GitHub", tools_loaded: 26 });
    expect(saved.textContent).toContain("GitHub connected · 26 tools");
    expect(saved.textContent).not.toContain("saved securely");
    expect(document.body.innerHTML).not.toContain(TOKEN);

    // Pressing Continue carries the ORIGINAL question on, with GitHub on for
    // the chat — the "+" menu was never opened.
    H.stream.replies.push("You have 3 open pull requests.");
    fireEvent.click(within(saved).getByRole("button", { name: /Continue/ }));
    await screen.findByText("You have 3 open pull requests.");
    const body = H.stream.bodies.at(-1);
    expect(lastUser(body)).toBe(`GitHub is connected. Please carry on with: ${QUESTION}`);
    expect(body?.connectors).toEqual(["github"]);

    // The press is remembered: no second Continue, here or after a reload.
    await waitFor(() => expect(screen.queryByTestId("credential-card-continue")).toBeNull());
    await waitFor(() => {
      const save = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/")).at(-1);
      const msgs = (save?.body.messages as Record<string, unknown>[] | undefined) ?? [];
      const card = msgs.flatMap((m) => (m.configCards as Record<string, unknown>[] | undefined) ?? [])[0];
      expect(card?.connected).toMatchObject({ app: "github", name: "GitHub", tools: 26, continued: true });
    });
    // The switch is saved with the thread's setup, like a "+" menu toggle.
    await waitFor(() => {
      const withSetup = H.api.puts.filter((p) => p.body.setup).at(-1);
      expect((withSetup?.body.setup as { connectors?: string[] } | undefined)?.connectors).toEqual(["github"]);
    });
    expect(JSON.stringify(H.api.puts)).not.toContain(TOKEN);
    expect(JSON.stringify(H.stream.bodies)).not.toContain(TOKEN);
  });

  it("saving the token alone turns the app on: the next message carries it even without Continue", async () => {
    await connectGitHub({ status: "stored", action_id: "tool_9", app: "github", app_name: "GitHub", tools_loaded: 26 });
    await send("what are my open PRs?", "Here they are.");
    expect(H.stream.bodies.at(-1)?.connectors).toEqual(["github"]);
    // Continue belongs to the newest reply only.
    expect(screen.queryByTestId("credential-card-continue")).toBeNull();
  });

  it("a pack that did not load says so and points at Apps & tools — and offers no Continue", async () => {
    const saved = await connectGitHub({
      status: "stored",
      action_id: "tool_9",
      app: "github",
      app_name: "GitHub",
      tools_loaded: 0,
      load_reason: "the package was not found",
    });
    expect(saved.textContent).toContain("Saved, but its tools did not load");
    const link = within(saved).getByRole("link", { name: "open Apps & tools" });
    // Review: the words name Apps & tools (the Directory, `/marketplace` →
    // Settings › Connections › Apps & tools, where the app's row says why it
    // did not start). `/tools` opens "Tools & permissions", a different page.
    expect(link.getAttribute("href")).toBe("/marketplace");
    expect(readFileSync(join(__dirname, "..", "lib", "relocated.ts"), "utf8").replace(/\r\n/g, "\n")).toMatch(
      /"\/marketplace": \{ base: "\/settings", extra: \{ section: "connections-apps" \} \}/,
    );
    // ...and it is a client-side door (next/link, like the chat's other
    // Directory links), not a plain <a> that reloads the whole chat page.
    const cards = readFileSync(join(__dirname, "..", "components", "chat", "ConfigCards.tsx"), "utf8").replace(/\r\n/g, "\n");
    expect(cards).toMatch(/^import Link from "next\/link";$/m);
    const notLoaded = cards.slice(cards.indexOf('data-testid="credential-card-not-loaded"'));
    expect(notLoaded.slice(0, notLoaded.indexOf("</span>"))).toMatch(/<Link href="\/marketplace"[^>]*>\s*open Apps &amp; tools\s*<\/Link>/);
    expect(within(saved).getByTestId("credential-card-not-loaded").getAttribute("title")).toBe("the package was not found");
    expect(screen.queryByTestId("credential-card-continue")).toBeNull();
  });

  it("an app that still needs its second key says which one, and offers no Continue", async () => {
    const saved = await connectGitHub({
      status: "stored",
      action_id: "tool_9",
      app: "box",
      app_name: "Box",
      tools_loaded: 0,
      waiting_for: ["Box client secret"],
    });
    expect(saved.textContent).toContain("saved securely");
    expect(saved.textContent).toContain("Box still needs its client secret");
    expect(screen.queryByTestId("credential-card-continue")).toBeNull();
  });

  it("an older daemon's answer keeps today's words exactly", async () => {
    const saved = await connectGitHub({ status: "stored", action_id: "tool_9" });
    expect(saved.textContent).toContain("GitHub personal access token saved securely");
    expect(screen.queryByTestId("credential-card-continue")).toBeNull();
  });

  it("an app the turn found ALREADY connected is turned on for the chat (app_connect's own promise)", async () => {
    render(<ChatPage />);
    H.stream.tools = [{ id: "c0", name: "app_connect", status: "done", ok: true, args: { app: "github" } }];
    await send("connect my GitHub", "GitHub is already connected (26 tools). It is on for this chat now.");
    H.stream.tools = [];
    await send(QUESTION, "Here they are.");
    expect(H.stream.bodies.at(-1)?.connectors).toEqual(["github"]);
    // A FAILED app_connect turns nothing on.
    cleanup();
    H.stream.bodies.length = 0;
    render(<ChatPage />);
    H.stream.tools = [{ id: "c1", name: "app_connect", status: "done", ok: false, args: { app: "slack" } }];
    await send("connect my Slack", "That did not work.");
    H.stream.tools = [];
    await send("hello", "hi");
    expect(H.stream.bodies.at(-1)?.connectors).toBeUndefined();
  });
});

describe("a reopened chat keeps what the save said (whitelist + connected)", () => {
  it("withAppConnected puts `connected` back after decodeConfigCards, and junk dies", () => {
    const raw = [
      { ...GITHUB_CARD, saved: "stored", connected: { app: "github", name: "GitHub", tools: 26, evil: "<x>" } },
      { ...GITHUB_CARD, request_id: "seq_2", saved: "stored", connected: { app: "../etc", tools: 3 } },
    ];
    const cards = withAppConnected(decodeConfigCards(raw), raw);
    expect((cards[0] as unknown as { connected: unknown }).connected).toEqual({ app: "github", name: "GitHub", tools: 26 });
    expect((cards[1] as unknown as { connected?: unknown }).connected).toBeUndefined();
    const onContinue = vi.fn();
    render(<ConfigCards cards={cards} onSettle={() => {}} onContinue={onContinue} />);
    expect(screen.getByTestId("credential-card-connected").textContent).toContain("GitHub connected · 26 tools");
    fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
    expect(onContinue).toHaveBeenCalledWith(0);
  });

  it("no Continue without the page's go-ahead (an older reply, or a turn running)", () => {
    const raw = [{ ...GITHUB_CARD, saved: "stored", connected: { app: "github", name: "GitHub", tools: 2 } }];
    render(<ConfigCards cards={withAppConnected(decodeConfigCards(raw), raw)} onSettle={() => {}} />);
    expect(screen.getByTestId("credential-card-connected").textContent).toContain("GitHub connected · 2 tools");
    expect(screen.queryByTestId("credential-card-continue")).toBeNull();
  });
});

describe("lib/appConnect", () => {
  it("names the app of a card, and only an app's", () => {
    expect(appIdOf("app.github")).toBe("github");
    expect(appIdOf("app.box__BOX_CLIENT_ID")).toBe("box");
    expect(appIdOf("connection.openai")).toBe("");
    expect(appIdOf("app.")).toBe("");
  });

  it("reads the save's answer — and nothing from an older daemon or another card", () => {
    expect(connectedFromSave("app.github", { status: "stored", app: "github", app_name: "GitHub", tools_loaded: 1 })).toEqual({
      app: "github",
      name: "GitHub",
      tools: 1,
    });
    expect(connectedFromSave("app.github", { status: "stored" })).toBeUndefined();
    expect(connectedFromSave("channel.phone", { tools_loaded: 3 })).toBeUndefined();
  });

  it("words: one tool, many tools, and what a two-key app still needs", () => {
    expect(connectedWords({ app: "a", name: "GitHub", tools: 1 })).toBe("GitHub connected · 1 tool");
    expect(connectedWords({ app: "a", name: "GitHub", tools: 26 })).toBe("GitHub connected · 26 tools");
    expect(waitingWords({ app: "box", name: "Box", tools: 0, waiting: ["Box client secret"] })).toBe("Box still needs its client secret");
  });

  it("finds the task to carry on with — never the 'connect my X' message, and unwraps an earlier Continue", () => {
    const msgs = [
      { role: "user", content: QUESTION },
      { role: "assistant", content: "I need GitHub." },
      { role: "user", content: "ok, connect my GitHub please" },
      { role: "assistant", content: "card" },
    ];
    expect(taskBefore(msgs, 3)).toBe(QUESTION);
    const after = [...msgs, { role: "user", content: continueText("GitHub", QUESTION) }, { role: "assistant", content: "x" }];
    expect(taskBefore(after, 5)).toBe(QUESTION);
    expect(taskBefore([{ role: "user", content: "connect my GitHub" }], 1)).toBe("");
    expect(continueText("GitHub", "")).toBe("GitHub is connected. Please carry on.");
    expect(taskBefore([{ role: "user", content: "note", steer: true }, { role: "user", content: "real task" }], 2)).toBe("real task");
  });

  it("tells a bare connect request from a task that mentions connecting", () => {
    for (const t of ["connect my GitHub", "Connect GitHub please", "ok, connect it", "can you link my Notion", "please set up Slack", "reconnect github"])
      expect(isConnectRequest(t), t).toBe(true);
    for (const t of [QUESTION, "why won't GitHub connect?", "summarize the connection string docs", "list PRs and connect the dots"])
      expect(isConnectRequest(t), t).toBe(false);
  });

  it("reads only finished, successful app_connect calls, and turns apps on within the cap", () => {
    expect(
      appConnectIds([
        { name: "app_connect", status: "done", ok: true, args: { app: "GitHub" } },
        { name: "app_connect", status: "running", ok: undefined, args: { app: "notion" } },
        { name: "app_connect", status: "done", ok: false, args: { app: "slack" } },
        { name: "web_search", status: "done", ok: true, args: { app: "x" } },
        { name: "app_connect", status: "done", ok: true, args: { app: "../bad" } },
      ]),
    ).toEqual(["github"]);
    const prev = ["a"];
    expect(withConnectors(prev, ["a"], 6)).toBe(prev);
    expect(withConnectors(prev, ["b", "c"], 2)).toEqual(["a", "b"]);
  });
});

describe("the look stays the calm card's (source)", () => {
  const src = readFileSync(join(__dirname, "..", "components", "chat", "ConfigCards.tsx"), "utf8").replace(/\r\n/g, "\n");
  it("the Continue is one quiet text button — no new box, border or shadow; theme tokens only", () => {
    const start = src.indexOf('data-testid="credential-card-continue"');
    expect(start).toBeGreaterThan(-1);
    const button = src.slice(src.lastIndexOf("<button", start), src.indexOf("</button>", start));
    expect(button).toContain("text-accent-soft");
    expect(button).not.toMatch(/\bborder\b|shadow|rounded-xl|bg-white/);
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgb\(/);
    expect(src).not.toMatch(/text-\[\d+\.\d+px\]/);
  });
});

describe("review: a connect request that also carries the task IS the task", () => {
  it("is not skipped — an older, unrelated question is never carried on instead", () => {
    for (const t of [
      "connect my GitHub and list my open PRs",
      "connect my GitHub so you can list my open PRs",
      "Connect GitHub, then show my open pull requests",
      "set up Notion and summarize my meeting notes page",
      "link my GitHub to see my open PRs",
    ])
      expect(isConnectRequest(t), t).toBe(false);
    for (const t of ["please connect to GitHub", "connect my GitHub, please", "set up my Google Drive for me", "connect my GitHub account"])
      expect(isConnectRequest(t), t).toBe(true);
    const msgs = [
      { role: "user", content: "what's the weather in Seattle" },
      { role: "assistant", content: "Rainy." },
      { role: "user", content: "connect my GitHub and list my open PRs" },
      { role: "assistant", content: "card" },
    ];
    expect(taskBefore(msgs, 3)).toBe("connect my GitHub and list my open PRs");
  });
});

describe("review: a pressed Continue is not offered again", () => {
  it("even while its reply is the newest and the page gives the go-ahead", () => {
    const raw = [{ ...GITHUB_CARD, saved: "stored", connected: { app: "github", name: "GitHub", tools: 2, continued: true } }];
    render(<ConfigCards cards={withAppConnected(decodeConfigCards(raw), raw)} onSettle={() => {}} onContinue={() => {}} />);
    expect(screen.getByTestId("credential-card-connected").textContent).toContain("GitHub connected · 2 tools");
    expect(screen.queryByTestId("credential-card-continue")).toBeNull();
  });
});

describe("fix round 2: a task that starts with 'add' or 'set up' is the task", () => {
  const TODOIST_CARD = {
    kind: "secret",
    name: "app.todoist",
    label: "Todoist API token",
    why: "to add the task",
    request_id: "seq_td",
  };

  it("through the page: Continue carries 'add a task in Todoist' on, never the older question", async () => {
    H.api.postResponses["/config/secret"] = { status: "stored", action_id: "tool_7", app: "todoist", app_name: "Todoist", tools_loaded: 5 };
    render(<ChatPage />);
    await send("what's the weather in Seattle", "Rainy.");
    await send("add a task in Todoist", "Paste your Todoist token on the card.", { configCards: [TODOIST_CARD] });
    const form = await screen.findByTestId("credential-card");
    fireEvent.change(form.querySelector("input") as HTMLInputElement, { target: { value: "td-PAGE-NEVERSHOWN-1234" } });
    fireEvent.click(screen.getByRole("button", { name: "Save securely" }));
    const saved = await screen.findByTestId("credential-card-saved");
    expect(saved.textContent).toContain("Todoist connected · 5 tools");
    H.stream.replies.push("Added it.");
    fireEvent.click(within(saved).getByRole("button", { name: /Continue/ }));
    await screen.findByText("Added it.");
    expect(lastUser(H.stream.bodies.at(-1))).toBe("Todoist is connected. Please carry on with: add a task in Todoist");
  });

  it("judged for the card's app: its name or its key is a request; anything else after 'add'/'set up' is work", () => {
    const github = { id: "github", name: "GitHub" };
    for (const t of [
      "connect my GitHub",
      "set up GitHub",
      "please add GitHub",
      "add my GitHub token",
      "add my github account",
      "set up GitHub for me",
      "connect it",
      "yes, set it up",
      "ok, add it",
      "connect my Notion",
    ])
      expect(isConnectRequest(t, github), t).toBe(true);
    for (const t of [
      "add a comment on PR 42",
      "set up a new repo called demo",
      "add Bob as a collaborator",
      "set up a meeting with Bob tomorrow",
    ])
      expect(isConnectRequest(t, github), t).toBe(false);
    expect(isConnectRequest("add a task in Todoist", { id: "todoist", name: "Todoist" })).toBe(false);
    expect(isConnectRequest("add Todoist", { id: "todoist", name: "Todoist" })).toBe(true);
    const drive = { id: "google-drive", name: "Google Drive" };
    expect(isConnectRequest("set up my Google Drive", drive)).toBe(true);
    expect(isConnectRequest("set up my drive", drive)).toBe(true);
    expect(isConnectRequest("add a folder in my Drive", drive)).toBe(false);
    // With no app named, the rule is the first review's, unchanged.
    expect(isConnectRequest("add a task in Todoist")).toBe(true);
  });

  it("a SHORT request that carries the task is the task (the second-clause rule alone), and a long one is too (the word limit alone)", () => {
    // Under the six-word limit: only the second-clause rule can see these.
    for (const t of ["connect GitHub and list PRs", "link GitHub to see PRs", "connect GitHub, then summarize", "set up Slack so I can post"])
      expect(isConnectRequest(t), t).toBe(false);
    // No conjunction, comma or later "to": only the word limit sees this one.
    expect(isConnectRequest("link GitHub list my open pull requests today")).toBe(false);
  });

  it("a go-ahead ('yes') answers the offer to connect and is not the task", () => {
    for (const t of ["yes", "Sure, go ahead", "ok please", "yes please!"]) expect(isGoAhead(t), t).toBe(true);
    for (const t of ["", "yes list my PRs", "no", "what PRs do I have?"]) expect(isGoAhead(t), t).toBe(false);
    const msgs = [
      { role: "user", content: QUESTION },
      { role: "assistant", content: "Shall I connect GitHub?" },
      { role: "user", content: "yes" },
      { role: "assistant", content: "card" },
    ];
    expect(taskBefore(msgs, 3, { id: "github", name: "GitHub" })).toBe(QUESTION);
    const work = [
      { role: "user", content: "what's the weather in Seattle" },
      { role: "assistant", content: "Rainy." },
      { role: "user", content: "set up a GitHub webhook for my repo" },
      { role: "assistant", content: "card" },
    ];
    expect(taskBefore(work, 3, { id: "github", name: "GitHub" })).toBe("set up a GitHub webhook for my repo");
  });
});
