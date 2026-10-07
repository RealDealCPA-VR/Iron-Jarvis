/**
 * v1.306.0 — "Share with Build" on the Memory page's "What Jarvis knows about
 * you" card (idea from agent-personalizer, MIT — no code taken).
 *
 * One quiet switch per vendor CLI found on this PC, OFF by default. The
 * sentence under it says what is written, to which files, and that the CLI's
 * maker sees it — before the press. A hand-edited block is said on the row
 * with Overwrite / Keep yours. An older daemon (404) hides the row; a CLI not
 * found on this PC has no switch. Stable ids `#profile-share-<cli>`.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    puts: [] as { path: string; body: unknown }[],
    getResponses: {} as Record<string, unknown>,
    postResponses: {} as Record<string, unknown>,
    putResponse: null as unknown,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.gets.push(path);
    const r = H.getResponses[path];
    if (r instanceof Error) throw r;
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: unknown) => {
    H.posts.push({ path, body });
    const r = H.postResponses[path];
    if (r instanceof Error) throw r;
    return r ?? {};
  },
  put: async (path: string, body: unknown) => {
    H.puts.push({ path, body });
    const r = H.putResponse;
    if (r instanceof Error) throw r;
    return r ?? {};
  },
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { KnowsAboutYou } from "@/components/memory/KnowsAboutYou";
import { ProfileShareRow } from "@/components/memory/ProfileShareRow";
import { decodeShareView, fileLine, shareSentence } from "@/lib/profileShare";

const HOME = "C:\\Users\\dana\\.claude\\CLAUDE.md";
const WORK = "D:\\accounts\\work\\CLAUDE.md";

const cli = (over: Record<string, unknown>) => ({
  cli: "claude-code",
  label: "Claude Code",
  vendor: "Anthropic",
  available: true,
  on: false,
  file_name: "CLAUDE.md",
  files: [],
  targets: [{ path: HOME, account: null }],
  last_written: null,
  drift: false,
  accounts_known: true,
  chars: 0,
  omitted: 0,
  ...over,
});

const codexCli = (over: Record<string, unknown> = {}) =>
  cli({
    cli: "codex",
    label: "Codex",
    vendor: "OpenAI",
    file_name: "AGENTS.md",
    targets: [{ path: "C:\\Users\\dana\\.codex\\AGENTS.md", account: null }],
    ...over,
  });

const file = (over: Record<string, unknown> = {}) => ({
  path: HOME,
  account: null,
  exists: true,
  last_written: "2026-10-06T10:00:00+00:00",
  drift: null,
  held: false,
  created: true,
  error: null,
  ...over,
});

const VIEW = (clis: unknown[]) => ({ clis, limit: 4000 });

/** The GET has been CALLED is not the GET has SETTLED: the mock resolves (or
 *  rejects) on a later microtask, and an absence asserted before that passes
 *  whatever the component would have rendered. Flush one macrotask. */
async function settled(path: string) {
  await waitFor(() => expect(H.gets).toContain(path));
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

const OVERVIEW = {
  profile: { filled: true, enabled: true, about_line: "Goes by Dana Reyes.", tone: "", writing_style: "" },
  preferences: [{ id: "l1", text: "Prefers numbered steps", source: "preference", weight: 5, created_at: "2026-09-18" }],
  lessons: { total: 1, reflections: 0, by_source: { preference: 1 } },
  bases: [],
  working: {},
  history: { docs: 0, available: false },
  empty: false,
};

beforeEach(() => {
  H.gets.length = 0;
  H.posts.length = 0;
  H.puts.length = 0;
  H.getResponses = {};
  H.postResponses = {};
  H.putResponse = null;
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the wire shape and the words", () => {
  it("decodeShareView keeps valid rows and refuses junk", () => {
    expect(decodeShareView(null)).toBeNull();
    expect(decodeShareView({ nope: 1 })).toBeNull();
    const v = decodeShareView(VIEW([cli({}), { label: "no id" }, 7]))!;
    expect(v).toHaveLength(1);
    expect(v[0]).toMatchObject({ cli: "claude-code", available: true, on: false, fileName: "CLAUDE.md" });
    expect(v[0].targets).toEqual([{ path: HOME, account: null }]);
  });

  it("the sentence says what is written, where, and who sees it", () => {
    const [c] = decodeShareView(VIEW([cli({})]))!;
    expect(shareSentence(c)).toBe(
      "Writes your profile and the preferences you said or kept — nothing else Jarvis remembers — " +
        `into a marked block in ${HOME}. Claude Code reads it in every session, so Anthropic sees it when Claude Code runs.`,
    );
    const [x] = decodeShareView(VIEW([codexCli()]))!;
    expect(shareSentence(x)).toContain("so OpenAI sees it when Codex runs.");
  });

  it("names the Iron-Proxy account folders too", () => {
    const [c] = decodeShareView(
      VIEW([cli({ targets: [{ path: HOME, account: null }, { path: WORK, account: "Work Claude" }] })]),
    )!;
    expect(shareSentence(c)).toContain(`${HOME} and the same file in 1 Iron-Proxy account folder (Work Claude).`);
  });

  it("a file line for edited, removed, damaged, kept and failed files", () => {
    const f = (o: Record<string, unknown>) => decodeShareView(VIEW([cli({ on: true, files: [file(o)] })]))![0].files[0];
    expect(fileLine(f({}))).toBeNull();
    expect(fileLine(f({ drift: "edited" }))).toBe(`You edited the shared block in ${HOME}.`);
    expect(fileLine(f({ drift: "removed" }))).toBe(`You removed the shared block from ${HOME}.`);
    expect(fileLine(f({ drift: "broken" }))).toContain("markers in");
    expect(fileLine(f({ held: true }))).toBe(`You kept your own version in ${HOME}; Jarvis no longer updates it.`);
    expect(fileLine(f({ error: "could not write it (PermissionError)" }))).toBe(
      `Could not update ${HOME}: could not write it (PermissionError).`,
    );
  });
});

describe("Share with Build on the card", () => {
  it("an older daemon (404) shows no row at all", async () => {
    H.getResponses = { "/memory/overview": OVERVIEW };
    render(<KnowsAboutYou />);
    await screen.findByTestId("knows-about-you");
    await settled("/profile/share");
    expect(screen.queryByTestId("profile-share")).toBeNull();
    expect(document.getElementById("profile-share-claude-code")).toBeNull();
  });

  it("lives on the card; only CLIs found on this PC get a switch, off by default", async () => {
    H.getResponses = {
      "/memory/overview": OVERVIEW,
      "/profile/share": VIEW([cli({}), codexCli({ available: false })]),
    };
    render(<KnowsAboutYou />);
    await waitFor(() => expect(document.getElementById("profile-share-claude-code")).not.toBeNull());
    const card = screen.getByTestId("knows-about-you");
    expect(card.contains(screen.getByTestId("profile-share"))).toBe(true);
    expect(document.getElementById("profile-share-codex")).toBeNull();
    const sw = screen.getByRole("switch", { name: "Share my profile with Claude Code in Build" });
    expect(sw.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByTestId("profile-share-sentence-claude-code").textContent).toContain(
      "so Anthropic sees it when Claude Code runs.",
    );
  });

  it("no CLI found = no row", async () => {
    H.getResponses = { "/profile/share": VIEW([cli({ available: false }), codexCli({ available: false })]) };
    render(<ProfileShareRow />);
    await settled("/profile/share");
    expect(screen.queryByTestId("profile-share")).toBeNull();
  });

  it("the switch PUTs {cli, on} and shows the daemon's answer", async () => {
    H.getResponses = { "/profile/share": VIEW([cli({}), codexCli()]) };
    H.putResponse = VIEW([
      cli({ on: true, files: [file()], last_written: "2026-10-06T10:00:00+00:00" }),
      codexCli(),
    ]);
    render(<ProfileShareRow />);
    const sw = await screen.findByTestId("profile-share-switch-claude-code");
    fireEvent.click(sw);
    await waitFor(() => expect(sw.getAttribute("aria-checked")).toBe("true"));
    expect(H.puts).toEqual([{ path: "/profile/share", body: { cli: "claude-code", on: true } }]);
    expect(document.getElementById("profile-share-claude-code")!.textContent).toContain("written");
    expect(screen.getByTestId("profile-share-switch-codex").getAttribute("aria-checked")).toBe("false");
  });

  it("a refusal is said on the row", async () => {
    H.getResponses = { "/profile/share": VIEW([cli({})]) };
    H.putResponse = new H.FakeApiError("Claude Code is not installed on this PC, so there is nothing to share with.", 409);
    render(<ProfileShareRow />);
    fireEvent.click(await screen.findByTestId("profile-share-switch-claude-code"));
    const err = await screen.findByTestId("profile-share-error-claude-code");
    expect(err.textContent).toBe("Claude Code is not installed on this PC, so there is nothing to share with.");
  });

  it("an edited block says so, with Overwrite and Keep yours", async () => {
    H.getResponses = { "/profile/share": VIEW([cli({ on: true, drift: true, files: [file({ drift: "edited" })] })]) };
    H.postResponses["/profile/share/claude-code/overwrite"] = VIEW([cli({ on: true, files: [file()] })]);
    render(<ProfileShareRow />);
    const line = await screen.findByTestId("profile-share-drift-claude-code");
    expect(line.textContent).toContain(`You edited the shared block in ${HOME}.`);
    expect(line.textContent).toContain("Overwrite");
    expect(line.textContent).toContain("Keep yours");
    fireEvent.click(screen.getByTestId("profile-share-overwrite-claude-code"));
    await waitFor(() => expect(screen.queryByTestId("profile-share-drift-claude-code")).toBeNull());
    expect(H.posts).toEqual([{ path: "/profile/share/claude-code/overwrite", body: { path: HOME } }]);
  });

  it("Keep yours posts keep and the row then says the file is no longer updated", async () => {
    H.getResponses = { "/profile/share": VIEW([cli({ on: true, files: [file({ drift: "edited" })] })]) };
    H.postResponses["/profile/share/claude-code/keep"] = VIEW([cli({ on: true, files: [file({ held: true })] })]);
    render(<ProfileShareRow />);
    fireEvent.click(await screen.findByTestId("profile-share-keep-claude-code"));
    await waitFor(() =>
      expect(screen.getByTestId("profile-share-drift-claude-code").textContent).toContain("no longer updates it"),
    );
    expect(H.posts).toEqual([{ path: "/profile/share/claude-code/keep", body: { path: HOME } }]);
    expect(screen.queryByTestId("profile-share-keep-claude-code")).toBeNull();
    expect(screen.getByTestId("profile-share-overwrite-claude-code")).toBeTruthy();
  });

  it("switched off, a block that could not be taken out is still said (no Overwrite / Keep)", async () => {
    H.getResponses = { "/profile/share": VIEW([cli({ on: true, files: [file()] })]) };
    H.putResponse = VIEW([
      cli({ on: false, files: [file({ error: "could not change it (PermissionError)" })] }),
    ]);
    render(<ProfileShareRow />);
    const sw = await screen.findByTestId("profile-share-switch-claude-code");
    fireEvent.click(sw);
    const line = await screen.findByTestId("profile-share-drift-claude-code");
    expect(line.textContent).toBe(`Could not update ${HOME}: could not change it (PermissionError).`);
    expect(sw.getAttribute("aria-checked")).toBe("false");
    expect(screen.queryByTestId("profile-share-overwrite-claude-code")).toBeNull();
    expect(screen.queryByTestId("profile-share-keep-claude-code")).toBeNull();
  });
});
