#!/usr/bin/env node
var __defProp = Object.defineProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};

// src/commands.ts
import { spawn as nodeSpawn } from "child_process";
import { mkdir as mkdir5, readFile as readFile4, rm as rm3, stat as stat3, writeFile as writeFile3 } from "fs/promises";
import { join as join7 } from "path";
import { createInterface } from "readline";
import { Writable } from "stream";
import { parseArgs } from "util";

// ../core/src/types.ts
var PROVIDER_IDS = [
  "anthropic",
  "openai",
  "google",
  "xai",
  "openai-compatible"
];
var DEFAULT_POLICY = {
  defaultRateLimitCooldownMs: 6e4,
  defaultQuotaCooldownMs: 30 * 6e4,
  billingCooldownMs: 24 * 60 * 6e4,
  overloadCooldownMs: 15e3,
  overloadRetries: 1,
  maxCooldownMs: 7 * 24 * 60 * 6e4,
  requestTimeoutMs: 10 * 6e4,
  autoReturn: true,
  preemptAtUtilisation: 0.95,
  resumeInterrupted: false
};
var USAGE_WINDOWS = ["1h", "5h", "24h", "7d"];

// ../core/src/util.ts
import { randomBytes } from "crypto";
import { mkdir, readFile, rename, rm, writeFile, chmod } from "fs/promises";
import { dirname } from "path";
var systemClock = { now: () => Date.now() };
function newId(prefix = "") {
  const t = Date.now().toString(36).padStart(10, "0");
  const r = randomBytes(9).toString("base64url").slice(0, 12);
  return prefix ? `${prefix}_${t}${r}` : `${t}${r}`;
}
function isoNow(clock = systemClock) {
  return new Date(clock.now()).toISOString();
}
function parseDurationMs(input) {
  if (!input) return void 0;
  const s = input.trim().toLowerCase();
  if (!s) return void 0;
  if (/^\d+$/.test(s)) return Number(s) * 1e3;
  const re = /(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?|d|days?)(?![a-z])/g;
  let total = 0;
  let matched = false;
  for (const m of s.matchAll(re)) {
    matched = true;
    const n = Number(m[1]);
    const unit = m[2] ?? "";
    if (unit.startsWith("ms") || unit.startsWith("milli")) total += n;
    else if (unit.startsWith("s")) total += n * 1e3;
    else if (unit.startsWith("m")) total += n * 6e4;
    else if (unit.startsWith("h")) total += n * 36e5;
    else if (unit.startsWith("d")) total += n * 864e5;
  }
  return matched ? Math.round(total) : void 0;
}
function parseResetAt(input, now = Date.now()) {
  if (!input) return void 0;
  const s = input.trim();
  if (!s) return void 0;
  if (/^\d{9,10}$/.test(s)) return new Date(Number(s) * 1e3).toISOString();
  if (/^\d{12,13}$/.test(s)) return new Date(Number(s)).toISOString();
  const asDate = new Date(s);
  if (!Number.isNaN(asDate.getTime()) && /\d{4}/.test(s)) return asDate.toISOString();
  const clock = /(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b/i.exec(s);
  if (clock && (clock[2] !== void 0 || clock[3] !== void 0)) {
    let h = Number(clock[1]);
    const min = clock[2] ? Number(clock[2]) : 0;
    const ap = clock[3]?.toLowerCase();
    if (ap === "pm" && h < 12) h += 12;
    if (ap === "am" && h === 12) h = 0;
    if (h > 23 || min > 59) return void 0;
    const d = new Date(now);
    d.setHours(h, min, 0, 0);
    if (d.getTime() <= now) d.setDate(d.getDate() + 1);
    return d.toISOString();
  }
  return void 0;
}
async function readJsonFile(path, fallback) {
  try {
    const text = await readFile(path, "utf8");
    return JSON.parse(text);
  } catch (err) {
    if (err.code === "ENOENT") return fallback;
    throw err;
  }
}
async function writeJsonFileAtomic(path, value, mode = 384) {
  await mkdir(dirname(path), { recursive: true });
  const tmp = `${path}.${process.pid}.${randomBytes(4).toString("hex")}.tmp`;
  await writeFile(tmp, JSON.stringify(value, null, 2), { encoding: "utf8", mode });
  try {
    await chmod(tmp, mode);
  } catch {
  }
  for (let attempt = 0; ; attempt++) {
    try {
      await rename(tmp, path);
      return;
    } catch (err) {
      const code2 = err.code;
      if (attempt >= 20 || code2 !== "EPERM" && code2 !== "EACCES" && code2 !== "EBUSY") {
        await rm(tmp, { force: true }).catch(() => {
        });
        throw err;
      }
      await new Promise((r) => setTimeout(r, 10 + attempt * 5));
    }
  }
}
function sleep(ms, signal) {
  return new Promise((resolve2, reject) => {
    if (signal?.aborted) return reject(signal.reason ?? new Error("aborted"));
    const t = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve2();
    }, ms);
    function onAbort() {
      clearTimeout(t);
      reject(signal?.reason ?? new Error("aborted"));
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}
function redactSecrets(text) {
  return text.replace(/(sk-[A-Za-z0-9_-]{6})[A-Za-z0-9_-]{10,}/g, "$1\u2026").replace(/(AIza[A-Za-z0-9_-]{4})[A-Za-z0-9_-]{10,}/g, "$1\u2026").replace(/(xai-[A-Za-z0-9_-]{4})[A-Za-z0-9_-]{10,}/g, "$1\u2026").replace(/(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}/gi, "$1\u2026").replace(/(eyJ[A-Za-z0-9_-]{8})[A-Za-z0-9_-]{20,}\.[A-Za-z0-9._-]+/g, "$1\u2026");
}
function inferProvider(model) {
  if (!model) return void 0;
  const m = model.toLowerCase();
  if (m.startsWith("claude")) return "anthropic";
  if (m.startsWith("gemini") || m.startsWith("models/gemini")) return "google";
  if (m.startsWith("grok")) return "xai";
  if (/^(gpt|o\d|chatgpt|codex|text-embedding|davinci)/.test(m)) return "openai";
  return void 0;
}

// ../core/src/errors.ts
var CLI_INSTALL_HINTS = {
  claude: "Install Claude Code: npm install -g @anthropic-ai/claude-code",
  codex: "Install the Codex CLI: npm install -g @openai/codex",
  gemini: "Install the Gemini CLI: npm install -g @google/gemini-cli",
  grok: "Install Grok Build from xAI, then run grok --version"
};
var DEFAULT_HINTS = {
  NO_PROFILE: `Add an account: iron-proxy profiles add --provider <provider> --lane cli --title "...", or 'Add account' in the switcher.`,
  PROFILE_NOT_FOUND: "Run iron-proxy profiles list (or open the switcher) and use one of the ids shown there.",
  PROVIDER_MISMATCH: "Pick an account of the provider the request is for; Iron-Proxy never switches providers.",
  AUTH_REQUIRED: "Log the account in again: iron-proxy login <id>, or 'Log in' on it in the switcher.",
  QUOTA_EXCEEDED: "Wait for the reset, or resend without strict so the next account of the same provider takes it.",
  ALL_PROFILES_EXHAUSTED: "Wait for the earliest reset, or add another account of this provider: iron-proxy profiles add.",
  PROVIDER_ERROR: "Retry in a moment; if it keeps failing, check the provider's status page and iron-proxy status.",
  CLI_NOT_FOUND: "Install the vendor CLI and put it on PATH, then run iron-proxy doctor to confirm.",
  CLI_FAILED: "Run iron-proxy doctor, then try the same step yourself with iron-proxy login <id> --terminal.",
  VAULT_ERROR: "The vault key changed or a different key protector is in use: start Iron-Proxy the way it was set up (same app, same OS user), or re-enter the API keys.",
  INVALID_REQUEST: "Check the command or request against iron-proxy --help and docs/ADOPTING.md, then try again.",
  TIMEOUT: "Retry; if it keeps timing out, raise requestTimeoutMs in the failover policy or pass timeoutMs.",
  ABORTED: "The request was cancelled; send it again if you still need it.",
  STREAM_INTERRUPTED: "Resend; the next account will take it.",
  UNSUPPORTED: "Use a lane that supports this, or register one with registry.addLane()."
};
var EMAIL = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
function sanitizeHint(hint) {
  return redactSecrets(hint).replace(EMAIL, "[email]").trim();
}
function installHint(binary) {
  const base = (binary ?? "").split(/[\\/]/).pop().replace(/\.(exe|cmd|bat|ps1)$/i, "").toLowerCase();
  const known = CLI_INSTALL_HINTS[base];
  if (known) return known;
  return `Install ${base ? `"${base}"` : "the vendor CLI"} and put it on PATH, then run iron-proxy doctor to confirm.`;
}
function localTime(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}
function addAccountCommand(provider) {
  return `iron-proxy profiles add --provider ${provider} --lane cli --title "..."`;
}
var IronProxyError = class extends Error {
  code;
  retryable;
  details;
  /** What the user should do next. Always set; never contains secrets or emails. */
  hint;
  constructor(code2, message, opts = {}) {
    super(message, opts.cause !== void 0 ? { cause: opts.cause } : void 0);
    this.name = "IronProxyError";
    this.code = code2;
    this.retryable = opts.retryable ?? false;
    this.details = opts.details ?? {};
    this.hint = sanitizeHint(opts.hint ?? DEFAULT_HINTS[code2] ?? DEFAULT_HINTS.PROVIDER_ERROR);
  }
  toJSON() {
    return {
      name: this.name,
      message: this.message,
      code: this.code,
      retryable: this.retryable,
      details: this.details,
      ...this.hint ? { hint: this.hint } : {}
    };
  }
};
var NoProfileError = class extends IronProxyError {
  constructor(provider) {
    super("NO_PROFILE", `No enabled profile for provider "${provider}". Add one and log in.`, {
      details: { provider },
      hint: `Add an account for ${provider}: ${addAccountCommand(provider)} (then iron-proxy login <id>), or 'Add account' in the switcher.`
    });
    this.name = "NoProfileError";
  }
};
var ProfileNotFoundError = class extends IronProxyError {
  constructor(profileId) {
    super("PROFILE_NOT_FOUND", `Profile "${profileId}" does not exist.`, {
      details: { profileId },
      hint: "Run iron-proxy profiles list (or open the switcher) and use one of the ids shown there."
    });
    this.name = "ProfileNotFoundError";
  }
};
var AuthRequiredError = class extends IronProxyError {
  constructor(profileId, message = "This account needs to log in again.", ctx = {}) {
    const who = ctx.title ? `"${ctx.title}"` : `account ${profileId}`;
    const hint = ctx.lane === "api-key" ? `Enter the API key for ${who} again: 'Set API key' on it in the switcher.` : `Log ${who} in again: iron-proxy login ${profileId}, or 'Log in' on it in the switcher.`;
    super("AUTH_REQUIRED", message, {
      details: { profileId, ...ctx.title ? { title: ctx.title } : {} },
      hint
    });
    this.name = "AuthRequiredError";
  }
};
var QuotaExceededError = class extends IronProxyError {
  signal;
  profileId;
  constructor(profileId, signal, provider) {
    const when = signal.resetAt ? ` (it resets at ${localTime(signal.resetAt)})` : "";
    super("QUOTA_EXCEEDED", signal.message ?? `Account exhausted (${signal.kind}).`, {
      retryable: true,
      details: { profileId, signal },
      hint: `This request was pinned with strict, so Iron-Proxy did not switch: wait for the reset${when}, or resend without strict so the next ${provider ?? "same-provider"} account takes it.`
    });
    this.name = "QuotaExceededError";
    this.signal = signal;
    this.profileId = profileId;
  }
};
var AllProfilesExhaustedError = class extends IronProxyError {
  provider;
  earliestResetAt;
  constructor(provider, earliestResetAt, tried) {
    const when = earliestResetAt ? ` Earliest reset: ${earliestResetAt}.` : "";
    const wait = earliestResetAt ? `Wait until ${localTime(earliestResetAt)} for the first reset` : "Wait for the first account to reset";
    super(
      "ALL_PROFILES_EXHAUSTED",
      `Every ${provider} account is parked.${when} Iron-Proxy never switches providers on its own.`,
      {
        retryable: true,
        // `resetAt` repeats `earliestResetAt` under the name the external-executor API documents.
        details: {
          provider,
          earliestResetAt,
          ...earliestResetAt ? { resetAt: earliestResetAt } : {},
          tried
        },
        hint: `${wait}, or add another ${provider} account: ${addAccountCommand(provider)}.`
      }
    );
    this.name = "AllProfilesExhaustedError";
    this.provider = provider;
    this.earliestResetAt = earliestResetAt;
  }
};
var ProviderError = class extends IronProxyError {
  status;
  constructor(message, opts = {}) {
    super("PROVIDER_ERROR", message, opts);
    this.name = "ProviderError";
    this.status = opts.status;
  }
};
var CliError = class extends IronProxyError {
  constructor(code2, message, details = {}, hint) {
    const binary = typeof details.binary === "string" ? details.binary : void 0;
    const resolved = hint ?? (code2 === "CLI_NOT_FOUND" ? installHint(binary) : void 0);
    super(code2, message, { details, ...resolved !== void 0 ? { hint: resolved } : {} });
    this.name = "CliError";
  }
};
function serializeError(err) {
  if (err instanceof IronProxyError) return err.toJSON();
  if (err instanceof Error) {
    const aborted = err.name === "AbortError";
    const code2 = aborted ? "ABORTED" : "PROVIDER_ERROR";
    return {
      name: err.name,
      message: err.message,
      code: code2,
      retryable: false,
      hint: DEFAULT_HINTS[code2]
    };
  }
  return {
    name: "Error",
    message: String(err),
    code: "PROVIDER_ERROR",
    retryable: false,
    hint: DEFAULT_HINTS.PROVIDER_ERROR
  };
}

// ../core/src/events.ts
var TypedEmitter = class {
  listeners = /* @__PURE__ */ new Map();
  anyListeners = /* @__PURE__ */ new Set();
  onListenerError = () => {
  };
  on(type, listener) {
    let set = this.listeners.get(type);
    if (!set) {
      set = /* @__PURE__ */ new Set();
      this.listeners.set(type, set);
    }
    set.add(listener);
    return () => this.off(type, listener);
  }
  off(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }
  onAny(listener) {
    this.anyListeners.add(listener);
    return () => {
      this.anyListeners.delete(listener);
    };
  }
  emit(event) {
    const targets = [...this.listeners.get(event.type) ?? [], ...this.anyListeners];
    for (const l of targets) {
      try {
        l(event);
      } catch (err) {
        this.onListenerError(err, event);
      }
    }
  }
  removeAll() {
    this.listeners.clear();
    this.anyListeners.clear();
  }
};

// ../core/src/manager.ts
import { homedir as homedir2 } from "os";
import { isAbsolute, join as join6, relative, resolve, sep } from "path";
import { rm as rm2, stat as stat2 } from "fs/promises";

// ../core/src/adapters/types.ts
var LaneQuotaSignal = class extends Error {
  constructor(signal) {
    super(signal.message ?? signal.kind);
    this.signal = signal;
    this.name = "LaneQuotaSignal";
  }
  signal;
};
var AdapterRegistry = class {
  adapters = /* @__PURE__ */ new Map();
  register(adapter) {
    this.adapters.set(adapter.id, adapter);
    return this;
  }
  get(id) {
    const a = this.adapters.get(id);
    if (!a) throw new Error(`No adapter registered for provider "${id}".`);
    return a;
  }
  laneFor(profile) {
    const adapter = this.get(profile.provider);
    const lane = adapter.lanes[profile.lane];
    if (!lane) {
      throw new Error(`Provider "${profile.provider}" has no "${profile.lane}" lane.`);
    }
    return lane;
  }
  /** Register an OAuth (or any) lane implementation for a provider after the fact. */
  addLane(provider, lane) {
    const adapter = this.get(provider);
    adapter.lanes[lane.kind] = lane;
    return this;
  }
  list() {
    return [...this.adapters.values()];
  }
};

// ../core/src/translate/sse.ts
async function* parseSse(body, signal) {
  if (!body) return;
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let event;
  let data = [];
  const onAbort = () => reader.cancel().catch(() => {
  });
  signal?.addEventListener("abort", onAbort, { once: true });
  try {
    for (; ; ) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, idx).replace(/\r$/, "");
        buf = buf.slice(idx + 1);
        if (line === "") {
          if (data.length) yield { event, data: data.join("\n") };
          event = void 0;
          data = [];
          continue;
        }
        if (line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const field = colon < 0 ? line : line.slice(0, colon);
        const value2 = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "event") event = value2;
        else if (field === "data") data.push(value2);
      }
    }
    if (data.length) yield { event, data: data.join("\n") };
  } finally {
    signal?.removeEventListener("abort", onAbort);
    reader.releaseLock?.();
  }
}
function sseFrame(data, event) {
  const payload = typeof data === "string" ? data : JSON.stringify(data);
  return (event ? `event: ${event}
` : "") + `data: ${payload}

`;
}

// ../core/src/translate/anthropic.ts
var anthropic_exports = {};
__export(anthropic_exports, {
  ANTHROPIC_DEFAULT_MAX_TOKENS: () => ANTHROPIC_DEFAULT_MAX_TOKENS,
  AnthropicEventTranslator: () => AnthropicEventTranslator,
  ToAnthropicStream: () => ToAnthropicStream,
  fromAnthropicRequest: () => fromAnthropicRequest,
  fromAnthropicResponse: () => fromAnthropicResponse,
  mapAnthropicStop: () => mapAnthropicStop,
  mapAnthropicUsage: () => mapAnthropicUsage,
  safeJson: () => safeJson,
  toAnthropicRequest: () => toAnthropicRequest,
  toAnthropicResponse: () => toAnthropicResponse
});

// ../core/src/translate/openai.ts
var openai_exports = {};
__export(openai_exports, {
  OpenAIChunkTranslator: () => OpenAIChunkTranslator,
  fromOpenAIRequest: () => fromOpenAIRequest,
  fromOpenAIResponse: () => fromOpenAIResponse,
  mapOpenAIFinish: () => mapOpenAIFinish,
  mapOpenAIUsage: () => mapOpenAIUsage,
  safeJson: () => safeJson,
  toOpenAIChunk: () => toOpenAIChunk,
  toOpenAIRequest: () => toOpenAIRequest,
  toOpenAIResponse: () => toOpenAIResponse
});
function toOpenAIRequest(req, model, stream) {
  const messages = [];
  if (req.system) messages.push({ role: "system", content: req.system });
  for (const m of req.messages) messages.push(...toOpenAIMessages(m));
  const out = { model, messages };
  if (req.tools?.length) {
    out.tools = req.tools.map((t) => ({
      type: "function",
      function: {
        name: t.name,
        ...t.description ? { description: t.description } : {},
        parameters: t.parameters
      }
    }));
  }
  if (req.toolChoice) {
    out.tool_choice = typeof req.toolChoice === "string" ? req.toolChoice : { type: "function", function: { name: req.toolChoice.name } };
  }
  if (req.maxTokens !== void 0) out.max_completion_tokens = req.maxTokens;
  if (req.temperature !== void 0) out.temperature = req.temperature;
  if (req.topP !== void 0) out.top_p = req.topP;
  if (req.stop?.length) out.stop = req.stop;
  if (stream) {
    out.stream = true;
    out.stream_options = { include_usage: true };
  }
  if (req.extra) Object.assign(out, req.extra);
  return out;
}
function toOpenAIMessages(m) {
  if (m.role === "system") {
    return [{ role: "system", content: textOf(m.content), ...m.name ? { name: m.name } : {} }];
  }
  if (m.role === "tool") {
    return m.content.filter((p) => p.type === "tool_result").map((p) => ({ role: "tool", content: p.content, tool_call_id: p.toolCallId }));
  }
  if (m.role === "assistant") {
    const text = textOf(m.content);
    const calls = m.content.filter(
      (p) => p.type === "tool_call"
    );
    const msg = {
      role: "assistant",
      content: text || null
    };
    if (m.name) msg.name = m.name;
    if (calls.length) {
      msg.tool_calls = calls.map((c) => ({
        id: c.id,
        type: "function",
        function: { name: c.name, arguments: JSON.stringify(c.arguments) }
      }));
    }
    return [msg];
  }
  const parts = [];
  const toolResults = [];
  for (const p of m.content) {
    if (p.type === "text") parts.push({ type: "text", text: p.text });
    else if (p.type === "image")
      parts.push({ type: "image_url", image_url: { url: `data:${p.mimeType};base64,${p.data}` } });
    else if (p.type === "image_url") parts.push({ type: "image_url", image_url: { url: p.url } });
    else if (p.type === "tool_result")
      toolResults.push({ role: "tool", content: p.content, tool_call_id: p.toolCallId });
  }
  const out = [...toolResults];
  if (parts.length) {
    const onlyText = parts.every((p) => p.type === "text");
    out.push({
      role: "user",
      content: onlyText ? parts.map((p) => p.text).join("") : parts,
      ...m.name ? { name: m.name } : {}
    });
  }
  return out;
}
function textOf(parts) {
  return parts.filter((p) => p.type === "text").map((p) => p.text).join("");
}
function mapOpenAIFinish(reason) {
  switch (reason) {
    case "stop":
      return "stop";
    case "length":
      return "length";
    case "tool_calls":
    case "function_call":
      return "tool_calls";
    case "content_filter":
      return "content_filter";
    default:
      return "other";
  }
}
function mapOpenAIUsage(u) {
  if (!u) return void 0;
  const out = {};
  if (u.prompt_tokens !== void 0) out.inputTokens = u.prompt_tokens;
  if (u.completion_tokens !== void 0) out.outputTokens = u.completion_tokens;
  if (u.prompt_tokens_details?.cached_tokens !== void 0)
    out.cacheReadTokens = u.prompt_tokens_details.cached_tokens;
  return out;
}
function fromOpenAIResponse(res) {
  const choice = res.choices[0];
  const content = [];
  if (choice?.message.content) content.push({ type: "text", text: choice.message.content });
  for (const tc of choice?.message.tool_calls ?? []) {
    content.push({
      type: "tool_call",
      id: tc.id,
      name: tc.function.name,
      arguments: safeJson(tc.function.arguments)
    });
  }
  const out = {
    id: res.id,
    model: res.model,
    message: { role: "assistant", content },
    finishReason: mapOpenAIFinish(choice?.finish_reason),
    raw: res
  };
  const usage2 = mapOpenAIUsage(res.usage);
  if (usage2) out.usage = usage2;
  return out;
}
function safeJson(s) {
  if (!s) return {};
  try {
    const v = JSON.parse(s);
    return typeof v === "object" && v !== null ? v : { value: v };
  } catch {
    return { _raw: s };
  }
}
var OpenAIChunkTranslator = class {
  constructor(meta) {
    this.meta = meta;
  }
  meta;
  started = false;
  openCalls = /* @__PURE__ */ new Map();
  translate(chunk) {
    const out = [];
    if (!this.started) {
      this.started = true;
      out.push({
        type: "start",
        id: chunk.id,
        model: chunk.model,
        provider: this.meta.provider,
        profileId: this.meta.profileId
      });
    }
    const choice = chunk.choices[0];
    if (choice) {
      if (choice.delta.content) out.push({ type: "text", delta: choice.delta.content });
      for (const tc of choice.delta.tool_calls ?? []) {
        let id = this.openCalls.get(tc.index);
        if (!id) {
          id = tc.id ?? `call_${tc.index}`;
          this.openCalls.set(tc.index, id);
          out.push({ type: "tool_call_start", id, name: tc.function?.name ?? "" });
        }
        if (tc.function?.arguments)
          out.push({ type: "tool_call_delta", id, argumentsDelta: tc.function.arguments });
      }
      if (choice.finish_reason) {
        for (const id of this.openCalls.values()) out.push({ type: "tool_call_end", id });
        this.openCalls.clear();
        out.push({ type: "finish", finishReason: mapOpenAIFinish(choice.finish_reason) });
      }
    }
    const usage2 = mapOpenAIUsage(chunk.usage);
    if (usage2) out.push({ type: "usage", usage: usage2 });
    return out;
  }
};
function toOpenAIChunk(ev, id, model, created) {
  const base = { id, model, created, object: "chat.completion.chunk" };
  switch (ev.type) {
    case "start":
      return {
        ...base,
        choices: [{ index: 0, delta: { role: "assistant", content: "" }, finish_reason: null }]
      };
    case "text":
      return {
        ...base,
        choices: [{ index: 0, delta: { content: ev.delta }, finish_reason: null }]
      };
    case "tool_call_start":
      return {
        ...base,
        choices: [
          {
            index: 0,
            delta: {
              tool_calls: [
                {
                  index: 0,
                  id: ev.id,
                  type: "function",
                  function: { name: ev.name, arguments: "" }
                }
              ]
            },
            finish_reason: null
          }
        ]
      };
    case "tool_call_delta":
      return {
        ...base,
        choices: [
          {
            index: 0,
            delta: { tool_calls: [{ index: 0, function: { arguments: ev.argumentsDelta } }] },
            finish_reason: null
          }
        ]
      };
    case "finish":
      return {
        ...base,
        choices: [
          {
            index: 0,
            delta: {},
            finish_reason: ev.finishReason === "tool_calls" ? "tool_calls" : ev.finishReason === "length" ? "length" : "stop"
          }
        ]
      };
    case "usage":
      return {
        ...base,
        choices: [],
        usage: {
          prompt_tokens: ev.usage.inputTokens ?? 0,
          completion_tokens: ev.usage.outputTokens ?? 0
        }
      };
    default:
      return void 0;
  }
}
function toOpenAIResponse(res) {
  const text = textOf(res.message.content);
  const calls = res.message.content.filter(
    (p) => p.type === "tool_call"
  );
  return {
    id: res.id,
    object: "chat.completion",
    created: Math.floor(Date.now() / 1e3),
    model: res.model,
    choices: [
      {
        index: 0,
        message: {
          role: "assistant",
          content: text || null,
          ...calls.length ? {
            tool_calls: calls.map((c) => ({
              id: c.id,
              type: "function",
              function: { name: c.name, arguments: JSON.stringify(c.arguments) }
            }))
          } : {}
        },
        finish_reason: res.finishReason === "tool_calls" ? "tool_calls" : res.finishReason === "length" ? "length" : "stop"
      }
    ],
    usage: {
      prompt_tokens: res.usage?.inputTokens ?? 0,
      completion_tokens: res.usage?.outputTokens ?? 0
    }
  };
}
function fromOpenAIRequest(body) {
  const messages = [];
  let system;
  for (const m of body.messages) {
    if (m.role === "system" || m.role === "developer") {
      system = system ? `${system}

${m.content}` : m.content;
      continue;
    }
    if (m.role === "tool") {
      messages.push({
        role: "tool",
        content: [{ type: "tool_result", toolCallId: m.tool_call_id, content: m.content }]
      });
      continue;
    }
    if (m.role === "assistant") {
      const content2 = [];
      if (m.content) content2.push({ type: "text", text: m.content });
      for (const tc of m.tool_calls ?? []) {
        content2.push({
          type: "tool_call",
          id: tc.id,
          name: tc.function.name,
          arguments: safeJson(tc.function.arguments)
        });
      }
      messages.push({ role: "assistant", content: content2, ...m.name ? { name: m.name } : {} });
      continue;
    }
    const content = [];
    if (typeof m.content === "string") content.push({ type: "text", text: m.content });
    else {
      for (const p of m.content) {
        if (p.type === "text") content.push({ type: "text", text: p.text });
        else if (p.type === "image_url") {
          const dm = /^data:([^;]+);base64,(.*)$/s.exec(p.image_url.url);
          if (dm) content.push({ type: "image", mimeType: dm[1], data: dm[2] });
          else content.push({ type: "image_url", url: p.image_url.url });
        }
      }
    }
    messages.push({ role: "user", content, ...m.name ? { name: m.name } : {} });
  }
  const out = { messages };
  if (body.model) out.model = body.model;
  if (system) out.system = system;
  if (body.tools?.length) {
    out.tools = body.tools.map((t) => ({
      name: t.function.name,
      ...t.function.description ? { description: t.function.description } : {},
      parameters: t.function.parameters ?? { type: "object", properties: {} }
    }));
  }
  if (body.tool_choice) {
    out.toolChoice = typeof body.tool_choice === "string" ? body.tool_choice : { name: body.tool_choice.function.name };
  }
  const max = body.max_completion_tokens ?? body.max_tokens;
  if (max !== void 0) out.maxTokens = max;
  if (body.temperature !== void 0) out.temperature = body.temperature;
  if (body.top_p !== void 0) out.topP = body.top_p;
  if (body.stop?.length) out.stop = body.stop;
  return out;
}

// ../core/src/translate/anthropic.ts
var ANTHROPIC_DEFAULT_MAX_TOKENS = 4096;
function toAnthropicRequest(req, model, stream) {
  const messages = [];
  let system = req.system;
  for (const m of req.messages) {
    if (m.role === "system") {
      const t = textOf2(m.content);
      system = system ? `${system}

${t}` : t;
      continue;
    }
    const role = m.role === "assistant" ? "assistant" : "user";
    const blocks = toAnthropicBlocks(m.content);
    if (!blocks.length) continue;
    const last = messages[messages.length - 1];
    if (last && last.role === role && Array.isArray(last.content)) last.content.push(...blocks);
    else messages.push({ role, content: blocks });
  }
  const out = {
    model,
    max_tokens: req.maxTokens ?? ANTHROPIC_DEFAULT_MAX_TOKENS,
    messages
  };
  if (system) out.system = system;
  if (req.tools?.length) {
    out.tools = req.tools.map((t) => ({
      name: t.name,
      ...t.description ? { description: t.description } : {},
      input_schema: t.parameters
    }));
  }
  if (req.toolChoice) {
    out.tool_choice = req.toolChoice === "auto" ? { type: "auto" } : req.toolChoice === "none" ? { type: "none" } : req.toolChoice === "required" ? { type: "any" } : { type: "tool", name: req.toolChoice.name };
  }
  if (req.temperature !== void 0) out.temperature = req.temperature;
  if (req.topP !== void 0) out.top_p = req.topP;
  if (req.stop?.length) out.stop_sequences = req.stop;
  if (stream) out.stream = true;
  if (req.extra) Object.assign(out, req.extra);
  return out;
}
function toAnthropicBlocks(parts) {
  const out = [];
  for (const p of parts) {
    switch (p.type) {
      case "text":
        if (p.text) out.push({ type: "text", text: p.text });
        break;
      case "image":
        out.push({
          type: "image",
          source: { type: "base64", media_type: p.mimeType, data: p.data }
        });
        break;
      case "image_url":
        out.push({ type: "image", source: { type: "url", url: p.url } });
        break;
      case "tool_call":
        out.push({ type: "tool_use", id: p.id, name: p.name, input: p.arguments });
        break;
      case "tool_result":
        out.push({
          type: "tool_result",
          tool_use_id: p.toolCallId,
          content: p.content,
          ...p.isError ? { is_error: true } : {}
        });
        break;
    }
  }
  return out;
}
function textOf2(parts) {
  return parts.filter((p) => p.type === "text").map((p) => p.text).join("");
}
function mapAnthropicStop(reason) {
  switch (reason) {
    case "end_turn":
    case "stop_sequence":
      return "stop";
    case "max_tokens":
      return "length";
    case "tool_use":
      return "tool_calls";
    case "refusal":
      return "content_filter";
    default:
      return "other";
  }
}
function mapAnthropicUsage(u) {
  if (!u) return void 0;
  const out = {};
  if (u.input_tokens !== void 0) out.inputTokens = u.input_tokens;
  if (u.output_tokens !== void 0) out.outputTokens = u.output_tokens;
  if (u.cache_read_input_tokens !== void 0) out.cacheReadTokens = u.cache_read_input_tokens;
  if (u.cache_creation_input_tokens !== void 0)
    out.cacheWriteTokens = u.cache_creation_input_tokens;
  return out;
}
function fromAnthropicResponse(res) {
  const content = [];
  for (const b of res.content) {
    if (b.type === "text") content.push({ type: "text", text: b.text });
    else if (b.type === "tool_use")
      content.push({ type: "tool_call", id: b.id, name: b.name, arguments: b.input });
  }
  const out = {
    id: res.id,
    model: res.model,
    message: { role: "assistant", content },
    finishReason: mapAnthropicStop(res.stop_reason),
    raw: res
  };
  const usage2 = mapAnthropicUsage(res.usage);
  if (usage2) out.usage = usage2;
  return out;
}
var AnthropicEventTranslator = class {
  constructor(meta) {
    this.meta = meta;
  }
  meta;
  blocks = /* @__PURE__ */ new Map();
  usage = {};
  translate(ev) {
    switch (ev.type) {
      case "message_start": {
        const u = mapAnthropicUsage(ev.message.usage);
        if (u) this.usage = { ...this.usage, ...u };
        return [
          {
            type: "start",
            id: ev.message.id,
            model: ev.message.model,
            provider: this.meta.provider,
            profileId: this.meta.profileId
          }
        ];
      }
      case "content_block_start":
        if (ev.content_block.type === "tool_use") {
          this.blocks.set(ev.index, { id: ev.content_block.id, name: ev.content_block.name });
          return [
            { type: "tool_call_start", id: ev.content_block.id, name: ev.content_block.name }
          ];
        }
        if (ev.content_block.type === "text" && ev.content_block.text)
          return [{ type: "text", delta: ev.content_block.text }];
        return [];
      case "content_block_delta":
        if (ev.delta.type === "text_delta") return [{ type: "text", delta: ev.delta.text }];
        if (ev.delta.type === "input_json_delta") {
          const b = this.blocks.get(ev.index);
          return b ? [{ type: "tool_call_delta", id: b.id, argumentsDelta: ev.delta.partial_json }] : [];
        }
        return [];
      case "content_block_stop": {
        const b = this.blocks.get(ev.index);
        if (b) {
          this.blocks.delete(ev.index);
          return [{ type: "tool_call_end", id: b.id }];
        }
        return [];
      }
      case "message_delta": {
        const out = [];
        if (ev.usage?.output_tokens !== void 0) this.usage.outputTokens = ev.usage.output_tokens;
        out.push({ type: "usage", usage: { ...this.usage } });
        out.push({ type: "finish", finishReason: mapAnthropicStop(ev.delta.stop_reason) });
        return out;
      }
      case "error":
        return [
          {
            type: "error",
            error: {
              name: ev.error.type,
              message: ev.error.message,
              code: "PROVIDER_ERROR",
              retryable: false
            }
          }
        ];
      default:
        return [];
    }
  }
};
function fromAnthropicRequest(body) {
  const messages = [];
  for (const m of body.messages) {
    const content = [];
    if (typeof m.content === "string") content.push({ type: "text", text: m.content });
    else {
      for (const b of m.content) {
        if (b.type === "text") content.push({ type: "text", text: b.text });
        else if (b.type === "image") {
          if (b.source.type === "base64")
            content.push({ type: "image", mimeType: b.source.media_type, data: b.source.data });
          else content.push({ type: "image_url", url: b.source.url });
        } else if (b.type === "tool_use")
          content.push({ type: "tool_call", id: b.id, name: b.name, arguments: b.input });
        else if (b.type === "tool_result") {
          const text = typeof b.content === "string" ? b.content : b.content.map((c) => c.text).join("");
          content.push({
            type: "tool_result",
            toolCallId: b.tool_use_id,
            content: text,
            ...b.is_error ? { isError: true } : {}
          });
        }
      }
    }
    messages.push({ role: m.role, content });
  }
  const out = { messages, model: body.model, maxTokens: body.max_tokens };
  if (body.system)
    out.system = typeof body.system === "string" ? body.system : body.system.map((s) => s.text).join("\n\n");
  if (body.tools?.length) {
    out.tools = body.tools.map((t) => ({
      name: t.name,
      ...t.description ? { description: t.description } : {},
      parameters: t.input_schema
    }));
  }
  if (body.tool_choice) {
    const tc = body.tool_choice;
    out.toolChoice = tc.type === "tool" ? { name: tc.name } : tc.type === "any" ? "required" : tc.type;
  }
  if (body.temperature !== void 0) out.temperature = body.temperature;
  if (body.top_p !== void 0) out.topP = body.top_p;
  if (body.stop_sequences?.length) out.stop = body.stop_sequences;
  return out;
}
function toAnthropicResponse(res) {
  const content = [];
  for (const p of res.message.content) {
    if (p.type === "text") content.push({ type: "text", text: p.text });
    else if (p.type === "tool_call")
      content.push({ type: "tool_use", id: p.id, name: p.name, input: p.arguments });
  }
  return {
    id: res.id,
    type: "message",
    role: "assistant",
    model: res.model,
    content,
    stop_reason: res.finishReason === "tool_calls" ? "tool_use" : res.finishReason === "length" ? "max_tokens" : "end_turn",
    usage: {
      input_tokens: res.usage?.inputTokens ?? 0,
      output_tokens: res.usage?.outputTokens ?? 0
    }
  };
}
var ToAnthropicStream = class {
  index = -1;
  textOpen = false;
  toolIndex = /* @__PURE__ */ new Map();
  usage = {};
  translate(ev, id, model) {
    switch (ev.type) {
      case "start":
        return [
          {
            type: "message_start",
            message: {
              id,
              type: "message",
              role: "assistant",
              model,
              content: [],
              stop_reason: null,
              usage: { input_tokens: 0, output_tokens: 0 }
            }
          }
        ];
      case "text": {
        const out = [];
        if (!this.textOpen) {
          this.index++;
          this.textOpen = true;
          out.push({
            type: "content_block_start",
            index: this.index,
            content_block: { type: "text", text: "" }
          });
        }
        out.push({
          type: "content_block_delta",
          index: this.index,
          delta: { type: "text_delta", text: ev.delta }
        });
        return out;
      }
      case "tool_call_start": {
        const out = [];
        if (this.textOpen) {
          out.push({ type: "content_block_stop", index: this.index });
          this.textOpen = false;
        }
        this.index++;
        this.toolIndex.set(ev.id, this.index);
        out.push({
          type: "content_block_start",
          index: this.index,
          content_block: { type: "tool_use", id: ev.id, name: ev.name, input: {} }
        });
        return out;
      }
      case "tool_call_delta": {
        const i = this.toolIndex.get(ev.id);
        return i === void 0 ? [] : [
          {
            type: "content_block_delta",
            index: i,
            delta: { type: "input_json_delta", partial_json: ev.argumentsDelta }
          }
        ];
      }
      case "tool_call_end": {
        const i = this.toolIndex.get(ev.id);
        return i === void 0 ? [] : [{ type: "content_block_stop", index: i }];
      }
      case "usage":
        this.usage = { ...this.usage, ...ev.usage };
        return [];
      case "finish": {
        const out = [];
        if (this.textOpen) {
          out.push({ type: "content_block_stop", index: this.index });
          this.textOpen = false;
        }
        out.push({
          type: "message_delta",
          delta: {
            stop_reason: ev.finishReason === "tool_calls" ? "tool_use" : ev.finishReason === "length" ? "max_tokens" : "end_turn"
          },
          usage: { output_tokens: this.usage.outputTokens ?? 0 }
        });
        out.push({ type: "message_stop" });
        return out;
      }
      case "error":
        return [
          {
            type: "error",
            error: { type: ev.error.code.toLowerCase(), message: ev.error.message }
          }
        ];
      default:
        return [];
    }
  }
};

// ../core/src/quota/detect.ts
function headersFrom(h) {
  if (typeof h.get === "function") return (n) => h.get(n);
  const rec = h;
  const lower = /* @__PURE__ */ new Map();
  for (const [k, v] of Object.entries(rec)) {
    if (v === void 0) continue;
    lower.set(k.toLowerCase(), Array.isArray(v) ? v.join(", ") : v);
  }
  return (n) => lower.get(n.toLowerCase());
}
function excerpt(text, max = 240) {
  if (!text) return void 0;
  const t = text.replace(/\s+/g, " ").trim();
  return t.length > max ? `${t.slice(0, max)}\u2026` : t;
}
function detectFromHttp(provider, status2, headers, bodyText, now = Date.now()) {
  const body = bodyText ?? "";
  const lower = body.toLowerCase();
  const retryAfter = headers("retry-after");
  const retryAfterMs = retryAfter ? /^\d+$/.test(retryAfter.trim()) ? Number(retryAfter) * 1e3 : Math.max(0, (new Date(retryAfter).getTime() || now) - now) : void 0;
  if (status2 === 401 || status2 === 403) {
    if (status2 === 401 || isAuthForbidden(body)) {
      return {
        kind: "auth-expired",
        source: "status",
        message: excerpt(body) ?? "Authentication failed."
      };
    }
  }
  if (status2 === 402) {
    return { kind: "billing", source: "status", message: excerpt(body) ?? "Payment required." };
  }
  if (status2 === 429) {
    if (/insufficient_quota|exceeded your current quota|credit balance|billing|payment|plan.*upgrade/i.test(
      body
    )) {
      return {
        kind: /insufficient_quota|credit balance/.test(lower) ? "billing" : "quota-exhausted",
        source: "body",
        message: excerpt(body),
        ...retryAfterMs !== void 0 ? { retryAfterMs } : {}
      };
    }
    const resetAt = resetFromHeaders(provider, headers, now) ?? resetFromBody(body, now);
    const sig = {
      kind: "rate-limit",
      source: retryAfter || resetAt ? "header" : "status"
    };
    if (resetAt) sig.resetAt = resetAt;
    else if (retryAfterMs !== void 0) sig.retryAfterMs = retryAfterMs;
    const msg = excerpt(body);
    if (msg) sig.message = msg;
    return sig;
  }
  if (status2 === 529 || status2 === 503 || status2 === 500 && /overloaded/.test(lower)) {
    const sig = {
      kind: "overloaded",
      source: "status",
      message: excerpt(body) ?? "Provider overloaded."
    };
    if (retryAfterMs !== void 0) sig.retryAfterMs = retryAfterMs;
    return sig;
  }
  if (status2 === 400 && /credit balance is too low|billing/i.test(body)) {
    return { kind: "billing", source: "body", message: excerpt(body) };
  }
  return void 0;
}
function isAuthForbidden(body) {
  if (/authentication_error/i.test(body)) return true;
  if (/permission_error|does not have access|model access|request not allowed/i.test(body))
    return false;
  return /invalid.*(api key|token)|expired|revoked|unauthorized|authentication/i.test(body);
}
function resetFromHeaders(provider, headers, now) {
  switch (provider) {
    case "anthropic": {
      const candidates = [
        headers("anthropic-ratelimit-requests-reset"),
        headers("anthropic-ratelimit-input-tokens-reset"),
        headers("anthropic-ratelimit-output-tokens-reset"),
        headers("anthropic-ratelimit-tokens-reset"),
        headers("anthropic-ratelimit-unified-reset")
      ];
      let best;
      for (const c of candidates) {
        const t = c ? new Date(c).getTime() : NaN;
        if (!Number.isNaN(t) && (best === void 0 || t > best)) best = t;
      }
      return best !== void 0 ? new Date(best).toISOString() : void 0;
    }
    case "openai":
    case "xai":
    case "openai-compatible": {
      const a = parseDurationMs(headers("x-ratelimit-reset-requests"));
      const b = parseDurationMs(headers("x-ratelimit-reset-tokens"));
      const ms = Math.max(a ?? 0, b ?? 0);
      return ms > 0 ? new Date(now + ms).toISOString() : void 0;
    }
    case "google":
      return void 0;
  }
}
function resetFromBody(body, now) {
  const g = /"retryDelay"\s*:\s*"([^"]+)"/.exec(body);
  if (g) {
    const ms = parseDurationMs(g[1]);
    if (ms) return new Date(now + ms).toISOString();
  }
  const t = /(?:try again|retry)\s+(?:in|after)\s+([\d.]+\s*[a-z]+(?:\s*[\d.]+\s*[a-z]+)?)/i.exec(
    body
  );
  if (t) {
    const ms = parseDurationMs(t[1]);
    if (ms) return new Date(now + ms).toISOString();
  }
  const r = /resets?\s+(?:at\s+)?([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm)?)/i.exec(body);
  if (r) return parseResetAt(r[1], now);
  return void 0;
}
function detectFromCliOutput(provider, text, now = Date.now()) {
  const t = text.toLowerCase();
  if (!t.trim()) return void 0;
  const authy = /not (?:logged in|authenticated)|please (?:log ?in|sign in|run .*login)|invalid (?:api key|token)|token (?:has )?expired|authentication (?:failed|required)|unauthorized|re-?authenticat/i;
  const limit = /(?:usage|rate|weekly|daily|monthly|session|5-hour|five-hour) limit|limit (?:reached|exceeded|hit)|you(?:'ve| have) hit your|out of (?:credits|quota)|quota (?:exceeded|exhausted)|too many requests|resource_exhausted|429/i;
  const overload = /overloaded|capacity|529|503|temporarily unavailable|service unavailable/i;
  const billing = /credit balance|insufficient[_ ]quota|billing|payment required|upgrade your plan/i;
  if (billing.test(t)) return { kind: "billing", source: "cli-output", message: excerpt(text) };
  if (authy.test(t)) return { kind: "auth-expired", source: "cli-output", message: excerpt(text) };
  if (limit.test(t)) {
    const resetAt = resetFromBody(text, now);
    const sig = {
      kind: /usage|weekly|daily|monthly|session|5-hour|five-hour|quota|credits/.test(t) ? "quota-exhausted" : "rate-limit",
      source: "cli-output",
      message: excerpt(text)
    };
    if (resetAt) sig.resetAt = resetAt;
    return sig;
  }
  if (overload.test(t)) return { kind: "overloaded", source: "cli-output", message: excerpt(text) };
  void provider;
  return void 0;
}
function usageFromHeaders(provider, headers, now = Date.now()) {
  const num = (n) => n !== void 0 && n !== null && n !== "" ? Number(n) : void 0;
  const snap = { observedAt: new Date(now).toISOString() };
  let any = false;
  if (provider === "anthropic") {
    const rl = num(headers("anthropic-ratelimit-requests-limit"));
    const rr = num(headers("anthropic-ratelimit-requests-remaining"));
    const tl = num(headers("anthropic-ratelimit-tokens-limit")) ?? num(headers("anthropic-ratelimit-input-tokens-limit"));
    const tr = num(headers("anthropic-ratelimit-tokens-remaining")) ?? num(headers("anthropic-ratelimit-input-tokens-remaining"));
    if (rl !== void 0) {
      snap.requestsLimit = rl;
      any = true;
    }
    if (rr !== void 0) {
      snap.requestsRemaining = rr;
      any = true;
    }
    if (tl !== void 0) {
      snap.tokensLimit = tl;
      any = true;
    }
    if (tr !== void 0) {
      snap.tokensRemaining = tr;
      any = true;
    }
    const reset = resetFromHeaders(provider, headers, now);
    if (reset) {
      snap.resetAt = reset;
      any = true;
    }
  } else if (provider !== "google") {
    const rl = num(headers("x-ratelimit-limit-requests"));
    const rr = num(headers("x-ratelimit-remaining-requests"));
    const tl = num(headers("x-ratelimit-limit-tokens"));
    const tr = num(headers("x-ratelimit-remaining-tokens"));
    if (rl !== void 0) {
      snap.requestsLimit = rl;
      any = true;
    }
    if (rr !== void 0) {
      snap.requestsRemaining = rr;
      any = true;
    }
    if (tl !== void 0) {
      snap.tokensLimit = tl;
      any = true;
    }
    if (tr !== void 0) {
      snap.tokensRemaining = tr;
      any = true;
    }
    const reset = resetFromHeaders(provider, headers, now);
    if (reset) {
      snap.resetAt = reset;
      any = true;
    }
  }
  if (!any) return void 0;
  const ratios = [];
  if (snap.requestsLimit && snap.requestsRemaining !== void 0)
    ratios.push(1 - snap.requestsRemaining / snap.requestsLimit);
  if (snap.tokensLimit && snap.tokensRemaining !== void 0)
    ratios.push(1 - snap.tokensRemaining / snap.tokensLimit);
  if (ratios.length) snap.utilisation = Math.min(1, Math.max(0, Math.max(...ratios)));
  return snap;
}

// ../core/src/adapters/api/shared.ts
async function requireKey(ctx) {
  const ref = ctx.profile.apiKey?.secretRef;
  const who = { title: ctx.profile.title, lane: ctx.profile.lane };
  if (!ref) throw new AuthRequiredError(ctx.profile.id, "Profile has no API key configured.", who);
  const key = await ctx.vault.get(ref);
  if (!key)
    throw new AuthRequiredError(
      ctx.profile.id,
      "API key missing from the vault. Enter it again.",
      who
    );
  return key;
}
async function postJson(provider, call, ctx) {
  let res;
  try {
    res = await ctx.fetch(call.url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json, text/event-stream",
        ...call.headers
      },
      body: JSON.stringify(call.body),
      signal: ctx.signal
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ProviderError(`Network error talking to ${provider}: ${err.message}`, {
      retryable: true,
      cause: err
    });
  }
  const get = headersFrom(res.headers);
  const usage2 = usageFromHeaders(provider, get, ctx.now());
  if (usage2) ctx.reportUsage(usage2);
  if (res.ok) return res;
  const text = await res.text().catch(() => "");
  const signal = detectFromHttp(provider, res.status, get, text, ctx.now());
  if (signal) throw new LaneQuotaSignal(signal);
  throw new ProviderError(
    `${provider} returned HTTP ${res.status}: ${redactSecrets(text).slice(0, 500)}`,
    {
      status: res.status,
      retryable: res.status >= 500,
      details: { status: res.status }
    }
  );
}
function joinUrl(base, path) {
  return `${base.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}

// ../core/src/adapters/api/anthropic.ts
var ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1";
var ANTHROPIC_VERSION = "2023-06-01";
var ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5";
var AnthropicApiLane = class {
  kind = "api-key";
  headers(profile, key) {
    const auth = key.startsWith("sk-ant-oat") ? { authorization: `Bearer ${key}` } : { "x-api-key": key };
    return { ...auth, "anthropic-version": ANTHROPIC_VERSION, ...profile.apiKey?.headers ?? {} };
  }
  base(profile) {
    return profile.apiKey?.baseUrl ?? ANTHROPIC_BASE_URL;
  }
  async complete(req, ctx) {
    const key = await requireKey(ctx);
    const model = req.model ?? ctx.profile.defaultModel ?? ANTHROPIC_DEFAULT_MODEL;
    const res = await postJson(
      "anthropic",
      {
        url: joinUrl(this.base(ctx.profile), "/messages"),
        headers: this.headers(ctx.profile, key),
        body: toAnthropicRequest(req, model, false)
      },
      ctx
    );
    const json = await res.json();
    if (json.type !== "message")
      throw new ProviderError("Anthropic returned an unexpected body.", {
        details: { body: json }
      });
    return fromAnthropicResponse(json);
  }
  async *stream(req, ctx) {
    const key = await requireKey(ctx);
    const model = req.model ?? ctx.profile.defaultModel ?? ANTHROPIC_DEFAULT_MODEL;
    const res = await postJson(
      "anthropic",
      {
        url: joinUrl(this.base(ctx.profile), "/messages"),
        headers: this.headers(ctx.profile, key),
        body: toAnthropicRequest(req, model, true)
      },
      ctx
    );
    const tr = new AnthropicEventTranslator({ provider: "anthropic", profileId: ctx.profile.id });
    let finished = false;
    for await (const frame of parseSse(res.body, ctx.signal)) {
      let ev;
      try {
        ev = JSON.parse(frame.data);
      } catch {
        continue;
      }
      for (const out of tr.translate(ev)) {
        if (out.type === "finish") finished = true;
        if (out.type === "error") {
          const kind = out.error.name === "rate_limit_error" ? "rate-limit" : out.error.name === "overloaded_error" ? "overloaded" : void 0;
          if (kind)
            throw new LaneQuotaSignal({
              kind,
              source: "body",
              message: redactSecrets(out.error.message).slice(0, 240)
            });
          throw new ProviderError(out.error.message, { details: { event: ev } });
        }
        yield out;
      }
    }
    if (!finished) yield { type: "finish", finishReason: "stop" };
  }
  async checkAuth(profile, vault) {
    if (!profile.apiKey?.secretRef) return "unauthenticated";
    return await vault.has(profile.apiKey.secretRef) ? "unknown" : "unauthenticated";
  }
  async logout(profile, vault) {
    if (profile.apiKey?.secretRef) await vault.delete(profile.apiKey.secretRef);
  }
  async listModels(profile, ctx) {
    const key = await requireKey(ctx);
    const res = await ctx.fetch(joinUrl(this.base(profile), "/models?limit=100"), {
      headers: this.headers(profile, key),
      signal: ctx.signal
    });
    if (!res.ok) return [];
    const json = await res.json();
    return (json.data ?? []).map((m) => m.id).sort();
  }
};

// ../core/src/translate/google.ts
function toGeminiRequest(req) {
  const contents = [];
  let system = req.system;
  const callNames = /* @__PURE__ */ new Map();
  for (const m of req.messages) {
    if (m.role === "system") {
      const t = textOf3(m.content);
      system = system ? `${system}

${t}` : t;
      continue;
    }
    const role = m.role === "assistant" ? "model" : "user";
    const parts = [];
    for (const p of m.content) {
      switch (p.type) {
        case "text":
          if (p.text) parts.push({ text: p.text });
          break;
        case "image":
          parts.push({ inlineData: { mimeType: p.mimeType, data: p.data } });
          break;
        case "image_url":
          parts.push({ fileData: { fileUri: p.url } });
          break;
        case "tool_call":
          callNames.set(p.id, p.name);
          parts.push({ functionCall: { name: p.name, args: p.arguments } });
          break;
        case "tool_result": {
          const name = callNames.get(p.toolCallId) ?? p.toolCallId;
          let response;
          try {
            const v = JSON.parse(p.content);
            response = typeof v === "object" && v !== null ? v : { result: v };
          } catch {
            response = { result: p.content };
          }
          if (p.isError) response = { error: response };
          parts.push({ functionResponse: { name, response } });
          break;
        }
      }
    }
    if (!parts.length) continue;
    const last = contents[contents.length - 1];
    if (last && last.role === role) last.parts.push(...parts);
    else contents.push({ role, parts });
  }
  const out = { contents };
  if (system) out.systemInstruction = { parts: [{ text: system }] };
  if (req.tools?.length) {
    out.tools = [
      {
        functionDeclarations: req.tools.map((t) => ({
          name: t.name,
          ...t.description ? { description: t.description } : {},
          parameters: stripSchema(t.parameters)
        }))
      }
    ];
  }
  if (req.toolChoice) {
    out.toolConfig = {
      functionCallingConfig: req.toolChoice === "auto" ? { mode: "AUTO" } : req.toolChoice === "none" ? { mode: "NONE" } : req.toolChoice === "required" ? { mode: "ANY" } : { mode: "ANY", allowedFunctionNames: [req.toolChoice.name] }
    };
  }
  const gc = {};
  if (req.maxTokens !== void 0) gc.maxOutputTokens = req.maxTokens;
  if (req.temperature !== void 0) gc.temperature = req.temperature;
  if (req.topP !== void 0) gc.topP = req.topP;
  if (req.stop?.length) gc.stopSequences = req.stop;
  if (Object.keys(gc).length) out.generationConfig = gc;
  if (req.extra) Object.assign(out, req.extra);
  return out;
}
function stripSchema(schema) {
  const drop = /* @__PURE__ */ new Set(["$schema", "additionalProperties", "$id", "examples", "default"]);
  const walk = (v) => {
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === "object") {
      const out = {};
      for (const [k, val] of Object.entries(v)) {
        if (drop.has(k)) continue;
        out[k] = walk(val);
      }
      return out;
    }
    return v;
  };
  return walk(schema);
}
function textOf3(parts) {
  return parts.filter((p) => p.type === "text").map((p) => p.text).join("");
}
function mapGeminiFinish(reason) {
  switch (reason) {
    case "STOP":
      return "stop";
    case "MAX_TOKENS":
      return "length";
    case "SAFETY":
    case "RECITATION":
    case "BLOCKLIST":
    case "PROHIBITED_CONTENT":
    case "SPII":
      return "content_filter";
    default:
      return "other";
  }
}
function mapGeminiUsage(u) {
  if (!u) return void 0;
  const out = {};
  if (u.promptTokenCount !== void 0) out.inputTokens = u.promptTokenCount;
  if (u.candidatesTokenCount !== void 0) out.outputTokens = u.candidatesTokenCount;
  if (u.cachedContentTokenCount !== void 0) out.cacheReadTokens = u.cachedContentTokenCount;
  return out;
}
var callCounter = 0;
function nextCallId() {
  callCounter = (callCounter + 1) % 1e6;
  return `gcall_${Date.now().toString(36)}_${callCounter}`;
}
function fromGeminiResponse(res, model) {
  const cand = res.candidates?.[0];
  const content = [];
  let hasCall = false;
  for (const p of cand?.content?.parts ?? []) {
    if ("text" in p && p.text) content.push({ type: "text", text: p.text });
    else if ("functionCall" in p) {
      hasCall = true;
      content.push({
        type: "tool_call",
        id: nextCallId(),
        name: p.functionCall.name,
        arguments: p.functionCall.args ?? {}
      });
    }
  }
  const out = {
    id: res.responseId ?? `gemini_${Date.now().toString(36)}`,
    model: res.modelVersion ?? model,
    message: { role: "assistant", content },
    finishReason: hasCall ? "tool_calls" : mapGeminiFinish(cand?.finishReason),
    raw: res
  };
  const usage2 = mapGeminiUsage(res.usageMetadata);
  if (usage2) out.usage = usage2;
  return out;
}
var GeminiChunkTranslator = class {
  constructor(meta) {
    this.meta = meta;
  }
  meta;
  started = false;
  sawCall = false;
  translate(chunk) {
    const out = [];
    if (!this.started) {
      this.started = true;
      out.push({
        type: "start",
        id: chunk.responseId ?? `gemini_${Date.now().toString(36)}`,
        model: chunk.modelVersion ?? this.meta.model,
        provider: this.meta.provider,
        profileId: this.meta.profileId
      });
    }
    const cand = chunk.candidates?.[0];
    for (const p of cand?.content?.parts ?? []) {
      if ("text" in p && p.text) out.push({ type: "text", delta: p.text });
      else if ("functionCall" in p) {
        this.sawCall = true;
        const id = nextCallId();
        out.push({ type: "tool_call_start", id, name: p.functionCall.name });
        out.push({
          type: "tool_call_delta",
          id,
          argumentsDelta: JSON.stringify(p.functionCall.args ?? {})
        });
        out.push({ type: "tool_call_end", id });
      }
    }
    const usage2 = mapGeminiUsage(chunk.usageMetadata);
    if (usage2) out.push({ type: "usage", usage: usage2 });
    if (cand?.finishReason)
      out.push({
        type: "finish",
        finishReason: this.sawCall ? "tool_calls" : mapGeminiFinish(cand.finishReason)
      });
    return out;
  }
};

// ../core/src/adapters/api/google.ts
var GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta";
var GOOGLE_DEFAULT_MODEL = "gemini-2.5-pro";
var GoogleApiLane = class {
  kind = "api-key";
  base(profile) {
    return profile.apiKey?.baseUrl ?? GOOGLE_BASE_URL;
  }
  headers(profile, key) {
    return { "x-goog-api-key": key, ...profile.apiKey?.headers ?? {} };
  }
  modelPath(model) {
    return model.startsWith("models/") ? model : `models/${model}`;
  }
  async complete(req, ctx) {
    const key = await requireKey(ctx);
    const model = req.model ?? ctx.profile.defaultModel ?? GOOGLE_DEFAULT_MODEL;
    const res = await postJson(
      "google",
      {
        url: joinUrl(this.base(ctx.profile), `${this.modelPath(model)}:generateContent`),
        headers: this.headers(ctx.profile, key),
        body: toGeminiRequest(req)
      },
      ctx
    );
    return fromGeminiResponse(await res.json(), model);
  }
  async *stream(req, ctx) {
    const key = await requireKey(ctx);
    const model = req.model ?? ctx.profile.defaultModel ?? GOOGLE_DEFAULT_MODEL;
    const res = await postJson(
      "google",
      {
        url: joinUrl(
          this.base(ctx.profile),
          `${this.modelPath(model)}:streamGenerateContent?alt=sse`
        ),
        headers: this.headers(ctx.profile, key),
        body: toGeminiRequest(req)
      },
      ctx
    );
    const tr = new GeminiChunkTranslator({ provider: "google", profileId: ctx.profile.id, model });
    let finished = false;
    for await (const frame of parseSse(res.body, ctx.signal)) {
      let chunk;
      try {
        chunk = JSON.parse(frame.data);
      } catch {
        continue;
      }
      for (const ev of tr.translate(chunk)) {
        if (ev.type === "finish") finished = true;
        yield ev;
      }
    }
    if (!finished) yield { type: "finish", finishReason: "stop" };
  }
  async checkAuth(profile, vault) {
    if (!profile.apiKey?.secretRef) return "unauthenticated";
    return await vault.has(profile.apiKey.secretRef) ? "unknown" : "unauthenticated";
  }
  async logout(profile, vault) {
    if (profile.apiKey?.secretRef) await vault.delete(profile.apiKey.secretRef);
  }
  async listModels(profile, ctx) {
    const key = await requireKey(ctx);
    const res = await ctx.fetch(joinUrl(this.base(profile), "/models?pageSize=200"), {
      headers: this.headers(profile, key),
      signal: ctx.signal
    });
    if (!res.ok) return [];
    const json = await res.json();
    return (json.models ?? []).map((m) => m.name.replace(/^models\//, "")).sort();
  }
};

// ../core/src/adapters/api/openai-chat.ts
var OpenAIChatLane = class {
  constructor(opts) {
    this.opts = opts;
  }
  opts;
  kind = "api-key";
  base(profile) {
    return profile.apiKey?.baseUrl ?? this.opts.defaultBaseUrl;
  }
  headers(profile, key) {
    const auth = this.opts.authHeader ? this.opts.authHeader(key) : { authorization: `Bearer ${key}` };
    return { ...auth, ...profile.apiKey?.headers ?? {} };
  }
  async complete(req, ctx) {
    const key = await requireKey(ctx);
    const model = req.model ?? ctx.profile.defaultModel ?? this.opts.defaultModel;
    const res = await postJson(
      this.opts.provider,
      {
        url: joinUrl(this.base(ctx.profile), "/chat/completions"),
        headers: this.headers(ctx.profile, key),
        body: toOpenAIRequest(req, model, false)
      },
      ctx
    );
    const json = await res.json();
    if (!json.choices)
      throw new ProviderError(`${this.opts.provider} returned no choices.`, {
        details: { body: json }
      });
    return fromOpenAIResponse(json);
  }
  async *stream(req, ctx) {
    const key = await requireKey(ctx);
    const model = req.model ?? ctx.profile.defaultModel ?? this.opts.defaultModel;
    const res = await postJson(
      this.opts.provider,
      {
        url: joinUrl(this.base(ctx.profile), "/chat/completions"),
        headers: this.headers(ctx.profile, key),
        body: toOpenAIRequest(req, model, true)
      },
      ctx
    );
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("text/event-stream")) {
      const json = await res.json();
      const full = fromOpenAIResponse(json);
      yield {
        type: "start",
        id: full.id,
        model: full.model,
        provider: this.opts.provider,
        profileId: ctx.profile.id
      };
      for (const p of full.message.content) {
        if (p.type === "text") yield { type: "text", delta: p.text };
        if (p.type === "tool_call") {
          yield { type: "tool_call_start", id: p.id, name: p.name };
          yield { type: "tool_call_delta", id: p.id, argumentsDelta: JSON.stringify(p.arguments) };
          yield { type: "tool_call_end", id: p.id };
        }
      }
      if (full.usage) yield { type: "usage", usage: full.usage };
      yield { type: "finish", finishReason: full.finishReason };
      return;
    }
    const tr = new OpenAIChunkTranslator({
      provider: this.opts.provider,
      profileId: ctx.profile.id
    });
    let finished = false;
    for await (const frame of parseSse(res.body, ctx.signal)) {
      if (frame.data === "[DONE]") break;
      let chunk;
      try {
        chunk = JSON.parse(frame.data);
      } catch {
        continue;
      }
      if (chunk.error) {
        const e = chunk.error;
        throw new ProviderError(e.message ?? "stream error", { details: { chunk } });
      }
      for (const ev of tr.translate(chunk)) {
        if (ev.type === "finish") finished = true;
        yield ev;
      }
    }
    if (!finished) yield { type: "finish", finishReason: "stop" };
  }
  async checkAuth(profile, vault) {
    if (!profile.apiKey?.secretRef) return "unauthenticated";
    return await vault.has(profile.apiKey.secretRef) ? "unknown" : "unauthenticated";
  }
  async logout(profile, vault) {
    if (profile.apiKey?.secretRef) await vault.delete(profile.apiKey.secretRef);
  }
  async listModels(profile, ctx) {
    const key = await requireKey(ctx);
    const res = await ctx.fetch(joinUrl(this.base(profile), "/models"), {
      headers: this.headers(profile, key),
      signal: ctx.signal
    });
    if (!res.ok) return [];
    const json = await res.json();
    return (json.data ?? []).map((m) => m.id).sort();
  }
};

// ../core/src/adapters/cli/lane.ts
import { mkdir as mkdir2 } from "fs/promises";

// ../core/src/adapters/cli/runner.ts
import { spawn } from "child_process";
import { delimiter } from "path";
function baseEnv(extra = {}) {
  const keep = [
    "PATH",
    "Path",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "TEMP",
    "TMP",
    "TMPDIR",
    "SystemRoot",
    "SYSTEMROOT",
    "ComSpec",
    "COMSPEC",
    "PATHEXT",
    "LANG",
    "LC_ALL",
    "TERM",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "SHELL",
    "NO_COLOR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy"
  ];
  const env = {};
  for (const k of keep) {
    const v = process.env[k];
    if (v !== void 0) env[k] = v;
  }
  env.NO_COLOR = "1";
  env.FORCE_COLOR = "0";
  env.CI = "1";
  return { ...env, ...extra };
}
function run(opts) {
  return new Promise((resolve2, reject) => {
    let child;
    try {
      child = spawn(opts.binary, opts.args, {
        env: opts.env,
        ...opts.cwd ? { cwd: opts.cwd } : {},
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
        shell: false
      });
    } catch (err) {
      return reject(
        new CliError("CLI_FAILED", `Cannot spawn ${opts.binary}: ${err.message}`)
      );
    }
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    let settled = false;
    const timer = opts.timeoutMs ? setTimeout(() => {
      timedOut = true;
      child.kill();
    }, opts.timeoutMs) : void 0;
    const onAbort = () => child.kill();
    opts.signal?.addEventListener("abort", onAbort, { once: true });
    child.stdout?.setEncoding("utf8").on("data", (d) => stdout += d);
    child.stderr?.setEncoding("utf8").on("data", (d) => stderr += d);
    child.on("error", (err) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      opts.signal?.removeEventListener("abort", onAbort);
      if (err.code === "ENOENT") {
        reject(
          new CliError("CLI_NOT_FOUND", `"${opts.binary}" is not installed or not on PATH.`, {
            binary: opts.binary
          })
        );
      } else {
        reject(
          new CliError("CLI_FAILED", `${opts.binary}: ${err.message}`, { binary: opts.binary })
        );
      }
    });
    child.on("close", (code2) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      opts.signal?.removeEventListener("abort", onAbort);
      resolve2({ code: code2, stdout, stderr, timedOut });
    });
    if (opts.stdin !== void 0) child.stdin?.end(opts.stdin);
    else child.stdin?.end();
  });
}
function spawnLines(opts) {
  const child = spawn(opts.binary, opts.args, {
    env: opts.env,
    ...opts.cwd ? { cwd: opts.cwd } : {},
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
    shell: false
  });
  const queue = [];
  let waiter;
  let closed = false;
  let spawnError;
  const push = (source, chunk, buf) => {
    buf.s += chunk;
    let idx;
    while ((idx = buf.s.indexOf("\n")) >= 0) {
      const line = buf.s.slice(0, idx).replace(/\r$/, "");
      buf.s = buf.s.slice(idx + 1);
      queue.push({ source, line });
    }
    waiter?.();
  };
  const outBuf = { s: "" };
  const errBuf = { s: "" };
  child.stdout?.setEncoding("utf8").on("data", (d) => push("stdout", d, outBuf));
  child.stderr?.setEncoding("utf8").on("data", (d) => push("stderr", d, errBuf));
  const exit = new Promise((resolve2) => {
    child.on("error", (err) => {
      spawnError = err.code === "ENOENT" ? new CliError("CLI_NOT_FOUND", `"${opts.binary}" is not installed or not on PATH.`, {
        binary: opts.binary
      }) : new CliError("CLI_FAILED", `${opts.binary}: ${err.message}`, { binary: opts.binary });
      closed = true;
      waiter?.();
      resolve2({ code: null, signal: null });
    });
    child.on("close", (code2, signal) => {
      if (outBuf.s) queue.push({ source: "stdout", line: outBuf.s });
      if (errBuf.s) queue.push({ source: "stderr", line: errBuf.s });
      closed = true;
      waiter?.();
      resolve2({ code: code2, signal });
    });
  });
  const timer = opts.timeoutMs ? setTimeout(() => child.kill(), opts.timeoutMs) : void 0;
  timer?.unref?.();
  opts.signal?.addEventListener("abort", () => child.kill(), { once: true });
  if (opts.stdin !== void 0) child.stdin?.end(opts.stdin);
  else child.stdin?.end();
  const lines = {
    async *[Symbol.asyncIterator]() {
      for (; ; ) {
        if (queue.length) {
          yield queue.shift();
          continue;
        }
        if (closed) {
          if (timer) clearTimeout(timer);
          if (spawnError) throw spawnError;
          return;
        }
        await new Promise((r) => waiter = r);
        waiter = void 0;
      }
    }
  };
  return { lines, exit, kill: () => child.kill(), child };
}
function candidateExtensions(binary, platform, pathext) {
  if (platform !== "win32") return [""];
  const exts = (pathext || ".COM;.EXE;.BAT;.CMD").split(";").map((e) => e.trim().toLowerCase()).filter(Boolean);
  const base = binary.split(/[\\/]/).pop() ?? binary;
  const dot = base.lastIndexOf(".");
  const own = dot > 0 ? base.slice(dot).toLowerCase() : "";
  return own && exts.includes(own) ? [""] : exts;
}
async function which(binary) {
  const { access } = await import("fs/promises");
  const { isAbsolute: isAbsolute2, join: join8 } = await import("path");
  if (isAbsolute2(binary)) {
    try {
      await access(binary);
      return binary;
    } catch {
      return void 0;
    }
  }
  const dirs = (process.env.PATH ?? process.env.Path ?? "").split(delimiter).filter(Boolean);
  const exts = candidateExtensions(binary, process.platform, process.env.PATHEXT);
  for (const dir of dirs) {
    for (const ext of exts) {
      const candidate = join8(dir, binary + ext);
      try {
        await access(candidate);
        return candidate;
      } catch {
      }
    }
  }
  return void 0;
}

// ../core/src/adapters/cli/lane.ts
function renderPrompt(req) {
  const system = req.system;
  const turns = req.messages.filter((m) => m.role !== "system");
  const text = (m) => m.content.map((p) => {
    switch (p.type) {
      case "text":
        return p.text;
      case "tool_call":
        return `[tool call ${p.name}(${JSON.stringify(p.arguments)})]`;
      case "tool_result":
        return `[tool result ${p.toolCallId}: ${p.content}]`;
      case "image":
      case "image_url":
        return "[image omitted: vendor CLI lane accepts text only]";
    }
  }).join("\n");
  const last = turns[turns.length - 1];
  if (turns.length === 1 && last?.role === "user") return { prompt: text(last), system };
  const lines = [
    "The following is the conversation so far. Continue it by replying as the assistant to the final user message. Reply with the assistant message only.",
    ""
  ];
  for (const m of turns)
    lines.push(
      `${m.role === "assistant" ? "Assistant" : m.role === "tool" ? "Tool" : "User"}: ${text(m)}`,
      ""
    );
  return { prompt: lines.join("\n").trimEnd(), system };
}
var CliLane = class {
  constructor(spec) {
    this.spec = spec;
  }
  spec;
  kind = "cli";
  /** Resolve executable + leading args. A `.js`/`.mjs` binary is run through the current Node. */
  exe(profile) {
    const bin = profile.cli?.binary ?? this.spec.binary;
    if (/\.(m?js|cjs)$/i.test(bin)) return { binary: process.execPath, lead: [bin] };
    return { binary: bin, lead: [] };
  }
  env(profile) {
    if (!profile.cli?.home)
      throw new CliError(
        "CLI_FAILED",
        `Profile "${profile.title}" has no CLI home directory.`,
        {},
        `Remove this profile and add it again (iron-proxy profiles remove ${profile.id}), so it gets a home directory.`
      );
    const env = baseEnv({ [this.spec.homeEnv]: profile.cli.home, ...profile.cli.env ?? {} });
    for (const k of this.spec.stripEnv) delete env[k];
    return env;
  }
  /** The exact command a host could run in a terminal to log this profile in. */
  loginCommand(profile, headless = false) {
    const { binary, lead } = this.exe(profile);
    const args = headless && this.spec.login.headlessArgs ? this.spec.login.headlessArgs : this.spec.login.args;
    return { binary, args: [...lead, ...args], env: this.env(profile) };
  }
  /**
   * The command that starts the vendor CLI interactively as this profile, with
   * `args` appended: the same scrubbed environment every lane spawn gets (home
   * variable set, inherited API keys removed).
   */
  interactiveCommand(profile, args = []) {
    const { binary, lead } = this.exe(profile);
    return { binary, args: [...lead, ...args], env: this.env(profile) };
  }
  /**
   * What a user's own shell needs to act as this profile: the home variable and
   * the profile's own `cli.env` entries to set, and the API-key variables to
   * clear so the subscription is used. Never PATH, never a secret from the vault.
   */
  shellEnv(profile) {
    const full = this.env(profile);
    const set = { [this.spec.homeEnv]: full[this.spec.homeEnv] };
    for (const k of Object.keys(profile.cli?.env ?? {})) {
      const v = full[k];
      if (v !== void 0 && !this.spec.stripEnv.includes(k) && !/^path$/i.test(k)) set[k] = v;
    }
    return { set, unset: [...this.spec.stripEnv] };
  }
  /** Where the profile's binary resolves on this machine, or undefined when it is not installed. */
  async findBinary(profile) {
    return which(profile?.cli?.binary ?? this.spec.binary);
  }
  /** Create the isolated home. An adopted home belongs to the user and is never touched. */
  async ensureHome(profile) {
    if (!profile.cli?.home || profile.cli.adopted) return;
    await mkdir2(profile.cli.home, { recursive: true });
    await this.spec.prepareHome?.(profile.cli.home);
  }
  async complete(req, ctx) {
    let text = "";
    let usage2;
    let id = newId("cli");
    for await (const ev of this.stream(req, ctx)) {
      if (ev.type === "start") id = ev.id;
      else if (ev.type === "text") text += ev.delta;
      else if (ev.type === "usage") usage2 = ev.usage;
    }
    const out = {
      id,
      model: req.model ?? ctx.profile.defaultModel ?? this.spec.displayName,
      message: { role: "assistant", content: [{ type: "text", text }] },
      finishReason: "stop"
    };
    if (usage2) out.usage = usage2;
    return out;
  }
  async *stream(req, ctx) {
    const { prompt, system } = renderPrompt(req);
    const model = req.model ?? ctx.profile.defaultModel;
    const built = this.spec.run({
      prompt,
      ...system ? { system } : {},
      ...model ? { model } : {}
    });
    const { binary, lead } = this.exe(ctx.profile);
    await this.ensureHome(ctx.profile);
    const proc = spawnLines({
      binary,
      args: [...lead, ...built.args],
      env: this.env(ctx.profile),
      ...built.stdin !== void 0 ? { stdin: built.stdin } : {},
      signal: ctx.signal,
      cwd: ctx.profile.cli.home
    });
    const state = {};
    let started = false;
    let streamedText = "";
    let finalText;
    let usage2;
    const errors = [];
    const stderr = [];
    const id = newId("cli");
    const start = () => {
      started = true;
      return {
        type: "start",
        id,
        model: model ?? this.spec.displayName,
        provider: this.spec.provider,
        profileId: ctx.profile.id
      };
    };
    for await (const { source, line } of proc.lines) {
      if (source === "stderr") {
        stderr.push(line);
        continue;
      }
      for (const parsed of this.spec.parseLine(line, state)) {
        switch (parsed.kind) {
          case "text":
            if (!started) {
              const sig = detectFromCliOutput(this.spec.provider, parsed.delta, ctx.now());
              if (sig && !streamedText) {
                proc.kill();
                throw new LaneQuotaSignal(sig);
              }
              yield start();
            }
            streamedText += parsed.delta;
            yield { type: "text", delta: parsed.delta };
            break;
          case "final":
            finalText = parsed.text;
            break;
          case "usage":
            usage2 = parsed.usage;
            break;
          case "error":
            errors.push(parsed.text);
            break;
          case "ignore":
            break;
        }
      }
    }
    const exit = await proc.exit;
    const errorText = [...errors, ...stderr].join("\n");
    if (errorText) {
      const sig = detectFromCliOutput(this.spec.provider, errorText, ctx.now());
      if (sig && !streamedText) throw new LaneQuotaSignal(sig);
    }
    if (!streamedText && finalText !== void 0) {
      const sig = detectFromCliOutput(this.spec.provider, finalText, ctx.now());
      if (sig && (exit.code ?? 0) !== 0) throw new LaneQuotaSignal(sig);
    }
    if (!started) {
      if ((exit.code ?? 0) !== 0 && finalText === void 0) {
        throw new ProviderError(
          `${this.spec.displayName} exited with code ${exit.code}: ${redactSecrets(errorText || "(no output)").slice(0, 500)}`,
          { details: { code: exit.code } }
        );
      }
      yield start();
    }
    if (!streamedText && finalText) yield { type: "text", delta: finalText };
    if (streamedText && finalText && finalText.length > streamedText.length && finalText.startsWith(streamedText)) {
      yield { type: "text", delta: finalText.slice(streamedText.length) };
    }
    if (usage2) yield { type: "usage", usage: usage2 };
    if (started && (exit.code ?? 0) !== 0 && !streamedText && !finalText) {
      throw new ProviderError(
        `${this.spec.displayName} exited with code ${exit.code}: ${redactSecrets(errorText).slice(0, 500)}`
      );
    }
    yield { type: "finish", finishReason: "stop" };
  }
  async checkAuth(profile) {
    if (!this.spec.status) return "unknown";
    const { binary, lead } = this.exe(profile);
    if (!await which(binary)) return "unknown";
    await this.ensureHome(profile);
    try {
      const result = await run({
        binary,
        args: [...lead, ...this.spec.status.args],
        env: this.env(profile),
        timeoutMs: 2e4,
        cwd: profile.cli.home
      });
      return this.spec.status.interpret(result);
    } catch {
      return "unknown";
    }
  }
  login(profile, _vault, opts = {}) {
    if (this.spec.login.requiresTerminal) {
      throw new IronProxyError(
        "UNSUPPORTED",
        `${this.spec.displayName} signs in interactively. Run loginCommand() in a terminal window instead.`,
        { details: { provider: this.spec.provider, requiresTerminal: true } }
      );
    }
    const listeners = /* @__PURE__ */ new Set();
    const emit = (e) => {
      for (const l of listeners) l(e);
    };
    const controller = new AbortController();
    const cmd = this.loginCommand(profile, opts.headless ?? true);
    const method = opts.headless ?? true ? "cli-headless" : "cli";
    const done = (async () => {
      await this.ensureHome(profile);
      emit({ type: "started", profileId: profile.id, method });
      const proc = spawnLines({
        ...cmd,
        signal: controller.signal,
        timeoutMs: 15 * 6e4,
        cwd: profile.cli.home
      });
      const seenUrls = /* @__PURE__ */ new Set();
      const seenCodes = /* @__PURE__ */ new Set();
      for await (const { line } of proc.lines) {
        const clean = redactSecrets(line);
        emit({ type: "output", profileId: profile.id, line: clean });
        for (const m of clean.matchAll(/https?:\/\/[^\s"'<>)]+/g)) {
          if (!seenUrls.has(m[0])) {
            seenUrls.add(m[0]);
            emit({ type: "url", profileId: profile.id, url: m[0] });
          }
        }
        const code2 = /\b(?:code|enter)\b[^A-Z0-9]*([A-Z0-9]{4,8}(?:-[A-Z0-9]{4,8})+|[A-Z0-9]{6,9})\b/i.exec(
          clean
        );
        if (code2?.[1] && !seenCodes.has(code2[1]) && !/^https?/i.test(code2[1])) {
          seenCodes.add(code2[1]);
          emit({ type: "code", profileId: profile.id, code: code2[1] });
        }
      }
      const exit = await proc.exit;
      if (controller.signal.aborted) {
        emit({ type: "cancelled", profileId: profile.id });
        throw new CliError(
          "CLI_FAILED",
          "Login cancelled.",
          {},
          `Start the login again when you are ready: iron-proxy login ${profile.id}.`
        );
      }
      const status2 = await this.checkAuth(profile);
      if ((exit.code ?? 0) === 0 && status2 !== "unauthenticated") {
        emit({ type: "completed", profileId: profile.id });
        return;
      }
      const message = `Login did not complete (exit ${exit.code}, status ${status2}).`;
      emit({ type: "failed", profileId: profile.id, message });
      throw new CliError(
        "CLI_FAILED",
        message,
        { code: exit.code, status: status2 },
        `Finish the sign-in in a terminal window: iron-proxy login ${profile.id} --terminal prints the exact command.`
      );
    })();
    done.catch(() => {
    });
    return {
      profileId: profile.id,
      done,
      cancel: () => controller.abort(),
      on: (l) => {
        listeners.add(l);
        return () => listeners.delete(l);
      }
    };
  }
  async logout(profile) {
    if (!this.spec.logoutArgs) return;
    const { binary, lead } = this.exe(profile);
    await run({
      binary,
      args: [...lead, ...this.spec.logoutArgs],
      env: this.env(profile),
      timeoutMs: 3e4,
      cwd: profile.cli.home
    }).catch(() => {
    });
  }
  async listModels(profile) {
    if (!this.spec.modelsArgs || !this.spec.parseModels) return [];
    const { binary, lead } = this.exe(profile);
    const r = await run({
      binary,
      args: [...lead, ...this.spec.modelsArgs],
      env: this.env(profile),
      timeoutMs: 3e4,
      cwd: profile.cli.home
    }).catch(() => void 0);
    return r ? this.spec.parseModels(r.stdout) : [];
  }
};

// ../core/src/adapters/cli/specs.ts
import { homedir } from "os";
import { join } from "path";
function tryJson(line) {
  const t = line.trim();
  if (!t.startsWith("{")) return void 0;
  try {
    return JSON.parse(t);
  } catch {
    return void 0;
  }
}
function pick(obj, path) {
  let cur = obj;
  for (const k of path) {
    if (cur === null || typeof cur !== "object") return void 0;
    cur = cur[k];
  }
  return cur;
}
function usageFrom(u) {
  if (!u || typeof u !== "object") return void 0;
  const r = u;
  const out = {};
  const inp = r.input_tokens ?? r.inputTokens ?? r.prompt_tokens;
  const outp = r.output_tokens ?? r.outputTokens ?? r.completion_tokens;
  const cr = r.cache_read_input_tokens ?? r.cached_input_tokens ?? r.cached_tokens;
  const cw = r.cache_creation_input_tokens;
  if (typeof inp === "number") out.inputTokens = inp;
  if (typeof outp === "number") out.outputTokens = outp;
  if (typeof cr === "number") out.cacheReadTokens = cr;
  if (typeof cw === "number") out.cacheWriteTokens = cw;
  return Object.keys(out).length ? out : void 0;
}
function userHomeFrom(env) {
  const fromEnv = process.platform === "win32" ? env.USERPROFILE : env.HOME;
  return fromEnv || homedir();
}
var COMMON_STRIP = [
  "ANTHROPIC_API_KEY",
  "ANTHROPIC_AUTH_TOKEN",
  "OPENAI_API_KEY",
  "GEMINI_API_KEY",
  "GOOGLE_API_KEY",
  "XAI_API_KEY",
  "GROK_API_KEY"
];
var claudeSpec = {
  provider: "anthropic",
  displayName: "Claude Code",
  binary: "claude",
  homeEnv: "CLAUDE_CONFIG_DIR",
  stripEnv: COMMON_STRIP,
  defaultHome: (env) => env.CLAUDE_CONFIG_DIR || join(userHomeFrom(env), ".claude"),
  run({ prompt, system, model }) {
    const args = [
      "-p",
      prompt,
      "--output-format",
      "stream-json",
      "--verbose",
      "--include-partial-messages",
      "--tools",
      "",
      "--max-turns",
      "1",
      "--no-session-persistence",
      "--setting-sources",
      ""
    ];
    if (model) args.push("--model", model);
    if (system) args.push("--system-prompt", system);
    return { args };
  },
  parseLine(line) {
    const j = tryJson(line);
    if (!j) return line.trim() ? [{ kind: "text", delta: line + "\n" }] : [];
    switch (j.type) {
      case "stream_event": {
        const ev = j.event;
        if (ev?.type === "content_block_delta") {
          const text = pick(ev, ["delta", "text"]);
          if (typeof text === "string" && pick(ev, ["delta", "type"]) === "text_delta")
            return [{ kind: "text", delta: text }];
        }
        return [{ kind: "ignore" }];
      }
      case "assistant": {
        const blocks = pick(j, ["message", "content"]) ?? [];
        const text = blocks.filter((b) => b.type === "text").map((b) => String(b.text ?? "")).join("");
        return text ? [{ kind: "final", text }] : [{ kind: "ignore" }];
      }
      case "result": {
        const out = [];
        const u = usageFrom(j.usage);
        if (u) out.push({ kind: "usage", usage: u });
        if (j.is_error)
          out.push({
            kind: "error",
            text: String(j.result ?? j.error ?? "Claude Code reported an error.")
          });
        else if (typeof j.result === "string" && j.result)
          out.push({ kind: "final", text: j.result });
        return out.length ? out : [{ kind: "ignore" }];
      }
      case "error":
        return [{ kind: "error", text: String(j.message ?? j.error ?? line) }];
      default:
        return [{ kind: "ignore" }];
    }
  },
  login: { args: ["auth", "login"], headlessArgs: ["auth", "login"] },
  logoutArgs: ["auth", "logout"],
  status: {
    args: ["auth", "status"],
    interpret(r) {
      const j = tryJson(
        r.stdout.trim().split("\n").find((l) => l.trim().startsWith("{")) ?? ""
      ) ?? tryJson(r.stdout);
      if (j && typeof j.loggedIn === "boolean") return j.loggedIn ? "ok" : "unauthenticated";
      if (/logged in|authenticated/i.test(r.stdout) && !/not logged in/i.test(r.stdout))
        return "ok";
      if (/not logged in|no credentials/i.test(r.stdout + r.stderr)) return "unauthenticated";
      return "unknown";
    }
  }
};
var codexSpec = {
  provider: "openai",
  displayName: "Codex CLI",
  binary: "codex",
  homeEnv: "CODEX_HOME",
  stripEnv: COMMON_STRIP,
  defaultHome: (env) => env.CODEX_HOME || join(userHomeFrom(env), ".codex"),
  run({ prompt, system, model }) {
    const args = [
      "exec",
      "--json",
      "--skip-git-repo-check",
      "--ephemeral",
      "--color",
      "never",
      "-s",
      "read-only"
    ];
    if (model) args.push("-m", model);
    args.push("-");
    const stdin = system ? `<system>
${system}
</system>

${prompt}` : prompt;
    return { args, stdin };
  },
  parseLine(line) {
    const j = tryJson(line);
    if (!j) return line.trim() ? [{ kind: "text", delta: line + "\n" }] : [];
    const type = String(j.type ?? "");
    if (type === "item.completed" || type === "item.updated") {
      const item = j.item;
      if (item?.type === "agent_message" && typeof item.text === "string")
        return [{ kind: "final", text: item.text }];
      if (item?.type === "error")
        return [{ kind: "error", text: String(item.message ?? item.text ?? line) }];
      return [{ kind: "ignore" }];
    }
    if (type === "item.delta" || type === "agent_message_delta" || type === "message.delta") {
      const delta = j.delta ?? pick(j, ["item", "delta"]) ?? j.text;
      return typeof delta === "string" ? [{ kind: "text", delta }] : [{ kind: "ignore" }];
    }
    if (type === "turn.completed") {
      const u = usageFrom(j.usage);
      return u ? [{ kind: "usage", usage: u }] : [{ kind: "ignore" }];
    }
    if (type === "turn.failed" || type === "error") {
      const msg = pick(j, ["error", "message"]) ?? j.message ?? line;
      return [{ kind: "error", text: String(msg) }];
    }
    return [{ kind: "ignore" }];
  },
  login: { args: ["login"], headlessArgs: ["login", "--device-auth"] },
  logoutArgs: ["logout"],
  status: {
    args: ["login", "status"],
    interpret(r) {
      const all = r.stdout + "\n" + r.stderr;
      if (/not logged in|not authenticated|no credentials/i.test(all)) return "unauthenticated";
      if (/logged in|authenticated|api key/i.test(all)) return "ok";
      return r.code === 0 ? "ok" : "unknown";
    }
  }
};
var grokSpec = {
  provider: "xai",
  displayName: "Grok CLI",
  binary: "grok",
  homeEnv: "GROK_HOME",
  stripEnv: COMMON_STRIP,
  defaultHome: (env) => env.GROK_HOME || join(userHomeFrom(env), ".grok"),
  run({ prompt, system, model }) {
    const args = [
      "-p",
      prompt,
      "--output-format",
      "streaming-messages-json",
      "--include-partial-messages",
      "--tools",
      "",
      "--max-turns",
      "1",
      "--permission-mode",
      "default"
    ];
    if (model) args.push("-m", model);
    if (system) args.push("--system-prompt-override", system);
    return { args };
  },
  parseLine(line) {
    const j = tryJson(line);
    if (!j) return line.trim() ? [{ kind: "text", delta: line + "\n" }] : [];
    const type = String(j.type ?? "");
    if (type === "stream_event") {
      const ev = j.event;
      const text = pick(ev, ["delta", "text"]) ?? (typeof ev?.text === "string" ? ev.text : void 0);
      return typeof text === "string" ? [{ kind: "text", delta: text }] : [{ kind: "ignore" }];
    }
    if (type === "assistant" || j.role === "assistant" || type === "message") {
      const content = pick(j, ["message", "content"]) ?? j.content;
      if (typeof content === "string") return [{ kind: "final", text: content }];
      if (Array.isArray(content)) {
        const text = content.filter((b) => b && b.type === "text").map((b) => String(b.text ?? "")).join("");
        return text ? [{ kind: "final", text }] : [{ kind: "ignore" }];
      }
      return [{ kind: "ignore" }];
    }
    if (type === "result") {
      const out = [];
      const u = usageFrom(j.usage);
      if (u) out.push({ kind: "usage", usage: u });
      if (j.is_error || j.error) out.push({ kind: "error", text: String(j.result ?? j.error) });
      else if (typeof j.result === "string" && j.result)
        out.push({ kind: "final", text: j.result });
      return out.length ? out : [{ kind: "ignore" }];
    }
    if (type === "error") return [{ kind: "error", text: String(j.message ?? j.error ?? line) }];
    return [{ kind: "ignore" }];
  },
  login: { args: ["login", "--oauth"], headlessArgs: ["login", "--device-auth"] },
  logoutArgs: ["logout"],
  status: {
    args: ["models"],
    interpret(r) {
      const all = r.stdout + "\n" + r.stderr;
      if (/not authenticated|not logged in|please (?:log|sign) in/i.test(all))
        return "unauthenticated";
      if (/available models/i.test(all) && r.code === 0) return "ok";
      return "unknown";
    }
  },
  modelsArgs: ["models"],
  parseModels(stdout) {
    return [...stdout.matchAll(/^\s*[*-]\s+([a-z0-9][\w.-]*)/gim)].map((m) => m[1]).filter(Boolean);
  }
};
var geminiSpec = {
  provider: "google",
  displayName: "Gemini CLI",
  binary: "gemini",
  homeEnv: "GEMINI_CLI_HOME",
  stripEnv: COMMON_STRIP,
  // Unverified: where Gemini CLI keeps its sign-in by default (and how GEMINI_CLI_HOME
  // maps onto it) is not confirmed, so an existing Gemini login is not offered for
  // adoption. See docs/PROVIDERS.md.
  defaultHome: () => void 0,
  run({ prompt, system, model }) {
    const args = [
      "-p",
      system ? `<system>
${system}
</system>

${prompt}` : prompt,
      "--output-format",
      "stream-json",
      "--approval-mode",
      "default"
    ];
    if (model) args.push("-m", model);
    return { args };
  },
  parseLine(line) {
    const j = tryJson(line);
    if (!j) return line.trim() ? [{ kind: "text", delta: line + "\n" }] : [];
    const type = String(j.type ?? "");
    if (type === "message" && j.role === "assistant") {
      const c = j.content;
      if (typeof c === "string")
        return j.delta === true ? [{ kind: "text", delta: c }] : [{ kind: "final", text: c }];
      return [{ kind: "ignore" }];
    }
    if (type === "content" || type === "text") {
      const t = j.text ?? j.content ?? j.delta;
      return typeof t === "string" ? [{ kind: "text", delta: t }] : [{ kind: "ignore" }];
    }
    if (type === "result") {
      const out = [];
      const u = usageFrom(j.usage ?? pick(j, ["stats", "usage"]));
      if (u) out.push({ kind: "usage", usage: u });
      if (typeof j.response === "string" && j.response)
        out.push({ kind: "final", text: j.response });
      if (j.error)
        out.push({ kind: "error", text: String(pick(j, ["error", "message"]) ?? j.error) });
      return out.length ? out : [{ kind: "ignore" }];
    }
    if (type === "error")
      return [{ kind: "error", text: String(j.message ?? pick(j, ["error", "message"]) ?? line) }];
    return [{ kind: "ignore" }];
  },
  // Gemini CLI has no login subcommand: the interactive app signs in on first run.
  login: { args: [], requiresTerminal: true },
  status: {
    args: ["-p", "Reply with the single word OK.", "--output-format", "json"],
    interpret(r) {
      const all = r.stdout + "\n" + r.stderr;
      if (/not (?:logged in|authenticated)|please (?:log|sign) in|auth/i.test(all) && r.code !== 0)
        return "unauthenticated";
      return r.code === 0 ? "ok" : "unknown";
    }
  }
};
var CLI_SPECS = [claudeSpec, codexSpec, grokSpec, geminiSpec];

// ../core/src/adapters/index.ts
var OPENAI_BASE_URL = "https://api.openai.com/v1";
var OPENAI_DEFAULT_MODEL = "gpt-5";
var XAI_BASE_URL = "https://api.x.ai/v1";
var XAI_DEFAULT_MODEL = "grok-4";
function anthropicAdapter() {
  return {
    id: "anthropic",
    displayName: "Anthropic (Claude)",
    defaultModel: ANTHROPIC_DEFAULT_MODEL,
    lanes: { "api-key": new AnthropicApiLane(), cli: new CliLane(claudeSpec) }
  };
}
function openaiAdapter() {
  return {
    id: "openai",
    displayName: "OpenAI (ChatGPT / Codex)",
    defaultModel: OPENAI_DEFAULT_MODEL,
    lanes: {
      "api-key": new OpenAIChatLane({
        provider: "openai",
        defaultBaseUrl: OPENAI_BASE_URL,
        defaultModel: OPENAI_DEFAULT_MODEL
      }),
      cli: new CliLane(codexSpec)
    }
  };
}
function googleAdapter() {
  return {
    id: "google",
    displayName: "Google (Gemini)",
    defaultModel: GOOGLE_DEFAULT_MODEL,
    lanes: { "api-key": new GoogleApiLane(), cli: new CliLane(geminiSpec) }
  };
}
function xaiAdapter() {
  return {
    id: "xai",
    displayName: "xAI (Grok)",
    defaultModel: XAI_DEFAULT_MODEL,
    lanes: {
      "api-key": new OpenAIChatLane({
        provider: "xai",
        defaultBaseUrl: XAI_BASE_URL,
        defaultModel: XAI_DEFAULT_MODEL
      }),
      cli: new CliLane(grokSpec)
    }
  };
}
function openaiCompatibleAdapter() {
  return {
    id: "openai-compatible",
    displayName: "OpenAI-compatible endpoint",
    defaultModel: "",
    lanes: {
      "api-key": new OpenAIChatLane({
        provider: "openai-compatible",
        defaultBaseUrl: "http://127.0.0.1:11434/v1",
        defaultModel: ""
      })
    }
  };
}
function createDefaultRegistry() {
  return new AdapterRegistry().register(anthropicAdapter()).register(openaiAdapter()).register(googleAdapter()).register(xaiAdapter()).register(openaiCompatibleAdapter());
}

// ../core/src/router/router.ts
var USAGE_STALE_MS = 10 * 6e4;
var CONTINUE_INSTRUCTION = "Continue exactly where you stopped. Do not repeat any text you already wrote.";
function isUsageFresh(usage2, now) {
  if (usage2.resetAt !== void 0) {
    const reset = Date.parse(usage2.resetAt);
    return !Number.isNaN(reset) && reset > now;
  }
  const seen = Date.parse(usage2.observedAt);
  return !Number.isNaN(seen) && now - seen <= USAGE_STALE_MS;
}
function isUsageHot(usage2, threshold, now) {
  if (!usage2 || !(threshold < 1)) return false;
  if (usage2.resetAt === void 0 || !isUsageFresh(usage2, now)) return false;
  return usage2.utilisation !== void 0 && usage2.utilisation >= threshold;
}
function continuationRequest(req, partial, profile) {
  const native = profile.provider === "anthropic" && profile.lane === "api-key";
  const text = native ? partial.trimEnd() : partial;
  const messages = [...req.messages];
  const last = messages[messages.length - 1];
  if (last?.role === "assistant")
    messages[messages.length - 1] = {
      ...last,
      content: [...last.content, { type: "text", text }]
    };
  else messages.push({ role: "assistant", content: [{ type: "text", text }] });
  if (!native)
    messages.push({ role: "user", content: [{ type: "text", text: CONTINUE_INSTRUCTION }] });
  return { ...req, messages };
}
function streamInterrupted(profile, signal) {
  return new IronProxyError(
    "STREAM_INTERRUPTED",
    `Account "${profile.title}" hit a limit mid-stream. Resend to continue on the next account.`,
    {
      retryable: true,
      details: { profileId: profile.id, signal },
      hint: "Resend; the next account will take it."
    }
  );
}
function clockTime(iso) {
  return new Date(iso).toLocaleTimeString(void 0, { hour: "numeric", minute: "2-digit" });
}
function preemptSignal(usage2) {
  const pct = Math.round((usage2.utilisation ?? 1) * 100);
  return {
    kind: "rate-limit",
    source: "usage",
    message: `Switched early: ${pct}% of the window used${usage2.resetAt ? `, resets ${clockTime(usage2.resetAt)}` : ""}`,
    ...usage2.resetAt ? { resetAt: usage2.resetAt } : {}
  };
}
var Router = class {
  constructor(deps) {
    this.deps = deps;
    this.policy = { ...DEFAULT_POLICY, ...deps.policy ?? {} };
    this.clock = deps.clock ?? systemClock;
    this.fetch = deps.fetch ?? ((input, init) => fetch(input, init));
  }
  deps;
  policy;
  clock;
  fetch;
  activeByProvider = /* @__PURE__ */ new Map();
  /** Usage writes in flight per profile, so a later state write never overwrites them. */
  usageWrites = /* @__PURE__ */ new Map();
  usageListeners = /* @__PURE__ */ new Set();
  policyFor(provider) {
    return { ...this.policy, ...this.policy.providers?.[provider] ?? {} };
  }
  /* ---------------------------------------------------------------- */
  /* Public API                                                       */
  /* ---------------------------------------------------------------- */
  async complete(req, opts = {}) {
    const provider = await this.resolveProvider(req, opts);
    const requestId = newId("req");
    const tried = [];
    let lastSignal;
    for (; ; ) {
      const attempt = await this.nextAttempt(provider, opts, tried);
      if (!attempt) throw await this.exhausted(provider, tried, lastSignal);
      const { profile, ctx } = attempt;
      tried.push(profile.id);
      const startedAt = this.clock.now();
      this.deps.emitter.emit({
        type: "request.started",
        requestId,
        provider,
        profileId: profile.id
      });
      try {
        const lane = this.deps.registry.laneFor(profile);
        const res = await this.withOverloadRetry(provider, profile, () => lane.complete(req, ctx));
        await this.markServed(profile, provider, attempt.preempted ?? lastSignal);
        const out = { ...res, provider, profileId: profile.id };
        if (!opts.includeRaw) delete out.raw;
        this.deps.emitter.emit({
          type: "request.finished",
          requestId,
          provider,
          profileId: profile.id,
          durationMs: this.clock.now() - startedAt,
          ...res.usage ? { usage: res.usage } : {}
        });
        return out;
      } catch (err) {
        const signal = this.asQuotaSignal(err);
        if (signal) {
          lastSignal = signal;
          await this.park(profile, signal);
          if (opts.strict) throw new QuotaExceededError(profile.id, signal, provider);
          continue;
        }
        this.deps.emitter.emit({
          type: "request.failed",
          requestId,
          provider,
          error: serializeError(err)
        });
        throw err;
      } finally {
        attempt.release();
      }
    }
  }
  async *stream(req, opts = {}) {
    const provider = await this.resolveProvider(req, opts);
    const requestId = newId("req");
    const tried = [];
    let lastSignal;
    let previous;
    const resume = !opts.strict && (opts.resumeInterrupted ?? this.policyFor(provider).resumeInterrupted);
    let sentText = "";
    const openTools = /* @__PURE__ */ new Set();
    let interrupted;
    for (; ; ) {
      const attempt = await this.nextAttempt(provider, opts, tried);
      if (!attempt) {
        if (interrupted) {
          const e = streamInterrupted(interrupted.profile, interrupted.signal);
          this.deps.emitter.emit({
            type: "request.failed",
            requestId,
            provider,
            error: e.toJSON()
          });
          yield { type: "error", error: e.toJSON() };
          return;
        }
        const err = await this.exhausted(provider, tried, lastSignal);
        yield { type: "error", error: serializeError(err) };
        return;
      }
      const { profile, ctx } = attempt;
      tried.push(profile.id);
      const attemptReq = interrupted ? continuationRequest(req, sentText, profile) : req;
      const startedAt = this.clock.now();
      this.deps.emitter.emit({
        type: "request.started",
        requestId,
        provider,
        profileId: profile.id
      });
      let emitted = false;
      let usage2;
      try {
        const lane = this.deps.registry.laneFor(profile);
        let overloads = 0;
        for (; ; ) {
          try {
            for await (const ev of lane.stream(attemptReq, ctx)) {
              const full = ev.type === "start" ? { ...ev, provider, profileId: profile.id } : ev;
              if (full.type === "start" && interrupted) {
                const from = interrupted;
                interrupted = void 0;
                emitted = true;
                yield {
                  type: "switched",
                  fromProfileId: from.profile.id,
                  toProfileId: profile.id,
                  reason: lastSignal ?? from.signal,
                  resumed: true
                };
                continue;
              }
              if (full.type === "start" && previous) {
                yield {
                  type: "switched",
                  fromProfileId: previous,
                  toProfileId: profile.id,
                  reason: lastSignal
                };
              }
              if (full.type === "usage") usage2 = full.usage;
              else if (full.type === "text") sentText += full.delta;
              else if (full.type === "tool_call_start") openTools.add(full.id);
              else if (full.type === "tool_call_end") openTools.delete(full.id);
              emitted = true;
              yield full;
            }
            break;
          } catch (err) {
            const sig = this.asQuotaSignal(err);
            if (sig?.kind === "overloaded" && !emitted && overloads < this.policyFor(provider).overloadRetries) {
              overloads++;
              await sleep(this.backoff(overloads), ctx.signal);
              continue;
            }
            throw err;
          }
        }
        await this.markServed(profile, provider, attempt.preempted ?? lastSignal);
        this.deps.emitter.emit({
          type: "request.finished",
          requestId,
          provider,
          profileId: profile.id,
          durationMs: this.clock.now() - startedAt,
          ...usage2 ? { usage: usage2 } : {}
        });
        return;
      } catch (err) {
        const signal = this.asQuotaSignal(err);
        if (signal) {
          lastSignal = signal;
          await this.park(profile, signal);
          if (!emitted && !opts.strict) {
            if (!interrupted) previous = profile.id;
            continue;
          }
          if (emitted && resume && sentText.trim() && openTools.size === 0) {
            interrupted = { profile, signal };
            continue;
          }
          const e = emitted ? streamInterrupted(profile, signal) : new QuotaExceededError(profile.id, signal, provider);
          this.deps.emitter.emit({
            type: "request.failed",
            requestId,
            provider,
            error: e.toJSON()
          });
          yield { type: "error", error: e.toJSON() };
          return;
        }
        if (err.name === "AbortError") return;
        this.deps.emitter.emit({
          type: "request.failed",
          requestId,
          provider,
          error: serializeError(err)
        });
        yield { type: "error", error: serializeError(err) };
        return;
      } finally {
        attempt.release();
      }
    }
  }
  /** Profile currently serving a provider, if any request has run. */
  activeProfileId(provider) {
    return this.activeByProvider.get(provider);
  }
  /**
   * Called after every usage snapshot a lane reports has been stored on the
   * profile's state (the manager records utilisation samples from it). Listener
   * errors are swallowed. Returns an unsubscribe function.
   */
  onUsage(listener) {
    this.usageListeners.add(listener);
    return () => {
      this.usageListeners.delete(listener);
    };
  }
  /** Clear a park early (user pressed "try again"). */
  async unpark(profileId) {
    const st = await this.state(profileId);
    if (st.status === "parked") {
      st.status = "ready";
      delete st.parkedUntil;
      delete st.parkedReason;
      await this.deps.states.put(st);
      this.deps.emitter.emit({ type: "profile.unparked", profileId });
      this.deps.emitter.emit({ type: "profile.state", state: st });
    }
  }
  /* ---------------------------------------------------------------- */
  /* Candidate selection                                              */
  /* ---------------------------------------------------------------- */
  async candidates(provider, opts = {}) {
    const all = (await this.deps.profiles.list()).filter(
      (p) => p.provider === provider && p.enabled
    );
    if (opts.profileId) {
      const pinned = all.find((p) => p.id === opts.profileId);
      if (!pinned) throw new ProfileNotFoundError(opts.profileId);
      if (opts.strict) return [pinned];
      return [pinned, ...all.filter((p) => p.id !== pinned.id).sort((a, b) => a.order - b.order)];
    }
    return all.sort((a, b) => a.order - b.order);
  }
  /**
   * Whether a request would try this profile right now. Clears a park whose
   * cooldown has passed first (the auto-return), exactly as every request does.
   * Unauthenticated profiles are skipped unless the caller pinned one.
   */
  async availability(profile, opts = {}, now = this.clock.now()) {
    const st = await this.state(profile.id);
    if (st.status === "parked") {
      if (st.parkedUntil && new Date(st.parkedUntil).getTime() <= now) {
        st.status = "ready";
        delete st.parkedUntil;
        delete st.parkedReason;
        await this.deps.states.put(st);
        this.deps.emitter.emit({ type: "profile.unparked", profileId: profile.id });
      } else return { usable: false, state: st };
    }
    if (st.status === "unauthenticated" && !opts.profileId) return { usable: false, state: st };
    try {
      this.deps.registry.laneFor(profile);
    } catch {
      return { usable: false, state: st };
    }
    return { usable: true, state: st };
  }
  async nextAttempt(provider, opts, tried) {
    const now = this.clock.now();
    const candidates = await this.candidates(provider, opts);
    if (!candidates.length && !tried.length) throw new NoProfileError(provider);
    const hot = await this.hotProfiles(provider, candidates, opts, now);
    const ordered = hot.size && hot.size < candidates.length ? [...candidates.filter((p) => !hot.has(p.id)), ...candidates.filter((p) => hot.has(p.id))] : candidates;
    for (const profile of ordered) {
      if (tried.includes(profile.id)) continue;
      if (!(await this.availability(profile, opts, now)).usable) continue;
      let preempted;
      if (!hot.has(profile.id)) {
        for (const ahead of candidates.slice(0, candidates.indexOf(profile))) {
          const usage2 = hot.get(ahead.id);
          if (!usage2 || tried.includes(ahead.id)) continue;
          if (!(await this.availability(ahead, opts, now)).usable) continue;
          preempted = preemptSignal(usage2);
          break;
        }
      }
      const controller = new AbortController();
      const timeout = setTimeout(
        () => controller.abort(
          new IronProxyError("TIMEOUT", "Request timed out.", { retryable: true })
        ),
        opts.timeoutMs ?? this.policyFor(provider).requestTimeoutMs
      );
      timeout.unref?.();
      const onOuterAbort = () => controller.abort(opts.signal?.reason);
      opts.signal?.addEventListener("abort", onOuterAbort, { once: true });
      if (opts.signal?.aborted) onOuterAbort();
      const ctx = {
        profile,
        vault: this.deps.vault,
        fetch: this.fetch,
        signal: controller.signal,
        now: () => this.clock.now(),
        reportUsage: (usage2) => this.reportUsage(profile.id, usage2)
      };
      return {
        profile,
        ctx,
        release: () => {
          clearTimeout(timeout);
          opts.signal?.removeEventListener("abort", onOuterAbort);
        },
        ...preempted ? { preempted } : {}
      };
    }
    return void 0;
  }
  /**
   * Candidates whose last usage snapshot is nearly full (see isUsageHot), with
   * that snapshot. Empty when the caller pinned a profile or the provider's
   * policy turns pre-emption off.
   */
  async hotProfiles(provider, candidates, opts, now) {
    const hot = /* @__PURE__ */ new Map();
    const threshold = this.policyFor(provider).preemptAtUtilisation;
    if (opts.profileId || !(threshold < 1)) return hot;
    for (const p of candidates) {
      await this.usageWrites.get(p.id);
      const usage2 = (await this.state(p.id)).usage;
      if (usage2 && isUsageHot(usage2, threshold, now)) hot.set(p.id, usage2);
    }
    return hot;
  }
  async exhausted(provider, tried, last) {
    if (!tried.length) return new NoProfileError(provider);
    const states = await this.deps.states.all();
    let earliest;
    for (const id of tried) {
      const until = states[id]?.parkedUntil;
      if (until && (!earliest || until < earliest)) earliest = until;
    }
    if (last?.kind === "auth-expired" && tried.length === 1) {
      const p = await this.deps.profiles.get(tried[0]);
      return new AuthRequiredError(tried[0], last.message, {
        ...p ? { title: p.title, lane: p.lane } : {}
      });
    }
    this.deps.emitter.emit({
      type: "provider.exhausted",
      provider,
      ...earliest ? { earliestResetAt: earliest } : {}
    });
    return new AllProfilesExhaustedError(provider, earliest, tried);
  }
  async resolveProvider(req, opts) {
    if (opts.provider) return opts.provider;
    if (opts.profileId) {
      const p = await this.deps.profiles.get(opts.profileId);
      if (!p) throw new ProfileNotFoundError(opts.profileId);
      return p.provider;
    }
    const inferred = inferProvider(req.model);
    if (inferred) return inferred;
    const providers = new Set(
      (await this.deps.profiles.list()).filter((p) => p.enabled).map((p) => p.provider)
    );
    if (providers.size === 1) return [...providers][0];
    throw new IronProxyError(
      "INVALID_REQUEST",
      "Cannot infer the provider from the model name. Pass `provider` or a recognisable model.",
      {
        details: { model: req.model, configuredProviders: [...providers] },
        hint: "Pass the provider (x-iron-provider header, --provider, or RunOptions.provider) or a model name starting with claude, gpt, gemini or grok."
      }
    );
  }
  /* ---------------------------------------------------------------- */
  /* State                                                            */
  /* ---------------------------------------------------------------- */
  async state(profileId) {
    return await this.deps.states.get(profileId) ?? { profileId, status: "unknown", served: 0 };
  }
  /**
   * Resolves once every usage snapshot reported so far has been stored and its
   * onUsage listeners have run, including writes queued while waiting.
   */
  async settleUsage() {
    while (this.usageWrites.size) await Promise.all([...this.usageWrites.values()]);
  }
  /** Queue a usage write behind any earlier one for the same profile. */
  reportUsage(profileId, usage2) {
    const prev = this.usageWrites.get(profileId) ?? Promise.resolve();
    const next = prev.then(() => this.recordUsage(profileId, usage2)).catch(() => {
    });
    this.usageWrites.set(profileId, next);
    void next.then(() => {
      if (this.usageWrites.get(profileId) === next) this.usageWrites.delete(profileId);
    });
  }
  async recordUsage(profileId, usage2) {
    const st = await this.state(profileId);
    st.usage = usage2;
    await this.deps.states.put(st);
    this.deps.emitter.emit({ type: "profile.state", state: st });
    for (const l of this.usageListeners) {
      try {
        l(profileId, usage2);
      } catch {
      }
    }
  }
  async markServed(profile, provider, reason) {
    await this.usageWrites.get(profile.id);
    const prev = this.activeByProvider.get(provider);
    this.activeByProvider.set(provider, profile.id);
    const st = await this.state(profile.id);
    st.status = "active";
    st.served += 1;
    st.lastUsedAt = new Date(this.clock.now()).toISOString();
    delete st.lastError;
    await this.deps.states.put(st);
    this.deps.emitter.emit({ type: "profile.state", state: st });
    if (prev && prev !== profile.id) {
      const ps = await this.state(prev);
      if (ps.status === "active") {
        ps.status = "ready";
        await this.deps.states.put(ps);
        this.deps.emitter.emit({ type: "profile.state", state: ps });
      }
    }
    if (prev !== profile.id) {
      this.deps.emitter.emit({
        type: "profile.switched",
        provider,
        ...prev ? { fromProfileId: prev } : {},
        toProfileId: profile.id,
        ...reason ? { reason } : {}
      });
    }
  }
  /**
   * Record a request that ran outside the router (an external executor ran the
   * vendor CLI as this account itself) exactly like one the router served: the
   * profile becomes the provider's `active`, `served` increments, a switch away
   * from the previous active account is announced (with that account's park
   * reason, when it is parked), and `request.finished` carries the usage.
   */
  async recordServed(profile, info = {}) {
    const provider = profile.provider;
    const prev = this.activeByProvider.get(provider);
    let reason;
    if (prev && prev !== profile.id) {
      const ps = await this.state(prev);
      if (ps.status === "parked" || ps.status === "unauthenticated") reason = ps.parkedReason;
    }
    await this.markServed(profile, provider, reason);
    this.deps.emitter.emit({
      type: "request.finished",
      requestId: newId("req"),
      provider,
      profileId: profile.id,
      durationMs: info.durationMs ?? 0,
      ...info.usage ? { usage: info.usage } : {},
      ...info.model ? { model: info.model } : {}
    });
    return this.state(profile.id);
  }
  async park(profile, signal) {
    const policy = this.policyFor(profile.provider);
    const now = this.clock.now();
    await this.usageWrites.get(profile.id);
    const st = await this.state(profile.id);
    if (signal.kind === "auth-expired") {
      st.status = "unauthenticated";
      delete st.parkedUntil;
      st.parkedReason = signal;
      st.lastError = signal.message ?? "Authentication expired.";
    } else {
      let ms;
      if (signal.resetAt) ms = Math.max(0, new Date(signal.resetAt).getTime() - now);
      else if (signal.retryAfterMs !== void 0) ms = signal.retryAfterMs;
      else
        ms = signal.kind === "billing" ? policy.billingCooldownMs : signal.kind === "quota-exhausted" ? policy.defaultQuotaCooldownMs : signal.kind === "overloaded" ? policy.overloadCooldownMs : policy.defaultRateLimitCooldownMs;
      ms = Math.min(Math.max(ms, 1e3), policy.maxCooldownMs);
      st.status = "parked";
      st.parkedUntil = new Date(now + ms).toISOString();
      st.parkedReason = signal;
      st.lastError = signal.message ?? signal.kind;
    }
    await this.deps.states.put(st);
    this.deps.emitter.emit({
      type: "profile.parked",
      profileId: profile.id,
      reason: signal,
      ...st.parkedUntil ? { until: st.parkedUntil } : {}
    });
    this.deps.emitter.emit({ type: "profile.state", state: st });
  }
  /* ---------------------------------------------------------------- */
  /* Helpers                                                          */
  /* ---------------------------------------------------------------- */
  asQuotaSignal(err) {
    if (err instanceof LaneQuotaSignal) return err.signal;
    if (err instanceof QuotaExceededError) return err.signal;
    if (err instanceof AuthRequiredError)
      return { kind: "auth-expired", source: "status", message: err.message };
    if (err instanceof ProviderError && err.status !== void 0 && err.status >= 500 && err.status !== 501) {
      return { kind: "overloaded", source: "status", message: err.message };
    }
    return void 0;
  }
  backoff(n) {
    return Math.min(2e3 * 2 ** (n - 1), 1e4);
  }
  async withOverloadRetry(provider, profile, fn) {
    const retries = this.policyFor(provider).overloadRetries;
    for (let i = 0; ; i++) {
      try {
        return await fn();
      } catch (err) {
        const sig = this.asQuotaSignal(err);
        if (sig?.kind === "overloaded" && i < retries) {
          await sleep(this.backoff(i + 1));
          continue;
        }
        void profile;
        throw err;
      }
    }
  }
};

// ../core/src/store/profile-store.ts
import { join as join2 } from "path";

// ../core/src/store/file-lock.ts
import { randomBytes as randomBytes2 } from "crypto";
import { mkdir as mkdir3, open, readFile as readFile2, rename as rename2, stat, unlink } from "fs/promises";
import { dirname as dirname2 } from "path";
var LOCK_TIMEOUT_MS = 2e3;
var LOCK_STALE_MS = 1e4;
var pause = (ms) => new Promise((r) => setTimeout(r, ms));
function code(err) {
  return err?.code;
}
function lockPathFor(path) {
  return `${path}.lock`;
}
async function withFileLock(path, fn, opts = {}) {
  const lock = lockPathFor(path);
  const timeoutMs = opts.timeoutMs ?? LOCK_TIMEOUT_MS;
  const staleMs = opts.staleMs ?? LOCK_STALE_MS;
  const token = `${process.pid}-${randomBytes2(6).toString("hex")}`;
  const started = Date.now();
  let delay = 5;
  await mkdir3(dirname2(lock), { recursive: true });
  for (; ; ) {
    if (await createExclusive(lock, token)) break;
    let stale;
    try {
      stale = Date.now() - (await stat(lock)).mtimeMs > staleMs;
    } catch {
      continue;
    }
    if (stale && await takeOverStale(lock, token, staleMs)) continue;
    if (Date.now() - started >= timeoutMs) {
      throw new Error(`Timed out waiting for the lock on ${lock}`);
    }
    await pause(delay + Math.floor(Math.random() * delay));
    delay = Math.min(delay * 2, 50);
  }
  try {
    return await fn();
  } finally {
    try {
      if (await readFile2(lock, "utf8") === token) await unlink(lock);
    } catch {
    }
  }
}
async function createExclusive(path, content) {
  try {
    const fh = await open(path, "wx", 384);
    try {
      await fh.writeFile(content, "utf8");
    } finally {
      await fh.close();
    }
    return true;
  } catch (err) {
    const c = code(err);
    if (c !== "EEXIST" && c !== "EPERM" && c !== "EACCES") throw err;
    return false;
  }
}
async function takeOverStale(lock, token, staleMs) {
  const guard = `${lock}.takeover`;
  if (!await createExclusive(guard, token)) {
    try {
      if (Date.now() - (await stat(guard)).mtimeMs > staleMs) await unlink(guard);
    } catch {
    }
    return false;
  }
  try {
    try {
      if (Date.now() - (await stat(lock)).mtimeMs <= staleMs) return false;
    } catch {
      return true;
    }
    const grave = `${lock}.stale-${token}`;
    try {
      await rename2(lock, grave);
    } catch (err) {
      return code(err) === "ENOENT";
    }
    await unlink(grave).catch(() => {
    });
    return true;
  } finally {
    try {
      if (await readFile2(guard, "utf8") === token) await unlink(guard);
    } catch {
    }
  }
}
async function fileFingerprint(path) {
  try {
    const st = await stat(path);
    return `${st.ino}:${st.mtimeMs}:${st.size}`;
  } catch {
    return "missing";
  }
}

// ../core/src/store/profile-store.ts
var FileProfileStore = class {
  path;
  chain = Promise.resolve();
  cache;
  /** Identity of the file the cache came from (or was last written to). */
  seen;
  /** Identity of the file this store last wrote. */
  lastWritten;
  /** File versions written elsewhere that this store has read. */
  external = 0;
  /** This process's changes not yet on disk: puts by id, and deleted ids. */
  dirty = /* @__PURE__ */ new Map();
  deleted = /* @__PURE__ */ new Set();
  lock;
  constructor(dataDir, opts = {}) {
    this.path = join2(dataDir, "profiles.json");
    this.lock = opts.lock ?? {};
  }
  async readDisk() {
    const file = await readJsonFile(this.path, { version: 1, profiles: [] });
    return new Map((file.profiles ?? []).map((p) => [p.id, p]));
  }
  overlay(map) {
    for (const id of this.deleted) map.delete(id);
    for (const [id, p] of this.dirty) map.set(id, structuredClone(p));
    return map;
  }
  async load() {
    const fp = await fileFingerprint(this.path);
    if (this.cache && fp === this.seen) return this.cache;
    this.noteVersion(fp);
    this.cache = this.overlay(await this.readDisk());
    this.seen = fp;
    return this.cache;
  }
  /** Re-read the file under the lock, apply this process's changes, write it back. */
  async commit() {
    try {
      await withFileLock(
        this.path,
        async () => {
          this.noteVersion(await fileFingerprint(this.path));
          const merged = this.overlay(await this.readDisk());
          const file = { version: 1, profiles: [...merged.values()] };
          await writeJsonFileAtomic(this.path, file);
          this.cache = merged;
          this.seen = this.lastWritten = await fileFingerprint(this.path);
        },
        this.lock
      );
    } catch (err) {
      this.cache = void 0;
      throw err;
    } finally {
      this.dirty.clear();
      this.deleted.clear();
    }
  }
  /** Count a version of the file that neither this store wrote nor had read already. */
  noteVersion(fp) {
    if (fp !== "missing" && fp !== this.seen && fp !== this.lastWritten) this.external++;
  }
  serial(fn) {
    const next = this.chain.then(fn, fn);
    this.chain = next.catch(() => {
    });
    return next;
  }
  /**
   * How many versions of the file written by another process (the CLI, a
   * running `serve`, another app) this store has read so far. A watcher can
   * compare it before and after a reload to tell another process's changes from
   * this process's own writes.
   */
  get externalWrites() {
    return this.external;
  }
  list() {
    return this.serial(
      async () => [...(await this.load()).values()].map((p) => structuredClone(p))
    );
  }
  get(id) {
    return this.serial(async () => {
      const p = (await this.load()).get(id);
      return p ? structuredClone(p) : void 0;
    });
  }
  put(profile) {
    return this.serial(async () => {
      this.deleted.delete(profile.id);
      this.dirty.set(profile.id, structuredClone(profile));
      await this.commit();
    });
  }
  delete(id) {
    return this.serial(async () => {
      this.dirty.delete(id);
      this.deleted.add(id);
      await this.commit();
    });
  }
};

// ../core/src/store/state-store.ts
import { join as join3 } from "path";
var FileStateStore = class {
  path;
  /** The file as last read or written. */
  base = {};
  loaded = false;
  /** Identity of the file `base` came from. */
  seen;
  /** Identity of the file this store last wrote. */
  lastWritten;
  /** File versions written elsewhere that this store has read. */
  external = 0;
  /** Bumped whenever `base` is replaced, so a slower, older read cannot overwrite a newer one. */
  gen = 0;
  /** Unsaved puts by id, and unsaved deletes (id -> sequence number). */
  dirty = /* @__PURE__ */ new Map();
  deleted = /* @__PURE__ */ new Map();
  seq = 0;
  timer;
  writing = Promise.resolve();
  debounceMs;
  lock;
  constructor(dataDir, opts = {}) {
    this.path = join3(dataDir, "state.json");
    this.debounceMs = opts.debounceMs ?? 150;
    this.lock = opts.lock ?? {};
  }
  async readDisk() {
    const file = await readJsonFile(this.path, { version: 1, states: {} });
    return { ...file.states ?? {} };
  }
  async load() {
    const fp = await fileFingerprint(this.path);
    if (this.loaded && fp === this.seen) return;
    const gen = this.gen;
    const states = await this.readDisk();
    if (gen !== this.gen) return;
    this.noteVersion(fp);
    this.base = states;
    this.seen = fp;
    this.loaded = true;
    this.gen++;
  }
  /** Count a version of the file that neither this store wrote nor had read already. */
  noteVersion(fp) {
    if (fp !== "missing" && fp !== this.seen && fp !== this.lastWritten) this.external++;
  }
  view() {
    const out = { ...this.base };
    for (const id of this.deleted.keys()) delete out[id];
    for (const [id, st] of this.dirty) out[id] = st;
    return out;
  }
  write() {
    const run2 = async () => {
      if (!this.dirty.size && !this.deleted.size) return;
      const puts = new Map(this.dirty);
      const dels = new Map(this.deleted);
      await withFileLock(
        this.path,
        async () => {
          this.noteVersion(await fileFingerprint(this.path));
          const states = await this.readDisk();
          for (const id of dels.keys()) delete states[id];
          for (const [id, st] of puts) states[id] = st;
          await writeJsonFileAtomic(this.path, { version: 1, states });
          const fp = await fileFingerprint(this.path);
          this.base = states;
          this.seen = this.lastWritten = fp;
          this.loaded = true;
          this.gen++;
          for (const [id, st] of puts) if (this.dirty.get(id) === st) this.dirty.delete(id);
          for (const [id, n] of dels) if (this.deleted.get(id) === n) this.deleted.delete(id);
        },
        this.lock
      );
    };
    const next = this.writing.then(run2, run2);
    this.writing = next.catch(() => {
    });
    return next;
  }
  schedule() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = void 0;
      this.write().catch(() => this.schedule());
    }, this.debounceMs);
    this.timer.unref?.();
  }
  /** Force any debounced write to disk now. */
  async flush() {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = void 0;
    }
    await this.writing;
    await this.write();
  }
  /**
   * How many versions of the file written by another process (the CLI, a
   * running `serve`, another app) this store has read so far. A watcher can
   * compare it before and after a reload to tell another process's changes from
   * this process's own writes.
   */
  get externalWrites() {
    return this.external;
  }
  async all() {
    await this.load();
    return structuredClone(this.view());
  }
  async get(id) {
    await this.load();
    if (this.deleted.has(id) && !this.dirty.has(id)) return void 0;
    const s = this.dirty.get(id) ?? this.base[id];
    return s ? structuredClone(s) : void 0;
  }
  async put(state) {
    this.deleted.delete(state.profileId);
    this.dirty.set(state.profileId, structuredClone(state));
    this.schedule();
  }
  async delete(id) {
    this.dirty.delete(id);
    this.deleted.set(id, ++this.seq);
    this.schedule();
  }
};

// ../core/src/store/usage-store.ts
import { join as join4 } from "path";
var USAGE_MAX_AGE_MS = 14 * 24 * 60 * 6e4;
var USAGE_MAX_RECORDS = 5e3;
var empty = () => ({ requests: [], parks: [], samples: [] });
function time(iso) {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? -Infinity : t;
}
function pruneUsageHistory(h, now, maxAgeMs = USAGE_MAX_AGE_MS, maxRecords = USAGE_MAX_RECORDS) {
  const cutoff = now - maxAgeMs;
  const fresh = (list) => {
    let i = 0;
    while (i < list.length && !(time(list[i].at) >= cutoff)) i++;
    return i ? list.slice(i) : list;
  };
  h.requests = fresh(h.requests);
  h.parks = fresh(h.parks);
  h.samples = fresh(h.samples);
  let excess = h.requests.length + h.parks.length + h.samples.length - Math.max(0, maxRecords);
  if (excess > 0) {
    const lists = [h.requests, h.parks, h.samples];
    const heads = [0, 0, 0];
    while (excess > 0) {
      let pick2 = -1;
      let oldest = Infinity;
      for (let i = 0; i < lists.length; i++) {
        const rec = lists[i][heads[i]];
        if (rec && time(rec.at) < oldest) {
          oldest = time(rec.at);
          pick2 = i;
        }
      }
      if (pick2 < 0) break;
      heads[pick2]++;
      excess--;
    }
    h.requests = h.requests.slice(heads[0]);
    h.parks = h.parks.slice(heads[1]);
    h.samples = h.samples.slice(heads[2]);
  }
  return h;
}
var BoundedUsageStore = class {
  clock;
  maxAgeMs;
  maxRecords;
  constructor(opts = {}) {
    this.clock = opts.clock ?? systemClock;
    this.maxAgeMs = opts.maxAgeMs ?? USAGE_MAX_AGE_MS;
    this.maxRecords = opts.maxRecordsPerProfile ?? USAGE_MAX_RECORDS;
  }
  /**
   * Apply one change to a history map. An append also prunes: the profile to the
   * age and record limits, and every other profile by age (so idle ones age out).
   */
  apply(map, op) {
    if (op.type === "delete") {
      delete map[op.profileId];
      return;
    }
    const h = map[op.profileId] ??= empty();
    h[op.kind].push(op.record);
    const now = this.clock.now();
    pruneUsageHistory(h, now, this.maxAgeMs, this.maxRecords);
    for (const [id, other] of Object.entries(map))
      if (id !== op.profileId) pruneUsageHistory(other, now, this.maxAgeMs, Infinity);
  }
  async add(profileId, kind, record) {
    const map = await this.load();
    const op = { type: "add", profileId, kind, record: structuredClone(record) };
    this.apply(map, op);
    this.changed(op, map);
  }
  addRequest(profileId, record) {
    return this.add(profileId, "requests", record);
  }
  addPark(profileId, record) {
    return this.add(profileId, "parks", record);
  }
  addSample(profileId, record) {
    return this.add(profileId, "samples", record);
  }
  async history(profileId) {
    const h = (await this.load())[profileId];
    return h ? structuredClone(h) : empty();
  }
  async all() {
    return structuredClone(await this.load());
  }
  async delete(profileId) {
    const map = await this.load();
    if (!(profileId in map)) return;
    const op = { type: "delete", profileId };
    this.apply(map, op);
    this.changed(op, map);
  }
};
var FileUsageStore = class extends BoundedUsageStore {
  path;
  cache;
  loading;
  /** Identity of the file the cache came from. */
  seen;
  /** Changes since the last write, oldest first. */
  ops = [];
  /** Bumped by every write, so a read that started before it cannot replace its result. */
  gen = 0;
  timer;
  writing = Promise.resolve();
  debounceMs;
  lock;
  constructor(dataDir, opts = {}) {
    super(opts);
    this.path = join4(dataDir, "usage.json");
    this.debounceMs = opts.debounceMs ?? 500;
    this.lock = opts.lock ?? {};
  }
  async readDisk() {
    const file = await readJsonFile(this.path, { version: 1, profiles: {} }).catch(
      () => ({ version: 1, profiles: {} })
      // a corrupt history is dropped, never fatal
    );
    const profiles2 = {};
    for (const [id, h] of Object.entries(file?.profiles ?? {})) {
      profiles2[id] = {
        requests: Array.isArray(h?.requests) ? h.requests : [],
        parks: Array.isArray(h?.parks) ? h.parks : [],
        samples: Array.isArray(h?.samples) ? h.samples : []
      };
    }
    return profiles2;
  }
  load() {
    this.loading ??= (async () => {
      try {
        const fp = await fileFingerprint(this.path);
        if (this.cache && fp === this.seen) return this.cache;
        const gen = this.gen;
        const map = await this.readDisk();
        if (gen !== this.gen && this.cache) return this.cache;
        for (const op of this.ops) this.apply(map, op);
        this.cache = map;
        this.seen = fp;
        return map;
      } finally {
        this.loading = void 0;
      }
    })();
    return this.loading;
  }
  changed(op, map) {
    this.ops.push(op);
    if (this.cache && this.cache !== map) this.apply(this.cache, op);
    this.schedule();
  }
  schedule() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = void 0;
      this.write().catch(() => this.schedule());
    }, this.debounceMs);
    this.timer.unref?.();
  }
  write() {
    const run2 = async () => {
      const n = this.ops.length;
      if (!n) return;
      const ops = this.ops.slice(0, n);
      await withFileLock(
        this.path,
        async () => {
          const map = await this.readDisk();
          for (const op of ops) this.apply(map, op);
          await writeJsonFileAtomic(this.path, { version: 1, profiles: map });
          const fp = await fileFingerprint(this.path);
          this.ops.splice(0, n);
          for (const op of this.ops) this.apply(map, op);
          this.cache = map;
          this.seen = fp;
          this.gen++;
        },
        this.lock
      );
    };
    const next = this.writing.then(run2, run2);
    this.writing = next.catch(() => {
    });
    return next;
  }
  /** Force any debounced write to disk now. */
  async flush() {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = void 0;
    }
    await this.writing;
    await this.write();
  }
};

// ../core/src/usage/report.ts
var USAGE_WINDOW_MS = {
  "1h": 60 * 6e4,
  "5h": 5 * 60 * 6e4,
  "24h": 24 * 60 * 6e4,
  "7d": 7 * 24 * 60 * 6e4
};
var SAMPLE_WINDOW_WITHOUT_RESET_MS = 60 * 6e4;
var ESTIMATE_MIN_SAMPLES = 3;
var ESTIMATE_MEDIUM_SAMPLES = 6;
var ESTIMATE_MEDIUM_SPAN_MS = 10 * 6e4;
function time2(iso) {
  if (iso === void 0) return NaN;
  return Date.parse(iso);
}
function currentWindowSamples(samples, now) {
  const seen = samples.filter((s) => Number.isFinite(s.utilisation) && time2(s.at) <= now).sort((a, b) => time2(a.at) - time2(b.at));
  const latest = seen[seen.length - 1];
  if (!latest) return [];
  if (latest.resetAt !== void 0) {
    const reset = time2(latest.resetAt);
    if (!(reset > now)) return [];
    return seen.filter((s) => s.resetAt !== void 0 && time2(s.resetAt) === reset);
  }
  const since = now - SAMPLE_WINDOW_WITHOUT_RESET_MS;
  return seen.filter((s) => s.resetAt === void 0 && time2(s.at) >= since);
}
function estimateTimeLeft(samples, now) {
  const win = currentWindowSamples(samples, now);
  if (win.length < ESTIMATE_MIN_SAMPLES) return void 0;
  const t0 = time2(win[0].at);
  const xs = win.map((s) => (time2(s.at) - t0) / 6e4);
  const ys = win.map((s) => s.utilisation);
  const n = win.length;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let sxy = 0;
  let sxx = 0;
  for (let i = 0; i < n; i++) {
    sxy += (xs[i] - mx) * (ys[i] - my);
    sxx += (xs[i] - mx) ** 2;
  }
  if (!(sxx > 0)) return void 0;
  const slope = sxy / sxx;
  if (!(slope > 0) || !Number.isFinite(slope)) return void 0;
  const latest = win[n - 1];
  let minutes = Math.max(0, 1 - latest.utilisation) / slope;
  const reset = time2(latest.resetAt);
  if (Number.isFinite(reset)) minutes = Math.min(minutes, Math.max(0, (reset - now) / 6e4));
  const span = time2(latest.at) - t0;
  return {
    minutesLeft: Math.round(minutes),
    basis: "utilisation-trend",
    confidence: n >= ESTIMATE_MEDIUM_SAMPLES && span >= ESTIMATE_MEDIUM_SPAN_MS ? "medium" : "low"
  };
}
function buildUsageReport(profileId, history, now) {
  const windows = {};
  for (const w of USAGE_WINDOWS) {
    const since = now - USAGE_WINDOW_MS[w];
    const totals = { requests: 0, inputTokens: 0, outputTokens: 0 };
    for (const r of history.requests) {
      const t = time2(r.at);
      if (!(t >= since && t <= now)) continue;
      totals.requests++;
      totals.inputTokens += r.inputTokens ?? 0;
      totals.outputTokens += r.outputTokens ?? 0;
    }
    windows[w] = totals;
  }
  const weekAgo = now - USAGE_WINDOW_MS["7d"];
  let parks7d = 0;
  let lastParkedAt;
  for (const p of history.parks) {
    const t = time2(p.at);
    if (!(t <= now)) continue;
    if (t >= weekAgo) parks7d++;
    if (lastParkedAt === void 0 || t > time2(lastParkedAt)) lastParkedAt = p.at;
  }
  const report = { profileId, windows, parks7d };
  if (lastParkedAt !== void 0) report.lastParkedAt = lastParkedAt;
  const current = currentWindowSamples(history.samples, now);
  const latest = current[current.length - 1];
  if (latest) {
    report.utilisation = latest.utilisation;
    if (latest.resetAt !== void 0) report.resetAt = latest.resetAt;
  }
  const estimate = estimateTimeLeft(history.samples, now);
  if (estimate) report.estimate = estimate;
  return report;
}

// ../core/src/vault/vault.ts
import { createCipheriv, createDecipheriv, randomBytes as randomBytes3 } from "crypto";
import { chmod as chmod2, mkdir as mkdir4, readFile as readFile3, writeFile as writeFile2 } from "fs/promises";
import { dirname as dirname3, join as join5 } from "path";
var plainKeyProtector = {
  label: "plain",
  protect: async (k) => k,
  unprotect: async (k) => k
};
var FileVault = class {
  constructor(dataDir, protector = plainKeyProtector) {
    this.protector = protector;
    this.vaultPath = join5(dataDir, "vault.json");
    this.keyPath = join5(dataDir, "vault.key");
  }
  protector;
  vaultPath;
  keyPath;
  key;
  chain = Promise.resolve();
  serial(fn) {
    const next = this.chain.then(fn, fn);
    this.chain = next.catch(() => {
    });
    return next;
  }
  async loadKey() {
    if (this.key) return this.key;
    let file;
    try {
      file = JSON.parse(await readFile3(this.keyPath, "utf8"));
    } catch (err) {
      if (err.code !== "ENOENT") {
        throw new IronProxyError(
          "VAULT_ERROR",
          `Cannot read vault key: ${err.message}`,
          {
            cause: err,
            hint: "Check that the Iron-Proxy data directory is readable by this OS user, then retry."
          }
        );
      }
    }
    if (file) {
      if (file.protector !== this.protector.label) {
        throw new IronProxyError(
          "VAULT_ERROR",
          `Vault key was protected with "${file.protector}" but this process uses "${this.protector.label}".`,
          {
            hint: `Wrong key protector: start Iron-Proxy with the "${file.protector}" protector it was set up with (same app, same OS user), or re-enter the API keys in a fresh data directory.`
          }
        );
      }
      this.key = await this.protector.unprotect(Buffer.from(file.key, "base64"));
      return this.key;
    }
    const raw = randomBytes3(32);
    const protectedKey = await this.protector.protect(raw);
    await mkdir4(dirname3(this.keyPath), { recursive: true });
    const out = {
      version: 1,
      protector: this.protector.label,
      key: protectedKey.toString("base64")
    };
    await writeFile2(this.keyPath, JSON.stringify(out), {
      encoding: "utf8",
      mode: 384,
      flag: "wx"
    }).catch(async (err) => {
      if (err.code === "EEXIST") return;
      throw err;
    });
    try {
      await chmod2(this.keyPath, 384);
    } catch {
    }
    this.key = void 0;
    const reloaded = JSON.parse(await readFile3(this.keyPath, "utf8"));
    this.key = await this.protector.unprotect(Buffer.from(reloaded.key, "base64"));
    return this.key;
  }
  async load() {
    return readJsonFile(this.vaultPath, { version: 1, entries: {} });
  }
  encrypt(key, ref, plain) {
    const iv = randomBytes3(12);
    const cipher = createCipheriv("aes-256-gcm", key, iv);
    cipher.setAAD(Buffer.from(ref, "utf8"));
    const ct = Buffer.concat([cipher.update(plain, "utf8"), cipher.final()]);
    return Buffer.concat([iv, ct, cipher.getAuthTag()]).toString("base64");
  }
  decrypt(key, ref, packed) {
    const buf = Buffer.from(packed, "base64");
    const iv = buf.subarray(0, 12);
    const tag = buf.subarray(buf.length - 16);
    const ct = buf.subarray(12, buf.length - 16);
    const decipher = createDecipheriv("aes-256-gcm", key, iv);
    decipher.setAAD(Buffer.from(ref, "utf8"));
    decipher.setAuthTag(tag);
    return Buffer.concat([decipher.update(ct), decipher.final()]).toString("utf8");
  }
  get(ref) {
    return this.serial(async () => {
      const file = await this.load();
      const packed = file.entries[ref];
      if (!packed) return void 0;
      const key = await this.loadKey();
      try {
        return this.decrypt(key, ref, packed);
      } catch (err) {
        throw new IronProxyError(
          "VAULT_ERROR",
          `Cannot decrypt secret "${ref}". Key file changed?`,
          {
            cause: err,
            hint: "The vault key changed since this secret was saved: re-enter the API key ('Set API key' in the switcher)."
          }
        );
      }
    });
  }
  set(ref, secret) {
    return this.serial(async () => {
      const key = await this.loadKey();
      const file = await this.load();
      file.entries[ref] = this.encrypt(key, ref, secret);
      await writeJsonFileAtomic(this.vaultPath, file);
    });
  }
  delete(ref) {
    return this.serial(async () => {
      const file = await this.load();
      if (ref in file.entries) {
        delete file.entries[ref];
        await writeJsonFileAtomic(this.vaultPath, file);
      }
    });
  }
  has(ref) {
    return this.serial(async () => ref in (await this.load()).entries);
  }
};

// ../core/src/manager.ts
var PROVIDER_SHORT_NAMES = {
  anthropic: "Claude",
  openai: "Codex",
  google: "Gemini",
  xai: "Grok",
  "openai-compatible": "Custom"
};
function samePath(a, b) {
  const norm = (p) => {
    const r = resolve(p);
    return process.platform === "win32" ? r.toLowerCase() : r;
  };
  return norm(a) === norm(b);
}
function isStrictlyInside(parent, child, platform = process.platform) {
  const fold = (p) => platform === "win32" ? p.toLowerCase() : p;
  const rel = relative(fold(resolve(parent)), fold(resolve(child)));
  const escapes = rel === ".." || rel.startsWith(`..${sep}`) || rel.startsWith("../") || platform === "win32" && rel.startsWith("..\\");
  return rel !== "" && !escapes && !isAbsolute(rel);
}
async function isDirectory(path) {
  try {
    return (await stat2(path)).isDirectory();
  } catch {
    return false;
  }
}
function defaultDataDir() {
  return process.env.IRON_PROXY_DATA_DIR ?? join6(homedir2(), ".iron-proxy");
}
var IronProxy = class {
  dataDir;
  profiles;
  states;
  usage;
  vault;
  registry;
  events;
  router;
  clock;
  env;
  logins = /* @__PURE__ */ new Map();
  background = /* @__PURE__ */ new Set();
  /** Usage-history writes in flight, awaited by close() before the flush. */
  usageWrites = /* @__PURE__ */ new Set();
  usageOff = [];
  constructor(opts = {}) {
    this.dataDir = opts.dataDir ?? defaultDataDir();
    this.profiles = opts.profiles ?? new FileProfileStore(this.dataDir);
    this.states = opts.states ?? new FileStateStore(this.dataDir);
    this.vault = opts.vault ?? new FileVault(this.dataDir, opts.keyProtector);
    this.registry = opts.registry ?? createDefaultRegistry();
    this.events = new TypedEmitter();
    this.clock = opts.clock ?? systemClock;
    this.env = opts.env ?? process.env;
    this.router = new Router({
      profiles: this.profiles,
      states: this.states,
      vault: this.vault,
      registry: this.registry,
      emitter: this.events,
      clock: this.clock,
      ...opts.policy ? { policy: opts.policy } : {},
      ...opts.fetch ? { fetch: opts.fetch } : {}
    });
    this.usage = opts.usage ?? new FileUsageStore(this.dataDir, { clock: this.clock });
    this.recordUsageHistory();
  }
  /**
   * Feed the usage history from the manager's own events and the router's usage
   * hook. Only counts, kinds and timestamps are kept: no prompt text, output,
   * secrets or emails.
   */
  recordUsageHistory() {
    const track = (write) => {
      const p = write().catch(() => {
      });
      this.usageWrites.add(p);
      void p.then(() => this.usageWrites.delete(p));
    };
    const at = () => isoNow(this.clock);
    this.usageOff.push(
      this.events.on(
        "request.finished",
        (e) => track(
          () => this.usage.addRequest(e.profileId, {
            at: at(),
            durationMs: e.durationMs,
            ...e.usage?.inputTokens !== void 0 ? { inputTokens: e.usage.inputTokens } : {},
            ...e.usage?.outputTokens !== void 0 ? { outputTokens: e.usage.outputTokens } : {},
            ...e.usage?.cacheReadTokens !== void 0 ? { cacheReadTokens: e.usage.cacheReadTokens } : {}
          })
        )
      ),
      this.events.on(
        "profile.parked",
        (e) => track(
          () => this.usage.addPark(e.profileId, {
            at: at(),
            kind: e.reason.kind,
            ...e.until ? { until: e.until } : {}
          })
        )
      ),
      this.router.onUsage((profileId, usage2) => {
        const u = usage2.utilisation;
        if (typeof u !== "number" || !Number.isFinite(u)) return;
        track(
          () => this.usage.addSample(profileId, {
            at: at(),
            utilisation: u,
            ...usage2.resetAt ? { resetAt: usage2.resetAt } : {}
          })
        );
      })
    );
  }
  /* ---------------------------------------------------------------- */
  /* Profiles                                                         */
  /* ---------------------------------------------------------------- */
  async listProfiles(provider) {
    const all = await this.profiles.list();
    return (provider ? all.filter((p) => p.provider === provider) : all).sort(
      (a, b) => a.provider.localeCompare(b.provider) || a.order - b.order
    );
  }
  async getProfile(id) {
    const p = await this.profiles.get(id);
    if (!p) throw new ProfileNotFoundError(id);
    return p;
  }
  /**
   * Create a profile. For the CLI lane an isolated home directory is created
   * under `<dataDir>/cli-homes/<provider>/<id>` unless one is supplied.
   */
  createProfile(input) {
    return this.insertProfile(input, true);
  }
  async insertProfile(input, refresh) {
    if (!PROVIDER_IDS.includes(input.provider)) {
      throw new IronProxyError("INVALID_REQUEST", `Unknown provider "${input.provider}".`, {
        hint: `Use one of: ${PROVIDER_IDS.join(", ")}.`
      });
    }
    if (!input.title?.trim())
      throw new IronProxyError("INVALID_REQUEST", "A profile needs a title.", {
        hint: 'Give the account a title you will recognise, e.g. "Work Claude".'
      });
    if (input.lane === "oauth" && !input.oauth?.extension) {
      throw new IronProxyError(
        "INVALID_REQUEST",
        "An oauth profile needs oauth.extension (the registered extension name)."
      );
    }
    const adapter = this.registry.get(input.provider);
    if (!adapter.lanes[input.lane]) {
      throw new IronProxyError(
        "UNSUPPORTED",
        `Provider "${input.provider}" has no "${input.lane}" lane. Register one with registry.addLane().`
      );
    }
    const id = newId("prof");
    const siblings = await this.listProfiles(input.provider);
    const order = input.order ?? (siblings.length ? Math.max(...siblings.map((s) => s.order)) + 1 : 0);
    const now = isoNow(this.clock);
    const profile = {
      id,
      title: input.title.trim(),
      provider: input.provider,
      lane: input.lane,
      order,
      enabled: input.enabled ?? true,
      createdAt: now,
      updatedAt: now
    };
    if (input.defaultModel) profile.defaultModel = input.defaultModel;
    if (input.lane === "cli") {
      profile.cli = {
        home: join6(this.dataDir, "cli-homes", input.provider, id),
        ...input.cli ?? {}
      };
      const lane = this.registry.laneFor(profile);
      if (lane instanceof CliLane) await lane.ensureHome(profile);
    } else if (input.lane === "api-key") {
      const secretRef = input.apiKey?.secretRef || `apikey:${id}`;
      profile.apiKey = { ...input.apiKey ?? {}, secretRef };
      if (input.provider === "openai-compatible" && !profile.apiKey.baseUrl) {
        throw new IronProxyError(
          "INVALID_REQUEST",
          "An openai-compatible profile needs apiKey.baseUrl.",
          { hint: "Pass the server's /v1 URL, e.g. --base-url http://127.0.0.1:11434/v1." }
        );
      }
      if (input.apiKeySecret) await this.vault.set(secretRef, input.apiKeySecret);
    } else if (input.lane === "oauth") {
      if (!input.oauth?.extension)
        throw new IronProxyError("INVALID_REQUEST", "An oauth profile needs oauth.extension.");
      profile.oauth = {
        secretRef: input.oauth.secretRef ?? `oauth:${id}`,
        extension: input.oauth.extension
      };
    }
    await this.profiles.put(profile);
    await this.states.put({ profileId: id, status: "unknown", served: 0 });
    this.events.emit({ type: "profile.created", profile });
    if (refresh) this.inBackground(this.refreshStatus(id));
    return profile;
  }
  async updateProfile(id, patch) {
    const p = await this.getProfile(id);
    if (patch.title !== void 0) {
      if (!patch.title.trim())
        throw new IronProxyError("INVALID_REQUEST", "A profile needs a title.");
      p.title = patch.title.trim();
    }
    if (patch.order !== void 0) p.order = patch.order;
    if (patch.enabled !== void 0) p.enabled = patch.enabled;
    if (patch.defaultModel !== void 0) {
      if (patch.defaultModel) p.defaultModel = patch.defaultModel;
      else delete p.defaultModel;
    }
    if (patch.cli && p.cli) {
      const { adopted: _adopted, home, ...rest } = patch.cli;
      const newHome = home !== void 0 && !p.cli.adopted ? home : void 0;
      if (newHome !== void 0) {
        const owner = (await this.profiles.list()).find(
          (o) => o.id !== id && !!o.cli?.home && samePath(o.cli.home, newHome)
        );
        if (owner)
          throw new IronProxyError(
            "INVALID_REQUEST",
            `Profile "${owner.title}" already uses "${resolve(newHome)}".`,
            {
              details: { home: resolve(newHome), profileId: owner.id },
              hint: `Pick another directory; two profiles on one home would fail over onto the same account (${owner.id} uses it).`
            }
          );
      }
      p.cli = { ...p.cli, ...rest, ...newHome !== void 0 ? { home: newHome } : {} };
    }
    if (patch.apiKey && p.apiKey) p.apiKey = { ...p.apiKey, ...patch.apiKey };
    if (patch.oauth && p.oauth) p.oauth = { ...p.oauth, ...patch.oauth };
    p.updatedAt = isoNow(this.clock);
    await this.profiles.put(p);
    this.events.emit({ type: "profile.updated", profile: p });
    if (patch.enabled !== void 0) {
      const st = await this.router.state(id);
      st.status = patch.enabled ? "unknown" : "disabled";
      await this.states.put(st);
      this.events.emit({ type: "profile.state", state: st });
      if (patch.enabled) this.inBackground(this.refreshStatus(id));
    }
    return p;
  }
  /**
   * Delete the profile, its secrets and (for CLI profiles) its isolated home
   * directory. Only directories Iron-Proxy created under `<dataDir>/cli-homes`
   * are removed; an adopted home is never touched, and neither is a home that
   * another profile also uses or that holds another profile's home.
   */
  async deleteProfile(id, opts = {}) {
    const p = await this.getProfile(id);
    this.logins.get(id)?.cancel();
    if (p.apiKey?.secretRef) await this.vault.delete(p.apiKey.secretRef).catch(() => {
    });
    if (p.oauth?.secretRef) await this.vault.delete(p.oauth.secretRef).catch(() => {
    });
    const home = p.cli?.home;
    if (home && !p.cli?.adopted && !opts.keepFiles && isStrictlyInside(join6(this.dataDir, "cli-homes"), home)) {
      const shared = (await this.profiles.list()).some(
        (o) => o.id !== id && !!o.cli?.home && (samePath(o.cli.home, home) || isStrictlyInside(home, o.cli.home))
      );
      if (!shared) await rm2(home, { recursive: true, force: true }).catch(() => {
      });
    }
    await this.profiles.delete(id);
    await this.states.delete(id);
    await this.usage.delete(id).catch(() => {
    });
    this.events.emit({ type: "profile.deleted", profileId: id });
  }
  /** Reorder a provider's profiles. `ids` is the full new order; missing ones keep relative position after. */
  async reorder(provider, ids) {
    const current = await this.listProfiles(provider);
    const byId = new Map(current.map((p) => [p.id, p]));
    const ordered = [
      ...ids.map((id) => byId.get(id)).filter((p) => !!p),
      ...current.filter((p) => !ids.includes(p.id))
    ];
    for (let i = 0; i < ordered.length; i++) {
      const p = ordered[i];
      if (p.order !== i) {
        p.order = i;
        p.updatedAt = isoNow(this.clock);
        await this.profiles.put(p);
        this.events.emit({ type: "profile.updated", profile: p });
      }
    }
    return ordered;
  }
  /** Make this profile the primary for its provider (order 0) and clear any park on it. */
  async activate(id) {
    const p = await this.getProfile(id);
    const rest = (await this.listProfiles(p.provider)).filter((x) => x.id !== id).map((x) => x.id);
    await this.reorder(p.provider, [id, ...rest]);
    await this.router.unpark(id);
    return this.getProfile(id);
  }
  async setApiKey(id, secret) {
    const p = await this.getProfile(id);
    if (p.lane !== "api-key" || !p.apiKey)
      throw new IronProxyError("INVALID_REQUEST", "Profile is not an API-key profile.", {
        hint: "Only API-key accounts take a key; log subscription accounts in with iron-proxy login <id>."
      });
    await this.vault.set(p.apiKey.secretRef, secret);
    const st = await this.router.state(id);
    st.status = "ready";
    delete st.lastError;
    await this.states.put(st);
    this.events.emit({ type: "profile.state", state: st });
  }
  /* ---------------------------------------------------------------- */
  /* Existing logins                                                  */
  /* ---------------------------------------------------------------- */
  /**
   * Vendor CLIs already signed in at their default home on this machine. Runs
   * each CLI's own status command against that home; never reads credential
   * files, never persists anything and never reports an email.
   */
  async discoverLogins() {
    const profiles2 = await this.profiles.list();
    const probed = await Promise.all(
      this.registry.list().map(async (adapter) => {
        const lane = adapter.lanes.cli;
        if (!(lane instanceof CliLane)) return void 0;
        const found = lane.spec.defaultHome?.(this.env);
        if (!found) return void 0;
        const home = resolve(found);
        if (!await isDirectory(home)) return void 0;
        const probe = {
          id: `discover-${adapter.id}`,
          title: lane.spec.displayName,
          provider: adapter.id,
          lane: "cli",
          order: 0,
          enabled: true,
          cli: { home, adopted: true },
          createdAt: "",
          updatedAt: ""
        };
        const installed = !!await lane.findBinary(probe);
        const status2 = installed ? await lane.checkAuth(probe).catch(() => "unknown") : "unknown";
        return { provider: adapter.id, binary: lane.spec.binary, home, installed, status: status2 };
      })
    );
    const taken = new Set(profiles2.map((p) => p.title));
    const out = [];
    for (const f of probed) {
      if (!f) continue;
      const owner = profiles2.find((p) => !!p.cli?.home && samePath(p.cli.home, f.home));
      const suggestedTitle = freeTitle(
        `${PROVIDER_SHORT_NAMES[f.provider]} (existing login)`,
        taken
      );
      taken.add(suggestedTitle);
      out.push({ ...f, ...owner ? { adoptedProfileId: owner.id } : {}, suggestedTitle });
    }
    return out;
  }
  /**
   * The default home a provider's vendor CLI uses on this machine (resolved, not
   * checked for existence), without running anything. Undefined when the
   * provider has no vendor CLI or the CLI has no default home.
   */
  defaultCliHome(provider) {
    if (!PROVIDER_IDS.includes(provider)) return void 0;
    const lane = this.registry.get(provider).lanes.cli;
    if (!(lane instanceof CliLane)) return void 0;
    const found = lane.spec.defaultHome?.(this.env);
    return found ? resolve(found) : void 0;
  }
  /**
   * Turn an existing CLI login into a profile, as-is: the profile's home is that
   * directory, nothing is copied, and deleting the profile never removes it.
   */
  async adoptLogin(input) {
    if (!input || !PROVIDER_IDS.includes(input.provider))
      throw new IronProxyError("INVALID_REQUEST", `Unknown provider "${input?.provider}".`, {
        hint: `Use one of: ${PROVIDER_IDS.join(", ")}.`
      });
    const lane = this.registry.get(input.provider).lanes.cli;
    if (!(lane instanceof CliLane))
      throw new IronProxyError(
        "UNSUPPORTED",
        `Provider "${input.provider}" has no vendor CLI to adopt a login from.`,
        { hint: "Adopt a Claude, Codex or Grok login, or add this provider with an API key." }
      );
    if (typeof input.home !== "string" || !input.home.trim())
      throw new IronProxyError("INVALID_REQUEST", "Adopting a login needs its home directory.", {
        hint: "Run iron-proxy profiles discover to see the logins found on this computer."
      });
    const home = resolve(input.home.trim());
    if (!await isDirectory(home))
      throw new IronProxyError("INVALID_REQUEST", `No CLI home directory at "${home}".`, {
        details: { home },
        hint: `Sign in with ${lane.spec.displayName} itself first, or pass the directory it uses with --home.`
      });
    const existing = await this.profiles.list();
    const owner = existing.find((p) => !!p.cli?.home && samePath(p.cli.home, home));
    if (owner)
      throw new IronProxyError(
        "INVALID_REQUEST",
        `Profile "${owner.title}" already uses "${home}".`,
        {
          details: { home, profileId: owner.id },
          hint: `Use the existing profile ${owner.id}; two profiles on one login would fail over onto the same account.`
        }
      );
    const title = input.title?.trim() || freeTitle(
      `${PROVIDER_SHORT_NAMES[input.provider]} (existing login)`,
      new Set(existing.map((p) => p.title))
    );
    const profile = await this.insertProfile(
      { title, provider: input.provider, lane: "cli", cli: { home, adopted: true } },
      false
    );
    await this.refreshStatus(profile.id).catch(() => []);
    return profile;
  }
  /* ---------------------------------------------------------------- */
  /* Auth                                                             */
  /* ---------------------------------------------------------------- */
  /** Start a login. Progress arrives as `login` events and on the returned session. */
  login(id, opts = {}) {
    return (async () => {
      const p = await this.getProfile(id);
      const lane = this.registry.laneFor(p);
      if (!lane.login)
        throw new IronProxyError(
          "UNSUPPORTED",
          `The ${p.lane} lane for ${p.provider} has no interactive login. Set an API key instead.`
        );
      this.logins.get(id)?.cancel();
      const session = lane.login(p, this.vault, opts);
      this.logins.set(id, session);
      session.on((event) => this.events.emit({ type: "login", event }));
      session.done.then(async () => {
        const st = await this.router.state(id);
        st.status = "ready";
        delete st.lastError;
        delete st.parkedReason;
        await this.states.put(st);
        this.events.emit({ type: "profile.state", state: st });
      }).catch(() => {
      }).finally(() => {
        if (this.logins.get(id) === session) this.logins.delete(id);
      });
      return session;
    })();
  }
  /** The terminal command a host can run itself when headless login is not possible. */
  async loginCommand(id) {
    const p = await this.getProfile(id);
    const lane = this.registry.laneFor(p);
    if (!(lane instanceof CliLane))
      throw new IronProxyError("UNSUPPORTED", "Only CLI profiles have a login command.");
    const cmd = lane.loginCommand(p, false);
    return { ...cmd, requiresTerminal: lane.spec.login.requiresTerminal ?? false };
  }
  async logout(id) {
    const p = await this.getProfile(id);
    this.logins.get(id)?.cancel();
    const lane = this.registry.laneFor(p);
    await lane.logout?.(p, this.vault);
    const st = await this.router.state(id);
    st.status = "unauthenticated";
    await this.states.put(st);
    this.events.emit({ type: "profile.state", state: st });
  }
  /** Re-check authentication for one or all profiles. */
  async refreshStatus(id) {
    const targets = id ? [await this.getProfile(id)] : await this.listProfiles();
    const out = [];
    await Promise.all(
      targets.map(async (p) => {
        let st = await this.router.state(p.id);
        if (!p.enabled) {
          st.status = "disabled";
        } else if (st.status !== "parked" && st.status !== "active") {
          const lane = this.registry.laneFor(p);
          const result = await lane.checkAuth(p, this.vault).catch(() => "unknown");
          st = await this.router.state(p.id);
          if (st.status !== "parked" && st.status !== "active") {
            st.status = result === "unauthenticated" ? "unauthenticated" : result === "ok" ? "ready" : st.status === "unknown" ? "ready" : st.status;
          }
        }
        await this.states.put(st);
        this.events.emit({ type: "profile.state", state: st });
        out.push(st);
      })
    );
    return out;
  }
  /* ---------------------------------------------------------------- */
  /* Running requests                                                 */
  /* ---------------------------------------------------------------- */
  complete(req, opts) {
    return this.router.complete(req, opts);
  }
  stream(req, opts) {
    return this.router.stream(req, opts);
  }
  /* ---------------------------------------------------------------- */
  /* Picking an account for the user's own terminal                   */
  /* ---------------------------------------------------------------- */
  /**
   * The account a request for `provider` would try first right now: enabled,
   * on `lane` (default `cli`; `any` for every lane), not parked (a park whose
   * cooldown has passed is cleared, exactly as the router does), not signed out,
   * lowest order. With `profileId`, that profile, if it is an enabled account of
   * that provider on that lane.
   *
   * Throws NoProfileError when there is no such account, AllProfilesExhaustedError
   * (with the earliest reset) when the rest are parked, and AuthRequiredError when
   * every one needs to sign in again.
   */
  async pickProfile(provider, opts = {}) {
    if (!PROVIDER_IDS.includes(provider))
      throw new IronProxyError("INVALID_REQUEST", `Unknown provider "${provider}".`, {
        hint: `Use one of: ${PROVIDER_IDS.join(", ")}.`
      });
    const lane = opts.lane ?? "cli";
    const onLane = (p) => lane === "any" || p.lane === lane;
    if (opts.profileId) {
      const p = await this.getProfile(opts.profileId);
      if (p.provider !== provider || !onLane(p))
        throw new IronProxyError(
          "INVALID_REQUEST",
          `Profile "${p.title}" is a ${p.provider} ${p.lane} account, not a ${provider}${lane === "any" ? "" : ` ${lane}`} one.`,
          {
            details: { profileId: p.id, provider: p.provider, lane: p.lane },
            hint: `Pick one of the ${provider} accounts that iron-proxy profiles list shows${lane === "any" ? "" : ` with lane ${lane}`}.`
          }
        );
      if (!p.enabled)
        throw new IronProxyError("INVALID_REQUEST", `Profile "${p.title}" is disabled.`, {
          details: { profileId: p.id },
          hint: `Enable it first: iron-proxy profiles enable ${p.id}.`
        });
      await this.router.availability(p, { profileId: p.id });
      return p;
    }
    const candidates = (await this.router.candidates(provider)).filter(onLane);
    if (!candidates.length) throw new NoProfileError(provider);
    let earliest;
    let parked = false;
    let signedOut;
    for (const p of candidates) {
      const { usable, state } = await this.router.availability(p);
      if (usable) return p;
      if (state.status === "parked") {
        parked = true;
        const until = state.parkedUntil;
        if (until && (!earliest || until < earliest)) earliest = until;
      } else if (state.status === "unauthenticated") signedOut ??= p;
    }
    if (parked)
      throw new AllProfilesExhaustedError(
        provider,
        earliest,
        candidates.map((p) => p.id)
      );
    if (signedOut)
      throw new AuthRequiredError(
        signedOut.id,
        `Every ${provider} account needs to log in again.`,
        { title: signedOut.title, lane: signedOut.lane }
      );
    throw new NoProfileError(provider);
  }
  /**
   * The command that starts this CLI profile's vendor CLI interactively, `args`
   * appended, with the lane's scrubbed environment. Creates an isolated home that
   * is missing; never touches an adopted one.
   */
  async interactiveCommand(id, args = []) {
    const p = await this.getProfile(id);
    const lane = this.cliLaneOf(p);
    await lane.ensureHome(p);
    return lane.interactiveCommand(p, args);
  }
  /** The variables a user's own shell sets and clears to run this CLI profile's vendor CLI. */
  async shellEnv(id) {
    const p = await this.getProfile(id);
    return this.cliLaneOf(p).shellEnv(p);
  }
  cliLaneOf(p) {
    const lane = p.lane === "cli" ? this.registry.laneFor(p) : void 0;
    if (!(lane instanceof CliLane))
      throw new IronProxyError("UNSUPPORTED", `Profile "${p.title}" is not a vendor CLI account.`, {
        details: { profileId: p.id, lane: p.lane },
        hint: "Pick a cli-lane account; API-key accounts are used through iron-proxy chat or the proxy."
      });
    return lane;
  }
  /* ---------------------------------------------------------------- */
  /* External executors                                               */
  /* ---------------------------------------------------------------- */
  /**
   * An external executor (a host that ran the vendor CLI as this account itself,
   * after `pickProfile`) reports what it saw. Iron-Proxy classifies it with its own
   * detectors: `detectFromHttp` when `status` is given, otherwise
   * `detectFromCliOutput` on `text`. A quota signal parks the profile through the
   * router's own park path (same `profile.parked` event, same usage park record,
   * same state; `auth-expired` leaves it `unauthenticated`, the "needs sign-in"
   * state). Anything else (a model-access 403, a plain error) parks nothing.
   */
  async reportSignal(id, input) {
    const p = await this.getProfile(id);
    const { status: status2, headers, text } = validateSignalInput(input);
    const now = this.clock.now();
    const found = status2 !== void 0 ? detectFromHttp(p.provider, status2, headersFrom(headers ?? {}), text, now) : detectFromCliOutput(p.provider, text ?? "", now);
    if (!found) return { parked: false };
    const signal = found.message !== void 0 ? { ...found, message: redactSecrets(found.message) } : found;
    await this.router.park(p, signal);
    return { parked: true, signal, state: await this.router.state(id) };
  }
  /**
   * An external executor ran a request on this account and it succeeded. Recorded
   * exactly like a request the router served: the profile becomes its provider's
   * `active`, `served` increments, `request.finished` is emitted and a usage record
   * is appended (time, duration, token counts; never text).
   */
  async reportFinished(id, input = {}) {
    const p = await this.getProfile(id);
    const { usage: usage2, durationMs, model } = validateFinishedInput(input);
    return this.router.recordServed(p, {
      ...usage2 ? { usage: usage2 } : {},
      ...durationMs !== void 0 ? { durationMs } : {},
      ...model ? { model } : {}
    });
  }
  /* ---------------------------------------------------------------- */
  /* State + diagnostics                                              */
  /* ---------------------------------------------------------------- */
  async allStates() {
    const states = await this.states.all();
    for (const p of await this.profiles.list())
      states[p.id] ??= { profileId: p.id, status: "unknown", served: 0 };
    return states;
  }
  unpark(id) {
    return this.router.unpark(id);
  }
  /**
   * Requests and tokens per profile over the last 1h / 5h / 24h / 7d, parks this
   * week, the latest utilisation of the current window and, when at least three
   * samples of that window show a rising trend, an estimate of the minutes left
   * at this pace. Reads local history only; never calls a provider.
   */
  async usageReport(opts = {}) {
    const targets = opts.profileId ? [await this.getProfile(opts.profileId)] : await this.listProfiles();
    await this.router.settleUsage();
    await Promise.all([...this.usageWrites]);
    const now = this.clock.now();
    return Promise.all(
      targets.map(async (p) => buildUsageReport(p.id, await this.usage.history(p.id), now))
    );
  }
  activeProfileId(provider) {
    return this.router.activeProfileId(provider);
  }
  async listModels(id) {
    const p = await this.getProfile(id);
    const lane = this.registry.laneFor(p);
    if (!lane.listModels) return [];
    const controller = new AbortController();
    return lane.listModels(p, {
      profile: p,
      vault: this.vault,
      fetch: (input, init) => fetch(input, init),
      signal: controller.signal,
      now: () => this.clock.now(),
      reportUsage: () => {
      }
    });
  }
  /** Which vendor CLIs are installed on this machine. */
  async doctor() {
    return Promise.all(
      CLI_SPECS.map(async (spec) => {
        const path = await which(spec.binary);
        const probe = {
          provider: spec.provider,
          binary: spec.binary,
          found: !!path,
          homeEnv: spec.homeEnv
        };
        if (path) {
          probe.path = path;
          const r = await run({
            binary: path,
            args: ["--version"],
            env: { ...process.env },
            timeoutMs: 15e3
          }).catch(() => void 0);
          const v = r?.stdout.trim().split("\n")[0];
          if (v) probe.version = v;
        }
        return probe;
      })
    );
  }
  /**
   * Cancel logins, wait for usage still being stored, then flush debounced state
   * and the usage store (`UsageStore.flush`, when it has one). Call on app quit.
   */
  /**
   * Run work the caller does not wait for (a status check after creating or
   * enabling an account), but keep hold of it so close() can wait: otherwise a
   * check that finishes after close() would still write state.json.
   */
  inBackground(work) {
    const p = work.then(
      () => {
      },
      () => {
      }
    );
    this.background.add(p);
    void p.finally(() => this.background.delete(p));
  }
  async close() {
    for (const s of this.logins.values()) s.cancel();
    this.logins.clear();
    while (this.background.size) await Promise.all([...this.background]);
    await this.router.settleUsage();
    for (const off of this.usageOff.splice(0)) off();
    await Promise.all([...this.usageWrites]);
    await this.usage.flush?.().catch(() => {
    });
    if (this.states instanceof FileStateStore) await this.states.flush();
    this.events.removeAll();
  }
};
function badInput(message) {
  return new IronProxyError("INVALID_REQUEST", message, {
    hint: "Check the body against the external-executor API in docs/ADOPTING.md."
  });
}
var isCount = (v) => Number.isInteger(v) && v >= 0;
function validateSignalInput(input) {
  if (!input || typeof input !== "object" || Array.isArray(input))
    throw badInput("The signal must be an object: { status?, headers?, text? }.");
  const { status: status2, headers, text } = input;
  if (status2 !== void 0 && (!Number.isInteger(status2) || status2 < 100 || status2 > 599))
    throw badInput("`status` must be an HTTP status code (100-599).");
  if (text !== void 0 && typeof text !== "string") throw badInput("`text` must be a string.");
  if (headers !== void 0 && (!headers || typeof headers !== "object" || Array.isArray(headers) || Object.values(headers).some((v) => typeof v !== "string")))
    throw badInput("`headers` must be an object of string values.");
  if (status2 === void 0 && !text?.trim())
    throw badInput("A signal needs `status` (an HTTP failure) or `text` (what the CLI printed).");
  return {
    ...status2 !== void 0 ? { status: status2 } : {},
    ...headers !== void 0 ? { headers } : {},
    ...text !== void 0 ? { text } : {}
  };
}
function validateFinishedInput(input) {
  if (!input || typeof input !== "object" || Array.isArray(input))
    throw badInput("The report must be an object: { usage?, durationMs?, model? }.");
  const { usage: usage2, durationMs, model } = input;
  if (usage2 !== void 0) {
    if (!usage2 || typeof usage2 !== "object" || !isCount(usage2.inputTokens) || !isCount(usage2.outputTokens) || usage2.cacheReadTokens !== void 0 && !isCount(usage2.cacheReadTokens))
      throw badInput(
        "`usage` needs non-negative integer `inputTokens` and `outputTokens` (and optional `cacheReadTokens`)."
      );
  }
  if (durationMs !== void 0 && !isCount(durationMs))
    throw badInput("`durationMs` must be a non-negative integer.");
  if (model !== void 0 && typeof model !== "string") throw badInput("`model` must be a string.");
  return {
    ...usage2 ? {
      usage: {
        inputTokens: usage2.inputTokens,
        outputTokens: usage2.outputTokens,
        ...usage2.cacheReadTokens !== void 0 ? { cacheReadTokens: usage2.cacheReadTokens } : {}
      }
    } : {},
    ...durationMs !== void 0 ? { durationMs } : {},
    ...model ? { model } : {}
  };
}
function freeTitle(base, taken) {
  if (!taken.has(base)) return base;
  for (let n = 2; ; n++) {
    const t = `${base} ${n}`;
    if (!taken.has(t)) return t;
  }
}
function createIronProxy(opts = {}) {
  return new IronProxy(opts);
}

// ../core/src/client.ts
var LocalIronClient = class {
  constructor(iron) {
    this.iron = iron;
  }
  iron;
  sessions = /* @__PURE__ */ new Map();
  async providers() {
    return this.iron.registry.list().map((a) => ({
      id: a.id,
      displayName: a.displayName,
      defaultModel: a.defaultModel,
      lanes: Object.keys(a.lanes)
    }));
  }
  listProfiles() {
    return this.iron.listProfiles();
  }
  states() {
    return this.iron.allStates();
  }
  createProfile(input) {
    return this.iron.createProfile(input);
  }
  updateProfile(id, patch) {
    return this.iron.updateProfile(id, patch);
  }
  deleteProfile(id) {
    return this.iron.deleteProfile(id);
  }
  reorder(provider, ids) {
    return this.iron.reorder(provider, ids);
  }
  activate(id) {
    return this.iron.activate(id);
  }
  setApiKey(id, secret) {
    return this.iron.setApiKey(id, secret);
  }
  async login(id) {
    const session = await this.iron.login(id);
    this.sessions.set(id, session);
    try {
      await session.done;
    } finally {
      this.sessions.delete(id);
    }
  }
  async cancelLogin(id) {
    this.sessions.get(id)?.cancel();
  }
  loginCommand(id) {
    return this.iron.loginCommand(id);
  }
  logout(id) {
    return this.iron.logout(id);
  }
  refreshStatus(id) {
    return this.iron.refreshStatus(id);
  }
  unpark(id) {
    return this.iron.unpark(id);
  }
  listModels(id) {
    return this.iron.listModels(id);
  }
  doctor() {
    return this.iron.doctor();
  }
  discoverLogins() {
    return this.iron.discoverLogins();
  }
  adoptLogin(input) {
    return this.iron.adoptLogin(input);
  }
  usageReport(profileId) {
    return this.iron.usageReport(profileId ? { profileId } : {});
  }
  onEvent(listener) {
    return this.iron.events.onAny(listener);
  }
};

// ../proxy/src/server.ts
import { randomBytes as randomBytes4, timingSafeEqual } from "crypto";
import { createServer } from "http";
var PROXY_VERSION = true ? "0.1.0" : "0.0.0-dev";
var PROXY_FEATURES = ["executor-v1"];
var LANES = ["cli", "api-key", "oauth", "any"];
var HttpError = class extends Error {
  constructor(status2, message, code2 = "INVALID_REQUEST", extra = {}) {
    super(message);
    this.status = status2;
    this.code = code2;
    this.extra = extra;
  }
  status;
  code;
  extra;
};
var STATUS_BY_CODE = {
  NO_PROFILE: 404,
  PROFILE_NOT_FOUND: 404,
  AUTH_REQUIRED: 401,
  ALL_PROFILES_EXHAUSTED: 429,
  QUOTA_EXCEEDED: 429,
  INVALID_REQUEST: 400,
  UNSUPPORTED: 400,
  TIMEOUT: 504,
  ABORTED: 499,
  STREAM_INTERRUPTED: 502
};
function statusForCode(code2) {
  return STATUS_BY_CODE[code2] ?? 502;
}
function retryAfterSeconds(details) {
  const at = details?.earliestResetAt;
  if (typeof at !== "string") return void 0;
  const ms = new Date(at).getTime() - Date.now();
  if (Number.isNaN(ms)) return void 0;
  return Math.max(1, Math.ceil(ms / 1e3));
}
function errorBody(dialect, err) {
  const iron = {
    code: err.code,
    retryable: err.retryable,
    details: err.details ?? {},
    ...err.hint ? { hint: err.hint } : {}
  };
  if (dialect === "openai") {
    return {
      error: {
        message: err.message,
        type: err.code.toLowerCase(),
        code: err.code.toLowerCase(),
        param: null
      },
      iron
    };
  }
  if (dialect === "anthropic") {
    return { type: "error", error: { type: err.code.toLowerCase(), message: err.message }, iron };
  }
  return { error: { message: err.message, code: err.code }, iron };
}
function createProxyServer(opts) {
  const iron = opts.iron;
  const client = new LocalIronClient(iron);
  const host = opts.host ?? "127.0.0.1";
  const token = opts.token ?? randomBytes4(24).toString("base64url");
  const tokenBuf = Buffer.from(token);
  const maxBody = opts.maxBodyBytes ?? 20 * 1024 * 1024;
  const heartbeatMs = opts.heartbeatMs ?? 15e3;
  const logins = /* @__PURE__ */ new Map();
  const sockets = /* @__PURE__ */ new Set();
  function corsHeaders(req) {
    if (!opts.cors) return {};
    const origin = opts.cors === true ? req.headers.origin ?? "*" : opts.cors;
    return {
      "access-control-allow-origin": origin,
      "access-control-allow-headers": "authorization, content-type, x-iron-token, x-iron-provider, x-iron-profile, x-iron-resume, anthropic-version, x-api-key",
      "access-control-allow-methods": "GET, POST, PATCH, DELETE, OPTIONS",
      "access-control-expose-headers": "x-iron-profile, retry-after",
      ...opts.cors === true ? { vary: "origin" } : {}
    };
  }
  function presentedToken(req) {
    const auth = req.headers.authorization;
    if (auth?.toLowerCase().startsWith("bearer ")) return auth.slice(7).trim();
    const x = req.headers["x-iron-token"];
    if (typeof x === "string") return x.trim();
    return void 0;
  }
  function tokenOk(presented) {
    if (presented === void 0) return false;
    const b = Buffer.from(presented);
    return b.length === tokenBuf.length && timingSafeEqual(b, tokenBuf);
  }
  function authorize(req, required) {
    const presented = presentedToken(req);
    if (presented === void 0) {
      if (required) throw new HttpError(401, "Missing bearer token.", "AUTH_REQUIRED");
      return;
    }
    if (!tokenOk(presented)) {
      if (required) throw new HttpError(401, "Invalid token.", "AUTH_REQUIRED");
    }
  }
  function readBody(req) {
    return new Promise((resolve2, reject) => {
      const chunks = [];
      let size = 0;
      req.on("data", (c) => {
        size += c.length;
        if (size > maxBody) {
          reject(new HttpError(413, `Request body exceeds ${maxBody} bytes.`, "INVALID_REQUEST"));
          req.destroy();
          return;
        }
        chunks.push(c);
      });
      req.on("end", () => resolve2(Buffer.concat(chunks).toString("utf8")));
      req.on("error", reject);
    });
  }
  async function readJson(req) {
    const text = await readBody(req);
    if (!text.trim()) return {};
    try {
      return JSON.parse(text);
    } catch {
      throw new HttpError(400, "Body is not valid JSON.");
    }
  }
  function send(res, status2, body, headers = {}) {
    const text = JSON.stringify(body);
    res.writeHead(status2, {
      "content-type": "application/json; charset=utf-8",
      "content-length": Buffer.byteLength(text),
      ...headers
    });
    res.end(text);
  }
  function sendError(res, req, dialect, err) {
    let status2;
    let ser;
    if (err instanceof HttpError) {
      status2 = err.status;
      ser = {
        name: "HttpError",
        message: err.message,
        code: err.code,
        retryable: false,
        details: err.extra
      };
    } else if (err instanceof IronProxyError) {
      ser = err.toJSON();
      status2 = statusForCode(err.code);
    } else if (isSerialized(err)) {
      ser = err;
      status2 = statusForCode(err.code);
    } else {
      ser = serializeError(err);
      status2 = statusForCode(ser.code);
    }
    const headers = { ...corsHeaders(req) };
    if (status2 === 429) {
      const ra = retryAfterSeconds(ser.details);
      if (ra !== void 0) headers["retry-after"] = String(ra);
    }
    if (res.headersSent) {
      res.end();
      return;
    }
    send(res, status2, errorBody(dialect, ser), headers);
  }
  function runOptions(req, body) {
    const out = {};
    const provider = req.headers["x-iron-provider"];
    if (typeof provider === "string" && provider) {
      if (!PROVIDER_IDS.includes(provider)) {
        throw new HttpError(
          400,
          `Unknown provider "${provider}". Known: ${PROVIDER_IDS.join(", ")}.`
        );
      }
      out.provider = provider;
    }
    const profile = req.headers["x-iron-profile"];
    if (typeof profile === "string" && profile) out.profileId = profile;
    const resume = req.headers["x-iron-resume"];
    if (typeof resume === "string" && resume.trim()) {
      const v = resume.trim().toLowerCase();
      if (v === "1" || v === "true" || v === "yes") out.resumeInterrupted = true;
      else if (v === "0" || v === "false" || v === "no") out.resumeInterrupted = false;
      else throw new HttpError(400, `x-iron-resume must be 1 or 0, not "${resume}".`);
    }
    if (!out.provider && !out.profileId && typeof body.model === "string") {
      const inferred = inferProvider(body.model);
      if (inferred) out.provider = inferred;
    }
    return out;
  }
  async function startStream(gen) {
    const first = [];
    for (; ; ) {
      const r = await gen.next();
      if (r.done) return { first, gen };
      const ev = r.value;
      first.push(ev);
      if (ev.type === "error" && first.length === 1) throw ev.error;
      if (ev.type === "start" || ev.type === "error") return { first, gen };
    }
  }
  const sseHeaders = (req) => ({
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-cache, no-transform",
    connection: "keep-alive",
    "x-accel-buffering": "no",
    ...corsHeaders(req)
  });
  async function chatCompletions(req, res) {
    authorize(req, opts.requireAuthForModels ?? false);
    const body = await readJson(req);
    if (!Array.isArray(body.messages)) throw new HttpError(400, "`messages` must be an array.");
    const unified = openai_exports.fromOpenAIRequest(body);
    const ro = runOptions(req, body);
    const created = Math.floor(Date.now() / 1e3);
    if (!body.stream) {
      const result = await iron.complete(unified, ro);
      send(res, 200, openai_exports.toOpenAIResponse(result), {
        "x-iron-profile": result.profileId,
        ...corsHeaders(req)
      });
      return;
    }
    const { first, gen } = await startStream(iron.stream(unified, ro));
    let id = `chatcmpl_${randomBytes4(8).toString("hex")}`;
    let model = body.model ?? "";
    res.writeHead(200, sseHeaders(req));
    const write = (ev) => {
      if (ev.type === "switched") {
        res.write(switchedComment(ev));
        return;
      }
      if (ev.type === "start") {
        id = ev.id || id;
        model = ev.model || model;
        res.write(`: iron profile ${ev.profileId}

`);
      }
      if (ev.type === "error") {
        res.write(sseFrame(errorBody("openai", ev.error)));
        return;
      }
      const chunk = openai_exports.toOpenAIChunk(ev, id, model, created);
      if (chunk) res.write(sseFrame(chunk));
    };
    try {
      for (const ev of first) write(ev);
      for await (const ev of gen) write(ev);
    } finally {
      res.write("data: [DONE]\n\n");
      res.end();
    }
  }
  async function messages(req, res) {
    authorize(req, opts.requireAuthForModels ?? false);
    const body = await readJson(req);
    if (!Array.isArray(body.messages)) throw new HttpError(400, "`messages` must be an array.");
    if (typeof body.model !== "string" || !body.model)
      throw new HttpError(400, "`model` is required.");
    if (typeof body.max_tokens !== "number") throw new HttpError(400, "`max_tokens` is required.");
    const unified = anthropic_exports.fromAnthropicRequest(body);
    const ro = runOptions(req, body);
    if (!body.stream) {
      const result = await iron.complete(unified, ro);
      send(res, 200, anthropic_exports.toAnthropicResponse(result), {
        "x-iron-profile": result.profileId,
        ...corsHeaders(req)
      });
      return;
    }
    const { first, gen } = await startStream(iron.stream(unified, ro));
    const tr = new anthropic_exports.ToAnthropicStream();
    let id = `msg_${randomBytes4(8).toString("hex")}`;
    let model = body.model;
    res.writeHead(200, sseHeaders(req));
    const write = (ev) => {
      if (ev.type === "switched") {
        res.write(switchedComment(ev));
        return;
      }
      if (ev.type === "start") {
        id = ev.id || id;
        model = ev.model || model;
        res.write(`: iron profile ${ev.profileId}

`);
      }
      for (const a of tr.translate(ev, id, model)) res.write(sseFrame(a, a.type));
    };
    try {
      for (const ev of first) write(ev);
      for await (const ev of gen) write(ev);
    } finally {
      res.end();
    }
  }
  async function models(req, res) {
    authorize(req, opts.requireAuthForModels ?? false);
    const created = Math.floor(Date.now() / 1e3);
    const seen = /* @__PURE__ */ new Map();
    for (const p of await iron.listProfiles()) {
      if (p.enabled && p.defaultModel) seen.set(p.defaultModel, p.provider);
    }
    for (const a of iron.registry.list()) if (a.defaultModel) seen.set(a.defaultModel, a.id);
    const data = [...seen.entries()].map(([id, owned_by]) => ({
      id,
      object: "model",
      created,
      owned_by
    }));
    send(res, 200, { object: "list", data }, corsHeaders(req));
  }
  function events(req, res) {
    res.writeHead(200, sseHeaders(req));
    res.write(": connected\n\n");
    sockets.add(res);
    const off = iron.events.onAny((ev) => {
      res.write(sseFrame(ev, ev.type));
    });
    const hb = setInterval(() => res.write(": ping\n\n"), heartbeatMs);
    hb.unref?.();
    const cleanup = () => {
      clearInterval(hb);
      off();
      sockets.delete(res);
    };
    req.on("close", cleanup);
    res.on("close", cleanup);
  }
  async function control(req, res, path) {
    if (path === "/iron/health") {
      send(
        res,
        200,
        {
          ok: true,
          name: "iron-proxy",
          profiles: (await iron.listProfiles()).length,
          version: PROXY_VERSION,
          features: PROXY_FEATURES
        },
        corsHeaders(req)
      );
      return;
    }
    authorize(req, true);
    const method = req.method ?? "GET";
    const h = corsHeaders(req);
    const parts = path.split("/").filter(Boolean);
    const sub = parts[1];
    const id = parts[2];
    const action = parts[3];
    const action2 = parts[4];
    if (sub === "events" && method === "GET") return events(req, res);
    if (sub === "providers" && method === "GET") return send(res, 200, await client.providers(), h);
    if (sub === "states" && method === "GET") return send(res, 200, await client.states(), h);
    if (sub === "doctor" && method === "GET") return send(res, 200, await client.doctor(), h);
    if (sub === "discover" && method === "GET")
      return send(res, 200, await client.discoverLogins(), h);
    if (sub === "usage" && method === "GET" && !id) {
      const profileId = new URL(req.url ?? "/", "http://localhost").searchParams.get("profileId");
      return send(res, 200, await client.usageReport(profileId || void 0), h);
    }
    if (sub === "adopt" && method === "POST") {
      const body = await readJson(req);
      if (typeof body.provider !== "string" || typeof body.home !== "string")
        throw new HttpError(400, "`provider` and `home` are required.");
      return send(
        res,
        201,
        await client.adoptLogin({
          provider: body.provider,
          home: body.home,
          ...typeof body.title === "string" && body.title ? { title: body.title } : {}
        }),
        h
      );
    }
    if (sub === "pick" && method === "GET" && !id) {
      const q = new URL(req.url ?? "/", "http://localhost").searchParams;
      const provider = q.get("provider");
      if (!provider)
        throw new HttpError(400, "`provider` is required: /iron/pick?provider=anthropic.");
      const lane = q.get("lane") || "cli";
      if (!LANES.includes(lane))
        throw new HttpError(400, `\`lane\` must be one of ${LANES.join(", ")}, not "${lane}".`);
      const profile = await iron.pickProfile(provider, {
        lane
      });
      const env = profile.lane === "cli" ? await iron.shellEnv(profile.id) : null;
      return send(res, 200, { profile, env }, h);
    }
    if (sub === "refresh" && method === "POST") {
      const body = await readJson(req);
      return send(res, 200, await client.refreshStatus(body.id), h);
    }
    if (sub === "profiles") {
      if (!id && method === "GET") return send(res, 200, await client.listProfiles(), h);
      if (!id && method === "POST") {
        const body = await readJson(req);
        return send(res, 201, await client.createProfile(body), h);
      }
      if (id === "reorder" && method === "POST") {
        const body = await readJson(req);
        if (!body.provider || !Array.isArray(body.ids))
          throw new HttpError(400, "`provider` and `ids` are required.");
        return send(res, 200, await client.reorder(body.provider, body.ids), h);
      }
      if (!id) throw new HttpError(405, "Method not allowed.");
      if (!action) {
        if (method === "GET") return send(res, 200, await iron.getProfile(id), h);
        if (method === "PATCH")
          return send(res, 200, await client.updateProfile(id, await readJson(req)), h);
        if (method === "DELETE") {
          await client.deleteProfile(id);
          return send(res, 200, { ok: true }, h);
        }
        throw new HttpError(405, "Method not allowed.");
      }
      if (method === "POST" && action === "activate")
        return send(res, 200, await client.activate(id), h);
      if (method === "POST" && action === "api-key") {
        const body = await readJson(req);
        if (typeof body.secret !== "string" || !body.secret)
          throw new HttpError(400, "`secret` is required.");
        await client.setApiKey(id, body.secret);
        return send(res, 200, { ok: true }, h);
      }
      if (method === "POST" && action === "login" && !action2) {
        const session = await iron.login(id);
        logins.set(id, session);
        session.done.catch(() => {
        }).finally(() => {
          if (logins.get(id) === session) logins.delete(id);
        });
        return send(res, 202, { profileId: id, started: true }, h);
      }
      if (method === "POST" && action === "login" && action2 === "cancel") {
        logins.get(id)?.cancel();
        await client.cancelLogin(id);
        return send(res, 200, { ok: true }, h);
      }
      if (method === "GET" && action === "login-command")
        return send(res, 200, await client.loginCommand(id), h);
      if (method === "POST" && action === "logout") {
        await client.logout(id);
        return send(res, 200, { ok: true }, h);
      }
      if (method === "POST" && action === "unpark") {
        await client.unpark(id);
        return send(res, 200, { ok: true }, h);
      }
      if (method === "POST" && action === "signal" && !action2) {
        const body = await readJson(req);
        return send(res, 200, await iron.reportSignal(id, body), h);
      }
      if (method === "POST" && action === "finished" && !action2) {
        const body = await readJson(req);
        const state = await iron.reportFinished(id, body);
        return send(res, 200, { ok: true, state }, h);
      }
      if (method === "GET" && action === "models")
        return send(res, 200, await client.listModels(id), h);
    }
    throw new HttpError(404, `No control route ${method} ${path}.`, "INVALID_REQUEST");
  }
  const server = createServer((req, res) => {
    const url = new URL(req.url ?? "/", `http://${host}`);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    const dialect = path.startsWith("/v1/messages") ? "anthropic" : path.startsWith("/v1/") ? "openai" : "json";
    const method = req.method ?? "GET";
    if (method === "OPTIONS" && opts.cors) {
      res.writeHead(204, corsHeaders(req));
      res.end();
      return;
    }
    (async () => {
      if (path === "/v1/chat/completions" && method === "POST") return chatCompletions(req, res);
      if (path === "/v1/messages" && method === "POST") return messages(req, res);
      if (path === "/v1/models" && method === "GET") return models(req, res);
      if (path.startsWith("/iron/")) return control(req, res, path);
      if (path === "/" && method === "GET") {
        return send(
          res,
          200,
          {
            name: "iron-proxy",
            endpoints: ["/v1/chat/completions", "/v1/messages", "/v1/models", "/iron/*"]
          },
          corsHeaders(req)
        );
      }
      throw new HttpError(404, `No route ${method} ${path}.`);
    })().catch((err) => sendError(res, req, dialect, err));
  });
  server.keepAliveTimeout = 65e3;
  return {
    server,
    token,
    listen() {
      return new Promise((resolve2, reject) => {
        server.once("error", reject);
        server.listen(opts.port ?? 0, host, () => {
          server.off("error", reject);
          const addr = server.address();
          const shownHost = addr.address.includes(":") ? `[${addr.address}]` : addr.address;
          resolve2({
            host: addr.address,
            port: addr.port,
            url: `http://${shownHost}:${addr.port}`,
            token
          });
        });
      });
    },
    close() {
      for (const s of sockets) s.end();
      sockets.clear();
      for (const l of logins.values()) l.cancel();
      logins.clear();
      return new Promise((resolve2, reject) => {
        server.closeAllConnections?.();
        server.close(
          (err) => err && err.code !== "ERR_SERVER_NOT_RUNNING" ? reject(err) : resolve2()
        );
      });
    }
  };
}
function switchedComment(ev) {
  return `: iron switched ${ev.fromProfileId} -> ${ev.toProfileId}${ev.resumed ? " resumed" : ""}

`;
}
function isSerialized(err) {
  return !!err && typeof err === "object" && typeof err.code === "string" && typeof err.message === "string" && "retryable" in err;
}

// src/launch.ts
var CMD_META = /([()\][%!^"`<>&|;, *?])/g;
function escapeCmdCommand(command) {
  return command.replace(CMD_META, "^$1");
}
function escapeCmdArgument(arg, doubleEscape = true) {
  let a = arg.replace(/(\\*)"/g, '$1$1\\"');
  a = a.replace(/(\\*)$/, "$1$1");
  a = `"${a}"`;
  a = a.replace(CMD_META, "^$1");
  if (doubleEscape) a = a.replace(CMD_META, "^$1");
  return a;
}
function buildLaunch(resolved, args, platform = process.platform, comspec = process.env.ComSpec || "cmd.exe") {
  if (platform === "win32" && /\.(cmd|bat)$/i.test(resolved)) {
    const line = [escapeCmdCommand(resolved), ...args.map((a) => escapeCmdArgument(a))].join(" ");
    return {
      command: comspec,
      args: ["/d", "/s", "/c", `"${line}"`],
      windowsVerbatimArguments: true
    };
  }
  return { command: resolved, args };
}
function launchInteractive(spawn2, spec, env) {
  return new Promise((resolve2, reject) => {
    const ignore = () => {
    };
    process.on("SIGINT", ignore);
    const done = () => process.off("SIGINT", ignore);
    let child;
    try {
      child = spawn2(spec.command, spec.args, {
        stdio: "inherit",
        env,
        shell: false,
        ...spec.windowsVerbatimArguments ? { windowsVerbatimArguments: true } : {}
      });
    } catch (err) {
      done();
      reject(err);
      return;
    }
    child.once("error", (err) => {
      done();
      reject(err);
    });
    child.once("close", (code2, signal) => {
      done();
      resolve2(code2 ?? (signal ? 1 : 0));
    });
  });
}

// src/commands.ts
var VERSION = true ? "0.1.0" : "0.0.0-dev";
var HELP = `iron-proxy \u2014 bring-your-own-subscription account switching for AI providers

Usage:
  iron-proxy --version
  iron-proxy setup [--yes]
  iron-proxy serve [--port 8791] [--host 127.0.0.1] [--token T] [--data-dir D] [--cors]
  iron-proxy profiles list [--json]
  iron-proxy profiles add --provider P --lane cli|api-key --title T [--model M] [--api-key-stdin] [--base-url URL]
  iron-proxy profiles discover [--json]
  iron-proxy profiles adopt <provider> [--home DIR] [--title T]
  iron-proxy profiles rename <id> <title>
  iron-proxy profiles remove <id>
  iron-proxy profiles reorder <provider> <id> [<id> ...]
  iron-proxy profiles activate|enable|disable <id>
  iron-proxy login <id> [--terminal]
  iron-proxy logout <id>
  iron-proxy status [--json]
  iron-proxy usage [--json] [--profile id]
  iron-proxy doctor [--json]
  iron-proxy models <id>
  iron-proxy chat <provider-or-model> [-m model] [--profile id] [--resume] "prompt"
  iron-proxy run <provider> [--profile id] [-- extra args for the vendor CLI]
  iron-proxy env <provider> [--profile id] [--shell bash|powershell|cmd]

Providers: ${PROVIDER_IDS.join(", ")}
Data dir:  --data-dir or IRON_PROXY_DATA_DIR (default ~/.iron-proxy)
`;
async function runCli(argv, io) {
  let parsed;
  try {
    parsed = parseArgs({
      args: argv,
      allowPositionals: true,
      strict: true,
      options: {
        help: { type: "boolean", short: "h" },
        version: { type: "boolean", short: "v" },
        json: { type: "boolean" },
        port: { type: "string" },
        host: { type: "string" },
        token: { type: "string" },
        "data-dir": { type: "string" },
        cors: { type: "boolean" },
        terminal: { type: "boolean" },
        provider: { type: "string" },
        lane: { type: "string" },
        title: { type: "string" },
        model: { type: "string", short: "m" },
        "api-key-stdin": { type: "boolean" },
        "base-url": { type: "string" },
        profile: { type: "string" },
        home: { type: "string" },
        shell: { type: "string" },
        yes: { type: "boolean", short: "y" },
        resume: { type: "boolean" }
      }
    });
  } catch (err2) {
    io.stderr.write(`${err2.message}

${HELP}`);
    return 2;
  }
  const { values, positionals } = parsed;
  const [cmd, ...rest] = positionals;
  if (values.version) {
    io.stdout.write(`${VERSION}
`);
    return 0;
  }
  if (!cmd || values.help) {
    io.stdout.write(HELP);
    return values.help ? 0 : 1;
  }
  const dataDir = values["data-dir"] ?? (io.env ?? process.env).IRON_PROXY_DATA_DIR ?? defaultDataDir();
  const iron = io.iron ?? createIronProxy({ dataDir });
  const own = !io.iron;
  const json = !!values.json;
  const out = (s) => io.stdout.write(s.endsWith("\n") ? s : `${s}
`);
  const err = (s) => io.stderr.write(s.endsWith("\n") ? s : `${s}
`);
  try {
    switch (cmd) {
      case "serve":
        return await serve(
          iron,
          io,
          dataDir,
          values
        );
      case "profiles":
        return await profiles(
          iron,
          io,
          rest,
          values,
          json
        );
      case "login": {
        const id = need(rest[0], "profile id");
        if (values.terminal) {
          await printTerminalLogin(iron, id, out);
          return 0;
        }
        await headlessLogin(iron, id, out, err);
        return 0;
      }
      case "logout": {
        const id = need(rest[0], "profile id");
        const p = await iron.getProfile(id);
        await iron.logout(id);
        out("Logged out.");
        if (p.cli?.adopted)
          err(
            `Note: "${p.title}" was an existing login, so your own ${p.provider} CLI is signed out too.`
          );
        return 0;
      }
      case "status":
        return await status(iron, io, json);
      case "usage":
        return await usage(iron, io, json, values.profile);
      case "doctor": {
        const probes = await iron.doctor();
        if (json) out(JSON.stringify(probes, null, 2));
        else {
          out(
            table(
              ["provider", "binary", "found", "version", "home env", "path"],
              probes.map((p) => [
                p.provider,
                p.binary,
                p.found ? "yes" : "no",
                p.version ?? "",
                p.homeEnv,
                p.path ?? ""
              ])
            )
          );
        }
        return 0;
      }
      case "models": {
        const models = await iron.listModels(need(rest[0], "profile id"));
        out(json ? JSON.stringify(models) : models.join("\n") || "(none reported)");
        return 0;
      }
      case "chat":
        return await chat(iron, io, rest, values);
      case "run":
        return await runVendor(iron, io, rest, values.profile);
      case "env":
        return await envLines(
          iron,
          io,
          rest,
          values.profile,
          values.shell
        );
      case "setup":
        return await setup(iron, io, !!values.yes);
      default:
        err(`Unknown command "${cmd}".

${HELP}`);
        return 2;
    }
  } catch (e) {
    if (e instanceof IronProxyError) err(`${e.code}: ${e.message}`);
    else err(e.message ?? String(e));
    const hint = e?.hint;
    if (typeof hint === "string" && hint) err(`hint: ${hint}`);
    return 1;
  } finally {
    if (own && cmd !== "serve") await iron.close();
  }
}
async function printTerminalLogin(iron, id, out) {
  const cmdInfo = await iron.loginCommand(id);
  const envLine = Object.entries(cmdInfo.env).filter(([k]) => /^(CLAUDE_CONFIG_DIR|CODEX_HOME|GROK_HOME|GEMINI_CLI_HOME)$/.test(k)).map(([k, v]) => process.platform === "win32" ? `$env:${k}="${v}";` : `${k}="${v}"`).join(" ");
  out(
    `Run this in a terminal window:

  ${envLine} ${quote(cmdInfo.binary)} ${cmdInfo.args.map(quote).join(" ")}
`
  );
  if (cmdInfo.requiresTerminal)
    out("This CLI has no headless login; it signs in interactively on first run.");
}
async function headlessLogin(iron, id, out, err) {
  const session = await iron.login(id);
  session.on((e) => {
    if (e.type === "url") out(`Open this URL to sign in: ${e.url}`);
    else if (e.type === "code") out(`Enter this code: ${e.code}`);
    else if (e.type === "output") err(`  ${e.line}`);
    else if (e.type === "completed") out("Logged in.");
    else if (e.type === "failed") err(`Login failed: ${e.message}`);
  });
  await session.done;
}
function need(v, what) {
  if (!v) throw new IronProxyError("INVALID_REQUEST", `Missing ${what}.`);
  return v;
}
function quote(s) {
  return /[\s"']/.test(s) || s === "" ? JSON.stringify(s) : s;
}
async function serve(iron, io, dataDir, v) {
  const port = v.port !== void 0 ? Number(v.port) : 8791;
  if (!Number.isInteger(port) || port < 0 || port > 65535)
    throw new IronProxyError("INVALID_REQUEST", `Bad port "${v.port}".`);
  const host = v.host ?? "127.0.0.1";
  const proxy = createProxyServer({
    iron,
    host,
    port,
    ...typeof v.token === "string" ? { token: v.token } : {},
    ...v.cors ? { cors: true } : {},
    requireAuthForModels: host !== "127.0.0.1" && host !== "localhost" && host !== "::1"
  });
  const info = await proxy.listen();
  const descriptor = join7(dataDir, "proxy.json");
  await mkdir5(dataDir, { recursive: true });
  await writeFile3(
    descriptor,
    JSON.stringify({ url: info.url, token: info.token, pid: process.pid }, null, 2),
    { mode: 384 }
  );
  io.stdout.write(
    `Iron-Proxy listening on ${info.url}
  OpenAI-compatible:    ${info.url}/v1/chat/completions
  Anthropic-compatible: ${info.url}/v1/messages
  Control API token:    ${info.token}
  Descriptor:           ${descriptor}
`
  );
  if (host !== "127.0.0.1" && host !== "localhost" && host !== "::1") {
    io.stderr.write(
      "Warning: bound to a non-loopback host; every account here is reachable from the network. Model routes now require the token.\n"
    );
  }
  const wait = io.waitForShutdown ?? (() => new Promise((resolve2) => {
    const stop = () => resolve2();
    process.once("SIGINT", stop);
    process.once("SIGTERM", stop);
  }));
  await wait();
  await proxy.close();
  await rm3(descriptor, { force: true }).catch(() => {
  });
  await iron.close();
  return 0;
}
async function profiles(iron, io, rest, v, json) {
  const out = (s) => io.stdout.write(s.endsWith("\n") ? s : `${s}
`);
  const [sub, ...args] = rest;
  switch (sub ?? "list") {
    case "list": {
      const list = await iron.listProfiles();
      out(
        json ? JSON.stringify(list, null, 2) : table(
          ["id", "title", "provider", "lane", "order", "enabled", "model"],
          list.map((p) => [
            p.id,
            p.title,
            p.provider,
            p.lane,
            String(p.order),
            p.enabled ? "yes" : "no",
            p.defaultModel ?? ""
          ])
        )
      );
      return 0;
    }
    case "add": {
      const provider = v.provider;
      const lane = v.lane ?? "cli";
      const title = v.title;
      if (!provider || !PROVIDER_IDS.includes(provider))
        throw new IronProxyError(
          "INVALID_REQUEST",
          `--provider must be one of ${PROVIDER_IDS.join(", ")}.`
        );
      if (!["cli", "api-key", "oauth"].includes(lane))
        throw new IronProxyError("INVALID_REQUEST", "--lane must be cli, api-key or oauth.");
      if (!title) throw new IronProxyError("INVALID_REQUEST", "--title is required.");
      let apiKeySecret;
      if (v["api-key-stdin"]) {
        const read = io.readStdin ?? defaultReadStdin;
        apiKeySecret = (await read()).trim();
        if (!apiKeySecret) throw new IronProxyError("INVALID_REQUEST", "No API key on stdin.");
      }
      const created = await iron.createProfile({
        title,
        provider,
        lane,
        ...typeof v.model === "string" ? { defaultModel: v.model } : {},
        ...lane === "api-key" && typeof v["base-url"] === "string" ? { apiKey: { secretRef: "", baseUrl: v["base-url"] } } : {},
        ...apiKeySecret ? { apiKeySecret } : {}
      });
      if (created.apiKey && created.apiKey.secretRef === "") {
      }
      out(
        json ? JSON.stringify(created, null, 2) : `Created ${created.id} (${created.title}, ${created.provider}/${created.lane}).${created.lane === "cli" ? ` Next: iron-proxy login ${created.id}` : created.lane === "api-key" && !apiKeySecret ? " Next: set its key with --api-key-stdin on add, or via the API." : ""}`
      );
      return 0;
    }
    case "discover": {
      const found = await iron.discoverLogins();
      if (json) out(JSON.stringify(found, null, 2));
      else if (!found.length)
        out("No existing vendor CLI logins found in their default locations on this computer.");
      else
        out(
          table(
            ["provider", "home", "installed", "signed in", "already adopted"],
            found.map((f) => [
              f.provider,
              f.home,
              f.installed ? "yes" : "no",
              f.status === "ok" ? "yes" : f.status === "unauthenticated" ? "no" : "?",
              f.adoptedProfileId ?? "no"
            ])
          )
        );
      return 0;
    }
    case "adopt": {
      const provider = need(args[0], "provider");
      if (!PROVIDER_IDS.includes(provider))
        throw new IronProxyError("INVALID_REQUEST", `Unknown provider "${provider}".`, {
          hint: `Use one of: ${PROVIDER_IDS.join(", ")}.`
        });
      let home = typeof v.home === "string" ? v.home : void 0;
      if (!home) {
        const found = iron.defaultCliHome(provider);
        if (!found || !await isDir(found))
          throw new IronProxyError(
            "INVALID_REQUEST",
            `No existing ${provider} CLI login found in its default location${found ? ` (${found})` : ""}.`,
            {
              hint: "Run iron-proxy profiles discover to see what was found, or pass the directory with --home DIR."
            }
          );
        home = found;
      }
      const p = await iron.adoptLogin({
        provider,
        home,
        ...typeof v.title === "string" ? { title: v.title } : {}
      });
      const st = (await iron.allStates())[p.id]?.status ?? "unknown";
      out(
        json ? JSON.stringify(p, null, 2) : `Adopted ${p.cli?.home} as ${p.id} ("${p.title}", ${st}). Iron-Proxy will never delete that directory.`
      );
      if (st === "unauthenticated")
        io.stderr.write(`Note: "${p.title}" is not signed in yet: iron-proxy login ${p.id}
`);
      return 0;
    }
    case "rename": {
      const p = await iron.updateProfile(need(args[0], "profile id"), {
        title: need(args[1], "new title")
      });
      out(`Renamed ${p.id} to "${p.title}".`);
      return 0;
    }
    case "remove": {
      await iron.deleteProfile(need(args[0], "profile id"));
      out("Removed.");
      return 0;
    }
    case "reorder": {
      const provider = need(args[0], "provider");
      if (!PROVIDER_IDS.includes(provider))
        throw new IronProxyError("INVALID_REQUEST", `Unknown provider "${provider}".`);
      const ordered = await iron.reorder(provider, args.slice(1));
      out(ordered.map((p, i) => `${i}. ${p.title} (${p.id})`).join("\n"));
      return 0;
    }
    case "activate": {
      const p = await iron.activate(need(args[0], "profile id"));
      out(`"${p.title}" is now first for ${p.provider}.`);
      return 0;
    }
    case "enable":
    case "disable": {
      const p = await iron.updateProfile(need(args[0], "profile id"), {
        enabled: sub === "enable"
      });
      out(`"${p.title}" ${p.enabled ? "enabled" : "disabled"}.`);
      return 0;
    }
    default:
      throw new IronProxyError("INVALID_REQUEST", `Unknown profiles subcommand "${sub}".`);
  }
}
async function status(iron, io, json) {
  const list = await iron.listProfiles();
  const states = await iron.allStates();
  if (json) {
    io.stdout.write(
      JSON.stringify(
        list.map((p) => ({ profile: p, state: states[p.id] })),
        null,
        2
      ) + "\n"
    );
    return 0;
  }
  const rows = list.map((p) => {
    const s = states[p.id];
    const active = iron.activeProfileId(p.provider) === p.id ? "*" : "";
    return [
      active,
      p.id,
      p.title,
      p.provider,
      p.lane,
      String(p.order),
      s?.status ?? "unknown",
      s?.parkedUntil ? fmtTime(s.parkedUntil) : "",
      s?.usage?.utilisation !== void 0 ? `${Math.round(s.usage.utilisation * 100)}%` : "",
      s?.lastUsedAt ? fmtTime(s.lastUsedAt) : ""
    ];
  });
  io.stdout.write(
    table(
      [
        "",
        "id",
        "title",
        "provider",
        "lane",
        "order",
        "status",
        "parked until",
        "used",
        "last used"
      ],
      rows
    ) + "\n"
  );
  return 0;
}
async function chat(iron, io, rest, v) {
  const [target, ...promptParts] = rest;
  if (!target)
    throw new IronProxyError("INVALID_REQUEST", 'Usage: chat <provider-or-model> "prompt"');
  let prompt = promptParts.join(" ");
  if (!prompt) {
    const read = io.readStdin ?? defaultReadStdin;
    prompt = (await read()).trim();
  }
  if (!prompt) throw new IronProxyError("INVALID_REQUEST", "No prompt given.");
  const opts = {};
  const req = {
    messages: [{ role: "user", content: [{ type: "text", text: prompt }] }]
  };
  if (PROVIDER_IDS.includes(target)) opts.provider = target;
  else req.model = target;
  if (typeof v.model === "string") req.model = v.model;
  if (typeof v.profile === "string") opts.profileId = v.profile;
  if (v.resume) opts.resumeInterrupted = true;
  let served;
  let failed = false;
  for await (const ev of iron.stream(req, opts)) {
    if (ev.type === "start") served = ev.profileId;
    else if (ev.type === "text") io.stdout.write(ev.delta);
    else if (ev.type === "switched") {
      if (ev.resumed) served = ev.toProfileId;
      io.stderr.write(
        `[iron] switched ${ev.fromProfileId} -> ${ev.toProfileId} (${ev.reason.kind}${ev.resumed ? ", resumed" : ""})
`
      );
    } else if (ev.type === "error") {
      failed = true;
      io.stderr.write(`
[iron] ${ev.error.code}: ${ev.error.message}
`);
      if (ev.error.hint) io.stderr.write(`hint: ${ev.error.hint}
`);
    }
  }
  io.stdout.write("\n");
  if (served) {
    const p = await iron.getProfile(served).catch(() => void 0);
    io.stderr.write(`[iron] served by ${p ? `"${p.title}"` : served} (${served})
`);
  }
  return failed ? 1 : 0;
}
function needProvider(v, usage2) {
  if (!v) throw new IronProxyError("INVALID_REQUEST", `Usage: ${usage2}`);
  if (!PROVIDER_IDS.includes(v))
    throw new IronProxyError("INVALID_REQUEST", `Unknown provider "${v}".`, {
      hint: `Use one of: ${PROVIDER_IDS.join(", ")}.`
    });
  return v;
}
async function runVendor(iron, io, rest, profileId) {
  const [target, ...extra] = rest;
  const provider = needProvider(target, "run <provider> [--profile id] [-- extra args]");
  const p = await iron.pickProfile(provider, {
    lane: "cli",
    ...profileId ? { profileId } : {}
  });
  const cmd = await iron.interactiveCommand(p.id, extra);
  const resolved = await which(cmd.binary);
  if (!resolved)
    throw new CliError("CLI_NOT_FOUND", `"${cmd.binary}" is not installed or not on PATH.`, {
      binary: cmd.binary
    });
  io.stderr.write(`Using "${p.title}" (${p.provider})
`);
  if (profileId) await notePinnedState(iron, io, p);
  return launchInteractive(io.spawn ?? nodeSpawn, buildLaunch(resolved, cmd.args), cmd.env);
}
async function notePinnedState(iron, io, p) {
  const st = (await iron.allStates())[p.id];
  if (st?.status === "parked")
    io.stderr.write(
      st.parkedUntil ? `Note: "${p.title}" is parked until ${fmtTime(st.parkedUntil)}; it may refuse requests.
` : `Note: "${p.title}" is parked; it may refuse requests.
`
    );
  else if (st?.status === "unauthenticated")
    io.stderr.write(`Note: "${p.title}" is not signed in: iron-proxy login ${p.id}
`);
}
function shQuote(v) {
  return `'${v.replace(/'/g, `'\\''`)}'`;
}
function psQuote(v) {
  return `'${v.replace(/'/g, "''")}'`;
}
async function envLines(iron, io, rest, profileId, shellOpt) {
  const provider = needProvider(
    rest[0],
    "env <provider> [--profile id] [--shell bash|powershell|cmd]"
  );
  const shell = shellOpt ?? ((io.platform ?? process.platform) === "win32" ? "powershell" : "bash");
  if (!["bash", "powershell", "cmd"].includes(shell))
    throw new IronProxyError("INVALID_REQUEST", `Unknown shell "${shellOpt}".`, {
      hint: "Use --shell bash (also zsh), --shell powershell or --shell cmd."
    });
  const p = await iron.pickProfile(provider, {
    lane: "cli",
    ...profileId ? { profileId } : {}
  });
  const { set, unset } = await iron.shellEnv(p.id);
  const lines = [];
  for (const [k, v] of Object.entries(set)) {
    if (shell === "bash") lines.push(`export ${k}=${shQuote(v)}`);
    else if (shell === "powershell") lines.push(`$env:${k} = ${psQuote(v)}`);
    else lines.push(`set "${k}=${v}"`);
  }
  if (shell === "bash" && unset.length) lines.push(`unset ${unset.join(" ")}`);
  if (shell === "powershell")
    for (const k of unset) lines.push(`Remove-Item Env:${k} -ErrorAction SilentlyContinue`);
  io.stdout.write(`${lines.join("\n")}
`);
  io.stderr.write(`Using "${p.title}" (${p.provider})
`);
  if (profileId) await notePinnedState(iron, io, p);
  io.stderr.write(
    shell === "cmd" ? `Note: these lines set the account's home only; API-key variables are unset only by iron-proxy run ${provider}, so clear them yourself if they are set.
` : `Note: these lines set the account's home and clear API-key variables in this shell; only iron-proxy run ${provider} starts the CLI with a fully scrubbed environment.
`
  );
  return 0;
}
function readlinePrompt(streams = {}) {
  const input = streams.input ?? process.stdin;
  const sink = streams.output ?? process.stdout;
  let muted = false;
  const output = new Writable({
    write(chunk, _enc, cb) {
      if (!muted) sink.write(chunk);
      cb();
    }
  });
  const rl = createInterface({ input, output, terminal: !!input.isTTY });
  const lines = [];
  const waiting = [];
  let closed = false;
  rl.on("line", (line) => {
    const next = waiting.shift();
    if (next) next(line);
    else lines.push(line);
  });
  rl.on("close", () => {
    closed = true;
    for (const next of waiting.splice(0)) next("");
  });
  const nextLine = () => {
    if (lines.length) return Promise.resolve(lines.shift());
    if (closed) return Promise.resolve("");
    return new Promise((resolve2) => waiting.push(resolve2));
  };
  const ask = async (question, opts = {}) => {
    if (!opts.secret) {
      rl.setPrompt(question);
      rl.prompt(true);
      return nextLine();
    }
    sink.write(question);
    rl.setPrompt("");
    muted = true;
    try {
      return await nextLine();
    } finally {
      muted = false;
      sink.write("\n");
    }
  };
  return { ask, close: () => rl.close() };
}
async function isDir(path) {
  try {
    return (await stat3(path)).isDirectory();
  } catch {
    return false;
  }
}
function isYes(answer, byDefault) {
  const a = answer.trim().toLowerCase();
  if (!a) return byDefault;
  return a === "y" || a === "yes";
}
function freeTitle2(base, taken) {
  if (!taken.has(base)) return base;
  for (let n = 2; ; n++) if (!taken.has(`${base} ${n}`)) return `${base} ${n}`;
}
async function choose(ask, out, question, choices) {
  out(choices.map((c, i) => `  ${i + 1}) ${c.label}`).join("\n"));
  for (let attempt = 0; attempt < 3; attempt++) {
    const a = (await ask(`${question} [1-${choices.length}]: `)).trim();
    const byNumber = choices[Number(a) - 1];
    const picked = /^\d+$/.test(a) ? byNumber : choices.find((c) => c.value === a);
    if (picked) return picked.value;
    out(`Please answer with a number from 1 to ${choices.length}.`);
  }
  throw new IronProxyError("INVALID_REQUEST", "No valid choice given.", {
    hint: "Run iron-proxy setup again, or add the account with iron-proxy profiles add."
  });
}
async function setup(iron, io, yes) {
  const out = (s) => io.stdout.write(s.endsWith("\n") ? s : `${s}
`);
  const errLine = (s) => io.stderr.write(s.endsWith("\n") ? s : `${s}
`);
  const own = !io.prompt && !yes ? readlinePrompt() : void 0;
  const ask = io.prompt ?? own?.ask ?? (async () => "");
  const report = (e) => {
    errLine(
      e instanceof IronProxyError ? `${e.code}: ${e.message}` : String(e.message ?? e)
    );
    const hint = e?.hint;
    if (typeof hint === "string" && hint) errLine(`hint: ${hint}`);
  };
  try {
    out("\n== 1. Vendor CLIs on this computer ==");
    for (const adapter of iron.registry.list()) {
      const lane = adapter.lanes.cli;
      if (!(lane instanceof CliLane)) continue;
      const path = await lane.findBinary();
      const official = CLI_SPECS.find((s) => s.provider === adapter.id)?.binary ?? lane.spec.binary;
      if (path) out(`  ${official.padEnd(7)} installed   (${adapter.id})`);
      else out(`  ${official.padEnd(7)} missing     (${adapter.id})  ${installHint(official)}`);
    }
    out("\n== 2. Existing logins ==");
    const found = await iron.discoverLogins();
    const usable = found.filter((f) => f.status === "ok" && !f.adoptedProfileId);
    if (!usable.length) out("  No new signed-in vendor CLI logins found.");
    for (const f of usable) {
      const take = yes || isYes(await ask(`Use "${f.suggestedTitle}" as an account? [Y/n] `), true);
      if (!take) continue;
      try {
        const p = await iron.adoptLogin({
          provider: f.provider,
          home: f.home,
          title: f.suggestedTitle
        });
        out(`  Added "${p.title}" (${p.provider}), using ${f.home} as-is.`);
      } catch (e) {
        report(e);
      }
    }
    if (!yes) {
      out("\n== 3. More accounts ==");
      while (isYes(await ask("Add another account? [y/N] "), false)) {
        try {
          await addAccount(iron, ask, out, errLine);
        } catch (e) {
          report(e);
        }
      }
    }
    out(`
== ${yes ? 3 : 4}. Your accounts ==`);
    await status(iron, io, false);
    const first = (await iron.listProfiles())[0];
    out(`
You're set. Try: iron-proxy chat ${first?.provider ?? "anthropic"} "hello"`);
    return 0;
  } finally {
    own?.close();
  }
}
async function addAccount(iron, ask, out, errLine) {
  const adapters = iron.registry.list();
  const provider = await choose(
    ask,
    out,
    "Provider",
    adapters.map((a) => ({ value: a.id, label: `${a.id.padEnd(17)} ${a.displayName}` }))
  );
  const offered = Object.keys(iron.registry.get(provider).lanes).filter(
    (l) => l === "cli" || l === "api-key"
  );
  if (!offered.length)
    throw new IronProxyError("UNSUPPORTED", `Provider "${provider}" has no lane setup can add.`);
  const lane = offered.length === 1 ? offered[0] : await choose(
    ask,
    out,
    "Sign in with",
    offered.map((l) => ({
      value: l,
      label: l === "cli" ? "cli      your subscription, through the vendor CLI" : "api-key  an API key"
    }))
  );
  const taken = new Set((await iron.listProfiles()).map((p2) => p2.title));
  const suggested = freeTitle2(
    `${PROVIDER_SHORT_NAMES[provider]} (${lane === "cli" ? "subscription" : "API key"})`,
    taken
  );
  const title = (await ask(`Title [${suggested}]: `)).trim() || suggested;
  if (lane === "api-key") {
    let baseUrl;
    if (provider === "openai-compatible") {
      baseUrl = (await ask("Base URL (the server's /v1 URL): ")).trim();
      if (!baseUrl) throw new IronProxyError("INVALID_REQUEST", "No base URL given.");
    }
    const key = (await ask("API key (not shown): ", { secret: true })).trim();
    if (!key)
      throw new IronProxyError("INVALID_REQUEST", "No API key given.", {
        hint: "Add it later: iron-proxy profiles add --lane api-key --api-key-stdin."
      });
    const p2 = await iron.createProfile({
      title,
      provider,
      lane,
      apiKeySecret: key,
      ...baseUrl ? { apiKey: { secretRef: "", baseUrl } } : {}
    });
    out(`  Added "${p2.title}" (${p2.provider}, API key stored in the vault).`);
    return;
  }
  const p = await iron.createProfile({ title, provider, lane });
  out(`  Added "${p.title}" (${p.provider}). Signing in...`);
  const info = await iron.loginCommand(p.id);
  if (info.requiresTerminal) {
    await printTerminalLogin(iron, p.id, out);
    return;
  }
  await headlessLogin(iron, p.id, out, errLine);
}
function compactNumber(n) {
  if (n < 1e3) return String(n);
  if (n < 1e6) return `${(n / 1e3).toFixed(n < 1e4 ? 1 : 0).replace(/\.0$/, "")}k`;
  return `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M`;
}
function windowCell(w) {
  const tokens = w.inputTokens + w.outputTokens;
  return `${w.requests} req, ${compactNumber(tokens)} tok`;
}
function estimateLine(r) {
  if (!r.estimate) return "";
  return `about ${r.estimate.minutesLeft} min left at this pace${r.estimate.confidence === "low" ? " (rough)" : ""}`;
}
async function usage(iron, io, json, profileId) {
  const reports = await iron.usageReport(profileId ? { profileId } : {});
  const out = (s) => io.stdout.write(s.endsWith("\n") ? s : `${s}
`);
  if (json) {
    out(JSON.stringify(reports, null, 2));
    return 0;
  }
  if (!reports.length) {
    out("No accounts yet. Add one with iron-proxy setup.");
    return 0;
  }
  const profiles2 = new Map((await iron.listProfiles()).map((p) => [p.id, p]));
  const rows = reports.map((r) => {
    const p = profiles2.get(r.profileId);
    return [
      p?.title ?? r.profileId,
      p?.provider ?? "",
      windowCell(r.windows["5h"]),
      windowCell(r.windows["24h"]),
      windowCell(r.windows["7d"]),
      String(r.parks7d),
      estimateLine(r)
    ];
  });
  out(table(["title", "provider", "5h", "24h", "7d", "parks this week", "pace"], rows));
  return 0;
}
function fmtTime(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}
function table(headers, rows) {
  const widths = headers.map((h, i) => Math.max(h.length, ...rows.map((r) => (r[i] ?? "").length)));
  const line = (cells) => cells.map((c, i) => (c ?? "").padEnd(widths[i] ?? 0)).join("  ").trimEnd();
  return [line(headers), line(widths.map((w) => "-".repeat(w))), ...rows.map(line)].join("\n");
}
async function defaultReadStdin() {
  let s = "";
  for await (const chunk of process.stdin) s += chunk;
  return s;
}

// src/cli.ts
runCli(process.argv.slice(2), { stdout: process.stdout, stderr: process.stderr }).then(
  (code2) => {
    process.exitCode = code2;
  },
  (err) => {
    process.stderr.write(`${err.message ?? String(err)}
`);
    process.exitCode = 1;
  }
);
