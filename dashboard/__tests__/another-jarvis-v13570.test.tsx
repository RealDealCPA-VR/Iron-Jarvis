/**
 * v1.357.0 (G61) — "Another computer running Jarvis" in Agents › Your team.
 *
 * ACCEPTANCE (as a new user would check it): on two PCs, the user registers
 * the second Jarvis by address and token in Agents › Your team, "@desk do X"
 * runs there, and the answer comes home to the chat. The two-PC run itself
 * is pinned on the daemon (tests/test_another_jarvis_v13570.py, two real app
 * factories); these pins drive the REAL card inside the REAL Remote agents
 * section (what Your team renders), with only lib/api mocked:
 *
 *  - off: what it is, a name for the other computer, ONE primary (Turn on);
 *  - Turn on POSTs the enable route and shows the token ONCE with the address
 *    and the steps for the other computer; a later read never shows a token;
 *  - the reachability sentence the daemon gives is shown as is (nothing is
 *    opened here); Rotate mints again; Turn off is two presses;
 *  - a failed answer back is named; an older Iron Jarvis (404) shows nothing;
 *    a failed read is the one LoadFailedLine;
 *  - the connect form says when an address is another Jarvis and shows that
 *    the answers come back (and what reaching THIS computer takes);
 *  - the look stays the calm one: tone tokens only, whole-pixel sizes, no
 *    retired words, the door path equal to the daemon's.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";

const api = vi.hoisted(() => ({
  posts: [] as { path: string; body: unknown }[],
  gets: [] as string[],
  door: {} as Record<string, unknown>,
  doorError: null as null | { status: number; message: string },
  enableAnswer: null as null | Record<string, unknown>,
  addAnswer: {} as Record<string, unknown>,
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
  return {
    ApiError,
    API_BASE: "http://test",
    ijToken: () => "tok-1",
    get: (p: string) => {
      api.gets.push(p);
      if (p === "/agents/task-door") {
        if (api.doorError) return Promise.reject(new ApiError(api.doorError.message, api.doorError.status));
        return Promise.resolve(api.door);
      }
      return Promise.resolve({});
    },
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    post: (p: string, body?: unknown) => {
      api.posts.push({ path: p, body });
      if (p === "/agents/task-door/enable") return Promise.resolve(api.enableAnswer ?? {});
      if (p === "/agents/task-door/disable")
        return Promise.resolve({ enabled: false, caller: "laptop", url: "", reachable: null });
      if (p === "/agents/remote") return Promise.resolve(api.addAnswer);
      if (p.endsWith("/inbound/enable"))
        return Promise.resolve({
          token: "inbound_tok_NEVER_COPIED",
          url: "http://a-pc:8787/agents/remote/desk/inbound",
          reachable: { host_allowed: true, note: "" },
        });
      return Promise.resolve({});
    },
  };
});

import { RemoteAgentsSection, isTaskDoorUrl } from "@/components/agents/SetupCard";
import {
  CARD_TITLE,
  OFF_EXPLAINER,
  TASK_DOOR_PATH,
  TOKEN_ONCE,
  addThisComputerSteps,
  replyFailedLine,
  decodeDoor,
} from "@/lib/anotherJarvis";

const DOOR_URL = `http://192.168.1.20:8787${TASK_DOOR_PATH}`;
const REACH_NOTE =
  "Iron Jarvis on this computer currently accepts requests addressed to 127.0.0.1 or localhost only, so the other computer cannot reach it yet.";

function section() {
  return render(
    <RemoteAgentsSection
      remotes={[]}
      faces={{}}
      facesSupported={false}
      onChanged={() => {}}
      onFaceChanged={() => {}}
    />,
  );
}

const ON = {
  enabled: true,
  caller: "laptop",
  url: DOOR_URL,
  last_task_at: null,
  last_reply: null,
  reachable: { host_allowed: false, note: REACH_NOTE },
};

let writeText: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.posts = [];
  api.gets = [];
  api.door = { enabled: false, caller: "another computer", url: "", reachable: null };
  api.doorError = null;
  api.enableAnswer = { ...ON, token: "door_token_SHOWN_ONCE_123" };
  api.addAnswer = { name: "desk", inbound_enabled: false };
  writeText = vi.fn(() => Promise.resolve());
  Object.assign(navigator, { clipboard: { writeText } });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Another computer running Jarvis — the card in Your team", () => {
  it("off: says what it is, asks for a name, and has ONE primary (Turn on)", async () => {
    section();
    const card = await screen.findByTestId("another-jarvis");
    expect(within(card).getByRole("heading", { name: CARD_TITLE })).toBeTruthy();
    await waitFor(() => expect(within(card).getByText(OFF_EXPLAINER)).toBeTruthy());
    expect(within(card).getByLabelText("What do you call the other computer?")).toBeTruthy();
    const press = within(card).getByRole("button", { name: "Turn on" });
    expect(press.className).toMatch(/btn-accent/);
    expect(within(card).queryByTestId("another-jarvis-minted")).toBeNull();
    expect(api.gets).toContain("/agents/task-door");
  });

  it("Turn on posts the name, shows the token ONCE with the address and the other computer's steps", async () => {
    section();
    const card = await screen.findByTestId("another-jarvis");
    const field = await within(card).findByLabelText("What do you call the other computer?");
    fireEvent.change(field, { target: { value: "laptop" } });
    fireEvent.click(within(card).getByRole("button", { name: "Turn on" }));
    const minted = await within(card).findByTestId("another-jarvis-minted");
    expect(api.posts).toEqual([{ path: "/agents/task-door/enable", body: { name: "laptop" } }]);
    expect(minted.textContent).toContain(TOKEN_ONCE);
    expect(minted.textContent).toContain("door_token_SHOWN_ONCE_123");
    expect(within(minted).getByTestId("another-jarvis-steps").textContent).toBe(
      addThisComputerSteps("laptop"),
    );
    expect(addThisComputerSteps("laptop")).toMatch(/http-task/);
    expect(addThisComputerSteps("laptop")).toMatch(/Connect a remote agent/);
    expect(within(card).getByTestId("another-jarvis-url").textContent).toBe(DOOR_URL);
    expect(within(card).getByTestId("another-jarvis-on").textContent).toMatch(/Tasks from laptop run here/);
    // the daemon's reachability sentence, shown as is — nothing opened here
    expect(within(card).getByTestId("another-jarvis-reach-note").textContent).toBe(REACH_NOTE);
    // Copy token says Copied only once the clipboard took it
    fireEvent.click(within(minted).getByRole("button", { name: "Copy token" }));
    await waitFor(() => expect(within(minted).getByRole("button", { name: "Copied" })).toBeTruthy());
    expect(writeText).toHaveBeenCalledWith("door_token_SHOWN_ONCE_123");
    // Done hides it, and nothing brings it back
    fireEvent.click(within(minted).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(within(card).queryByTestId("another-jarvis-minted")).toBeNull());
    expect(card.textContent).not.toContain("door_token_SHOWN_ONCE_123");
  });

  it("a read of an ON door never shows a token, and Rotate mints a new one", async () => {
    api.door = { ...ON, token: "must_never_render" };
    section();
    const card = await screen.findByTestId("another-jarvis");
    await within(card).findByTestId("another-jarvis-on");
    expect(card.textContent).not.toContain("must_never_render");
    expect(within(card).getByTestId("another-jarvis-on").textContent).toMatch(/No task has arrived yet/);
    api.enableAnswer = { ...ON, token: "rotated_token_XYZ" };
    fireEvent.click(within(card).getByTestId("another-jarvis-rotate"));
    const minted = await within(card).findByTestId("another-jarvis-minted");
    expect(minted.textContent).toContain("rotated_token_XYZ");
    expect(api.posts).toEqual([{ path: "/agents/task-door/enable", body: { name: "" } }]);
  });

  it("Turn off is two presses and goes back to the off form", async () => {
    api.door = { ...ON };
    section();
    const card = await screen.findByTestId("another-jarvis");
    const off = await within(card).findByRole("button", { name: "Turn off" });
    fireEvent.click(off);
    expect(api.posts).toEqual([]);
    fireEvent.click(within(card).getByRole("button", { name: "Confirm?" }));
    await within(card).findByRole("button", { name: "Turn on" });
    expect(api.posts).toEqual([{ path: "/agents/task-door/disable", body: undefined }]);
    expect(within(card).queryByTestId("another-jarvis-url")).toBeNull();
  });

  it("names a failed answer back, and says when the last answer arrived", async () => {
    api.door = {
      ...ON,
      last_task_at: new Date(Date.now() - 120_000).toISOString(),
      last_reply: { ok: false, detail: "the other computer could not be reached" },
    };
    section();
    const card = await screen.findByTestId("another-jarvis");
    const failed = await within(card).findByTestId("another-jarvis-reply-failed");
    expect(failed.textContent).toBe(
      "The last answer could not be sent back: the other computer could not be reached.",
    );
    expect(failed.className).toMatch(/text-tone-warn/);
    expect(within(card).getByTestId("another-jarvis-on").textContent).toMatch(/Last task 2m ago\./);
    // the ok case reads as sent, with no warning line
    expect(replyFailedLine(decodeDoor({ ...ON, last_reply: { ok: true } }))).toBe("");
  });

  it("an older Iron Jarvis (404) shows nothing; a failed read is one quiet line", async () => {
    api.doorError = { status: 404, message: "Not Found" };
    const { unmount } = section();
    await waitFor(() => expect(api.gets).toContain("/agents/task-door"));
    await waitFor(() => expect(screen.queryByTestId("another-jarvis")).toBeNull());
    unmount();
    api.doorError = { status: 500, message: "boom" };
    section();
    const line = await screen.findByTestId("another-jarvis-load-failed");
    expect(line.textContent).toMatch(/This computer's task door couldn't load\./);
    expect(screen.queryByRole("button", { name: "Turn on" })).toBeNull();
  });
});

describe("the connect form knows another Jarvis", () => {
  it("an address that is a task door says so; the answer says its replies come back", async () => {
    expect(isTaskDoorUrl(DOOR_URL)).toBe(true);
    expect(isTaskDoorUrl(`${DOOR_URL}/`)).toBe(true);
    expect(isTaskDoorUrl("http://192.168.1.20:8080/run")).toBe(false);
    expect(isTaskDoorUrl("not a url")).toBe(false);
    api.addAnswer = {
      name: "desk",
      inbound_enabled: true,
      another_jarvis: true,
      reachable: { host_allowed: false, note: "Iron Jarvis on this computer currently accepts requests addressed to 127.0.0.1 or localhost only, so a remote on another machine cannot reach it yet." },
    };
    section();
    const form = await screen.findByTestId("connect-remote-form");
    fireEvent.change(within(form).getByLabelText("Remote agent name"), { target: { value: "desk" } });
    fireEvent.change(within(form).getByLabelText("Base URL"), { target: { value: DOOR_URL } });
    expect(within(form).getByTestId("connect-remote-jarvis-hint").textContent).toMatch(
      /another computer running Iron Jarvis/,
    );
    fireEvent.change(within(form).getByLabelText("Bearer secret"), { target: { value: "door-token" } });
    fireEvent.click(within(form).getByRole("button", { name: /Connect remote/ }));
    await within(form).findByText(/"@desk" in a chat hands it a task/);
    expect(api.posts.find((p) => p.path === "/agents/remote")?.body).toMatchObject({
      name: "desk",
      base_url: DOOR_URL,
      kind: "http-task",
      token: "door-token",
    });
    expect(within(form).getByTestId("connect-remote-reach-note").textContent).toMatch(
      /cannot reach it yet/,
    );
  });

  it("rotating another Jarvis's answer token has nothing to copy (it rides with each task)", async () => {
    render(
      <RemoteAgentsSection
        remotes={[
          {
            name: "desk",
            base_url: DOOR_URL,
            kind: "http-task",
            enabled: true,
            has_credential: true,
            inbound_enabled: true,
            inbound_url: "http://a-pc:8787/agents/remote/desk/inbound",
          } as never,
        ]}
        faces={{}}
        facesSupported={false}
        onChanged={() => {}}
        onFaceChanged={() => {}}
      />,
    );
    fireEvent.click(await screen.findByTestId("inbound-rotate"));
    const minted = await screen.findByTestId("inbound-minted");
    expect(minted.textContent).toMatch(/There is nothing to copy/);
    expect(minted.textContent).not.toContain("inbound_tok_NEVER_COPIED");
    expect(api.posts.map((p) => p.path)).toContain("/agents/remote/desk/inbound/enable");
  });

  it("an ordinary remote keeps today's words and shows no Jarvis hint", async () => {
    section();
    const form = await screen.findByTestId("connect-remote-form");
    fireEvent.change(within(form).getByLabelText("Remote agent name"), { target: { value: "hermes" } });
    fireEvent.change(within(form).getByLabelText("Base URL"), {
      target: { value: "http://192.168.1.50:8080/run" },
    });
    expect(within(form).queryByTestId("connect-remote-jarvis-hint")).toBeNull();
    fireEvent.click(within(form).getByRole("button", { name: /Connect remote/ }));
    await within(form).findByText('"hermes" is connected. Jarvis can hand it work now.');
    expect(within(form).queryByTestId("connect-remote-reach-note")).toBeNull();
  });
});

describe("the look and the words stay the calm ones", () => {
  const root = path.resolve(__dirname, "..");
  const read = (rel: string) => readFileSync(path.join(root, rel), "utf8").replace(/\r\n/g, "\n");
  const card = read("components/agents/AnotherJarvisCard.tsx");
  const words = read("lib/anotherJarvis.ts");

  it("tone tokens only, whole-pixel sizes", () => {
    for (const src of [card, words]) {
      expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b(?![\w-])/);
      expect(src).not.toMatch(/\brgba?\(/);
      expect(src).not.toMatch(/text-\[\d+\.\d+px\]/);
    }
  });

  it("no retired word in anything the user reads", () => {
    const prose = [
      OFF_EXPLAINER,
      CARD_TITLE,
      TOKEN_ONCE,
      addThisComputerSteps("laptop"),
      replyFailedLine(decodeDoor({ ...ON, last_reply: { ok: false, detail: "x" } })),
    ].join(" ");
    expect(prose).not.toMatch(/\b(daemon|job|jobs|mission|pack|mock|pane|connector|assignment)\b/i);
  });

  it("the steps name the tab the connect form lives on, by its real label", () => {
    // Agents › Your team opens on the roster; the connect form (and this
    // tile) sit on the "New & manage" tab — the steps must say so.
    const modal = read("components/agents/AgentsModal.tsx");
    const label = /tabBtn\("manage", "([^"]+)"\)/.exec(modal)?.[1];
    expect(label).toBeTruthy();
    expect(addThisComputerSteps("laptop")).toContain(
      `Agents, Your team, ${label}, Connect a remote agent`,
    );
  });

  it("the door path is the daemon's own", () => {
    const auth = readFileSync(path.resolve(root, "../src/iron_jarvis/daemon/auth.py"), "utf8");
    expect(auth).toContain(`TASK_DOOR_PATH = "${TASK_DOOR_PATH}"`);
  });
});
