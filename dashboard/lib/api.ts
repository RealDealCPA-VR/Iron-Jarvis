// Tiny runtime API client. All calls happen in the browser ('use client'),
// so `next build` never touches the daemon.

export const API_BASE = (
  process.env.NEXT_PUBLIC_IJ_API || "http://127.0.0.1:8787"
).replace(/\/$/, "");

export function wsUrl(path: string): string {
  return API_BASE.replace(/^http/, "ws") + path;
}

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
