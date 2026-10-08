/**
 * This window's DEVICE id (calm UI redesign S2/S3): per-device settings — the
 * theme, the layout — are stored by the daemon against it, so a change made
 * from chat on the phone lands on the phone, not on the desktop.
 *
 * Minted once per browser profile and kept in localStorage; when storage is
 * unavailable (a private window) the id lives for the page only, which is the
 * honest scope of anything set from there.
 */

const KEY = "ij_device_id";
let memo = "";

function mint(): string {
  const c = globalThis.crypto as Crypto | undefined;
  const raw =
    c && typeof c.randomUUID === "function"
      ? c.randomUUID().replace(/-/g, "")
      : Math.random().toString(36).slice(2) + Date.now().toString(36);
  return `dev_${raw.slice(0, 16)}`;
}

export function getDeviceId(): string {
  if (memo) return memo;
  try {
    const held = window.localStorage.getItem(KEY);
    if (held && /^dev_[A-Za-z0-9]{6,32}$/.test(held)) {
      memo = held;
      return memo;
    }
    memo = mint();
    window.localStorage.setItem(KEY, memo);
  } catch {
    memo = memo || mint();
  }
  return memo;
}
