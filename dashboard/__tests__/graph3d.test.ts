import { describe, expect, it } from "vitest";

import {
  GROUP_3D,
  deletableKind,
  escapeHtml,
  linkTooltip,
  neighborsOf,
  nodeColorFor,
  toGraphData,
  tooltipHtml,
  type GraphDto,
  type Node3D,
} from "@/components/memory/graph3d";

/**
 * The 3D memory graph's pure layer (v1.115.0). The tooltip tests are the ones
 * that matter: react-force-graph renders nodeLabel as RAW HTML and memory
 * text is user/agent-written — a remembered note must never be able to inject
 * markup into the page that displays it.
 */

const DTO: GraphDto = {
  nodes: [
    { id: "lesson:1", label: "Confirm the tax year", group: "lesson", snippet: "always ask" },
    { id: "wm:user:-:focus", label: "focus", group: "memory", snippet: "Alvarez" },
    { id: "ltm:brain:a.md", label: "a", group: "note", snippet: "note text" },
  ],
  edges: [
    { a: "lesson:1", b: "wm:user:-:focus", weight: 1.0, kind: "manual" },
    { a: "wm:user:-:focus", b: "ltm:brain:a.md", weight: 0.62, kind: "auto" },
    // Dangling — its node was trimmed by the server's per-group cap.
    { a: "lesson:1", b: "lesson:GONE", weight: 0.9, kind: "auto" },
  ],
  embedder: "mock",
};

describe("toGraphData", () => {
  it("maps nodes and keeps only edges whose BOTH ends exist", () => {
    const g = toGraphData(DTO);
    expect(g.nodes).toHaveLength(3);
    expect(g.links).toHaveLength(2); // the dangling edge would crash the force engine
    expect(g.links[0]).toMatchObject({ source: "lesson:1", kind: "manual" });
  });
  it("falls back to the id when a label is empty", () => {
    const g = toGraphData({ ...DTO, nodes: [{ id: "x", label: "", group: "memory", snippet: "" }], edges: [] });
    expect(g.nodes[0].label).toBe("x");
  });
});

describe("tooltip safety — remembered text is untrusted", () => {
  const hostile: Node3D = {
    id: "wm:user:-:evil",
    label: `<img src=x onerror=alert(1)>`,
    group: "memory",
    snippet: `"><script>document.title='pwn'</script> & 'quotes'`,
  };
  it("escapes every HTML-significant character in label and snippet", () => {
    const html = tooltipHtml(hostile);
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<script");
    expect(html).toContain("&lt;img");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("&amp;");
    expect(html).toContain("&#39;quotes&#39;");
  });
  it("escapeHtml covers the full set", () => {
    expect(escapeHtml(`&<>"'`)).toBe("&amp;&lt;&gt;&quot;&#39;");
  });
  it("clips runaway snippets", () => {
    const html = tooltipHtml({ ...hostile, snippet: "a".repeat(2000) });
    expect(html.length).toBeLessThan(1200);
  });
});

describe("link tooltips say what a click does", () => {
  it("manual vs similarity", () => {
    expect(linkTooltip({ source: "a", target: "b", kind: "manual", weight: 1 })).toContain("your link");
    expect(linkTooltip({ source: "a", target: "b", kind: "auto", weight: 0.617 })).toContain("similarity 0.62");
  });
});

describe("selection highlight lifts lightness, not hue", () => {
  const n: Node3D = { id: "lesson:1", label: "x", group: "lesson", snippet: "" };
  it("selected and link-source use the bright variant", () => {
    expect(nodeColorFor(n, "lesson:1", null)).toBe(GROUP_3D.lesson.bright);
    expect(nodeColorFor(n, null, "lesson:1")).toBe(GROUP_3D.lesson.bright);
    expect(nodeColorFor(n, null, null)).toBe(GROUP_3D.lesson.hex);
  });
});

describe("deletableKind — the canvas must not reach into memory bases", () => {
  it("lessons and working memory delete", () => {
    expect(deletableKind("lesson:abc").ok).toBe(true);
    expect(deletableKind("wm:user:-:key").ok).toBe(true);
  });
  it("ltm refuses and names the base for the pointer copy", () => {
    const d = deletableKind("ltm:clientA:note.md");
    expect(d.ok).toBe(false);
    expect(d.base).toBe("clientA");
  });
  it("unknown prefixes refuse", () => {
    expect(deletableKind("sess:1").ok).toBe(false);
  });
});

describe("neighborsOf — the sidecar's walkable connections (v1.116.0)", () => {
  const links = [
    { source: "a", target: "b", kind: "auto" as const, weight: 0.5 },
    { source: "b", target: "a", kind: "manual" as const, weight: 1 }, // dup pair, manual wins
    { source: "a", target: "c", kind: "auto" as const, weight: 0.6 },
    { source: "x", target: "y", kind: "manual" as const, weight: 1 }, // unrelated
  ];
  it("finds both directions, dedupes, and lets manual outrank auto", () => {
    const n = neighborsOf("a", links);
    expect(n).toEqual([
      { id: "b", kind: "manual" },
      { id: "c", kind: "auto" },
    ]);
  });
  it("resolves object endpoints (the force engine mutates links in place)", () => {
    const objLinks = [
      {
        source: { id: "a", label: "", group: "memory" as const, snippet: "" },
        target: { id: "z", label: "", group: "note" as const, snippet: "" },
        kind: "auto" as const,
        weight: 0.7,
      },
    ];
    expect(neighborsOf("a", objLinks)).toEqual([{ id: "z", kind: "auto" }]);
  });
  it("no connections → empty", () => {
    expect(neighborsOf("solo", links)).toEqual([]);
  });
});
