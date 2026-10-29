// Research photographs and close-ups (CONTRACT §12): coverage, filters, provenance wording,
// crop outlines and inspector zoom. Pure, unit tested in logic.test.ts.
import type { Coverage, CoverageView, PhotoStatus, ResearchCloseup, ResearchPhoto } from '../types';

export const COVERAGE_VIEWS: CoverageView[] = ['port', 'starboard', 'bow', 'stern', 'top', 'details'];

const LABELS: Record<string, string> = {
  port: 'Port', starboard: 'Starboard', bow: 'Bow', stern: 'Stern', top: 'Top', details: 'Details', interior: 'Interior',
};
export const viewLabel = (v: string) => LABELS[v.toLowerCase()] ?? (v ? v[0].toUpperCase() + v.slice(1) : v);

// ---- Coverage ------------------------------------------------------------------------------

export interface CoverageCell {
  view: CoverageView;
  label: string;
  /** Photo ids named for this view that exist in the research, in the order named. */
  ids: string[];
  /** What the research says, verbatim when stated; the ids when derived; "missing" when none. */
  text: string;
  missing: boolean;
}

/** Every photo id ("P01", "P12") a coverage text names, once each, in order. */
export function photoIdsIn(text: string | null | undefined): string[] {
  const out: string[] = [];
  for (const m of String(text ?? '').matchAll(/\bP\d+\b/g)) if (!out.includes(m[0])) out.push(m[0]);
  return out;
}

const SAYS_NONE = /^\s*(none|missing|not found|no\b|n\/a|-|—)/i;

/**
 * The six-view strip. The research's own `coverage` wins when present (a view it leaves null, empty
 * or "none…" is missing); without it, coverage is worked out from the photos' views, ignoring
 * excluded photos. Missing is a finding, not a failure.
 */
export function coverageSummary(coverage: Coverage | null | undefined, photos: ResearchPhoto[] = []): { cells: CoverageCell[]; note: string | null; stated: boolean } {
  const known = new Set(photos.map((p) => p.id));
  const stated = !!coverage && typeof coverage === 'object';
  const cells = COVERAGE_VIEWS.map((view): CoverageCell => {
    const label = viewLabel(view);
    if (stated) {
      const raw = coverage![view];
      const text = typeof raw === 'string' ? raw.trim() : '';
      const ids = photoIdsIn(text).filter((id) => known.has(id));
      const missing = !text || (SAYS_NONE.test(text) && !ids.length);
      return { view, label, ids, text: missing ? 'missing' : text, missing };
    }
    const ids = photos.filter((p) => photoStatus(p) !== 'excluded' && (p.views ?? []).some((v) => v.toLowerCase() === view)).map((p) => p.id);
    return { view, label, ids, text: ids.length ? ids.join(', ') : 'missing', missing: !ids.length };
  });
  const note = stated && typeof coverage!.note === 'string' && coverage!.note.trim() ? coverage!.note.trim() : null;
  return { cells, note, stated };
}

// ---- Status and filters --------------------------------------------------------------------

/** A photo's status as the contract spells it, or 'unstated' when the record has none we know. */
export function photoStatus(p: Pick<ResearchPhoto, 'status'>): PhotoStatus | 'unstated' {
  const s = String(p.status ?? '').trim().toLowerCase();
  return s === 'baseline' || s === 'comparison' || s === 'excluded' ? s : 'unstated';
}

export const STATUS_TEXT: Record<PhotoStatus | 'unstated', { label: string; tone: string; means: string }> = {
  baseline: { label: 'Baseline', tone: 'green', means: 'Shows this vessel; the model is matched to it.' },
  comparison: { label: 'Comparison', tone: 'blue', means: 'A sister or similar craft: explains a family of equipment, never what is fitted on this one.' },
  excluded: { label: 'Excluded', tone: 'grey', means: 'Kept for the record, not used for the model.' },
  unstated: { label: 'Status not stated', tone: 'amber', means: 'The research does not say how this photo may be used.' },
};

export type ViewFilter = 'all' | string;
export type StatusFilter = 'all' | PhotoStatus;

/** Photos matching a view (any of its views, case-insensitive) and a status, keeping their index in the research. */
export function filterPhotos(photos: ResearchPhoto[], view: ViewFilter, status: StatusFilter): { photo: ResearchPhoto; index: number }[] {
  const v = view.toLowerCase();
  return photos
    .map((photo, index) => ({ photo, index }))
    .filter(({ photo }) => v === 'all' || (photo.views ?? []).some((x) => x.toLowerCase() === v))
    .filter(({ photo }) => status === 'all' || photoStatus(photo) === status);
}

/** View filter buttons: the six coverage views and interior always, then any other view the photos use, each with its count. */
export function viewOptions(photos: ResearchPhoto[]): { key: string; label: string; count: number }[] {
  const keys = [...COVERAGE_VIEWS, 'interior'] as string[];
  for (const p of photos) for (const v of p.views ?? []) if (!keys.includes(v.toLowerCase())) keys.push(v.toLowerCase());
  return [
    { key: 'all', label: 'All', count: photos.length },
    ...keys.map((key) => ({ key, label: viewLabel(key), count: filterPhotos(photos, key, 'all').length })),
  ];
}

export function statusOptions(photos: ResearchPhoto[]): { key: StatusFilter; label: string; count: number }[] {
  const n = (s: StatusFilter) => filterPhotos(photos, 'all', s).length;
  return [
    { key: 'all', label: 'Any status', count: photos.length },
    { key: 'baseline', label: 'Baseline', count: n('baseline') },
    { key: 'comparison', label: 'Comparison', count: n('comparison') },
    { key: 'excluded', label: 'Excluded', count: n('excluded') },
  ];
}

/** The count line: the research's own count of distinct exposures, and how it was counted. */
export function countLine(photoCount: number | undefined, method: string | undefined, records: number): { count: string; method: string } {
  const counted = typeof photoCount === 'number' && Number.isFinite(photoCount);
  const n = counted ? photoCount : records;
  const count = counted
    ? `${n} distinct photograph${n === 1 ? '' : 's'}${records !== n ? ` · ${records} record${records === 1 ? '' : 's'} listed` : ''}`
    : `${records} photograph${records === 1 ? '' : 's'} listed · not counted for distinct exposures`;
  return { count, method: method?.trim() || 'The research does not say how the photographs were counted.' };
}

// ---- Provenance ----------------------------------------------------------------------------

export const NOT_RECORDED = 'not recorded';

/** Only http(s) links are ever made clickable: the URLs are written by an AI from web pages. */
export function safeHref(url: string | null | undefined): string | null {
  const u = String(url ?? '').trim();
  return /^https?:\/\/[^\s/]+/i.test(u) ? u : null;
}

export function hostOf(url: string | null | undefined): string {
  const u = safeHref(url);
  if (!u) return '';
  try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return ''; }
}

export function shortHash(sha: string | null | undefined, n = 12): string {
  const s = String(sha ?? '').trim();
  if (!/^[0-9a-f]{16,}$/i.test(s)) return NOT_RECORDED;
  return `${s.slice(0, n).toLowerCase()}…`;
}

export function sizeText(w: number | null | undefined, h: number | null | undefined): string {
  return w && h && w > 0 && h > 0 ? `${Math.round(w)} × ${Math.round(h)} px` : NOT_RECORDED;
}

export interface ProvRow { label: string; value: string; href?: string; mono?: boolean; missing: boolean; title?: string }

/** The provenance block, in reading order: what it shows, who took it, where it came from, what the server measured, how it may be used. */
export function provenanceRows(p: ResearchPhoto): ProvRow[] {
  const row = (label: string, value: string | null | undefined, extra: Partial<ProvRow> = {}): ProvRow => {
    const v = String(value ?? '').trim();
    return { label, value: v || NOT_RECORDED, missing: !v || v === NOT_RECORDED, ...extra };
  };
  const src = safeHref(p.source), img = safeHref(p.image_url);
  const st = STATUS_TEXT[photoStatus(p)];
  return [
    row('Status', st.label, { title: st.means }),
    row('Vessel', p.vessel),
    row('Views', (p.views ?? []).map(viewLabel).join(', ')),
    row('Credit', p.credit),
    row('Date', p.date),
    row('Source', src ? hostOf(src) || src : p.source, src ? { href: src } : {}),
    row('Image link', img ? hostOf(img) || img : p.image_url, img ? { href: img } : {}),
    row('Size', sizeText(p.width, p.height)),
    row('SHA-256', shortHash(p.sha256), { mono: true, title: p.sha256 || undefined }),
    row('Accessed', p.accessed),
    row('Rights', p.rights),
    row('Modelling use', p.modelling_use),
    row('Observation', p.observation),
  ];
}

// ---- Crop outline --------------------------------------------------------------------------

export interface Outline { left: number; top: number; width: number; height: number }

/**
 * A close-up's box ([x, y, w, h] in the original photo's pixels) as percentages of the photo, for an
 * outline that scales with the image. Clipped to the photo; null when the box or size is unusable.
 */
export function boxToPercent(box: ResearchCloseup['box'] | number[] | null | undefined, width: number | null | undefined, height: number | null | undefined): Outline | null {
  if (!Array.isArray(box) || box.length !== 4 || !width || !height || width <= 0 || height <= 0) return null;
  const [x, y, w, h] = box.map(Number);
  if (![x, y, w, h].every(Number.isFinite) || w <= 0 || h <= 0) return null;
  const x0 = Math.max(0, x), y0 = Math.max(0, y), x1 = Math.min(width, x + w), y1 = Math.min(height, y + h);
  if (x1 <= x0 || y1 <= y0) return null;
  const pct = (v: number, of: number) => Math.round((v / of) * 10000) / 100;
  return { left: pct(x0, width), top: pct(y0, height), width: pct(x1 - x0, width), height: pct(y1 - y0, height) };
}

export function boxText(box: ResearchCloseup['box'] | null | undefined): string {
  if (!Array.isArray(box) || box.length !== 4) return NOT_RECORDED;
  const [x, y, w, h] = box.map((n) => Math.round(Number(n)));
  return `${w} × ${h} px at (${x}, ${y})`;
}

// ---- Inspector zoom ------------------------------------------------------------------------

export const ZOOM_MIN = 1;
export const ZOOM_MAX = 8;
export const clampZoom = (z: number) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Number.isFinite(z) ? z : ZOOM_MIN));

type Pt = { x: number; y: number };
type Size = { w: number; h: number };

/** The size that fits an image inside a viewport, keeping its proportions. */
export function fitSize(natural: Size, view: Size): Size {
  if (natural.w <= 0 || natural.h <= 0 || view.w <= 0 || view.h <= 0) return { w: 0, h: 0 };
  const s = Math.min(view.w / natural.w, view.h / natural.h);
  return { w: natural.w * s, h: natural.h * s };
}

/** Keep the image covering the viewport where it is bigger than it, and centred where it is smaller. */
export function clampPan(pan: Pt, zoom: number, base: Size, view: Size): Pt {
  const mx = Math.max(0, (base.w * zoom - view.w) / 2);
  const my = Math.max(0, (base.h * zoom - view.h) / 2);
  const c = (v: number, m: number) => (m === 0 ? 0 : Math.min(m, Math.max(-m, v)));
  return { x: c(pan.x, mx), y: c(pan.y, my) };
}

/**
 * Zoom by `factor` keeping the image point under `at` (relative to the viewport centre) still, as
 * a wheel or pinch does; then clamp. With `at` at the centre it zooms about the centre.
 */
export function zoomAt(zoom: number, pan: Pt, factor: number, at: Pt, base: Size, view: Size): { zoom: number; pan: Pt } {
  const z = clampZoom(zoom * factor);
  const k = z / zoom;
  const p = { x: at.x - (at.x - pan.x) * k, y: at.y - (at.y - pan.y) * k };
  return { zoom: z, pan: clampPan(p, z, base, view) };
}
