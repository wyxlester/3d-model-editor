// Home: a chat box that routes to the right workshop (Lester, 29 Oct). Routing opens a screen
// only after the user clicks "Open X"; when unsure it asks one question with choices.
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, errorText } from '../api';
import { AttachmentList, Dot, ErrorNote, TopBar, filesOnlyText, useAttachPicker } from '../components/common';
import { recall, remember, useCatalog, useHealth, useJobs, useProjects } from '../hooks';
import { clarifyOptions, paths, targetFor } from '../lib/routing';
import type { Attachment, Message, ProjectBase, ProjectKind } from '../types';
import { JobLines } from '../components/common';

interface Turn {
  who: 'me' | 'ai';
  text: string;
  attachments?: Attachment[];
  route?: { path: string; label: string };
  clarify?: boolean;
  pending?: boolean;
}

const EXAMPLES = [
  'Research and build PCG 3-Series PC 303 as photographed in 2025',
  'The cabin door on PC 303 looks wrong',
  'A female coxswain in patrol working dress',
  'Animation: hand signal to the coxswain, slow ahead',
  'Put four crew on PC 303 coming alongside a suspect boat',
  'Get v4 ready for tomorrow’s customer meeting',
];

const NEW: { kind: ProjectKind; base: ProjectBase; label: string }[] = [
  { kind: 'ship', base: 'container', label: 'Container ship' },
  { kind: 'ship', base: 'sampan', label: 'Sampan' },
  { kind: 'ship', base: 'tanker', label: 'Tanker' },
  { kind: 'crew', base: 'male_default', label: 'Default male crew' },
];

const toCarried = (turns: Turn[]): Message[] => turns.filter((t) => !t.pending).map((t) => ({
  who: t.who === 'me' ? 'You' : 'AI', text: t.text, at: new Date().toISOString(),
  ...(t.attachments?.length ? { attachments: t.attachments } : {}),
}));

export function Home() {
  const nav = useNavigate();
  const { health, error: healthErr } = useHealth();
  const projects = useProjects();
  const catalog = useCatalog();
  const { jobs, run, dismiss } = useJobs();
  const [draft, setDraft] = useState('');
  const [thread, setThread] = useState<Turn[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [empty, setEmpty] = useState(false);
  // No project exists yet, so files are held on the server and carried into the workshop's chat.
  const files = useAttachPicker((f) => api.uploadHome(f));

  const send = async (typed: string) => {
    if (files.uploading) return;
    const attachments = files.ready;
    const text = typed.trim() || (attachments.length ? filesOnlyText(attachments.length) : '');
    if (!text) { setEmpty(true); return; }
    setDraft(''); setEmpty(false); setErr(null); files.clear();
    setThread((t) => [...t, { who: 'me', text, attachments }, { who: 'ai', text: 'Working out where this belongs…', pending: true }]);
    try {
      const r = await api.route(text, attachments.map((a) => a.name));
      const target = targetFor(r.destination, r.project_id);
      if (target) {
        // Sure of the workshop: open it straight away (Lester, 29 Oct), carrying the conversation and files.
        const turns: Turn[] = [...thread, { who: 'me', text, attachments }, { who: 'ai', text: r.reason }];
        nav(target.path, { state: { carried: toCarried(turns) } });
        return;
      }
      setThread((t) => [...t.slice(0, -1), { who: 'ai', text: r.reason || 'I’m not sure which this is. What do you want to change?', clarify: true }]);
    } catch (e) {
      setThread((t) => t.slice(0, -1));
      setErr(errorText(e));
    }
  };

  const carried = (): Message[] => toCarried(thread);

  const create = (kind: ProjectKind, base: ProjectBase, label: string) => {
    let id = '';
    void run(`Building a new ${label.toLowerCase()}`, async () => {
      const r = await api.createProject(kind, base);
      id = r.project.id;
      return { job_id: r.job_id };
    }, () => {
      remember(kind, id);
      nav(kind === 'ship' ? paths.shipBuild(id) : paths.crew(id));
    });
  };

  const h = health;
  const status = (on: boolean | undefined, label: string, off: string) => (
    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <Dot color={on === undefined ? '#8A919C' : on ? '#4CC38A' : '#E6B04A'} />{on === false ? off : label}
    </span>
  );
  const list = projects.data ?? [];
  const ships = list.filter((p) => p.kind === 'ship');
  const crew = list.filter((p) => p.kind === 'crew');
  const recent = [...list].sort((a, b) => String(b.updated ?? '').localeCompare(String(a.updated ?? ''))).slice(0, 4);

  return (
    <>
      <TopBar home>
        <div className="topbar-right" style={{ gap: 14 }}>
          {healthErr ? <span className="warn">Server not reachable</span> : <>
            {status(h?.blender, 'Blender connected', 'Blender not found')}
            {status(h?.mpfb, 'Human generator ready', 'Human generator missing')}
            {status(h?.ai, 'AI online', 'AI offline')}
            {status(h?.unreal, 'Unreal ready', 'Unreal not found')}
          </>}
        </div>
      </TopBar>
      <main className="scroll" style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '48px 24px 32px' }}>
        <div style={{ width: '100%', maxWidth: 860, display: 'flex', flexDirection: 'column', gap: 18 }}>
          <h1 style={{ margin: 0, fontSize: 28, fontWeight: 500, textAlign: 'center' }}>What do you want to work on?</h1>
          <p style={{ margin: 0, fontSize: 14, color: 'var(--text-2)', textAlign: 'center' }}>
            Describe it in your own words. I'll open the right workshop and carry this conversation with you.
          </p>

          {thread.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 14, lineHeight: 1.55 }}>
              {thread.map((m, i) => (
                <div key={i} className={`bubble ${m.who === 'me' ? 'me' : 'ai'}`} style={{ padding: m.who === 'me' ? '10px 14px' : '12px 14px', borderRadius: 12, maxWidth: m.who === 'me' ? '80%' : '88%' }}>
                  {m.attachments && <AttachmentList items={m.attachments} />}
                  <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>{m.pending && <span className="spinner" />}{m.text}</span>
                  {m.route && (
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10, alignItems: 'center' }}>
                      <button className="btn primary lg" style={{ height: 38 }} onClick={() => nav(m.route!.path, { state: { carried: carried() } })}>Open {m.route.label}</button>
                      <span className="faint" style={{ fontSize: 12 }}>Wrong place? Say so, or pick a workshop from the top bar.</span>
                    </div>
                  )}
                  {m.clarify && (
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
                      {clarifyOptions.map((o) => <button key={o.label} className="btn lg" style={{ height: 36 }} onClick={() => void send(o.text)}>{o.label}</button>)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          <div {...files.dropProps} style={{ background: 'var(--panel-2)', border: files.dragging ? '1px dashed var(--accent)' : '1px solid var(--border-2)', borderRadius: 14, padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
            <label htmlFor="brief" className="sr-only">Message the AI</label>
            <textarea
              id="brief" rows={3} value={draft} placeholder="Research and build PCG 3-Series PC 303 as photographed in 2025"
              onChange={(e) => { setDraft(e.target.value); setEmpty(false); }}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(draft); } }}
              onPaste={files.onPaste}
              style={{ width: '100%', resize: 'none', border: 0, outline: 'none', background: 'transparent', color: 'var(--text)', fontSize: 15, lineHeight: 1.5 }}
            />
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              {files.button}
              {files.picked.length === 0 && <span className="faint" style={{ fontSize: 12 }}>Attach photos, drawings or documents</span>}
              {empty && <span className="bad" style={{ fontSize: 12 }}>Type a request first.</span>}
              <button className="btn primary lg" style={{ marginLeft: 'auto', height: 38 }} onClick={() => void send(draft)} disabled={files.uploading}>Send</button>
            </div>
            {files.chips}
            {files.note}
          </div>
          <ErrorNote error={err} />

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <span className="faint" style={{ fontSize: 12 }}>Try one. Each goes to a different workshop.</span>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {EXAMPLES.map((t) => (
                <button key={t} onClick={() => void send(t)} style={{ minHeight: 36, padding: '6px 12px', borderRadius: 18, border: '1px solid var(--border-2)', background: 'transparent', color: 'var(--text-soft)', fontSize: 13, textAlign: 'left' }}>{t}</button>
              ))}
            </div>
          </div>

          <JobLines jobs={jobs} dismiss={dismiss} />

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16, marginTop: 12 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <span className="muted">Recent</span>
              {projects.error && <ErrorNote error={projects.error} retry={projects.reload} />}
              {!projects.error && recent.length === 0 && !projects.loading && <span className="faint">Nothing yet. Start one below or ask above.</span>}
              {recent.map((p) => (
                <Link key={p.id} to={p.kind === 'ship' ? paths.shipBuild(p.id) : paths.crew(p.id)} onClick={() => remember(p.kind, p.id)}
                  style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 14px', border: '1px solid var(--border)', borderRadius: 10, background: 'var(--panel-2)', color: 'var(--text)' }}>
                  <span><span style={{ fontWeight: 500 }}>{p.name}</span> <span className="muted">{p.kind === 'ship' ? 'Ship' : 'Crew'}{p.version ? ` · v${p.version}` : ''}</span></span>
                  {p.ok === false ? <span className="warn">Last build failed</span> : p.ok ? <span className="ok">Checks pass</span> : null}
                </Link>
              ))}
              <span className="muted" style={{ marginTop: 6 }}>Start new</span>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {NEW.map((n) => <button key={n.base} className="btn" onClick={() => create(n.kind, n.base, n.label)}>{n.label}</button>)}
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <span className="muted">Catalogue</span>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8, fontSize: 12 }}>
                {[
                  [catalog.data?.clothes.length, 'Clothes'],
                  [catalog.data?.animations.length, 'Animations'],
                  [catalog.data?.equipment.length, 'Equipment'],
                  [catalog.data?.ships.length, 'Ship types'],
                  [ships.length, 'Ships'],
                  [crew.length, 'Crew'],
                ].map(([n, l]) => (
                  <div key={String(l)} className="card" style={{ padding: 10, borderRadius: 8 }}>
                    <div style={{ fontSize: 18, fontWeight: 500 }}>{n ?? '–'}</div>
                    <div className="muted">{l}</div>
                  </div>
                ))}
              </div>
              {recall('scene') && <Link to={paths.review(recall('scene'))} className="faint" style={{ fontSize: 12 }}>Last scene: open Review</Link>}
            </div>
          </div>
        </div>
      </main>
    </>
  );
}
