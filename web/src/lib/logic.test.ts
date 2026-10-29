import { describe, expect, it } from 'vitest';
import type { Job, Part } from '../types';
import { hiddenParts, isPartName, jointsOf, levelsFor, partLabel, resolvePart } from './parts';
import { MAX_POLL_FAILURES, describe as describePoll, nextDelayMs, pollReducer, type PollState } from './jobs';
import { blenderToGltf, cameraFor, gltfToBlender, jointAxisGltf } from './frames';
import { assetLabel, clothesSlot, mergePatch, toggleIn } from './spec';
import { targetFor } from './routing';
import { fileType, refuse, sizeText } from './attachments';

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

import { HAIR_SWATCHES, describeMessage, hexToLinear, itemKind, itemsFor, madeSomethingNew, removeItemPatch, selectedSwatch } from './describe';

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

  it('prefixes the slot and refuses a blank description', () => {
    expect(describeMessage('Clothes', '  a white   singlet ')).toBe('Clothes: a white singlet');
    expect(describeMessage('Equipment', '   ')).toBeNull();
  });

  it('sorts items into tabs by the object names their code makes', () => {
    expect(itemKind(item('a', 'crew.garment("Clothes__Singlet", ...)', 'white singlet'))).toBe('clothes');
    expect(itemKind(item('b', 'crew.garment("Clothes__Cap", ...)', 'navy cap with badge'))).toBe('head');
    expect(itemKind(item('c', 'o.name = "Equipment__Boat_hook"', 'boat hook'))).toBe('equipment');
    expect(itemKind(item('d', 'pass', 'mystery'))).toBe('other');
    const items = [item('a', 'Clothes__X'), item('c', 'Equipment__Y'), item('d', 'pass'), item('b', 'Clothes__Z', 'a beard')];
    expect(itemsFor(items, 'Clothes').map((i) => i.id)).toEqual(['a', 'd']);
    expect(itemsFor(items, 'Equipment').map((i) => i.id)).toEqual(['c']);
    expect(itemsFor(items, 'Head').map((i) => i.id)).toEqual(['b']);
    expect(itemsFor(undefined, 'Clothes')).toEqual([]);
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
