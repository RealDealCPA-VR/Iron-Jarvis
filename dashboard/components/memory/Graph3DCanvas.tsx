"use client";

// The WebGL half of the 3D memory graph (v1.115.0). This file is the ONLY one
// that imports react-force-graph-3d / three, and MemoryGraph.tsx loads it via
// next/dynamic({ ssr: false }) — three.js touches `window` at import time, so
// a static import would break the prerender build. It is also split because
// next/dynamic does not forward refs: the imperative surface (fly-to-node,
// re-fit) rides an `apiRef` PROP instead, which survives the dynamic wrapper.

import { useCallback, useEffect, useRef, useState } from "react";
import ForceGraph3D, { type ForceGraphMethods } from "react-force-graph-3d";

import {
  linkTooltip,
  nodeColorFor,
  tooltipHtml,
  type Link3D,
  type Node3D,
} from "./graph3d";

export interface Graph3DApi {
  /** Smoothly orbit the camera to frame one node (the Find list uses this). */
  flyTo: (id: string) => void;
  refit: () => void;
}

export default function Graph3DCanvas({
  nodes,
  links,
  selectedId,
  linkFromId,
  onNodeClick,
  onLinkClick,
  onBackgroundClick,
  apiRef,
}: {
  nodes: Node3D[];
  links: Link3D[];
  selectedId: string | null;
  linkFromId: string | null;
  onNodeClick: (n: Node3D) => void;
  onLinkClick: (l: Link3D) => void;
  onBackgroundClick: () => void;
  apiRef: React.MutableRefObject<Graph3DApi | null>;
}) {
  const fgRef = useRef<ForceGraphMethods<Node3D, Link3D> | undefined>(undefined);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ w: 600, h: 560 });
  const fitOnceRef = useRef(false);

  // The lib needs explicit pixel dimensions — measure the container and track
  // resizes (the sidebar drawer and window changes both reflow this column).
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const measure = () =>
      setSize({ w: Math.max(280, el.clientWidth), h: Math.max(360, el.clientHeight) });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Bloom is what makes it read as "very 3D": small glowing cores instead of
  // flat billiard balls, the arc-reactor look the rest of the app wears.
  // Loaded lazily and guarded — a three version drift or a GPU without the
  // postprocessing path should cost the glow, never the graph.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { UnrealBloomPass } = await import(
          "three/examples/jsm/postprocessing/UnrealBloomPass.js"
        );
        if (cancelled) return;
        const composer = fgRef.current?.postProcessingComposer?.();
        if (!composer) return;
        // (resolution ignored by the pass) strength / radius / threshold —
        // tuned low so amber/emerald nodes glow without washing to white.
        const pass = new UnrealBloomPass(undefined as never, 0.9, 0.7, 0.1);
        composer.addPass(pass);
      } catch {
        /* no bloom — the graph itself still renders */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const flyTo = useCallback(
    (id: string) => {
      const fg = fgRef.current;
      const node = nodes.find((n) => n.id === id);
      if (!fg || !node || node.x === undefined) return;
      // Sit the camera on the node's own bearing vector, pulled back — keeps
      // the cluster context in frame instead of face-planting into the sphere.
      const dist = 90;
      const len = Math.hypot(node.x, node.y ?? 0, node.z ?? 0) || 1;
      const k = 1 + dist / len;
      fg.cameraPosition(
        { x: node.x * k, y: (node.y ?? 0) * k, z: (node.z ?? 0) * k },
        { x: node.x, y: node.y ?? 0, z: node.z ?? 0 },
        800,
      );
    },
    [nodes],
  );

  useEffect(() => {
    apiRef.current = {
      flyTo,
      refit: () => fgRef.current?.zoomToFit(600, 60),
    };
    return () => {
      apiRef.current = null;
    };
  }, [apiRef, flyTo]);

  return (
    <div ref={boxRef} className="h-full w-full">
      <ForceGraph3D
        ref={fgRef}
        width={size.w}
        height={size.h}
        graphData={{ nodes, links }}
        backgroundColor="rgba(0,0,0,0)"
        showNavInfo={false}
        // Simple glowing spheres — NO text sprites. The text lives in the
        // hover tooltip (nodeLabel renders our escaped HTML chip), which is
        // the requested behaviour and also what keeps a 200-node graph
        // readable instead of a word cloud.
        nodeLabel={(n) => tooltipHtml(n as Node3D)}
        nodeColor={(n) => nodeColorFor(n as Node3D, selectedId, linkFromId)}
        nodeVal={(n) =>
          (n as Node3D).id === selectedId || (n as Node3D).id === linkFromId ? 9 : 4
        }
        nodeOpacity={0.92}
        nodeResolution={24}
        linkLabel={(l) => linkTooltip(l as Link3D)}
        linkColor={(l) => ((l as Link3D).kind === "manual" ? "#22d3ee" : "#526073")}
        linkOpacity={0.4}
        linkWidth={(l) => ((l as Link3D).kind === "manual" ? 1.6 : 0.6)}
        // Slow particles run along the user's OWN links only — the manual
        // curation literally lights up against the passive similarity mesh.
        linkDirectionalParticles={(l) => ((l as Link3D).kind === "manual" ? 2 : 0)}
        linkDirectionalParticleWidth={1.8}
        linkDirectionalParticleSpeed={0.005}
        onNodeClick={(n) => onNodeClick(n as Node3D)}
        onLinkClick={(l) => onLinkClick(l as Link3D)}
        onBackgroundClick={onBackgroundClick}
        enableNodeDrag
        cooldownTicks={200}
        onEngineStop={() => {
          // One framing pass after the first layout settles; after that the
          // camera belongs to the user (re-fitting on every settle would yank
          // it out of their hands mid-orbit).
          if (!fitOnceRef.current) {
            fitOnceRef.current = true;
            fgRef.current?.zoomToFit(600, 60);
          }
        }}
      />
    </div>
  );
}
