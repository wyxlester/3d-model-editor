// Typed client for the server API, exactly per docs/CONTRACT.md §10.
// VITE_MOCK=1 routes every call to an in-browser mock (src/mock) for development before the
// server exists. The default build always talks to the real /api.
import type {
  ApproveResult, Attachment, Catalog, ExportResult, Health, Job, JobSummary, ModuleId, Part, Pin, Project, ProjectBase,
  ProjectKind, ProjectSummary, RouteResult, Scene, SceneSpec, UnrealReport, VariantRequest, Vec3,
} from './types';
import { httpErrorText } from './lib/designPlan';

export const MOCK = import.meta.env.VITE_MOCK === '1';

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) {
    super(message);
  }
}

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH';
type Transport = (method: Method, path: string, body?: unknown) => Promise<unknown>;

const httpTransport: Transport = async (method, path, body) => {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError(0, `Can't reach the server (${(e as Error).message}). Is it running on port 8787?`);
  }
  const text = await res.text();
  let data: unknown = text;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* not JSON */
  }
  if (!res.ok) {
    const detail = (data && typeof data === 'object' && 'detail' in data) ? (data as { detail: unknown }).detail : text;
    throw new ApiError(res.status, httpErrorText(method, path, res.status, detail), data);
  }
  return data;
};

let transport: Transport = httpTransport;
let mockModelUrl: ((projectId: string, n: number) => string) | null = null;

/** Called by src/mock/install.ts in mock mode only. */
export function installMock(t: Transport, modelUrl: (projectId: string, n: number) => string) {
  transport = t;
  mockModelUrl = modelUrl;
}

const call = <T>(method: Method, path: string, body?: unknown) => transport(method, path, body) as Promise<T>;
const enc = encodeURIComponent;

export const api = {
  health: () => call<Health>('GET', '/api/health'),
  catalog: () => call<Catalog>('GET', '/api/catalog'),
  route: (message: string, attachments: string[] = []) =>
    call<RouteResult>('POST', '/api/route', attachments.length ? { message, attachments } : { message }),

  projects: () => call<ProjectSummary[]>('GET', '/api/projects'),
  /** `brief` is the user's description of a custom ship (§5 `type: "custom"`). */
  createProject: (kind: ProjectKind, base: ProjectBase, name?: string, brief?: string) =>
    call<{ project: Project; job_id: string }>('POST', '/api/projects', { kind, base, ...(name ? { name } : {}), ...(brief ? { brief } : {}) }),
  /** Approve a custom ship's current draft plan and build it (§5). 409 with a sentence when it can't be. */
  approvePlan: (id: string) => call<{ job_id: string }>('POST', `/api/projects/${enc(id)}/design-plan/approve`),
  project: (id: string) => call<Project>('GET', `/api/projects/${enc(id)}`),
  parts: (id: string, n: number) => call<Part[]>('GET', `/api/projects/${enc(id)}/versions/${n}/parts.json`),
  patchSpec: (id: string, patch: Record<string, unknown>) => call<{ job_id: string }>('POST', `/api/projects/${enc(id)}/spec`, { patch }),
  /** `module` scopes a crew chat to one module (CONTRACT §6 "Scoped AI"); omitted = the whole person. */
  chat: (id: string, message: string, mode: 'build' | 'research', attachments: string[] = [], module?: ModuleId) =>
    call<{ job_id: string }>('POST', `/api/projects/${enc(id)}/chat`, {
      message, mode, ...(attachments.length ? { attachments } : {}), ...(module ? { module } : {}),
    }),
  uploadHome: (file: { name: string; type: string; data: string }) =>
    call<{ attachment: Attachment }>('POST', '/api/uploads', file),
  upload: (id: string, file: { name: string; type: string; data: string }) =>
    call<{ attachment: Attachment }>('POST', `/api/projects/${enc(id)}/uploads`, file),
  addPin: (id: string, pin: { part: string; point: Vec3; text: string; author: string }) =>
    call<{ pin: Pin; job_id: string }>('POST', `/api/projects/${enc(id)}/pins`, pin),
  replyPin: (id: string, pinId: string, text: string) =>
    call<{ job_id: string }>('POST', `/api/projects/${enc(id)}/pins/${enc(pinId)}/reply`, { text }),
  setPinStatus: (id: string, pinId: string, status: 'open' | 'resolved') =>
    call<{ pin: Pin }>('PATCH', `/api/projects/${enc(id)}/pins/${enc(pinId)}`, { status }),
  undo: (id: string) => call<{ project: Project }>('POST', `/api/projects/${enc(id)}/undo`),
  /** Rename a project or file it under a category; no rebuild. */
  patchProject: (id: string, fields: { category?: string; name?: string }) =>
    call<{ project: Project }>('PATCH', `/api/projects/${enc(id)}`, fields),
  createVariant: (id: string, req: VariantRequest) =>
    call<{ project: Project; job_id: string }>('POST', `/api/projects/${enc(id)}/variants`, req),
  approve: (id: string) => call<ApproveResult>('POST', `/api/projects/${enc(id)}/approve`),
  discard: (id: string) => call<{ project: Project }>('POST', `/api/projects/${enc(id)}/discard`),

  job: <R = unknown>(jobId: string) => call<Job<R>>('GET', `/api/jobs/${enc(jobId)}`),
  jobs: () => call<{ jobs: JobSummary[] }>('GET', '/api/jobs'),

  scenes: () => call<Scene[]>('GET', '/api/scenes'),
  createScene: (spec: Omit<SceneSpec, 'id'>) => call<{ scene: Scene }>('POST', '/api/scenes', spec),
  scene: (id: string) => call<Scene>('GET', `/api/scenes/${enc(id)}`),
  updateScene: (id: string, spec: SceneSpec) => call<{ scene: Scene }>('PUT', `/api/scenes/${enc(id)}`, spec),
  exportScene: (id: string) => call<{ job_id: string }>('POST', `/api/scenes/${enc(id)}/export`),
  unrealTest: (id: string) => call<{ job_id: string }>('POST', `/api/scenes/${enc(id)}/unreal-test`),
};

export type ExportJobResult = ExportResult;
export type UnrealJobResult = UnrealReport;

/** URL of a project version's GLB. */
export function modelUrl(projectId: string, n: number): string {
  if (mockModelUrl) return mockModelUrl(projectId, n);
  return `/api/projects/${enc(projectId)}/versions/${n}/model.glb`;
}

/** A file under the server's data folder (zips, packages), e.g. an export's `zip_url`. */
export function fileUrl(pathOrUrl: string): string {
  if (/^(https?:)?\//.test(pathOrUrl)) return pathOrUrl;
  return `/api/files/${pathOrUrl.split('/').map(enc).join('/')}`;
}

export function errorText(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}
