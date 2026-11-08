"use client";

/**
 * The three connect doors, shared (v1.310.0, wave 2 — "the first five
 * minutes"). Lifted out of FirstRunWizard so the wizard's step 1, its gated
 * step 3, and the chat empty state all offer the SAME way forward.
 *
 * What changed on the way out (findings subscription-door-dead-end,
 * mock-default-trap-cli-ollama, demo-mode-chat-nonsense — the verifier's
 * fix_adjustment wins over each proposal):
 *
 *  - THE SUBSCRIPTION DOOR TELLS THE TRUTH in three states, read off the
 *    /health claude-cli / codex-cli rows (W2-3) the dashboard already polls —
 *    no new probe:
 *      not installed → one sentence that a desktop/web-app subscription cannot
 *        be shared here + a plain link to the vendor's page. No `irm | iex`,
 *        no "install in a Build pane" (Claude Code's npm path needs Node, which
 *        a non-developer PC does not have);
 *      installed, signed out → the CLI's OWN fix text (cli_auth.SIGN_IN_FIX)
 *        + "Sign in", which opens a Build pane running the CLI
 *        (`POST /terminals/launch` — the click is the consent); the user still
 *        finishes the sign-in there, and the door says HOW per CLI
 *        (`paneHint`: Claude Code's `/login`, Codex's own sign-in screen);
 *      installed, sign-in not checked yet (`signed_in: null`) → a neutral
 *        "checking" line + the re-check, never a guessed "not signed in";
 *      signed in → green.
 *    An older daemon whose rows carry no `installed` keeps the plain re-check.
 *  - "USE IT FOR ANSWERS" is the ONE explicit promotion press (W2-1). A
 *    signed-in CLI or a reachable Ollama does NOT make answers real while the
 *    default every turn uses is still the offline demo — and cloud vs local
 *    is the user's privacy decision, so nothing switches on its own. The press
 *    is offered only while the default is the demo, one per real thing that
 *    is ready (a real choice stays the user's), and it says what happened:
 *    the new default, the daemon's "you already chose X", or its 409 sentence.
 *  - The Ollama door ends in the same press once Ollama answers.
 *  - v1.353.0 (wave 3, G33/G35): the signed-out line no longer prints the
 *    CLI's terminal sentence above its own Sign in button — it says to press
 *    Sign in and what happens in the window that opens (lib/cliSignIn, the
 *    same press Settings › Connections › Accounts offers). A promotion calls
 *    `onChosen(label)` with the PRESSED words, so the chat home can keep
 *    saying "Done — <label> answers you now." after the doors are gone.
 *  - v1.313.0 (opencode-press-no-privacy-line-raw-id): a press is offered
 *    ONLY for what that press can choose. POST /onboarding/use-model takes
 *    claude-cli, codex-cli, ollama, custom and the API providers; anything
 *    else (an OpenCode or Grok sign-in, a fleet node) answered 409 "can't be
 *    chosen with this button" — a press that could only fail, printed under
 *    its raw id with no line about where the words go. Those are now named in
 *    plain words beside a link to Connections, where they CAN be chosen.
 *    Every press that remains says where the words go, and the two sign-ins
 *    use the daemon's own names (checklist.py's _PROVIDER_LABELS), so Chat
 *    and the Overview call the same choice the same thing.
 *
 * The key door keeps the pre-v1.197.0 mechanics byte-for-byte.
 *
 *  - v1.355.0 (wave 5, lane 5D): the key door takes a Google Gemini key (an
 *    AI Studio key, G46; the old "Gemini connects with account login"
 *    pointer is gone); its model-server choice ("Model servers on your
 *    network", the Connections card's words) offers "LM Studio on this PC" and
 *    "llama.cpp", which fill the address and list the server's models with
 *    the Connections form's own picker (G72); and the free-and-private door's
 *    step 2 is a "Download a starter model" press that opens a Build pane
 *    running the download, with no command to copy (G73, lib/starterModel).
 *  - v1.449.0 (readiness Wave C, items 20, 21, 25): a subscriber WITHOUT the
 *    command-line tool gets "Install Claude Code for me" / "Install Codex for
 *    me" — a Build terminal running the vendor's own documented installer
 *    (lib/cliInstall; never silent, the terminal shows it), then "I've
 *    installed it — check now" (the rescan), then the Sign in press as
 *    before. The Ollama door reads the row's `installed` / `running`: step 2
 *    is greyed until Ollama is on this PC, "running but has no model yet" is
 *    said in words, step 3 is folded into the ONE "Use Ollama for answers"
 *    press (the same use-model press as the Ready panel; it saves this PC's
 *    address as part of the press), and Gemini's free key is named as the
 *    quick alternative. The Anthropic and OpenAI key tabs say where a key
 *    comes from and that it is billed per use; the model-servers tab reads
 *    the `?check=1` answer, so a failed check says "Nothing was saved" there
 *    too.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { CheckCircle2, Download, KeyRound, Loader2, RefreshCw, TerminalSquare } from "lucide-react";
import { post, put } from "@/lib/api";
import { cacheSet, cachedGet } from "@/lib/apiCache";
import { codeParts, paneHref, signedOutLine, useCliSignIn } from "@/lib/cliSignIn";
import {
  INSTALLED_CHECK_LABEL,
  installLabel,
  installingLine,
  stillNotInstalledLine,
  useCliInstall,
} from "@/lib/cliInstall";
import { EndpointModelPicker } from "@/components/connections/EndpointModelPicker";
import { LocalServerChips, useFetchModelsSoon } from "@/components/connections/LocalServerChips";
import type { LocalServerPreset } from "@/lib/localServerPresets";
import { STARTER_MODEL_LABEL, useStarterModel } from "@/lib/starterModel";
import { useDaemon } from "@/lib/daemon";
import {
  LOCAL_SERVER_PROVIDERS,
  OLLAMA_LOCAL_URL,
  SUBSCRIPTION_CLIS,
  answerCandidates,
  cliDoorState,
  friendlyProvider,
  healthRow,
  isDemoDefault,
  keyShapeProvider,
  type AnswerCandidate,
  type KeySaveAnswer,
  type SubscriptionCli,
} from "@/lib/onboarding";
import type { ConnectionTestResult } from "@/lib/types";
import { AnswerPressNote, useAnswerPress } from "./AnswerPress";
import { MODEL_SERVERS_TITLE } from "@/lib/modelServers";
import { MODEL_NAME_LABEL, MODEL_SERVER_ADDRESS } from "@/lib/chatControlWords";
// v1.420.0 (C6.6): the Test's plain words; (C6.12) the free tiers on record.
import { testWords, type ConnectionTestWords } from "@/lib/connectionTestWords";
import { DETAILS_WORDS } from "@/lib/failureActions";
import { freeTierNote, freeTierWords } from "@/lib/freeTiers";
// v1.357.0 (G76): the ONE demo name.
import { DEMO_MODEL, DEMO_REPLIES } from "@/lib/demoWords";

type Door = "subscription" | "local" | "key";

const DOORS: { id: Door; label: string; hint: string }[] = [
  {
    id: "subscription",
    label: "I already pay for Claude or ChatGPT",
    hint: "Use Claude Code or Codex, signed in on this PC",
  },
  {
    id: "local",
    label: "Free & private on this PC",
    hint: "Run a local model with Ollama — nothing leaves the machine",
  },
  {
    id: "key",
    label: "I have an API key",
    hint: "Paste it here — about 30 seconds",
  },
];

type KeyProvider = "anthropic" | "openai" | "google" | "custom";

const KEY_PROVIDERS: {
  id: KeyProvider;
  label: string;
  placeholder: string;
  /** Where to get one, when a new user may not know (v1.355.0). */
  keyUrl?: string;
  keyLabel?: string;
  /** v1.449.0 (item 25): how that key is charged, in one sentence. Only
   *  what the vendor's own key page says: a pay-as-you-go account, billed
   *  for what the answers use, not a monthly plan. */
  billing?: string;
}[] = [
  // v1.449.0 (item 25): where a key comes from, and that it is billed per
  // use — a new user with a Claude.ai or ChatGPT PLAN often has no key and
  // does not know the key is a second, metered account. Pages read
  // 2026-11-07: console.anthropic.com/settings/keys, platform.openai.com/api-keys.
  {
    id: "anthropic",
    label: "Anthropic",
    placeholder: "sk-ant-…",
    keyUrl: "https://console.anthropic.com/settings/keys",
    keyLabel: "the Anthropic Console",
    billing: "Anthropic bills that account per use (pay as you go) — it is separate from a Claude plan.",
  },
  {
    id: "openai",
    label: "OpenAI",
    placeholder: "sk-…",
    keyUrl: "https://platform.openai.com/api-keys",
    keyLabel: "OpenAI's platform",
    billing: "OpenAI bills that account per use (pay as you go) — it is separate from a ChatGPT plan.",
  },
  // v1.355.0 (G46): the AI Studio key most Gemini users have. It used to be a
  // pointer to the account login, because the google connection refused a key.
  {
    id: "google",
    label: "Google Gemini",
    // A new AI Studio key starts "AQ." (since 2026-05-28); an older one "AIza".
    placeholder: "AQ.… or AIza…",
    keyUrl: "https://aistudio.google.com/apikey",
    keyLabel: "Google AI Studio",
  },
  // v1.355.0 integrate: the Connections card's own words for the same slot
  // (lib/modelServers), so the first-run door and Settings say one thing.
  { id: "custom", label: MODEL_SERVERS_TITLE, placeholder: "sk-… (optional)" },
];

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** v1.445.0: the door picked and the Sign in panes opened, kept for THIS
 *  VISIT (module memory in lib/apiCache: never on disk, gone on a reload).
 *  Leaving Chat for the Build terminal unmounts the doors; coming back used
 *  to close the door and forget the pane, so the row offered Sign in again
 *  and a second terminal opened. A key that is not a GET path, so no reader
 *  of the cache ever mistakes it for an answer. */
export const DOORS_VISIT_KEY = "ui:connect-doors/visit";

interface DoorsVisit {
  door: Door | null;
  panes: Record<string, string | null>;
}

function readVisit(): DoorsVisit {
  const v = cachedGet<DoorsVisit>(DOORS_VISIT_KEY);
  return v && typeof v === "object"
    ? { door: v.door ?? null, panes: { ...(v.panes ?? {}) } }
    : { door: null, panes: {} };
}

function writeVisit(patch: Partial<DoorsVisit>): void {
  const now = readVisit();
  cacheSet(DOORS_VISIT_KEY, { ...now, ...patch, panes: { ...now.panes, ...(patch.panes ?? {}) } });
}

/** v1.445.0: the check-now press, and what it says when the CLI still reads
 *  signed out after a fresh look. */
export const CHECK_NOW_LABEL = "I've signed in — check now";
export function stillSignedOutLine(name: string): string {
  return `${name} still says it isn't signed in. Finish the sign-in in the Build terminal, then press “${CHECK_NOW_LABEL}” again.`;
}

/** The plain name of a key's company, as it reads in "This looks like
 *  an OpenAI key". */
const KEY_SHAPE_NAMES: Record<string, string> = {
  anthropic: "an Anthropic",
  openai: "an OpenAI",
  google: "a Google Gemini",
};

/** What POST /onboarding/use-model can choose: readiness.USE_MODEL_CLIS +
 *  USE_MODEL_LOCAL + providers.manager.API_PROVIDERS. Keep in step with the
 *  daemon — a name outside this set is answered 409, so it gets no press. */
const ONE_PRESS = new Set([
  "claude-cli",
  "codex-cli",
  "ollama",
  "custom",
  "anthropic",
  "openai",
  "google",
  "xai",
  "openrouter",
  // v1.358.0 (lane 8D): LM Studio / llama.cpp found on this PC (the /health
  // custom row's detected_servers) — the use-model door saves the
  // model-server slot and chooses them; lib/onboarding names them.
  ...LOCAL_SERVER_PROVIDERS,
]);

/** The press names, in checklist.py's _PROVIDER_LABELS words (the Overview's
 *  card reads them off /onboarding), so one choice has one name everywhere.
 *  The sign-in in brackets tells a Claude Code sign-in apart from a pasted
 *  Anthropic key. */
const PRESS_LABELS: Record<string, string> = {
  "claude-cli": "Claude (your Claude Code sign-in)",
  "codex-cli": "ChatGPT (your Codex sign-in)",
  // v1.353.0 (G34): one name with checklist.py's _PROVIDER_LABELS.
  ollama: "Ollama (on this PC)",
  custom: "Your own model server",
};

/** Where the words go when lib/onboarding's WHERE table has no entry — never
 *  an empty line beside a press. */
const WHERE_FALLBACK: Record<string, string> = {
  custom: "goes only to the model server you set up",
};

/** Plain names for what is connected but chosen on Connections, never a raw
 *  id like "opencode-cli". */
const OTHER_NAMES: Record<string, string> = {
  "opencode-cli": "OpenCode",
  "grok-cli": "Grok",
  // Wave D item 33: the canon's one name for the user's own servers
  // (lib/loopWords says the same).
  fleet: "Your model servers",
};

/** A label as it reads mid-sentence ("With your own model server, …"). */
function midSentence(label: string): string {
  return /^(Your|The|A|An)\b/.test(label) ? label[0].toLowerCase() + label.slice(1) : label;
}

/** "OpenCode", "OpenCode and Grok", "A, B and C". */
function andList(names: string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** A sentence with `backticked` words to type, drawn as code. */
function withCode(text: string) {
  return codeParts(text).map((part, i) =>
    part.code ? (
      <code key={i} className="font-mono text-zinc-300">
        {part.text}
      </code>
    ) : (
      part.text
    ),
  );
}

function keyPickFor(id: string) {
  return KEY_PROVIDERS.find((p) => p.id === id);
}

export function ConnectDoors({
  onChanged,
  onEnabled,
  onChosen,
  onOpenPane,
  className = "",
  layout = "stack",
}: {
  /** After anything connects (the wizard also reloads its checklist). Default:
   *  the DaemonProvider's /health refresh. */
  onChanged?: () => void;
  /** The provider the user just enabled through a door — the wizard sends it
   *  explicitly on the first task. */
  onEnabled?: (provider: string) => void;
  /** v1.353.0 (G35): a "Use … for answers" press really made a model the
   *  default — `label` is the press's own words ("Claude (your Claude Code
   *  sign-in)"). The chat home keeps saying it once the doors are gone. */
  onChosen?: (label: string) => void;
  /** The user is leaving for the Build pane a Sign in opened (the wizard
   *  steps aside so the pane is visible). */
  onOpenPane?: () => void;
  className?: string;
  /** v1.314.0: "row" lays the three doors side by side from `sm` up. Opt-in:
   *  the chat empty state passes it so its starter prompts reach the fold;
   *  the first-run wizard's narrower modal keeps the default stacked doors. */
  layout?: "stack" | "row";
}) {
  const { health, refresh } = useDaemon();
  const after = onChanged ?? refresh;
  const demo = isDemoDefault(health);
  const ready = answerCandidates(health);
  // v1.313.0: a press only for what the one-press route can choose, named and
  // with its where-line as the Overview says it.
  const candidates: AnswerCandidate[] = ready
    .filter((c) => ONE_PRESS.has(c.provider))
    .map((c) => ({
      ...c,
      label: PRESS_LABELS[c.provider] ?? c.label,
      where: c.where || WHERE_FALLBACK[c.provider] || "goes to the service you connected",
    }));
  // ...and what is connected but can only be chosen on Connections.
  const elsewhere = ready
    .filter((c) => !ONE_PRESS.has(c.provider))
    .map((c) => OTHER_NAMES[c.provider] ?? friendlyProvider(c.provider));

  /* Which door is open. Local state ONLY (v1.197.0): remembering it would
     resurrect a stale pick on the next visit, and re-picking costs one click. */
  const [door, setDoorState] = useState<Door | null>(() => readVisit().door);
  function setDoor(next: Door | null) {
    setDoorState(next);
    writeVisit({ door: next });
  }

  // --- the ONE explicit promotion press (W2-1, components/onboarding/
  //     AnswerPress — the wizard's finale says it the same way) ------------
  const { busy: pressBusy, note: pressNote, press } = useAnswerPress(after);

  // --- subscription door ---------------------------------------------------
  const [rescanBusy, setRescanBusy] = useState(false);
  /** v1.445.0: per CLI, the check-now press's answer when it still reads
   *  signed out. */
  const [checkNote, setCheckNote] = useState<Record<string, string>>({});

  async function rescan(c?: SubscriptionCli, lookFor: "signed-in" | "installed" = "signed-in") {
    setRescanBusy(true);
    if (c) setCheckNote((n) => ({ ...n, [c.cli]: "" }));
    try {
      // The rescan RE-PROBES each installed CLI's sign-in (blocking, on the
      // daemon's threadpool), so the very next /health read says the truth.
      // v1.449.0 (item 20): it also looks for the tool's program again (the
      // presence memo is dropped first), so a tool installed a moment ago is
      // found at once — the rescan lists a subscription CLI only when its
      // program is on this PC.
      const res = await post<{ detected?: { provider?: string; signed_in?: boolean | null }[] }>(
        "/providers/rescan",
      );
      if (c) {
        const row = (res?.detected ?? []).find((r) => r?.provider === c.provider);
        if (lookFor === "installed") {
          if (!row) setCheckNote((n) => ({ ...n, [c.cli]: stillNotInstalledLine(c.name) }));
        } else if (!row || row.signed_in !== true) {
          setCheckNote((n) => ({ ...n, [c.cli]: stillSignedOutLine(c.name) }));
        }
      }
    } catch (e) {
      if (c) setCheckNote((n) => ({ ...n, [c.cli]: errText(e) }));
    } finally {
      after();
      setRescanBusy(false);
    }
  }

  /* Sign in = a NEW Build pane with the CLI started in it, on THIS PC's own
     login ("default" — never an Iron-Proxy account the user did not pick).
     The click is the consent (the ResumeStrip rule). The CLI still asks the
     user to type /login; the door says so instead of pretending.
     v1.353.0 (G33): the press lives in lib/cliSignIn, shared with Settings ›
     Connections › Accounts. */
  const { signIn: signInNow, start: startSignIn } = useCliSignIn((c: SubscriptionCli) => {
    onEnabled?.(c.provider);
    after();
  });
  // v1.445.0: a pane opened earlier in this visit is still "the" sign-in
  // pane after the doors were gone and came back (no second terminal).
  const [rememberedPanes] = useState<Record<string, string | null>>(() => readVisit().panes);
  useEffect(() => {
    const opened: Record<string, string | null> = {};
    for (const [cli, st] of Object.entries(signInNow)) {
      if (st && st.paneId !== undefined) opened[cli] = st.paneId;
    }
    if (Object.keys(opened).length) writeVisit({ panes: opened });
  }, [signInNow]);
  const signIn: typeof signInNow = { ...signInNow };
  for (const [cli, paneId] of Object.entries(rememberedPanes)) {
    if (!cli.startsWith("install:") && !signIn[cli]) signIn[cli] = { paneId };
  }

  /* v1.449.0 (item 20): "Install Claude Code for me" — a Build terminal
     running the vendor's own installer (lib/cliInstall), remembered for the
     visit under "install:<cli>" so a return from the terminal never opens a
     second one. The click is the consent; the terminal shows every step. */
  const { install: installNow, start: startInstall } = useCliInstall(() => after());
  useEffect(() => {
    const opened: Record<string, string | null> = {};
    for (const [cli, st] of Object.entries(installNow)) {
      if (st && st.paneId !== undefined) opened[`install:${cli}`] = st.paneId;
    }
    if (Object.keys(opened).length) writeVisit({ panes: opened });
  }, [installNow]);
  const install: typeof installNow = { ...installNow };
  for (const [key, paneId] of Object.entries(rememberedPanes)) {
    if (key.startsWith("install:") && !install[key.slice("install:".length)]) {
      install[key.slice("install:".length)] = { paneId };
    }
  }

  /* Ollama door (v1.197.0). Rescan does NOT detect Ollama: the provider only
     reports available once `ollama_base_url` is CONFIGURED. Saving the default
     local URL is the real mechanism; availability still requires the server
     to actually be REACHABLE, so this cannot fake a green. */
  const [ollamaBusy, setOllamaBusy] = useState(false);
  const [ollamaSaved, setOllamaSaved] = useState(false);
  const [ollamaError, setOllamaError] = useState<string | null>(null);
  const ollamaRow = healthRow(health, "ollama");
  const ollamaUp = Boolean(ollamaRow?.available);
  /* v1.449.0 (item 21): the three facts the door says (daemon: the ollama
     row's `installed` / `running` / `detected`). An older daemon sends none
     of the first two: "not known" greys nothing and claims nothing. */
  const ollamaKnown = typeof ollamaRow?.running === "boolean";
  const ollamaRunning = ollamaRow?.running === true || ollamaUp || ollamaRow?.detected === true;
  const ollamaOnPc = ollamaRow?.installed === true || ollamaRunning;
  const ollamaHasModel = ollamaUp || ollamaRow?.detected === true;
  /** The one "Use Ollama for answers" press: the SAME use-model press the
   *  Ready panel offers (it saves this PC's address as part of the press). */
  const ollamaCandidate: AnswerCandidate = {
    provider: "ollama",
    label: PRESS_LABELS.ollama,
    where: candidates.find((c) => c.provider === "ollama")?.where ?? "stays on this PC",
  };

  async function connectOllama() {
    setOllamaBusy(true);
    setOllamaError(null);
    try {
      await put("/settings", { values: { ollama_base_url: OLLAMA_LOCAL_URL } });
      setOllamaSaved(true);
      onEnabled?.("ollama");
      after(); // immediate re-check — don't wait for the 5s health poll
    } catch (e) {
      setOllamaError(errText(e));
    } finally {
      setOllamaBusy(false);
    }
  }

  /* v1.355.0 (G73): "Download a starter model" opens a Build pane running the
     download (lib/starterModel: the Sign in press's door and pane hint). */
  const starter = useStarterModel();

  // --- key door (the pre-v1.197.0 form, unchanged) -------------------------
  const [keyProvider, setKeyProvider] = useState<KeyProvider>("anthropic");
  const [keyValue, setKeyValue] = useState("");
  const [customBaseUrl, setCustomBaseUrl] = useState("");
  const [customModel, setCustomModel] = useState("");
  const [keyBusy, setKeyBusy] = useState(false);
  const [keyResult, setKeyResult] = useState<ConnectionTestResult | null>(null);
  const [keyError, setKeyError] = useState<string | null>(null);
  /** v1.445.0: the tab the key's SHAPE switched away from (an OpenAI key
   *  pasted into the Anthropic tab, the usual slip), for the line saying so. */
  const [shapeSwitchedFrom, setShapeSwitchedFrom] = useState<KeyProvider | null>(null);
  /** v1.445.0: this Connect also chose the key for answers (no "choose it
   *  above" line under a result the press already acted on). */
  const [keyChose, setKeyChose] = useState(false);
  /** v1.445.0: the daemon said the key failed its test and was NOT stored. */
  const [keyNotSaved, setKeyNotSaved] = useState(false);

  function onKeyInput(value: string) {
    setKeyValue(value);
    if (keyProvider === "custom") return;
    const shaped = keyShapeProvider(value);
    // Only when the value CHANGES into another tab's shape: a tab the user
    // then picks by hand stands (the mismatch line offers the switch back).
    if (shaped && shaped !== keyProvider && shaped !== keyShapeProvider(keyValue)) {
      setShapeSwitchedFrom(keyProvider);
      setKeyProvider(shaped);
      setKeyResult(null);
      setKeyError(null);
    }
  }

  /* v1.355.0 (G72): "LM Studio on this PC" / "llama.cpp" fill the address and
     press the model picker's own "Fetch available models" below it. */
  const customRef = useRef<HTMLDivElement>(null);
  const fetchModelsSoon = useFetchModelsSoon(customRef);
  function pickLocalServer(preset: LocalServerPreset) {
    setCustomBaseUrl(preset.baseUrl);
    setKeyError(null);
    setKeyResult(null);
    fetchModelsSoon();
  }

  /* v1.445.0: TEST FIRST, CHOOSE ONLY AFTER A PASS. The key used to be
     saved AND made the default before any test (the route's first-connect
     promotion); the doors then unmounted (the default was no longer the
     demo) and a wrong key's error went with them. Now `?check=1` tests the
     key before it is stored (a failed test stores nothing and changes
     nothing), and only a passing key is chosen for answers — with the same
     one press as "Use … for answers", whose "Done — … answers you now" the
     chat keeps saying after the doors are gone (onChosen). */
  async function connectKey() {
    const isCustom = keyProvider === "custom";
    if (isCustom ? !customBaseUrl.trim() : !keyValue.trim()) return;
    setKeyBusy(true);
    setKeyError(null);
    setKeyResult(null);
    setKeyChose(false);
    setKeyNotSaved(false);
    try {
      let result: ConnectionTestResult;
      let saved = true;
      if (isCustom) {
        // Save the endpoint FIRST so a key-less local server (LM Studio, etc.)
        // still sticks, then optionally attach a key.
        await put("/settings", {
          values: { custom_base_url: customBaseUrl.trim(), custom_model: customModel.trim() },
        });
        // v1.449.0 (item 25): the `?check=1` answer is READ here too — a key
        // the server refused was not stored, and the panel says so, instead
        // of a Test's failure with nothing about what was kept.
        let keyAnswer: KeySaveAnswer | undefined;
        if (keyValue.trim()) {
          keyAnswer = await post<KeySaveAnswer>(`/connections/custom/key?check=1`, { key: keyValue.trim() });
        }
        if (keyAnswer?.saved === false && keyAnswer.test && typeof keyAnswer.test.ok === "boolean") {
          saved = false;
          result = keyAnswer.test as ConnectionTestResult;
        } else {
          result = await post<ConnectionTestResult>(`/connections/custom/test`);
        }
      } else {
        const answer = await post<KeySaveAnswer>(`/connections/${keyProvider}/key?check=1`, {
          key: keyValue.trim(),
        });
        saved = answer?.saved !== false;
        // The route's own test when it ran one; an older daemon (no `test`)
        // stored the key, so the Test route says what it thinks.
        result =
          answer?.test && typeof answer.test.ok === "boolean"
            ? (answer.test as ConnectionTestResult)
            : await post<ConnectionTestResult>(`/connections/${keyProvider}/test`);
      }
      setKeyResult(result);
      setKeyNotSaved(!saved);
      if (saved) setKeyValue("");
      if (result?.ok && saved) {
        onEnabled?.(keyProvider);
        if (demo) {
          setKeyChose(true);
          const label = PRESS_LABELS[keyProvider] ?? keyPickFor(keyProvider)?.label ?? keyProvider;
          await press({ provider: keyProvider, label, where: "" }, (_provider, chosen) =>
            onChosen?.(chosen),
          );
          return; // the press refreshed /health itself
        }
      }
      after(); // immediate green — don't wait for the 5s health poll
    } catch (e) {
      setKeyError(errText(e));
    } finally {
      setKeyBusy(false);
    }
  }

  const states = SUBSCRIPTION_CLIS.map((c) => ({
    c,
    row: healthRow(health, c.provider),
    state: cliDoorState(healthRow(health, c.provider)),
  }));
  const anyNotInstalled = states.some((s) => s.state === "not-installed");
  /* The re-check is offered while any row cannot say yet: an older daemon's
     row ("unknown") or an installed CLI whose sign-in probe has not run
     ("checking"). Both turn green by themselves once it does. */
  const anyUnknown = states.some((s) => s.state === "unknown" || s.state === "checking");
  const showPress = demo && candidates.length > 0;
  const keyPick = keyPickFor(keyProvider);
  const keyShape = keyProvider === "custom" ? null : keyShapeProvider(keyValue);
  const keyWords = testWords(keyResult as ConnectionTestWords | null);

  return (
    <div className={className}>
      {showPress ? (
        /* Ready, but the demo still answers: the explicit press. Never the
           words "answers are real" here — they are not, until the press. */
        <div className="rounded-xl border border-tone-success/25 bg-tone-success/[0.06] px-4 py-3">
          <div className="flex items-center gap-2 text-sm font-medium text-tone-success">
            <CheckCircle2 size={16} className="text-tone-success" />
            {/* v1.313.0: the presses below name each one in full; the heading
                no longer repeats every long name before them. v1.353.0 (G35):
                not "on this PC" — a pasted cloud key is ready too, and it
                does not run here. */}
            Ready — choose what answers you
          </div>
          <p className="mt-1 text-xs leading-relaxed text-tone-success/80">
            Replies still come from the {DEMO_MODEL} until you choose what answers you —
            nothing switches on its own.
          </p>
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {/* v1.313.0: every offer in the SAME outlined style — a solid
                first button read as a recommendation, and which model
                answers (cloud or this PC) is the user's call. */}
            {candidates.map((c) => (
              <button
                key={c.provider}
                type="button"
                onClick={() =>
                  void press(c, (provider, label) => {
                    onEnabled?.(provider);
                    onChosen?.(label);
                  })
                }
                disabled={pressBusy !== null}
                className="inline-flex items-center gap-1.5 rounded-lg border border-accent/35 bg-accent/[0.08] px-3 py-1.5 text-left text-xs font-semibold text-accent-soft transition-colors hover:border-accent/50 hover:bg-accent/[0.14] disabled:cursor-not-allowed disabled:opacity-40"
              >
                {pressBusy === c.provider && (
                  <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                )}
                Use {c.label} for answers
              </button>
            ))}
          </div>
          {candidates.some((c) => c.where) && (
            <p className="mt-2 text-[11px] leading-relaxed text-tone-success/60">
              {candidates
                .filter((c) => c.where)
                .map((c) => `With ${midSentence(c.label)}, what you type ${c.where}.`)
                .join(" ")}
            </p>
          )}
        </div>
      ) : (
        <p className="text-xs leading-relaxed text-zinc-500">
          {demo
            ? `Until a model is connected, replies come from the ${DEMO_MODEL}: ${DEMO_REPLIES}. Pick whichever sounds like you:`
            : "No model is answering right now. Pick whichever sounds like you:"}
        </p>
      )}

      {/* v1.313.0: connected, but not choosable with one press here — said
          in plain words, with the way to choose it in the same sentence.
          "too" only when a one-press offer sits above it; when this is the
          ONLY connected model, "too" has nothing to refer to and reads as if
          the demo line above were wrong. */}
      {demo && elsewhere.length > 0 && (
        <p className="mt-2 text-[11px] leading-relaxed text-zinc-500">
          {andList(elsewhere)} {elsewhere.length === 1 ? "is" : "are"} connected
          {showPress ? " too" : ""} — choose {elsewhere.length === 1 ? "it" : "one"} to answer on
          the{" "}
          <Link
            href="/connections"
            className="text-zinc-400 underline decoration-zinc-700 underline-offset-2 transition-colors hover:text-zinc-200"
          >
            Connections page
          </Link>
          .
        </p>
      )}

      <AnswerPressNote note={pressNote} />

      {/* The three doors (v1.197.0) — plain-language first, mechanics
          revealed only for the picked one. */}
      <div className={`mt-3 grid gap-1.5${layout === "row" ? " sm:grid-cols-3" : ""}`}>
        {DOORS.map((d) => {
          const active = door === d.id;
          return (
            <button
              key={d.id}
              type="button"
              onClick={() => setDoor(d.id)}
              aria-pressed={active}
              className={`rounded-xl border px-3.5 py-2.5 text-left transition-colors ${
                active
                  ? "border-accent/40 bg-accent/[0.08]"
                  : "border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.04]"
              }`}
            >
              <span className={`block text-xs font-semibold ${active ? "text-accent-soft" : "text-zinc-200"}`}>
                {d.label}
              </span>
              <span className="mt-0.5 block text-[11px] text-zinc-500">{d.hint}</span>
            </button>
          );
        })}
      </div>

      {/* Door: a Claude / ChatGPT plan, through the vendor's command-line tool. */}
      {door === "subscription" && (
        <div className="mt-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5">
          <p className="text-xs leading-relaxed text-zinc-400">
            Iron Jarvis can use your plan through each company&apos;s own app, signed in on
            this PC: Claude Code for a Claude Pro or Max plan, or Codex for ChatGPT Plus or
            Pro. Nothing to paste, and it never signs in for you.
          </p>
          {anyNotInstalled && (
            <p className="mt-2 text-xs leading-relaxed text-zinc-400">
              A subscription you use in the desktop app or on the web can&apos;t be shared with
              other apps — only these command-line tools can lend it to Iron Jarvis.
            </p>
          )}

          <ul className="mt-3 space-y-2.5">
            {states
              .filter((s) => s.state !== "unknown")
              .map(({ c, state }) => {
                const si = signIn[c.cli] ?? {};
                return (
                  <li key={c.provider} className="text-xs leading-relaxed text-zinc-400">
                    {state === "signed-in" && (
                      <span className="inline-flex items-center gap-1.5 text-tone-success">
                        <CheckCircle2 size={13} className="text-tone-success" />
                        {c.name} is signed in on this PC.
                        {demo ? " Choose it above to use it for answers." : ""}
                      </span>
                    )}
                    {state === "not-installed" && (
                      <>
                        <span className="text-zinc-300">{c.longName}</span> isn&apos;t on this PC
                        yet.
                        {/* v1.449.0 (item 20): one press installs it in a Build
                            terminal running the vendor's own installer, in
                            view — never silently. The vendor's page stays as
                            the way to read about it first. */}
                        {(install[c.cli] ?? {}).paneId !== undefined ? (
                          <span
                            data-testid={`install-pane-${c.cli}`}
                            className="mt-1.5 block text-zinc-400"
                          >
                            {installingLine(c.name)}{" "}
                            <Link
                              href={paneHref(install[c.cli]?.paneId)}
                              onClick={() => onOpenPane?.()}
                              className="font-medium text-accent-soft underline decoration-accent/40 underline-offset-2 transition-colors hover:text-accent"
                            >
                              Open the Build terminal
                            </Link>
                            {!installNow[c.cli] && (
                              <>
                                {" · "}
                                <button
                                  type="button"
                                  data-testid={`install-again-${c.cli}`}
                                  onClick={() => void startInstall(c.cli)}
                                  className="text-zinc-400 underline decoration-zinc-700 underline-offset-2 transition-colors hover:text-zinc-200"
                                >
                                  Closed it? Open a new one
                                </button>
                              </>
                            )}
                          </span>
                        ) : (
                          <span className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                            <button
                              type="button"
                              data-testid={`install-${c.cli}`}
                              onClick={() => void startInstall(c.cli)}
                              disabled={install[c.cli]?.busy}
                              className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:bg-white/[0.06] disabled:opacity-50"
                            >
                              {install[c.cli]?.busy ? (
                                <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                              ) : (
                                <TerminalSquare size={13} aria-hidden="true" />
                              )}
                              {installLabel(c.name)}
                            </button>
                            <span className="text-zinc-500">
                              Opens a Build terminal that runs {c.name}&apos;s own installer where you can
                              watch it — nothing is installed quietly. Or{" "}
                              <a
                                href={c.installUrl}
                                target="_blank"
                                rel="noreferrer"
                                className="text-zinc-400 underline decoration-zinc-700 underline-offset-2 transition-colors hover:text-zinc-200"
                              >
                                read the vendor&apos;s install page
                              </a>
                              .
                            </span>
                          </span>
                        )}
                        {install[c.cli]?.error && (
                          <span role="alert" className="mt-1 block text-tone-danger">
                            {install[c.cli]?.error}
                          </span>
                        )}
                        {(install[c.cli] ?? {}).paneId !== undefined && (
                          <>
                            <button
                              type="button"
                              data-testid={`installed-check-${c.cli}`}
                              onClick={() => void rescan(c, "installed")}
                              disabled={rescanBusy}
                              className="mt-1.5 inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:bg-white/[0.06] disabled:opacity-50"
                            >
                              {rescanBusy ? (
                                <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                              ) : (
                                <RefreshCw size={13} aria-hidden="true" />
                              )}
                              {INSTALLED_CHECK_LABEL}
                            </button>
                            {checkNote[c.cli] && (
                              <span
                                role="status"
                                data-testid={`installed-check-note-${c.cli}`}
                                className="mt-1 block text-zinc-400"
                              >
                                {checkNote[c.cli]}
                              </span>
                            )}
                          </>
                        )}
                      </>
                    )}
                    {state === "checking" && (
                      <>
                        <span className="text-zinc-300">{c.longName}</span> is on this PC.
                        Checking whether it&apos;s signed in…
                      </>
                    )}
                    {state === "signed-out" && (
                      <>
                        <span className="text-zinc-300">{c.longName}</span> is on this PC but
                        not signed in.
                        {/* v1.353.0 (G33): never the terminal sentence
                            (row.sign_in_fix stays for the refusals) — the
                            press is right below, and the window it opens is
                            where the sign-in finishes. */}
                        <span data-testid={`signed-out-line-${c.cli}`} className="mt-1 block text-zinc-500">
                          {withCode(signedOutLine(c))}
                        </span>
                        {si.paneId !== undefined ? (
                          <span
                            data-testid={`pane-hint-${c.cli}`}
                            className="mt-1.5 block text-zinc-400"
                          >
                            A window opened with {c.name} running. {withCode(c.paneHint)} This
                            turns green by itself a few seconds after {c.name} says you&apos;re signed
                            in, or press “{CHECK_NOW_LABEL}”.{" "}
                            <Link
                              href={paneHref(si.paneId)}
                              onClick={() => onOpenPane?.()}
                              className="font-medium text-accent-soft underline decoration-accent/40 underline-offset-2 transition-colors hover:text-accent"
                            >
                              Open the Build terminal
                            </Link>
                            {/* v1.445.0: a pane remembered from earlier in
                                this visit may have been closed since. */}
                            {!signInNow[c.cli] && (
                              <>
                                {" · "}
                                <button
                                  type="button"
                                  data-testid={`sign-in-again-${c.cli}`}
                                  onClick={() => void startSignIn(c)}
                                  className="text-zinc-400 underline decoration-zinc-700 underline-offset-2 transition-colors hover:text-zinc-200"
                                >
                                  Closed it? Open a new one
                                </button>
                              </>
                            )}
                          </span>
                        ) : (
                          <button
                            type="button"
                            onClick={() => void startSignIn(c)}
                            disabled={si.busy}
                            className="mt-1.5 inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:bg-white/[0.06] disabled:opacity-50"
                          >
                            {si.busy ? (
                              <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                            ) : (
                              <TerminalSquare size={13} aria-hidden="true" />
                            )}
                            Sign in to {c.name}
                          </button>
                        )}
                        {si.error && (
                          <span className="mt-1 block text-tone-danger">{si.error}</span>
                        )}
                        {/* v1.445.0: the user finished signing in — look
                            again NOW (the rescan re-probes the sign-in)
                            instead of waiting for the next automatic check. */}
                        <button
                          type="button"
                          data-testid={`check-now-${c.cli}`}
                          onClick={() => void rescan(c)}
                          disabled={rescanBusy}
                          className="mt-1.5 ml-1.5 inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:bg-white/[0.06] disabled:opacity-50"
                        >
                          {rescanBusy ? (
                            <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                          ) : (
                            <RefreshCw size={13} aria-hidden="true" />
                          )}
                          {CHECK_NOW_LABEL}
                        </button>
                        {checkNote[c.cli] && (
                          <span
                            role="status"
                            data-testid={`check-now-note-${c.cli}`}
                            className="mt-1 block text-zinc-400"
                          >
                            {checkNote[c.cli]}
                          </span>
                        )}
                      </>
                    )}
                  </li>
                );
              })}
          </ul>

          {/* An older daemon whose rows say nothing about install/sign-in:
              the honest re-check, as before (claude/codex presence is a live
              per-poll /health check, so "turns green" is true here). */}
          {anyUnknown && (
            <>
              <button
                type="button"
                onClick={() => void rescan()}
                disabled={rescanBusy}
                className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:bg-white/[0.06] disabled:opacity-50"
              >
                {rescanBusy ? (
                  <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                ) : (
                  <RefreshCw size={13} aria-hidden="true" />
                )}
                Rescan now
              </button>
              <p data-testid="recheck-words" className="mt-2 text-[11px] text-zinc-600">
                Iron Jarvis also checks again by itself about every two minutes. Press Rescan now
                to check at once.
              </p>
            </>
          )}
        </div>
      )}

      {/* Door: free local model via Ollama. Deliberately NO rescan button
          (v1.197.0): rescan only enumerates CLIs and cannot detect Ollama. */}
      {door === "local" && (
        <div className="mt-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5">
          <ol className="space-y-2 text-xs leading-relaxed text-zinc-400">
            <li data-testid="ollama-step-1">
              1.{" "}
              <a
                href="https://ollama.com/download"
                target="_blank"
                rel="noreferrer"
                className="font-medium text-accent-soft underline decoration-accent/40 underline-offset-2 transition-colors hover:text-accent"
              >
                Download Ollama
              </a>{" "}
              and install it.
              {/* v1.449.0 (item 21): the row says when step 1 is done. */}
              {ollamaOnPc && (
                <span
                  data-testid="ollama-on-pc"
                  className="ml-1.5 inline-flex items-center gap-1 text-tone-success"
                >
                  <CheckCircle2 size={12} className="text-tone-success" aria-hidden="true" />
                  Ollama is on this PC.
                </span>
              )}
            </li>
            <li data-testid="ollama-step-2">
              {/* v1.355.0 (G73): no command to copy. One press opens a Build
                  pane that runs the download, the way Sign in opens one.
                  v1.449.0 (item 21): greyed until Ollama is on this PC —
                  pressed before step 1 the terminal only showed an error
                  while this said "downloading". */}
              2. Download a starter model ({STARTER_MODEL_LABEL}, about 2 GB):
              {ollamaKnown && ollamaRunning && !ollamaHasModel && starter.paneId === undefined && (
                <span data-testid="ollama-no-model" className="mt-1 block text-tone-warn">
                  Ollama is running but has no model yet — press Download a starter model.
                </span>
              )}
              {starter.paneId !== undefined ? (
                <span data-testid="starter-model-pane" className="mt-1.5 block text-zinc-400">
                  A Build terminal opened and is downloading it. That takes a few minutes; when the
                  terminal says success, come back for step 3.{" "}
                  <Link
                    href={paneHref(starter.paneId)}
                    onClick={() => onOpenPane?.()}
                    className="font-medium text-accent-soft underline decoration-accent/40 underline-offset-2 transition-colors hover:text-accent"
                  >
                    Open the Build terminal
                  </Link>
                </span>
              ) : (
                <>
                  <button
                    type="button"
                    data-testid="starter-model-download"
                    onClick={() => void starter.start()}
                    disabled={starter.busy || (ollamaKnown && !ollamaOnPc)}
                    className="mt-1.5 flex w-fit items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {starter.busy ? (
                      <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                    ) : (
                      <Download size={13} aria-hidden="true" />
                    )}
                    Download a starter model
                  </button>
                  {ollamaKnown && !ollamaOnPc && (
                    <span data-testid="starter-model-wait" className="mt-1 block text-zinc-500">
                      Install Ollama first (step 1) — this turns on by itself once Ollama is on this PC.
                    </span>
                  )}
                </>
              )}
              {starter.error && (
                <span role="alert" className="mt-1 block text-tone-danger">
                  {starter.error}
                </span>
              )}
            </li>
            <li data-testid="ollama-step-3">
              {/* v1.449.0 (item 21): step 3 IS the one press — the same
                  "Use … for answers" the Ready panel offers (the daemon saves
                  this PC's address as part of it, G34), so nobody has to come
                  back up for a second press. Greyed until Ollama answers with
                  a model; the daemon's own sentence when it has none. With a
                  real default already chosen the press has nothing to choose,
                  so the old "connect" (save the address) stands there. */}
              3. Tell Iron Jarvis to use it:
              {demo ? (
                <button
                  type="button"
                  data-testid="ollama-use"
                  onClick={() =>
                    void press(ollamaCandidate, (provider, label) => {
                      onEnabled?.(provider);
                      onChosen?.(label);
                    })
                  }
                  disabled={pressBusy !== null || (ollamaKnown && !ollamaHasModel)}
                  className="mt-1.5 flex w-fit items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-ink-950 shadow-glow-sm transition-colors hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {pressBusy === "ollama" && (
                    <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                  )}
                  Use Ollama for answers
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => void connectOllama()}
                  disabled={ollamaBusy}
                  className="mt-1.5 flex w-fit items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-ink-950 shadow-glow-sm transition-colors hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {ollamaBusy && <Loader2 size={13} className="animate-spin" aria-hidden="true" />}
                  Connect to Ollama on this PC
                </button>
              )}
              {demo && ollamaKnown && !ollamaHasModel && (
                <span data-testid="ollama-use-wait" className="mt-1 block text-zinc-500">
                  {ollamaRunning
                    ? "Turns on once a model is downloaded (step 2)."
                    : "Turns on once Ollama is running with a model (steps 1 and 2)."}
                </span>
              )}
            </li>
          </ol>
          {ollamaError && (
            <p role="alert" className="mt-2 text-xs text-tone-danger">
              {ollamaError}
            </p>
          )}
          {ollamaSaved && !ollamaError && ollamaUp && (
            <p className="mt-2 inline-flex items-center gap-1.5 text-[11px] text-tone-success">
              <CheckCircle2 size={12} className="text-tone-success" />
              Ollama is answering on this PC.
              {demo ? " Choose it above to use it for answers." : ""}
            </p>
          )}
          {ollamaSaved && !ollamaError && !ollamaUp && (
            <p className="mt-2 text-[11px] text-zinc-600">
              Iron Jarvis re-checks by itself every few seconds — this step turns green on its own
              once Ollama is reachable. If it stays grey, check that Ollama is actually running.
            </p>
          )}
          <p className="mt-2.5 text-[11px] leading-relaxed text-zinc-500">
            Honest note: a small local model is less capable than a frontier one — but it costs
            nothing and nothing leaves this machine.
          </p>
          {/* v1.449.0 (item 21): the quick alternative to a 2 GB download,
              named only while lib/freeTiers has the tier on record. */}
          {freeTierWords("google") && (
            <p data-testid="ollama-gemini-alternative" className="mt-2 text-[11px] leading-relaxed text-zinc-500">
              Prefer no download? A Google Gemini key has a {freeTierWords("google")} —{" "}
              <button
                type="button"
                data-testid="ollama-gemini-switch"
                onClick={() => {
                  setDoor("key");
                  setKeyProvider("google");
                  setKeyResult(null);
                  setKeyError(null);
                  setShapeSwitchedFrom(null);
                }}
                className="text-zinc-400 underline decoration-zinc-700 underline-offset-2 transition-colors hover:text-zinc-200"
              >
                paste one under “I have an API key”
              </button>
              . {freeTierNote("google")}
            </p>
          )}
        </div>
      )}

      {/* Door: paste an API key (the pre-v1.197.0 form, unchanged) */}
      {door === "key" && (
        <div className="mt-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5">
          <div className="mb-2.5 flex items-center gap-1.5 text-xs font-medium text-zinc-300">
            <KeyRound size={13} className="text-accent-soft" /> Paste an API key
          </div>
          <div className="flex flex-wrap gap-1.5">
            {KEY_PROVIDERS.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => {
                  setKeyProvider(p.id);
                  setKeyResult(null);
                  setKeyError(null);
                  setShapeSwitchedFrom(null);
                }}
                className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
                  keyProvider === p.id
                    ? "border-accent/40 bg-accent/[0.1] text-accent-soft"
                    : "border-white/10 text-zinc-400 hover:bg-white/[0.04]"
                }`}
              >
                {p.label}
                {/* v1.420.0 (C6.12): a provider with a free tier on record
                    (lib/freeTiers) says so on its own button. Hidden from the
                    button's name; the line under the form says it in words. */}
                {freeTierWords(p.id) && (
                  <span aria-hidden="true" data-testid={`key-door-free-${p.id}`} className="ml-1 text-tone-success">
                    · {freeTierWords(p.id)}
                  </span>
                )}
              </button>
            ))}
          </div>

          {keyProvider === "custom" && (
            <div ref={customRef} className="mt-2.5 space-y-2">
              {/* v1.355.0 (G72): the two local servers, one press each. */}
              <LocalServerChips onPick={pickLocalServer} />
              {/* v1.356.0 (G75): the field says what goes in it, never
                  "Base URL". */}
              <input
                value={customBaseUrl}
                onChange={(e) => setCustomBaseUrl(e.target.value)}
                placeholder={`${MODEL_SERVER_ADDRESS} (e.g. http://localhost:1234/v1)`}
                aria-label={MODEL_SERVER_ADDRESS}
                title="Where your model server answers, like http://localhost:1234/v1"
                className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent/40 focus:outline-none"
              />
              {/* v1.355.0 (G72): the Connections form's own Model field: type
                  an id, or "Fetch available models" lists the server's. */}
              <EndpointModelPicker
                baseUrl={customBaseUrl}
                apiKey={keyValue}
                value={customModel}
                onChange={setCustomModel}
                label={MODEL_NAME_LABEL}
              />
            </div>
          )}

          <form
            onSubmit={(e) => {
              e.preventDefault();
              void connectKey();
            }}
            className="mt-2.5 flex items-center gap-2"
          >
            <input
              type="password"
              value={keyValue}
              onChange={(e) => onKeyInput(e.target.value)}
              placeholder={keyPick?.placeholder ?? "key"}
              className="min-w-0 flex-1 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-accent/40 focus:outline-none"
            />
            <button
              type="submit"
              disabled={keyBusy || (keyProvider === "custom" ? !customBaseUrl.trim() : !keyValue.trim())}
              // v1.450.0 (Wave D, item 38): a greyed Connect says what it waits for.
              title={
                keyBusy
                  ? undefined
                  : keyProvider === "custom"
                    ? customBaseUrl.trim()
                      ? undefined
                      : "Type the model server's address first."
                    : keyValue.trim()
                      ? undefined
                      : "Paste a key first."
              }
              className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-ink-950 shadow-glow-sm transition-colors hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-40"
            >
              {keyBusy && <Loader2 size={13} className="animate-spin" aria-hidden="true" />}
              Connect
            </button>
          </form>

          {/* v1.445.0: the key's shape names its company. Pasted into another
              tab (the usual slip), the right tab was picked and this says
              so; a tab picked by hand afterwards gets a one-press switch. */}
          {keyShape && keyShape === keyProvider && shapeSwitchedFrom && (
            <p data-testid="key-door-shape" className="mt-2 text-[11px] text-zinc-400">
              This looks like {KEY_SHAPE_NAMES[keyShape]} key, so the {keyPickFor(keyShape)?.label} tab
              is picked.
            </p>
          )}
          {keyShape && keyShape !== keyProvider && (
            <p data-testid="key-door-shape" className="mt-2 text-[11px] text-tone-warn">
              This looks like {KEY_SHAPE_NAMES[keyShape]} key, not{" "}
              {KEY_SHAPE_NAMES[keyProvider] ?? "this"} one.{" "}
              <button
                type="button"
                data-testid="key-door-shape-switch"
                onClick={() => {
                  setKeyProvider(keyShape);
                  setShapeSwitchedFrom(null);
                  setKeyResult(null);
                  setKeyError(null);
                }}
                className="font-medium text-accent-soft underline decoration-accent/40 underline-offset-2 transition-colors hover:text-accent"
              >
                Switch to {keyPickFor(keyShape)?.label}
              </button>
            </p>
          )}

          {keyResult && (
            <div role="status" className={`mt-2 text-xs ${keyResult.ok ? "text-tone-success" : "text-tone-danger"}`}>
              <p data-testid="key-door-result">
                {/* v1.420.0 (C6.6): the daemon's plain words (title + fix);
                    an older daemon's raw detail keeps the old prefix. */}
                {!keyWords.worded && (keyResult.ok ? "Connected — " : "Not connected — ")}
                {keyWords.text}
                {/* v1.353.0 (G35): connected is not chosen — while replies are
                    the demo, say the next step instead of stopping at "Connected". */}
                {keyResult.ok && demo && !keyChose ? " Choose it above to use it for answers." : ""}
                {/* v1.445.0: a failed key test stored nothing — say so. */}
                {!keyResult.ok && keyNotSaved
                  ? " Nothing was saved, and what answers you didn't change."
                  : ""}
              </p>
              {keyWords.technical && (
                <details data-testid="key-door-technical" className="mt-1 text-[11px] text-zinc-500">
                  <summary className="cursor-pointer">{DETAILS_WORDS}</summary>
                  <p className="mt-1 break-words font-mono">{keyWords.technical}</p>
                </details>
              )}
            </div>
          )}
          {keyError && (
            <p role="alert" className="mt-2 text-xs text-tone-danger">
              {keyError}
            </p>
          )}

          {/* v1.355.0 (G46): Gemini takes a key here now (the old line
              pointed to an account login that first needed a Cloud Console
              app). Where to get one, for the provider that needs saying. */}
          {keyPick?.keyUrl && (
            <p data-testid="key-door-get-one" className="mt-2.5 text-[11px] text-zinc-500">
              No key yet? Get one at{" "}
              <a
                href={keyPick.keyUrl}
                target="_blank"
                rel="noreferrer"
                className="text-zinc-400 underline decoration-zinc-700 underline-offset-2 transition-colors hover:text-zinc-200"
              >
                {keyPick.keyLabel}
              </a>
              .
              {freeTierWords(keyPick.id) && (
                <span data-testid="key-door-free-tier">
                  {" "}
                  {keyPick.label} has a {freeTierWords(keyPick.id)}. {freeTierNote(keyPick.id)}
                </span>
              )}
              {/* v1.449.0 (item 25): how the key is charged, in the vendor's
                  own terms — a plan holder often does not know a key is a
                  second, metered account. */}
              {keyPick.billing && (
                <span data-testid="key-door-billing"> {keyPick.billing}</span>
              )}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
