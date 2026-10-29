// Crew modules (CONTRACT §6 "Modules"): which module a described item belongs to, the spec patches
// presets and swatches send (no AI), and the one-line summary each collapsed row shows. Pure, unit tested.
import type { Accessory, CrewSpec, CustomItem, Equipment, EquipmentEntry, Garment, ModuleId, Place, Vec3 } from '../types';
import { hexToLinear, selectedSwatch, GEAR_SWATCHES, HAIR_SWATCHES, type Swatch } from './describe';
import { assetLabel } from './spec';

export interface ModuleDef { id: ModuleId; name: string; placeholder: string; note?: string }

/** The ten rows, in the approved mock's order and wording. */
export const MODULES: ModuleDef[] = [
  { id: 'gender', name: 'Gender', placeholder: 'A stocky man in his fifties' },
  { id: 'skin', name: 'Skin colour', placeholder: 'Sunburnt, weathered' },
  { id: 'hair', name: 'Hair', placeholder: 'Blonde, shoulder length' },
  { id: 'shirt', name: 'Shirt', placeholder: 'A khaki sleeveless vest, open at the front' },
  { id: 'pants', name: 'Pants', placeholder: 'Loose faded trousers, rolled at the ankle' },
  { id: 'outfit', name: 'Full outfit', placeholder: 'Navy coverall with reflective strips', note: 'A ready-made textured outfit fills Shirt and Pants together.' },
  { id: 'shoes', name: 'Shoes', placeholder: 'Rubber deck boots' },
  { id: 'equipment', name: 'Equipment', placeholder: 'An old rifle with a wooden stock' },
  { id: 'placement', name: 'Equipment placement', placeholder: 'Rifle held up in the right hand' },
  { id: 'accessories', name: 'Other accessories', placeholder: 'A canvas shoulder bag, worn across the body' },
];

const IDS = MODULES.map((m) => m.id);
export const isModuleId = (x: unknown): x is ModuleId => typeof x === 'string' && (IDS as string[]).includes(x);
export const moduleName = (m: ModuleId) => MODULES.find((d) => d.id === m)?.name ?? m;

// ---- Swatches (screen colours; sent as linear [r,g,b]) ----------------------------------------

export const SKIN_TONES: Swatch[] = [
  { id: 'light', label: 'Light', hex: '#F1C8A8' },
  { id: 'medium', label: 'Medium', hex: '#B07A52' },
  { id: 'dark', label: 'Dark', hex: '#4A2C1C' },
];

export const SHIRT_SWATCHES: Swatch[] = [
  { id: 'white', label: 'White', hex: '#EEEEEE' },
  { id: 'mustard', label: 'Mustard', hex: '#D6A63A' },
  { id: 'khaki', label: 'Khaki', hex: '#B5A77A' },
  { id: 'navy', label: 'Navy', hex: '#1F2F55' },
  { id: 'olive', label: 'Olive', hex: '#5B6236' },
  { id: 'orange', label: 'Orange', hex: '#E86A1A' },
  { id: 'black', label: 'Black', hex: '#1C1C1C' },
];

export const PANTS_SWATCHES: Swatch[] = [
  { id: 'khaki', label: 'Khaki', hex: '#B5A77A' },
  { id: 'navy', label: 'Navy', hex: '#1F2F55' },
  { id: 'olive', label: 'Olive', hex: '#5B6236' },
  { id: 'rust', label: 'Rust', hex: '#B0563F' },
  { id: 'black', label: 'Black', hex: '#1C1C1C' },
  { id: 'grey', label: 'Grey', hex: '#9A9A9A' },
];

/** Shoes keep the earlier gear hexes, so colours already saved on a person still light up. */
export const SHOE_SWATCHES: Swatch[] = GEAR_SWATCHES.filter((s) => ['black', 'brown', 'navy', 'grey'].includes(s.id));

/** "mustard", "custom colour" (set, but not one of ours), or null when unset. */
export function colourName(value: Vec3 | null | undefined, swatches: Swatch[]): string | null {
  const on = selectedSwatch(value, swatches);
  if (on === 'default') return null;
  if (on === null) return 'custom colour';
  return swatches.find((s) => s.id === on)!.label.toLowerCase();
}

// ---- Presets --------------------------------------------------------------------------------

export const SHIRT_STYLES = [
  { id: 'tshirt', label: 'T-shirt' }, { id: 'singlet', label: 'Singlet' }, { id: 'long_sleeve', label: 'Long sleeve' }, { id: 'polo', label: 'Polo' },
];
export const PANTS_STYLES = [{ id: 'shorts', label: 'Shorts' }, { id: 'trousers', label: 'Trousers' }, { id: 'cargo', label: 'Cargo' }];

/**
 * Hair styles. GET /api/catalog does not list them, so these copy `catalog/crew/male_default.json`
 * options.hair. null = the gender's default style, "none" = bald.
 */
export const HAIR_STYLES: { id: string | null; label: string }[] = [
  { id: null, label: 'Default' }, { id: 'short01', label: 'Short 1' }, { id: 'short02', label: 'Short 2' },
  { id: 'short03', label: 'Short 3' }, { id: 'short04', label: 'Short 4' }, { id: 'afro01', label: 'Afro' },
  { id: 'bob01', label: 'Bob 1' }, { id: 'bob02', label: 'Bob 2' }, { id: 'braid01', label: 'Braid' },
  { id: 'long01', label: 'Long' }, { id: 'ponytail01', label: 'Ponytail' }, { id: 'none', label: 'Bald' },
];

/** Accessories (§6): `accessories[]` names, plus the two catalog hats, which live in `clothes.hat`. */
export const ACCESSORIES = [
  { id: 'cap', label: 'Cap' }, { id: 'sunglasses', label: 'Sunglasses' }, { id: 'ear_defenders', label: 'Ear defenders' }, { id: 'gloves', label: 'Gloves' },
];
export const HATS = [{ id: 'fedora01', label: 'Fedora' }, { id: 'fedora_cocked', label: 'Fedora, cocked' }];

export function hairLabel(style: string | null | undefined): string {
  return HAIR_STYLES.find((h) => h.id === (style ?? null))?.label ?? assetLabel(style ?? '');
}

/** Outfit chip names: "male_worksuit01" → "Work overalls", "male_casualsuit03" → "Casual 3". */
export function suitLabel(name: string): string {
  const m = name.match(/^(male|female)_([a-z]+?)suit0?(\d+)$/);
  if (!m) return assetLabel(name);
  const kind: Record<string, string> = { work: 'Work overalls', casual: 'Casual', elegant: 'Business', sport: 'Sport' };
  const base = kind[m[2]] ?? assetLabel(m[2]);
  return m[3] === '1' && m[2] !== 'casual' ? base : `${base} ${m[3]}`;
}

/** Suits that suit the body (male_* for male, female_* for female), plus whatever is worn now. */
export function suitsFor(suits: string[], gender: string, current: string | null): string[] {
  const pre = gender === 'female' ? 'female_' : 'male_';
  return suits.filter((s) => s.startsWith(pre) || s === current || !/^(male|female)_/.test(s));
}

export function shoeLabel(name: string): string {
  return name === 'shoes01' ? 'Work shoes' : assetLabel(name);
}

// ---- Described items --------------------------------------------------------------------------

/**
 * Which module a described item sits in: its `module` tag; untagged items (made before modules)
 * are `equipment` if their code makes Equipment__ objects, else `shirt` (§6).
 */
export function moduleOfItem(item: Pick<CustomItem, 'module' | 'code'>): ModuleId {
  if (isModuleId(item.module)) return item.module;
  return (item.code ?? '').includes('Equipment__') ? 'equipment' : 'shirt';
}

export function itemsInModule(items: CustomItem[] | undefined, m: ModuleId): CustomItem[] {
  return (items ?? []).filter((i) => moduleOfItem(i) === m);
}

/** The chat line for "Make it": "Shirt: a khaki vest". Null when blank. */
export function moduleMessage(m: ModuleId, text: string): string | null {
  const t = text.trim().replace(/\s+/g, ' ');
  return t ? `${moduleName(m)}: ${t}` : null;
}

/** `changed_modules` from a chat job's result, keeping only real module ids. */
export function changedModules(result: unknown): ModuleId[] {
  const v = (result && typeof result === 'object') ? (result as { changed_modules?: unknown }).changed_modules : undefined;
  return Array.isArray(v) ? v.filter(isModuleId) : [];
}

// ---- Patches (sent through POST /spec; no AI) -----------------------------------------------

type Spec = Partial<CrewSpec>;
type Patch = Record<string, unknown>;

/**
 * A preset replaces what was described for its module: the patch also removes that module's
 * described items (the server does the same; sending it keeps the screen right at once).
 */
export function presetPatch(spec: Spec, m: ModuleId, patch: Patch): Patch {
  const gone = itemsInModule(spec.custom_items, m);
  return gone.length ? { ...patch, custom_items: (spec.custom_items ?? []).filter((i) => !gone.includes(i)) } : patch;
}

/**
 * Shirt or pants preset. A garment sets `suit` to null (an outfit draws instead of shirt and pants).
 * Picking a style keeps the colour already chosen; the first time, the swatch row's first colour.
 */
export function garmentStylePatch(spec: Spec, k: 'shirt' | 'pants', style: string | null): Patch {
  if (style === null) return presetPatch(spec, k, { clothes: { [k]: null } });
  const cur = spec.clothes?.[k];
  const first = (k === 'shirt' ? SHIRT_SWATCHES : PANTS_SWATCHES)[0];
  const g: Garment = { style, color: cur?.color ?? hexToLinear(first.hex) };
  return presetPatch(spec, k, { clothes: { [k]: g, suit: null } });
}

/** A swatch under Shirt or Pants. Null when there is no style to colour yet. */
export function garmentColorPatch(spec: Spec, k: 'shirt' | 'pants', color: Vec3): Patch | null {
  const cur = spec.clothes?.[k];
  if (!cur) return null;
  return { clothes: { [k]: { style: cur.style, color }, suit: null } };
}

/** Described shirt/pants items an outfit would remove. The screen asks before it does. */
export function outfitWouldRemove(spec: Spec): CustomItem[] {
  return (spec.custom_items ?? []).filter((i) => ['shirt', 'pants'].includes(moduleOfItem(i)));
}

/** Choosing an outfit nulls shirt and pants and removes shirt/pants described items; None just clears it. */
export function outfitPatch(spec: Spec, suit: string | null): Patch {
  if (suit === null) return { clothes: { suit: null } };
  const patch: Patch = { clothes: { suit, shirt: null, pants: null } };
  const gone = outfitWouldRemove(spec);
  if (gone.length) patch.custom_items = (spec.custom_items ?? []).filter((i) => !gone.includes(i));
  return patch;
}

export const equipmentName = (e: Equipment) => (typeof e === 'string' ? e : e.name);

/** Add or remove a catalog item, keeping other entries (and their colour and place) as they are. */
export function toggleEquipment(list: Equipment[] | undefined, name: string): Equipment[] {
  const l = list ?? [];
  return l.some((e) => equipmentName(e) === name) ? l.filter((e) => equipmentName(e) !== name) : [...l, name];
}

export const accessoryName = (a: Accessory) => (typeof a === 'string' ? a : a.name);

/** Toggle an accessory by name, keeping the others (and their colour) as they are. */
export function toggleAccessory(list: Accessory[] | undefined, name: string): Accessory[] {
  const l = list ?? [];
  return l.some((a) => accessoryName(a) === name) ? l.filter((a) => accessoryName(a) !== name) : [...l, name];
}

// ---- Placement ------------------------------------------------------------------------------

export const PLACES: { id: Place; label: string }[] = [
  { id: 'right_hand', label: 'Right hand' }, { id: 'left_hand', label: 'Left hand' }, { id: 'chest', label: 'Chest' },
  { id: 'back', label: 'Back' }, { id: 'hip', label: 'Hip' }, { id: 'head', label: 'Head' },
];
export const placeLabel = (p: Place) => PLACES.find((x) => x.id === p)?.label ?? p;

/** Worn items with one fixed spot: no `place` (§6). */
export const FIXED_SPOT = ['life_vest', 'helmet'];

/**
 * Where an item without a `place` is drawn today: the catalog builders put binoculars on the chest
 * and the radio on the right hip; anything else held goes in the right hand.
 */
const DEFAULT_PLACE: Record<string, Place> = { binoculars: 'chest', radio: 'hip' };

export interface PlacementRow { key: string; label: string; kind: 'equipment' | 'item'; ref: string; place: Place | 'fixed' }

export function placementRows(spec: Spec): PlacementRow[] {
  const rows: PlacementRow[] = (spec.equipment ?? []).map((e) => {
    const name = equipmentName(e);
    const place = typeof e === 'string' ? undefined : e.place;
    return {
      key: `e:${name}`, label: assetLabel(name), kind: 'equipment', ref: name,
      place: FIXED_SPOT.includes(name) ? 'fixed' : place ?? DEFAULT_PLACE[name] ?? 'right_hand',
    };
  });
  for (const i of itemsInModule(spec.custom_items, 'equipment')) {
    rows.push({ key: `i:${i.id}`, label: i.description || i.id, kind: 'item', ref: i.id, place: i.place ?? 'right_hand' });
  }
  return rows;
}

/** Move one equipment entry (by name) or one described equipment item (by id). The whole array goes. */
export function placePatch(spec: Spec, row: Pick<PlacementRow, 'kind' | 'ref'>, place: Place): Patch {
  if (row.kind === 'equipment') {
    return {
      equipment: (spec.equipment ?? []).map((e): Equipment => {
        if (equipmentName(e) !== row.ref) return e;
        const entry: EquipmentEntry = typeof e === 'string' ? { name: e } : { ...e };
        return { ...entry, place };
      }),
    };
  }
  return { custom_items: (spec.custom_items ?? []).map((i) => (i.id === row.ref ? { ...i, place } : i)) };
}

// ---- Summaries (the collapsed row's one line) -------------------------------------------------

const described = (items: CustomItem[]) => items.map((i) => i.description || i.id).join(', ');
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function moduleSummary(spec: Spec, m: ModuleId): string {
  const body = spec.body;
  const clothes = spec.clothes;
  const items = itemsInModule(spec.custom_items, m);
  const join = (...xs: (string | null | undefined | false)[]) => xs.filter(Boolean).join(' · ');
  switch (m) {
    case 'gender':
      return body ? join(body.gender === 'female' ? 'Female' : 'Male', `${body.height_cm} cm`, `${body.age_years} yrs`) : '';
    case 'skin':
      return body?.skin ? (SKIN_TONES.find((s) => s.id === body.skin)?.label ?? cap(body.skin)) : '';
    case 'hair':
      if (items.length) return `Described: ${described(items)}`;
      return join(hairLabel(body?.hair), body?.hair !== 'none' && colourName(body?.hair_color, HAIR_SWATCHES));
    case 'shirt':
    case 'pants': {
      if (items.length) return `Described: ${described(items)}`;
      if (clothes?.suit) return 'Covered by the full outfit';
      const g = clothes?.[m];
      const styles = m === 'shirt' ? SHIRT_STYLES : PANTS_STYLES;
      if (!g) return 'None';
      return join(styles.find((s) => s.id === g.style)?.label ?? assetLabel(g.style), colourName(g.color, m === 'shirt' ? SHIRT_SWATCHES : PANTS_SWATCHES));
    }
    case 'outfit':
      return clothes?.suit ? join(suitLabel(clothes.suit), clothes.suit_color && 'tinted') : 'None';
    case 'shoes':
      if (items.length) return `Described: ${described(items)}`;
      return clothes?.shoes ? join(shoeLabel(clothes.shoes), colourName(clothes.shoes_color, SHOE_SWATCHES)) : 'Barefoot';
    case 'equipment': {
      const names = [...(spec.equipment ?? []).map((e) => assetLabel(equipmentName(e))), ...items.map((i) => i.description || i.id)];
      return names.length ? names.join(', ') : 'None';
    }
    case 'placement': {
      const rows = placementRows(spec).filter((r) => r.place !== 'fixed');
      return rows.length ? rows.map((r) => `${r.label}: ${placeLabel(r.place as Place)}`).join(', ') : 'Nothing held';
    }
    case 'accessories': {
      const hat = clothes?.hat ? (HATS.find((h) => h.id === clothes.hat)?.label ?? assetLabel(clothes.hat)) : null;
      const names = [hat, ...(spec.accessories ?? []).map(accessoryName).map((a) => ACCESSORIES.find((x) => x.id === a)?.label ?? assetLabel(a)), ...items.map((i) => i.description || i.id)];
      const list = names.filter(Boolean);
      return list.length ? list.join(', ') : 'None';
    }
  }
}
