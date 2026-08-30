import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * The curated tool gallery may never hand a parameter to a shell (finding 07).
 *
 * The gallery is posted VERBATIM to POST /tools/custom, and the daemon's
 * CommandTool (src/iron_jarvis/tools/dynamic.py `_render`) fills each {param}
 * by a PLAIN TEXTUAL substitution into one argv element, then runs the argv
 * with `shell=False`. That stops a value becoming a new argv WORD — it does
 * nothing once the program on the other end re-parses that word as code. Four
 * entries wrapped the placeholder in a PowerShell script string
 * (`powershell -NoProfile -Command "Get-ChildItem -Force '{path}'"`), so
 * path = "C:\x'; Remove-Item …; '" closed the literal and ran the rest — a
 * "list a folder" grant escalating to arbitrary execution, which is exactly
 * what the fail-closed permission model exists to prevent.
 *
 * MEASURED while fixing this (Windows PowerShell 5.1), because the obvious fix
 * is a trap: giving the value its OWN argv element
 * (`-Command Get-ChildItem -Force -LiteralPath <value>`) is STILL injectable —
 * powershell.exe strips the process-level quoting and rejoins the tail into one
 * script string, so `C:\x; Write-Output INJECTED` ran and `$(…)` expanded. The
 * only safe shape under this contract is a NATIVE program that never re-parses
 * its argv. So the invariant pinned here is not "quote it better", it is:
 *
 *   no gallery command may put a {placeholder} in front of an interpreter.
 *
 * Also pinned: a daemon that already stored the injectable definition is told
 * so (the saved-tool warning) and can replace it in one click (Update) — a fix
 * that only reaches fresh installs would leave every current user vulnerable.
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
    gets: [] as string[],
    posts: [] as { path: string; body: any }[],
    tools: [] as any[],
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    api.gets.push(path);
    if (path === "/tools/custom") return Promise.resolve({ tools: api.tools });
    if (path === "/mcp/servers") return Promise.resolve({ servers: [] });
    if (path === "/mcp/catalog") return Promise.resolve({ catalog: [] });
    return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    return Promise.resolve({ name: "ok" });
  },
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

// Imported AFTER the mocks so the page binds to the fakes. A Next page file may
// export nothing beyond its default, so the gallery is read back off the DOM
// (`data-suite-tool` / `data-argv` carry the exact argv the Add button posts).
const ToolsPage = (await import("@/app/tools/page")).default;

/* -------------------------------------------------------------------------- */
/*  The invariant, spelled out here rather than imported                       */
/* -------------------------------------------------------------------------- */

/** Programs that parse an argument as a script. */
const SHELL_PROGRAMS = new Set([
  "powershell",
  "pwsh",
  "cmd",
  "bash",
  "sh",
  "zsh",
  "wsl",
  "mshta",
  "cscript",
  "wscript",
  "node",
  "python",
  "python3",
]);

const PLACEHOLDER_RE = /\{([A-Za-z_][A-Za-z0-9_]*)\}/g;

function programOf(argv: string[]): string {
  return (argv[0] ?? "")
    .trim()
    .toLowerCase()
    .split(/[\\/]/)
    .pop()!
    .replace(/\.(exe|com|cmd|bat)$/, "");
}

/** The defect shape: a {placeholder} that reaches an interpreter. */
function handsValueToShell(argv: string[]): boolean {
  return (
    SHELL_PROGRAMS.has(programOf(argv)) &&
    argv.slice(1).some((element) => new RegExp(PLACEHOLDER_RE.source).test(element))
  );
}

function placeholdersOf(argv: string[]): string[] {
  const found = new Set<string>();
  for (const element of argv) {
    for (const m of element.matchAll(PLACEHOLDER_RE)) found.add(m[1]);
  }
  return [...found];
}

/**
 * Faithful mirror of CommandTool._render + execute's empty-drop:
 * ONE simultaneous textual pass per argv element, then elements that rendered
 * to "" are dropped. No quoting, no escaping — that is the whole point.
 */
function renderArgv(argv: string[], values: Record<string, string>): string[] {
  const names = Object.keys(values);
  if (names.length === 0) return [...argv];
  const re = new RegExp(
    "\\{(" + names.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")\\}",
    "g",
  );
  return argv
    .map((element) => element.replace(re, (_m, name: string) => values[name] ?? ""))
    .filter((element) => element !== "");
}

/** Values a model could pass. Each one BREAKS OUT of PowerShell quoting. */
const PAYLOADS = [
  "C:\\x'; Write-Output INJECTED; '",
  'C:\\x"; Write-Output INJECTED; "',
  "C:\\x; Write-Output INJECTED",
  "C:\\x$(Write-Output INJECTED)",
  // Not an attack — just a real customer folder, which the old quoting also
  // could not survive ("The string is missing the terminator").
  "C:\\Users\\O'Brien\\Documents",
];

function suiteCards(): { name: string; command: string[] }[] {
  return Array.from(document.querySelectorAll("[data-suite-tool]")).map((el) => ({
    name: el.getAttribute("data-suite-tool")!,
    command: JSON.parse(el.getAttribute("data-argv")!) as string[],
  }));
}

async function renderGallery() {
  render(<ToolsPage />);
  await waitFor(() => {
    expect(document.querySelectorAll("[data-suite-tool]").length).toBeGreaterThan(0);
  });
  return suiteCards();
}

const OLD_LIST_DIR = [
  "powershell",
  "-NoProfile",
  "-Command",
  "Get-ChildItem -Force '{path}'",
];

beforeEach(() => {
  api.gets = [];
  api.posts = [];
  api.tools = [];
});

afterEach(() => cleanup());

describe("the injection checker used below is not vacuous", () => {
  it("flags the exact shape this release removed, and clears the safe ones", () => {
    expect(handsValueToShell(OLD_LIST_DIR)).toBe(true);
    expect(
      handsValueToShell([
        "powershell",
        "-NoProfile",
        "-Command",
        "(Get-Content '{file}' -Raw | Measure-Object -Word).Words",
      ]),
    ).toBe(true);
    // argv into a native program: the value is data, whatever it contains.
    expect(handsValueToShell(["curl", "-s", "{url}"])).toBe(false);
    expect(handsValueToShell(["git", "-C", "{repo}", "status", "--short"])).toBe(false);
    // A shell with a CONSTANT script takes no untrusted input — not the defect.
    expect(
      handsValueToShell([
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-PSDrive -PSProvider FileSystem",
      ]),
    ).toBe(false);
  });
});

describe("the curated gallery", () => {
  it("never puts a parameter in front of an interpreter", async () => {
    const cards = await renderGallery();
    expect(cards.length).toBeGreaterThan(5);
    for (const card of cards) {
      expect(
        handsValueToShell(card.command),
        `${card.name} hands a parameter to ${programOf(card.command)}: ${card.command.join(" ")}`,
      ).toBe(false);
    }
  });

  it("keeps every parameter out of a quoted script fragment", async () => {
    const cards = await renderGallery();
    for (const card of cards) {
      for (const element of card.command) {
        if (!new RegExp(PLACEHOLDER_RE.source).test(element)) continue;
        // A quote around a placeholder means the value is inside a LITERAL that
        // some parser will close for it. Native programs need no quoting.
        expect(
          /['"`]/.test(element),
          `${card.name}: parameter sits inside a quoted fragment ${JSON.stringify(element)}`,
        ).toBe(false);
      }
    }
  });

  it("a value carrying quotes, ';' or '$(…)' cannot alter the executed argv", async () => {
    const cards = await renderGallery();
    for (const card of cards) {
      const names = placeholdersOf(card.command);
      // A command with no placeholder takes no untrusted input at all, so it
      // may legitimately be a fixed PowerShell script (disk_free).
      if (names.length === 0) continue;
      for (const payload of PAYLOADS) {
        const values = Object.fromEntries(names.map((n) => [n, payload]));
        const rendered = renderArgv(card.command, values);

        // Same number of argv elements, same program, and every element that
        // holds no placeholder is byte-identical: the value cannot add, remove
        // or alter a word of the command it was approved for.
        expect(rendered.length, `${card.name} lost/gained an argv element`).toBe(
          card.command.length,
        );
        expect(rendered[0]).toBe(card.command[0]);
        card.command.forEach((element, i) => {
          if (!new RegExp(PLACEHOLDER_RE.source).test(element)) {
            expect(rendered[i], `${card.name} element ${i} changed`).toBe(element);
          } else {
            // The payload lands VERBATIM in exactly its own element — nothing
            // escapes it, and nothing splits it out of it.
            expect(rendered[i].includes(payload)).toBe(true);
          }
        });
        // …and since argv[0] is not an interpreter, that element stays data.
        expect(SHELL_PROGRAMS.has(programOf(rendered))).toBe(false);
      }
    }
  });

  it("drops word_count rather than shipping an escape that only looks safe", async () => {
    const cards = await renderGallery();
    expect(cards.map((c) => c.name)).not.toContain("word_count");
    expect(document.body.textContent).not.toContain("Measure-Object -Word");
  });

  it("Add posts exactly the command the card shows, with every placeholder declared", async () => {
    const cards = await renderGallery();
    for (const card of cards) {
      fireEvent.click(screen.getByTitle(`Add "${card.name}"`));
      // ADD IS TWO STEPS SINCE v1.216.0: the grid button opens the consequence
      // preview and the dialog's Enable is what POSTs. The security invariant
      // below is unchanged and is the whole point of this file — what reaches
      // the daemon must still be exactly the argv the card shows.
      fireEvent.click(await screen.findByTestId("enable-confirm"));
      // The success note is set at the END of the handler (after the POST
      // resolves) — never wait on the POST itself.
      await waitFor(() => {
        expect(screen.getByText(`Tool "${card.name}" added.`)).toBeInTheDocument();
      });
      const posted = api.posts[api.posts.length - 1];
      expect(posted.path).toBe("/tools/custom");
      expect(posted.body.command).toEqual(card.command);
      expect(handsValueToShell(posted.body.command)).toBe(false);
      const declared = new Set(
        (posted.body.parameters as { name: string }[]).map((p) => p.name),
      );
      for (const name of placeholdersOf(card.command)) {
        expect(declared.has(name), `${card.name}: {${name}} is not a declared parameter`).toBe(
          true,
        );
      }
    }
  });
});

describe("a daemon that already stored the injectable definition", () => {
  const savedListDir = {
    name: "list_dir",
    description: "List a directory.",
    parameters: [
      { name: "path", type: "string", required: true, description: "Directory path." },
    ],
    command: OLD_LIST_DIR,
    timeout_seconds: 20,
    created_by: "you",
    created_at: new Date().toISOString(),
  };

  it("says so on the saved tool, and one click replaces it with the safe command", async () => {
    api.tools = [savedListDir];
    const cards = await renderGallery();
    const safe = cards.find((c) => c.name === "list_dir")!;

    // The stored record is flagged where the user can see and act on it.
    await waitFor(() => {
      expect(screen.getByText(/value goes into a shell/)).toBeInTheDocument();
    });

    // …and the gallery offers the replacement instead of a smug "Added".
    expect(screen.queryByTitle('Add "list_dir"')).toBeNull();
    fireEvent.click(screen.getByTitle('Replace the saved "list_dir" with this command'));
    // Through the preview, as every add now goes (v1.216.0). The dialog says
    // it is a replacement; what it POSTs is still the safe argv.
    fireEvent.click(await screen.findByTestId("enable-confirm"));

    await waitFor(() => {
      expect(screen.getByText('Tool "list_dir" updated.')).toBeInTheDocument();
    });
    const posted = api.posts[api.posts.length - 1];
    expect(posted.path).toBe("/tools/custom");
    expect(posted.body.name).toBe("list_dir");
    expect(posted.body.command).toEqual(safe.command);
    expect(handsValueToShell(posted.body.command)).toBe(false);
  });

  it("a saved tool that already matches is just Added — no nag, no warning", async () => {
    const cards = await renderGallery();
    const safe = cards.find((c) => c.name === "list_dir")!;
    cleanup();

    api.tools = [{ ...savedListDir, command: safe.command }];
    render(<ToolsPage />);

    // "Enabled" since v1.216.0 — the status chip carries an icon AND a word
    // so it is not colour-only (review, accessibility).
    await waitFor(() => {
      expect(screen.getByTestId("status-added")).toBeInTheDocument();
    });
    expect(
      screen.queryByTitle('Replace the saved "list_dir" with this command'),
    ).toBeNull();
    expect(screen.queryByText(/value goes into a shell/)).toBeNull();
  });
});
