// Ship workshop, Research mode (canvas: Research.dc.html). Renders project.research in the
// CONTRACT §12 shape (me3d.research/1). Canvas sections the contract has no field for
// (photographs, close-ups, build plan) say so instead of showing invented data.
import { useEffect, useState, type ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api';
import { ChatPanel, ErrorNote, EvidenceChip, JobLines, Loading, PRIORITY, TopBar, useCarried } from '../components/common';
import { remember, useJobs, useProject } from '../hooks';
import { partLabel } from '../lib/parts';
import { paths } from '../lib/routing';
import type { Attachment, Project, Research } from '../types';

type Sec = 'summary' | 'photos' | 'platform' | 'closeups' | 'articulation' | 'plan' | 'questions' | 'sources';
type Sel = { kind: 'claim'; i: number } | { kind: 'source'; i: number } | { kind: 'photo'; i: number } | { kind: 'question'; i: number } | null;

const LEGEND = ['Official', 'Photographed', 'Photo reading', 'Inference', 'Comparison only', 'Secondary lead', 'Unverified'];
const WEAK = (s: string) => /Unverified|Secondary lead|Inference/.test(s);

export function ShipResearch() {
  const { id } = useParams();
  const { data: project, error, reload, loading } = useProject(id);
  const { jobs, run, dismiss, busy } = useJobs();
  const carried = useCarried();
  const [sec, setSec] = useState<Sec>('summary');
  const [sel, setSel] = useState<Sel>(null);
  const [excluded, setExcluded] = useState<Record<string, boolean>>({});
  useEffect(() => { if (id) remember('ship', id); }, [id]);

  const ask = async (message: string, atts: Attachment[] = []) => {
    if (!id) return;
    await run('Researching', async () => {
      const r = await api.chat(id, message, 'research', atts.map((a) => a.id));
      void reload();
      return r;
    }, () => void reload(), () => void reload());
  };

  const r = project?.research ?? null;
  return (
    <>
      <TopBar title={project?.name}>
        <ModeSwitch id={id!} mode="research" />
        <div className="topbar-right" style={{ gap: 8 }}>
          {r ? <span className="chip green" style={{ fontSize: 12, padding: '4px 10px' }}>Research assembled</span>
            : <span className="chip amber" style={{ fontSize: 12, padding: '4px 10px' }}>No research yet</span>}
          {r && (r.claims ?? []).some((c) => /Unverified/.test(c.status)) && (
            <span className="chip amber" style={{ fontSize: 12, padding: '4px 10px' }}>{(r.claims ?? []).filter((c) => /Unverified/.test(c.status)).length} unverified claims</span>
          )}
          <Link to={paths.shipBuild(id)} className="btn primary" style={{ height: 34, borderRadius: 8 }}>Start building</Link>
        </div>
      </TopBar>
      <div className="workspace">
        <aside className="side left" style={{ width: 320 }}>
          <ChatPanel
            messages={project?.messages ?? []}
            carried={carried}
            onSend={ask}
            projectId={id}
            busy={busy}
            label="Ask or direct the research"
            placeholder="Find a true overhead photo"
            empty="Tell the AI which vessel to research, and as of when. It checks for existing work first, then gathers sources for you to verify before anything is modelled."
            footer={<JobLines jobs={jobs} dismiss={dismiss} />}
          />
        </aside>

        <main className="scroll" style={{ flex: 1, minWidth: 0, background: 'var(--stage)', padding: '0 24px 22px', display: 'flex', flexDirection: 'column', gap: 18 }}>
          {loading && !project && <Loading what="the research" />}
          <ErrorNote error={error} retry={reload} />
          {project && !r && <EmptyResearch project={project} onRun={ask} busy={busy} />}
          {project && r && (
            <>
              <SectionNav r={r} sec={sec} setSec={(s) => { setSec(s); setSel(null); }} />
              {sec === 'summary' && <Summary r={r} />}
              {sec === 'photos' && <Photos r={r} sel={sel} setSel={setSel} />}
              {sec === 'platform' && <Claims r={r} sel={sel} setSel={setSel} />}
              {sec === 'closeups' && <NotInContract what="Close-ups (crops of the recorded photographs)" have={(r.closeups ?? []).length > 0}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 12 }}>
                  {(r.closeups ?? []).map((c, i) => (
                    <div key={i} className="card" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      <span style={{ fontWeight: 500 }}>{c.title}</span><span className="muted">{c.text}</span><span className="faint" style={{ fontSize: 12 }}>{c.photo}{c.hull ? ` · ${c.hull}` : ''}</span>
                    </div>
                  ))}
                </div>
              </NotInContract>}
              {sec === 'articulation' && <Articulation r={r} id={id!} />}
              {sec === 'plan' && <NotInContract what="The build plan with acceptance checks" have={(r.plan ?? []).length > 0}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12 }}>
                  {(r.plan ?? []).map((p) => (
                    <div key={p.n} className="card" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      <span><span className="mono" style={{ color: 'var(--accent)' }}>{p.n}</span> <span style={{ fontWeight: 500, fontSize: 15 }}>{p.title}</span></span>
                      <span className="muted">{p.what}</span>
                      <span style={{ borderTop: '1px solid var(--border)', paddingTop: 8 }}><span style={{ color: 'var(--green-text)' }}>Accept:</span> {p.accept}</span>
                    </div>
                  ))}
                </div>
              </NotInContract>}
              {sec === 'questions' && <Questions r={r} onAsk={ask} sel={sel} setSel={setSel} />}
              {sec === 'sources' && <Sources r={r} excluded={excluded} onExclude={(sid, title) => {
                const on = !excluded[sid];
                setExcluded({ ...excluded, [sid]: on });
                void ask(on ? `Exclude source ${sid} (${title}) from the research, and tell me which claims and parts that affects.` : `Use source ${sid} (${title}) again.`);
              }} />}
            </>
          )}
        </main>

        <aside className="side right scroll" style={{ width: 300, padding: 16, gap: 14 }}>
          {r ? <DetailPanel r={r} sel={sel} onAsk={ask} busy={busy} /> : <span className="faint">Details of a selected claim, source or question show here.</span>}
        </aside>
      </div>
    </>
  );
}

export function ModeSwitch({ id, mode }: { id: string; mode: 'research' | 'build' }) {
  return (
    <div role="group" aria-label="Ship workshop mode" className="segmented">
      <Link to={paths.shipResearch(id)} className={mode === 'research' ? 'on' : ''}>Research</Link>
      <Link to={paths.shipBuild(id)} className={mode === 'build' ? 'on' : ''}>Build</Link>
    </div>
  );
}

function EmptyResearch({ project, onRun, busy }: { project: Project; onRun: (m: string) => void; busy: boolean }) {
  const prompt = `Research ${project.name} to the standard of the PCG 3-Series dossier: check for existing work first, then official sources and photographs, and label every claim by how we know it.`;
  return (
    <div style={{ margin: 'auto', maxWidth: 520, display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'flex-start' }}>
      <span className="eyebrow">Research</span>
      <h1 className="title">No research for this ship yet</h1>
      <p className="muted" style={{ margin: 0, lineHeight: 1.6 }}>
        Research comes before building: the AI checks for existing work, then gathers sources and photographs, and labels every claim
        Official, Photographed, Inference or Unverified. You can also direct it from the chat on the left.
      </p>
      <button className="btn primary lg" disabled={busy} onClick={() => onRun(prompt)}>Run research</button>
      <span className="faint" style={{ fontSize: 12 }}>Sends: “{prompt}”</span>
    </div>
  );
}

function SectionNav({ r, sec, setSec }: { r: Research; sec: Sec; setSec: (s: Sec) => void }) {
  const weak = (r.claims ?? []).filter((c) => WEAK(c.status)).length;
  const secs: [Sec, string, string, string, string][] = [
    ['summary', '00', 'Summary', '', ''],
    ['photos', '01', 'Photographs', r.photos?.length ? String(r.photos.length) : '', 'var(--text-2)'],
    ['platform', '02', 'Platform evidence', weak ? `${weak} open` : String(r.claims?.length ?? 0), weak ? 'var(--red-text)' : 'var(--text-2)'],
    ['closeups', '03', 'Close-ups', r.closeups?.length ? String(r.closeups.length) : '', 'var(--text-2)'],
    ['articulation', '04', 'Articulation', String(r.articulation?.length ?? 0), 'var(--text-2)'],
    ['plan', '05', 'Build plan', r.plan?.length ? String(r.plan.length) : '', 'var(--text-2)'],
    ['questions', '06', 'Open questions', String(r.open_questions?.length ?? 0), 'var(--amber-text)'],
    ['sources', '07', 'Sources', String(r.sources?.length ?? 0), 'var(--text-2)'],
  ];
  return (
    <nav aria-label="Research sections" style={{ position: 'sticky', top: 0, zIndex: 1, display: 'flex', flexWrap: 'wrap', gap: 2, padding: '10px 0', background: 'var(--stage)', borderBottom: '1px solid var(--border)' }}>
      {secs.map(([id, n, label, flag, fc]) => (
        <button key={id} onClick={() => setSec(id)} style={{ display: 'flex', alignItems: 'center', gap: 6, height: 34, padding: '0 10px', borderRadius: 6, border: 0, background: sec === id ? 'var(--accent-bg)' : 'transparent', color: sec === id ? 'var(--accent-text)' : 'var(--text)', fontSize: 13 }}>
          <span className="mono faint" style={{ fontSize: 11 }}>{n}</span><span>{label}</span><span style={{ fontSize: 11, color: fc }}>{flag}</span>
        </button>
      ))}
    </nav>
  );
}

function Head({ n, eyebrow, title, sub }: { n: string; eyebrow: string; title: string; sub?: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span className="eyebrow">{n} · {eyebrow}</span>
      <h1 className="title">{title}</h1>
      {sub && <p className="muted" style={{ margin: 0 }}>{sub}</p>}
    </div>
  );
}

function Summary({ r }: { r: Research }) {
  const claims = r.claims ?? [];
  const unverified = claims.filter((c) => /Unverified/.test(c.status)).length;
  const stats: [string, string, boolean][] = [
    [String(r.sources?.length ?? 0), 'sources, each labelled with what it may prove', false],
    [String(claims.length), 'claims about the vessel', false],
    [String(unverified), 'claims still unverified', unverified > 0],
    [String(r.open_questions?.length ?? 0), 'open questions that need a person, not a search', false],
  ];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, paddingTop: 12 }}>
        <span className="eyebrow">00 · Summary</span>
        <h1 style={{ margin: 0, fontSize: 26, fontWeight: 500 }}>{r.summary?.verdict ?? 'No verdict recorded.'}</h1>
        {r.subject && <p className="muted" style={{ margin: 0, fontSize: 14, lineHeight: 1.6, maxWidth: 820 }}>{r.subject}</p>}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 12 }}>
        {stats.map(([v, l, alert]) => (
          <div key={l} className="card"><div style={{ fontSize: 26, fontWeight: 500, color: alert ? 'var(--red-text)' : undefined }}>{v}</div><div className="muted" style={{ fontSize: 12 }}>{l}</div></div>
        ))}
      </div>
      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 6, lineHeight: 1.55 }}>
        <span style={{ fontWeight: 500 }}>Checked before starting</span>
        <span className="muted">{r.summary?.existing_work ?? 'The research does not say whether existing work was checked.'}</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <span className="faint" style={{ fontSize: 12 }}>How each claim is labelled across this research</span>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>{LEGEND.map((l) => <EvidenceChip key={l} status={l} />)}</div>
      </div>
    </div>
  );
}

function NotInContract({ what, have, children }: { what: string; have: boolean; children: ReactNode }) {
  if (have) return <div style={{ paddingTop: 12 }}>{children}</div>;
  return (
    <div className="card" style={{ marginTop: 16, color: 'var(--text-2)', lineHeight: 1.6 }}>
      {what}: the research format (me3d.research/1) has no place for this yet, so there is nothing to show. The canvas has this section; it needs a contract change first.
    </div>
  );
}

function Photos({ r, sel, setSel }: { r: Research; sel: Sel; setSel: (s: Sel) => void }) {
  const [filter, setFilter] = useState('All');
  const photos = r.photos ?? [];
  if (!photos.length) return <NotInContract what="Photographs with provenance" have={false}>{null}</NotInContract>;
  const views = ['All', 'Bow', 'Stern', 'Port', 'Starboard', 'Top', 'Interior', 'Details'];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Head n="01" eyebrow="Reference atlas" title="Photographs you can inspect" sub="Each entry records the original source, credit, date, hull number and what it is useful for. Click one to see its record." />
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {views.map((v) => <button key={v} className={`btn pill ${filter === v ? 'selected' : 'muted'}`} onClick={() => setFilter(v)}>{v}</button>)}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0, 1fr))', gap: 10 }}>
        {photos.map((p, i) => ({ p, i })).filter(({ p }) => filter === 'All' || (p.views ?? []).some((v) => v.toLowerCase() === filter.toLowerCase())).map(({ p, i }) => {
          const on = sel?.kind === 'photo' && sel.i === i;
          return (
            <button key={p.id} onClick={() => setSel({ kind: 'photo', i })} style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: 8, borderRadius: 8, color: 'var(--text)', border: on ? '2px solid var(--accent)' : '1px solid var(--border)', background: on ? 'var(--accent-bg)' : 'var(--panel-2)' }}>
              <span className="mono faint" style={{ height: 74, borderRadius: 6, background: 'var(--bubble)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, backgroundImage: p.image_url ? `url(${p.image_url})` : undefined, backgroundSize: 'cover', backgroundPosition: 'center' }}>{p.image_url ? '' : p.id}</span>
              <span style={{ fontSize: 12, lineHeight: 1.35, textAlign: 'left' }}>{p.title}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

const sourcesText = (s: string[] | string | undefined) => (Array.isArray(s) ? s.join(', ') : s ?? '');

function Claims({ r, sel, setSel }: { r: Research; sel: Sel; setSel: (s: Sel) => void }) {
  const cols = 'minmax(0, 1.1fr) 130px 120px minmax(0, 1.2fr)';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Head n="02" eyebrow="Platform evidence" title="What the evidence establishes" sub="Every finding is labelled by how we know it, and says what it means for the model. Click a row to see its sources." />
      <div style={{ display: 'grid', gridTemplateColumns: cols, gap: 12, padding: '8px 12px', fontSize: 12, color: 'var(--text-3)', borderBottom: '1px solid var(--border)' }}>
        <span>Claim</span><span>Status</span><span>Sources</span><span>Consequence for the model</span>
      </div>
      {(r.claims ?? []).map((c, i) => {
        const on = sel?.kind === 'claim' && sel.i === i;
        return (
          <button key={i} onClick={() => setSel({ kind: 'claim', i })} style={{ display: 'grid', gridTemplateColumns: cols, gap: 12, padding: 12, border: 0, borderBottom: '1px solid var(--bubble)', fontSize: 13, lineHeight: 1.5, textAlign: 'left', color: 'var(--text)', background: on ? 'var(--accent-bg)' : 'transparent' }}>
            <span style={{ fontWeight: 500 }}>{c.claim}</span>
            <span style={{ justifySelf: 'start' }}><EvidenceChip status={c.status} /></span>
            <span className="mono muted" style={{ fontSize: 12 }}>{sourcesText(c.sources) || '—'}</span>
            <span className="muted">{c.consequence_for_model}</span>
          </button>
        );
      })}
      {!(r.claims ?? []).length && <span className="faint">No claims recorded.</span>}
    </div>
  );
}

function Articulation({ r, id }: { r: Research; id: string }) {
  const cols = '220px 120px minmax(0, 1fr) 110px';
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Head n="04" eyebrow="Articulation proposal" title="Rig only the mechanisms the references support" sub="Priority P0 to P2. What must not move is recorded too." />
      <div style={{ display: 'grid', gridTemplateColumns: cols, gap: 12, padding: '8px 12px', fontSize: 12, color: 'var(--text-3)', borderBottom: '1px solid var(--border)' }}>
        <span>Part</span><span>Priority</span><span>Motion</span><span />
      </div>
      {(r.articulation ?? []).map((a, i) => (
        <div key={i} style={{ display: 'grid', gridTemplateColumns: cols, gap: 12, padding: '10px 12px', borderBottom: '1px solid var(--bubble)', alignItems: 'center' }}>
          <span style={{ fontWeight: 500 }}>{a.part.includes('__') ? partLabel(a.part) : a.part}</span>
          <span><span className={`chip ${PRIORITY[a.priority] ?? 'grey'}`}>{a.priority}</span></span>
          <span className="muted">{a.motion}</span>
          {a.part.includes('__') ? <Link to={paths.shipBuild(id, a.part)} style={{ fontSize: 12 }}>Show in Build</Link> : <span />}
        </div>
      ))}
    </div>
  );
}

function Questions({ r, onAsk, sel, setSel }: { r: Research; onAsk: (m: string) => void; sel: Sel; setSel: (s: Sel) => void }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Head n="06" eyebrow="Open questions" title="What no public source answered" sub="Each one stays a labelled assumption in the model until answered. Answer one yourself, or ask the AI to search again." />
      {(r.open_questions ?? []).map((q, i) => (
        <div key={i} className="card" style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: 12, alignItems: 'center', borderColor: sel?.kind === 'question' && sel.i === i ? 'var(--accent)' : undefined }}>
          <span style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontWeight: 500 }}>{q.question}</span>
            {q.meanwhile && <span className="muted">Until answered: {q.meanwhile}</span>}
          </span>
          <span style={{ display: 'flex', gap: 8 }}>
            <button className="btn" style={{ height: 34, borderRadius: 8 }} onClick={() => setSel({ kind: 'question', i })}>I know this</button>
            <button className="btn" style={{ height: 34, borderRadius: 8 }} onClick={() => onAsk(`Search again for: ${q.question}`)}>Search again</button>
          </span>
        </div>
      ))}
    </div>
  );
}

function Sources({ r, excluded, onExclude }: { r: Research; excluded: Record<string, boolean>; onExclude: (id: string, title: string) => void }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Head n="07" eyebrow="Sources" title="Every source, and what it was allowed to prove" sub="Comparison-only sources explain a family of equipment. They never identify what is installed on this craft." />
      {(r.sources ?? []).map((s) => (
        <div key={s.id} style={{ display: 'grid', gridTemplateColumns: '44px minmax(0, 1fr) 150px auto', gap: 12, alignItems: 'center', padding: '10px 12px', borderBottom: '1px solid var(--bubble)' }}>
          <span className="mono faint">{s.id}</span>
          <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a> : <span>{s.title}</span>}
            <span className="muted" style={{ fontSize: 12 }}>{s.what_it_proves}</span>
          </span>
          <span><EvidenceChip status={excluded[s.id] ? 'Excluded' : s.status} /></span>
          <button className="btn sm" onClick={() => onExclude(s.id, s.title)} title="Sends the request to the AI in the research chat">{excluded[s.id] ? 'Use again' : 'Exclude'}</button>
        </div>
      ))}
    </div>
  );
}

function DetailPanel({ r, sel, onAsk, busy }: { r: Research; sel: Sel; onAsk: (m: string) => void; busy: boolean }) {
  const [value, setValue] = useState('');
  let kind = 'Selected finding', title = 'Pick a claim, source or question', rows: [string, string][] = [], actions: [string, string][] = [];
  let answerFor: string | null = null;
  if (sel?.kind === 'claim') {
    const c = r.claims?.[sel.i];
    if (c) {
      title = c.claim;
      rows = [['Status', c.status], ['Sources', sourcesText(c.sources) || 'None'], ['Consequence', c.consequence_for_model ?? '']];
      if (WEAK(c.status)) { answerFor = c.claim; actions = [['Search again', `Search again for evidence on: ${c.claim}`]]; }
      else actions = [['Challenge this', `Challenge this claim and recheck its sources: ${c.claim}`], ['Rebuild affected parts', `Rebuild the parts affected by: ${c.claim}`]];
    }
  } else if (sel?.kind === 'photo') {
    const p = r.photos?.[sel.i];
    if (p) {
      kind = 'Photo record';
      title = `${p.id} · ${p.title}`;
      rows = [['Hull', p.vessel ?? ''], ['Views', (p.views ?? []).join(', ')], ['Credit', p.credit ?? ''], ['Date', p.date ?? ''],
        ['Resolution', p.width ? `${p.width} × ${p.height} px` : ''], ['Useful for', p.observation ?? ''], ['Accessed', p.accessed ?? ''], ['SHA-256', p.sha256 ?? 'not recorded'], ['Rights', p.rights ?? '']];
      actions = [['Mark as misleading', `Photo ${p.id} is misleading for this hull: recheck what depends on it.`], ['Find a better photo', `Find a better photo than ${p.id} for: ${p.observation ?? p.title}`]];
    }
  } else if (sel?.kind === 'question') {
    const q = r.open_questions?.[sel.i];
    if (q) { kind = 'Open question'; title = q.question; rows = [['Until answered', q.meanwhile ?? '']]; answerFor = q.question; }
  }
  return (
    <>
      <span className="faint" style={{ fontSize: 12 }}>{kind}</span>
      <span style={{ fontSize: 16, fontWeight: 500, lineHeight: 1.4 }}>{title}</span>
      {rows.length > 0 && <div className="kv" style={{ gridTemplateColumns: '96px minmax(0, 1fr)', lineHeight: 1.45 }}>{rows.flatMap(([k, v]) => [<span key={k}>{k}</span>, <span key={k + 'v'}>{v || '—'}</span>])}</div>}
      {answerFor && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
          <label htmlFor="known" className="faint" style={{ fontSize: 12 }}>I know this value</label>
          <input id="known" className="field" value={value} onChange={(e) => setValue(e.target.value)} placeholder="e.g. 13.8 m, from the GA drawing" />
          <button className="btn" disabled={!value.trim() || busy} onClick={() => { onAsk(`For “${answerFor}”, the answer is: ${value}. Record it with me as the source and update what depends on it.`); setValue(''); }}>Tell the AI</button>
        </div>
      )}
      {actions.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
          <span className="faint" style={{ fontSize: 12 }}>If the model looks wrong here, fix the cause</span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {actions.map(([l, m]) => <button key={l} className="btn" style={{ height: 34, borderRadius: 8, fontSize: 12 }} disabled={busy} onClick={() => onAsk(m)}>{l}</button>)}
          </div>
        </div>
      )}
    </>
  );
}
