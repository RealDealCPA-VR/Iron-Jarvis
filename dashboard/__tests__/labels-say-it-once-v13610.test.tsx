/**
 * v1.361.0 lane 11C ("labels say it once"): four of the final hunt's
 * regressions — things that worked at v1.359.0 and broke in v1.360.0 —
 * closed without losing what v1.360.0 fixed. Every assertion drives the REAL
 * component or helper; only the network is a double.
 *
 *  (1) The approval chip's menu is placed again when its rows' words change
 *      while it is open. The reading sentence is asked for when the user
 *      reaches for the chip (v1.360.0), so a tap opens the menu before the
 *      answer lands; the longer "only in the folders you chose" rows then
 *      wrap to more lines, and a menu hung above the chip on its OLD height
 *      grew past the window's top with no way to scroll to the cut rows.
 *  (2) A substitute named by its machine says the machine once: "answered by
 *      Spark B (you asked for Claude Code) · local", never "… · on Spark B".
 *  (3) OpenCode reaching the user's server through its own "<server>/fleet"
 *      alias keeps the program's name on the run page and under "Ran on"
 *      ("OpenCode · Your model server", the model list's words).
 *  (4) An Ollama-slot "fleet" pick is never named after another model's row
 *      (a detected Ollama row's `name` is its model id, "llama3").
 *
 * Both directions for every matcher: what changed, and the neighbour that
 * must not.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  release: null as null | ((body: unknown) => void),
  calls: [] as string[],
}));

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  API_BASE: "",
  // GET /settings/access answers when the test says so (a read that loses
  // the race with the user's tap); anything else is not part of this pin.
  get: (path: string) => {
    H.calls.push(path);
    if (path.split("?")[0] !== "/settings/access") return Promise.reject(new Error(`unmocked GET ${path}`));
    return new Promise((resolve) => {
      H.release = resolve;
    });
  },
  put: async () => ({}),
  post: async () => ({}),
}));

import { PermissionChip } from "@/components/chat/PermissionChip";
import { TurnReceipt, type TurnRoute } from "@/components/chat/TurnReceipt";
import { runModelWords } from "@/components/sessions/runPageWords";
import { answeredModelName } from "@/lib/answeredModel";
import { isOwnServerSlot, machineName, modelPickWords, modelRowName } from "@/lib/onboarding";
import { ranOnWords } from "@/lib/ranOnWords";
import { __resetReadFacts } from "@/lib/readFacts";

beforeEach(() => {
  H.release = null;
  H.calls = [];
  __resetReadFacts();
});
afterEach(() => cleanup());

/* ------------------------------------------------------------------ (1) */

/** A landscape phone: 844 x 330, the chip low in the composer. */
const VIEW = { width: 844, height: 330 };
const CHIP = { top: 270, bottom: 300, left: 120, right: 150, width: 30, height: 30 };
const CHOSEN = { limited: true, folders: ["D:\\Clients"], chosen: ["D:\\Clients"], install: false };
const OPEN = { limited: false, folders: [], chosen: [], install: false };

/** Stubs the chip's and the menu's boxes: the menu is 226px tall with the
 *  "anywhere" rows and 274px once the longer chosen-folders sentence wraps
 *  (one more line per row) — the hunter's measured shape. Restores the
 *  window size afterwards. */
function withLayout(run: () => Promise<void>) {
  const spy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
    this: HTMLElement,
  ) {
    const base = { x: 0, y: 0, top: 0, left: 0, bottom: 0, right: 0, width: 0, height: 0, toJSON() {} };
    if (this.dataset.testid === "permission-chip") return { ...base, ...CHIP } as DOMRect;
    if (this.dataset.testid === "permission-menu") {
      const h = /folders you chose/.test(this.textContent ?? "") ? 274 : 226;
      return { ...base, width: 288, height: h, bottom: h, right: 288 } as DOMRect;
    }
    return base as DOMRect;
  });
  const prevW = window.innerWidth;
  const prevH = window.innerHeight;
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: VIEW.width });
  Object.defineProperty(window, "innerHeight", { configurable: true, writable: true, value: VIEW.height });
  return run().finally(() => {
    spy.mockRestore();
    Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: prevW });
    Object.defineProperty(window, "innerHeight", { configurable: true, writable: true, value: prevH });
  });
}

/** Reach for the chip and tap it while GET /settings/access is still out. */
async function tapBeforeTheReadLands() {
  render(
    <div>
      <PermissionChip id="chat-approval-mode" value="approve_for_me" onChange={() => {}} />
      <button type="button" data-testid="elsewhere">
        elsewhere
      </button>
    </div>,
  );
  const chip = screen.getByTestId("permission-chip");
  fireEvent.pointerEnter(chip);
  fireEvent.click(chip);
  // The read starts a microtask after the reach (lib/readFacts).
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(H.release).not.toBeNull();
  return screen.getByTestId("permission-menu");
}

async function land(reads: unknown) {
  await act(async () => {
    H.release?.({ reads });
    await new Promise((r) => setTimeout(r, 0));
  });
}

describe("(1) the approval menu is placed again when its rows' words change while it is open", () => {
  it("rows that grow after it opened get the cap a fresh opening would give, without reopening", async () => {
    await withLayout(async () => {
      const menu = await tapBeforeTheReadLands();
      // Opened on the short rows: it fits above the chip, no cap.
      expect(menu.dataset.side).toBe("above");
      expect(menu.style.bottom).toBe(`${VIEW.height - CHIP.top + 6}px`);
      expect(menu.style.maxHeight).toBe("");
      await land(CHOSEN);
      const now = screen.getByTestId("permission-menu");
      expect(now).toBe(menu); // the same menu, never closed and reopened
      for (const row of within(now).getAllByTestId("permission-item")) {
        expect(row.textContent).toContain("only in the folders you chose");
      }
      // 262px above (270 - 8) cannot hold 274px: capped there, with its own
      // scroll, so every row is reachable and its top stays in the window.
      expect(now.style.maxHeight).toBe(`${CHIP.top - 8}px`);
      expect(now.style.overflowY).toBe("auto");
      const top = VIEW.height - parseFloat(now.style.bottom) - Math.min(274, parseFloat(now.style.maxHeight));
      expect(top).toBeGreaterThanOrEqual(0);
    });
  });

  it("OTHER DIRECTION: an answer that leaves the words as they were leaves the menu where it was", async () => {
    await withLayout(async () => {
      const menu = await tapBeforeTheReadLands();
      await land(OPEN);
      expect(within(menu).getAllByTestId("permission-item")[0].textContent).toContain("anywhere on this PC");
      expect(menu.style.maxHeight).toBe("");
      expect(menu.style.overflowY).toBe("");
      expect(menu.style.bottom).toBe(`${VIEW.height - CHIP.top + 6}px`);
    });
  });

  it("the words changing does not pull focus back to the menu (focus is given once per opening)", async () => {
    await withLayout(async () => {
      const menu = await tapBeforeTheReadLands();
      expect(document.activeElement).toBe(menu);
      const other = screen.getByTestId("elsewhere");
      other.focus();
      await land(CHOSEN);
      expect(screen.getByTestId("permission-menu").style.maxHeight).toBe(`${CHIP.top - 8}px`);
      expect(document.activeElement).toBe(other);
    });
  });
});

/* ------------------------------------------------------------------ (2) */

/** The live /models shape: the config slot's rows carry no name; fleet
 *  nodes' rows carry the label the user gave the machine. */
const CATALOG = [
  { provider: "custom", model: "fleet" },
  { provider: "custom", model: "glm" },
  { provider: "fleet-custom", model: "fleet", name: "DGX Spark" },
  { provider: "fleet-spark", model: "fleet", name: "Spark B" },
  { provider: "fleet-spark", model: "glm", name: "Spark B" },
  { provider: "opencode-cli", model: "spark/fleet" },
];

function count(hay: string, needle: string): number {
  return hay.split(needle).length - 1;
}

function line(route: TurnRoute, opts: { inline?: boolean; catalog?: boolean } = {}) {
  const inline = opts.inline ?? true;
  const modelName = opts.catalog === false ? undefined : answeredModelName(route, CATALOG);
  const view = render(<TurnReceipt inline={inline} route={route} modelName={modelName} />);
  return (view.container.textContent ?? "").replace(/\s+/g, " ").trim();
}

describe("(2) a substitute named by its machine says the machine once", () => {
  it("asked for Claude Code, answered by the 'fleet' pick on Spark B: 'Spark B' once, then 'local'", () => {
    const route: TurnRoute = {
      provider: "fleet-spark",
      model: "fleet",
      label: "Spark B",
      reason: "explicit",
      requested: "claude-cli",
      on_this_network: true,
    };
    for (const catalog of [true, false]) {
      const text = line(route, { catalog });
      expect(count(text, "Spark B"), text).toBe(1);
      expect(screen.getByTestId("turn-route-warning").textContent).toBe(
        "answered by Spark B (you asked for Claude Code)",
      );
      expect(screen.queryByTestId("turn-where-on")).toBeNull();
      expect(screen.getByTestId("turn-where").textContent).toBe("local");
      cleanup();
    }
  });

  it("a failover to the user's own server named 'DGX Spark': once, and 'cloud' when it is off the network", () => {
    const route: TurnRoute = {
      provider: "custom",
      model: "fleet",
      label: "DGX Spark",
      reason: "failover",
      from: "claude-cli",
      why: "",
      on_this_network: true,
    };
    const text = line(route);
    expect(count(text, "DGX Spark"), text).toBe(1);
    expect(screen.getByTestId("turn-where").textContent).toBe("local");
    cleanup();
    const cloud = line({ ...route, on_this_network: false });
    expect(count(cloud, "DGX Spark"), cloud).toBe(1);
    expect(screen.getByTestId("turn-where").textContent).toBe("cloud");
  });

  it("the Build pane's line (not inline) says it once too", () => {
    const text = line(
      {
        provider: "fleet-spark",
        model: "fleet",
        label: "Spark B",
        reason: "explicit",
        requested: "claude-cli",
        on_this_network: true,
      },
      { inline: false },
    );
    expect(count(text, "Spark B"), text).toBe(1);
    expect(screen.queryByTestId("turn-where-on")).toBeNull();
    expect(screen.getByTestId("turn-where").textContent).toBe("local");
  });

  it("OTHER DIRECTION: a substitute that is a model ON the machine still names the machine after the amber words", () => {
    // v1.352.0's rule for a named endpoint: '· on <label>' in place of 'local'.
    const text = line({
      provider: "fleet-spark",
      model: "glm",
      label: "Spark B",
      reason: "failover",
      from: "claude-cli",
      why: "",
      on_this_network: true,
    });
    expect(screen.getByTestId("turn-route-warning").textContent).toBe(
      "answered by glm (Claude Code was not available)",
    );
    expect(screen.getByTestId("turn-where-on").textContent).toBe("on Spark B");
    expect(screen.queryByTestId("turn-where")).toBeNull();
    expect(count(text, "Spark B"), text).toBe(1);
  });

  it("OTHER DIRECTION: the plain daily line is exactly v1.360.0's", () => {
    line({ provider: "custom", model: "fleet", label: "DGX Spark", reason: "default", on_this_network: true });
    expect(screen.getByTestId("turn-answered-by").textContent).toBe("answered by DGX Spark");
    expect(screen.queryByTestId("turn-where-on")).toBeNull();
    expect(screen.getByTestId("turn-where").textContent).toBe("local");
    cleanup();
    line({ provider: "custom", model: "glm", label: "DGX Spark", reason: "default", on_this_network: true });
    expect(screen.getByTestId("turn-answered-by").textContent).toBe("answered by glm on DGX Spark");
    expect(screen.queryByTestId("turn-where")).toBeNull();
  });
});

/* ------------------------------------------------------------------ (3) */

describe("(3) OpenCode on '<server>/fleet' keeps the program's name", () => {
  it("the run page and 'Ran on' name OpenCode, in the model list's words", () => {
    for (const cat of [[], CATALOG]) {
      expect(runModelWords("opencode-cli", "spark/fleet", cat)).toBe("OpenCode · Your model server");
      expect(runModelWords("opencode-cli", "spark/fleet", cat)).toBe(
        modelPickWords({ provider: "opencode-cli", model: "spark/fleet" }, cat),
      );
      const ran = ranOnWords("opencode-cli", "spark/fleet", cat);
      expect(ran.text).toBe("your model server on OpenCode");
      expect(ran.title).toBe("opencode-cli · spark/fleet");
    }
  });

  it("OTHER DIRECTION: the user's own server slots still say the machine alone", () => {
    expect(runModelWords("custom", "fleet", CATALOG)).toBe("DGX Spark");
    expect(runModelWords("fleet-custom", "fleet", CATALOG)).toBe("DGX Spark");
    expect(runModelWords("fleet-spark", "fleet", CATALOG)).toBe("Spark B");
    expect(runModelWords("custom", "fleet", [])).toBe("Your model server");
    expect(runModelWords("ollama", "fleet", [])).toBe("Your model server");
    expect(ranOnWords("custom", "fleet", CATALOG).text).toBe("DGX Spark");
    expect(ranOnWords("fleet-spark", "fleet", CATALOG).text).toBe("Spark B");
    expect(ranOnWords("custom", "fleet", []).text).toBe("your model server");
    // A real model on the machine keeps its own shape.
    expect(runModelWords("opencode-cli", "qwen3", [])).toBe("OpenCode · qwen3");
    expect(ranOnWords("custom", "glm", CATALOG).text).toBe("glm on DGX Spark");
  });

  it("isOwnServerSlot: the two config slots and every fleet node, nothing else", () => {
    for (const p of ["custom", "ollama", "fleet-custom", "fleet-a1b2c3", " fleet-spark "]) {
      expect(isOwnServerSlot(p), p).toBe(true);
    }
    for (const p of ["opencode-cli", "claude-cli", "codex-cli", "openai", "fleet", "", null, undefined]) {
      expect(isOwnServerSlot(p), String(p)).toBe(false);
    }
  });
});

/* ------------------------------------------------------------------ (4) */

describe("(4) an Ollama-slot 'fleet' pick is never named after another model's row", () => {
  /** A detected Ollama row: its `name` is the model's own id. */
  const DETECTED = [{ provider: "ollama", model: "llama3", name: "llama3" }];

  it("every surface reads 'your model server', never 'llama3'", () => {
    expect(machineName("ollama", { model: "fleet", catalog: DETECTED })).toBe("");
    expect(answeredModelName({ provider: "ollama", model: "fleet" }, DETECTED)).toBe("your model server");
    expect(modelRowName({ provider: "ollama", model: "fleet" }, DETECTED)).toBe("Your model server");
    expect(modelPickWords({ provider: "ollama", model: "fleet" }, DETECTED)).toBe("Ollama · Your model server");
    expect(ranOnWords("ollama", "fleet", DETECTED).text).toBe("your model server");
    expect(runModelWords("ollama", "fleet", DETECTED)).toBe("Your model server");
    // The reply's receipt with the page's catalog.
    const route: TurnRoute = { provider: "ollama", model: "fleet", reason: "default", on_this_network: true };
    render(<TurnReceipt inline route={route} modelName={answeredModelName(route, DETECTED)} />);
    expect(screen.getByTestId("turn-answered-by").textContent).toBe("answered by your model server");
  });

  it("OTHER DIRECTION: the machine's own name still reaches the pick, and the real llama3 row keeps its name", () => {
    // The slot's fleet twin carries the label the user gave the machine.
    const twin = [...DETECTED, { provider: "fleet-ollama", model: "fleet", name: "Gaming PC" }];
    expect(machineName("ollama", { model: "fleet", catalog: twin })).toBe("Gaming PC");
    expect(answeredModelName({ provider: "ollama", model: "fleet" }, twin)).toBe("Gaming PC");
    expect(modelRowName({ provider: "ollama", model: "fleet" }, twin)).toBe("Gaming PC");
    // A row for the alias itself that names the machine.
    const own = [...DETECTED, { provider: "ollama", model: "fleet", name: "Gaming PC" }];
    expect(machineName("ollama", { model: "fleet", catalog: own })).toBe("Gaming PC");
    // Every row of a fleet node names its machine, whichever model it is.
    expect(machineName("fleet-spark", { model: "fleet", catalog: [{ provider: "fleet-spark", model: "glm", name: "Spark B" }] })).toBe(
      "Spark B",
    );
    // No model asked about: the slot's rows still speak (v1.360.0's callers).
    expect(machineName("custom", { catalog: CATALOG })).toBe("DGX Spark");
    // The real llama3 row is untouched.
    expect(modelRowName({ provider: "ollama", model: "llama3", name: "llama3" }, DETECTED)).toBe("llama3");
    expect(answeredModelName({ provider: "ollama", model: "llama3" }, DETECTED)).toBe("llama3");
  });
});
