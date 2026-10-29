// Variant review (CONTRACT §10): the progress steps and the feed. Pure, unit tested.
import type { Draft } from '../types';

export type StepState = 'todo' | 'active' | 'done' | 'failed';
export interface Step { key: 'copy' | 'research' | 'change' | 'review'; label: string; state: StepState }

const LABELS: Record<Step['key'], string> = {
  copy: 'Copy and build it in Blender',
  research: 'Research the changes',
  change: 'AI makes the changes',
  review: 'Your review',
};
const AT: Record<string, Step['key']> = { copying: 'copy', researching: 'research', changing: 'change', ready: 'review', failed: 'copy' };

/** The steps this variant goes through, and where it is. A null draft means it was approved. */
export function variantSteps(draft: Pick<Draft, 'stage' | 'research' | 'error'> | null, opts: { brief: boolean }): Step[] {
  const keys: Step['key'][] = ['copy'];
  if (draft?.research) keys.push('research');
  if (opts.brief) keys.push('change');
  keys.push('review');
  if (!draft) return keys.map((key) => ({ key, label: LABELS[key], state: 'done' }));
  const at = keys.indexOf(AT[draft.stage] ?? 'copy');
  // A failed AI step leaves the variant reviewable: the last AI step before review is the one that failed.
  const failedAt = draft.stage === 'failed' ? at : draft.stage === 'ready' && draft.error && keys.length > 2 ? keys.length - 2 : -1;
  return keys.map((key, i) => ({
    key, label: LABELS[key],
    state: i === failedAt ? 'failed' : i < at ? 'done' : i === at && draft.stage !== 'failed' ? 'active' : 'todo',
  }));
}

/** The job log lines written for people ("• …"), without the marker. */
export function activityLines(log: string[]): string[] {
  return log.filter((l) => l.startsWith('• ')).map((l) => l.slice(2));
}
