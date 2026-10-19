/**
 * v1.356.0 lane 6D (G60): Microsoft 365 / Outlook can be connected.
 *
 * The Directory card "Microsoft 365 (Outlook, Calendar, OneDrive)" asks ONE
 * plain question (work or school / personal) and, once saved, offers "Sign in
 * to Microsoft": a Build pane opens running the app's own sign-in, which shows
 * a web address and a code.
 *
 * Driven through the REAL components (components/connections/AppSignIn) and
 * the REAL daemon rows: `fixtures/microsoft-365-row-v13560.json` is what the
 * daemon's GET /connectors sends before and after Connect (written by
 * tests/test_microsoft_365_v13560.py). Only lib/api is a fake (the house
 * pattern). Every waitFor waits for the thing it asserts.
 *
 * THE DIRECTORY MOUNT (last block): the v1.356.0 integrator applied the
 * lane's patch to DirectoryPage.tsx (ChoiceField for a field with `options`,
 * AppSignIn on a saved card with `signin`), so those pins are plain `it` now.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    posts: [] as { path: string; body: unknown }[],
    postAnswer: { id: "term_ms365" } as unknown,
    postError: null as Error | null,
    connectors: [] as Record<string, unknown>[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  sseUrl: (p: string) => p,
  wsUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  get: async (path: string) => {
    if (path === "/connectors") return { connectors: api.connectors, categories: ["Productivity"] };
    if (path === "/mcp/import/sources") return { sources: [] };
    if (path === "/mcp/servers") return { servers: [] };
    return {};
  },
  post: async (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    if (api.postError) throw api.postError;
    return api.postAnswer;
  },
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

const { AppSignIn, ChoiceField, PANE_OPENED_LINE, signInLine } = await import(
  "@/components/connections/AppSignIn"
);
const { launchAppSignIn, paneHref } = await import("@/lib/cliSignIn");

/** Read a source the CI runner checked out with CRLF (the v1.232.1 rule). */
function src(rel: string): string {
  return readFileSync(join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");
}

type Row = Record<string, unknown> & {
  id: string;
  name: string;
  fields: { name: string; label: string; help: string; optional: boolean; options?: { value: string; label: string; help?: string }[] }[];
  signin: { cli: string; label: string; account?: string } | null;
};
const ROWS = JSON.parse(src("__tests__/fixtures/microsoft-365-row-v13560.json")) as { before: Row; after: Row };

beforeEach(() => {
  api.posts = [];
  api.postAnswer = { id: "term_ms365" };
  api.postError = null;
  api.connectors = [];
});
afterEach(() => cleanup());

// --------------------------------------------------------------------------- //
// The daemon's rows, as the dashboard receives them
// --------------------------------------------------------------------------- //
describe("the daemon's Microsoft 365 row", () => {
  it("asks one plain question before Connect and offers a sign-in once saved", () => {
    expect(ROWS.before.name).toBe("Microsoft 365 (Outlook, Calendar, OneDrive)");
    expect(ROWS.before.signin).toBeNull();
    const field = ROWS.before.fields[0];
    expect(field.label).toBe("Which Microsoft account?");
    expect(field.options?.map((o) => o.label)).toEqual(["Work or school account", "Personal account"]);
    // Saved with the work answer ("common"): the sign-in for that answer.
    expect(ROWS.after.signin).toEqual({
      cli: "microsoft-365-signin-work",
      label: "Sign in to Microsoft",
      account: "Work or school account",
    });
  });
});

// --------------------------------------------------------------------------- //
// ChoiceField
// --------------------------------------------------------------------------- //
describe("ChoiceField", () => {
  function renderChoice(value = "", onChange = vi.fn()) {
    const f = ROWS.before.fields[0];
    render(
      <ChoiceField
        name={f.name}
        label={f.label}
        help={f.help}
        options={f.options ?? []}
        optional={f.optional}
        value={value}
        onChange={onChange}
      />,
    );
    return onChange;
  }

  it("names the question for a screen reader and answers by the label pressed", () => {
    const onChange = renderChoice();
    const group = screen.getByRole("group", { name: /Which Microsoft account\?/ });
    const radios = within(group).getAllByRole("radio");
    expect(radios).toHaveLength(2);
    expect(radios.every((r) => !(r as HTMLInputElement).checked)).toBe(true);
    // Each answer says what it means.
    expect(within(group).getByText("Outlook.com, Hotmail or Live.")).toBeTruthy();
    fireEvent.click(within(group).getByLabelText(/Personal account/));
    // The value sent is what the pack reads, never the words.
    expect(onChange).toHaveBeenCalledWith("consumers");
  });

  it("shows the answer the card holds", () => {
    renderChoice("common");
    expect((screen.getByLabelText(/Work or school account/) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText(/Personal account/) as HTMLInputElement).checked).toBe(false);
  });

  it("is one radio group per card, so arrow keys move between its answers only", () => {
    renderChoice();
    const names = new Set(screen.getAllByRole("radio").map((r) => (r as HTMLInputElement).name));
    expect(names.size).toBe(1);
  });
});

// --------------------------------------------------------------------------- //
// AppSignIn
// --------------------------------------------------------------------------- //
describe("AppSignIn", () => {
  const signin = ROWS.after.signin!;

  it("says what it needs and opens the app's own sign-in by NAME, with no account", async () => {
    render(<AppSignIn id="microsoft_365" signin={signin} />);
    expect(screen.getByText("It works once you sign in with your work or school account.")).toBeTruthy();
    fireEvent.click(screen.getByTestId("connector-signin-press-microsoft_365"));
    const hint = await screen.findByTestId("connector-signin-hint-microsoft_365");
    expect(api.posts).toEqual([{ path: "/terminals/launch", body: { cli: "microsoft-365-signin-work" } }]);
    expect(hint.textContent).toContain(PANE_OPENED_LINE);
    const link = within(hint).getByRole("link", { name: "Open the Build terminal" });
    expect(link.getAttribute("href")).toBe("/terminals?focus=term_ms365");
  });

  it("says the daemon's own sentence when the pane could not open, and offers the press again", async () => {
    api.postError = new api.FakeApiError("terminal session cap reached (5)", 429);
    render(<AppSignIn id="microsoft_365" signin={signin} />);
    fireEvent.click(screen.getByTestId("connector-signin-press-microsoft_365"));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("terminal session cap reached (5)");
    expect(screen.getByTestId("connector-signin-press-microsoft_365")).toBeTruthy();
  });

  it("opens the Build page when the daemon did not name the pane", async () => {
    api.postAnswer = {};
    render(<AppSignIn id="microsoft_365" signin={signin} />);
    fireEvent.click(screen.getByTestId("connector-signin-press-microsoft_365"));
    const hint = await screen.findByTestId("connector-signin-hint-microsoft_365");
    expect(within(hint).getByRole("link").getAttribute("href")).toBe("/terminals");
  });

  it("words a personal account and an unnamed one", () => {
    expect(signInLine({ account: "Personal account" })).toBe("It works once you sign in with your personal account.");
    expect(signInLine({})).toBe("It works once you sign in.");
  });

  it("launchAppSignIn never sends an account (an app sign-in is not an AI CLI)", async () => {
    await expect(launchAppSignIn("microsoft-365-signin-personal")).resolves.toBe("term_ms365");
    expect(api.posts).toEqual([{ path: "/terminals/launch", body: { cli: "microsoft-365-signin-personal" } }]);
    expect(paneHref(null)).toBe("/terminals");
  });
});

// --------------------------------------------------------------------------- //
// The look stays the app's
// --------------------------------------------------------------------------- //
describe("AppSignIn's look", () => {
  const file = src("components/connections/AppSignIn.tsx");
  const code = file.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

  it("uses theme tokens, whole pixels, and a spinner that stops under reduced motion", () => {
    expect(code).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(code).not.toMatch(/\brgba?\(/);
    expect(code).not.toMatch(/text-\[\d+\.\d+px\]/);
    expect(code).not.toMatch(/\b(border|shadow)-(?!none)/);
    expect(code).toContain("motion-reduce:animate-none");
  });

  it("reuses the calm action the Accounts rows' Sign in uses", () => {
    expect(code).toContain('import { CALM_ACTION } from "@/components/connections/endpointChip"');
    expect(code).toContain('import { launchAppSignIn, paneHref } from "@/lib/cliSignIn"');
  });
});

// --------------------------------------------------------------------------- //
// The Directory mount (lane 6A's DirectoryPage + the 6D patch)
// (v1.356.0 integrate: the mount patch is applied; these are plain pins.)
// --------------------------------------------------------------------------- //
describe("the Directory card", () => {
  async function renderDirectory(row: Row) {
    api.connectors = [
      {
        category: "Productivity",
        glyph: "",
        blurb: "Your Outlook email, calendar and OneDrive files.",
        unlocks: "Ask what's on your calendar.",
        scopes: [],
        docs_url: "",
        provider: "",
        tools_loaded: row.connected ? 24 : 0,
        tool_names: [],
        needs: "",
        package_note: "",
        href: "",
        action: "",
        ...row,
      },
    ];
    const DirectoryPage = (await import("@/components/settings/pages/DirectoryPage")).default;
    render(<DirectoryPage />);
    return screen.findByTestId("connector-card-microsoft_365", {}, { timeout: 10_000 });
  }

  it("asks the plain choice in the Connect form, and Connect waits for an answer", async () => {
    const card = await renderDirectory(ROWS.before);
    fireEvent.click(within(card).getByRole("button", { name: /Connect/ }));
    const group = await within(card).findByRole("group", { name: /Which Microsoft account\?/ });
    const submit = within(card).getAllByRole("button", { name: /Connect/ }).at(-1)!;
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(group).getByLabelText(/Personal account/));
    await waitFor(() => expect((submit as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(submit);
    await waitFor(() =>
      expect(api.posts).toEqual([
        { path: "/connectors/microsoft_365/connect", body: { values: { MS365_MCP_TENANT_ID: "consumers" } } },
      ]),
    );
  });

  it("offers Sign in to Microsoft on the saved card, opening the sign-in pane", async () => {
    const card = await renderDirectory(ROWS.after);
    const press = await within(card).findByTestId("connector-signin-press-microsoft_365");
    expect(press.textContent).toContain("Sign in to Microsoft");
    fireEvent.click(press);
    await within(card).findByTestId("connector-signin-hint-microsoft_365");
    expect(api.posts).toEqual([{ path: "/terminals/launch", body: { cli: "microsoft-365-signin-work" } }]);
  });
});
