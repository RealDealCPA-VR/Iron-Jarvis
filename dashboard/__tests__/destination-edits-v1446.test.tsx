import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * v1.446.0 — editing a notification destination only changes what you changed
 * (ease-of-use review item 13). The daemon's half is
 * tests/test_destination_edits_v1446.py; this file pins the page's half:
 * Edit ticks the SAVED alert choice (it used to tick everything, so a save
 * turned approval asks back on) and always sends it; the email form has the
 * two-way boxes chat sends people to ("Reading mail server", "Listen for
 * incoming mail", "Allowed senders") and a blank one keeps what is saved;
 * Add says it is new (a name in use is then refused, never replaced) while
 * Edit does not; the name rule is said beside the name box.
 */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    responses: {} as Record<string, unknown>,
    posts: [] as Array<{ path: string; body: unknown }>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    return Promise.resolve(r);
  },
  post: (path: string, body: unknown) => {
    api.posts.push({ path, body });
    if (path === "/comm/channels") return Promise.resolve({ added: true, two_way_note: "" });
    return Promise.resolve({ ok: true });
  },
  patch: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {actions}
    </div>
  ),
}));

import ChannelsPage from "@/components/settings/pages/NotificationsPage";

// The daemon's own field list (daemon/app.py _CHANNEL_TYPE_FIELDS["email"]).
const EMAIL_FIELDS = [
  { key: "host", label: "SMTP host", secret: false },
  { key: "port", label: "SMTP port", secret: false },
  { key: "username", label: "Username", secret: false },
  { key: "password", label: "Password / app password", secret: true },
  { key: "from_addr", label: "From address", secret: false },
  { key: "to_addr", label: "Send to", secret: false },
  { key: "imap_host", label: "IMAP host", secret: false },
  { key: "imap_port", label: "IMAP port", secret: false },
  { key: "inbound_enabled", label: "Listen for incoming messages", secret: false, type: "bool" },
  { key: "allowed_senders", label: "Allowlist (email addresses)", secret: false },
];
const DISCORD_FIELDS = [{ key: "webhook_url", label: "Webhook URL", secret: false }];

function emailRow(over: Record<string, unknown> = {}) {
  return {
    name: "inbox",
    type: "email",
    builtin: false,
    last_test_ok: true,
    events: ["review.requested", "session.completed"],
    inbound_enabled: true,
    chat_enabled: false,
    allowed_senders_count: 2,
    saved_fields: [
      "host", "port", "username", "password", "from_addr", "to_addr",
      "imap_host", "imap_port", "inbound_enabled", "allowed_senders",
    ],
    values: {
      host: "smtp.example.com",
      port: "587",
      username: "me@example.com",
      from_addr: "me@example.com",
      to_addr: "me@example.com",
      imap_host: "imap.example.com",
      imap_port: "993",
    },
    two_way_note: null,
    ...over,
  };
}

function seed(rows: unknown[] = [emailRow()]) {
  api.responses["/comm/channels"] = { channels: rows };
  api.responses["/comm/channel-types"] = {
    types: [
      { type: "email", fields: EMAIL_FIELDS, manifest: null, manifest_help: null },
      { type: "discord", fields: DISCORD_FIELDS, manifest: null, manifest_help: null },
    ],
  };
}

afterEach(() => {
  cleanup();
  api.responses = {};
  api.posts = [];
});

async function openEdit(name = "inbox") {
  render(<ChannelsPage />);
  fireEvent.click(await screen.findByTestId(`channel-edit-${name}`)); // v1.447.0: the pencil (was its title)
  await screen.findByText(/a blank box keeps what/i);
}

function savedConfig(): Record<string, unknown> {
  const save = api.posts.find((p) => p.path === "/comm/channels");
  expect(save).toBeTruthy();
  return (save!.body as { config: Record<string, unknown> }).config;
}

async function save(button: RegExp) {
  fireEvent.click(screen.getAllByRole("button", { name: button }).at(-1)!);
  await waitFor(() => expect(api.posts.some((p) => p.path === "/comm/channels")).toBe(true));
}

function box(label: string): HTMLInputElement {
  return screen.getByLabelText(label) as HTMLInputElement;
}

describe("Edit keeps the saved alert choice", () => {
  it("ticks only the saved alerts, and a save sends exactly them", async () => {
    seed();
    await openEdit();
    const asks = box("An agent asks permission to use a tool");
    expect(asks.checked).toBe(false);
    expect(box("A review needs you").checked).toBe(true);
    await save(/save changes/i);
    expect(savedConfig().events).toEqual(["review.requested", "session.completed"]);
  });

  it("everything ticked on an edit is sent as an empty list (everything)", async () => {
    seed([emailRow({ events: [] })]);
    await openEdit();
    await save(/save changes/i);
    expect(savedConfig().events).toEqual([]);
  });

  it("an edit's choice does not ride into the next Add", async () => {
    seed();
    await openEdit();
    fireEvent.click(screen.getByRole("button", { name: /^Cancel$/ }));
    fireEvent.click(screen.getByRole("button", { name: /Add destination/ }));
    fireEvent.click(await screen.findByText("Discord"));
    fireEvent.change(box("Destination name"), { target: { value: "team" } });
    fireEvent.change(box("Webhook URL"), { target: { value: "https://discord.com/api/webhooks/1/example" } });
    await save(/Add destination/);
    expect(savedConfig()).not.toHaveProperty("events");
  });
});

describe("the email form has the two-way boxes", () => {
  it("shows the reading server, its port, listening and the allowed senders, in plain words", async () => {
    seed();
    await openEdit();
    expect(box("Reading mail server").value).toBe("imap.example.com");
    expect(box("Reading port").value).toBe("993");
    expect(box("Listen for incoming mail").checked).toBe(true);
    const senders = box("Allowed senders");
    expect(senders.value).toBe("");
    expect(senders.placeholder).toBe("2 saved — leave blank to keep");
  });

  it("a save that changes nothing keeps them (blank senders, listening as saved)", async () => {
    seed();
    await openEdit();
    await save(/save changes/i);
    const config = savedConfig();
    expect(config.imap_host).toBe("imap.example.com");
    expect(config.imap_port).toBe("993");
    expect(config.inbound_enabled).toBe("true");
    expect(config.allowed_senders).toBe("");
    expect(config).not.toHaveProperty("clear");
    expect(config).not.toHaveProperty("new_destination");
  });
});

describe("names", () => {
  it("Add says the destination is new; Edit never does", async () => {
    seed([]);
    render(<ChannelsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Add destination/ }));
    fireEvent.click(await screen.findByText("Discord"));
    fireEvent.change(box("Destination name"), { target: { value: "phone" } });
    fireEvent.change(box("Webhook URL"), { target: { value: "https://discord.com/api/webhooks/1/example" } });
    await save(/Add destination/);
    expect(savedConfig().new_destination).toBe("true");
  });

  it("the rule is said beside the name box, and a bad name is called out", async () => {
    seed([]);
    render(<ChannelsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Add destination/ }));
    expect(screen.getByText(/Use letters, numbers, - and _, starting with a letter\./)).toBeTruthy();
    expect(screen.queryByTestId("channel-name-rule")).toBeNull();
    fireEvent.change(box("Destination name"), { target: { value: "my phone" } });
    expect(screen.getByTestId("channel-name-rule").textContent).toMatch(/That name won.t work/);
    fireEvent.change(box("Destination name"), { target: { value: "my-phone" } });
    expect(screen.queryByTestId("channel-name-rule")).toBeNull();
  });
});
