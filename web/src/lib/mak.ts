// MAK VR-Forces settings (CONTRACT §9b, §10): the Review panel's form logic. Pure, unit tested.
import type { MakDraft, MakSettings, MakSystemChange } from '../types';
import { partLabel } from './parts';

/** Null when `s` is a DIS entity type (7 colon-separated integers), else a sentence for the user. */
export function disTypeError(s: string): string | null {
  const fields = s.trim().split(':');
  if (fields.length !== 7) return `A DIS type has 7 numbers separated by colons, like 1:3:15:80:1:0:0 (this has ${s.trim() ? fields.length : 0}).`;
  if (!fields.every((f) => /^-?\d+$/.test(f.trim()))) return 'Each of the 7 DIS type fields must be a whole number, like 1:3:15:80:1:0:0.';
  return null;
}

/** A DIS articulated-part code typed in a cell: a number, null for blank (the part stays static), or 'bad'. */
export function parseCode(text: string): number | null | 'bad' {
  const t = text.trim();
  if (!t) return null;
  return /^\d+$/.test(t) ? Number(t) : 'bad';
}

/** One row of the moving-parts table. `code` is the cell's text: '' means static. */
export interface MakRow {
  path: string;
  label: string;
  motion: string;
  min_deg: number;
  max_deg: number;
  priority: string;
  suggested: number | null;
  code: string;
  /** Settings saved before parts_suggested: the saved code differs from today's suggestion, and whether the user
   *  chose it can't be told. The panel says so and offers the suggestion. */
  differs?: boolean;
}

/**
 * The parts table: one row per moving part the draft found on the current version, with the code the export
 * would use (CONTRACT §10, as mak/from_package.effective_codes):
 * - a part the settings don't name: today's suggestion;
 * - a saved code equal to the suggestion it was saved with: today's suggestion, so improved rules apply;
 * - a code the user changed: kept;
 * - settings saved before `parts_suggested`: kept as saved, flagged `differs` where today's suggestion differs.
 * Saved parts the version no longer has are left out, since the server would refuse them.
 */
export function makRows(draft: MakDraft, saved: MakSettings | null | undefined): MakRow[] {
  const savedParts = saved?.parts ?? {};
  const was = saved?.parts_suggested;
  const has = (o: object, k: string) => Object.prototype.hasOwnProperty.call(o, k);
  return draft.parts.map((p) => {
    let code = p.suggested_code;
    let differs = false;
    if (has(savedParts, p.path)) {
      const s = savedParts[p.path];
      const accepted = was !== undefined && has(was, p.path) && (was[p.path] ?? null) === (s ?? null);
      if (!accepted) {
        code = s;
        differs = was === undefined && (s ?? null) !== (p.suggested_code ?? null);
      }
    }
    return {
      path: p.path, label: partLabel(p.path), motion: p.motion, min_deg: p.min_deg, max_deg: p.max_deg,
      priority: p.priority, suggested: p.suggested_code, code: code == null ? '' : String(code), differs,
    };
  });
}

/** Every row back to today's suggestion (the panel's "Use the current suggestions"). */
export function withSuggestions(rows: MakRow[]): MakRow[] {
  return rows.map((r) => ({ ...r, code: r.suggested == null ? '' : String(r.suggested), differs: false }));
}

/** The panel's text fields. */
export interface MakForm { dis_type: string; donor_entity: string; short_name: string }

/** Where the form starts: the saved settings, else the draft's hint, first donor and short name. */
export function makFormStart(draft: MakDraft, saved: MakSettings | null | undefined): MakForm {
  if (saved) return { dis_type: saved.dis_type, donor_entity: saved.donor_entity, short_name: saved.short_name ?? '' };
  return { dis_type: draft.dis_type_hint, donor_entity: draft.donors[0]?.entity ?? '', short_name: draft.short_name };
}

/** One line for a drafted system change, in the panel's words. */
export function systemChangeText(c: MakSystemChange): string {
  const where = (n: string) => (n.includes('__') ? partLabel(n) : n.replace(/^socket_/, '').replace(/_/g, ' '));
  if (c.remove) return `Remove ${c.system}`;
  if (c.sysdef) {
    const what = c.sysdef.split('/').pop()!.replace(/\.sysdef$/, '');
    const on = c.bind ? Object.values(c.bind).map((v) => (typeof v === 'string' ? where(v) : String(v))) : [];
    const moving = (c.add_parts ?? []).filter((a) => a.motion !== 'fixed');
    const fixed = (c.add_parts ?? []).filter((a) => a.motion === 'fixed');
    const sim = (moving.length ? `, with sim-only part${moving.length > 1 ? 's' : ''} ${moving.map((a) => a.code).join(', ')} (no joint in the model)` : '')
      + (fixed.length ? `, with fixed launch point${fixed.length > 1 ? 's' : ''} ${fixed.map((a) => a.code).join(', ')} (no motion, nothing to show)` : '');
    return `Add ${c.system} (${what})${on.length ? ` on ${[...new Set(on)].join(', ')}` : ''}${c.position_from ? ` at ${where(c.position_from)}` : ''}${sim}`;
  }
  const parts: string[] = [];
  if (c.position_from) parts.push(`move ${c.system} to ${where(c.position_from)}`);
  if (c.bind) parts.push(`bind ${c.system} to ${[...new Set(Object.values(c.bind).map((v) => (typeof v === 'string' ? where(v) : String(v))))].join(', ')}`);
  if (c.params) parts.push(`set ${Object.entries(c.params).map(([k, v]) => `${k} ${v}`).join(', ')}`);
  const text = parts.join('; ') || `change ${c.system}`;
  return text[0].toUpperCase() + text.slice(1);
}

/** The PATCH /mak body from the form and table, or the first thing to fix. `off` = drafted system changes
 * turned down (saved only when there are some). */
export function makSettingsOf(form: MakForm, rows: MakRow[], off: string[] = []): { ok: true; mak: MakSettings } | { ok: false; error: string } {
  const disErr = disTypeError(form.dis_type);
  if (disErr) return { ok: false, error: disErr };
  if (!form.donor_entity.trim()) return { ok: false, error: 'Pick a donor entity: the ship copies its systems and visuals.' };
  const parts: Record<string, number | null> = {};
  const suggested: Record<string, number | null> = {};
  for (const r of rows) {
    const c = parseCode(r.code);
    if (c === 'bad') return { ok: false, error: `${r.label}: a DIS part code is a whole number, or leave it blank to keep the part static.` };
    parts[r.path] = c;
    suggested[r.path] = r.suggested;
  }
  const short = form.short_name.trim();
  return {
    ok: true,
    mak: {
      dis_type: form.dis_type.split(':').map((f) => f.trim()).join(':').trim(), donor_entity: form.donor_entity.trim(), short_name: short || null, parts,
      parts_suggested: suggested,
      ...(off.length ? { systems_off: [...new Set(off)].sort() } : {}),
    },
  };
}

/** The folders an install writes, as the confirm dialog names them (CONTRACT §9b). */
export function installFolders(sms: string, name: string): string[] {
  return [`SharedData/ModelData/Vehicles/Surface/${name}/`, `${sms}/vrfSim`, `${sms}/gui/visuals`];
}
