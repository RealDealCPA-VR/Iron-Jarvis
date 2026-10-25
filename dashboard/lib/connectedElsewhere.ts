/**
 * Readiness 12B (G15): connecting the app SOMEWHERE ELSE lets the work go on.
 *
 * v1.351.0 put a Continue on the chat's own key card: save the token there,
 * "GitHub connected · 26 tools", Continue sends the question on. But the chat
 * also offers "Connect <App> in Settings" (a door under the reply), and the
 * user may connect the app on the Directory, finish an OAuth sign-in in the
 * browser, or save a Pixio key in Settings › Connections — and come back to a
 * chat that never noticed: no Continue, the question left behind.
 *
 * This module reads, from the NEWEST reply alone, which apps the chat offered
 * to connect — its Connect door (the daemon's own words,
 * `daemon/doors.connect_label`, to `_APPS_FIX_HREF`), and an app's key card
 * from `app_connect` that was not saved here (or saved but did not start) —
 * and matches them against the Directory's own list (`GET /connectors`: the
 * `status` every card shows, "connected" = it works). The page reads that
 * list when the offer appears, when its window gets focus back and when it
 * becomes visible — never on a timer — and shows the SAME Continue the card
 * has. Nothing is ever sent by itself: a press continues.
 *
 * Dependency-free (no lib/api — ~70 test files mock it wholesale).
 */

/** The Directory's address every Connect door uses (`daemon/doors._APPS_FIX_HREF`). */
export const APPS_DOOR_HREF = "/settings?section=connections-apps";

/** "Connect <names> in Settings" (`daemon/doors.connect_label`). */
const DOOR_RE = /^Connect (.+) in Settings$/;

/** An app the chat offered to connect: by its name (a door says names), its
 *  catalog id (an app's key card, `app.<id>`) or its provider (a key card
 *  for a service, `connection.<provider>`). */
export interface OfferedApp {
  name?: string;
  id?: string;
  provider?: string;
}

/** The offered app the Directory now reads as connected. */
export interface ConnectedNow {
  /** The connection's id (for an extension, its saved name — the chat's
   *  connector switch takes it). */
  app: string;
  /** Its name, as the card shows it. */
  name: string;
  /** Tools it loaded (0 for an app that signs in or takes a key). */
  tools: number;
  /** An extension (its tools are turned on for the chat on Continue). */
  mcp: boolean;
}

interface DoorLike {
  href?: unknown;
  label?: unknown;
}

interface CardLike {
  kind?: unknown;
  name?: unknown;
  saved?: unknown;
  undone?: unknown;
  connected?: { tools?: unknown; continued?: unknown } | unknown;
}

interface MessageLike {
  role?: string;
  doors?: unknown;
  configCards?: unknown;
  toolsUsed?: unknown;
  continuation?: boolean;
}

const str = (v: unknown): string => (typeof v === "string" ? v : "");

/** The app names a Connect door's words carry ("Connect Dropbox and Google
 *  Drive in Settings" → ["Dropbox", "Google Drive"]; a choice "Gmail or
 *  Microsoft 365" → both). "N more apps" and "your apps" name nothing. */
export function doorAppNames(label: string): string[] {
  const m = DOOR_RE.exec(str(label).trim());
  if (!m) return [];
  const out: string[] = [];
  for (const part of m[1].split(/, | and | or /)) {
    const name = part.trim();
    if (!name || /^\d+ more apps?$/i.test(name) || /^your apps$/i.test(name)) continue;
    if (!out.includes(name)) out.push(name);
  }
  return out;
}

/** `app.github` / `app.box__BOX_CLIENT_ID` → {id: "github"}; `connection.pixio`
 *  → {provider: "pixio"}; anything else → null. */
function cardApp(name: string): OfferedApp | null {
  const app = /^app\.([A-Za-z0-9_-]{1,64})(?:__|$)/.exec(name);
  if (app) return { id: app[1] };
  const conn = /^connection\.([A-Za-z0-9_-]{1,64})$/.exec(name);
  if (conn) return { provider: conn[1] };
  return null;
}

/** The apps message `m` offered to connect. A key card counts only when the
 *  turn ran `app_connect` (the tool that shows it for an app that is not
 *  connected) and it was not saved here — or was saved and did not start
 *  (fixing it on the Directory is "connecting it elsewhere"). */
export function offeredApps(m: MessageLike | null | undefined): OfferedApp[] {
  if (!m || m.role !== "assistant") return [];
  const out: OfferedApp[] = [];
  const add = (a: OfferedApp) => {
    if (!out.some((o) => o.name === a.name && o.id === a.id && o.provider === a.provider)) out.push(a);
  };
  for (const d of Array.isArray(m.doors) ? (m.doors as DoorLike[]) : []) {
    if (!d || str(d.href) !== APPS_DOOR_HREF) continue;
    for (const name of doorAppNames(str(d.label))) add({ name });
  }
  const ranConnect = Array.isArray(m.toolsUsed) && (m.toolsUsed as unknown[]).includes("app_connect");
  if (ranConnect) {
    for (const c of Array.isArray(m.configCards) ? (m.configCards as CardLike[]) : []) {
      if (!c || c.kind !== "secret" || c.undone) continue;
      const conn = (c.connected ?? null) as { tools?: unknown; continued?: unknown } | null;
      const savedAndWorking = !!c.saved && !(conn && Number(conn.tools ?? 0) <= 0 && !conn.continued);
      if (savedAndWorking) continue;
      const app = cardApp(str(c.name));
      if (app) add(app);
    }
  }
  return out;
}

/** The newest message's offer, when the conversation ENDS on it (a later
 *  message means the work already went on). `key` changes only when the offer
 *  does, so a page re-reads the Directory once per offer, not per render. */
export function connectOffer(
  messages: readonly MessageLike[],
): { index: number; apps: OfferedApp[]; key: string } | null {
  const index = messages.length - 1;
  if (index < 0) return null;
  const apps = offeredApps(messages[index]);
  if (apps.length === 0) return null;
  const key = `${index}:${apps.map((a) => `${a.name ?? ""}|${a.id ?? ""}|${a.provider ?? ""}`).join(",")}`;
  return { index, apps, key };
}

interface RowLike {
  id?: unknown;
  catalog_id?: unknown;
  name?: unknown;
  provider?: unknown;
  connect_via?: unknown;
  status?: unknown;
  enabled?: unknown;
  tools_loaded?: unknown;
}

function matches(app: OfferedApp, row: RowLike): boolean {
  if (app.id) return str(row.id) === app.id || str(row.catalog_id) === app.id;
  if (app.provider) return str(row.provider) === app.provider && str(row.connect_via) !== "mcp";
  if (app.name) return str(row.name).toLowerCase() === app.name.toLowerCase();
  return false;
}

/** The first offered app whose Directory card now reads "connected" (it
 *  works — an extension's tools loaded and signed in, an app signed in, a
 *  key that was not refused) and is switched on; null when none. The
 *  Directory lists its own catalog first, so a catalog card wins a name a
 *  user's own extension shares. Never throws. */
export function nowConnected(apps: readonly OfferedApp[], rows: unknown): ConnectedNow | null {
  if (!Array.isArray(rows)) return null;
  for (const app of apps) {
    for (const raw of rows as RowLike[]) {
      if (!raw || typeof raw !== "object" || !matches(app, raw)) continue;
      if (str(raw.status) !== "connected" || raw.enabled === false) break;
      const id = str(raw.id);
      if (!id) break;
      const tools = typeof raw.tools_loaded === "number" && raw.tools_loaded > 0 ? Math.floor(raw.tools_loaded) : 0;
      return { app: id, name: str(raw.name) || app.name || id, tools, mcp: str(raw.connect_via) === "mcp" };
    }
  }
  return null;
}

/** "GitHub connected · 26 tools" / "Google Drive connected". */
export function connectedNowWords(c: ConnectedNow): string {
  return c.tools > 0 ? `${c.name} connected · ${c.tools} ${c.tools === 1 ? "tool" : "tools"}` : `${c.name} connected`;
}
