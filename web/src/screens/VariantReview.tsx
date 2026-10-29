// Variant review (CONTRACT §10): watch a variant being made, the original on the left and the
// variant on the right, updating as each version builds; ask for more changes; then approve it
// (it becomes an ordinary project) or discard it. Nothing about the original changes either way.
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, errorText, modelUrl } from '../api';
import { ChatPanel, ErrorNote, Loading, TopBar } from '../components/common';
import { remember, useLoad, useProject, useTicker } from '../hooks';
import type { ViewName } from '../lib/frames';
import { pollJob } from '../lib/poll';
import { paths, projectPath } from '../lib/routing';
import { activityLines, variantSteps, type StepState } from '../lib/variant';
import type { Part, Project } from '../types';
import { CrewViewer, ShipViewer, ViewButtons } from '../viewer/Viewer';

interface Section { jobId: string; label: string; log: string[]; phase: 'running' | 'done' | 'error'; error?: string; startedAt: number; endedAt?: number }

const RUNNING = ['copying', 'researching', 'changing'];
const STEP_MARK: Record<StepState, string> = { done: '✓', active: '', failed: '✗', todo: '' };
const STEP_COLOR: Record<StepState, string> = { done: 'var(--green-text)', active: 'var(--accent-link)', failed: 'var(--red-text)', todo: 'var(--text-3)' };

export function VariantReview() {
  const { id } = useParams();
  const nav = useNavigate();
  const project = useProject(id);
  const v = project.data;
  const src = v?.derived_from ?? null;
  const srcParts = useLoad<Part[]>(() => (src ? api.parts(src.project, src.version) : Promise.resolve([])), [src?.project, src?.version]);

  const [sections, setSections] = useState<Section[]>([]);
  const [showRaw, setShowRaw] = useState(false);
  const [view, setView] = useState<ViewName>('port');
  const [viewNonce, setViewNonce] = useState(0); // re-applies a view even when it is already selected
  const [err, setErr] = useState<string | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [acting, setActing] = useState(false);
  const feedRef = useRef<HTMLDivElement>(null);
  const aborts = useRef<AbortController[]>([]);
  const watched = useRef(new Set<string>());
  useEffect(() => () => {
    // Stop polling on unmount. Reset too, so StrictMode's second mount watches again from scratch.
    aborts.current.forEach((a) => a.abort());
    aborts.current = [];
    watched.current.clear();
    setSections([]);
  }, []);

  const reload = project.reload;
  /** Follow a job: its log feeds the progress list; the model is re-read when it ends. */
  const watch = useCallback((jobId: string, label: string) => {
    if (watched.current.has(jobId)) return;
    watched.current.add(jobId);
    setSections((ss) => [...ss, { jobId, label, log: [], phase: 'running', startedAt: Date.now() }]);
    const ac = new AbortController();
    aborts.current.push(ac);
    const set = (patch: Partial<Section>) => setSections((ss) => ss.map((s) => (s.jobId === jobId ? { ...s, ...patch } : s)));
    pollJob(jobId, { signal: ac.signal, onUpdate: (s) => { if ('log' in s) set({ log: s.log }); } })
      .then(() => set({ phase: 'done', endedAt: Date.now() }), (e) => { if (!ac.signal.aborted) set({ phase: 'error', error: errorText(e), endedAt: Date.now() }); })
      .finally(() => { if (!ac.signal.aborted) void reload(); });
  }, [reload]);

  // The variant's own job: watch it from the start (a finished job replays its whole log).
  const draftJob = v?.draft?.job_id ?? null;
  useEffect(() => { if (draftJob) watch(draftJob, 'Making the variant'); }, [draftJob, watch]);

  const watching = sections.some((s) => s.phase === 'running');
  useTicker(watching);
  // While anything runs, re-read the project so each version appears on the right as soon as it builds.
  useEffect(() => {
    if (!watching) return;
    const t = setInterval(() => void reload(), 2500);
    return () => clearInterval(t);
  }, [watching, reload]);

  const lineCount = sections.reduce((n, s) => n + s.log.length, 0);
  useEffect(() => { feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight }); }, [lineCount, showRaw]);

  if (project.error) return <><TopBar /><div style={{ padding: 24 }}><ErrorNote error={project.error} retry={reload} /></div></>;
  if (!v) return <><TopBar /><div className="viewer"><Loading what="the variant" /></div></>;
  if (!src) return <><TopBar /><div style={{ padding: 24 }}>This project is not a variant. <Link to={projectPath(v)}>Open it</Link>.</div></>;

  const draft = v.draft ?? null;
  const lostJob = !!draft && sections.some((s) => s.jobId === draft.job_id && s.phase === 'error');
  const making = !!draft && RUNNING.includes(draft.stage) && !lostJob;
  const busy = watching || making || acting;
  const steps = variantSteps(draft, { brief: !!v.brief });
  const builtOk = !!v.version && v.build_report.ok !== false;
  const isShip = v.kind === 'ship';

  const askChanges = async (text: string) => {
    setErr(null);
    try {
      const r = await api.chat(v.id, text, 'build');
      watch(r.job_id, `Changes: ${text}`);
      void reload();
    } catch (e) { setErr(errorText(e)); }
  };

  const approve = async () => {
    setActing(true); setErr(null);
    try {
      const r = await api.approve(v.id);
      remember(v.kind, v.id);
      if (r.scene?.missing_stations.length) { remember('scene', r.scene.id); nav(paths.assembly(r.scene.id)); }
      else nav(isShip ? paths.shipBuild(v.id) : paths.crew(v.id));
    } catch (e) { setErr(errorText(e)); } finally { setActing(false); }
  };

  const discard = async () => {
    if (!confirmDiscard) { setConfirmDiscard(true); return; }
    setActing(true); setErr(null);
    try {
      await api.discard(v.id);
      nav(isShip ? paths.shipBuild(src.project) : paths.crew(src.project));
    } catch (e) { setErr(errorText(e)); } finally { setActing(false); setConfirmDiscard(false); }
  };

  const status = v.discarded ? <span className="chip grey">Discarded</span>
    : !draft ? <span className="chip green">Approved</span>
      : draft.stage === 'failed' ? <span className="chip red">Couldn't build</span>
        : making || watching ? <span className="chip blue">Working…</span>
          : <span className="chip amber">Waiting for your approval</span>;

  const now = Date.now();
  const secs = (s: Section) => Math.round(((s.endedAt ?? now) - s.startedAt) / 1000);

  const pane = (side: 'original' | 'variant') => {
    const original = side === 'original';
    const url = original ? modelUrl(src.project, src.version) : v.model_url;
    const title = original ? `Original · ${src.name} v${src.version}` : `Variant · ${v.name}${v.version ? ` v${v.version}` : ''}`;
    const summary = original ? '' : v.versions.find((x) => x.n === v.version)?.summary ?? '';
    return (
      <div style={{ background: 'var(--stage)', display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13, padding: '12px 16px 8px', minWidth: 0 }}>
          <span style={{ fontWeight: 500, whiteSpace: 'nowrap' }}>{title}</span>
          {!original && busy && <span className="spinner" aria-label="Working" />}
          <span className="muted" style={{ marginLeft: 'auto', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{summary}</span>
        </div>
        <div style={{ position: 'relative', flex: 1, minHeight: 0, display: 'flex' }}>
          {isShip
            ? <ShipViewer url={url} view={view} viewNonce={viewNonce} partPaths={(original ? srcParts.data : v.parts)?.map((p) => p.path) ?? []}
                emptyText={draft?.stage === 'failed' ? 'The copy did not build.' : 'Building the copy in Blender…'} />
            : url ? <CrewViewer urls={[url]} animation="idle" playing />
              : <div className="viewer"><div className="viewer-msg">{draft?.stage === 'failed' ? 'The copy did not build.' : 'Building the copy in Blender…'}</div></div>}
        </div>
      </div>
    );
  };

  return (
    <>
      <TopBar title={`Variant review: ${v.name}`} back={v.kind}>
        <div className="topbar-right" style={{ gap: 8 }}>
          {status}
          {draft && !v.discarded && <>
            <button className={`btn ${confirmDiscard ? 'primary' : ''}`} style={confirmDiscard ? { background: 'var(--red-bg)', color: 'var(--red-text)' } : undefined}
              disabled={busy} onClick={() => void discard()} onBlur={() => setConfirmDiscard(false)}>
              {confirmDiscard ? 'Click again to discard' : 'Discard'}
            </button>
            <button className="btn primary" disabled={busy || !builtOk} onClick={() => void approve()}
              title={busy ? 'Wait until it finishes' : !builtOk ? 'Nothing built to approve' : `Keep ${v.name} v${v.version} as a new ${isShip ? 'ship' : 'crew member'}`}>
              {`Approve${v.version ? ` v${v.version}` : ''}`}
            </button>
          </>}
          {!draft && !v.discarded && <Link className="btn primary" to={projectPath(v)}>Open in the workshop</Link>}
        </div>
      </TopBar>
      <div className="workspace">
        <aside className="side left" style={{ width: 340, minHeight: 0, overflow: 'hidden' }}>
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 8, borderBottom: '1px solid var(--border)', fontSize: 13 }}>
            {v.brief && <div className="muted" style={{ lineHeight: 1.45 }}>“{v.brief}”</div>}
            <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }} aria-label="Progress">
              {steps.map((s) => (
                <li key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 8, color: STEP_COLOR[s.state] }}>
                  <span style={{ width: 16, display: 'inline-flex', justifyContent: 'center' }}>
                    {s.state === 'active' && (s.key !== 'review' || busy) ? <span className="spinner" /> : STEP_MARK[s.state] || '○'}
                  </span>
                  <span style={{ color: s.state === 'todo' ? 'var(--text-3)' : 'var(--text)' }}>{s.label}</span>
                </li>
              ))}
            </ol>
            {draft?.error && <span className="bad" style={{ fontSize: 12, lineHeight: 1.45 }}>{draft.error}</span>}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', padding: '10px 16px 0', fontSize: 12 }}>
            <span className="faint">What's happening</span>
            <button className="btn sm muted" style={{ marginLeft: 'auto', height: 24 }} onClick={() => setShowRaw(!showRaw)}>{showRaw ? 'Show summary' : 'Show full log'}</button>
          </div>
          <div ref={feedRef} className="scroll" style={{ flex: 1, minHeight: 80, padding: '8px 16px 12px', display: 'flex', flexDirection: 'column', gap: 10, fontSize: 12, lineHeight: 1.5 }}>
            {!sections.length && <span className="faint">{draft ? 'Connecting…' : 'Nothing running.'}</span>}
            {sections.map((s) => {
              const lines = showRaw ? s.log : activityLines(s.log);
              return (
                <div key={s.jobId} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <span style={{ display: 'flex', gap: 8, color: 'var(--text-2)', fontWeight: 500 }}>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.label}</span>
                    {s.phase === 'running' && <span className="faint" style={{ marginLeft: 'auto', fontWeight: 400 }}>{secs(s)} s</span>}
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

          {draft && !v.discarded && (
            <div style={{ height: 'clamp(200px, 34%, 300px)', flexShrink: 0, display: 'flex', flexDirection: 'column', borderTop: '1px solid var(--border)' }}>
              <ChatPanel messages={v.messages} onSend={askChanges} busy={busy}
                label="Not quite right? Ask for changes before approving" placeholder="Move the gun mount 1 m further forward" />
            </div>
          )}
        </aside>

        <section className="stage">
          <div className="toolbar">
            <ViewButtons view={view} onView={(x) => { setView(x); setViewNonce((n) => n + 1); }} />
            <span className="faint toolbar-hint" style={{ marginLeft: 8 }}>
              Left: the original, unchanged. Right: the variant, updated as each version builds.
            </span>
          </div>
          <div style={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 1, background: 'var(--border)' }}>
            {pane('original')}
            {pane('variant')}
          </div>
          <VersionStrip project={v} />
          {err && <div style={{ padding: '0 16px 12px' }}><ErrorNote error={err} /></div>}
        </section>
      </div>
    </>
  );
}

function VersionStrip({ project }: { project: Project }) {
  if (!project.versions.length) return null;
  return (
    <div style={{ borderTop: '1px solid var(--border)', background: 'var(--panel)', padding: '10px 16px', display: 'flex', gap: 8, flexWrap: 'wrap', fontSize: 12 }}>
      <span className="faint" style={{ alignSelf: 'center' }}>Versions of the variant:</span>
      {project.versions.map((x) => (
        <span key={x.n} className={`chip ${x.n === project.version ? 'blue' : x.ok ? 'grey' : 'red'}`} title={x.summary}>
          v{x.n}{x.n === project.version ? ' (shown)' : ''}{x.ok ? '' : ' · failed'} · {x.summary.length > 48 ? `${x.summary.slice(0, 47)}…` : x.summary}
        </span>
      ))}
    </div>
  );
}
