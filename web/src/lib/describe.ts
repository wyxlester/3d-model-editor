// "Made by description" helpers for the crew workshop (CONTRACT §6). Pure, unit tested.
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

export type DescribeSlot = 'Hair' | 'Clothes' | 'Head' | 'Equipment';

/** The chat message for a description typed under a picker: "Clothes: a white singlet". Null when blank. */
export function describeMessage(slot: DescribeSlot, text: string): string | null {
  const t = text.trim().replace(/\s+/g, ' ');
  return t ? `${slot}: ${t}` : null;
}

export type ItemKind = 'clothes' | 'head' | 'equipment' | 'other';

const HEAD_WORDS = /\b(hat|cap|helmet|beret|hood|beanie|fedora|turban|bandana|headband|goggles|glasses|sunglasses|beard|moustache|mustache|hair)\b/i;

/**
 * Which tab a made-by-description item belongs in. The code's object names say worn vs held
 * (CONTRACT §6: Clothes__ / Equipment__); worn things on the head are told apart by the words.
 */
export function itemKind(item: Pick<CustomItem, 'code' | 'description'>): ItemKind {
  const code = item.code ?? '';
  const worn = code.includes('Clothes__');
  const held = code.includes('Equipment__');
  if (worn && !held) return HEAD_WORDS.test(item.description ?? '') ? 'head' : 'clothes';
  if (held && !worn) return 'equipment';
  if (worn && held) return HEAD_WORDS.test(item.description ?? '') ? 'head' : 'clothes';
  return 'other';
}

/** Items to list in a tab. Ones that can't be classified show under Clothes so none is lost. */
export function itemsFor(items: CustomItem[] | undefined, tab: 'Clothes' | 'Head' | 'Equipment'): CustomItem[] {
  const want: ItemKind[] = tab === 'Clothes' ? ['clothes', 'other'] : tab === 'Head' ? ['head'] : ['equipment'];
  return (items ?? []).filter((i) => want.includes(itemKind(i)));
}

/** The spec patch that removes one made-by-description item (the whole array, as patches replace arrays). */
export function removeItemPatch(items: CustomItem[] | undefined, id: string): { custom_items: CustomItem[] } {
  return { custom_items: (items ?? []).filter((i) => i.id !== id) };
}
