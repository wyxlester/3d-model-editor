// The hull's footprint on the water plane, so the viewer can keep water out of the boat. Pure, unit tested.
// glTF frame: Y up, the waterline is y = 0 (CONTRACT §1); the footprint is in (x, z).

export type Pt = [number, number];

/**
 * Where triangle edges cross the plane y = level, as (x, z) points.
 * `pos` is xyz triples in world space; `index` lists triangle corners (null: every 3 vertices are a triangle).
 */
export function waterlineCrossings(pos: ArrayLike<number>, index: ArrayLike<number> | null, level = 0): Pt[] {
  const out: Pt[] = [];
  const n = index ? index.length : pos.length / 3;
  const vi = (k: number) => (index ? index[k] : k) * 3;
  const edge = (a: number, b: number) => {
    const ya = pos[a + 1] - level, yb = pos[b + 1] - level;
    if ((ya < 0 && yb < 0) || (ya > 0 && yb > 0) || ya === yb) return; // both on one side, or lying in the plane
    const t = ya / (ya - yb);
    out.push([pos[a] + t * (pos[b] - pos[a]), pos[a + 2] + t * (pos[b + 2] - pos[a + 2])]);
  };
  for (let k = 0; k + 2 < n; k += 3) {
    const a = vi(k), b = vi(k + 1), c = vi(k + 2);
    edge(a, b); edge(b, c); edge(c, a);
  }
  return out;
}

/** Convex hull, counter-clockwise, no repeated or collinear points (Andrew's monotone chain). */
export function convexHull(points: Pt[]): Pt[] {
  const p = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return [];
  const cross = (o: Pt, a: Pt, b: Pt) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const half = (pts: Pt[]) => {
    const h: Pt[] = [];
    for (const q of pts) {
      while (h.length >= 2 && cross(h[h.length - 2], h[h.length - 1], q) <= 0) h.pop();
      h.push(q);
    }
    h.pop();
    return h;
  };
  const hull = [...half(p), ...half(p.reverse())];
  return hull.length >= 3 ? hull : [];
}
