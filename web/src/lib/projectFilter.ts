// Finding ships and crew on the pickers: search, filters, sorting and grouping, and the filter
// kept in the page address. Pure, unit tested.
import type { ProjectSummary } from '../types';

/** The group for a project with no category (none set, and its catalog base gives none). */
export const UNCATEGORIZED = 'Uncategorized';

export type SortKey = 'recent' | 'name' | 'name_desc' | 'versions';
export type StatusFilter = 'all' | 'approval' | 'working' | 'built' | 'unbuilt';
export type OriginFilter = 'all' | 'originals' | 'variants';

export interface ProjectFilter {
  q: string;
  category: string | null;
  status: StatusFilter;
  origin: OriginFilter;
  base: string | null;
  sort: SortKey;
  group: boolean;
}

export const DEFAULT_FILTER: ProjectFilter = { q: '', category: null, status: 'all', origin: 'all', base: null, sort: 'recent', group: false };

const SORTS: SortKey[] = ['recent', 'name', 'name_desc', 'versions'];
const STATUSES: StatusFilter[] = ['all', 'approval', 'working', 'built', 'unbuilt'];
const ORIGINS: OriginFilter[] = ['all', 'originals', 'variants'];

type Listed = Pick<ProjectSummary, 'id' | 'name' | 'base' | 'version' | 'updated' | 'category' | 'derived_from' | 'draft'>;

export interface FilterContext {
  /** Projects with a change queued or running. */
  working: Set<string>;
  /** "container" → "Container ship", so a search for the type's name finds it. */
  baseLabel: (base: string) => string;
}

export const categoryOf = (p: Pick<Listed, 'category'>) => p.category?.trim() || UNCATEGORIZED;

/** Lower case, accents removed: "Pêche" matches "peche". */
const fold = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

/** Every word of the search appears somewhere in the name, category, base type or original's name. */
function matches(p: Listed, q: string, ctx: FilterContext): boolean {
  const words = fold(q).split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const base = p.base ?? '';
  const hay = fold([p.name, categoryOf(p), base, ctx.baseLabel(base), p.derived_from?.name ?? ''].join(' '));
  return words.every((w) => hay.includes(w));
}

function statusOk(p: Listed, s: StatusFilter, ctx: FilterContext): boolean {
  switch (s) {
    case 'all': return true;
    case 'approval': return !!p.draft;
    case 'working': return ctx.working.has(p.id);
    case 'built': return !!p.version;
    case 'unbuilt': return !p.version;
  }
}

/** Everything except the category: what the category chips count against. */
export function applyOtherFilters<P extends Listed>(list: readonly P[], f: ProjectFilter, ctx: FilterContext): P[] {
  return list.filter((p) => matches(p, f.q, ctx) && statusOk(p, f.status, ctx)
    && (f.origin === 'all' || (f.origin === 'variants') === !!p.derived_from)
    && (!f.base || p.base === f.base));
}

const byName = (a: Listed, b: Listed) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });

export function sortProjects<P extends Listed>(list: readonly P[], sort: SortKey): P[] {
  const out = [...list];
  switch (sort) {
    case 'recent': return out.sort((a, b) => String(b.updated ?? '').localeCompare(String(a.updated ?? '')));
    case 'name': return out.sort(byName);
    case 'name_desc': return out.sort((a, b) => byName(b, a));
    case 'versions': return out.sort((a, b) => (b.version ?? 0) - (a.version ?? 0));
  }
}

/** The list as the page shows it: filtered, then sorted. */
export function applyFilter<P extends Listed>(list: readonly P[], f: ProjectFilter, ctx: FilterContext): P[] {
  const kept = applyOtherFilters(list, f, ctx).filter((p) => !f.category || categoryOf(p) === f.category);
  return sortProjects(kept, f.sort);
}

/** Category order: the defaults as listed, then any others in use (A–Z), then Uncategorized. */
function categoryOrder(list: readonly Listed[], defaults: readonly string[]): string[] {
  const used = new Set(list.map(categoryOf));
  const extra = [...used].filter((c) => !defaults.includes(c) && c !== UNCATEGORIZED).sort((a, b) => a.localeCompare(b));
  return [...defaults, ...extra, ...(used.has(UNCATEGORIZED) ? [UNCATEGORIZED] : [])];
}

/** How many projects each category holds. Defaults stay listed at 0, so they can be chosen. */
export function categoryCounts(list: readonly Listed[], defaults: readonly string[]): { category: string; count: number }[] {
  return categoryOrder(list, defaults).map((category) => ({ category, count: list.filter((p) => categoryOf(p) === category).length }));
}

/** The list split under category headings, in category order, empty groups left out. */
export function groupByCategory<P extends Listed>(list: readonly P[], defaults: readonly string[]): { category: string; items: P[] }[] {
  return categoryOrder(list, defaults)
    .map((category) => ({ category, items: list.filter((p) => categoryOf(p) === category) }))
    .filter((g) => g.items.length > 0);
}

/** Search or a filter is hiding something (sorting and grouping hide nothing). */
export function isFiltered(f: ProjectFilter): boolean {
  return !!f.q.trim() || !!f.category || f.status !== 'all' || f.origin !== 'all' || !!f.base;
}

export function filterFromParams(sp: URLSearchParams): ProjectFilter {
  const pick = <T extends string>(v: string | null, allowed: T[], fallback: T): T => (allowed.includes(v as T) ? (v as T) : fallback);
  return {
    q: sp.get('q') ?? '',
    category: sp.get('cat') || null,
    status: pick(sp.get('status'), STATUSES, 'all'),
    origin: pick(sp.get('origin'), ORIGINS, 'all'),
    base: sp.get('base') || null,
    sort: pick(sp.get('sort'), SORTS, 'recent'),
    group: sp.get('group') === '1',
  };
}

export function filterToParams(f: ProjectFilter): Record<string, string> {
  const out: Record<string, string> = {};
  if (f.q) out.q = f.q;
  if (f.category) out.cat = f.category;
  if (f.status !== 'all') out.status = f.status;
  if (f.origin !== 'all') out.origin = f.origin;
  if (f.base) out.base = f.base;
  if (f.sort !== 'recent') out.sort = f.sort;
  if (f.group) out.group = '1';
  return out;
}
