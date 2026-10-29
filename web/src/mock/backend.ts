// In-browser mock of the server API (CONTRACT §10), for VITE_MOCK=1 development only.
// It returns contract-shaped data and fakes jobs that take a few seconds, so progress UI is
// exercised. Nothing here is AI: replies are canned and say "(mock)".
import { ApiError } from '../api';
import { httpErrorText } from '../lib/designPlan';
import { mergePatch } from '../lib/spec';
import { MODULES, isModuleId, moduleOfItem } from '../lib/modules';
import type {
  Attachment, Catalog, CustomItem, Job, Message, ModuleId, Part, Pin, Project, ProjectSummary, RouteResult, Scene, SceneSpec, UnrealReport, VersionInfo,
} from '../types';
import shipPartsJson from '../../public/mock/ship_parts.json';
import { mockResearch } from './research';
import { planState } from '../lib/designPlan';

const shipParts = shipPartsJson as unknown as Part[];
const now = () => new Date().toISOString();
const JOB_MS = 3500;

interface MockJob { job: Job; created: number; steps: string[]; finish: () => unknown; finished: boolean; fail?: string }
interface Store {
  projects: Record<string, Project & { glbs: Record<number, string> }>;
  scenes: Record<string, Scene>;
  seq: number;
}

const KEY = 'me3d-mock-store-v1';
const jobs: Record<string, MockJob> = {};

function seed(): Store {
  const ship: Store['projects'][string] = {
    id: 'p_pc303', kind: 'ship', name: 'PCG 3-Series · PC 303', base: 'sampan', version: 2,
    spec: {
      schema: 'me3d.ship_spec/1', type: 'sampan', id: 'pcg_pc303', name: 'PCG 3-Series · PC 303',
      params: { loa_m: 16, beam_m: 4.2, draught_m: 0.8, depth_m: 1.6, hull_color: [0.6, 0.64, 0.68] },
      overrides: {}, joint_limits: {}, custom_ops: [],
    },
    versions: [
      { n: 1, at: '2026-10-28T09:00:00Z', summary: 'Blockout matched to P01–P03, four outboards', ok: true },
      { n: 2, at: '2026-10-28T10:00:00Z', summary: 'Searchlight moved 0.4 m aft (pin 2)', ok: true },
    ],
    model_url: '/mock/ship_v2.glb',
    parts: shipParts,
    build_report: { ok: true, errors: [], warnings: ['Scale is a photo estimate: no verified length or beam'], triangles: 4120, seconds: 12.4 },
    pins: [
      { id: 'pin_1', part: 'Cabin__Door', point: [-0.3, 2.2, 1.65], status: 'resolved', author: 'You', before_version: 1, after_version: 2,
        messages: [{ who: 'You', text: 'This door isn’t where it is on 303', at: now() }, { who: 'AI', text: '(mock) It came from P04, which is PC 310. Matched to P21 (PC 303).', at: now() }] },
      { id: 'pin_2', part: 'Roof__Searchlight', point: [1.7, 2.45, 0.6], status: 'waiting', author: 'You', before_version: 1, after_version: 2,
        messages: [{ who: 'You', text: 'Too far forward compared with P01', at: now() }, { who: 'AI', text: '(mock) Moved it 0.4 m aft to match P01. The position is a photo estimate. Keep it?', at: now() }] },
      { id: 'pin_3', part: 'Transom__Outboard_2', point: [-8.4, 0.6, -0.5], status: 'open', author: 'Customer',
        messages: [{ who: 'Customer', text: 'The engines look too close together', at: now() }] },
    ],
    messages: [
      { who: 'AI', text: '(mock) Built v1 from the research: hull and cabin matched to P01, P02 and P03, four outboards, roof equipment. Scale is a photo estimate.', at: now() },
    ],
    research: mockResearch,
    glbs: { 1: '/mock/ship_v1.glb', 2: '/mock/ship_v2.glb' },
  };
  const crew = (id: string, name: string, glb: string, gender: string, skin: string): Store['projects'][string] => ({
    id, kind: 'crew', name, base: 'male_default', version: 1,
    spec: {
      schema: 'me3d.crew_spec/1', id, name,
      body: { gender, height_cm: 176, weight: 0.5, muscle: 0.5, age_years: 30, skin },
      clothes: { suit: 'male_worksuit01', shoes: 'shoes01', hat: null, suit_color: null },
      equipment: ['life_vest'], animations: ['idle', 'walk', 'brace', 'hands_up', 'squat'],
    },
    versions: [{ n: 1, at: now(), summary: 'Default crew member', ok: true }],
    model_url: glb, parts: [], build_report: { ok: true, errors: [], warnings: [], triangles: 18000 },
    pins: [], messages: [], research: null, glbs: { 1: glb },
  });
  const scene: Scene = {
    schema: 'me3d.scene_spec/1', id: 'scene_1', name: 'PC 303 with crew',
    ship: { project: 'p_pc303', version: 2 },
    crew: [
      { project: 'c_cox', version: 1, station: 'station__helm', animation: 'idle' },
      { project: 'c_board1', version: 1, station: 'station__aft_port', animation: 'brace' },
    ],
  };
  return {
    projects: {
      p_pc303: ship,
      c_cox: crew('c_cox', 'Coxswain', '/mock/crew_a.glb', 'male', 'medium'),
      c_board1: crew('c_board1', 'Boarding 1', '/mock/crew_b.glb', 'male', 'dark'),
    },
    scenes: { scene_1: scene },
    seq: 10,
  };
}

let store: Store = load();
function load(): Store {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return JSON.parse(raw) as Store;
  } catch { /* ignore */ }
  return seed();
}
function save() {
  try { localStorage.setItem(KEY, JSON.stringify(store)); } catch { /* ignore */ }
}
export function resetMock() {
  store = seed();
  save();
}

// Uploads live for this page load only (object URLs); mock mode never sends them anywhere.
const mockUploads: Record<string, Attachment> = {};

function startJob(steps: string[], finish: () => unknown, fail?: string): { job_id: string } {
  const id = `job_${++store.seq}`;
  jobs[id] = { job: { id, status: 'queued', log: [], result: null, error: null }, created: Date.now(), steps, finish, finished: false, fail };
  save();
  return { job_id: id };
}

function tickJob(id: string): Job {
  const j = jobs[id];
  if (!j) throw new HttpErr(404, `No job ${id}`);
  if (j.finished) return j.job;
  const t = Date.now() - j.created;
  if (t < 400) return j.job;
  const k = Math.min(j.steps.length, Math.floor(((t - 400) / (JOB_MS - 400)) * j.steps.length) + 1);
  j.job = { ...j.job, status: 'running', log: j.steps.slice(0, k) };
  if (t >= JOB_MS) {
    j.finished = true;
    if (j.fail) j.job = { ...j.job, status: 'error', error: j.fail };
    else j.job = { ...j.job, status: 'done', result: j.finish() ?? null };
    save();
  }
  return j.job;
}

class HttpErr extends Error {
  constructor(public status: number, msg: string) { super(msg); }
}

function proj(id: string) {
  const p = store.projects[id];
  if (!p) throw new HttpErr(404, `No project ${id}`);
  return p;
}
const strip = (p: Store['projects'][string]): Project => {
  const { glbs: _g, ...rest } = p;
  void _g;
  return JSON.parse(JSON.stringify({ ...rest, can_undo: rest.versions.length > 1 }));
};

function newVersion(p: Store['projects'][string], summary: string) {
  const n = p.version + 1;
  p.version = n;
  p.glbs[n] = p.kind === 'ship' ? (n % 2 ? '/mock/ship_v1.glb' : '/mock/ship_v2.glb') : p.glbs[1];
  p.model_url = p.glbs[n];
  p.versions = [...p.versions, { n, at: now(), summary, ok: true } as VersionInfo];
  return n;
}

/**
 * A crew chat scoped to a module ("Make it" under Shirt sends module "shirt") makes a fake custom
 * item tagged with that module, replacing that module's earlier items and nothing else, like the
 * real AI must (CONTRACT §6 "Modules"). Returns the modules it changed, for `changed_modules`.
 * Unscoped messages starting "Shirt: …" are treated the same, so the main chat can be tried too.
 */
function mockDescribed(p: Store['projects'][string], message: string, module: unknown): ModuleId[] {
  if (p.kind !== 'crew') return [];
  let m: ModuleId | null = isModuleId(module) ? module : null;
  let text = message;
  const pre = message.match(/^([A-Za-z ]+):\s*(.+)$/s);
  if (pre) {
    const named = MODULES.find((d) => d.name.toLowerCase() === pre[1].trim().toLowerCase());
    if (named && (!m || m === named.id)) { m = named.id; text = pre[2]; }
  }
  if (!m) return [];
  const spec = p.spec as Record<string, unknown>;
  const patch = (x: Record<string, unknown>) => { p.spec = mergePatch(p.spec as Record<string, unknown>, x) as Project['spec']; };
  if (m === 'hair') { if (/blond/i.test(text)) patch({ body: { hair_color: [0.687, 0.479, 0.188] } }); return ['hair']; }
  if (m === 'gender') { if (/\b(woman|female)\b/i.test(text)) patch({ body: { gender: 'female' } }); return ['gender']; }
  if (m === 'skin' || m === 'outfit' || m === 'placement') return [m];
  const items = ((spec.custom_items as CustomItem[] | undefined) ?? []).filter((i) => moduleOfItem(i) !== m);
  const n = ++store.seq;
  const held = m === 'equipment';
  const name = `${held ? 'Equipment' : 'Clothes'}__Mock_${n}`;
  const code = `# (mock) nothing is built in mock mode\nobj = ${held ? `crew.object_from_bmesh("${name}", crew.new_bmesh(), (0.4, 0.3, 0.2))` : `crew.garment("${name}", ["torso"], (1, 1, 1))`}`;
  const item: CustomItem = { id: `item_${n}`, description: text.trim(), code, module: m, ...(held ? { place: 'right_hand' as const } : {}) };
  p.spec = { ...spec, custom_items: [...items, item] } as Project['spec'];
  const hadSuit = Boolean((spec.clothes as { suit?: string | null } | undefined)?.suit);
  if (m === 'shirt' || m === 'pants') patch({ clothes: { [m]: null, suit: null } });
  if (m === 'shoes') patch({ clothes: { shoes: null } });
  // Like the server: a shirt or pants change that clears the outfit reports the outfit as changed too.
  return (m === 'shirt' || m === 'pants') && hadSuit ? [m, 'outfit'] : [m];
}

// ---- Ships of any type (CONTRACT §5 `type: "custom"`) ---------------------------------------
// Research first; every research turn amends the research (research_rev + 1) and the mock AI
// redrafts the plan, except that an approved plan goes stale and waits to be asked for a redraft,
// so every panel state can be seen. Approve builds; nothing else builds a custom ship.

const VESSEL_WORDS = ['yacht', 'tug', 'tugboat', 'ferry', 'trawler', 'barge', 'frigate', 'corvette', 'catamaran', 'dhow', 'junk', 'tanker', 'dredger', 'lifeboat', 'pilot boat', 'patrol boat', 'speedboat'];
function nameFromBrief(brief: string): string {
  const t = brief.toLowerCase();
  const hit = VESSEL_WORDS.map((w) => [t.indexOf(w), w] as const).filter(([i]) => i >= 0).sort((a, b) => a[0] - b[0])[0];
  return hit ? hit[1][0].toUpperCase() + hit[1].slice(1) : 'Custom ship';
}

function createCustom(body: { name?: string; brief?: string }) {
  const brief = String(body.brief ?? '').trim();
  const id = `p_${++store.seq}`;
  const name = (body.name || '').trim() || nameFromBrief(brief);
  const p: Store['projects'][string] = {
    id, kind: 'ship', name, base: 'custom', version: 0,
    spec: { schema: 'me3d.ship_spec/1', type: 'custom', id, name, params: {}, overrides: {}, joint_limits: {}, custom_ops: [] },
    versions: [], model_url: null, parts: shipParts, build_report: {}, pins: [], messages: [], research: null,
    research_rev: 0, design_plan: null, brief: brief || null, plan_built: false, glbs: {},
  };
  store.projects[id] = p;
  // The placeholder hull "builds" at once here: nobody polls this job, the Research screen opens first.
  newVersion(p, 'Created as a plain placeholder hull; research and an approved design plan shape it');
  p.build_report = { ok: true, errors: [], warnings: ['Placeholder hull: approve the design plan to build the real shape'], triangles: 900 };
  const job = startJob(['Creating the project'], () => ({ version: p.version }));
  save();
  return { project: strip(p), ...job };
}

function customResearchTurn(p: Store['projects'][string], message: string) {
  const big = message.match(/(\d{2,3})\s*m\b/);
  const prev = p.research as { _loa?: number } | null;
  const loa = big ? Number(big[1]) : prev?._loa ?? 36;
  const beam = Math.round(loa * 0.2 * 10) / 10;
  p.research = {
    schema: 'me3d.research/1', _loa: loa,
    subject: `${p.name}: from your description${p.brief ? ` (“${p.brief}”)` : ''} and photos (mock)`,
    summary: { verdict: `(mock) Buildable at simulator distance: a ${loa} m tri-deck motor yacht.`, existing_work: 'No existing model of this vessel in the project (mock).' },
    sources: [{ id: 'U1', title: 'Your photo', status: 'Photographed', what_it_proves: 'Overall form, deck count, colours' }],
    claims: [
      { claim: 'Length overall', sources: ['U1'], status: big ? 'Official' : 'Photo reading', consequence_for_model: `${loa} m${big ? ' (your correction)' : ''}` },
      { claim: 'Superstructure tiers', sources: ['U1'], status: 'Photo reading', consequence_for_model: 'Three tiers' },
      { claim: 'Beam', sources: [], status: 'Inference', consequence_for_model: `${beam} m, typical for ${loa} m` },
    ],
    open_questions: [
      { question: 'Is the fly-bridge open or covered with a hard top?', meanwhile: 'Modelled open' },
      { question: 'Should the tender garage in the stern open?', meanwhile: 'Closed, not articulated' },
    ],
    articulation: [],
  };
  p.research_rev = (p.research_rev ?? 0) + 1;
  const plan = p.design_plan;
  const asked = /plan/i.test(message);
  let note = 'Updated the research and redrafted the design plan. Nothing is built until you approve it.';
  if (plan?.status === 'approved' || (plan?.status === 'stale' && !asked)) {
    p.design_plan = { ...plan, status: 'stale' };
    note = 'Updated the research. The approved plan no longer matches it: ask me to redraft the plan, then approve it again.';
  } else {
    p.design_plan = {
      status: 'draft', research_rev: p.research_rev, at: now(),
      summary: [
        `Size · ${loa} m long, ${beam} m beam, 2.1 m draught`,
        `Hull · white, raked sharp bow, swim-platform stern${/stripe/i.test(message) ? ', dark grey sheer stripe' : ''}`,
        `Main deck · ${Math.round(loa * 0.63)} m long, full width, black window band`,
        `Upper deck · ${Math.round(loa * 0.45)} m, set back 2 m, raked front, window band`,
        'Fly-bridge · 10 m, open, radar arch',
        'Details · railings, tender garage door, sun loungers on the foredeck',
        `Assumed · beam ${beam} m and draught 2.1 m (typical for ${loa} m); fly-bridge open`,
      ],
      spec: { schema: 'me3d.ship_spec/1', type: 'custom', params: { loa_m: loa, beam_m: beam, draught_m: 2.1 } },
    };
  }
  const reply: Message = { who: 'AI', text: `(mock) ${note}`, at: now() };
  p.messages = [...p.messages, reply];
  return { message: reply, research_saved: true, plan_saved: p.design_plan?.status === 'draft' };
}

function approvePlan(p: Store['projects'][string]) {
  if (p.base !== 'custom') throw new HttpErr(400, 'Only custom ships have a design plan.');
  const plan = p.design_plan;
  if (!plan) throw new HttpErr(409, 'There is no design plan yet. Ask the AI in Research to draft one.');
  const st = planState(p);
  if (st === 'approved') throw new HttpErr(409, 'This design plan is already approved.');
  if (plan.status === 'stale') throw new HttpErr(409, 'The research changed after this plan was approved. Ask the AI in Research to redraft the plan.');
  if (st === 'redraft') throw new HttpErr(409, 'The research changed after this plan was drafted. Ask the AI in Research to redraft the plan, then approve it.');
  p.design_plan = { ...plan, status: 'approved', approved_at: now() };
  save();
  return startJob(['Building the approved design plan.', ...buildSteps, 'Adding the plan’s details.'], () => {
    const n = newVersion(p, 'Built from the approved design plan');
    p.build_report = { ok: true, errors: [], warnings: [], triangles: 5200 };
    p.plan_built = true;
    const reply: Message = { who: 'AI', text: '(mock) Built the approved design plan. Comment on any part to change it.', at: now() };
    p.messages = [...p.messages, reply];
    return { version: n, build_report: p.build_report, reply: reply.text, built_versions: [n] };
  });
}

/** The server's refusal (409) of any build on a custom ship before its first approved plan is built. */
function refuseUnplanned(p: Store['projects'][string]) {
  if (p.kind !== 'ship' || p.base !== 'custom') return;
  const plan = p.design_plan;
  if (p.plan_built || plan?.status === 'approved') return; // as the server's needs_plan
  if (!plan) throw new HttpErr(409, 'This ship is built from a design plan, and there is none yet. Go to Research and ask the AI to draft one; approving it builds the ship.');
  throw new HttpErr(409, 'This ship is built from its design plan. Go to Research and approve the plan first; that builds the ship.');
}

const buildSteps = ['Merging the spec', 'Running the builder in Blender', 'Applying overrides', 'Exporting glTF', 'Checking the build report'];

function route(message: string): RouteResult {
  const t = message.toLowerCase();
  const R: [RegExp, RouteResult['destination'], string, string | null][] = [
    [/customer|meeting|present|review|export|unreal|\bmak\b|compare|version/, 'review', 'That’s about showing or shipping a version, so Review and export is the place.', null],
    [/\bput\b|place|on board|aboard|alongside|scenario|assembl|crew on/, 'assembly', 'That puts people on a ship and has them do something, so Assembly.', null],
    [/animat|\bmove\b|brace|walk|\brun\b|squat|pose|gesture|signal/, 'animation', 'That’s a movement, so the animation editor.', 'c_cox'],
    [/crew member|uniform|coxswain|female|\bmale\b|person|human|\bvest\b|helmet|clothes|outfit|\bhat\b/, 'crew', 'That’s a person or something they wear, so the Crew workshop.', 'c_cox'],
    [/\b(create|new|make)\b.*\b(ship|yacht|tug|boat|vessel|ferry|trawler|barge|frigate)\b/, 'ship_research', 'A new vessel: describe it on the Ship page, and it starts with research.', null],
    [/door|searchlight|outboard|wiper|steer|hull|cabin|looks wrong|fix the/, 'ship_build', 'That changes a part of PC 303, so the ship in Build mode.', 'p_pc303'],
    [/research|build|model|ship|vessel|boat|class|pc ?3|frigate|patrol/, 'ship_research', 'A new vessel starts with research.', 'p_pc303'],
  ];
  const hit = R.find(([re]) => re.test(t));
  if (hit) return { destination: hit[1], reason: `(mock router) ${hit[2]}`, project_id: hit[3] };
  return { destination: 'clarify', reason: '(mock router) I’m not sure which this is. What do you want to change?', project_id: null };
}

const catalog: Catalog = {
  ships: [
    { type: 'container', name: 'Container ship', description: 'Box-hulled cargo ship with cranes' },
    { type: 'sampan', name: 'Sampan', description: 'Small open boat with an outboard' },
    { type: 'tanker', name: 'Tanker', description: 'Crude oil tanker' },
    // As the server lists it; the Ship page starts these from "Describe a new ship", not as a base.
    { type: 'custom', name: 'Any ship', description: 'A general monohull shaped from research and an approved design plan' },
  ],
  crew: [{ id: 'male_default', name: 'Default male crew' }],
  // Mirrors catalog/crew/male_default.json options (suits, shoes, hats).
  clothes: ['male_worksuit01', 'male_casualsuit01', 'male_casualsuit02', 'male_elegantsuit01', 'female_casualsuit01', 'female_elegantsuit01', 'female_sportsuit01',
    'shoes01', 'shoes02', 'shoes03', 'fedora01', 'fedora_cocked'],
  equipment: ['life_vest', 'helmet', 'binoculars', 'radio'],
  animations: ['idle', 'walk', 'run', 'brace', 'hands_up', 'squat', 'binocular_look'],
};

function sceneWithStations(s: Scene): Scene {
  const ship = store.projects[s.ship.project];
  return { ...s, stations: ship ? ship.parts.filter((p) => p.path.startsWith('station__')).map((p) => p.path) : [] };
}

async function handle(method: string, path: string, body: any): Promise<unknown> {
  const m = (re: RegExp) => path.match(re);
  let r: RegExpMatchArray | null;
  if (method === 'GET' && path === '/api/health') return { blender: true, mpfb: true, ai: true, unreal: true };
  if (method === 'GET' && path === '/api/catalog') return catalog;
  if (method === 'POST' && path === '/api/route') return route(String(body?.message ?? ''));
  if (method === 'GET' && path === '/api/projects') {
    return Object.values(store.projects).map((p): ProjectSummary => ({ id: p.id, kind: p.kind, name: p.name, base: p.base, version: p.version, ok: p.build_report.ok, updated: p.versions[p.versions.length - 1]?.at, model_url: p.model_url, open_pins: p.pins.filter((x) => x.status !== 'resolved').length }));
  }
  if (method === 'POST' && path === '/api/projects' && body.kind === 'ship' && body.base === 'custom') return createCustom(body);
  if (method === 'POST' && path === '/api/projects') {
    const id = `${body.kind === 'ship' ? 'p' : 'c'}_${++store.seq}`;
    const names: Record<string, string> = { container: 'Container ship', sampan: 'Sampan', tanker: 'Tanker', male_default: 'Default male crew' };
    const seedP = body.kind === 'ship' ? store.projects.p_pc303 ?? seed().projects.p_pc303 : seed().projects.c_cox;
    const p: Store['projects'][string] = JSON.parse(JSON.stringify(seedP));
    Object.assign(p, { id, name: body.name || names[body.base] || body.base, base: body.base, version: 0, versions: [], pins: [], messages: [], research: null, glbs: {}, model_url: null });
    p.spec = { ...p.spec, id, name: p.name, ...(body.kind === 'ship' ? { type: body.base } : {}) };
    store.projects[id] = p;
    const job = startJob(['Creating the project', ...buildSteps], () => { newVersion(p, 'First build'); return { version: p.version }; });
    save();
    return { project: strip(p), ...job };
  }
  if ((r = m(/^\/api\/projects\/([^/]+)$/)) && method === 'GET') return strip(proj(r[1]));
  if ((r = m(/^\/api\/projects\/([^/]+)\/versions\/(\d+)\/parts\.json$/))) return proj(r[1]).parts;
  if ((r = m(/^\/api\/projects\/([^/]+)\/spec$/)) && method === 'POST') {
    const p = proj(r[1]);
    refuseUnplanned(p);
    return startJob(buildSteps, () => {
      p.spec = mergePatch(p.spec as Record<string, unknown>, body.patch ?? {}) as Project['spec'];
      const n = newVersion(p, `Edited ${Object.keys(body.patch ?? {}).join(', ')}`);
      return { version: n };
    });
  }
  if (((r = m(/^\/api\/projects\/([^/]+)\/uploads$/)) || (r = m(/^\/api\/uploads$/))) && method === 'POST') {
    if (r[1]) proj(r[1]);
    const raw = atob(String(body.data).replace(/^data:[^,]*,/, ''));
    const blob = new Blob([Uint8Array.from(raw, (c) => c.charCodeAt(0))], { type: body.type || 'application/octet-stream' });
    const a: Attachment = { id: `u_${(++store.seq).toString(16).padStart(8, '0')}`, name: String(body.name), type: body.type || 'text/plain', size: blob.size, url: URL.createObjectURL(blob) };
    mockUploads[a.id] = a;
    return { attachment: a };
  }
  if ((r = m(/^\/api\/projects\/([^/]+)\/chat$/)) && method === 'POST') {
    const p = proj(r[1]);
    if (body.mode !== 'research') refuseUnplanned(p);
    const attachments = ((body.attachments ?? []) as string[]).map((id) => mockUploads[id]).filter(Boolean);
    p.messages = [...p.messages, { who: 'You', text: String(body.message), at: now(), ...(attachments.length ? { attachments } : {}) }];
    save();
    const research = body.mode === 'research';
    if (research && p.base === 'custom') return startJob(['Reading your description and photos', 'Searching sources', 'Updating the research', 'Drafting the design plan'], () => customResearchTurn(p, String(body.message)));
    return startJob(research ? ['Reading the research', 'Searching sources', 'Updating the research'] : ['Reading the request', ...buildSteps], () => {
      const reply: Message = { who: 'AI', text: research ? `(mock) Noted for the research: “${body.message}”. No real search runs in mock mode.` : `(mock) Done: “${body.message}”. Rebuilt as a new version.`, at: now() };
      p.messages = [...p.messages, reply];
      const changed = research ? [] : mockDescribed(p, String(body.message), body.module);
      if (!research) newVersion(p, String(body.message).slice(0, 60));
      return { message: reply, ...(p.kind === 'crew' && !research ? { changed_modules: changed } : {}) };
    });
  }
  if ((r = m(/^\/api\/projects\/([^/]+)\/design-plan\/approve$/)) && method === 'POST') return approvePlan(proj(r[1]));
  if ((r = m(/^\/api\/projects\/([^/]+)\/pins$/)) && method === 'POST') {
    const p = proj(r[1]);
    refuseUnplanned(p);
    const pin: Pin = { id: `pin_${++store.seq}`, part: body.part, point: body.point, status: 'working', author: body.author || 'You', messages: [{ who: body.author || 'You', text: body.text, at: now() }] };
    p.pins = [...p.pins, pin];
    save();
    const job = startJob(['Reading the comment', 'Checking the research for this part', ...buildSteps], () => {
      const before = p.version;
      const after = newVersion(p, `Pin: ${body.text}`.slice(0, 60));
      p.pins = p.pins.map((x) => x.id === pin.id ? { ...x, status: 'waiting', before_version: before, after_version: after, messages: [...x.messages, { who: 'AI', text: `(mock) Changed ${body.part} as asked. Keep it?`, at: now() }] } : x);
      return { pin: pin.id, version: after };
    });
    return { pin, ...job };
  }
  if ((r = m(/^\/api\/projects\/([^/]+)\/pins\/([^/]+)\/reply$/)) && method === 'POST') {
    const p = proj(r[1]); const pid = r[2];
    refuseUnplanned(p);
    p.pins = p.pins.map((x) => x.id === pid ? { ...x, status: 'working', messages: [...x.messages, { who: 'You', text: body.text, at: now() }] } : x);
    save();
    return startJob(['Reading the reply', ...buildSteps], () => {
      const before = p.version; const after = newVersion(p, `Pin reply: ${body.text}`.slice(0, 60));
      p.pins = p.pins.map((x) => x.id === pid ? { ...x, status: 'waiting', before_version: before, after_version: after, messages: [...x.messages, { who: 'AI', text: '(mock) Made that change. Keep it?', at: now() }] } : x);
      return { version: after };
    });
  }
  if ((r = m(/^\/api\/projects\/([^/]+)\/pins\/([^/]+)$/)) && method === 'PATCH') {
    const p = proj(r[1]);
    p.pins = p.pins.map((x) => (x.id === r![2] ? { ...x, status: body.status } : x));
    save();
    return { pin: p.pins.find((x) => x.id === r![2]) };
  }
  if ((r = m(/^\/api\/projects\/([^/]+)\/undo$/)) && method === 'POST') {
    const p = proj(r[1]);
    if (p.versions.length > 1) {
      const gone = p.version;
      p.versions = p.versions.slice(0, -1);
      p.version = p.versions[p.versions.length - 1].n;
      p.model_url = p.glbs[p.version];
      p.pins = p.pins.map((x) => (x.after_version === gone ? { ...x, status: 'open', after_version: null } : x));
    }
    save();
    return { project: strip(p) };
  }
  if ((r = m(/^\/api\/jobs\/([^/]+)$/))) return tickJob(r[1]);
  if (method === 'GET' && path === '/api/scenes') return Object.values(store.scenes).map(sceneWithStations);
  if (method === 'POST' && path === '/api/scenes') {
    const id = `scene_${++store.seq}`;
    store.scenes[id] = { ...(body as SceneSpec), id };
    save();
    return { scene: sceneWithStations(store.scenes[id]) };
  }
  if ((r = m(/^\/api\/scenes\/([^/]+)$/))) {
    const s = store.scenes[r[1]];
    if (!s) throw new HttpErr(404, `No scene ${r[1]}`);
    if (method === 'GET') return sceneWithStations(s);
    if (method === 'PUT') { store.scenes[r[1]] = { ...(body as SceneSpec), id: r[1] }; save(); return { scene: sceneWithStations(store.scenes[r[1]]) }; }
  }
  if ((r = m(/^\/api\/scenes\/([^/]+)\/export$/)) && method === 'POST') {
    const id = r[1];
    return startJob(['Collecting the ship and crew', 'Writing articulation.json', 'Writing manifest.json', 'Zipping the package'], () =>
      ({ package_id: `pkg_${id}`, package_dir: `data/packages/pkg_${id}`, zip_url: `/api/files/packages/pkg_${id}.zip` }));
  }
  if ((r = m(/^\/api\/scenes\/([^/]+)\/unreal-test$/)) && method === 'POST') {
    return startJob(['Exporting the package', 'Starting Unreal 5.8 headless', 'Importing the ship', 'Placing crew at stations', 'Saving and reloading the level'], (): UnrealReport => ({
      ok: false, engine: '5.8', level: `/Game/ME3D/${r![1]}/L_${r![1]}`,
      checks: [
        { name: 'Ship imported', pass: true, detail: '(mock) 1 static mesh, 23 nodes' },
        { name: 'Articulated parts keep their pivots', pass: true, detail: '(mock) 8 of 8' },
        { name: 'Crew at their stations', pass: true, detail: '(mock) 2 of 2 within 1 cm' },
        { name: 'Level reloads with the same actors', pass: false, detail: '(mock) a failing check, to show how failures look' },
      ],
    }));
  }
  throw new HttpErr(404, `Mock has no route for ${method} ${path}`);
}

export async function mockTransport(method: string, path: string, body?: unknown): Promise<unknown> {
  await new Promise((r) => setTimeout(r, 120));
  try {
    return JSON.parse(JSON.stringify(await handle(method, path, body ?? null)));
  } catch (e) {
    if (e instanceof HttpErr) {
      throw new ApiError(e.status, httpErrorText(method, path, e.status, e.message), { detail: e.message });
    }
    throw e;
  }
}

export function mockModelUrl(projectId: string, n: number): string {
  return store.projects[projectId]?.glbs[n] ?? '/mock/ship_v1.glb';
}
