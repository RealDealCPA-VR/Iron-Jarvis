/**
 * What ONE model behind an endpoint can do (v1.424.0, D8).
 *
 * A gateway (a LiteLLM proxy, an Ollama box) serves several models, and they
 * do not all call tools or see pictures. Before v1.424.0 the endpoint had ONE
 * answer — a check of its default model — and every model behind it wore it:
 * the Connections row said "tools ✓" for a model nobody had asked. The daemon
 * now answers per model (`GET /fleet` rows carry `model_capabilities`, each
 * `models[]` entry and each `fleet-*` row of `GET /models` its own
 * `tool_use` / `vision`): a live check of THAT model, else what its server
 * states in its own `/model/info`, else the endpoint's record for its default
 * model, else NOT KNOWN — never a guess.
 *
 * This module turns one model's answer into the words the Connections and
 * Fleet pages show. It is pure (no React, no fetch).
 */

/** Where an answer came from: a live check, the server's own statement, or
 *  nothing ("" — not known). The daemon's `fleet/adapter.CAP_*` words. */
export type CapSource = "checked" | "stated" | "";

/** One model's capability view, as the daemon sends it. */
export interface CapView {
  tool_use?: boolean | null;
  vision?: boolean | null;
  tool_use_from?: CapSource | string | null;
  vision_from?: CapSource | string | null;
}

/** A chip a page draws for one capability. `known` false = not known. */
export interface CapChip {
  text: string;
  title: string;
  /** True when the answer is a yes (the green tick). */
  yes: boolean;
  known: boolean;
}

/** The model a node's own record speaks for when nothing else names one. */
function shown(model: string): string {
  const m = (model || "").trim();
  return m && m !== "default" ? m : "this model";
}

function source(value: unknown): CapSource {
  return value === "checked" || value === "stated" ? value : "";
}

/**
 * One model's view out of a `GET /fleet` row: its own `model_capabilities`
 * entry when the daemon sent one (v1.424.0), else — an older daemon — the
 * node's one flag pair, said as "checked" (that is what it was). Never throws.
 */
export function capabilityOf(
  row: { model_capabilities?: Record<string, CapView> | null } | null | undefined,
  model: string,
  legacy?: { tool_use?: boolean | null; vision?: boolean | null } | null,
): CapView {
  const view = row?.model_capabilities?.[(model || "").trim()];
  if (view) return view;
  if (row && row.model_capabilities) {
    // A daemon that answers per model and has nothing for this one: not known.
    return { tool_use: null, vision: null, tool_use_from: "", vision_from: "" };
  }
  return {
    tool_use: legacy?.tool_use ?? null,
    vision: legacy?.vision ?? null,
    tool_use_from: legacy?.tool_use == null ? "" : "checked",
    vision_from: legacy?.vision == null ? "" : "checked",
  };
}

/** What a model that cannot call tools gets, said once (the router's rule
 *  since v1.131.0: no other model takes the turn). */
const NO_TOOLS_MEANS =
  "Picked by name, it answers in text only; as your default model, Iron Jarvis writes the tools into its instructions instead.";

/** What a model that cannot see gets (v1.424.0, decision D1: never a key
 *  that bills per use unless the model you picked does). */
const NO_VISION_MEANS =
  "A picture goes to another of your models that can see it (never one that bills per use, unless you picked one), or stays unseen here.";

/** The tools chip for one model. */
export function toolsChip(view: CapView | null | undefined, model: string): CapChip {
  const name = shown(model);
  const from = source(view?.tool_use_from);
  if (view?.tool_use === true) {
    return {
      text: "tools",
      yes: true,
      known: true,
      title:
        from === "stated"
          ? `The model's server says ${name} can call tools (not checked here yet), so turns that use the web or files run on it.`
          : `Checked: ${name} calls tools, so turns that use the web or files run on it.`,
    };
  }
  if (view?.tool_use === false) {
    return {
      text: "no tools",
      yes: false,
      known: true,
      title:
        (from === "stated"
          ? `The model's server says ${name} can't call tools. `
          : `Checked: ${name} doesn't call tools itself. `) + NO_TOOLS_MEANS,
    };
  }
  return {
    text: "tools not known",
    yes: false,
    known: false,
    title: `Not known yet whether ${name} can call tools, so it is treated as a model that can't. ${NO_TOOLS_MEANS}`,
  };
}

/** The vision chip for one model. */
export function visionChip(view: CapView | null | undefined, model: string): CapChip {
  const name = shown(model);
  const from = source(view?.vision_from);
  if (view?.vision === true) {
    return {
      text: "vision",
      yes: true,
      known: true,
      title:
        from === "stated"
          ? `The model's server says ${name} can see pictures (not checked here yet), so pictures and scanned PDFs can go to it.`
          : `Checked: ${name} saw the test picture, so pictures and scanned PDFs can go to it.`,
    };
  }
  if (view?.vision === false) {
    return {
      text: "no vision",
      yes: false,
      known: true,
      title:
        (from === "stated"
          ? `The model's server says ${name} can't see pictures. `
          : `Checked: ${name} answered but did not see the test picture. `) + NO_VISION_MEANS,
    };
  }
  return {
    text: "vision not known",
    yes: false,
    known: false,
    title: `Not known yet whether ${name} can see pictures. ${NO_VISION_MEANS}`,
  };
}

/** The sentence the endpoint form says after its save check (v1.424.0: about
 *  the MODEL it asked, and true to what really happens next). */
export function saveCheckWords(toolUse: boolean | null | undefined, model: string): string {
  // At the start of a sentence: "This model", never "this model" (a model id
  // keeps its own casing).
  const raw = shown(model);
  const name = raw === "this model" ? "This model" : raw;
  if (toolUse === true) return ` ${name} calls tools, so turns that use the web or files run on it.`;
  if (toolUse === false) {
    return ` ${name} doesn't call tools itself: picked by name, it answers in text only.`;
  }
  return ` Whether ${name} calls tools could not be checked yet. The server may be asleep; press Verify tools on its row later.`;
}
