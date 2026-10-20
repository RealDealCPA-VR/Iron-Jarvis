/**
 * v1.360.0 lane 10B (the final re-audit at v1.359.0): ONE NAME for the user's
 * own model server, everywhere, and the machine named on the reply.
 *
 * G21: a reply from the user's own model server (the custom endpoint, the
 * "fleet" alias in front of several boxes) read "answered by fleet · local",
 * never the machine, while the mission screen and the run page said "DGX
 * Spark". The daemon now sends `route.label` for the custom endpoint too
 * (tests/test_chat_matches_meant_v13600.py); this file pins what the user
 * READS from it.
 *
 * Minor (v1.359.0): the model menu called the custom/fleet row "Your model
 * server" while the reply receipt, the helper row and card and the mission
 * screen called the same pick "fleet". Now one rule (lib/onboarding
 * `machineName` + lib/answeredModel `serverPickName`): the machine's name
 * when the user gave it one, else "your model server".
 *
 * Every assertion drives the REAL helpers and the REAL TurnReceipt; nothing
 * under test is stubbed.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { TurnReceipt, type TurnRoute } from "@/components/chat/TurnReceipt";
import { answeredModelName, serverPickName } from "@/lib/answeredModel";
import {
  machineName,
  modelPickWords,
  modelRowName,
  SEED_ENDPOINT_LABELS,
  SERVER_PICK_WORDS,
} from "@/lib/onboarding";
import { ranOnWords } from "@/lib/ranOnWords";

afterEach(() => cleanup());

const read = (rel: string) =>
  readFileSync(path.join(__dirname, "..", "..", rel), "utf8").replace(/\r\n/g, "\n");

/** The live /models shape on the user's install (2026-10-19): the custom
 *  slot's rows carry no name; its fleet twin carries the label the user gave
 *  the machine. */
const CATALOG = [
  { provider: "custom", model: "fleet", kind: "local" },
  { provider: "custom", model: "glm", kind: "local" },
  { provider: "fleet-custom", model: "fleet", name: "DGX Spark", kind: "local" },
  { provider: "fleet-custom", model: "glm", name: "DGX Spark", kind: "local" },
];
/** The same install before the user named the machine (the registry's
 *  default label on the twin). */
const UNNAMED = [
  { provider: "custom", model: "fleet", kind: "local" },
  { provider: "fleet-custom", model: "fleet", name: "Custom endpoint", kind: "local" },
];

describe("the machine's name, when the user gave one (machineName)", () => {
  it("the route's label, else the row's name, else the config slot's fleet twin", () => {
    expect(machineName("custom", { label: "DGX Spark" })).toBe("DGX Spark");
    expect(machineName("fleet-custom", { name: "DGX Spark" })).toBe("DGX Spark");
    expect(machineName("custom", { catalog: CATALOG })).toBe("DGX Spark");
    expect(machineName("ollama", { catalog: [{ provider: "fleet-ollama", name: "Gaming PC" }] })).toBe("Gaming PC");
  });

  it("a registry default label, the bare id or the model's own name is not a name", () => {
    expect(machineName("custom", { label: "Custom endpoint" })).toBe("");
    expect(machineName("fleet-custom", { name: "Custom endpoint" })).toBe("");
    expect(machineName("custom", { catalog: UNNAMED })).toBe("");
    expect(machineName("ollama", { label: "Ollama endpoint" })).toBe("");
    expect(machineName("fleet-x7f2", { name: "fleet-x7f2" })).toBe("");
    expect(machineName("ollama", { name: "qwen3:30b", model: "qwen3:30b" })).toBe("");
    expect(machineName("", { label: "DGX Spark" })).toBe("");
  });

  it("the dashboard's default labels are the daemon's (chat_turn.SEED_ENDPOINT_LABELS)", () => {
    const py = read("src/iron_jarvis/daemon/chat_turn.py");
    const m = /SEED_ENDPOINT_LABELS[^=]*=\s*\{([^}]*)\}/.exec(py);
    expect(m).not.toBeNull();
    const pairs = Object.fromEntries([...m![1].matchAll(/"(\w+)":\s*"([^"]+)"/g)].map((x) => [x[1], x[2]]));
    expect(pairs).toEqual({ ...SEED_ENDPOINT_LABELS });
  });
});

describe("one name for the 'fleet' pick, on every surface", () => {
  it("the model menu row: the machine's name on the row that carries it, else 'Your model server'", () => {
    expect(modelRowName({ provider: "fleet-custom", model: "fleet", name: "DGX Spark" })).toBe("DGX Spark");
    // The config slot's own row carries no name: with the catalog, its twin's.
    expect(modelRowName({ provider: "custom", model: "fleet" }, CATALOG)).toBe("DGX Spark");
    // Without it (or unnamed): the menu's words, as before.
    expect(modelRowName({ provider: "custom", model: "fleet" })).toBe("Your model server");
    expect(modelRowName({ provider: "custom", model: "fleet" }, UNNAMED)).toBe("Your model server");
    expect(modelRowName({ provider: "opencode-cli", model: "spark/fleet" })).toBe("Your model server");
    // Every other model keeps its own words.
    expect(modelRowName({ provider: "fleet-custom", model: "glm", name: "DGX Spark" })).toBe("glm");
    // A model LIST says it once (group and row are the same name).
    expect(modelPickWords({ provider: "fleet-custom", model: "fleet", name: "DGX Spark" })).toBe("DGX Spark");
  });

  it("who answered: the machine's name when known, else 'your model server' — never 'fleet'", () => {
    // The daemon's route.label (the reply).
    expect(answeredModelName({ provider: "custom", model: "fleet", label: "DGX Spark" }, [])).toBe("DGX Spark");
    // The catalog (the page passes it).
    expect(answeredModelName({ provider: "custom", model: "fleet" }, CATALOG)).toBe("DGX Spark");
    // Neither (the helper row and card read no catalog).
    expect(answeredModelName({ provider: "custom", model: "fleet" }, [])).toBe(SERVER_PICK_WORDS);
    expect(answeredModelName({ provider: "custom", model: "fleet", label: "Custom endpoint" }, UNNAMED)).toBe(
      "your model server",
    );
    expect(serverPickName({ provider: "opencode-cli", model: "spark/fleet" }, [])).toBe("your model server");
    // Any other model: unchanged.
    expect(answeredModelName({ provider: "custom", model: "glm", label: "DGX Spark" }, CATALOG)).toBe("glm");
    expect(answeredModelName({ provider: "claude-cli", model: "subscription" }, [])).toBe("Claude Code");
  });

  it("the mission screen and run page say the alias once, by the same name", () => {
    expect(ranOnWords("custom", "fleet", CATALOG).text).toBe("DGX Spark");
    expect(ranOnWords("fleet-custom", "fleet", CATALOG).text).toBe("DGX Spark");
    expect(ranOnWords("custom", "fleet", undefined).text).toBe("your model server");
    expect(ranOnWords("custom", "fleet", UNNAMED).text).toBe("your model server");
    // A real model on the machine keeps "<model> on <machine>".
    expect(ranOnWords("custom", "glm", CATALOG).text).toBe("glm on DGX Spark");
    // The raw stamp stays the record.
    expect(ranOnWords("custom", "fleet", CATALOG).title).toBe("custom · fleet");
  });
});

describe("the reply names the machine (TurnReceipt, the real component)", () => {
  const renderLine = (route: TurnRoute, modelName?: string) =>
    render(<TurnReceipt inline route={route} modelName={modelName} />);

  it("the user's daily route: 'answered by DGX Spark · local' (the place is not said twice)", () => {
    renderLine({ provider: "custom", model: "fleet", label: "DGX Spark", reason: "default", on_this_network: true });
    expect(screen.getByTestId("turn-answered-by").textContent).toBe("answered by DGX Spark");
    expect(screen.queryByTestId("turn-where-on")).toBeNull();
    expect(screen.getByTestId("turn-where").textContent).toBe("local");
    expect(screen.getByTestId("turn-receipt").textContent).not.toMatch(/\bfleet\b/);
    // The raw ids stay in the line's title (the record).
    expect(screen.getByTestId("turn-answered-by").getAttribute("title")).toBe("DGX Spark · fleet · custom");
  });

  it("the page's catalog name agrees with the daemon's label", () => {
    const route: TurnRoute = { provider: "custom", model: "fleet", label: "DGX Spark", reason: "default", on_this_network: true };
    renderLine(route, answeredModelName(route, CATALOG));
    expect(screen.getByTestId("turn-answered-by").textContent).toBe("answered by DGX Spark");
  });

  it("a machine nobody named: 'answered by your model server · local'", () => {
    renderLine({ provider: "custom", model: "fleet", label: "", reason: "default", on_this_network: true });
    expect(screen.getByTestId("turn-answered-by").textContent).toBe("answered by your model server");
    expect(screen.getByTestId("turn-where").textContent).toBe("local");
  });

  it("another model on the named machine keeps 'on DGX Spark' exactly as before", () => {
    renderLine({ provider: "custom", model: "glm", label: "DGX Spark", reason: "default", on_this_network: true });
    expect(screen.getByTestId("turn-answered-by").textContent).toBe("answered by glm on DGX Spark");
    expect(screen.queryByTestId("turn-where")).toBeNull();
  });

  it("a named machine off the user's network still says cloud", () => {
    renderLine({ provider: "fleet-work", model: "fleet", label: "Work proxy", reason: "default", on_this_network: false });
    expect(screen.getByTestId("turn-answered-by").textContent).toBe("answered by Work proxy");
    expect(screen.getByTestId("turn-where").textContent).toBe("cloud");
  });
});
