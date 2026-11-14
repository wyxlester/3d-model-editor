// MAK VR-Forces settings (CONTRACT §9b, §10): the Review panel's form logic. Pure, unit tested.
import type { MakDraft, MakSettings } from '../types';
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
}

/**
 * The parts table: one row per moving part the draft found on the current version. A saved code
 * (null included) wins over the draft's suggestion. Saved parts the version no longer has are left
 * out, since the server would refuse them.
 */
export function makRows(draft: MakDraft, saved: MakSettings | null | undefined): MakRow[] {
  const savedParts = saved?.parts ?? {};
  return draft.parts.map((p) => {
    const code = Object.prototype.hasOwnProperty.call(savedParts, p.path) ? savedParts[p.path] : p.suggested_code;
    return {
      path: p.path, label: partLabel(p.path), motion: p.motion, min_deg: p.min_deg, max_deg: p.max_deg,
      priority: p.priority, suggested: p.suggested_code, code: code == null ? '' : String(code),
    };
  });
}

/** The panel's text fields. */
export interface MakForm { dis_type: string; donor_entity: string; short_name: string }

/** Where the form starts: the saved settings, else the draft's hint, first donor and short name. */
export function makFormStart(draft: MakDraft, saved: MakSettings | null | undefined): MakForm {
  if (saved) return { dis_type: saved.dis_type, donor_entity: saved.donor_entity, short_name: saved.short_name ?? '' };
  return { dis_type: draft.dis_type_hint, donor_entity: draft.donors[0]?.entity ?? '', short_name: draft.short_name };
}

/** The PATCH /mak body from the form and table, or the first thing to fix. */
export function makSettingsOf(form: MakForm, rows: MakRow[]): { ok: true; mak: MakSettings } | { ok: false; error: string } {
  const disErr = disTypeError(form.dis_type);
  if (disErr) return { ok: false, error: disErr };
  if (!form.donor_entity.trim()) return { ok: false, error: 'Pick a donor entity: the ship copies its systems and visuals.' };
  const parts: Record<string, number | null> = {};
  for (const r of rows) {
    const c = parseCode(r.code);
    if (c === 'bad') return { ok: false, error: `${r.label}: a DIS part code is a whole number, or leave it blank to keep the part static.` };
    parts[r.path] = c;
  }
  const short = form.short_name.trim();
  return {
    ok: true,
    mak: { dis_type: form.dis_type.split(':').map((f) => f.trim()).join(':').trim(), donor_entity: form.donor_entity.trim(), short_name: short || null, parts },
  };
}

/** The folders an install writes, as the confirm dialog names them (CONTRACT §9b). */
export function installFolders(sms: string, name: string): string[] {
  return [`SharedData/ModelData/Vehicles/Surface/${name}/`, `${sms}/vrfSim`, `${sms}/gui/visuals`];
}
