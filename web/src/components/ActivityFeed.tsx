// A live, plain-English feed of a model's jobs (CONTRACT §10: log lines starting "• "), with the raw
// log one click away, and the step list that sits above it. Shared by the variant review and the
// study screen.
import { useCallback, useEffect, useRef, useState } from 'react';
import { errorText } from '../api';
import { useTicker } from '../hooks';
import { pollJob } from '../lib/poll';
import { activityLines, type StepState } from '../lib/variant';

export interface FeedSection {
  jobId: string;
  label: string;
  log: string[];
  phase: 'running' | 'done' | 'error';
  error?: string;
  startedAt: number;
  endedAt?: number;
}

/**
 * Jobs being followed on this screen. `watch` starts polling one (a finished job replays its whole
 * log); while any runs, the project is re-read every few seconds so each version shows as it builds.
 */
export function useWatchedJobs(reload: () => Promise<unknown>) {
  const [sections, setSections] = useState<FeedSection[]>([]);
  const aborts = useRef<AbortController[]>([]);
  const watched = useRef(new Set<string>());
  useEffect(() => () => {
    // Stop polling on unmount. Reset too, so StrictMode's second mount watches again from scratch.
    aborts.current.forEach((a) => a.abort());
    aborts.current = [];
    watched.current.clear();
    setSections([]);
  }, []);

  const watch = useCallback((jobId: string, label: string) => {
    if (watched.current.has(jobId)) return;
    watched.current.add(jobId);
    setSections((ss) => [...ss, { jobId, label, log: [], phase: 'running', startedAt: Date.now() }]);
    const ac = new AbortController();
    aborts.current.push(ac);
    const set = (patch: Partial<FeedSection>) => setSections((ss) => ss.map((s) => (s.jobId === jobId ? { ...s, ...patch } : s)));
    pollJob(jobId, { signal: ac.signal, onUpdate: (s) => { if ('log' in s) set({ log: s.log }); } })
      .then(() => set({ phase: 'done', endedAt: Date.now() }), (e) => { if (!ac.signal.aborted) set({ phase: 'error', error: errorText(e), endedAt: Date.now() }); })
      .finally(() => { if (!ac.signal.aborted) void reload(); });
  }, [reload]);

  const watching = sections.some((s) => s.phase === 'running');
  useTicker(watching);
  useEffect(() => {
    if (!watching) return;
    const t = setInterval(() => void reload(), 2500);
    return () => clearInterval(t);
  }, [watching, reload]);

  return { sections, watch, watching };
}

/** The feed: each job's lines for people, newest at the bottom; "Show full log" for everything. */
export function ActivityFeed({ sections, empty }: { sections: FeedSection[]; empty: string }) {
  const [showRaw, setShowRaw] = useState(false);
  const feedRef = useRef<HTMLDivElement>(null);
  const lineCount = sections.reduce((n, s) => n + s.log.length, 0);
  useEffect(() => { feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight }); }, [lineCount, showRaw]);
  const now = Date.now();
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', padding: '10px 16px 0', fontSize: 12 }}>
        <span className="faint">What's happening</span>
        <button className="btn sm muted" style={{ marginLeft: 'auto', height: 24 }} onClick={() => setShowRaw(!showRaw)}>{showRaw ? 'Show summary' : 'Show full log'}</button>
      </div>
      <div ref={feedRef} className="scroll" style={{ flex: 1, minHeight: 80, padding: '8px 16px 12px', display: 'flex', flexDirection: 'column', gap: 10, fontSize: 12, lineHeight: 1.5 }}>
        {!sections.length && <span className="faint">{empty}</span>}
        {sections.map((s) => {
          const lines = showRaw ? s.log : activityLines(s.log);
          return (
            <div key={s.jobId} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ display: 'flex', gap: 8, color: 'var(--text-2)', fontWeight: 500 }}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.label}</span>
                {s.phase === 'running' && <span className="faint" style={{ marginLeft: 'auto', fontWeight: 400 }}>{Math.round((now - s.startedAt) / 1000)} s</span>}
              </span>
              {lines.map((l, i) => (
                <span key={i} className={showRaw ? 'mono' : undefined}
                  style={{ display: 'flex', gap: 8, color: l.startsWith('AI:') ? 'var(--text)' : 'var(--text-2)', overflowWrap: 'anywhere', fontSize: showRaw ? 11 : 12 }}>
                  {!showRaw && (i === lines.length - 1 && s.phase === 'running' ? <span className="spinner" style={{ marginTop: 3 }} /> : <span className="faint">·</span>)}
                  <span style={{ minWidth: 0 }}>{l}</span>
                </span>
              ))}
              {s.phase === 'running' && !lines.length && <span className="faint" style={{ display: 'flex', gap: 8 }}><span className="spinner" style={{ marginTop: 3 }} />Starting…</span>}
              {s.phase === 'error' && <span className="bad">{s.error}</span>}
            </div>
          );
        })}
      </div>
    </>
  );
}

const STEP_MARK: Record<StepState, string> = { done: '✓', active: '', failed: '✗', todo: '' };
const STEP_COLOR: Record<StepState, string> = { done: 'var(--green-text)', active: 'var(--accent-link)', failed: 'var(--red-text)', todo: 'var(--text-3)' };

/** A short list of steps: done, the one under way (spinning while work runs), failed, or to come. */
export function StepList({ steps, busy }: { steps: { key: string; label: string; state: StepState }[]; busy: boolean }) {
  return (
    <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }} aria-label="Progress">
      {steps.map((s, i) => (
        <li key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 8, color: STEP_COLOR[s.state] }}>
          <span style={{ width: 16, display: 'inline-flex', justifyContent: 'center' }}>
            {s.state === 'active' && (i < steps.length - 1 || busy) ? <span className="spinner" /> : STEP_MARK[s.state] || '○'}
          </span>
          <span style={{ color: s.state === 'todo' ? 'var(--text-3)' : 'var(--text)' }}>{s.label}</span>
        </li>
      ))}
    </ol>
  );
}
