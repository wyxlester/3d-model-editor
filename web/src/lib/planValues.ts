// What the user changes in research before a ship is built (CONTRACT §12, Review): their own values
// for the design plan's settings, and which claims still count once sources are crossed out. Pure, unit tested.
import type { Vec3 } from '../types';

const fromLinear = (c: number) => (c <= 0.0031308 ? c * 12.92 : 1.055 * c ** (1 / 2.4) - 0.055);

/** A Blender (linear 0-1) colour as the "#rrggbb" a colour picker shows: the inverse of hexToLinear. */
export function linearToHex(c: Vec3 | number[]): string {
  return '#' + c.slice(0, 3).map((v) => Math.round(Math.min(1, Math.max(0, fromLinear(v))) * 255).toString(16).padStart(2, '0')).join('');
}

export interface PlanRow {
  name: string;
  label: string;
  kind: 'length' | 'number' | 'colour';
  planned: unknown;
  mine: unknown;
  value: unknown;
  /** Whose value is in use: the user's own, or the plan's. */
  from: 'yours' | 'plan';
  doc?: string;
}

const DIMENSIONS = ['loa_m', 'beam_m', 'draught_m', 'depth_m'];
const PARAM_LABELS: Record<string, string> = { loa_m: 'Length overall', beam_m: 'Beam', draught_m: 'Draught', depth_m: 'Depth' };
const isNum = (v: unknown): v is number => typeof v === 'number';
const isColour = (v: unknown) => Array.isArray(v) && v.length === 3 && v.every(isNum);

function labelOf(name: string): string {
  if (PARAM_LABELS[name]) return PARAM_LABELS[name];
  const words = name.replace(/_m$/, '').replace(/_color$/, ' colour').replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * The plan's settings the user may set: `key` holds the principal dimensions then the colours,
 * `more` the plan's other numbers. Text choices and lists (bow form, superstructure tiers) are
 * changed by chatting, not here (the server refuses them too).
 */
export function planRows(planned: Record<string, unknown>, mine: Record<string, unknown>,
  docs: Record<string, unknown> = {}): { key: PlanRow[]; more: PlanRow[] } {
  const row = (name: string): PlanRow => {
    const kind: PlanRow['kind'] = isColour(planned[name]) || name.endsWith('_color') ? 'colour' : name.endsWith('_m') ? 'length' : 'number';
    const doc = docs[name];
    return {
      name, label: labelOf(name), kind, planned: planned[name], mine: mine[name],
      value: name in mine ? mine[name] : planned[name], from: name in mine ? 'yours' : 'plan',
      doc: typeof doc === 'string' ? doc : (doc as { doc?: string } | undefined)?.doc,
    };
  };
  const names = Object.keys(planned);
  const colours = names.filter((n) => isColour(planned[n]));
  const key = [...DIMENSIONS.filter((n) => isNum(planned[n])), ...colours];
  const more = names.filter((n) => isNum(planned[n]) && !key.includes(n));
  return { key: key.map(row), more: more.map(row) };
}

/** A claim still counts unless every source it cites was crossed out. */
export function claimUsed(claim: { sources?: string[] | string }, excluded: string[]): boolean {
  const cited = Array.isArray(claim.sources) ? claim.sources : [];
  return !cited.length || cited.some((s) => !excluded.includes(s));
}
