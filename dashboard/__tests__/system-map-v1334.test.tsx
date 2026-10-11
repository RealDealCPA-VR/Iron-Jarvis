/**
 * v1.334.0 — Everything › Status is a picture: the system map.
 *
 * The user: "If the directory has everything the user will need to access,
 * then the status should simply be a beautiful visual allowing the user to
 * understand everything that is set up, used, in use." These pins hold the
 * picture to the data: every star's state, size, words and door come from
 * GET /ui/system-map, and the layout keeps every star on the canvas, apart
 * from its neighbours, at desktop and phone widths.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: () => {}, refresh: () => {}, prefetch: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/everything",
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));

import {
  Heartbeat,
  SystemMap,
  describe as describeNode,
  layoutMap,
  mapHeight,
  nodeLine,
  nodeRadius,
  nodeState,
  type MapArea,
  type MapNode,
} from "@/components/overview/SystemMap";

afterEach(() => {
  cleanup();
  push.mockReset();
});

const n = (key: string, over: Partial<MapNode> = {}): MapNode => ({
  key,
  label: key[0].toUpperCase() + key.slice(1),
  href: `/${key}`,
  setup: 0,
  used: 0,
  live: 0,
  detail: `About ${key}.`,
  ...over,
});

/** The real app's shape: 27 stars in five constellations. */
function realAreas(): MapArea[] {
  const keys: Record<string, string[]> = {
    work: ["projects", "chats", "agents", "build", "documents", "creative", "templates"],
    automations: ["schedules", "workflows", "goals", "reflexes", "sentinels", "webhooks"],
    system: ["sessions", "activity", "usage", "fleet", "selfdev"],
    knowledge: ["memory", "skills", "artifacts", "you"],
    setup: ["models", "tools", "browser", "channels", "directory"],
  };
  return Object.entries(keys).map(([key, list]) => ({
    key,
    label: key,
    nodes: list.map((k, i) => n(k, { setup: i % 3, used: i % 2 ? 40 : 0, live: i === 1 ? 1 : 0 })),
  }));
}

describe("a star's state is the data's", () => {
  it("off / set up / used / in use, in that order of precedence", () => {
    expect(nodeState(n("a"))).toBe("off");
    expect(nodeState(n("a", { setup: 2 }))).toBe("setup");
    expect(nodeState(n("a", { setup: 2, used: 5 }))).toBe("used");
    expect(nodeState(n("a", { setup: 2, used: 5, live: 1 }))).toBe("live");
    // Used without anything "set up" (activity, usage) is still used.
    expect(nodeState(n("a", { used: 3 }))).toBe("used");
  });

  it("busier stars are bigger, on a log scale with a ceiling", () => {
    const quiet = nodeRadius(n("a", { setup: 1 }));
    const some = nodeRadius(n("a", { setup: 1, used: 8 }));
    const lots = nodeRadius(n("a", { setup: 1, used: 5000 }));
    const absurd = nodeRadius(n("a", { setup: 1, used: 5_000_000 }));
    expect(nodeRadius(n("a"))).toBeLessThan(quiet);
    expect(quiet).toBeLessThan(some);
    expect(some).toBeLessThan(lots);
    expect(absurd).toBe(lots); // capped: one busy part never swallows the map
  });

  it("speaks plainly, attention first", () => {
    expect(nodeLine(n("a"))).toBe("not set up");
    expect(nodeLine(n("a", { setup: 3, unit: "schedule" }))).toBe("3 schedules");
    expect(nodeLine(n("a", { setup: 1, unit: "reflex" }))).toBe("1 reflex");
    expect(nodeLine(n("a", { setup: 2, unit: "reflex" }))).toBe("2 reflexes");
    expect(nodeLine(n("a", { setup: 2, unit: "memory" }))).toBe("2 memories");
    expect(nodeLine(n("a", { used: 1234 }))).toBe("1.2k this week");
    expect(nodeLine(n("a", { setup: 1, used: 4, live: 2 }))).toBe("2 in use now");
    expect(nodeLine(n("a", { setup: 1, live: 2, attention: "boom" }))).toBe("needs you");
    expect(describeNode(n("a", { setup: 2, unit: "pack", used: 1, live: 1 }))).toBe(
      "2 packs · 1 use this week · 1 in use now",
    );
    expect(describeNode(n("a"))).toBe("not set up yet");
  });
});

describe("the layout keeps every star on the canvas and apart", () => {
  for (const w of [1280, 900, 390]) {
    it(`at ${w} px`, () => {
      const h = mapHeight(w);
      const l = layoutMap(realAreas(), w, h);
      expect(l.placed).toHaveLength(27);
      for (const p of l.placed) {
        expect(p.x - p.r, p.node.key).toBeGreaterThanOrEqual(0);
        expect(p.x + p.r, p.node.key).toBeLessThanOrEqual(w);
        expect(p.y - p.r, p.node.key).toBeGreaterThanOrEqual(0);
        expect(p.y + p.r, p.node.key).toBeLessThanOrEqual(h);
        // Outside the reactor.
        expect(Math.hypot(p.x - l.cx, p.y - l.cy) - p.r, p.node.key).toBeGreaterThan(l.core);
      }
      // No two stars touch.
      for (let i = 0; i < l.placed.length; i++)
        for (let j = i + 1; j < l.placed.length; j++) {
          const a = l.placed[i];
          const b = l.placed[j];
          expect(Math.hypot(a.x - b.x, a.y - b.y), `${a.node.key}/${b.node.key}`).toBeGreaterThan(a.r + b.r);
        }
    });
  }

  it("constellations go clockwise from the top in the Directory's order, whatever order they arrive in", () => {
    const l = layoutMap([...realAreas()].reverse(), 1280, mapHeight(1280));
    expect(l.arcs.map((a) => a.area)).toEqual(["work", "automations", "system", "knowledge", "setup"]);
    // Each arc counts its stars that exist.
    expect(l.arcs[0]).toMatchObject({ total: 7 });
  });
});

describe("the map renders the data, and every star is a door", () => {
  const data = {
    areas: [
      {
        key: "automations",
        label: "Automations",
        nodes: [
          n("schedules", { label: "Schedules", setup: 2, used: 3, unit: "schedule", attention: "1 schedule failed on the last run" }),
          n("webhooks", { label: "Webhooks" }),
        ],
      },
      { key: "work", label: "Work", nodes: [n("agents", { label: "Agents", setup: 9, used: 2, live: 1 })] },
    ],
  };

  it("each star wears its state and says it to a screen reader", () => {
    render(<SystemMap data={data} />);
    expect(screen.getByTestId("system-map-node-webhooks").getAttribute("data-state")).toBe("off");
    expect(screen.getByTestId("system-map-node-agents").getAttribute("data-state")).toBe("live");
    const sched = screen.getByTestId("system-map-node-schedules");
    expect(sched.getAttribute("data-attention")).toBe("1");
    expect(sched.getAttribute("aria-label")).toContain("needs you — 1 schedule failed on the last run");
    const list = screen.getByTestId("system-map-list");
    expect(list.textContent).toContain("Work — Agents: 9 set up · 2 uses this week · 1 in use now");
    expect(list.textContent).toContain("Automations — Webhooks: not set up yet");
    // The picture's title sums it up.
    expect(document.querySelector("title")?.textContent).toBe(
      "Iron Jarvis system map: 2 of 3 parts set up, 2 used this week, 1 in use now",
    );
  });

  it("hovering a star opens its card with the reason and the facts; leaving closes it", () => {
    render(<SystemMap data={data} />);
    const sched = screen.getByTestId("system-map-node-schedules");
    fireEvent.mouseEnter(sched);
    const card = screen.getByTestId("system-map-card");
    expect(within(card).getByText("Schedules")).toBeTruthy();
    expect(within(card).getByText("Needs you")).toBeTruthy();
    expect(card.textContent).toContain("1 schedule failed on the last run");
    expect(card.textContent).toContain("2 schedules · 3 uses this week");
    // The others step back.
    expect(screen.getByTestId("system-map-node-agents").getAttribute("data-dim")).toBe("1");
    fireEvent.mouseLeave(sched);
    expect(screen.queryByTestId("system-map-card")).toBeNull();
  });

  it("keyboard focus opens the same card", () => {
    render(<SystemMap data={data} />);
    fireEvent.focus(screen.getByTestId("system-map-node-agents"));
    expect(screen.getByTestId("system-map-card").textContent).toContain("In use now");
  });

  it("a click opens the star's page in the app; a ctrl-click is left to the browser", () => {
    render(<SystemMap data={data} />);
    const star = screen.getByTestId("system-map-node-schedules");
    expect(star.getAttribute("href")).toBe("/schedules");
    fireEvent.click(star, { button: 0, ctrlKey: true });
    expect(push).not.toHaveBeenCalled();
    fireEvent.click(star, { button: 0 });
    expect(push).toHaveBeenCalledWith("/schedules");
  });
});

describe("the heartbeat", () => {
  it("names the day's total and the busiest hour", () => {
    const hours = Array.from({ length: 24 }, (_, i) => (i === 20 ? 30 : 1));
    render(<Heartbeat hours={hours} />);
    expect(screen.getByTestId("system-heartbeat").textContent).toContain("53 things happened · busiest 3h ago");
  });

  it("a quiet day says so", () => {
    render(<Heartbeat hours={new Array(24).fill(0)} />);
    expect(screen.getByTestId("system-heartbeat").textContent).toContain("A quiet day so far");
  });
});
