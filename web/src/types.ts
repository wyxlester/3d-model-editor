// Shapes from docs/CONTRACT.md. That file is binding; if this disagrees with it, this is wrong.

export type Vec3 = [number, number, number];

/** §3: parsed `me3d_joint`. `axis` is in the part's local Blender frame. */
export interface Joint {
  axis: 'x' | 'y' | 'z';
  min_deg: number;
  max_deg: number;
  priority: 'P0' | 'P1' | 'P2' | string;
  kind: 'revolute' | string;
}

/** §4 parts.json row. All positions in the Blender world frame, metres. */
export interface Part {
  path: string;
  label: string;
  parent: string | null;
  joint: Joint | null;
  /** Parsed `me3d_station` for stations, otherwise null. Shape not fixed by the contract. */
  station: Record<string, unknown> | boolean | null;
  origin_m?: Vec3;
  rotation_deg?: Vec3;
  bounds_min: Vec3;
  bounds_max: Vec3;
  triangles: number;
}

export interface BuildReport {
  ok?: boolean;
  errors?: string[];
  warnings?: string[];
  triangles?: number;
  bounds_min?: Vec3;
  bounds_max?: Vec3;
  seconds?: number;
}

export type PinStatus = 'open' | 'working' | 'waiting' | 'resolved';

/** A picture or document sent with a chat message (contract §10, Attachments). */
export interface Attachment {
  id: string;
  name: string;
  type: string;
  size: number;
  url: string;
}

export interface Message {
  who: 'You' | 'AI' | 'Customer' | string;
  text: string;
  at: string;
  attachments?: Attachment[];
}

export interface Pin {
  id: string;
  part: string;
  /** glTF / three.js world coordinates (Y up) where the click happened. */
  point: Vec3;
  status: PinStatus;
  author: string;
  messages: Message[];
  before_version?: number | null;
  after_version?: number | null;
}

export interface VersionInfo {
  n: number;
  at: string;
  summary: string;
  ok: boolean;
}

export type ProjectKind = 'ship' | 'crew';
/** `custom` = a ship of any type, made from a brief (CONTRACT §5 `type: "custom"`). */
export type ProjectBase = 'container' | 'sampan' | 'tanker' | 'male_default' | 'female_default' | 'custom';

/** §5 `type: "custom"`: what will be built, approved by the user before anything builds. */
export interface DesignPlan {
  status: 'draft' | 'approved' | 'stale';
  /** Plain-English lines: size, hull form, each superstructure tier, colours, details, assumptions. */
  summary: string[];
  /** The full custom ship spec it would build. */
  spec?: Record<string, unknown>;
  /** The research revision it was drafted against. */
  research_rev: number;
  at: string;
  /** Server extras: when it was approved, and why an approved plan failed to build (back to draft). */
  approved_at?: string;
  build_error?: string;
  /** The user's own values for the plan's params: they win when it is built (CONTRACT §12, Review). */
  user_params?: Record<string, unknown>;
}

/** A saved version of the research, as listed (the research itself is fetched by number). */
export interface ResearchVersion { n: number; at: string; subject?: string | null; verdict?: string | null; sources: number; claims: number }

/** Something the research AI searched for or read, kept for review (`text` only when fetched one by one). */
export interface Material { id: string; kind: 'search' | 'page' | string; title: string; at: string; chars: number; source: Record<string, unknown>; text?: string }

/** A finished job's log, kept with its project. */
export interface JobLogRecord { id: string; kind: string; label: string; status: string; error: string | null; created: string; finished: string | null; log: string[] }

export interface ShipSpec {
  schema: 'me3d.ship_spec/1';
  type: string;
  id: string;
  name: string;
  params: Record<string, unknown>;
  overrides?: Record<string, PartOverride>;
  joint_limits?: Record<string, { min_deg: number; max_deg: number }>;
  custom_ops?: { id: string; description: string; code: string }[];
}

export interface PartOverride {
  offset_m?: Vec3;
  rotate_deg?: Vec3;
  scale?: Vec3;
  color?: Vec3;
  hidden?: boolean;
}

/**
 * CONTRACT §6 "Modules": the nine parts of a crew member, each owning fixed spec keys. Equipment
 * owns its items and where each is carried (the old "placement" module was merged into it).
 */
export type ModuleId = 'gender' | 'skin' | 'hair' | 'shirt' | 'pants' | 'outfit' | 'shoes' | 'equipment' | 'accessories';

/** Where a held or attached equipment item goes (§6 "Placement", part of equipment). Absent = the item's fixed spot. */
export type Place = 'right_hand' | 'left_hand' | 'chest' | 'back' | 'hip' | 'head' | 'both_hands';

/** `clothes.shirt` / `clothes.pants`: a preset garment. */
export interface Garment { style: string; color?: Vec3 | null }

/** An equipment entry: a bare catalog name, or `{name, color, place}`. Colour is a name or [r,g,b]. */
export interface EquipmentEntry { name: string; color?: string | Vec3 | null; place?: Place }
export type Equipment = string | EquipmentEntry;

/** `accessories[]` entry (§6 "Modules"): a bare name, or `{name, color}`. */
export type Accessory = string | { name: string; color?: string | Vec3 | null };

export interface CrewSpec {
  schema: 'me3d.crew_spec/1';
  id: string;
  name: string;
  body: {
    gender: 'male' | 'female' | string;
    height_cm: number;
    weight: number;
    muscle: number;
    age_years: number;
    skin: string;
    /** Optional MPFB proportions slider 0..1 (default 0.5). */
    proportions?: number;
    /** Catalog hair style, "none" for bald, null/absent for the gender's default. */
    hair?: string | null;
    /** [r,g,b] 0..1 or null; absent in the default spec (CONTRACT §6 "Made by description"). */
    hair_color?: Vec3 | null;
  };
  clothes: {
    suit: string | null; shoes: string | null; hat: string | null; suit_color: Vec3 | null;
    shoes_color?: Vec3 | null; hat_color?: Vec3 | null;
    shirt?: Garment | null; pants?: Garment | null;
  };
  equipment: Equipment[];
  animations: string[];
  accessories?: Accessory[];
  /** Things the AI made from a description: Blender Python creating Clothes__* / Equipment__* objects. */
  custom_items?: CustomItem[];
}

export interface CustomItem { id: string; description: string; code: string; module?: ModuleId; place?: Place }

/** A project's spec: a ship spec (§5) or a crew spec (§6), depending on `kind`. */
export type ProjectSpec = { schema?: string } & Partial<Omit<ShipSpec, 'schema'>> & Partial<Omit<CrewSpec, 'schema' | 'id' | 'name'>> & Record<string, unknown>;

export interface Project {
  id: string;
  kind: ProjectKind;
  name: string;
  base: ProjectBase | string;
  version: number;
  spec: ProjectSpec;
  versions: VersionInfo[];
  model_url: string | null;
  parts: Part[];
  build_report: BuildReport;
  pins: Pin[];
  messages: Message[];
  research: Research | null;
  /** Bumped by every research save (custom ships, §5). */
  research_rev?: number;
  /** Custom ships only (§5): null until the AI drafts one. */
  design_plan?: DesignPlan | null;
  /** Research sources the user crossed out: the AI rests nothing on them. */
  excluded_sources?: string[];
  /** The research revision the model was last updated from (null: never). */
  research_applied_rev?: number | null;
  /** The request the project was made from: a custom ship's description, or what a variant was asked to change. */
  brief?: string | null;
  /** Custom ships: an approved plan has been built at least once, so Build works normally (server extra). */
  plan_built?: boolean;
  /** Set on creation when something the user should know happened (e.g. no placeholder model). */
  note?: string;
  /** Set on a variant: the project and version it was copied from. */
  derived_from?: DerivedFrom | null;
  /** Set on a variant until it is approved. */
  draft?: Draft | null;
  discarded?: boolean;
  /** A label for finding it (CONTRACT §10); null when neither it nor its catalog base has one. */
  category?: string | null;
  /** Server extra: whether POST /undo has something to undo. */
  can_undo?: boolean;
  /** The version POST /undo returns to; null when there is nothing to undo (CONTRACT §10). */
  undo_to?: number | null;
}

export type DraftStage = 'copying' | 'researching' | 'changing' | 'ready' | 'failed';

/** A variant's review state until it is approved (CONTRACT §10). */
export interface Draft {
  stage: DraftStage | string;
  job_id: string | null;
  research: boolean;
  scene: string | null;
  error: string | null;
}

export interface DerivedFrom {
  project: string;
  version: number;
  name: string;
}

/** POST /api/projects/{id}/variants body. */
export interface VariantRequest {
  name?: string;
  version?: number;
  brief?: string;
  research?: boolean;
  scene?: string;
}

/** The variant job's result. */
export interface VariantResult {
  version: number;
  build_report: BuildReport;
  reply?: string;
}

/** POST /api/projects/{id}/approve. */
export interface ApproveResult {
  project: Project;
  scene: { id: string; missing_stations: string[] } | null;
}

/** As the server sends it (not in §10's table). */
export interface ProjectSummary {
  id: string;
  kind: ProjectKind;
  name: string;
  base?: string;
  /** Custom ships: an approved plan has been built (variants need it). */
  plan_built?: boolean;
  version?: number;
  updated?: string;
  model_url?: string | null;
  open_pins?: number;
  ok?: boolean;
  derived_from?: DerivedFrom | null;
  draft?: Draft | null;
  discarded?: boolean;
  category?: string | null;
}

export interface Catalog {
  ships: { type: string; name: string; description: string; category?: string | null }[];
  crew: { id: string; name: string; category?: string | null }[];
  /** The default categories (catalog/categories.json). */
  categories?: { ship: string[]; crew: string[] };
  clothes: string[];
  equipment: string[];
  animations: string[];
}

export type Destination =
  | 'ship_research'
  | 'ship_build'
  | 'ship_variant'
  | 'crew'
  | 'crew_variant'
  | 'animation'
  | 'assembly'
  | 'review'
  | 'clarify';

export interface RouteResult {
  destination: Destination;
  reason: string;
  project_id: string | null;
}

export interface Health {
  blender: boolean;
  mpfb: boolean;
  ai: boolean;
  unreal: boolean;
}

export type JobStatus = 'queued' | 'running' | 'done' | 'error';

export interface Job<R = unknown> {
  id: string;
  status: JobStatus;
  log: string[];
  result: R;
  error: string | null;
  project?: string | null;
  label?: string;
}

/** A row of GET /api/jobs: what is going on, on every model (CONTRACT §10). */
export interface JobSummary {
  id: string;
  kind: string;
  label: string;
  project: string | null;
  project_name: string | null;
  project_kind: ProjectKind | null;
  status: JobStatus | string;
  /** The latest line written for people. */
  last: string;
  error: string | null;
  created: string;
  finished: string | null;
}

export interface SceneCrew {
  project: string;
  version: number;
  station: string;
  animation: string;
}

export interface SceneSpec {
  schema: 'me3d.scene_spec/1';
  id?: string;
  name: string;
  ship: { project: string; version: number };
  crew: SceneCrew[];
}

/** §10 says "Scene with stations from the ship's parts"; the stations shape is not fixed, so both forms are read. */
export interface Scene extends SceneSpec {
  id: string;
  stations?: (string | Part | { path: string; [k: string]: unknown })[];
}

export interface ExportResult {
  package_id?: string;
  package_dir: string;
  zip_url: string;
}

export interface UnrealReport {
  ok: boolean;
  engine: string;
  level: string;
  checks: { name: string; pass: boolean; detail: string }[];
}

// Research (CONTRACT §12).

export type EvidenceStatus =
  | 'Official'
  | 'Photographed'
  | 'Official + photo'
  | 'Photo reading'
  | 'Inference'
  | 'Comparison only'
  | 'Secondary lead'
  | 'Unverified'
  | string;

/** §12 "Photographs and close-ups": how a photo may be used. */
export type PhotoStatus = 'baseline' | 'comparison' | 'excluded';

/**
 * §12 `photos[]`. The AI writes the provenance; the server writes `width`, `height`, `sha256`,
 * `accessed` and `file` when it downloads the image. `file` is the stored copy, served by the app.
 */
export interface ResearchPhoto {
  id: string;
  title: string;
  vessel?: string;
  views?: string[];
  credit?: string;
  date?: string;
  observation?: string;
  /** The page the photo was published on. */
  source?: string;
  /** The image itself, as published. Never shown directly: only the server's stored `file` is. */
  image_url?: string;
  status?: PhotoStatus | string;
  rights?: string;
  modelling_use?: string;
  width?: number;
  height?: number;
  sha256?: string;
  accessed?: string;
  file?: string;
}

/** §12 `closeups[]`: a crop the server cut from a stored photo. `box` is [x, y, w, h] in the photo's original pixels. */
export interface ResearchCloseup {
  id: string;
  photo: string;
  box?: [number, number, number, number];
  title: string;
  what_it_shows?: string;
  claims?: string[];
  file?: string;
}

/** §12 `coverage`: which photo ids show each view (free text such as "P01, P03"), null when none does. */
export type CoverageView = 'port' | 'starboard' | 'bow' | 'stern' | 'top' | 'details';
export type Coverage = Partial<Record<CoverageView, string | null>> & { note?: string | null };

/** §12 `me3d.research/1`. The build plan is a canvas section the contract does not define yet. */
export interface Research {
  schema?: 'me3d.research/1' | string;
  subject?: string;
  summary?: { verdict?: string; existing_work?: string };
  sources?: { id: string; title: string; url?: string; status: EvidenceStatus; what_it_proves?: string }[];
  claims?: { claim: string; value?: string | number | null; unit?: string | null; sources?: string[] | string; status: EvidenceStatus; consequence_for_model?: string }[];
  open_questions?: { question: string; meanwhile?: string }[];
  articulation?: { part: string; motion: string; priority: string }[];
  /** Distinct exposures only (crops and republished copies do not count), and how they were counted. */
  photo_count?: number;
  count_method?: string;
  coverage?: Coverage;
  photos?: ResearchPhoto[];
  closeups?: ResearchCloseup[];
  // Not in the contract (canvas-only section):
  plan?: { n: string; title: string; what: string; accept: string }[];
  [key: string]: unknown;
}
