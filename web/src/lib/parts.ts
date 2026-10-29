// Part naming and part-tree logic (CONTRACT §2). Pure, unit tested.
import type { Joint, Part } from '../types';

/** `Group__Part` or `Group__Part__Sub`, `[A-Za-z0-9_]` only. */
const PART_NAME = /^[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*(?:__[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)+$/;

export function isStationName(name: string): boolean {
  return name.startsWith('station__');
}

export function isPartName(name: string): boolean {
  return PART_NAME.test(name) && !isStationName(name) && !name.startsWith('socket_');
}

/** Display label: `__` → ` › `, `_` → space ("Roof__Searchlight" → "Roof › Searchlight"). */
export function partLabel(path: string): string {
  return path
    .split('__')
    .map((seg) => seg.replace(/_/g, ' ').trim())
    .join(' › ');
}

/**
 * Given the chain of node names from the clicked mesh up to the scene root
 * (`chain[0]` is the mesh itself), return the part the click belongs to.
 *
 * When the parts list is known it is the authority: the nearest ancestor whose
 * name is a listed part wins. This matters because three.js splits a multi-material
 * mesh into children such as `Roof__Searchlight_1`, which *look* like part names.
 * Without a parts list, fall back to the naming rule.
 */
export function resolvePart(chain: string[], knownParts?: Iterable<string>): string | null {
  const isPart = partTest(knownParts);
  for (const name of chain) if (isPart(name)) return name;
  return null;
}

/** Whether a node name is a part: listed (and not a station) when the list is known, else by the naming rule. */
function partTest(knownParts?: Iterable<string>): (name: string) => boolean {
  const known = knownParts ? new Set(knownParts) : null;
  if (known && known.size > 0) return (name) => known.has(name) && !isStationName(name);
  return isPartName;
}

/**
 * The nodes that belong to `node` itself: `node` and its descendants, minus any child part and
 * everything under it. Exactly the nodes a click resolves to `node` (resolvePart), so a highlight
 * of Hull__Main lights the hull, not the house, cranes and hatches attached to it.
 */
export function ownNodes<T extends { name: string; children: T[] }>(node: T, knownParts?: Iterable<string>): T[] {
  const isPart = partTest(knownParts);
  const out: T[] = [];
  const walk = (n: T) => {
    out.push(n);
    for (const c of n.children) if (!isPart(c.name)) walk(c);
  };
  walk(node);
  return out;
}

export interface Level {
  path: string;
  label: string;
  relation: 'ancestor' | 'self' | 'child';
}

/**
 * Levels offered by the pin tag's level picker: ancestors (outermost first), the part
 * itself, then its direct children. Stations are never offered.
 */
export function levelsFor(path: string, parts: Part[]): Level[] {
  const byPath = new Map(parts.map((p) => [p.path, p]));
  const ancestors: Level[] = [];
  const seen = new Set<string>([path]);
  let cur = byPath.get(path)?.parent ?? null;
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    const p = byPath.get(cur);
    if (!p) break;
    if (!isStationName(cur)) ancestors.unshift({ path: cur, label: labelOf(p), relation: 'ancestor' });
    cur = p.parent;
  }
  const self = byPath.get(path);
  const children: Level[] = parts
    .filter((p) => p.parent === path && !isStationName(p.path))
    .map((p) => ({ path: p.path, label: labelOf(p), relation: 'child' as const }));
  return [...ancestors, { path, label: self ? labelOf(self) : partLabel(path), relation: 'self' }, ...children];
}

function labelOf(p: Part): string {
  return p.label && p.label.trim() ? p.label : partLabel(p.path);
}

export function stationsOf(parts: Part[]): Part[] {
  return parts.filter((p) => isStationName(p.path) || (p.station !== null && p.station !== false && p.station !== undefined));
}

/** Parts that are articulated, with effective limits (spec `joint_limits` override the built ones). */
export function jointsOf(
  parts: Part[],
  limits?: Record<string, { min_deg: number; max_deg: number }>,
): { part: Part; joint: Joint }[] {
  return parts
    .filter((p) => p.joint)
    .map((p) => {
      const o = limits?.[p.path];
      return { part: p, joint: o ? { ...p.joint!, min_deg: o.min_deg, max_deg: o.max_deg } : p.joint! };
    });
}

/**
 * Parts that are hard to click in the viewer because they sit entirely below the waterline
 * (Blender z max < 0). Interior parts cannot be detected from parts.json, so the Comments tab
 * also offers a full part list.
 */
export function hiddenParts(parts: Part[]): Part[] {
  return parts.filter((p) => !isStationName(p.path) && p.bounds_max && p.bounds_max[2] < 0);
}
