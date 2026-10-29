// Work across models (CONTRACT §10, GET /api/jobs): which jobs are still going, and which have
// just finished since the last look. Pure, unit tested.
import type { JobSummary } from '../types';

export const isActiveJob = (j: Pick<JobSummary, 'status'>) => j.status === 'queued' || j.status === 'running';

/**
 * Jobs that finished since the last look (`before`: id → status then). A job counts if it was
 * queued or running then, or was not listed at all (it started and finished in between: the
 * listing keeps finished jobs for minutes, so anything new to it is new). Nothing on the first
 * look, when `before` is null: jobs that ended before the page opened are not news.
 */
export function newlyFinished(before: Map<string, string> | null, now: readonly JobSummary[]): JobSummary[] {
  if (!before) return [];
  return now.filter((j) => {
    if (isActiveJob(j)) return false;
    const then = before.get(j.id);
    return then === undefined || then === 'queued' || then === 'running';
  });
}

/** What is still queued or running for one model. */
export function activeFor<J extends Pick<JobSummary, 'project' | 'status'>>(jobs: readonly J[], project: string | null | undefined): J[] {
  if (!project) return [];
  return jobs.filter((j) => j.project === project && isActiveJob(j));
}
