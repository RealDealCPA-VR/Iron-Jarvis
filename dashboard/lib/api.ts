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

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(),
        ...(init?.headers || {}),
      },
      cache: "no-store",
    });
  } catch {
    // Network error => daemon almost certainly offline.
    throw new ApiError("daemon offline", 0);
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* ignore */
    }
    throw new ApiError(detail, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const get = <T>(path: string) => api<T>(path);

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });

export const del = <T>(path: string) => api<T>(path, { method: "DELETE" });
