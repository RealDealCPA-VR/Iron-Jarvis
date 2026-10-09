/**
 * v1.325.0 (wave D, FE-D1): the ```chart fence.
 *
 * - `parseChartSpec` is STRICT: anything the reader would have to guess at is
 *   null, and null means the ordinary code block (the user still sees what was
 *   written).
 * - `chartToCsv` is RFC 4180 with the spreadsheet formula guard.
 * - `ChartCard` draws its own SVG in THEME colours, says what it shows to a
 *   screen reader, flips to a table, copies its numbers, and shows a value on
 *   hover.
 * - `Markdown` routes a ```chart fence that parses to the card and anything
 *   else (half-written JSON while streaming included) to the code block; the
 *   draft fence still becomes the draft card.
 */
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({ API_BASE: "http://127.0.0.1:8787", ijToken: () => "" }));

import {
  CHART_FENCE,
  CHART_TYPES,
  chartToCsv,
  niceTicks,
  parseChartSpec,
  type ChartSpec,
} from "@/lib/chartSpec";
import { ChartCard, chartSummary, formatValue, seriesColor } from "@/components/chat/ChartCard";
import { Markdown } from "@/components/Markdown";

const BAR = {
  type: "bar",
  title: "Revenue by quarter",
  unit: "USD",
  labels: ["Q1", "Q2", "Q3"],
  series: [
    { name: "Revenue", values: [1200, 1500.5, 900] },
    { name: "Cost", values: [800, 700, -100] },
  ],
};

function spec(over: Record<string, unknown> = {}): string {
  return JSON.stringify({ ...BAR, ...over });
}

describe("chart vocabulary (the agreement with CHART_BLOCK)", () => {
  it("names the fence and the three types", () => {
    expect(CHART_FENCE).toBe("chart");
    expect([...CHART_TYPES]).toEqual(["bar", "line", "pie"]);
  });
});

describe("parseChartSpec — strict", () => {
  it("accepts a well-formed bar, line and pie", () => {
    const bar = parseChartSpec(spec());
    expect(bar).toEqual({
      type: "bar",
      title: "Revenue by quarter",
      unit: "USD",
      labels: ["Q1", "Q2", "Q3"],
      series: [
        { name: "Revenue", values: [1200, 1500.5, 900] },
        { name: "Cost", values: [800, 700, -100] },
      ],
    });
    expect(parseChartSpec(spec({ type: "line" }))?.type).toBe("line");
    const pie = parseChartSpec(
      JSON.stringify({ type: "pie", labels: ["A", "B"], series: [{ name: "Share", values: [3, 1] }] }),
    );
    expect(pie?.type).toBe("pie");
    expect(pie?.title).toBeUndefined();
  });

  it("tolerates surrounding whitespace (a fence body ends in a newline)", () => {
    expect(parseChartSpec(`\n  ${spec()}  \n`)).not.toBeNull();
  });

  const bad: [string, string][] = [
    ["not JSON", "{ type: bar }"],
    ["half-written (still streaming)", spec().slice(0, 60)],
    ["an array", "[1,2,3]"],
    ["null", "null"],
    ["unknown type", spec({ type: "scatter" })],
    ["missing type", JSON.stringify({ labels: ["a"], series: [{ name: "x", values: [1] }] })],
    ["unknown top-level key", spec({ colors: ["red"] })],
    ["unknown series key", spec({ series: [{ name: "x", values: [1, 2, 3], color: "red" }] })],
    ["numbers as strings", spec({ series: [{ name: "x", values: ["1,234", "2", "3"] }] })],
    ["a numeric string", spec({ series: [{ name: "x", values: [1, "2", 3] }] })],
    ["null value", spec({ series: [{ name: "x", values: [1, null, 3] }] })],
    ["values shorter than labels", spec({ series: [{ name: "x", values: [1, 2] }] })],
    ["values longer than labels", spec({ series: [{ name: "x", values: [1, 2, 3, 4] }] })],
    ["no labels", spec({ labels: [], series: [{ name: "x", values: [] }] })],
    ["51 labels", JSON.stringify({ type: "bar", labels: Array.from({ length: 51 }, (_, i) => `L${i}`), series: [{ name: "x", values: Array.from({ length: 51 }, () => 1) }] })],
    ["a non-string label", spec({ labels: ["Q1", 2, "Q3"] })],
    ["no series", spec({ series: [] })],
    ["7 series", spec({ series: Array.from({ length: 7 }, (_, i) => ({ name: `s${i}`, values: [1, 2, 3] })) })],
    ["series name not a string", spec({ series: [{ name: 5, values: [1, 2, 3] }] })],
    ["title too long", spec({ title: "x".repeat(121) })],
    ["title not a string", spec({ title: 5 })],
    ["unit not a string", spec({ unit: 1 })],
    ["pie with two series", JSON.stringify({ type: "pie", labels: ["a", "b"], series: [{ name: "x", values: [1, 2] }, { name: "y", values: [1, 2] }] })],
    ["pie with a negative slice", JSON.stringify({ type: "pie", labels: ["a", "b"], series: [{ name: "x", values: [3, -1] }] })],
    ["pie of all zeros", JSON.stringify({ type: "pie", labels: ["a", "b"], series: [{ name: "x", values: [0, 0] }] })],
  ];
  it.each(bad)("refuses %s", (_why, raw) => {
    expect(parseChartSpec(raw)).toBeNull();
  });

  it("refuses NaN / Infinity smuggled past JSON (1e999)", () => {
    expect(parseChartSpec(spec({ series: [{ name: "x", values: [1, 1e999, 3] }] }).replace("null", "1e999"))).toBeNull();
    expect(parseChartSpec('{"type":"bar","labels":["a"],"series":[{"name":"x","values":[1e999]}]}')).toBeNull();
  });

  it("accepts exactly 50 labels and 6 series and a 120-char title", () => {
    const n = 50;
    const raw = JSON.stringify({
      type: "line",
      title: "t".repeat(120),
      labels: Array.from({ length: n }, (_, i) => `L${i}`),
      series: Array.from({ length: 6 }, (_, j) => ({ name: `s${j}`, values: Array.from({ length: n }, (_, i) => i * j) })),
    });
    expect(parseChartSpec(raw)?.series).toHaveLength(6);
  });
});

describe("chartToCsv", () => {
  it("writes one row per label with a Label column, CRLF between records", () => {
    const s = parseChartSpec(spec())!;
    expect(chartToCsv(s)).toBe("Label,Revenue,Cost\r\nQ1,1200,800\r\nQ2,1500.5,700\r\nQ3,900,-100");
  });

  it("quotes per RFC 4180 and guards formulas in text cells", () => {
    const s: ChartSpec = {
      type: "bar",
      labels: ['say "hi"', "a,b", "=HYPERLINK(1)", "@cmd", "+x", "line\nbreak"],
      series: [{ name: "-risky", values: [1, 2, 3, 4, 5, -6] }],
    };
    const csv = chartToCsv(s);
    const lines = csv.split("\r\n");
    expect(lines[0]).toBe("Label,'-risky");
    expect(lines[1]).toBe('"say ""hi""",1');
    expect(lines[2]).toBe('"a,b",2');
    expect(lines[3]).toBe("'=HYPERLINK(1),3");
    expect(lines[4]).toBe("'@cmd,4");
    expect(lines[5]).toBe("'+x,5");
    // A negative NUMBER is a number, never apostrophe-guarded.
    expect(csv.endsWith('"line\nbreak",-6')).toBe(true);
  });
});

describe("niceTicks", () => {
  it("rounds to 1/2/2.5/5 steps covering the range", () => {
    expect(niceTicks(0, 1500.5)).toEqual([0, 500, 1000, 1500, 2000]);
    expect(niceTicks(-100, 1500)).toEqual([-500, 0, 500, 1000, 1500]);
    expect(niceTicks(0, 1)).toEqual([0, 0.2, 0.4, 0.6, 0.8, 1]);
    expect(niceTicks(0.1, 0.3)).toEqual([0.1, 0.15, 0.2, 0.25, 0.3]);
  });
  it("gives a flat range some height", () => {
    expect(niceTicks(0, 0)).toEqual([0, 1]);
    const t = niceTicks(5, 5);
    expect(t[0]).toBeLessThan(5);
    expect(t[t.length - 1]).toBeGreaterThan(5);
  });
});

describe("ChartCard", () => {
  let writeText: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  });
  afterEach(() => vi.useRealTimers());

  it("is an accessible image with a plain-words summary", () => {
    render(<ChartCard spec={parseChartSpec(spec())!} />);
    const img = screen.getByRole("img");
    expect(img.tagName.toLowerCase()).toBe("svg");
    const label = img.getAttribute("aria-label")!;
    expect(label).toContain("Bar chart: Revenue by quarter");
    expect(label).toContain("Revenue, Cost");
    expect(label).toContain("Q1 to Q3");
    expect(label).toContain("-100 USD");
    expect(label).toContain("1,500.5 USD");
    expect(screen.getByText("Revenue by quarter")).toBeInTheDocument();
  });

  it("fits a phone: viewBox + width 100%", () => {
    render(<ChartCard spec={parseChartSpec(spec())!} />);
    const svg = screen.getByTestId("chart-svg");
    expect(svg.getAttribute("viewBox")).toMatch(/^0 0 \d+ \d+$/);
    expect(svg.getAttribute("width")).toBe("100%");
  });

  it("draws a grouped bar per value and a legend only with more than one series", () => {
    const { unmount } = render(<ChartCard spec={parseChartSpec(spec())!} />);
    expect(screen.getAllByTestId("chart-bar")).toHaveLength(6);
    expect(screen.getByTestId("chart-legend")).toHaveTextContent("Revenue");
    unmount();
    render(<ChartCard spec={parseChartSpec(spec({ series: [{ name: "Revenue", values: [1, 2, 3] }] }))!} />);
    expect(screen.queryByTestId("chart-legend")).toBeNull();
  });

  it("colours every mark from the theme tokens, never a literal colour", () => {
    const { container } = render(<ChartCard spec={parseChartSpec(spec({ type: "line" }))!} />);
    const marks = container.querySelectorAll("polyline, circle, rect, line, text");
    expect(marks.length).toBeGreaterThan(5);
    for (const el of Array.from(marks)) {
      const style = (el as SVGElement).getAttribute("style") || "";
      const fill = el.getAttribute("fill") || "";
      expect(style + fill).not.toMatch(/#[0-9a-f]{3,8}\b|rgb\(\s*\d/i);
    }
    const line = container.querySelector("polyline") as SVGElement;
    expect(line.getAttribute("style")).toContain("var(--accent-rgb)");
    expect(seriesColor(1)).toBe("rgb(var(--tone-violet))");
    // A seventh slice reuses the first token, dimmer.
    expect(seriesColor(6)).toMatch(/^rgb\(var\(--accent-rgb\) \/ 0\.\d+\)$/);
  });

  it("shows a bar's value on hover", () => {
    render(<ChartCard spec={parseChartSpec(spec())!} />);
    const bars = screen.getAllByTestId("chart-bar");
    // Series 0's bars come first: Q2's Revenue bar is index 1.
    fireEvent.mouseEnter(bars[1]);
    expect(screen.getByTestId("chart-readout")).toHaveTextContent("Q2 — Revenue: 1,500.5 USD");
    fireEvent.mouseLeave(screen.getByTestId("chart-svg"));
    expect(screen.getByTestId("chart-readout").textContent).toBe("");
  });

  it("shows a line point's value on hover", () => {
    render(<ChartCard spec={parseChartSpec(spec({ type: "line" }))!} />);
    const points = screen.getAllByTestId("chart-point");
    expect(points).toHaveLength(6);
    fireEvent.mouseEnter(points[5]); // Cost, Q3
    expect(screen.getByTestId("chart-readout")).toHaveTextContent("Q3 — Cost: -100 USD");
  });

  it("draws a pie with shares in the legend and on hover", () => {
    const pie = parseChartSpec(
      JSON.stringify({ type: "pie", unit: "$", labels: ["Rent", "Food", "Fun"], series: [{ name: "Spend", values: [500, 300, 200] }] }),
    )!;
    render(<ChartCard spec={pie} />);
    expect(screen.getAllByTestId("chart-slice")).toHaveLength(3);
    expect(screen.getByTestId("chart-legend")).toHaveTextContent("Rent50%");
    fireEvent.mouseEnter(screen.getAllByTestId("chart-slice")[1]);
    expect(screen.getByTestId("chart-readout")).toHaveTextContent("Food: $300 (30%)");
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain("Rent $500 (50%)");
  });

  it("draws a single-slice pie as a whole circle", () => {
    const pie = parseChartSpec(
      JSON.stringify({ type: "pie", labels: ["All", "None"], series: [{ name: "x", values: [4, 0] }] }),
    )!;
    const { container } = render(<ChartCard spec={pie} />);
    expect(screen.getAllByTestId("chart-slice")).toHaveLength(1);
    expect(container.querySelector("circle[data-testid='chart-slice']")).not.toBeNull();
  });

  it("flips to a table of the same numbers and back", () => {
    render(<ChartCard spec={parseChartSpec(spec())!} />);
    const toggle = screen.getByRole("button", { name: /show as table/i });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(toggle);
    const table = screen.getByTestId("chart-table");
    expect(screen.queryByRole("img")).toBeNull();
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent("LabelRevenueCost");
    expect(rows[2]).toHaveTextContent("Q21,500.5 USD700 USD");
    fireEvent.click(screen.getByRole("button", { name: /show as chart/i }));
    expect(screen.getByRole("img")).toBeInTheDocument();
  });

  it("copies the numbers as CSV and says so", async () => {
    render(<ChartCard spec={parseChartSpec(spec())!} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /copy data/i }));
    });
    expect(writeText).toHaveBeenCalledWith("Label,Revenue,Cost\r\nQ1,1200,800\r\nQ2,1500.5,700\r\nQ3,900,-100");
    expect(await screen.findByRole("button", { name: /copied/i })).toBeInTheDocument();
  });

  it("says when the copy failed instead of claiming it worked", async () => {
    writeText.mockImplementation(() => Promise.reject(new Error("denied")));
    render(<ChartCard spec={parseChartSpec(spec())!} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /copy data/i }));
    });
    expect(await screen.findByRole("button", { name: /couldn't copy/i })).toBeInTheDocument();
  });

  it("formats units the way people write them", () => {
    expect(formatValue(1234.5, "$")).toBe("$1,234.5");
    expect(formatValue(-5, "$")).toBe("-$5");
    expect(formatValue(12, "%")).toBe("12%");
    expect(formatValue(3, "kWh")).toBe("3 kWh");
    expect(formatValue(3)).toBe("3");
  });

  it("summarises a long pie without reading every slice", () => {
    const labels = Array.from({ length: 10 }, (_, i) => `S${i}`);
    const s: ChartSpec = { type: "pie", labels, series: [{ name: "x", values: labels.map(() => 1) }] };
    expect(chartSummary(s)).toContain("and 2 more");
  });
});

describe("Markdown routes the chart fence", () => {
  it("a chart fence that parses becomes the chart card", () => {
    render(<Markdown content={"Here:\n\n```chart\n" + spec() + "\n```\n"} />);
    expect(screen.getByTestId("chart-card")).toHaveAttribute("data-chart-type", "bar");
    expect(screen.queryByText(/"labels"/)).toBeNull();
  });

  it("a chart fence that does not parse stays an ordinary code block", () => {
    const { container } = render(
      <Markdown content={"```chart\n" + spec({ series: [{ name: "x", values: ["1,234", "2", "3"] }] }) + "\n```\n"} />,
    );
    expect(screen.queryByTestId("chart-card")).toBeNull();
    expect(container.querySelector("pre")).not.toBeNull();
    expect(container.querySelector("pre")!.textContent).toContain('"1,234"');
  });

  it("a half-written chart fence (still streaming) is a code block, not a broken chart", () => {
    const { container } = render(<Markdown content={"```chart\n" + spec().slice(0, 50)} />);
    expect(screen.queryByTestId("chart-card")).toBeNull();
    expect(container.querySelector("pre")).not.toBeNull();
  });

  it("the same JSON in a json fence stays code", () => {
    render(<Markdown content={"```json\n" + spec() + "\n```\n"} />);
    expect(screen.queryByTestId("chart-card")).toBeNull();
  });

  it("the email draft fence still becomes the draft card", () => {
    render(<Markdown content={"```email\nSubject: Hi\n\nHello **there**\n```\n"} />);
    expect(screen.getByTestId("draft-card")).toBeInTheDocument();
    expect(screen.queryByTestId("chart-card")).toBeNull();
  });
});
