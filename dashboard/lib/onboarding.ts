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
 *  shared lib/types.ts, which this track does not own. */
export type HealthRow = ProviderHealth & { sign_in_fix?: string };

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
  anthropic: "Anthropic",
  openai: "OpenAI",
  google: "Google Gemini",
  xai: "xAI",
  openrouter: "OpenRouter",
  ollama: "Ollama",
  custom: "your own endpoint",
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
  if (p === "mock") return "Demo model (scripted)";
  if (p === "auto") return "Auto";
  return friendlyProvider(p);
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
  const names = new Set(rows.map((r) => r.provider));
  const out: AnswerCandidate[] = [];
  const seen = new Set<string>();
  for (const row of [...rows].sort((a, b) => rank(a) - rank(b))) {
    const name =
      row.inherited_from && names.has(row.inherited_from) ? row.inherited_from : row.provider;
    if (seen.has(name)) continue;
    seen.add(name);
    out.push({ provider: name, label: friendlyProvider(name), where: WHERE[name] ?? "" });
  }
  return out;
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
