// v1.490.5: `app_connect` with a calendar shows the calendar link's secure
// card. A calendar is not one of the chat's app switches, so a finished
// calendar call must never be "turned on for this chat" like an app.
import { describe, expect, it } from "vitest";

import { appConnectIds, namesCalendar, withConnectors } from "@/lib/appConnect";

const call = (app: string, ok = true) => ({ name: "app_connect", ok, status: "done", args: { app } });

describe("a calendar connected through app_connect", () => {
  it("knows the calendar by the daemon's own rule", () => {
    for (const ref of ["calendar", "google_calendar", "Outlook-Calendar", "ical", "ICS", "agenda", " calendar "]) {
      expect(namesCalendar(ref), ref).toBe(true);
    }
    for (const ref of ["notion", "medical_records", "topical", "github", ""]) {
      expect(namesCalendar(ref), ref).toBe(false);
    }
  });

  it("is never turned on as an app for the chat", () => {
    expect(appConnectIds([call("calendar"), call("google_calendar"), call("ical")])).toEqual([]);
    // Real apps beside it still are.
    expect(appConnectIds([call("google_calendar"), call("notion"), call("github")])).toEqual(["notion", "github"]);
    const before = ["slack"];
    expect(withConnectors(before, appConnectIds([call("calendar")]), 8)).toBe(before);
  });

  it("leaves every other rule of the list as it was", () => {
    expect(appConnectIds([call("notion", false)])).toEqual([]);
    expect(appConnectIds([{ ...call("notion"), status: "running" }])).toEqual([]);
    expect(appConnectIds([call("no spaces allowed")])).toEqual([]);
    expect(appConnectIds([call("notion"), call("notion")])).toEqual(["notion"]);
  });
});
