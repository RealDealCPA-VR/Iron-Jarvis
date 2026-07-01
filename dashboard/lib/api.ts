// Tiny runtime API client. All calls happen in the browser ('use client'),
// so `next build` never touches the daemon.

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

export const put = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined });

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
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

// OPT-IN request timeout. Applied ONLY when a caller passes `timeoutMs` (the
// /health poll + list polls, to detect a frozen-but-connected daemon). NEVER
// blanket-applied: a user-initiated GET like a whole-drive file search or the first
// cold-Ollama semantic search legitimately runs far longer than any poll timeout.
export type ApiInit = RequestInit & { timeoutMs?: number };

export async function api<T>(path: string, init?: ApiInit): Promise<T> {
  const { timeoutMs, ...rest } = init || {};
  const controller = timeoutMs ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...rest,
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(),
        ...(rest.headers || {}),
      },
      cache: "no-store",
      ...(controller ? { signal: controller.signal } : {}),
    });
  } catch {
    // Network error or (opt-in) timeout => daemon offline / not responding.
    throw new ApiError("daemon offline", 0);
  } finally {
    if (timer) clearTimeout(timer);
  }
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) signalAuth(true);
    else if (res.status >= 500) signalError(true); // SERVER error — surface globally
    // (a 4xx like 404/400/422 is a normal per-request condition the page handles —
    //  never flash a global banner for it)
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* ignore */
    }
    throw new ApiError(detail, res.status);
  }
  // A successful DATA response clears the error signals. Skip /health (auth-exempt:
  // it succeeds even with a bad token, so it must not clear an auth error).
  if (path !== "/health") signalAuth(false);
  signalError(false);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const get = <T>(path: string, opts?: { timeoutMs?: number }) => api<T>(path, opts);

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });

export const del = <T>(path: string) => api<T>(path, { method: "DELETE" });
