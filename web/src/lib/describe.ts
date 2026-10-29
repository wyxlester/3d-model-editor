// "Made by description" helpers for the crew workshop (CONTRACT §6): swatch colours, removing a
// described item, the picker offer. Which module an item belongs to is in modules.ts. Pure, unit tested.
import type { CustomItem, Vec3 } from '../types';

export interface Swatch { id: string; label: string; hex: string }

export const HAIR_SWATCHES: Swatch[] = [
  { id: 'black', label: 'Black', hex: '#1C1A19' },
  { id: 'dark_brown', label: 'Dark brown', hex: '#3B2A20' },
  { id: 'brown', label: 'Brown', hex: '#6A4630' },
  { id: 'blonde', label: 'Blonde', hex: '#D8B878' },
  { id: 'red', label: 'Red', hex: '#9A3F1E' },
  { id: 'grey', label: 'Grey', hex: '#8E8C88' },
  { id: 'white', label: 'White', hex: '#E6E3DC' },
];

/** Shoe and hat colours: the plain ones a work uniform uses. */
export const GEAR_SWATCHES: Swatch[] = [
  { id: 'black', label: 'Black', hex: '#1E1E1E' },
  { id: 'brown', label: 'Brown', hex: '#5A3B24' },
  { id: 'navy', label: 'Navy', hex: '#1F2E4D' },
  { id: 'grey', label: 'Grey', hex: '#7A7D82' },
  { id: 'white', label: 'White', hex: '#EDEDED' },
  { id: 'orange', label: 'Orange', hex: '#E8641E' },
];

const r3 = (x: number) => Math.round(x * 1000) / 1000;
const toLinear = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);

/**
 * "#D8B878" → [r,g,b] in 0..1, LINEAR (what a Blender material's Base Color takes). The swatch hex
 * is what the eye sees on screen (sRGB); sending it unconverted would come out washed-out pale.
 */
export function hexToLinear(hex: string): Vec3 {
  const h = hex.replace('#', '');
  const v = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  return [r3(toLinear(v[0])), r3(toLinear(v[1])), r3(toLinear(v[2]))];
}

/**
 * Which swatch the spec's colour is: 'default' when unset, the swatch id when it matches one,
 * null when it's a colour the AI or someone else chose (no swatch lit, rather than a wrong one).
 */
export function selectedSwatch(value: Vec3 | null | undefined, swatches: Swatch[]): string | null {
  if (!value) return 'default';
  const hit = swatches.find((s) => hexToLinear(s.hex).every((c, i) => Math.abs(c - value[i]) < 0.01));
  return hit ? hit.id : null;
}

/** The spec patch that removes one made-by-description item (the whole array, as patches replace arrays). */
export function removeItemPatch(items: CustomItem[] | undefined, id: string): { custom_items: CustomItem[] } {
  return { custom_items: (items ?? []).filter((i) => i.id !== id) };
}

/**
 * Offer "Add as a picker" only when the AI's last turn actually made a new item: comparing the
 * item ids from when the user sent with the ids now. Unknown "before" (page just opened) → no offer.
 */
export function madeSomethingNew(before: string[] | null, items: CustomItem[] | undefined): boolean {
  if (!before) return false;
  return (items ?? []).some((i) => !before.includes(i.id));
}
