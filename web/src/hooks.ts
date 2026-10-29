import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorText } from './api';
import { pollJob } from './lib/poll';
import type { PollState } from './lib/jobs';
import type { Catalog, Health, Project, ProjectSummary } from './types';

/** Load something once (and on `reload`), tracking error and loading. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const reload = useCallback(async () => {
    const mine = ++seq.current;
    setLoading(true);
    try {
      const d = await load();
      if (mine === seq.current) { setData(d); setError(null); }
      return d;
    } catch (e) {
      if (mine === seq.current) setError(errorText(e));
      return null;
    } finally {
      if (mine === seq.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { void reload(); }, [reload]);
  return { data, setData, error, loading, reload };
}

export function useProject(id: string | undefined) {
  return useLoad<Project | null>(() => (id ? api.project(id) : Promise.resolve(null)), [id]);
}

export function useProjects() {
  return useLoad<ProjectSummary[]>(() => api.projects(), []);
}

let catalogCache: Promise<Catalog> | null = null;
export function useCatalog() {
  return useLoad<Catalog>(() => {
    catalogCache ??= api.catalog().catch((e) => { catalogCache = null; throw e; });
    return catalogCache;
  }, []);
}

export function useHealth() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = () => api.health().then((h) => { if (alive) { setHealth(h); setError(null); } }, (e) => { if (alive) setError(errorText(e)); });
    tick();
    const t = setInterval(tick, 15_000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  return { health, error };
}

export interface RunningJob {
  key: number;
  label: string;
  state: PollState;
}

/**
 * Track the jobs a screen started. Each job shows a live progress line (elapsed time and
 * the last log line) until it finishes; finished lines linger briefly, errors stay until dismissed.
 */
export function useJobs() {
  const [jobs, setJobs] = useState<RunningJob[]>([]);
  const keyRef = useRef(0);
  const alive = useRef(true);
  // Set on every mount: StrictMode's dev-only unmount/remount would otherwise leave it false, and a
  // job started at mount (Research watching the first research turn) would never update its line.
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const run = useCallback(async <R,>(
    label: string,
    start: () => Promise<{ job_id: string }>,
    onDone?: (result: R) => void | Promise<void>,
    /** Also called when the job fails: a failed build is still saved as a (non-current) version. */
    onError?: (error: string) => void | Promise<void>,
  ) => {
    const key = ++keyRef.current;
    const put = (state: PollState) => alive.current && setJobs((js) => {
      const rest = js.filter((j) => j.key !== key);
      return [...rest, { key, label, state }].sort((a, b) => a.key - b.key);
    });
    put({ phase: 'polling', jobId: '…', status: 'queued', log: ['Sending'], failures: 0, startedAt: Date.now() });
    try {
      const { job_id } = await start();
      const result = await pollJob<R>(job_id, { onUpdate: (s) => put(s as PollState) });
      await onDone?.(result);
      setTimeout(() => alive.current && setJobs((js) => js.filter((j) => j.key !== key)), 4000);
      return result;
    } catch (e) {
      put({ phase: 'error', jobId: null, error: errorText(e), log: [], startedAt: Date.now(), endedAt: Date.now() });
      try { await onError?.(errorText(e)); } catch { /* the error line already shows */ }
      return undefined;
    }
  }, []);

  const dismiss = useCallback((key: number) => setJobs((js) => js.filter((j) => j.key !== key)), []);
  const busy = jobs.some((j) => j.state.phase === 'polling');
  return { jobs, run, dismiss, busy };
}

/** Re-render every second while something is running, for elapsed-time text. */
export function useTicker(active: boolean) {
  const [, set] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => set((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [active]);
}

/** Per-viewer convenience: the last project opened in each workshop. Safe when storage is blocked. */
export function remember(kind: 'ship' | 'crew' | 'scene', id: string) {
  try { localStorage.setItem(`me3d-last-${kind}`, id); } catch { /* ignore */ }
}
export function recall(kind: 'ship' | 'crew' | 'scene'): string | null {
  try { return localStorage.getItem(`me3d-last-${kind}`); } catch { return null; }
}
