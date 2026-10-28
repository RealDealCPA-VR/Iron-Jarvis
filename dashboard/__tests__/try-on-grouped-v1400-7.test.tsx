/**
 * v1.400.7 — the "Try on…" list is GROUPED BY PROVIDER (the user, 2026-10-27,
 * with a screenshot of the v1.397.0 list: one flat checkbox list of every
 * model of every provider, Opus 5.5 twice, "your model server" beside
 * "glm-5.3-flash", dozens of raw OpenRouter ids, nothing saying who serves a
 * model or whether it stays on this network).
 *
 * The catalog here is the user's: the v1.376.0 fold fixture holds the REAL
 * daemon's GET /models rows for Claude Code + a keyless Anthropic and Codex +
 * a keyless OpenAI (the top of the screenshot, row for row); the user's
 * LiteLLM gateway is reached two ways (the `custom` config slot and the fleet
 * node the registry derives from it, `fleet-custom`), serves glm-5.3-flash on
 * the network and passes OpenRouter names on to the cloud; and the RTX box is
 * a fleet node of its own.
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import fold from "./fixtures/harness-model-fold-v1376.json";
import { TryOnControl } from "@/components/harness/HarnessTryOn";
import { KIND_BADGE, modelProvidersOf } from "@/components/chat/ModelMenu";
import { floatEdges } from "@/components/chat/FitPanel";
import { MAX_ATTEMPTS, tryOnBody, type TryOnPick } from "@/lib/buildAttempts";
import { companyMenu, foldKeylessApis } from "@/lib/harnessModelMenu";
import { MIXED_BADGE, groupBadge, matchTryOn, tryOnGroups, type TryOnGroup } from "@/lib/tryOnMenu";
import type { ModelOption } from "@/lib/types";

afterEach(cleanup);

const local = (provider: string, model: string, extra: Partial<ModelOption> = {}): ModelOption => ({
  provider,
  model,
  available: true,
  kind: "local",
  on_this_network: true,
  inherited_from: null,
  ...extra,
});
const routedOut = (provider: string, model: string, extra: Partial<ModelOption> = {}): ModelOption =>
  local(provider, model, { on_this_network: false, ...extra });

/** The user's own servers, as GET /models lists them. */
const GATEWAY: ModelOption[] = [
  // Listed before the gateway's own model: the list still puts local first.
  routedOut("custom", "frontier"),
  local("custom", "glm-5.3-flash"),
  // The hidden "fleet" alias: unlisted, on a gateway with a cloud upstream —
  // the daemon reads it as cloud (v1.362.0).
  routedOut("custom", "fleet"),
  local("custom", "glm-5.3-flash-think"),
  routedOut("custom", "deepseek/deepseek-v4.1-flash"),
  routedOut("custom", "~z-ai/glm-flash-latest"),
  // The registry's fleet node for the same slot: one row per deployment, the
  // other names in `aliases` (fleet/picker.py), the registry's default label.
  local("fleet-custom", "glm-5.3-flash", {
    name: "Custom endpoint",
    source: "endpoint",
    aliases: ["glm-5.3-flash-think", "vision"],
  } as Partial<ModelOption>),
  routedOut("fleet-custom", "frontier", { name: "Custom endpoint", source: "endpoint" }),
  routedOut("fleet-custom", "deepseek/deepseek-v4.1-flash", { name: "Custom endpoint", source: "endpoint" }),
  local("fleet-rtx6000ada", "Qwen3.8-27B (RTX)", { name: "RTX6000ada", source: "endpoint", size_b: 27 }),
];
const CATALOG: ModelOption[] = [
  ...(fold.models as unknown as ModelOption[]),
  ...GATEWAY,
  { provider: "mock", model: "mock-1", available: true },
];
const catalogPairs = new Set(CATALOG.map((m) => `${m.provider}::${m.model}`));

const byId = (groups: TryOnGroup[], id: string) => groups.find((g) => g.id === id)!;
const labels = (g: TryOnGroup) => g.models.map((m) => m.label);
const sent = (g: TryOnGroup) => g.models.map((m) => `${m.provider}::${m.model}`);

describe("the list is one row per provider, local first, no model twice", () => {
  const groups = tryOnGroups(CATALOG, false);

  it("groups: your own hardware first, then your plans; offline providers and the demo model never", () => {
    expect(groups.map((g) => [g.id, g.label, g.place])).toEqual([
      ["fleet-rtx6000ada", "RTX6000ada", "local"],
      ["custom", "Your model server", "mixed"],
      ["claude-cli", "Claude Code", "cli"],
      ["codex-cli", "Codex", "cli"],
    ]);
  });

  it("Claude Code lists each model once; a keyless Anthropic's own model sits under it and SENDS its catalog pair", () => {
    const cc = byId(groups, "claude-cli");
    expect(labels(cc)).toEqual([
      "Your Claude plan",
      "Opus 5.5",
      "Fable 5.1",
      "Sonnet 5.5",
      "Haiku 4.5",
      "Opus 4.8",
      "Sonnet 4.6",
      expect.any(String),
    ]);
    // The screenshot's Opus 5.5 / Fable 5.1 / Sonnet 5.5 / Haiku twins are gone.
    expect(sent(cc)).toEqual([
      "claude-cli::subscription",
      "claude-cli::claude-opus-5-5",
      "claude-cli::claude-fable-5-1",
      "claude-cli::claude-sonnet-5-5",
      "claude-cli::claude-haiku-4-5-20251001",
      "anthropic::claude-opus-4-8",
      "anthropic::claude-sonnet-4-6",
      "anthropic::claude-fable-5",
    ]);
    expect(groups.some((g) => g.id === "anthropic")).toBe(false);
    // Codex the same way: its plan, then the keyless OpenAI's models.
    expect(sent(byId(groups, "codex-cli"))).toEqual([
      "codex-cli::subscription",
      "openai::gpt-4o",
      "openai::gpt-4o-mini",
      "openai::gpt-5.5",
    ]);
  });

  it("every pick sends a pair GET /models lists (the daemon refuses any other), and no pair or word is listed twice", () => {
    const all = groups.flatMap((g) => g.models);
    for (const m of all) expect(catalogPairs.has(`${m.provider}::${m.model}`)).toBe(true);
    const pairs = all.map((m) => `${m.provider}::${m.model}`);
    expect(new Set(pairs).size).toBe(pairs.length);
    const words = all.map((m) => `${m.label}`);
    expect(new Set(words).size).toBe(words.length);
  });

  it("the gateway is ONE group (its config slot and its fleet twin), the fleet alias and another name of a model are not entries", () => {
    const server = byId(groups, "custom");
    expect(groups.some((g) => g.id === "fleet-custom")).toBe(false);
    expect(server.models.map((m) => m.model)).toEqual([
      "glm-5.3-flash",
      "frontier",
      "deepseek/deepseek-v4.1-flash",
      "~z-ai/glm-flash-latest",
    ]);
    // Local first; a model both reach sends the config slot's pair.
    expect(server.models.map((m) => m.place)).toEqual(["local", "endpoint", "endpoint", "endpoint"]);
    expect(sent(server)[0]).toBe("custom::glm-5.3-flash");
    expect(labels(server)).not.toContain("Your model server");
  });

  it("the server is named by the machine's name once the user gave one", () => {
    const named = CATALOG.map((m) => (m.provider === "fleet-custom" ? { ...m, name: "DGX Spark" } : m));
    expect(byId(tryOnGroups(named, false), "custom").label).toBe("DGX Spark");
  });

  it("the fleet alias stays when it is the server's only way to a model on this network, named by the machine", () => {
    const rows = [
      local("custom", "fleet"),
      routedOut("custom", "frontier"),
      local("fleet-custom", "fleet", { name: "Spark", source: "endpoint" }),
    ];
    const [g] = tryOnGroups(rows, false);
    expect(g.label).toBe("Spark");
    expect(g.models.map((m) => [m.provider, m.model, m.label])).toEqual([
      ["custom", "fleet", "Spark"],
      ["custom", "frontier", expect.any(String)],
    ]);
  });

  it("a provider reached with a real API key keeps its own pay-per-use row, named for the company", () => {
    const keyed = [
      ...(fold.models as unknown as ModelOption[]).filter((m) => m.provider !== "anthropic"),
      { ...(fold.models as unknown as ModelOption[])[0], inherited_from: null },
    ];
    const api = byId(tryOnGroups(keyed, false), "anthropic");
    expect(api.label).toBe("Anthropic API");
    expect(api.place).toBe("api");
    expect(groupBadge(api.place).text).toBe(KIND_BADGE.api.text);
  });
});

describe("where each provider and each model runs is read from the row, never guessed", () => {
  it("a model's place follows on_this_network: a gateway name sent on to the cloud is cloud", () => {
    const g = byId(tryOnGroups(CATALOG, false), "custom");
    const where = Object.fromEntries(g.models.map((m) => [m.model, m.place]));
    expect(where["glm-5.3-flash"]).toBe("local");
    expect(where.frontier).toBe("endpoint");
    expect(groupBadge("mixed")).toBe(MIXED_BADGE);
    expect(groupBadge("local").text).toBe("local");
    expect(groupBadge("cli").text).toBe("cloud · on your plan");
    expect(groupBadge("endpoint").text).toBe("cloud");
  });

  it("Local only offers only models that stay on this network; the server's group is then plain local", () => {
    const groups = tryOnGroups(CATALOG, true);
    expect(groups.map((g) => [g.id, g.place])).toEqual([
      ["fleet-rtx6000ada", "local"],
      ["custom", "local"],
    ]);
    expect(groups.flatMap((g) => g.models).every((m) => m.place === "local")).toBe(true);
  });

  it("the shared fold is the harness menu's own (one rule): the same rows listed", () => {
    expect(foldKeylessApis(CATALOG).listed).toEqual(companyMenu(CATALOG, null, null, "").models);
    // And the chat menu's provider rows are untouched by the lift of rowKind:
    // there a provider still reads as its FIRST row (here a cloud name).
    expect(modelProvidersOf(CATALOG).map((p) => [p.id, p.kind])).toContainEqual(["custom", "endpoint"]);
    expect(modelProvidersOf(CATALOG).map((p) => [p.id, p.kind])).toContainEqual(["fleet-rtx6000ada", "local"]);
  });

  it("a typed word finds a model across every provider and says where it runs", () => {
    const groups = tryOnGroups(CATALOG, false);
    const { hits, more } = matchTryOn(groups, "opus", 12);
    expect(hits.map((h) => [h.model.label, h.group.label])).toEqual([
      ["Opus 5.5", "Claude Code"],
      ["Opus 4.8", "Claude Code"],
    ]);
    expect(more).toBe(0);
    expect(matchTryOn(groups, "deepseek", 12).hits.map((h) => h.group.label)).toEqual(["Your model server"]);
    expect(matchTryOn(groups, "x", 1).more).toBeGreaterThan(0);
  });
});

describe("a pop-out beside its row opens on the right when the list sits at the box's left", () => {
  const place = { side: "up" as const, maxHeight: 300 };
  it("right when the window has room; left when it does not; shifted inside the window last", () => {
    expect(floatEdges({ top: 400, bottom: 430, left: 380, right: 700 }, place, 1280, 720, 256, "right")).toMatchObject({
      left: 704,
      align: "beside",
    });
    expect(floatEdges({ top: 400, bottom: 430, left: 700, right: 1020 }, place, 1280, 720, 256, "right")).toMatchObject({
      right: 580,
      align: "beside",
    });
    expect(floatEdges({ top: 400, bottom: 430, left: 100, right: 420 }, place, 600, 720, 256, "right")).toMatchObject({
      align: "shifted",
    });
    // Past the list's own padding and border when it is told how far.
    expect(floatEdges({ top: 400, bottom: 430, left: 380, right: 700 }, place, 1280, 720, 256, "right", 13)).toMatchObject({
      left: 713,
    });
    // The default (the chat's menu) is left, exactly as before.
    expect(floatEdges({ top: 400, bottom: 430, left: 380, right: 700 }, place, 1280, 720, 224)).toEqual(
      floatEdges({ top: 400, bottom: 430, left: 380 }, place, 1280, 720, 224),
    );
  });
});

function Harness({ localOnly = false, onPicks }: { localOnly?: boolean; onPicks?: (p: TryOnPick[]) => void }) {
  const [picks, setPicks] = React.useState<TryOnPick[]>([]);
  return (
    <TryOnControl
      models={CATALOG}
      localOnly={localOnly}
      picks={picks}
      onChange={(p) => {
        onPicks?.(p);
        setPicks(p);
      }}
    />
  );
}

const groupRow = (id: string) =>
  screen.getAllByTestId("harness-try-on-group").find((b) => b.getAttribute("data-group") === id)!;
const rowFor = (provider: string, model: string) =>
  (screen.getAllByTestId("harness-try-on-row") as HTMLInputElement[]).find(
    (r) => r.getAttribute("data-provider") === provider && r.getAttribute("data-model") === model,
  )!;

describe("the control: provider rows, a pop-out of each one's models, at most three picks", () => {
  it("opens on provider rows only, each marked where it runs; a model is ticked inside its provider's pop-out", () => {
    const onPicks = vi.fn();
    render(<Harness onPicks={onPicks} />);
    fireEvent.click(screen.getByTestId("harness-try-on"));
    const rows = screen.getAllByTestId("harness-try-on-group");
    expect(rows.map((r) => r.getAttribute("data-group"))).toEqual(["fleet-rtx6000ada", "custom", "claude-cli", "codex-cli"]);
    expect(screen.queryAllByTestId("harness-try-on-row")).toHaveLength(0);
    expect(screen.getAllByTestId("harness-try-on-group-place").map((b) => b.textContent)).toEqual([
      "local",
      "local + cloud",
      "cloud · on your plan",
      "cloud · on your plan",
    ]);
    fireEvent.click(groupRow("claude-cli"));
    expect(groupRow("claude-cli")).toHaveAttribute("aria-expanded", "true");
    const panel = screen.getByTestId("harness-try-on-panel");
    expect(within(panel).getAllByTestId("harness-try-on-row")).toHaveLength(8);
    // Every model says local or cloud.
    expect(within(panel).getAllByTestId("harness-try-on-place").map((p) => p.textContent)).toEqual(
      Array(8).fill("cloud"),
    );
    fireEvent.click(rowFor("anthropic", "claude-opus-4-8"));
    expect(onPicks).toHaveBeenLastCalledWith([{ provider: "anthropic", model: "claude-opus-4-8", label: "Opus 4.8" }]);
    expect(within(groupRow("claude-cli")).getByTestId("harness-try-on-group-picked")).toHaveTextContent("1 picked");
    // The picks sit at the top, where the pop-out cannot hide them.
    expect(screen.getByTestId("harness-try-on-picked")).toHaveTextContent("Opus 4.8 · Claude Code");
    expect(screen.getByTestId("harness-try-on")).toHaveTextContent("Try on Opus 4.8");
  });

  it("the mixed server's pop-out says which models stay on the network and which go to the cloud", () => {
    render(<Harness />);
    fireEvent.click(screen.getByTestId("harness-try-on"));
    fireEvent.click(groupRow("custom"));
    const panel = screen.getByTestId("harness-try-on-panel");
    expect(panel).toHaveTextContent(/On your network[\s\S]*glm-5\.3-flash[\s\S]*Passed on to the cloud/);
    expect(within(panel).getAllByTestId("harness-try-on-place").map((p) => p.getAttribute("data-place"))).toEqual([
      "local",
      "endpoint",
      "endpoint",
      "endpoint",
    ]);
    expect(within(panel).queryByText(/your model server/i)).toBeNull();
  });

  it("three at most: the rest are disabled, in every provider; the body is the catalog pairs", () => {
    const onPicks = vi.fn();
    render(<Harness onPicks={onPicks} />);
    fireEvent.click(screen.getByTestId("harness-try-on"));
    fireEvent.click(groupRow("custom"));
    fireEvent.click(rowFor("custom", "glm-5.3-flash"));
    fireEvent.click(groupRow("codex-cli"));
    fireEvent.click(rowFor("openai", "gpt-5.5"));
    fireEvent.click(groupRow("claude-cli"));
    fireEvent.click(rowFor("claude-cli", "claude-opus-5-5"));
    expect(screen.getByTestId("harness-try-on")).toHaveTextContent(`Try on ${MAX_ATTEMPTS} models`);
    expect(rowFor("claude-cli", "claude-sonnet-5-5")).toBeDisabled();
    expect(rowFor("claude-cli", "claude-opus-5-5")).not.toBeDisabled();
    const last = onPicks.mock.calls.at(-1)![0] as TryOnPick[];
    expect(tryOnBody(last)).toEqual([
      { provider: "custom", model: "glm-5.3-flash" },
      { provider: "openai", model: "gpt-5.5" },
      { provider: "claude-cli", model: "claude-opus-5-5" },
    ]);
    // A pick is taken back from the picked list.
    fireEvent.click(screen.getByRole("button", { name: "Do not try it on gpt-5.5" }));
    expect(screen.getByTestId("harness-try-on")).toHaveTextContent("Try on 2 models");
    expect(rowFor("claude-cli", "claude-sonnet-5-5")).not.toBeDisabled();
  });

  it("a typed word lists matches across providers with their provider named; they tick like any row", () => {
    const onPicks = vi.fn();
    render(<Harness onPicks={onPicks} />);
    fireEvent.click(screen.getByTestId("harness-try-on"));
    const box = screen.getByTestId("harness-try-on-filter");
    expect(document.activeElement).toBe(box);
    fireEvent.change(box, { target: { value: "glm" } });
    expect(screen.queryAllByTestId("harness-try-on-group")).toHaveLength(0);
    const hits = screen.getAllByTestId("harness-try-on-row");
    expect(hits.map((h) => h.getAttribute("data-model"))).toEqual(["glm-5.3-flash", "~z-ai/glm-flash-latest"]);
    fireEvent.click(hits[0]);
    expect(onPicks).toHaveBeenLastCalledWith([{ provider: "custom", model: "glm-5.3-flash", label: "glm-5.3-flash" }]);
    fireEvent.change(box, { target: { value: "nothing-like-it" } });
    expect(screen.getByTestId("harness-try-on-list")).toHaveTextContent("No model matches.");
  });

  it("Escape closes the open pop-out first (the focus on its provider), then the list (the focus on the chip)", () => {
    render(<Harness />);
    const chip = screen.getByTestId("harness-try-on");
    fireEvent.click(chip);
    fireEvent.click(groupRow("codex-cli"));
    const menu = screen.getByTestId("harness-try-on-menu");
    fireEvent.keyDown(rowFor("openai", "gpt-4o"), { key: "Escape" });
    expect(screen.queryByTestId("harness-try-on-panel")).toBeNull();
    expect(document.activeElement).toBe(groupRow("codex-cli"));
    fireEvent.keyDown(menu, { key: "Escape" });
    expect(screen.queryByTestId("harness-try-on-menu")).toBeNull();
    expect(document.activeElement).toBe(chip);
  });

  it("every button is type=button (a disclosure, never a form)", () => {
    render(<Harness />);
    fireEvent.click(screen.getByTestId("harness-try-on"));
    fireEvent.click(groupRow("claude-cli"));
    fireEvent.click(rowFor("claude-cli", "claude-opus-5-5"));
    for (const b of screen.getAllByRole("button")) expect(b).toHaveAttribute("type", "button");
  });

  it("Local only: provider rows that stay on the network only", () => {
    render(<Harness localOnly />);
    fireEvent.click(screen.getByTestId("harness-try-on"));
    expect(screen.getAllByTestId("harness-try-on-group").map((b) => b.getAttribute("data-group"))).toEqual([
      "fleet-rtx6000ada",
      "custom",
    ]);
    expect(screen.getByTestId("harness-try-on-menu")).toHaveTextContent("Local only: models on this PC or your network.");
  });
});
