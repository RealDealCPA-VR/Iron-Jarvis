/**
 * Shared first-run helpers (v1.310.0, wave 2 — "the first five minutes").
 *
 * One place for the decisions three surfaces make about the same /health
 * answer: the first-run wizard, the chat empty state and the Overview's
 * welcome card. Before this file each surface would have re-derived "is a real
 * model answering?" on its own, and the old wizard got it wrong in the one way
 * that matters: a signed-in Claude Code made it say "answers are real" while
 * the default every chat turn uses was still the offline demo (finding
 * mock-default-trap-cli-ollama).
 *
 * Contract W2-6 (other tracks build on these names — keep them):
 *  - `useModel(provider)` posts W2-1 (`POST /onboarding/use-model`) — the ONE
 *    explicit "use this for answers" press — and resolves the DECODED answer
 *    `{promoted, reason}`. The daemon promotes only an untouched demo default;
 *    a user's own choice comes back as `promoted: null` + a sentence. Pass the
 *    DaemonProvider's `refresh` as the second argument so /health follows the
 *    change at once (this module cannot reach the provider on its own).
 *  - `decodeOnboardingModel(raw)` decodes W2-2's `model` block of
 *    `GET /onboarding`, refusing junk.
 *
 * NOTE on the name: `useModel` is a plain async function, not a React hook —
 * the contract fixed the name before the shape. Components call the
 * `chooseForAnswers` alias so the hooks lint rule does not mistake an
 * onClick for a hook call.
 *
 * Decoders live here, OUTSIDE lib/api.ts, because ~70 test files mock that
 * module wholesale (the lib/etag.ts lesson).
 */

import { post } from "@/lib/api";
import type { Health, ProviderHealth } from "@/lib/types";
import { DEMO_MODEL_LABEL } from "@/lib/demoWords";
import { friendlyModelName } from "@/lib/friendlyModelName";

/* ------------------------------------------------------------ W2-1 / W2-6 --- */

export interface PromotedModel {
  provider: string;
  model: string;
}

/** What the "use it for answers" press answers (W2-1), decoded. */
export interface UseModelAnswer {
  /** The new default, or null when nothing changed (see `reason`). */
  promoted: PromotedModel | null;
  /** One plain sentence when nothing changed ("you already chose X"); "" on a
   *  promotion. Always a string. */
  reason: string;
}

/** Whitelist W2-1's answer: extra keys dropped, a malformed `promoted` is
 *  null (never a half-object that renders "undefined"), `reason` a string. */
export function decodeUseModelAnswer(raw: unknown): UseModelAnswer {
  const r = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const p = r.promoted;
  let promoted: PromotedModel | null = null;
  if (p && typeof p === "object") {
    const { provider, model } = p as Record<string, unknown>;
    if (typeof provider === "string" && provider && typeof model === "string") {
      promoted = { provider, model };
    }
  }
  return { promoted, reason: typeof r.reason === "string" ? r.reason : "" };
}

/**
 * The ONE explicit "use this for answers" press (W2-1). Rejects with the
 * daemon's own sentence on a 409 (unknown / unavailable provider) — callers
 * show `err.message`. `refresh` (the DaemonProvider's) runs after a resolved
 * answer, so the topbar and every /health reader follow without the 5 s poll.
 */
export async function useModel(
  provider: string,
  refresh?: () => void,
): Promise<UseModelAnswer> {
  const raw = await post<unknown>("/onboarding/use-model", { provider });
  const answer = decodeUseModelAnswer(raw);
  try {
    refresh?.();
  } catch {
    /* a refresh hiccup must never turn a landed promotion into an error */
  }
  return answer;
}

/** The same press under a name the hooks lint rule leaves alone (it is called
 *  from click handlers). */
export const chooseForAnswers = useModel;

/* ------------------------------------------------------------------- W2-2 --- */

export interface OnboardingModelRow {
  provider: string;
  label: string;
  /** The daemon's own word on whether this one keeps what you type on this PC
   *  (readiness.py emits it on every row). Passed through only when it is a
   *  real boolean — absent means "not said", never a guess. */
  local?: boolean;
}

export interface OnboardingModel {
  default_provider: string;
  default_model: string;
  is_mock: boolean;
  /** Real providers available right now (the daemon's cached view). */
  usable: OnboardingModelRow[];
}

/** Decode W2-2's `model` block. A non-object is null; a non-array `usable` is
 *  `[]`; malformed rows are dropped (a label-less row borrows its name). */
export function decodeOnboardingModel(raw: unknown): OnboardingModel | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  const default_provider = typeof r.default_provider === "string" ? r.default_provider : "";
  const default_model = typeof r.default_model === "string" ? r.default_model : "";
  const is_mock =
    typeof r.is_mock === "boolean" ? r.is_mock : default_provider === "" || default_provider === "mock";
  const usable: OnboardingModelRow[] = [];
  if (Array.isArray(r.usable)) {
    for (const row of r.usable) {
      if (!row || typeof row !== "object") continue;
      const { provider, label, local } = row as Record<string, unknown>;
      if (typeof provider !== "string" || !provider) continue;
      usable.push({
        provider,
        label: typeof label === "string" && label ? label : provider,
        ...(typeof local === "boolean" ? { local } : {}),
      });
    }
  }
  return { default_provider, default_model, is_mock, usable };
}

/* ---------------------------------------------------- reading /health ---- */

/** A /health provider row as the subscription door reads it. `sign_in_fix`
 *  is W2-3's additive field (cli_auth.SIGN_IN_FIX) — typed here, not in the
 *  shared lib/types.ts, which this track does not own. `detected` (v1.353.0,
 *  G34, the ollama row only): Ollama answers on this PC with a model to use,
 *  from the daemon's cached probe — true even before its address is saved,
 *  which is exactly when the one press can save it. */
export type HealthRow = ProviderHealth & {
  sign_in_fix?: string;
  detected?: boolean;
  /** v1.353.0 (ollama row only): the user saved an Ollama address. */
  address_saved?: boolean;
  /** v1.358.0 (lane 8D, the `custom` row only): LM Studio / llama.cpp
   *  answering on this PC with the model-server slot free — what ONE press
   *  (`POST /onboarding/use-model {provider: key}`) connects and chooses
   *  (providers/local_servers_seen.health_field). Absent on an older daemon. */
  detected_servers?: { key: string; label?: string; base_url?: string }[];
};

/** v1.358.0 (lane 8D): the press names of the two model servers people run on
 *  their own PC, in the daemon's words (local_servers_seen.SERVERS labels).
 *  A press posts the KEY; the daemon saves the address and chooses it. */
export const LOCAL_SERVER_PRESS_LABELS: Readonly<Record<string, string>> = {
  lmstudio: "LM Studio (on this PC)",
  llamacpp: "llama.cpp (on this PC)",
};

/** v1.358.0 (lane 8D): the presses `POST /onboarding/use-model` takes for
 *  them — keep a press list (ConnectDoors' ONE_PRESS) in step with this. */
export const LOCAL_SERVER_PROVIDERS: readonly string[] = Object.keys(LOCAL_SERVER_PRESS_LABELS);

/** v1.358.0 (lane 8D): LM Studio / llama.cpp the daemon's cached probe found
 *  answering on this PC, ready for the one press (the `custom` row's
 *  `detected_servers`); [] on an older daemon or when none answers. */
export function localServersDetected(health: Health | null | undefined): { provider: string; label: string }[] {
  const row = healthRow(health, "custom");
  const list = Array.isArray(row?.detected_servers) ? row!.detected_servers! : [];
  const out: { provider: string; label: string }[] = [];
  for (const s of list) {
    const key = s && typeof s.key === "string" ? s.key : "";
    if (!key || !(key in LOCAL_SERVER_PRESS_LABELS) || out.some((o) => o.provider === key)) continue;
    out.push({ provider: key, label: LOCAL_SERVER_PRESS_LABELS[key] });
  }
  return out;
}

/**
 * v1.313.0 (contract U1-1): THE one truth for "replies are a scripted demo".
 * True when no model has been chosen to answer — the provider is unset, blank
 * or the offline "mock". An explicit "auto" or any real name is the USER's
 * choice and answers false. The title-bar chip, the simulated-mode strip, the
 * Overview and the wizard all ask this one function, so they can never
 * disagree about whether the user is talking to a real model (the chip used
 * to name `claude-opus-4-8` while the same screen said "scripted demo").
 */
export function noModelChosen(provider: string | null | undefined): boolean {
  const dp = (provider ?? "").trim();
  return dp === "" || dp === "mock";
}

/** The default every chat turn uses is still the offline demo ("mock", or
 *  unset). An explicit "auto" or any real name is the USER's choice.
 *  One rule, not two: this is `noModelChosen` read off /health. */
export function isDemoDefault(health: Health | null | undefined): boolean {
  return noModelChosen(health?.default_provider);
}

/** Real providers that answer right now (the demo itself never counts). */
export function availableRows(health: Health | null | undefined): HealthRow[] {
  return ((health?.providers ?? []) as HealthRow[]).filter(
    (p) => p.available && p.provider !== "mock",
  );
}

/** Should a surface lead with the connect doors? Only on a KNOWN answer (no
 *  doors flashing while /health loads): nothing real is available, or the
 *  default is still the demo. */
export function needsConnect(health: Health | null | undefined): boolean {
  if (!health) return false;
  return availableRows(health).length === 0 || isDemoDefault(health);
}

const FRIENDLY: Record<string, string> = {
  "claude-cli": "Claude Code",
  "codex-cli": "Codex",
  "grok-cli": "Grok CLI",
  // v1.353.0 (G57): the model menu named it by its raw id.
  "opencode-cli": "OpenCode",
  anthropic: "Anthropic",
  openai: "OpenAI",
  google: "Google Gemini",
  xai: "xAI",
  openrouter: "OpenRouter",
  ollama: "Ollama",
  custom: "your own endpoint",
  // v1.358.0 (lane 8D): the one-press names of the two local model servers.
  lmstudio: "LM Studio",
  llamacpp: "llama.cpp",
};

/** A plain name for a provider id ("claude-cli" → "Claude Code"). */
export function friendlyProvider(provider: string): string {
  return FRIENDLY[provider] ?? provider;
}

/** v1.314.0 (UX wave 2): the provider word a USER reads anywhere a row, chip,
 *  option or receipt names one. The built-in scripted model is never shown as
 *  "mock" — it is the demo model, and saying "scripted" keeps the disclosure
 *  honest. The VALUE sent to the daemon stays the id; keep the raw id in a
 *  `title` where the row is a record of what ran. */
export function providerDisplay(provider: string | null | undefined): string {
  const p = (provider || "").trim();
  if (!p) return "";
  if (p === "mock") return DEMO_MODEL_LABEL;
  if (p === "auto") return "Auto";
  return friendlyProvider(p);
}

/** v1.353.0 (G57): a provider as a HEADING or a list entry reads it — the
 *  model menu's rows, a settings option — never a raw id. Sentence case, and
 *  the user's own server is "Your model server" (mid-sentence surfaces keep
 *  `providerDisplay`'s "your own endpoint"). "" for an id no table knows, so
 *  the caller can fall back to a name of its own (an endpoint's label). */
const HEADING: Record<string, string> = { custom: "Your model server" };

export function providerHeading(provider: string | null | undefined): string {
  const p = (provider || "").trim();
  if (!p) return "";
  if (HEADING[p]) return HEADING[p];
  const shown = providerDisplay(p);
  if (shown === p) return ""; // unknown: not ours to name
  // Sentence case only for OUR lower-case phrases; a brand keeps its own
  // casing ("xAI" must not read "XAI"; review of v1.353.0 lane 3A).
  const first = shown.split(" ")[0];
  if (first !== first.toLowerCase()) return shown;
  return shown[0].toUpperCase() + shown.slice(1);
}

/** The name a model-menu row's provider group reads (v1.353.0, G57): the
 *  row's own `name` (an endpoint's label the user gave it) unless that is
 *  only the model id again — a detected Ollama row names its MODEL there —
 *  else the provider's plain name ("Claude Code", "Your model server"); the
 *  raw id only when nothing at all is known. The catalogue's rows carry no
 *  `name` for the built-in providers, which is how the menu came to list
 *  "claude-cli", "codex-cli", "custom" and "opencode-cli". */
export function modelGroupName(m: { provider: string; model?: string; name?: string }): string {
  const name = (m.name || "").trim();
  if (name && name !== m.model) return name;
  return providerHeading(m.provider) || m.provider;
}

/** v1.359.0 (lane 9B): the model ids that mean "this provider's own
 *  default": no model is passed and the CLI or the server picks. The
 *  daemon's twins are `subprocess_cli._PLACEHOLDER_MODELS` and
 *  `claude_models.DEFAULT_IDS`. The live menu listed the raw word
 *  "subscription" under Claude Code and Codex. */
export const PROVIDER_DEFAULT_MODEL_IDS: readonly string[] = ["", "subscription", "default", "auto"];

export function isProviderDefaultModel(model: string | null | undefined): boolean {
  return PROVIDER_DEFAULT_MODEL_IDS.includes((model ?? "").trim().toLowerCase());
}

/** The alias docs/LOCAL-MODELS.md tells a model-server owner to keep in
 *  front of their servers ("fleet"; OpenCode writes it "<its server>/fleet").
 *  It means "whatever my own servers run", so a row says that. */
export function isServerAlias(model: string | null | undefined): boolean {
  const m = (model ?? "").trim().toLowerCase();
  return m === "fleet" || m.endsWith("/fleet");
}

/** v1.361.0: the providers that ARE the user's own model server — the two
 *  config slots ("custom", "ollama") and every fleet node (`fleet-<id>`).
 *  Their "fleet" pick is named by the machine alone. A program that reaches
 *  a server through its own alias (OpenCode's "<server>/fleet") is not one
 *  of them: it keeps its own name beside the server's. */
export function isOwnServerSlot(provider: string | null | undefined): boolean {
  const p = (provider ?? "").trim();
  return p === "custom" || p === "ollama" || p.startsWith("fleet-");
}

/** v1.360.0 (lane 10B): the server alias's name mid-sentence ("answered by
 *  your model server") when the machine behind it has no name the user gave
 *  it. A row or heading says it in sentence case (`providerHeading`). */
export const SERVER_PICK_WORDS = "your model server";

/** v1.360.0 (lane 10B): the labels the daemon's fleet registry gives the
 *  two config endpoint slots until the user names the machine
 *  (`FleetRegistry.seeded`; the daemon's twin is
 *  `chat_turn.SEED_ENDPOINT_LABELS`, pinned equal). Not a machine's name. */
export const SEED_ENDPOINT_LABELS: Readonly<Record<string, string>> = {
  custom: "Custom endpoint",
  ollama: "Ollama endpoint",
};

/** A catalog row as `machineName` reads it (a GET /models row). */
export interface NamedRow {
  provider: string;
  model?: string;
  name?: string | null;
}

/** The NAME THE USER GAVE the machine behind `provider` (v1.360.0, lane
 *  10B, G21), or "": the route's own label (the daemon's `route.label`),
 *  else the row's `name`, else — for a config slot ("custom", "ollama"),
 *  which the catalog also lists as its fleet node `fleet-<id>` with the
 *  user's label (lib/ranOnWords says the same) — that twin's name. A
 *  registry default label ("Custom endpoint") and the bare id are not
 *  names. Pure. */
export function machineName(
  provider: string | null | undefined,
  opts: {
    label?: string | null;
    name?: string | null;
    model?: string | null;
    catalog?: readonly NamedRow[] | null;
  } = {},
): string {
  const p = (provider ?? "").trim();
  if (!p) return "";
  const slot = p.startsWith("fleet-") ? p.slice("fleet-".length) : p;
  const usable = (raw: string | null | undefined): string => {
    const v = (typeof raw === "string" ? raw : "").replace(/\s+/g, " ").trim();
    if (!v || v === p || v === slot || v === SEED_ENDPOINT_LABELS[slot]) return "";
    if (opts.model && v === opts.model.trim()) return "";
    return v;
  };
  const direct = usable(opts.label) || usable(opts.name);
  if (direct) return direct;
  const rows = Array.isArray(opts.catalog) ? opts.catalog : [];
  const wanted = (opts.model ?? "").trim();
  // v1.361.0: only a row that describes THIS pick names its machine — the
  // pick's own row, or any row of a fleet node (every row of `fleet-<id>`
  // carries the node's label). A config slot's OTHER rows are not its name:
  // a detected Ollama row's `name` is that model's id ("llama3"), and the
  // "fleet" pick was named after it everywhere.
  const describes = (row: NamedRow, id: string): boolean =>
    !wanted || id.startsWith("fleet-") || (row.model ?? "").trim() === wanted;
  const fromRows = (id: string): string => {
    for (const row of rows) {
      if (row && row.provider === id && describes(row, id)) {
        const v = usable(row.name);
        if (v) return v;
      }
    }
    return "";
  };
  return fromRows(p) || (p.startsWith("fleet-") ? "" : fromRows(`fleet-${p}`));
}

/** Readiness 12A (G33): a provider named in the MIDDLE of a sentence a
 *  person reads ("Claude Code isn't signed in", "DGX Spark isn't reachable
 *  right now"): the user's own model server by the name they gave the
 *  machine (`machineName`, with the /models `catalog` when the caller has
 *  it) else "your model server"; every other provider by `providerDisplay`.
 *  The chat's warning above the message box and the New task door printed
 *  "claude-cli" / "fleet-custom" there. "" for no provider. Pure. */
export function providerWords(
  provider: string | null | undefined,
  catalog?: readonly NamedRow[] | null,
): string {
  const p = (provider ?? "").trim();
  if (!p) return "";
  if (p === "custom" || p.startsWith("fleet-")) return machineName(p, { catalog }) || SERVER_PICK_WORDS;
  return providerDisplay(p);
}

/** *words* opening a sentence: OUR lower-case phrases get a capital ("Your
 *  model server …"); a brand keeps its own casing ("llama.cpp", "xAI"). */
export function sentenceStart(words: string): string {
  if (!words || !/^your\b/.test(words)) return words;
  return words[0].toUpperCase() + words.slice(1);
}

/** What a model-menu ROW calls its model (v1.359.0, lane 9B), from the
 *  words this file already has: the picker's own label ("Opus 5.5"); a
 *  provider-default row by the plan or server it runs on ("Your Claude
 *  plan", "Your ChatGPT plan", "Your model server"); the "fleet" alias by
 *  "Your model server"; else the name the id spells (friendlyModelName:
 *  "Opus 4.8"; an id it cannot read stays as it is, nothing invented). The
 *  raw id belongs in the row's title (`ModelRowBits.modelTitle`).
 *
 *  v1.360.0 (lane 10B): the "fleet" alias is ONE name everywhere — the
 *  machine's name when the user gave it one (the row's own `name`, or with
 *  `catalog` the config slot's fleet twin: `machineName`), else "Your model
 *  server" — the words the reply's receipt, the helper row and card, the
 *  mission screen and the run page now use for the same pick. */
export function modelRowName(
  m: { provider: string; model: string; label?: string | null; name?: string | null },
  catalog?: readonly NamedRow[] | null,
): string {
  const label = typeof m.label === "string" ? m.label.trim() : "";
  if (label) return label;
  const model = (m.model ?? "").trim();
  if (isProviderDefaultModel(model)) {
    const cli = SUBSCRIPTION_CLIS.find((c) => c.provider === m.provider);
    return cli?.planName || providerHeading(m.provider) || m.model;
  }
  if (isServerAlias(model)) {
    return machineName(m.provider, { name: m.name, model, catalog }) || providerHeading("custom");
  }
  return friendlyModelName(model);
}

/** One model as a model LIST names it outside the composer's two-level menu
 *  (v1.359.0 integrate, lane 9B review): the Settings default-model list, an
 *  agent's preferred model, a terminal's assist model, a template, a
 *  project's default and the New-task form all printed "claude-cli ·
 *  subscription" / "custom · fleet". Now one rule: the provider group's name
 *  (`modelGroupName`) and the row's own words (`modelRowName`), "Claude
 *  Code · Opus 4.8" / "Your model server · qwen3"; a provider-default row is
 *  its plan or server alone ("Your Claude plan"); the demo model is the demo
 *  model. The raw `provider · model` belongs in the option's title.
 *
 *  v1.360.0 (lane 10B fix): `catalog` (the GET /models rows the list already
 *  holds) is handed to `modelRowName`, so the "fleet" pick in a list reads
 *  the machine's name ("Your model server · DGX Spark") exactly as the
 *  reply, the menu and the run page say it; without it, "Your model server". */
export function modelPickWords(
  m: {
    provider: string;
    model: string;
    label?: string | null;
    name?: string;
  },
  catalog?: readonly NamedRow[] | null,
): string {
  if (m.provider === "mock") return providerDisplay("mock");
  const words = modelRowName(m, catalog);
  if (isProviderDefaultModel(m.model)) return words;
  const group = modelGroupName({ provider: m.provider, model: m.model, name: m.name });
  if (!group || group === words) return words;
  return `${group} · ${words}`;
}

/** The record behind `modelPickWords`, for an option's title. */
export function modelPickTitle(m: { provider: string; model: string }): string {
  return [m.provider, m.model].filter(Boolean).join(" · ");
}

export interface AnswerCandidate {
  /** What the press posts to W2-1 (a CLI name stays the CLI name; the daemon
   *  promotes it to the inherited API name so the quality dial applies). */
  provider: string;
  label: string;
  /** Where the user's words go: "stays on this PC" / "goes to Anthropic"… */
  where: string;
}

const WHERE: Record<string, string> = {
  "claude-cli": "goes to Anthropic",
  anthropic: "goes to Anthropic",
  "codex-cli": "goes to OpenAI",
  openai: "goes to OpenAI",
  google: "goes to Google",
  xai: "goes to xAI",
  "grok-cli": "goes to xAI",
  openrouter: "goes to OpenRouter",
  ollama: "stays on this PC",
  // v1.358.0 (lane 8D): found on THIS PC's own address (localhost).
  lmstudio: "stays on this PC",
  llamacpp: "stays on this PC",
};

/** Rank: a key the user pasted, then a model served through a signed-in CLI,
 *  then local servers, then the CLIs themselves. Deterministic, so the same
 *  /health always gives the same pick. */
function rank(row: HealthRow): number {
  if (row.class === "api" && !row.inherited_from) return 0;
  if (row.inherited_from) return 1;
  if (row.class === "cli") return 3;
  return 2;
}

/**
 * What a "use it for answers" press can offer: one entry per real thing the
 * user connected. An inherited API row (anthropic served by claude-cli)
 * collapses into its CLI — the user signed in to Claude Code, so that is the
 * name they recognise, and W2-1 maps it back to the inherited API name. More
 * than one entry = a real choice (cloud vs local is the user's privacy call),
 * so every candidate gets its own press — never a silent pick.
 */
export function answerCandidates(health: Health | null | undefined): AnswerCandidate[] {
  const rows = availableRows(health);
  // v1.353.0 (G34): Ollama answering on this PC is ready for the ONE press
  // even before its address is saved — the press saves it (POST
  // /onboarding/use-model, the user's click). Still never chosen on its own.
  const detected = ollamaDetected(health);
  if (detected && !rows.some((r) => r.provider === "ollama")) rows.push(detected);
  const names = new Set(rows.map((r) => r.provider));
  const out: AnswerCandidate[] = [];
  const seen = new Set<string>();
  // v1.358.0 (lane 8D): LM Studio / llama.cpp answering on this PC rank with
  // the local servers (after a pasted key and a signed-in CLI, before a bare
  // CLI) — each its own press, never chosen on its own.
  const ranked: { rank: number; name: string; label: string }[] = [];
  for (const row of rows) {
    const name =
      row.inherited_from && names.has(row.inherited_from) ? row.inherited_from : row.provider;
    ranked.push({ rank: rank(row), name, label: friendlyProvider(name) });
  }
  for (const s of localServersDetected(health)) {
    ranked.push({ rank: 2, name: s.provider, label: s.label });
  }
  for (const r of ranked.sort((a, b) => a.rank - b.rank)) {
    if (seen.has(r.name)) continue;
    seen.add(r.name);
    out.push({ provider: r.name, label: r.label, where: WHERE[r.name] ?? "" });
  }
  return out;
}

/** v1.353.0 (G34): the /health ollama row when the daemon's cached probe
 *  found Ollama running on this PC with a model (`detected`) but it is not
 *  connected yet — undefined otherwise (an older daemon sends no flag). */
export function ollamaDetected(health: Health | null | undefined): HealthRow | undefined {
  const row = healthRow(health, "ollama");
  return row && !row.available && row.detected === true ? row : undefined;
}

/**
 * The press that matches a provider the user saw answer (the wizard's finale
 * offers "Use <this> for answers" for the model that ran its first task). An
 * inherited API name maps back to its CLI — "anthropic" served by a signed-in
 * Claude Code is offered as Claude Code, the name the user recognises.
 * Unknown to the candidates (it went unavailable since) → its own name, so
 * the daemon's 409 can say so in one sentence rather than the press vanishing.
 */
export function candidateFor(
  health: Health | null | undefined,
  provider: string,
): AnswerCandidate | null {
  if (!provider || provider === "mock") return null;
  const candidates = answerCandidates(health);
  const direct = candidates.find((c) => c.provider === provider);
  if (direct) return direct;
  const via = availableRows(health).find((r) => r.provider === provider)?.inherited_from;
  const viaCandidate = via ? candidates.find((c) => c.provider === via) : undefined;
  if (viaCandidate) return viaCandidate;
  return { provider, label: friendlyProvider(provider), where: WHERE[provider] ?? "" };
}

/**
 * The provider the wizard's FIRST task names explicitly, so a demo default
 * cannot answer it (finding wizard-first-task-claims-real-on-mock).
 *  - A real default the user chose → undefined: never override their choice.
 *  - `justEnabled` (the door the user just connected through) wins when it is
 *    available; a CLI is sent as the inherited API name /health lists beside
 *    it (claude-cli → anthropic), so the quality dial applies.
 *  - Otherwise the deterministic rank above: a pasted key, then the inherited
 *    API name of a signed-in CLI, then a local server, then a bare CLI.
 */
export function firstTaskProvider(
  health: Health | null | undefined,
  justEnabled?: string | null,
): string | undefined {
  if (!isDemoDefault(health)) return undefined;
  const rows = availableRows(health);
  if (rows.length === 0) return undefined;
  const inheritedOf = (cli: string) => rows.find((r) => r.inherited_from === cli)?.provider;
  if (justEnabled) {
    const via = inheritedOf(justEnabled);
    if (via) return via;
    if (rows.some((r) => r.provider === justEnabled)) return justEnabled;
  }
  return [...rows].sort((a, b) => rank(a) - rank(b))[0].provider;
}

/** The address that connects the Ollama on THIS PC (its installer's default
 *  port). The connect doors' Ollama press, the Accounts row's "Use Ollama on
 *  this PC" and the daemon's one press (routes/settings.py) all save this. */
export const OLLAMA_LOCAL_URL = "http://localhost:11434";

/* ------------------------------------------------- the subscription door --- */

export interface SubscriptionCli {
  /** The /health row name. */
  provider: "claude-cli" | "codex-cli";
  /** The Build Launch-catalog id `POST /terminals/launch` takes. */
  cli: "claude" | "codex";
  name: string;
  /** How a non-technical user should read it — never "app/CLI". */
  longName: string;
  plan: string;
  /** v1.359.0 (lane 9B): what the model menu calls this CLI's default row
   *  (the raw "subscription"). */
  planName: string;
  installUrl: string;
  installLabel: string;
  /** Used only when the daemon's row carries no `sign_in_fix` text. */
  fallbackFix: string;
  /** What to do in the Build pane "Sign in" opens. PER CLI because the two
   *  differ (review, v1.310.0): Claude Code waits for `/login`; Codex, started
   *  signed out, shows its own sign-in screen — telling a Codex user to type
   *  /login contradicted the sign_in_fix printed right above it. Backticks
   *  mark the words to type. */
  paneHint: string;
}

export const SUBSCRIPTION_CLIS: SubscriptionCli[] = [
  {
    provider: "claude-cli",
    cli: "claude",
    name: "Claude Code",
    longName: "Claude Code (the command-line tool)",
    plan: "Claude Pro or Max",
    planName: "Your Claude plan",
    installUrl: "https://docs.anthropic.com/en/docs/claude-code/setup",
    installLabel: "How to get Claude Code",
    fallbackFix: "Claude Code isn't signed in yet. Run `claude`, then type `/login`.",
    paneHint: "Type `/login` there and finish in your browser.",
  },
  {
    provider: "codex-cli",
    cli: "codex",
    name: "Codex",
    longName: "Codex (OpenAI's command-line tool)",
    plan: "ChatGPT Plus or Pro",
    planName: "Your ChatGPT plan",
    installUrl: "https://github.com/openai/codex",
    installLabel: "How to get Codex",
    fallbackFix: "Codex isn't signed in yet. Run `codex login`.",
    paneHint:
      "Codex shows its own sign-in screen there — choose Sign in with ChatGPT and finish in your browser.",
  },
];

export type CliDoorState = "unknown" | "not-installed" | "checking" | "signed-out" | "signed-in";

/** Read one CLI's state off its /health row (W2-3: `installed`/`signed_in`).
 *  A row without `installed` (an older daemon) is "unknown" — the door then
 *  keeps the plain re-check instead of guessing. Installed with `signed_in:
 *  null` is "checking" (review, v1.310.0): null is W2-3's "unknown" — the
 *  cached probe has not run yet — and saying "not signed in" there would be a
 *  guess. Only an explicit `false` is signed out. */
export function cliDoorState(row: HealthRow | undefined): CliDoorState {
  if (!row) return "unknown";
  if (row.available || row.signed_in === true) return "signed-in";
  if (typeof row.installed !== "boolean") return "unknown";
  if (!row.installed) return "not-installed";
  return row.signed_in === false ? "signed-out" : "checking";
}

export function healthRow(health: Health | null | undefined, provider: string): HealthRow | undefined {
  return ((health?.providers ?? []) as HealthRow[]).find((p) => p.provider === provider);
}
