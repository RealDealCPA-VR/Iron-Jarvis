/**
 * Readiness 12B review (G15): a Connect door is read by the REAL words the
 * daemon writes — a catalog name may hold ", " / " and " / " or " itself.
 *
 * Microsoft 365's card is "Microsoft 365 (Outlook, Calendar, OneDrive)". The
 * lane's reader split the door's names at every separator, so the door for
 * "what's on my outlook calendar" read "Microsoft 365 (Outlook", "Calendar",
 * "OneDrive)": connecting Microsoft 365 elsewhere never offered Continue, and
 * a user's own extension named "Calendar" would have been taken for it. Its
 * pin used an invented label ("Connect Gmail or Microsoft 365 in Settings").
 *
 * Fed with the daemon's own door labels and Directory names
 * (fixtures/connected-elsewhere-doors-v1387.json, written by the REAL stream
 * lane in tests/test_connected_elsewhere_doors_v1387.py).
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { APPS_DOOR_HREF, connectOffer, nowConnected } from "@/lib/connectedElsewhere";

type Row = { id: string; name: string; connect_via: string; status: string };
const FIX = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "connected-elsewhere-doors-v1387.json"), "utf8").replace(/\r\n/g, "\n"),
) as { doors: Record<string, string>; rows: Record<string, Row> };

const reply = (label: string) => [
  { role: "user" },
  { role: "assistant", doors: [{ href: APPS_DOOR_HREF, label }] },
];
const offerFor = (label: string) => connectOffer(reply(label))!.apps;
/** The Directory's list, every row as the daemon answers it, one connected. */
function rows(connected: string, extra: Record<string, unknown>[] = []): Record<string, unknown>[] {
  return [
    ...Object.values(FIX.rows).map((r) => (r.id === connected ? { ...r, status: "connected", tools_loaded: 24 } : r)),
    ...extra,
  ];
}

describe("a door whose catalog name holds separators", () => {
  it("Microsoft 365 connected elsewhere answers its own door", () => {
    const hit = nowConnected(offerFor(FIX.doors.outlook), rows("microsoft_365"));
    expect(hit).toEqual({ app: "microsoft_365", name: FIX.rows.microsoft_365.name, tools: 24, mcp: true });
  });

  it("the mail choice: either app, connected elsewhere, answers it", () => {
    expect(nowConnected(offerFor(FIX.doors.inbox), rows("microsoft_365"))?.app).toBe("microsoft_365");
    expect(nowConnected(offerFor(FIX.doors.inbox), rows("gmail"))?.app).toBe("gmail");
    expect(nowConnected(offerFor(FIX.doors.inbox), rows("notion"))).toBeNull();
  });

  it("a part of a name is never an app: a user's own 'Calendar' does not answer Microsoft 365's door", () => {
    const own = { id: "calendar", name: "Calendar", connect_via: "mcp", status: "connected", tools_loaded: 3 };
    const own2 = { id: "onedrive-mine", name: "OneDrive)", connect_via: "mcp", status: "connected", tools_loaded: 1 };
    expect(nowConnected(offerFor(FIX.doors.outlook), rows("", [own, own2]))).toBeNull();
  });

  it("plain lists still read app by app", () => {
    expect(nowConnected(offerFor(FIX.doors.three), rows("dropbox"))?.app).toBe("dropbox");
    expect(nowConnected(offerFor(FIX.doors.three), rows("google_drive"))?.app).toBe("google_drive");
    expect(nowConnected(offerFor(FIX.doors.three), rows("onedrive"))).toBeNull();
  });

  it("a name that only STARTS an item is not that item", () => {
    // The Directory list as an older daemon (or a filtered read) may hand it:
    // no Google Drive card, and the user's own extension called "Google".
    const own = { id: "google-mine", name: "Google", connect_via: "mcp", status: "connected", tools_loaded: 2 };
    expect(nowConnected(offerFor("Connect Google Drive in Settings"), [own])).toBeNull();
    // Nor does a name reach ACROSS a separator into the next item.
    const across = { id: "x", name: "Notion, Google", connect_via: "mcp", status: "connected", tools_loaded: 1 };
    expect(nowConnected(offerFor(FIX.doors.three), [across])).toBeNull();
  });

  it("the LONGEST Directory name fills an item, whatever order the list comes in", () => {
    // A catalog name that holds " and " (a future card) beside the user's own
    // extension named after its first word, listed FIRST.
    const own = { id: "files-mine", name: "Files", connect_via: "mcp", status: "connected", tools_loaded: 1 };
    const card = { id: "files_folders", name: "Files and folders", connect_via: "mcp", status: "connected", tools_loaded: 14 };
    expect(nowConnected(offerFor("Connect Files and folders in Settings"), [own, card])?.app).toBe("files_folders");
  });
});
