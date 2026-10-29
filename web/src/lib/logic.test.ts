import { describe, expect, it } from 'vitest';
import type { Job, Part } from '../types';
import { hiddenParts, isPartName, jointsOf, levelsFor, ownNodes, partLabel, resolvePart } from './parts';
import { MAX_POLL_FAILURES, describe as describePoll, nextDelayMs, pollReducer, type PollState } from './jobs';
import { blenderToGltf, cameraFor, gltfToBlender, jointAxisGltf } from './frames';
import { assetLabel, clothesSlot, mergePatch, toggleIn } from './spec';
import { startsNewResearch, targetFor } from './routing';
import { fileType, refuse, sizeText } from './attachments';
import {
  boxText, boxToPercent, clampPan, countLine, coverageSummary, filterPhotos, fitSize, hostOf, photoIdsIn, photoStatus, provenanceRows,
  safeHref, shortHash, sizeText as photoSize, statusOptions, viewOptions, zoomAt,
} from './photos';
import type { ResearchPhoto } from '../types';

const part = (path: string, parent: string | null, extra: Partial<Part> = {}): Part => ({
  path, label: '', parent, joint: null, station: null,
  bounds_min: [0, 0, 0], bounds_max: [1, 1, 1], triangles: 10, ...extra,
});

describe('part labels', () => {
  it('replaces __ with › and _ with a space', () => {
    expect(partLabel('Roof__Searchlight')).toBe('Roof › Searchlight');
    expect(partLabel('Transom__Outboard_1')).toBe('Transom › Outboard 1');
    expect(partLabel('Cabin__Door__Handle')).toBe('Cabin › Door › Handle');
  });
  it('recognises part names and rejects stations, sockets and junk', () => {
    expect(isPartName('Roof__Searchlight')).toBe(true);
    expect(isPartName('Transom__Outboard_1')).toBe(true);
    expect(isPartName('station__helm')).toBe(false);
    expect(isPartName('socket_bridge_eye')).toBe(false);
    expect(isPartName('Scene')).toBe(false);
    expect(isPartName('Roof Searchlight')).toBe(false);
  });
});

describe('nearest part from a node chain', () => {
  const parts = [part('Roof__Searchlight', 'Cabin__Cabin'), part('Cabin__Cabin', 'Hull__Hull'), part('Hull__Hull', null)];
  const known = parts.map((p) => p.path);

  it('walks up past a multi-material child that only looks like a part', () => {
    // three.js splits multi-material meshes into children named <node>_<i>
    expect(resolvePart(['Roof__Searchlight_1', 'Roof__Searchlight', 'Cabin__Cabin', 'Scene'], known)).toBe('Roof__Searchlight');
  });
  it('returns the nearest listed part, not the outermost', () => {
    expect(resolvePart(['mesh_0', 'Cabin__Cabin', 'Hull__Hull'], known)).toBe('Cabin__Cabin');
  });
  it('returns null when the click hit nothing that is a part', () => {
    expect(resolvePart(['Water', 'Scene'], known)).toBeNull();
  });
  it('falls back to the naming rule without a parts list', () => {
    expect(resolvePart(['Cube', 'Roof__Horn', 'Scene'])).toBe('Roof__Horn');
    expect(resolvePart(['station__helm', 'Scene'])).toBeNull();
  });
});

describe('a part\'s own nodes (what its highlight lights)', () => {
  type N = { name: string; children: N[] };
  const n = (name: string, ...children: N[]): N => ({ name, children });
  // Hull__Main is the root every other part hangs from, as the ship builders emit it.
  const tree = n('Hull__Main',
    n('Hull__Main_1'), n('Hull__Main_2'),
    n('Accommodation__House', n('Accommodation__House_1'), n('Bridge__Wheelhouse')),
    n('station__helm', n('helm_marker')),
    n('Cargo__Hatch_01', n('Cargo__Bay_01')));
  const known = ['Hull__Main', 'Accommodation__House', 'Bridge__Wheelhouse', 'station__helm', 'Cargo__Hatch_01', 'Cargo__Bay_01'];
  const names = (node: N, k?: string[]) => ownNodes(node, k).map((x) => x.name);

  it('the hull is the hull and its material pieces, not the parts attached to it', () => {
    expect(names(tree, known)).toEqual(['Hull__Main', 'Hull__Main_1', 'Hull__Main_2', 'station__helm', 'helm_marker']);
  });
  it('a mid-level part keeps its own pieces and drops the part on top of it', () => {
    expect(names(tree.children[2], known)).toEqual(['Accommodation__House', 'Accommodation__House_1']);
  });
  it('matches picking: every own node resolves to the part, every other node does not', () => {
    const chains: Array<[N, string[]]> = [];
    const walk = (node: N, up: string[]) => { const c = [node.name, ...up]; chains.push([node, c]); node.children.forEach((x) => walk(x, c)); };
    walk(tree, ['Scene']);
    for (const p of known.filter((k) => !k.startsWith('station__'))) {
      const node = chains.find(([x]) => x.name === p)![0];
      const own = new Set(ownNodes(node, known));
      for (const [x, chain] of chains) {
        if (own.has(x)) expect(resolvePart(chain, known)).toBe(p);
        else if (chain.includes(p)) expect(resolvePart(chain, known)).not.toBe(p);
      }
    }
  });
});

describe('level picker', () => {
  const parts = [
    part('Hull__Hull', null),
    part('Cabin__Cabin', 'Hull__Hull'),
    part('Roof__Searchlight', 'Cabin__Cabin', { label: 'Roof › Searchlight' }),
    part('Roof__Searchlight__Lens', 'Roof__Searchlight'),
    part('station__roof', 'Roof__Searchlight', { station: {} }),
  ];
  it('lists ancestors outermost first, then the part, then its children, never stations', () => {
    const l = levelsFor('Roof__Searchlight', parts);
    expect(l.map((x) => x.path)).toEqual(['Hull__Hull', 'Cabin__Cabin', 'Roof__Searchlight', 'Roof__Searchlight__Lens']);
    expect(l.map((x) => x.relation)).toEqual(['ancestor', 'ancestor', 'self', 'child']);
    expect(l[3].label).toBe('Roof › Searchlight › Lens');
  });
  it('survives a parent cycle in bad data', () => {
    const cyc = [part('A__A', 'B__B'), part('B__B', 'A__A')];
    expect(levelsFor('A__A', cyc).map((x) => x.path)).toEqual(['B__B', 'A__A', 'B__B']);
  });
});

describe('joints and hidden parts', () => {
  const j = { axis: 'z' as const, min_deg: -30, max_deg: 30, priority: 'P0', kind: 'revolute' };
  const parts = [part('Transom__Outboard_1', null, { joint: j }), part('Hull__Hull', null), part('Outboards__Prop', null, { bounds_max: [1, 1, -0.2] })];
  it('applies spec joint_limits over the built limits', () => {
    const js = jointsOf(parts, { Transom__Outboard_1: { min_deg: -10, max_deg: 45 } });
    expect(js).toHaveLength(1);
    expect(js[0].joint).toMatchObject({ min_deg: -10, max_deg: 45, axis: 'z' });
  });
  it('treats parts wholly below the waterline as hidden', () => {
    expect(hiddenParts(parts).map((p) => p.path)).toEqual(['Outboards__Prop']);
  });
});

describe('frames', () => {
  it('maps Blender (+X bow, +Y port, +Z up) to glTF Y-up and back', () => {
    expect(blenderToGltf([10, 2, 3])).toEqual([10, 3, -2]);
    expect(gltfToBlender(blenderToGltf([10, 2, 3]))).toEqual([10, 2, 3]);
  });
  it('maps joint axes into the glTF local frame', () => {
    expect(jointAxisGltf('z')).toEqual([0, 1, 0]);
    expect(jointAxisGltf('y')).toEqual([0, 0, -1]);
    expect(jointAxisGltf('x')).toEqual([1, 0, 0]);
  });
  it('puts the port camera on the port (−Z) side and the bow camera ahead (+X)', () => {
    const b = { min: [-10, -2, -3] as [number, number, number], max: [10, 4, 3] as [number, number, number] };
    expect(cameraFor('port', b).position[2]).toBeLessThan(-3);
    expect(cameraFor('starboard', b).position[2]).toBeGreaterThan(3);
    expect(cameraFor('bow', b).position[0]).toBeGreaterThan(10);
    expect(cameraFor('top', b).position[1]).toBeGreaterThan(4);
    expect(cameraFor('port', b).target).toEqual([0, 1, 0]);
  });
});

describe('spec patches', () => {
  it('coalesces patches deeply, later wins, arrays replaced', () => {
    const a = { body: { height_cm: 170, weight: 0.5 }, animations: ['idle'] };
    const b = { body: { height_cm: 180 }, animations: ['idle', 'walk'] };
    expect(mergePatch(a, b)).toEqual({ body: { height_cm: 180, weight: 0.5 }, animations: ['idle', 'walk'] });
    expect(a.body.height_cm).toBe(170);
  });
  it('toggles list membership', () => {
    expect(toggleIn(['a', 'b'], 'a')).toEqual(['b']);
    expect(toggleIn(['a'], 'b')).toEqual(['a', 'b']);
  });
  it('guesses the clothes slot from the asset name', () => {
    expect(clothesSlot('male_worksuit01')).toBe('suit');
    expect(clothesSlot('shoes01')).toBe('shoes');
    expect(clothesSlot('combat_helmet')).toBe('hat');
    expect(clothesSlot('fedora_cocked')).toBe('hat');
    expect(assetLabel('male_worksuit01')).toBe('Male worksuit 01');
  });
});

describe('routing', () => {
  it('opens the right screen for each destination', () => {
    expect(targetFor('ship_build', 'p1')?.path).toBe('/ship/p1/build');
    expect(targetFor('ship_research', null)?.path).toBe('/ship');
    expect(targetFor('animation', 'c1')?.path).toBe('/crew/c1/moves');
    expect(targetFor('clarify', null)).toBeNull();
  });
  it('opens the variant dialog on the source project, or the picker without one', () => {
    expect(targetFor('ship_variant', 'p1')?.path).toBe('/ship/p1/build?variant=1');
    expect(targetFor('ship_variant', null)?.path).toBe('/ship');
    expect(targetFor('crew_variant', 'c1')?.path).toBe('/crew/c1?variant=1');
    expect(targetFor('crew_variant', null)?.path).toBe('/crew');
  });
  it('starts the research itself only for a ship that has no project yet', () => {
    expect(startsNewResearch('ship_research', null)).toBe(true);
    expect(startsNewResearch('ship_research', 'p1')).toBe(false);
    expect(startsNewResearch('ship_build', null)).toBe(false);
    expect(startsNewResearch('crew', null)).toBe(false);
  });
});

describe('job polling state machine', () => {
  const job = (status: Job['status'], extra: Partial<Job> = {}): Job => ({ id: 'j1', status, log: [], result: null, error: null, ...extra });
  const start = (): PollState => pollReducer({ phase: 'idle' }, { type: 'start', jobId: 'j1', now: 0 });

  it('goes queued → running → done and keeps the result', () => {
    let s = start();
    expect(s).toMatchObject({ phase: 'polling', status: 'queued' });
    s = pollReducer(s, { type: 'response', job: job('running', { log: ['Building hull'] }), now: 1000 });
    expect(s).toMatchObject({ phase: 'polling', status: 'running', log: ['Building hull'] });
    s = pollReducer(s, { type: 'response', job: job('done', { result: { version: 4 } }), now: 5000 });
    expect(s).toMatchObject({ phase: 'done', result: { version: 4 } });
    expect(describePoll(s, 6000)).toBe('Done in 5 s');
  });
  it('reports the server error', () => {
    const s = pollReducer(start(), { type: 'response', job: job('error', { error: 'Blender crashed' }), now: 10 });
    expect(s).toMatchObject({ phase: 'error', error: 'Blender crashed' });
  });
  it('ignores answers for a different job', () => {
    const s = start();
    expect(pollReducer(s, { type: 'response', job: { ...job('done'), id: 'other' }, now: 1 })).toBe(s);
  });
  it('tolerates a few network failures, resets on success, then gives up', () => {
    let s = start();
    for (let i = 0; i < MAX_POLL_FAILURES - 1; i++) s = pollReducer(s, { type: 'network_error', message: 'x', now: i });
    expect(s.phase).toBe('polling');
    expect(nextDelayMs(s, 0)).toBe(4000);
    s = pollReducer(s, { type: 'response', job: job('running'), now: 10 });
    expect(s).toMatchObject({ phase: 'polling', failures: 0 });
    for (let i = 0; i < MAX_POLL_FAILURES; i++) s = pollReducer(s, { type: 'network_error', message: 'refused', now: i });
    expect(s.phase).toBe('error');
  });
  it('does not change a finished job on a late event', () => {
    const done = pollReducer(start(), { type: 'response', job: job('done'), now: 1 });
    expect(pollReducer(done, { type: 'network_error', message: 'x', now: 2 })).toBe(done);
    expect(pollReducer(done, { type: 'response', job: job('running'), now: 3 })).toBe(done);
  });
  it('describes progress with elapsed time and the last log line', () => {
    const s = pollReducer(start(), { type: 'response', job: job('running', { log: ['a', 'Exporting glTF'] }), now: 0 });
    expect(describePoll(s, 14_000, 'Building')).toBe('Building · 14 s · Exporting glTF');
  });
});

import { groupPins, pinSummary, shortTime } from './pins';
import type { Pin } from '../types';

describe('comment summaries', () => {
  const pin = (status: Pin['status'], texts: string[]): Pin => ({
    id: `p${texts.length}${status}`, part: 'Funnel__Main', point: [0, 0, 0], status, author: 'You',
    messages: texts.map((text, i) => ({ who: i % 2 ? 'AI' : 'You', text, at: `2026-10-29T00:3${i}:00Z` })),
  });
  it('keeps the first comment, the latest reply and the reply count', () => {
    const s = pinSummary(pin('waiting', ['make it white', 'Done, all white']));
    expect(s.first).toBe('make it white');
    expect(s.latest?.text).toBe('Done, all white');
    expect(s.replies).toBe(1);
    expect(s.lastAt).toBe('2026-10-29T00:31:00Z');
  });
  it('handles a thread with no messages', () => {
    expect(pinSummary(pin('open', []))).toEqual({ first: '', latest: null, replies: 0, lastAt: null });
  });
  it('splits open from resolved but keeps the number shown on the model', () => {
    const g = groupPins([pin('resolved', ['a']), pin('open', ['b', 'c']), pin('waiting', ['d', 'e', 'f'])]);
    expect(g.open.map((x) => x.n)).toEqual([2, 3]);
    expect(g.resolved.map((x) => x.n)).toEqual([1]);
  });
  it('formats time and ignores bad timestamps', () => {
    expect(shortTime('nonsense')).toBe('');
    expect(shortTime(null)).toBe('');
    expect(shortTime('2026-10-29T00:39:39Z')).toMatch(/^\d{1,2} Oct, \d{2}:\d{2}$/);
  });
});

import { activityLines, variantSteps } from './variant';
import { projectPath } from './routing';

describe('variant review', () => {
  const draft = (stage: string, error: string | null = null) => ({ stage, job_id: 'j', research: false, scene: null, error }) as const;
  const states = (s: { key: string; state: string }[]) => s.map((x) => `${x.key}:${x.state}`).join(' ');

  it('lists only the steps that will happen', () => {
    expect(variantSteps(draft('copying'), { brief: false }).map((s) => s.key)).toEqual(['copy', 'review']);
    expect(variantSteps({ ...draft('copying'), research: true }, { brief: true }).map((s) => s.key)).toEqual(['copy', 'research', 'change', 'review']);
  });
  it('follows the stage', () => {
    expect(states(variantSteps(draft('copying'), { brief: true }))).toBe('copy:active change:todo review:todo');
    expect(states(variantSteps(draft('changing'), { brief: true }))).toBe('copy:done change:active review:todo');
    expect(states(variantSteps(draft('ready'), { brief: true }))).toBe('copy:done change:done review:active');
    expect(states(variantSteps(null, { brief: true }))).toBe('copy:done change:done review:done');
  });
  it('shows where it failed', () => {
    expect(states(variantSteps(draft('failed', 'no hull'), { brief: true }))).toBe('copy:failed change:todo review:todo');
    expect(states(variantSteps(draft('ready', 'AI down'), { brief: true }))).toBe('copy:done change:failed review:active');
  });
  it('keeps only the lines written for people', () => {
    expect(activityLines(['$ blender -b', '• Copying PC 303 v4', 'Read prefs', '• AI: Done.'])).toEqual(['Copying PC 303 v4', 'AI: Done.']);
  });
  it('opens a draft on its review screen', () => {
    expect(projectPath({ kind: 'ship', id: 'p1', draft: { stage: 'ready' } })).toBe('/variant/p1');
    expect(projectPath({ kind: 'ship', id: 'p1', draft: null })).toBe('/ship/p1/build');
    expect(projectPath({ kind: 'crew', id: 'c1' })).toBe('/crew/c1');
  });
});

describe('chat attachments', () => {
  const MB = 1024 * 1024;
  it('accepts pictures, PDFs and text, fixing common wrong types', () => {
    expect(refuse('bow.png', 'image/png', 1000)).toBeNull();
    expect(fileType('bow.jpg', 'image/jpg')).toBe('image/jpeg');
    expect(fileType('notes.md', '')).toBe('text/markdown');
    expect(refuse('drawing.pdf', 'application/pdf', 20 * MB)).toBeNull();
  });
  it('refuses with a sentence saying what to do', () => {
    expect(refuse('spec.docx', 'application/msword', 10)).toMatch(/PDF/);
    expect(refuse('IMG.HEIC', 'image/heic', 10)).toMatch(/JPEG/);
    expect(refuse('model.glb', 'model/gltf-binary', 10)).toMatch(/only pictures/);
    expect(refuse('x.png.exe', '', 10)).toMatch(/only pictures/);
  });
  it('holds the same size limits as the server', () => {
    expect(refuse('a.png', 'image/png', 5 * MB)).toBeNull();
    expect(refuse('a.png', 'image/png', 5 * MB + 1)).toMatch(/limit for this kind of file is 5 MB/);
    expect(refuse('a.pdf', 'application/pdf', 20 * MB + 1)).toMatch(/20 MB/);
    expect(refuse('a.txt', 'text/plain', 0)).toMatch(/empty/);
  });
  it('writes sizes for people', () => {
    expect(sizeText(900)).toBe('900 B');
    expect(sizeText(2048)).toBe('2 KB');
    expect(sizeText(3.25 * MB)).toBe('3.3 MB');
  });
});

import { HAIR_SWATCHES, hexToLinear, madeSomethingNew, removeItemPatch, selectedSwatch } from './describe';

describe('made by description', () => {
  const item = (id: string, code: string, description = 'thing') => ({ id, code, description });

  it('converts a screen colour to the linear colour Blender takes', () => {
    expect(hexToLinear('#FFFFFF')).toEqual([1, 1, 1]);
    expect(hexToLinear('#000000')).toEqual([0, 0, 0]);
    // sRGB mid-grey is ~0.216 linear, not 0.5: sending 0.5 would look much paler
    expect(hexToLinear('#808080')[0]).toBeCloseTo(0.216, 2);
  });

  it('lights the swatch the spec holds, default when unset, none for an AI-chosen colour', () => {
    const blonde = hexToLinear(HAIR_SWATCHES.find((s) => s.id === 'blonde')!.hex);
    expect(selectedSwatch(blonde, HAIR_SWATCHES)).toBe('blonde');
    expect(selectedSwatch(null, HAIR_SWATCHES)).toBe('default');
    expect(selectedSwatch(undefined, HAIR_SWATCHES)).toBe('default');
    expect(selectedSwatch([0.2, 0.9, 0.2], HAIR_SWATCHES)).toBeNull();
  });

  it('removes one item and sends the whole remaining list', () => {
    const items = [item('a', 'Clothes__X'), item('b', 'Equipment__Y')];
    expect(removeItemPatch(items, 'a')).toEqual({ custom_items: [items[1]] });
    expect(removeItemPatch(items, 'zzz').custom_items).toHaveLength(2);
    expect(removeItemPatch(undefined, 'a')).toEqual({ custom_items: [] });
  });
});

describe('picker offer', () => {
  const item = (id: string) => ({ id, description: 'x', code: "crew.garment('Clothes__X', ['torso'], (1, 1, 1))" });
  it('follows only a newly made item', () => {
    expect(madeSomethingNew(['item_1'], [item('item_1')])).toBe(false);   // a reply that made nothing
    expect(madeSomethingNew(['item_1'], [item('item_1'), item('item_2')])).toBe(true);
    expect(madeSomethingNew(null, [item('item_1')])).toBe(false);         // page just opened
    expect(madeSomethingNew([], undefined)).toBe(false);
  });
});

import {
  SHIRT_SWATCHES, changedModules, colourName, garmentColorPatch, garmentStylePatch, itemsInModule, moduleMessage, moduleOfItem,
  moduleSummary, outfitPatch, outfitWouldRemove, placePatch, placesFor, presetPatch, placementRows, suitLabel, suitsFor, toggleAccessory, toggleEquipment,
} from './modules';
import type { CrewSpec, CustomItem } from '../types';

describe('crew modules', () => {
  const ci = (id: string, code: string, extra: Partial<CustomItem> = {}): CustomItem => ({ id, code, description: id, ...extra });
  const base = (): Partial<CrewSpec> => ({
    body: { gender: 'male', height_cm: 178, weight: 0.5, muscle: 0.5, age_years: 30, skin: 'medium' },
    clothes: { suit: 'male_worksuit01', shoes: 'shoes01', hat: null, suit_color: null },
    equipment: ['life_vest'], animations: ['idle'],
  });

  it('puts a described item in its tagged module, and infers untagged ones from the code', () => {
    expect(moduleOfItem(ci('a', 'Clothes__Vest', { module: 'accessories' }))).toBe('accessories');
    expect(moduleOfItem(ci('b', 'obj.name = "Equipment__Rifle"'))).toBe('equipment');
    expect(moduleOfItem(ci('c', 'crew.garment("Clothes__Singlet")'))).toBe('shirt');
    // a tag that is not a module falls back to inference rather than vanishing
    expect(moduleOfItem({ code: 'Equipment__X', module: 'nonsense' as never })).toBe('equipment');
    const items = [ci('a', 'Clothes__A', { module: 'pants' }), ci('b', 'Equipment__B'), ci('c', 'Clothes__C')];
    expect(itemsInModule(items, 'shirt').map((i) => i.id)).toEqual(['c']);
    expect(itemsInModule(items, 'pants').map((i) => i.id)).toEqual(['a']);
    expect(itemsInModule(undefined, 'equipment')).toEqual([]);
  });

  it('choosing a shirt or pants style clears the outfit and keeps the chosen colour', () => {
    const s = base();
    const p = garmentStylePatch(s, 'shirt', 'singlet') as { clothes: Record<string, unknown> };
    expect(p.clothes.suit).toBeNull();
    expect(p.clothes.shirt).toEqual({ style: 'singlet', color: hexToLinear(SHIRT_SWATCHES[0].hex) });
    s.clothes!.shirt = { style: 'polo', color: [0.1, 0.2, 0.3] };
    expect((garmentStylePatch(s, 'shirt', 'tshirt') as { clothes: { shirt: unknown } }).clothes.shirt).toEqual({ style: 'tshirt', color: [0.1, 0.2, 0.3] });
    // None removes only the garment, and leaves the outfit alone
    expect(garmentStylePatch(s, 'pants', null)).toEqual({ clothes: { pants: null } });
  });

  it('a preset replaces the described items of its own module only', () => {
    const s = base();
    s.custom_items = [ci('vest', 'Clothes__Vest', { module: 'shirt' }), ci('boots', 'Clothes__B', { module: 'shoes' }), ci('old', 'Clothes__Old')];
    const shirt = garmentStylePatch(s, 'shirt', 'polo') as { custom_items: CustomItem[] };
    expect(shirt.custom_items.map((i) => i.id)).toEqual(['boots']);   // 'old' is untagged, so inferred as shirt
    expect((garmentStylePatch(s, 'shirt', null) as { custom_items: CustomItem[] }).custom_items.map((i) => i.id)).toEqual(['boots']);
    expect(presetPatch(s, 'shoes', { clothes: { shoes: null } })).toEqual({ clothes: { shoes: null }, custom_items: [s.custom_items[0], s.custom_items[2]] });
    expect(presetPatch(s, 'hair', { body: { hair: 'afro01' } })).toEqual({ body: { hair: 'afro01' } });
  });

  it('a swatch colours the current style, and does nothing without one', () => {
    const s = base();
    expect(garmentColorPatch(s, 'pants', [1, 0, 0])).toBeNull();
    s.clothes!.pants = { style: 'cargo', color: null };
    expect(garmentColorPatch(s, 'pants', [1, 0, 0])).toEqual({ clothes: { pants: { style: 'cargo', color: [1, 0, 0] }, suit: null } });
  });

  it('choosing an outfit nulls shirt and pants and removes only their described items', () => {
    const s = base();
    s.custom_items = [ci('vest', 'Clothes__Vest', { module: 'shirt' }), ci('shorts', 'Clothes__S', { module: 'pants' }), ci('rifle', 'Equipment__R', { module: 'equipment' }), ci('old', 'Clothes__Old')];
    expect(outfitWouldRemove(s).map((i) => i.id)).toEqual(['vest', 'shorts', 'old']);
    const p = outfitPatch(s, 'male_casualsuit02') as { clothes: Record<string, unknown>; custom_items: CustomItem[] };
    expect(p.clothes).toEqual({ suit: 'male_casualsuit02', shirt: null, pants: null });
    expect(p.custom_items.map((i) => i.id)).toEqual(['rifle']);
    expect(outfitPatch(base(), 'male_worksuit01')).not.toHaveProperty('custom_items');
    expect(outfitPatch(s, null)).toEqual({ clothes: { suit: null } });
  });

  it('places equipment by name or described item by id, touching nothing else', () => {
    const s = base();
    s.equipment = ['life_vest', { name: 'binoculars', color: 'navy' }, 'radio'];
    s.custom_items = [ci('rifle', 'Equipment__Rifle', { module: 'equipment' }), ci('vest', 'Clothes__V', { module: 'shirt' })];
    const rows = placementRows(s);
    expect(rows.map((r) => [r.ref, r.place])).toEqual([['life_vest', 'fixed'], ['binoculars', 'chest'], ['radio', 'hip'], ['rifle', 'right_hand']]);
    expect(placePatch(s, { kind: 'equipment', ref: 'radio' }, 'back')).toEqual({ equipment: ['life_vest', { name: 'binoculars', color: 'navy' }, { name: 'radio', place: 'back' }] });
    expect(placePatch(s, { kind: 'equipment', ref: 'binoculars' }, 'head')).toEqual({ equipment: ['life_vest', { name: 'binoculars', color: 'navy', place: 'head' }, 'radio'] });
    const p = placePatch(s, { kind: 'item', ref: 'rifle' }, 'back') as { custom_items: CustomItem[] };
    expect(p.custom_items[0].place).toBe('back');
    expect(p.custom_items[1]).toEqual(s.custom_items[1]);
  });

  it('offers both hands for described items only, and stores it as the place', () => {
    // Lester, 29 Oct: "carry rifle in both hands". Long described items get the chip; the short
    // catalog binoculars and radio do not (the builder refuses them).
    const s = base();
    s.equipment = ['life_vest', 'radio'];
    s.custom_items = [ci('rifle', 'Equipment__Rifle', { module: 'equipment' })];
    const [, radio, rifle] = placementRows(s);
    expect(placesFor(rifle).map((p) => p.id)).toContain('both_hands');
    expect(placesFor(rifle).find((p) => p.id === 'both_hands')?.label).toBe('Both hands');
    expect(placesFor(radio).map((p) => p.id)).not.toContain('both_hands');
    expect(placesFor(radio)).toHaveLength(6);
    const p = placePatch(s, rifle, 'both_hands') as { custom_items: CustomItem[] };
    expect(p.custom_items[0].place).toBe('both_hands');
    expect(placementRows({ ...s, custom_items: p.custom_items })[2].place).toBe('both_hands');
  });

  it('toggles equipment and accessories without dropping colour or place', () => {
    const list = [{ name: 'radio', place: 'back' as const }, 'life_vest'];
    expect(toggleEquipment(list, 'helmet')).toEqual([...list, 'helmet']);
    expect(toggleEquipment(list, 'life_vest')).toEqual([{ name: 'radio', place: 'back' }]);
    expect(toggleEquipment(list, 'radio')).toEqual(['life_vest']);
    expect(toggleAccessory([{ name: 'cap', color: 'navy' }], 'gloves')).toEqual([{ name: 'cap', color: 'navy' }, 'gloves']);
    expect(toggleAccessory([{ name: 'cap' }], 'cap')).toEqual([]);
    expect(toggleAccessory(['cap', 'gloves'], 'cap')).toEqual(['gloves']);
  });

  it('summarises each collapsed module in one line', () => {
    const s = base();
    expect(moduleSummary(s, 'gender')).toBe('Male · 178 cm · 30 yrs');
    expect(moduleSummary(s, 'skin')).toBe('Medium');
    expect(moduleSummary(s, 'shirt')).toBe('Covered by the full outfit');
    expect(moduleSummary(s, 'outfit')).toBe('Work overalls');
    expect(moduleSummary(s, 'shoes')).toBe('Work shoes');
    expect(moduleSummary(s, 'placement')).toBe('Nothing held');
    expect(moduleSummary(s, 'accessories')).toBe('None');
    s.clothes = { ...s.clothes!, suit: null, shirt: { style: 'singlet', color: hexToLinear('#D6A63A') }, shoes: null };
    expect(moduleSummary(s, 'shirt')).toBe('Singlet · mustard');
    expect(moduleSummary(s, 'pants')).toBe('None');
    expect(moduleSummary(s, 'shoes')).toBe('Barefoot');
    s.custom_items = [{ id: 'v', code: 'Clothes__V', description: 'khaki vest', module: 'shirt' }];
    expect(moduleSummary(s, 'shirt')).toBe('Described: khaki vest');
    s.equipment = ['binoculars'];
    expect(moduleSummary(s, 'placement')).toBe('Binoculars: Chest');
    s.body = { ...s.body!, hair: 'none', hair_color: [0.5, 0.5, 0.5] };
    expect(moduleSummary(s, 'hair')).toBe('Bald');
    s.body = { ...s.body!, hair: 'afro01', hair_color: hexToLinear(HAIR_SWATCHES[0].hex) };
    expect(moduleSummary(s, 'hair')).toBe('Afro · black');
    s.clothes = { ...s.clothes!, hat: 'fedora01' };
    s.accessories = [{ name: 'sunglasses' }, 'ear_defenders'];
    expect(moduleSummary(s, 'accessories')).toBe('Fedora, Sunglasses, Ear defenders');
  });

  it('names swatch colours, and says when a colour is not one of ours', () => {
    expect(colourName(null, SHIRT_SWATCHES)).toBeNull();
    expect(colourName(hexToLinear('#1F2F55'), SHIRT_SWATCHES)).toBe('navy');
    expect(colourName([0.9, 0.1, 0.9], SHIRT_SWATCHES)).toBe('custom colour');
  });

  it('labels and filters outfits by body', () => {
    expect(suitLabel('male_worksuit01')).toBe('Work overalls');
    expect(suitLabel('male_casualsuit03')).toBe('Casual 3');
    expect(suitLabel('female_elegantsuit01')).toBe('Business');
    expect(suitLabel('navy_coverall')).toBe('Navy coverall');
    expect(suitsFor(['male_worksuit01', 'female_sportsuit01', 'navy_coverall'], 'male', 'female_sportsuit01')).toEqual(['male_worksuit01', 'female_sportsuit01', 'navy_coverall']);
    expect(suitsFor(['male_worksuit01', 'female_sportsuit01'], 'female', null)).toEqual(['female_sportsuit01']);
  });

  it('prefixes a description with its module, refuses blanks, and reads changed_modules', () => {
    expect(moduleMessage('shirt', '  a khaki   vest ')).toBe('Shirt: a khaki vest');
    expect(moduleMessage('equipment', '  ')).toBeNull();
    expect(changedModules({ changed_modules: ['shirt', 'bogus', 'pants'] })).toEqual(['shirt', 'pants']);
    expect(changedModules({ message: {} })).toEqual([]);
    expect(changedModules(null)).toEqual([]);
  });
});

import { awaitingApproval, briefFromCarried, buildTabLabel, canApprove, httpErrorText, planNotice, planState, planStatusLabel, summaryLine } from './designPlan';
import type { Attachment, DesignPlan, Message } from '../types';

describe('design plan (ships of any type)', () => {
  const plan = (x: Partial<DesignPlan> = {}): DesignPlan => ({ status: 'draft', summary: ['Size · 38 m'], research_rev: 2, at: '2026-10-29T10:00:00Z', ...x });
  const ship = (x: Record<string, unknown> = {}) => ({ kind: 'ship', base: 'custom', research_rev: 2, design_plan: plan(), plan_built: false, ...x });

  it('reads the plan state, treating a draft against older research as needing a redraft', () => {
    expect(planState(ship({ design_plan: null }))).toBe('none');
    expect(planState(ship())).toBe('draft');
    expect(planState(ship({ research_rev: 3 }))).toBe('redraft');
    expect(planState(ship({ design_plan: plan({ status: 'stale' }) }))).toBe('stale');
    expect(planState(ship({ research_rev: 9, design_plan: plan({ status: 'approved' }) }))).toBe('approved');
  });
  it('labels each state, amber only while it waits for the user', () => {
    expect(planStatusLabel('draft')).toEqual({ text: 'Draft · waiting for you', tone: 'amber' });
    expect(planStatusLabel('redraft').text).toBe('Out of date');
    expect(planStatusLabel('stale').tone).toBe('red');
    expect(planStatusLabel('approved').tone).toBe('green');
    expect(planNotice('draft')).toBeNull();
    expect(planNotice('none')).toMatch(/ask the AI to draft one/);
    expect(planNotice('redraft')).toMatch(/Research changed since this plan/);
    expect(planNotice('stale')).toMatch(/since this plan was built/);
  });
  it('offers Approve only on a current draft of a custom ship, and not while a job runs', () => {
    expect(canApprove(ship(), false)).toBe(true);
    expect(canApprove(ship(), true)).toBe(false);
    expect(canApprove(ship({ research_rev: 3 }), false)).toBe(false);
    expect(canApprove(ship({ design_plan: plan({ status: 'stale' }) }), false)).toBe(false);
    expect(canApprove(ship({ design_plan: plan({ status: 'approved' }) }), false)).toBe(false);
    expect(canApprove(ship({ design_plan: null }), false)).toBe(false);
    expect(canApprove(ship({ base: 'sampan' }), false)).toBe(false);
  });
  it('keeps Build waiting only until the first approved plan is built, even if the plan later goes stale', () => {
    expect(buildTabLabel(ship())).toBe('Build · waiting for your approval');
    expect(awaitingApproval(ship({ design_plan: null }))).toBe(true);
    expect(awaitingApproval(ship({ plan_built: true, design_plan: plan({ status: 'stale' }) }))).toBe(false);
    expect(awaitingApproval(ship({ plan_built: true, research_rev: 7 }))).toBe(false);
    expect(buildTabLabel({ kind: 'ship', base: 'container' })).toBe('Build');
    expect(buildTabLabel(null)).toBe('Build');
  });
  it('splits a summary line into a bold label and text, and flags assumptions', () => {
    expect(summaryLine('Size · 38 m long, 7.6 m beam')).toEqual({ label: 'Size', text: '38 m long, 7.6 m beam', assumed: false });
    expect(summaryLine('Main deck: 24 m long')).toEqual({ label: 'Main deck', text: '24 m long', assumed: false });
    expect(summaryLine('Assumed · beam 7.6 m (typical)').assumed).toBe(true);
    expect(summaryLine('Assumptions: fly-bridge open').assumed).toBe(true);
    // A long sentence with a colon in it is not a label.
    const long = 'The hull is modelled as a planing monohull with a swim platform: see the photo';
    expect(summaryLine(long)).toEqual({ label: null, text: long, assumed: false });
    expect(summaryLine('Hull is a planing form: see the photo').label).toBeNull();
  });
  it('pre-fills the Describe card from what Home carried: the last request and every file, once', () => {
    const att = (id: string): Attachment => ({ id, name: `${id}.jpg`, type: 'image/jpeg', size: 1, url: `/u/${id}` });
    const carried: Message[] = [
      { who: 'You', text: 'create a yacht ship model', at: '', attachments: [att('a'), att('b')] },
      { who: 'AI', text: 'Which kind?', at: '', attachments: [att('z')] },
      { who: 'You', text: 'A 38 m motor yacht', at: '', attachments: [att('b'), att('c')] },
    ];
    const b = briefFromCarried(carried);
    expect(b.text).toBe('A 38 m motor yacht');
    expect(b.attachments.map((a) => a.id)).toEqual(['a', 'b', 'c']);
    expect(briefFromCarried(null)).toEqual({ text: '', attachments: [] });
  });
  it('shows a 409 refusal as the server sentence alone, other failures in full', () => {
    expect(httpErrorText('POST', '/api/projects/p1/chat', 409, ' Go to Research and approve the plan first. ')).toBe('Go to Research and approve the plan first.');
    expect(httpErrorText('POST', '/x', 500, 'boom')).toBe('POST /x failed (500): boom');
    expect(httpErrorText('POST', '/x', 409, { why: 1 })).toBe('POST /x failed (409): {"why":1}');
  });
});

import { convexHull, waterlineCrossings } from './waterline';

describe('hull footprint on the water (keeps water out of the boat)', () => {
  // An open box "boat" 4 m long (x), 2 m wide (z), from 1 m below the waterline to 1 m above:
  // its sides only, no deck, so the water plane would show inside it.
  const corners = (y: number) => [[-2, y, -1], [2, y, -1], [2, y, 1], [-2, y, 1]];
  const lo = corners(-1), hi = corners(1);
  const pos = [...lo, ...hi].flat();
  const index: number[] = [];
  for (let i = 0; i < 4; i++) { const j = (i + 1) % 4; index.push(i, j, 4 + j, i, 4 + j, 4 + i); }

  it('finds where the sides cross the waterline, and nothing from parts clear of it', () => {
    const pts = waterlineCrossings(pos, index);
    expect(pts.length).toBeGreaterThan(0);
    for (const [x, z] of pts) expect(Math.abs(x) === 2 || Math.abs(z) === 1).toBe(true);
    expect(waterlineCrossings([0, 1, 0, 1, 2, 0, 0, 3, 1], null)).toEqual([]); // a mast above the water
    expect(waterlineCrossings([0, -1, 0, 2, 1, 0, 0, 1, 2], null)).toEqual([[1, 0], [0, 1]]);
  });
  it('the footprint is the hull outline: the four corners, counter-clockwise', () => {
    const hull = convexHull(waterlineCrossings(pos, index));
    expect(hull).toHaveLength(4);
    expect(new Set(hull.map((p) => p.join(',')))).toEqual(new Set(['-2,-1', '2,-1', '2,1', '-2,1']));
    let area2 = 0;
    for (let i = 0; i < hull.length; i++) { const [a, b] = [hull[i], hull[(i + 1) % hull.length]]; area2 += a[0] * b[1] - b[0] * a[1]; }
    expect(area2).toBeCloseTo(16); // twice the 4 × 2 area, positive = counter-clockwise
  });
  it('drops interior and collinear points, and gives nothing for a line or a point', () => {
    expect(convexHull([[0, 0], [1, 0], [2, 0], [2, 2], [0, 2], [1, 1]])).toHaveLength(4);
    expect(convexHull([[0, 0], [1, 1], [2, 2]])).toEqual([]);
    expect(convexHull([[0, 0]])).toEqual([]);
  });
});

import { activeFor, newlyFinished } from './jobcenter';

describe('work across models', () => {
  const job = (id: string, status: string, project: string | null = 'p1') => ({
    id, status, project, kind: 'chat', label: id, last: '', error: null, created: '', finished: null,
    project_name: null, project_kind: null,
  }) as const;
  const seen = (...js: { id: string; status: string }[]) => new Map(js.map((j) => [j.id, j.status]));

  it('reports nothing on the first look: old jobs are not news', () => {
    expect(newlyFinished(null, [job('a', 'done')])).toEqual([]);
  });
  it('reports a job that was running and has finished, once', () => {
    expect(newlyFinished(seen(job('a', 'running')), [job('a', 'done')]).map((j) => j.id)).toEqual(['a']);
    expect(newlyFinished(seen(job('a', 'queued')), [job('a', 'error')]).map((j) => j.id)).toEqual(['a']);
    expect(newlyFinished(seen(job('a', 'done')), [job('a', 'done')])).toEqual([]);
    expect(newlyFinished(seen(job('a', 'running')), [job('a', 'running')])).toEqual([]);
  });
  it('reports a job that started and finished between two looks', () => {
    expect(newlyFinished(seen(), [job('b', 'done')]).map((j) => j.id)).toEqual(['b']);
    expect(newlyFinished(seen(), [job('b', 'running')])).toEqual([]);
  });
  it('lists what is still going on for one model', () => {
    const jobs = [job('a', 'running'), job('b', 'queued'), job('c', 'done'), job('d', 'running', 'p2')];
    expect(activeFor(jobs, 'p1').map((j) => j.id)).toEqual(['a', 'b']);
    expect(activeFor(jobs, undefined)).toEqual([]);
  });
});

import { DEFAULT_FILTER, UNCATEGORIZED, applyFilter, categoryCounts, filterFromParams, filterToParams, groupByCategory, isFiltered } from './projectFilter';

describe('finding ships and crew', () => {
  const p = (id: string, name: string, extra: Record<string, unknown> = {}) => ({
    id, kind: 'ship' as const, name, base: 'container', version: 1, updated: '2026-10-29T10:00:00Z', category: 'Commercial vessels', ...extra,
  });
  const list = [
    p('a', 'PC 303', { category: 'Law enforcement and coast guard', base: 'sampan', updated: '2026-10-29T12:00:00Z', version: 4 }),
    p('b', 'Container ship', { version: 2 }),
    p('c', 'Container ship with lesser cargo', { derived_from: { project: 'b', version: 1, name: 'Container ship' }, updated: '2026-10-29T11:00:00Z' }),
    p('d', 'Tanker 2', { base: 'tanker', version: 0 }),
    p('e', 'Gunboat', { category: 'Military vessels', draft: { stage: 'ready' }, derived_from: { project: 'a', version: 4, name: 'PC 303' } }),
    p('f', 'Old one', { category: null }),
  ];
  const ctx = { working: new Set(['b']), baseLabel: (b: string) => ({ container: 'Container ship', sampan: 'Sampan', tanker: 'Tanker' })[b] ?? b };
  const ids = (f: Partial<typeof DEFAULT_FILTER>) => applyFilter(list, { ...DEFAULT_FILTER, ...f }, ctx).map((x) => x.id);

  it('searches names, categories, base types and originals, word by word, ignoring case', () => {
    expect(ids({ q: 'container' })).toEqual(['c', 'b', 'e', 'f']); // e and f are built on the container base
    expect(ids({ q: 'pc 303' })).toEqual(['a', 'e']);                 // e is a variant of PC 303
    expect(ids({ q: 'coast' })).toEqual(['a']);
    expect(ids({ q: 'tanker' })).toEqual(['d']);
    expect(ids({ q: 'lesser container' })).toEqual(['c']);
    expect(ids({ q: '  ' })).toHaveLength(6);
  });
  it('filters by category, status, origin and base', () => {
    expect(ids({ category: 'Military vessels' })).toEqual(['e']);
    expect(ids({ category: UNCATEGORIZED })).toEqual(['f']);
    expect(ids({ status: 'approval' })).toEqual(['e']);
    expect(ids({ status: 'working' })).toEqual(['b']);
    expect(ids({ status: 'unbuilt' })).toEqual(['d']);
    expect(ids({ status: 'built' })).not.toContain('d');
    expect(ids({ origin: 'variants' })).toEqual(['c', 'e']);
    expect(ids({ origin: 'originals' })).toEqual(['a', 'b', 'd', 'f']);
    expect(ids({ base: 'tanker' })).toEqual(['d']);
  });
  it('sorts by recent change, name or versions', () => {
    expect(ids({}).slice(0, 2)).toEqual(['a', 'c']);
    expect(ids({ sort: 'name' })).toEqual(['b', 'c', 'e', 'f', 'a', 'd']);
    expect(ids({ sort: 'name_desc' })[0]).toBe('d');
    expect(ids({ sort: 'versions' })[0]).toBe('a');
  });
  it('counts each category under the other filters, defaults first, empty defaults kept', () => {
    const counts = categoryCounts(list, ['Commercial vessels', 'Military vessels', 'Fishing and small craft']);
    expect(counts).toEqual([
      { category: 'Commercial vessels', count: 3 }, { category: 'Military vessels', count: 1 },
      { category: 'Fishing and small craft', count: 0 }, { category: 'Law enforcement and coast guard', count: 1 },
      { category: UNCATEGORIZED, count: 1 },
    ]);
  });
  it('groups by category in the same order', () => {
    const groups = groupByCategory(applyFilter(list, DEFAULT_FILTER, ctx), ['Military vessels', 'Commercial vessels']);
    expect(groups.map((g) => [g.category, g.items.map((x) => x.id)])).toEqual([
      ['Military vessels', ['e']], ['Commercial vessels', ['c', 'b', 'd']], ['Law enforcement and coast guard', ['a']], [UNCATEGORIZED, ['f']],
    ]);
  });
  it('keeps the filter in the address, leaving out defaults', () => {
    const f = { ...DEFAULT_FILTER, q: 'pc', category: 'Military vessels', status: 'working' as const, group: true };
    const params = filterToParams(f);
    expect(params).toEqual({ q: 'pc', cat: 'Military vessels', status: 'working', group: '1' });
    expect(filterFromParams(new URLSearchParams(params))).toEqual(f);
    expect(filterFromParams(new URLSearchParams('status=nonsense&sort=zzz'))).toEqual(DEFAULT_FILTER);
    expect(isFiltered(DEFAULT_FILTER)).toBe(false);
    expect(isFiltered({ ...DEFAULT_FILTER, sort: 'name', group: true })).toBe(false); // sorting and grouping hide nothing
    expect(isFiltered(f)).toBe(true);
  });
});

import { TRASH_NOTE, deleteFacts, draftBadge, draftKind, isDraft } from './draft';

describe('draft or final, and the delete dialog (Lester, 29 Oct)', () => {
  const ship = (extra: Record<string, unknown> = {}) => ({ id: 'x', kind: 'ship' as const, name: 'Yacht', base: 'container', version: 1, ...extra });
  const unapproved = ship({ base: 'custom', plan_built: false });

  it('a described ship whose plan was never built is a draft; once built it is final', () => {
    expect(draftKind(unapproved)).toBe('plan');
    expect(draftKind(ship({ base: 'custom' }))).toBe('plan');                      // plan_built missing = not built
    expect(draftKind(ship({ base: 'custom', plan_built: true }))).toBeNull();
    expect(draftBadge(unapproved)).toBe('Draft · plan not approved');
    expect(draftBadge(ship({ base: 'custom', plan_built: true }))).toBeNull();
  });
  it('a variant awaiting review is a draft and keeps its own badge, even of a described ship', () => {
    const v = ship({ base: 'custom', plan_built: true, draft: { stage: 'ready' } });
    expect(draftKind(v)).toBe('variant');
    expect(draftBadge(v)).toBe('Awaiting your approval');
  });
  it('catalog ships and crew are final, with no badge', () => {
    expect(isDraft(ship())).toBe(false);
    expect(draftBadge(ship({ version: 0 }))).toBeNull();                           // not built yet is not a draft
    expect(isDraft({ kind: 'crew', base: 'custom', plan_built: false })).toBe(false); // only ships have plans
    expect(isDraft(null)).toBe(false);
  });
  it('the Draft and Final filters split the list, and combine with the other filters', () => {
    const list = [
      ship({ id: 'cat' }),
      ship({ id: 'plan', base: 'custom' }),
      ship({ id: 'built', base: 'custom', plan_built: true, updated: '2026-10-29T09:00:00Z' }),
      ship({ id: 'var', draft: { stage: 'ready' }, derived_from: { project: 'cat', version: 1, name: 'Yacht' } }),
    ];
    const ctx = { working: new Set<string>(), baseLabel: (b: string) => b };
    const ids = (f: Partial<typeof DEFAULT_FILTER>) => applyFilter(list, { ...DEFAULT_FILTER, sort: 'name', ...f }, ctx).map((x) => x.id).sort();
    expect(ids({ status: 'draft' })).toEqual(['plan', 'var']);
    expect(ids({ status: 'final' })).toEqual(['built', 'cat']);
    expect(ids({ status: 'approval' })).toEqual(['var']);                          // Bryan's filter is unchanged
    expect(ids({ status: 'draft', origin: 'originals' })).toEqual(['plan']);
    expect(filterFromParams(new URLSearchParams('status=draft')).status).toBe('draft');
    expect(filterToParams({ ...DEFAULT_FILTER, status: 'final' })).toEqual({ status: 'final' });
    expect(isFiltered({ ...DEFAULT_FILTER, status: 'draft' })).toBe(true);
  });
  it('the dialog names the kind, versions, category and draft state', () => {
    expect(deleteFacts(unapproved, 1, 'Other')).toEqual(['Ship', '1 version', 'Category: Other', 'Draft: its design plan was never approved']);
    expect(deleteFacts(ship(), 3, 'Commercial vessels')).toEqual(['Ship', '3 versions', 'Category: Commercial vessels']);
    expect(deleteFacts({ kind: 'crew', draft: { stage: 'ready' } as never }, 0, 'Navy'))
      .toEqual(['Crew member', 'No versions built yet', 'Category: Navy', 'Draft: a variant awaiting your approval']);
    expect(deleteFacts(ship(), null, 'Other')[1]).toBe('Counting versions…');
    expect(TRASH_NOTE).toBe('It is removed from the app. Its files are kept in the data/trash folder, so it can be recovered by hand.');
  });
});

describe('research photographs (CONTRACT §12)', () => {
  const ph = (id: string, views: string[], status?: string, extra: Partial<ResearchPhoto> = {}): ResearchPhoto => ({ id, title: id, views, status, ...extra });
  const photos = [
    ph('P01', ['port', 'bow', 'details'], 'baseline'),
    ph('P02', ['Starboard'], 'comparison'),
    ph('P03', ['top'], 'excluded'),
    ph('P04', ['interior', 'helm'], undefined),
  ];

  it('reads photo ids out of free coverage text, once each, in order', () => {
    expect(photoIdsIn('P04 / P05 elevated; P04 again, not P1x or XP06')).toEqual(['P04', 'P05']);
    expect(photoIdsIn(null)).toEqual([]);
  });
  it('uses the stated coverage: null, empty and "none" are missing; ids link only to photos that exist', () => {
    const { cells, note, stated } = coverageSummary({ port: 'P01, P09', starboard: null, bow: 'None found', stern: '  ', top: 'P03 elevated only', note: ' No true overhead. ' }, photos);
    const by = Object.fromEntries(cells.map((c) => [c.view, c]));
    expect(stated).toBe(true);
    expect(cells.map((c) => c.view)).toEqual(['port', 'starboard', 'bow', 'stern', 'top', 'details']);
    expect(by.port).toMatchObject({ ids: ['P01'], text: 'P01, P09', missing: false });
    expect(by.starboard).toMatchObject({ missing: true, text: 'missing' });
    expect(by.bow.missing).toBe(true);
    expect(by.stern.missing).toBe(true);
    expect(by.details.missing).toBe(true); // absent key
    expect(by.top).toMatchObject({ ids: ['P03'], missing: false });
    expect(note).toBe('No true overhead.');
  });
  it('works coverage out from the photos when the research states none, ignoring excluded photos', () => {
    const { cells, stated, note } = coverageSummary(undefined, photos);
    const by = Object.fromEntries(cells.map((c) => [c.view, c]));
    expect(stated).toBe(false);
    expect(note).toBeNull();
    expect(by.port.ids).toEqual(['P01']);
    expect(by.starboard.ids).toEqual(['P02']); // case-insensitive
    expect(by.top).toMatchObject({ ids: [], missing: true }); // P03 is excluded
    expect(by.stern.missing).toBe(true);
  });
  it('filters by view and by status, keeping each photo\'s index in the research', () => {
    expect(filterPhotos(photos, 'all', 'all').map((x) => x.index)).toEqual([0, 1, 2, 3]);
    expect(filterPhotos(photos, 'starboard', 'all').map((x) => x.photo.id)).toEqual(['P02']);
    expect(filterPhotos(photos, 'all', 'excluded').map((x) => x.index)).toEqual([2]);
    expect(filterPhotos(photos, 'port', 'comparison')).toEqual([]);
    expect(photoStatus({ status: ' Baseline ' })).toBe('baseline');
    expect(photoStatus({ status: 'maybe' })).toBe('unstated');
  });
  it('offers every coverage view and interior, then extra views the photos use, with counts', () => {
    const v = viewOptions(photos);
    expect(v.map((o) => o.key)).toEqual(['all', 'port', 'starboard', 'bow', 'stern', 'top', 'details', 'interior', 'helm']);
    expect(v.find((o) => o.key === 'all')!.count).toBe(4);
    expect(v.find((o) => o.key === 'stern')!.count).toBe(0);
    expect(v.find((o) => o.key === 'helm')!.label).toBe('Helm');
    expect(statusOptions(photos).map((o) => o.count)).toEqual([4, 1, 1, 1]);
  });
  it('states the distinct-exposure count, and says so when the research did not count', () => {
    expect(countLine(21, 'SHA-256 plus visual comparison.', 21).count).toBe('21 distinct photographs');
    expect(countLine(2, '', 3)).toEqual({ count: '2 distinct photographs · 3 records listed', method: 'The research does not say how the photographs were counted.' });
    expect(countLine(1, 'x', 1).count).toBe('1 distinct photograph');
    expect(countLine(undefined, undefined, 3).count).toBe('3 photographs listed · not counted for distinct exposures');
  });
});

describe('photo provenance wording', () => {
  it('links only http(s) URLs, because an AI wrote them', () => {
    expect(safeHref('https://example.org/a')).toBe('https://example.org/a');
    expect(safeHref('javascript:alert(1)')).toBeNull();
    expect(safeHref('data:image/png;base64,xx')).toBeNull();
    expect(safeHref('//evil.example/x')).toBeNull();
    expect(hostOf('https://www.police.gov.sg/x?y')).toBe('police.gov.sg');
    expect(hostOf('ftp://x')).toBe('');
  });
  it('shortens a SHA-256 and sizes, or says not recorded', () => {
    expect(shortHash('4AA712403560CF7C70F4757F88337B48')).toBe('4aa712403560…');
    expect(shortHash('')).toBe('not recorded');
    expect(shortHash('not-a-hash')).toBe('not recorded');
    expect(photoSize(1920, 1280)).toBe('1920 × 1280 px');
    expect(photoSize(0, 1280)).toBe('not recorded');
  });
  it('lists the provenance in order, flags what is missing, and never links a non-web source', () => {
    const rows = provenanceRows({ id: 'P01', title: 't', vessel: 'PC 303', views: ['port', 'bow'], status: 'comparison', source: 'https://www.defencepioneer.sg/a', image_url: 'javascript:x', sha256: 'ab'.repeat(32), width: 10, height: 20 });
    expect(rows.map((r) => r.label)).toEqual(['Status', 'Vessel', 'Views', 'Credit', 'Date', 'Source', 'Image link', 'Size', 'SHA-256', 'Accessed', 'Rights', 'Modelling use', 'Observation']);
    const by = Object.fromEntries(rows.map((r) => [r.label, r]));
    expect(by.Status.value).toBe('Comparison');
    expect(by.Views.value).toBe('Port, Bow');
    expect(by.Credit).toMatchObject({ value: 'not recorded', missing: true });
    expect(by.Source).toMatchObject({ value: 'defencepioneer.sg', href: 'https://www.defencepioneer.sg/a', missing: false });
    expect(by['Image link'].href).toBeUndefined();
    expect(by['SHA-256'].value).toBe('abababababab…');
    expect(by.Size.value).toBe('10 × 20 px');
    expect(by.Accessed.missing).toBe(true);
  });
});

describe('close-up outline and inspector zoom', () => {
  it('turns a pixel box into percentages of the photo', () => {
    expect(boxToPercent([480, 320, 960, 640], 1920, 1280)).toEqual({ left: 25, top: 25, width: 50, height: 50 });
    expect(boxToPercent([0, 0, 1, 3], 3, 9)).toEqual({ left: 0, top: 0, width: 33.33, height: 33.33 });
  });
  it('clips a box that runs off the photo, and refuses one that is unusable', () => {
    expect(boxToPercent([1800, -100, 400, 300], 2000, 1000)).toEqual({ left: 90, top: 0, width: 10, height: 20 });
    expect(boxToPercent([-200, 500, 400, 600], 2000, 1000)).toEqual({ left: 0, top: 50, width: 10, height: 50 });
    expect(boxToPercent([3000, 0, 10, 10], 2000, 1000)).toBeNull();
    expect(boxToPercent([0, 0, 0, 10], 2000, 1000)).toBeNull();
    expect(boxToPercent([0, 0, 10], 2000, 1000)).toBeNull();
    expect(boxToPercent([0, 0, 10, 10], undefined, 1000)).toBeNull();
    expect(boxText([10.4, 20, 300, 200])).toBe('300 × 200 px at (10, 20)');
  });
  it('fits an image to the viewport keeping its proportions', () => {
    expect(fitSize({ w: 2000, h: 1000 }, { w: 1000, h: 1000 })).toEqual({ w: 1000, h: 500 });
    expect(fitSize({ w: 0, h: 1 }, { w: 10, h: 10 })).toEqual({ w: 0, h: 0 });
  });
  it('zooms about the pointer: the image point under it stays put', () => {
    const base = { w: 1000, h: 500 }, view = { w: 1000, h: 500 };
    const r = zoomAt(1, { x: 0, y: 0 }, 2, { x: 250, y: 0 }, base, view);
    expect(r.zoom).toBe(2);
    // the point 250 px right of centre was image x = 250; at 2x it must still be under the pointer
    expect((250 - r.pan.x) / r.zoom).toBe(250 / 1);
    // from an already-zoomed state too: at 2x, pan 100, zoom 2x again about a point 300 px right
    const r2 = zoomAt(2, { x: 100, y: 0 }, 2, { x: 300, y: 0 }, { w: 4000, h: 500 }, view);
    expect(r2.zoom).toBe(4);
    expect((300 - r2.pan.x) / 4).toBe((300 - 100) / 2);
    expect(zoomAt(8, { x: 0, y: 0 }, 2, { x: 0, y: 0 }, base, view).zoom).toBe(8);
    expect(zoomAt(1, { x: 0, y: 0 }, 0.5, { x: 0, y: 0 }, base, view)).toEqual({ zoom: 1, pan: { x: 0, y: 0 } });
  });
  it('never drags the image off the viewport, and keeps a fitted image centred', () => {
    const base = { w: 1000, h: 500 }, view = { w: 1000, h: 600 };
    expect(clampPan({ x: 900, y: -900 }, 2, base, view)).toEqual({ x: 500, y: -200 });
    expect(clampPan({ x: 40, y: 40 }, 1, base, view)).toEqual({ x: 0, y: 0 });
  });
});

describe('always clothed: untagged leg garments are trousers', () => {
  it('reads an old described garment by the body regions it covers', () => {
    expect(moduleOfItem({ code: "crew.garment('Clothes__Shorts', ['pelvis', 'thighs'], (0, 0, 1))" })).toBe('pants');
    expect(moduleOfItem({ code: "crew.garment('Clothes__Overall', ['torso', 'thighs'], (0, 0, 1))" })).toBe('shirt');
    expect(moduleOfItem({ code: "crew.garment('Clothes__Vest', ['torso'], (1, 1, 1))" })).toBe('shirt');
  });
});

import { claimUsed, linearToHex, planRows } from './planValues';
import { hexToLinear as toLinearColour } from './describe';

describe('reviewing research before a ship is built', () => {
  it('turns Blender colours into screen colours and back', () => {
    expect(linearToHex([1, 1, 1])).toBe('#ffffff');
    expect(linearToHex([0, 0, 0])).toBe('#000000');
    expect(linearToHex(toLinearColour('#d8b878'))).toBe('#d8b878');
  });
  it('offers the dimensions and colours first, other numbers after, and never text or lists', () => {
    const planned = { loa_m: 24, beam_m: 6, depth_m: 3, draught_m: 1.6, bow_form: 'raked', hull_color: [1, 1, 1], sheer_m: 0.4, tiers: [{ x_aft_m: -6 }] };
    const { key, more } = planRows(planned, { beam_m: 6.5 }, { loa_m: { doc: 'Length overall' } });
    expect(key.map((r) => r.name)).toEqual(['loa_m', 'beam_m', 'draught_m', 'depth_m', 'hull_color']);
    expect(more.map((r) => r.name)).toEqual(['sheer_m']);
    expect([key[0].label, key[0].value, key[0].from, key[0].doc]).toEqual(['Length overall', 24, 'plan', 'Length overall']);
    expect([key[1].value, key[1].from]).toEqual([6.5, 'yours']);
    expect(key[4].kind).toBe('colour');
  });
  it('drops a claim once every source it cites is crossed out', () => {
    expect(claimUsed({ sources: ['S1', 'S2'] }, ['S2'])).toBe(true);
    expect(claimUsed({ sources: ['S2'] }, ['S2'])).toBe(false);
    expect(claimUsed({ sources: 'S2' }, ['S2'])).toBe(true);
    expect(claimUsed({ sources: [] }, ['S2'])).toBe(true);
  });
});
