/**
 * v1.387.0 review — a reply cut off at the model's OUTPUT limit is said on
 * the reply's receipt (the daemon's `route.cut_off: {times, recovered}`,
 * both chat lanes, LAST on the route, only when it happened). Before the
 * review it was carried on the route and rendered NOWHERE: a turn that the
 * daemon quietly recovered (ran nothing from the cut reply, asked the model
 * to write the file in parts) looked exactly like one that went straight
 * through. Through the REAL TurnReceipt and the REAL stream decode.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { TurnReceipt } from "@/components/chat/TurnReceipt";
import { decodeSSE } from "@/lib/useChatStream";
import {
  CUT_OFF_END_TITLE,
  CUT_OFF_TITLE,
  cutOffLine,
  cutOffTitle,
  decodeCutOff,
} from "@/lib/turnRecovery";

afterEach(() => cleanup());

const ROUTE = { requested: "", provider: "custom", model: "glm-5.3-flash", reason: "default" };

describe("the receipt says a reply was cut off at the output limit", () => {
  it("says it on the collapsed line, quiet, with why on hover", () => {
    render(<TurnReceipt inline route={{ ...ROUTE, cut_off: { times: 1, recovered: true } }} />);
    const part = screen.getByTestId("turn-cut-off");
    expect(part.textContent).toBe("Cut off at the output limit, picked up again");
    expect(part.getAttribute("title")).toBe(CUT_OFF_TITLE);
    expect(part.className).toContain("text-zinc-500");
    expect(part.className).not.toMatch(/tone-warn|border|rounded/);
    expect(screen.queryByTestId("turn-receipt-detail")).toBeNull();
  });

  it("a phone (no hover) reads why in the expanded receipt", () => {
    render(<TurnReceipt inline route={{ ...ROUTE, cut_off: { times: 2, recovered: true } }} />);
    expect(screen.getByTestId("turn-cut-off").textContent).toBe(
      "Cut off at the output limit 2 times, picked up again",
    );
    fireEvent.click(screen.getByTestId("turn-receipt"));
    expect(screen.getByTestId("turn-cut-off-detail").textContent).toBe(CUT_OFF_TITLE);
  });

  it("a turn that ENDED on a cut never says it was picked up again", () => {
    render(<TurnReceipt inline route={{ ...ROUTE, cut_off: { times: 3, recovered: false } }} />);
    const part = screen.getByTestId("turn-cut-off");
    expect(part.textContent).toBe("Cut off at the output limit 3 times");
    expect(part.textContent).not.toMatch(/picked up/);
    expect(part.getAttribute("title")).toBe(CUT_OFF_END_TITLE);
  });

  it("nothing is said when nothing was cut, or the field is not the daemon's shape", () => {
    for (const cut of [undefined, null, {}, { times: 0, recovered: true }, { times: "2" }, "yes"]) {
      cleanup();
      render(<TurnReceipt inline route={{ ...ROUTE, cut_off: cut as never }} />);
      expect(screen.queryByTestId("turn-cut-off")).toBeNull();
    }
    expect(decodeCutOff({ times: 2.7, recovered: "yes" })).toEqual({ times: 2, recovered: false });
    expect(cutOffLine(null)).toBeNull();
    expect(cutOffTitle({ times: 1, recovered: false })).toBe(CUT_OFF_END_TITLE);
  });

  it("the daemon's done frame carries it through the stream decode", () => {
    const ev = decodeSSE(
      "done",
      JSON.stringify({ reply: "ok", route: { ...ROUTE, cut_off: { times: 1, recovered: true } } }),
    ) as { type: string; route?: { cut_off?: unknown } } | null;
    expect(ev?.type).toBe("done");
    expect(decodeCutOff(ev?.route?.cut_off)).toEqual({ times: 1, recovered: true });
  });
});
