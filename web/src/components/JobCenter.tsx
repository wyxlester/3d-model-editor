// Work across models (CONTRACT §10, GET /api/jobs). The server keeps working on a model while the
// user is on another one; this keeps the app aware of it on every page: a "working" list in the
// top bar, a note when a change finishes, and a signal so a model's screen reloads itself.
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { api } from '../api';
import type { RunningJob } from '../hooks';
import { activeFor, isActiveJob, newlyFinished } from '../lib/jobcenter';
import { paths } from '../lib/routing';
import type { JobSummary } from '../types';

interface Center {
  jobs: JobSummary[];
  /** Per project, how many of its jobs have finished since the page opened. */
  finished: Record<string, number>;
  /** Look again now (call after starting a job). */
  refresh: () => void;
}

const NONE: Center = { jobs: [], finished: {}, refresh: () => undefined };
const Ctx = createContext<Center>(NONE);

const ACTIVE_POLL_MS = 1500;
const IDLE_POLL_MS = 5000;
const DONE_NOTE_MS = 8000;

const pathOf = (j: JobSummary) => (!j.project ? null : j.project_kind === 'crew' ? paths.crew(j.project) : paths.shipBuild(j.project));

export function JobCenterProvider({ children }: { children: ReactNode }) {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [finished, setFinished] = useState<Record<string, number>>({});
  const [notes, setNotes] = useState<JobSummary[]>([]);
  const before = useRef<Map<string, string> | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const loc = useLocation();
  const here = useRef(loc.pathname);
  here.current = loc.pathname;

  const look = useCallback(async () => {
    window.clearTimeout(timer.current);
    let active = false;
    try {
      const now = (await api.jobs()).jobs;
      const done = newlyFinished(before.current, now);
      before.current = new Map(now.map((j) => [j.id, j.status]));
      active = now.some(isActiveJob);
      setJobs(now);
      if (done.length) {
        setFinished((f) => {
          const next = { ...f };
          for (const j of done) if (j.project) next[j.project] = (next[j.project] ?? 0) + 1;
          return next;
        });
        // A note for work on a model the user is not looking at; that model's own screen updates itself.
        const away = done.filter((j) => j.project && !here.current.includes(j.project));
        if (away.length) setNotes((n) => [...n, ...away]);
      }
    } catch { /* the server may be restarting: try again later */ }
    timer.current = window.setTimeout(() => void look(), active ? ACTIVE_POLL_MS : IDLE_POLL_MS);
  }, []);

  useEffect(() => {
    void look();
    return () => window.clearTimeout(timer.current);
  }, [look]);

  // Finished notes fade on their own; failures stay until dismissed.
  useEffect(() => {
    if (!notes.some((n) => n.status === 'done')) return;
    const t = window.setTimeout(() => setNotes((n) => n.filter((x) => x.status !== 'done')), DONE_NOTE_MS);
    return () => window.clearTimeout(t);
  }, [notes]);

  const refresh = useCallback(() => { void look(); }, [look]);
  return (
    <Ctx.Provider value={{ jobs, finished, refresh }}>
      {children}
      {notes.length > 0 && (
        <div className="notes" role="status" aria-live="polite">
          {notes.map((n) => (
            <div key={n.id} className={`note ${n.status === 'done' ? 'ok' : 'bad'}`}>
              <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
                <span><strong>{n.project_name ?? 'A model'}</strong> {n.status === 'done' ? 'is ready' : 'could not finish a change'}</span>
                <span className="muted" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>{n.status === 'done' ? n.label : n.error ?? n.label}</span>
              </span>
              {pathOf(n) && <Link className="btn sm" to={pathOf(n)!} onClick={() => setNotes((x) => x.filter((y) => y.id !== n.id))}>Open</Link>}
              <button className="btn sm muted" aria-label="Dismiss" onClick={() => setNotes((x) => x.filter((y) => y.id !== n.id))}>×</button>
            </div>
          ))}
        </div>
      )}
    </Ctx.Provider>
  );
}

export function useJobCenter(): Center {
  return useContext(Ctx);
}

/**
 * The work going on for one model, whichever page started it. `onFinished` runs each time one of
 * its jobs finishes, so a screen opened mid-change shows the result without a manual reload.
 */
export function useProjectWork(project: string | undefined, onFinished?: () => void, local: RunningJob[] = []) {
  const { jobs, finished, refresh } = useJobCenter();
  const count = project ? finished[project] ?? 0 : 0;
  const seen = useRef(count);
  const callback = useRef(onFinished);
  callback.current = onFinished;
  useEffect(() => {
    if (count !== seen.current) {
      seen.current = count;
      callback.current?.();
    }
  }, [count]);
  const active = activeFor(jobs, project);
  const mine = new Set(local.map((j) => (j.state.phase === 'idle' ? null : j.state.jobId)));
  // Work this screen did not start (or no longer tracks, after leaving and coming back).
  const others = active.filter((j) => !mine.has(j.id));
  const chat = others.find((j) => j.kind === 'chat' || j.kind === 'variant');
  return {
    active, others, refresh,
    /** For ChatPanel's `working`: an AI turn on this model still running from earlier. */
    chatWorking: chat ? { text: chat.status === 'queued' ? 'Waiting for the change before it…' : chat.last || 'AI is working…' } : null,
  };
}

/** Progress lines for a model's work that this screen did not start. */
export function OtherWork({ jobs }: { jobs: JobSummary[] }) {
  if (!jobs.length) return null;
  return (
    <div className="jobs" role="status" aria-live="polite">
      {jobs.map((j) => (
        <div key={j.id} className="jobline">
          <span className="spinner" aria-hidden="true" />
          <span style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>
            {j.label}{j.status === 'queued' ? ' · waiting for the change before it' : j.last ? ` · ${j.last}` : ''}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Top-bar list of every model being worked on, with a link to each. */
export function WorkIndicator() {
  const { jobs } = useJobCenter();
  const [open, setOpen] = useState(false);
  const active = jobs.filter(isActiveJob);
  if (!active.length) return null;
  return (
    <div style={{ position: 'relative' }} onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false); }}>
      <button className="btn sm" aria-expanded={open} onClick={() => setOpen(!open)}
        title="Changes the AI and Blender are working on, on any model">
        <span className="spinner" aria-hidden="true" />
        {active.length === 1 ? '1 change in progress' : `${active.length} changes in progress`}
      </button>
      {open && (
        <div className="work-list" role="dialog" aria-label="Changes in progress">
          {active.map((j) => {
            const to = pathOf(j);
            const body = (
              <>
                <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <strong style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{j.project_name ?? 'Export'}</strong>
                  <span className={`chip ${j.status === 'queued' ? 'grey' : 'blue'}`} style={{ marginLeft: 'auto' }}>{j.status === 'queued' ? 'Waiting its turn' : 'Working'}</span>
                </span>
                <span className="muted" style={{ fontSize: 12 }}>{j.label}</span>
                {j.last && <span className="faint" style={{ fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{j.last}</span>}
              </>
            );
            return to
              ? <Link key={j.id} to={to} className="work-row" onClick={() => setOpen(false)}>{body}</Link>
              : <div key={j.id} className="work-row">{body}</div>;
          })}
        </div>
      )}
    </div>
  );
}
