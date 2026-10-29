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
export type ProjectBase = 'container' | 'sampan' | 'tanker' | 'male_default';

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
  };
  clothes: { suit: string | null; shoes: string | null; hat: string | null; suit_color: Vec3 | null };
  equipment: string[];
  animations: string[];
}

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
  /** Server extra: whether POST /undo has something to undo. */
  can_undo?: boolean;
}

/** As the server sends it (not in §10's table). */
export interface ProjectSummary {
  id: string;
  kind: ProjectKind;
  name: string;
  base?: string;
  version?: number;
  updated?: string;
  model_url?: string | null;
  open_pins?: number;
  ok?: boolean;
}

export interface Catalog {
  ships: { type: string; name: string; description: string }[];
  crew: { id: string; name: string }[];
  clothes: string[];
  equipment: string[];
  animations: string[];
}

export type Destination =
  | 'ship_research'
  | 'ship_build'
  | 'crew'
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

export interface ResearchPhoto {
  id: string;
  title: string;
  vessel?: string;
  views?: string[];
  credit?: string;
  date?: string;
  observation?: string;
  source?: string;
  image_url?: string;
  width?: number;
  height?: number;
  sha256?: string;
  accessed?: string;
  rights?: string;
  parts?: string;
}

/** §12 `me3d.research/1`. Photos, close-ups, coverage and the build plan are canvas sections the
 * contract does not define yet; they render only if the server ever sends them. */
export interface Research {
  schema?: 'me3d.research/1' | string;
  subject?: string;
  summary?: { verdict?: string; existing_work?: string };
  sources?: { id: string; title: string; url?: string; status: EvidenceStatus; what_it_proves?: string }[];
  claims?: { claim: string; sources?: string[] | string; status: EvidenceStatus; consequence_for_model?: string }[];
  open_questions?: { question: string; meanwhile?: string }[];
  articulation?: { part: string; motion: string; priority: string }[];
  // Not in the contract (canvas-only sections):
  photos?: ResearchPhoto[];
  coverage?: { view: string; ok: boolean | 'partial'; note: string }[];
  closeups?: { title: string; photo: string; hull?: string; text: string; image_url?: string }[];
  plan?: { n: string; title: string; what: string; accept: string }[];
  [key: string]: unknown;
}
