"use client";

/**
 * The Settings page's "Daemon access token" card (v1.232.0, audit Wave 6).
 *
 * Lifted out of app/settings/page.tsx so it can be mounted on its own. The
 * fix it carries: the packaged desktop app ALWAYS seeds this token
 * (desktop/preload.js writes localStorage["ij_token"] before the bundle
 * runs), so the box that said "Local installs usually need no token — leave
 * this empty" was wrong for every daily-driver user, and its Clear button was
 * a lever that would 401 the app until the next reload re-seeded it. Inside
 * the desktop shell the card is read-only and says who set it; the paste box
 * and the "leave this empty" line belong to a browser WITHOUT the bridge (a
 * deployed daemon, a phone over Tailscale).
 */

import { useEffect, useState } from "react";
import { KeyRound, ShieldCheck, Trash2 } from "lucide-react";
import { ijToken, setIjToken } from "@/lib/api";
import { isDesktopShell } from "@/lib/desktopShell";
import { Card, SectionLabel, SuccessNote } from "@/components/ui";

export function DaemonTokenCard() {
  // Token box (lives in localStorage, applies without a rebuild).
  const [token, setToken] = useState("");
  const [tokenNote, setTokenNote] = useState<string | null>(null);
  // Resolved after mount: `window` is absent during SSR, and the desktop
  // preload has written the token before any effect runs.
  const [seededByDesktop, setSeededByDesktop] = useState(false);

  useEffect(() => {
    const t = ijToken();
    setToken(t);
    setSeededByDesktop(isDesktopShell() && t.trim().length > 0);
  }, []);

  function saveToken() {
    setIjToken(token);
    setTokenNote(token.trim() ? "Token saved — it's now sent with every request." : "Token cleared.");
  }

  function clearToken() {
    setIjToken("");
    setToken("");
    setTokenNote("Token cleared.");
  }

  if (seededByDesktop) {
    return (
      <Card title="Daemon access token" icon={<KeyRound size={15} />}>
        <div className="space-y-3.5">
          <div>
            <SectionLabel>Token</SectionLabel>
            <input
              type="password"
              value={token}
              readOnly
              aria-label="Daemon access token (set by the desktop app)"
              className="field mt-1.5 font-mono text-[13px] opacity-70"
            />
          </div>
          <p className="text-[12px] leading-relaxed text-zinc-400" data-testid="token-seeded">
            Set by the desktop app. It pairs this window with its own daemon and is
            renewed on every launch — there is nothing to paste or clear here.
          </p>
        </div>
      </Card>
    );
  }

  return (
    <Card title="Daemon access token" icon={<KeyRound size={15} />}>
      <div className="space-y-3.5">
        <p className="text-[12px] leading-relaxed text-zinc-500">
          If the daemon is protected with a bearer token, paste it here. It&apos;s stored in
          your browser and sent with every request — so you can log into a deployed instance
          without a rebuild.
        </p>
        <div>
          <SectionLabel>Token</SectionLabel>
          <input
            type="password"
            value={token}
            onChange={(e) => {
              setToken(e.target.value);
              setTokenNote(null);
            }}
            placeholder="paste IRONJARVIS_TOKEN"
            autoComplete="off"
            className="field mt-1.5 font-mono text-[13px]"
          />
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={saveToken} className="btn-accent flex-1 py-1.5 text-xs">
            <ShieldCheck size={14} /> Save token
          </button>
          <button
            type="button"
            onClick={clearToken}
            className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-white/10 px-3 py-1.5 text-xs font-medium text-zinc-400 transition-colors hover:border-rose-500/40 hover:text-rose-300"
          >
            <Trash2 size={14} /> Clear
          </button>
        </div>
        {tokenNote && <SuccessNote>{tokenNote}</SuccessNote>}
        <p className="text-[11px] text-zinc-600">
          A daemon started by hand on this machine usually needs no token — leave this
          empty. The desktop app sets its own.
        </p>
      </div>
    </Card>
  );
}
