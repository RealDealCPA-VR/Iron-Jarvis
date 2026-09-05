// Tiny runtime API client. All calls happen in the browser ('use client'),
// so `next build` never touches the daemon.

import { NOT_MODIFIED, rememberEtag } from "./etag";

export const API_BASE = (
  process.env.NEXT_PUBLIC_IJ_API || "http://127.0.0.1:8787"
).replace(/\/$/, "");

// Optional bearer token for deployed daemons that set IRONJARVIS_TOKEN.
// Resolved at RUNTIME: a token saved in localStorage (via the Connections/login
// box) wins, so you can log into a deployed instance WITHOUT a rebuild; falls
// back to the build-time NEXT_PUBLIC_IJ_TOKEN. Unset (local) => no header.
const IJ_TOKEN_KEY = "ij_token";

export function ijToken(): string {
  if (typeof window !== "undefined") {
    try {
      const stored = window.localStorage.getItem(IJ_TOKEN_KEY);
      if (stored) return stored.trim();
    } catch {
      /* ignore */
    }
  }
  return (process.env.NEXT_PUBLIC_IJ_TOKEN || "").trim();
}

/** Save (or clear, when empty) the daemon auth token used by all requests. */
export function setIjToken(token: string): void {
  if (typeof window === "undefined") return;
  try {
    if (token.trim()) window.localStorage.setItem(IJ_TOKEN_KEY, token.trim());
    else window.localStorage.removeItem(IJ_TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

/** Authorization header for the bearer token, or {} when none is configured. */
function authHeaders(): Record<string, string> {
  const t = ijToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export function wsUrl(path: string): string {
  const url = API_BASE.replace(/^http/, "ws") + path;
  // Browsers can't set WS headers, so the token rides along as a query param.
  const t = ijToken();
  if (!t) return url;
  const sep = path.includes("?") ? "&" : "?";
  return `${url}${sep}token=${encodeURIComponent(t)}`;
}

/** Absolute URL for an SSE endpoint consumed via EventSource. Like wsUrl, the
 * bearer token rides as a `?token=` query param because EventSource (unlike
 * fetch) cannot set an Authorization header. */
export function sseUrl(path: string): string {
  const url = `${API_BASE}${path}`;
  const t = ijToken();
  if (!t) return url;
  const sep = path.includes("?") ? "&" : "?";
  return `${url}${sep}token=${encodeURIComponent(t)}`;
}

export const put = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined });

export class ApiError extends Error {
  status: number;
  /** v1.230.0 (FP5): true when the CALLER aborted the request (its own
   *  AbortSignal fired). Status stays 0 so every "status 0 = no error UI"
   *  consumer keeps working, but this is not the daemon being unreachable
   *  and it fires no app-wide network signal. */
  cancelled: boolean;
  constructor(message: string, status: number, cancelled = false) {
    super(message);
    this.status = status;
    this.cancelled = cancelled;
    this.name = "ApiError";
  }
}

/**
 * v1.226.0 (contract C4): a pydantic 422 from an older daemon carries a LIST
 * detail (`[{loc: ["body","steps"], msg: "..."}]`), which `String()` renders as
 * "[object Object]". Flatten it to "field: msg; field: msg" (loc minus the
 * leading "body"), the same string shape the daemon now emits itself.
 */
export function flattenDetail(detail: unknown): string {
  if (!Array.isArray(detail)) return String(detail);
  return detail
    .map((item) => {
      if (!item || typeof item !== "object") return String(item);
      const rec = item as { loc?: unknown; msg?: unknown };
      const loc = Array.isArray(rec.loc)
        ? rec.loc.filter((p, i) => !(i === 0 && p === "body")).map(String).join(".")
        : "";
      const msg = rec.msg === undefined ? JSON.stringify(item) : String(rec.msg);
      return loc ? `${loc}: ${msg}` : msg;
    })
    .join("; ");
}

// App-wide auth signal: a 401/403 from any DATA request means the bearer token is
// missing/stale. The /health poll is auth-EXEMPT so it can't detect this — without
// this, every page silently renders a false "empty install" on a bad token.
type AuthListener = (unauthorized: boolean) => void;
const authListeners = new Set<AuthListener>();
export function onUnauthorizedChange(fn: AuthListener): () => void {
  authListeners.add(fn);
  return () => authListeners.delete(fn);
}
function signalAuth(unauthorized: boolean): void {
  authListeners.forEach((fn) => fn(unauthorized));
}

// App-wide "the daemon returned an error" signal (a non-auth 4xx/5xx). Without it a
// 500 on a data page renders a misleading "No X yet" empty state (pages treat only
// status===0 as a problem). Cleared by the next successful data request.
type ErrorListener = (failing: boolean) => void;
const errorListeners = new Set<ErrorListener>();
export function onRequestErrorChange(fn: ErrorListener): () => void {
  errorListeners.add(fn);
  return () => errorListeners.delete(fn);
}
function signalError(failing: boolean): void {
  errorListeners.forEach((fn) => fn(failing));
}

// v1.226.0: "a request could not REACH the daemon" signal (the catch below
// that mints ApiError status 0). The /health poll alone misses a restart that
// falls BETWEEN two 5s polls: a page's one GET dies, the daemon is back before
// the next poll, and the offline->online edge never happens. DaemonProvider
// subscribes, marks itself offline and re-polls, so the next good poll walks
// the edge and status-0 hooks refetch.
type NetworkListener = () => void;
const networkListeners = new Set<NetworkListener>();
export function onNetworkError(fn: NetworkListener): () => void {
  networkListeners.add(fn);
  return () => networkListeners.delete(fn);
}
function signalNetworkError(): void {
  networkListeners.forEach((fn) => fn());
}

// OPT-IN request timeout. Applied ONLY when a caller passes `timeoutMs` (the
// /health poll + list polls, to detect a frozen-but-connected daemon). NEVER
// blanket-applied: a user-initiated GET like a whole-drive file search or the first
// cold-Ollama semantic search legitimately runs far longer than any poll timeout.
// `ifNoneMatch` (v1.230.0, FP3): send the ETag a previous response carried; a 304
// then resolves to the NOT_MODIFIED marker instead of a body (never an error).
export type ApiInit = RequestInit & { timeoutMs?: number; ifNoneMatch?: string };

export async function api<T>(path: string, init?: ApiInit): Promise<T> {
  const { timeoutMs, ifNoneMatch, ...rest } = init || {};
  const controller = timeoutMs ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  let res: Response;
  try {
    // v1.230.0 (FP1): NO `cache: "no-store"` here. It made Chromium bypass its
    // CORS preflight cache, so every GET cost an OPTIONS round-trip although the
    // daemon answers `access-control-max-age: 600` (measured in Edge: 10 GET ->
    // 10 OPTIONS with it, 1 without). Freshness is the DAEMON's job now: its
    // NoStoreMiddleware puts `Cache-Control: no-store` on every response, which
    // is what keeps session JSON (client file names) out of the Electron disk
    // cache. Do not put the client-side option back to "be safe" — both sides
    // were measured, and only the server header is load-bearing.
    res = await fetch(`${API_BASE}${path}`, {
      ...rest,
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(),
        ...(ifNoneMatch ? { "If-None-Match": ifNoneMatch } : {}),
        ...(rest.headers || {}),
      },
      ...(controller ? { signal: controller.signal } : {}),
    });
  } catch {
    // v1.230.0 (FP5): the CALLER's own abort (the command palette supersedes a
    // search on every keystroke) is not an outage. Reporting it as "daemon
    // offline" + the network signal restarted DaemonProvider's poll loop per
    // keystroke. A distinct, quiet rejection instead.
    if (rest.signal?.aborted) throw new ApiError("cancelled", 0, true);
    // Network error or (opt-in) timeout => daemon offline / not responding.
    // The /health poll is DaemonProvider's own probe: it judges misses itself
    // (FP4, two in a row), so its timeout must not restart that loop from here.
    if (path !== "/health") signalNetworkError();
    throw new ApiError("daemon offline", 0);
  } finally {
    if (timer) clearTimeout(timer);
  }
  // v1.230.0 (FP3): the daemon confirmed the caller's copy is current. Reachable
  // and authorised, so the data signals clear like any 2xx; no body to parse.
  if (res.status === 304 && ifNoneMatch) {
    if (path !== "/health") {
      signalAuth(false);
      signalError(false);
    }
    return NOT_MODIFIED as unknown as T;
  }
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) signalAuth(true);
    else if (res.status >= 500) signalError(true); // SERVER error — surface globally
    // (a 4xx like 404/400/422 is a normal per-request condition the page handles —
    //  never flash a global banner for it)
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = flattenDetail(body.detail);
    } catch {
      /* ignore */
    }
    throw new ApiError(detail, res.status);
  }
  // A successful DATA response clears the error signals. Skip /health for BOTH: it
  // is auth-exempt (a bad token still 200s) AND it polls every 5s, so clearing on it
  // would race a real persistent data 500 away (self-clear + flicker).
  if (path !== "/health") {
    signalAuth(false);
    signalError(false);
  }
  if (res.status === 204) return undefined as T;
  const data = (await res.json()) as T;
  // The tag travels with the payload (lib/etag.ts) so a hook can send it back.
  rememberEtag(data, typeof res.headers?.get === "function" ? res.headers.get("etag") : null);
  return data;
}

export const get = <T>(path: string, opts?: { timeoutMs?: number; ifNoneMatch?: string }) =>
  api<T>(path, opts);

export const post = <T>(
  path: string,
  body?: unknown,
  opts?: { timeoutMs?: number; signal?: AbortSignal },
) =>
  api<T>(path, {
    method: "POST",
    body: body ? JSON.stringify(body) : undefined,
    ...opts,
  });

export const patch = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined });

export const del = <T>(path: string) => api<T>(path, { method: "DELETE" });
