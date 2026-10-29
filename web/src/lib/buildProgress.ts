// What the build of an approved design plan is doing, read from its job log (CONTRACT §10: "• "
// lines; the server writes "Building version N in Blender: …" and "Version N built" for every build,
// and one line per detail the AI adds). Pure, unit tested.
import { activityLines } from './variant';

export type BuildStepKind = 'building' | 'built' | 'failed' | 'detail' | 'ai' | 'other';

export interface BuildStep {
  kind: BuildStepKind;
  text: string;
  /** The version a building, built or failed line is about. */
  version?: number;
}

export interface BuiltVersion {
  n: number;
  ok: boolean;
  /** The summary given when its build started ("Built from the approved design plan", "AI edit: custom_ops"…). */
  summary: string;
  /** The details the AI added or changed for this build, in words ("Adding port rail"…). */
  added: string[];
}

/** What a version is, for people: the details that went into it, or its build summary. */
export function versionText(v: BuiltVersion): string {
  return v.added.length ? v.added.join('; ') : v.summary;
}

/** A step's line for the list: a failure's message can be a page long (a probe's measurements), so it is cut. */
export function shortStep(text: string, max = 160): string {
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}

export interface BuildProgress {
  steps: BuildStep[];
  /** Every version this job built, in order, with whether it built. */
  versions: BuiltVersion[];
  /** The step under way: the last that is not the AI talking. */
  now: BuildStep | null;
  /** Details added since the job began. */
  details: number;
}

const BUILDING = /^Building version (\d+) in Blender:\s*(.*)$/;
const BUILT = /^Version (\d+) built$/;
const FAILED = /^Version (\d+) failed to build:\s*(.*)$/;
const DETAIL = /^(Adding|Making|Changing|Adjusting|Hiding|Resetting|Removing|Setting)\b/;

export function buildStep(text: string): BuildStep {
  let m = BUILDING.exec(text);
  if (m) return { kind: 'building', text, version: Number(m[1]) };
  m = BUILT.exec(text);
  if (m) return { kind: 'built', text, version: Number(m[1]) };
  m = FAILED.exec(text);
  if (m) return { kind: 'failed', text, version: Number(m[1]) };
  if (text.startsWith('AI: ')) return { kind: 'ai', text: text.slice(4) };
  // "Adding the plan's details" is the job's own heading, not a detail.
  if (DETAIL.test(text) && text !== "Adding the plan's details") return { kind: 'detail', text };
  return { kind: 'other', text };
}

export function buildProgress(log: string[]): BuildProgress {
  const steps = activityLines(log).map(buildStep);
  const summaries = new Map<number, string>();
  const addedFor = new Map<number, string[]>();
  const versions: BuiltVersion[] = [];
  let pending: string[] = []; // details since the last build started
  for (const s of steps) {
    if (s.kind === 'detail') pending.push(s.text);
    if (s.kind === 'building' && s.version != null) {
      summaries.set(s.version, BUILDING.exec(s.text)?.[2] ?? '');
      addedFor.set(s.version, pending);
      pending = [];
    }
    if ((s.kind === 'built' || s.kind === 'failed') && s.version != null) {
      versions.push({ n: s.version, ok: s.kind === 'built', summary: summaries.get(s.version) ?? '', added: addedFor.get(s.version) ?? [] });
    }
  }
  const acts = steps.filter((s) => s.kind !== 'ai');
  return { steps, versions, now: acts[acts.length - 1] ?? null, details: steps.filter((s) => s.kind === 'detail').length };
}

/** The headline for the step under way. */
export function buildHeadline(p: BuildProgress, running: boolean): string {
  if (!running) {
    const last = [...p.versions].reverse().find((v) => v.ok);
    return last ? `Finished: version ${last.n} is the ship as planned` : 'The build finished';
  }
  const s = p.now;
  if (!s) return 'Starting the build';
  if (s.kind === 'building') return `Building version ${s.version} in Blender`;
  if (s.kind === 'built') return `Version ${s.version} built; the AI is choosing what to add next`;
  if (s.kind === 'failed') return `Version ${s.version} did not build; the AI is fixing it`;
  return s.text;
}
