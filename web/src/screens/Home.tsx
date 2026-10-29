// Home: a chat box that routes to the right workshop (Lester, 29 Oct). Routing opens a screen
// only after the user clicks "Open X"; when unsure it asks one question with choices. Asked to
// research a ship that has no project yet, it creates the ship and starts the research itself.
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, errorText } from '../api';
import { AttachmentList, Dot, ErrorNote, TopBar, filesOnlyText, useAttachPicker } from '../components/common';
import { remember, useHealth, useJobs, useProjects } from '../hooks';
import { briefFromCarried } from '../lib/designPlan';
import { clarifyOptions, paths, projectPath, startsNewResearch, targetFor } from '../lib/routing';
import { startDescribedShip } from './ProjectPicker';
import type { Attachment, Message } from '../types';
import { JobLines } from '../components/common';

interface Turn {
  who: 'me' | 'ai';
  text: string;
  attachments?: Attachment[];
  route?: { path: string; label: string };
  clarify?: boolean;
  pending?: boolean;
}

const toCarried = (turns: Turn[]): Message[] => turns.filter((t) => !t.pending).map((t) => ({
  who: t.who === 'me' ? 'You' : 'AI', text: t.text, at: new Date().toISOString(),
  ...(t.attachments?.length ? { attachments: t.attachments } : {}),
}));

export function Home() {
  const nav = useNavigate();
  const { health, error: healthErr } = useHealth();
  const projects = useProjects();
  const { jobs, dismiss } = useJobs();
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
      if (startsNewResearch(r.destination, r.project_id)) {
        // A ship with no project yet: create it and start the research here, then open Research on
        // the running turn, where the report is reviewed and the plan approved before anything is built.
        setThread((t) => [...t.slice(0, -1), { who: 'ai', text: 'Starting the research…', pending: true }]);
        const turns: Turn[] = [...thread, { who: 'me', text, attachments }];
        const brief = briefFromCarried(toCarried(turns));
        const { id, state } = await startDescribedShip(brief.text, brief.attachments);
        nav(paths.shipResearch(id), { state });
        return;
      }
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

  const h = health;
  const status = (on: boolean | undefined, label: string, off: string) => (
    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <Dot color={on === undefined ? '#8A919C' : on ? '#4CC38A' : '#E6B04A'} />{on === false ? off : label}
    </span>
  );
  const list = projects.data ?? [];
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

          <JobLines jobs={jobs} dismiss={dismiss} />

          {/* Lester, 29 Oct: "home page should only have the chat box and recent sections" (no Try one,
              Start new or Catalogue). New ships and crew start from the chat, or from their own pages. */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 12 }}>
            <span className="muted">Recent</span>
            {projects.error && <ErrorNote error={projects.error} retry={projects.reload} />}
            {!projects.error && recent.length === 0 && !projects.loading && <span className="faint">Nothing yet. Describe what you want to work on above.</span>}
            {recent.map((p) => (
              <Link key={p.id} to={projectPath(p)} onClick={() => remember(p.kind, p.id)}
                style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 14px', border: '1px solid var(--border)', borderRadius: 10, background: 'var(--panel-2)', color: 'var(--text)' }}>
                <span><span style={{ fontWeight: 500 }}>{p.name}</span> <span className="muted">{p.kind === 'ship' ? 'Ship' : 'Crew'}{p.version ? ` · v${p.version}` : ''}</span></span>
                {p.draft ? <span className="warn">Variant awaiting your approval</span>
                  : p.ok === false ? <span className="warn">Last build failed</span> : p.ok ? <span className="ok">Checks pass</span> : null}
              </Link>
            ))}
          </div>
        </div>
      </main>
    </>
  );
}
