// Ship workshop, Research mode (canvas: Research.dc.html). Renders project.research in the
// CONTRACT §12 shape (me3d.research/1), photographs and close-ups included (ResearchPhotos.tsx).
// The build plan is a canvas section the contract has no field for; it says so instead of showing invented data.
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { api, errorText, researchExportUrl } from '../api';
import { ChatPanel, ErrorNote, EvidenceChip, JobLines, Loading, PRIORITY, TopBar, useCarried } from '../components/common';
import { OtherWork, useProjectWork } from '../components/JobCenter';
import { ResearchMaterials, ResearchVersions } from '../components/ResearchRecord';
import { hexToLinear } from '../lib/describe';
import { claimUsed, linearToHex, planRows, type PlanRow } from '../lib/planValues';
import { remember, useJobs, useProject } from '../hooks';
import { awaitingApproval, buildTabLabel, canApprove, isCustomShip, planNotice, planState, planStatusLabel, summaryLine } from '../lib/designPlan';
import { partLabel } from '../lib/parts';
import { paths } from '../lib/routing';
import { boxText, photoStatus, provenanceRows } from '../lib/photos';
import { PhotoInspector, type InspectTarget } from '../components/PhotoInspector';
import { CloseupsTab, PhotosTab } from './ResearchPhotos';
import type { Attachment, Project, Research } from '../types';
import type { ResearchStart } from './ProjectPicker';

type Sec = 'summary' | 'photos' | 'platform' | 'closeups' | 'articulation' | 'plan' | 'questions' | 'sources' | 'read' | 'versions';
type Sel = { kind: 'claim'; i: number } | { kind: 'source'; i: number } | { kind: 'photo'; i: number } | { kind: 'closeup'; i: number } | { kind: 'question'; i: number } | null;

const LEGEND = ['Official', 'Photographed', 'Photo reading', 'Inference', 'Comparison only', 'Secondary lead', 'Unverified'];
const WEAK = (s: string) => /Unverified|Secondary lead|Inference/.test(s);

export function ShipResearch() {
  const { id } = useParams();
  const { data: project, setData: setProject, error, reload, loading } = useProject(id);
  const { jobs, run, dismiss, busy } = useJobs();
  const carried = useCarried();
  const nav = useNavigate();
  const start = (useLocation().state ?? null) as ResearchStart | null;
  // Work on this ship that this screen did not start (or no longer tracks, after leaving and coming
  // back) shows below the chat and reloads the page when it lands. The Ship page's first research
  // turn is picked up below with watchResearch, so it counts as this screen's own job, not twice.
  const work = useProjectWork(id, () => void reload(), jobs);
  // Any work on this ship, wherever it was started: the plan cannot be approved under a research turn
  // that may redraft it, and a research turn still running from before shows as one.
  const anyWork = busy || work.others.length > 0;
  const [sec, setSec] = useState<Sec>('summary');
  const [sel, setSel] = useState<Sel>(null);
  const excludedIds = project?.excluded_sources ?? [];
  const excluded: Record<string, boolean> = Object.fromEntries(excludedIds.map((s) => [s, true]));
  const [excludeErr, setExcludeErr] = useState<string | null>(null);
  const toggleSource = async (sid: string) => {
    setExcludeErr(null);
    const next = excludedIds.includes(sid) ? excludedIds.filter((x) => x !== sid) : [...excludedIds, sid];
    try { setProject((await api.setExcludedSources(id!, next)).project); } catch (e) { setExcludeErr(errorText(e)); }
  };
  const [planErr, setPlanErr] = useState<string | null>(null);
  const [inspect, setInspect] = useState<{ target: InspectTarget; order: string[] } | null>(null);
  useEffect(() => { if (id) remember('ship', id); }, [id]);

  // A research turn's job: reload when it ends (its result says plan_saved when the plan was redrafted;
  // the research itself may have changed either way, so the project is reloaded regardless).
  const watchResearch = (begin: () => Promise<{ job_id: string }>) => run<{ plan_saved?: boolean }>('Researching', async () => {
    const j = await begin();
    void reload();
    return j;
  }, () => void reload(), () => void reload());

  const ask = async (message: string, atts: Attachment[] = []) => {
    if (!id) return;
    setPlanErr(null);
    await watchResearch(() => api.chat(id, message, 'research', atts.map((a) => a.id)));
  };

  // The Ship page's "Research and build" already sent the first turn: watch that job here.
  const watched = useRef<string | null>(null);
  useEffect(() => {
    const job = start?.researchJob;
    if (!job || watched.current === job) return;
    watched.current = job;
    void watchResearch(() => Promise.resolve({ job_id: job }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [start?.researchJob]);

  const approve = async () => {
    if (!id) return;
    setPlanErr(null);
    let job: { job_id: string };
    try {
      job = await api.approvePlan(id);
    } catch (e) {
      setPlanErr(errorText(e)); // a 409 is the server's sentence, verbatim
      void reload();
      return;
    }
    void reload();
    await run('Building the approved design plan', () => Promise.resolve(job),
      () => { nav(paths.shipBuild(id)); }, () => void reload());
  };

  const r = project?.research ?? null;
  const custom = isCustomShip(project);
  const subject = r?.subject || project?.name || 'this vessel';
  const selPhoto = (pid: string) => { const i = (r?.photos ?? []).findIndex((p) => p.id === pid); if (i >= 0) setSel({ kind: 'photo', i }); };
  const selCrop = (cid: string) => { const i = (r?.closeups ?? []).findIndex((c) => c.id === cid); if (i >= 0) setSel({ kind: 'closeup', i }); };
  const selectedId = sel?.kind === 'photo' ? r?.photos?.[sel.i]?.id ?? null : sel?.kind === 'closeup' ? r?.closeups?.[sel.i]?.id ?? null : null;
  // Moving around inside the inspector moves the right-hand panel with it.
  const openInspect = (target: InspectTarget, order: string[]) => {
    if (target.kind === 'photo') selPhoto(target.id); else selCrop(target.id);
    setInspect({ target, order });
  };
  const inspectFromPanel = (target: InspectTarget) => openInspect(target, target.kind === 'photo' ? (r?.photos ?? []).map((p) => p.id) : (r?.closeups ?? []).map((c) => c.id));
  return (
    <>
      <TopBar title={project?.name} back="ship">
        <ModeSwitch id={id!} mode="research" buildLabel={buildTabLabel(project)} />
        <div className="topbar-right" style={{ gap: 8 }}>
          {r ? <span className="chip green" style={{ fontSize: 12, padding: '4px 10px' }}>Research assembled</span>
            : <span className="chip amber" style={{ fontSize: 12, padding: '4px 10px' }}>No research yet</span>}
          {r && (r.claims ?? []).some((c) => /Unverified/.test(c.status)) && (
            <span className="chip amber" style={{ fontSize: 12, padding: '4px 10px' }}>{(r.claims ?? []).filter((c) => /Unverified/.test(c.status)).length} unverified claims</span>
          )}
          {r && id && <>
            <span className="faint" style={{ fontSize: 12 }}>Report:</span>
            <a className="btn sm" href={researchExportUrl(id, 'pdf')} download>PDF</a>
            <a className="btn sm" href={researchExportUrl(id, 'pptx')} download>PowerPoint</a>
            <a className="btn sm" href={researchExportUrl(id, 'json')} download>JSON</a>
          </>}
          {!awaitingApproval(project) && <Link to={paths.shipBuild(id)} className="btn primary" style={{ height: 34, borderRadius: 8 }}>{custom ? 'Open Build' : 'Start building'}</Link>}
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
            working={work.chatWorking}
            label={custom ? 'Correct or add to the research' : 'Ask or direct the research'}
            placeholder={custom ? 'Make it 38 m and give the hull a dark grey stripe' : 'Find a true overhead photo'}
            empty={custom ? 'Describe the ship and attach photos. The AI researches it and drafts a design plan; nothing is built until you approve it.'
              : 'Tell the AI which vessel to research, and as of when. It checks for existing work first, then gathers sources for you to verify before anything is modelled.'}
            footer={<>
              {start?.note && <span className="warn" style={{ fontSize: 12 }}>{start.note}</span>}
              <ErrorNote error={start?.startError ?? null} />
              <JobLines jobs={jobs} dismiss={dismiss} />
              <OtherWork jobs={work.others} />
            </>}
          />
        </aside>

        <main className="scroll" style={{ flex: 1, minWidth: 0, background: 'var(--stage)', padding: '0 24px 22px', display: 'flex', flexDirection: 'column', gap: 18 }}>
          {loading && !project && <Loading what="the research" />}
          <ErrorNote error={error} retry={reload} />
          {project && !r && (custom ? <EmptyCustom researching={anyWork} /> : <EmptyResearch project={project} onRun={ask} busy={busy} />)}
          {project && r && (
            <>
              <SectionNav r={r} sec={sec} setSec={(s) => { setSec(s); setSel(null); }} />
              {sec === 'summary' && <Summary r={r} />}
              {sec === 'photos' && <PhotosTab r={r} subject={subject} selected={sel?.kind === 'photo' ? selectedId : null} onSelect={selPhoto} onInspect={openInspect} onAsk={(m) => void ask(m)} busy={anyWork} />}
              {sec === 'platform' && <Claims r={r} sel={sel} setSel={setSel} excluded={excludedIds} />}
              {sec === 'closeups' && <CloseupsTab r={r} subject={subject} selected={sel?.kind === 'closeup' ? selectedId : null} onSelect={selCrop} onInspect={openInspect} onAsk={(m) => void ask(m)} busy={anyWork} />}
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
              {sec === 'sources' && <>
                <ErrorNote error={excludeErr} />
                <Sources r={r} excluded={excluded} onExclude={(sid) => void toggleSource(sid)} />
              </>}
              {sec === 'read' && <ResearchMaterials pid={id!} />}
              {sec === 'versions' && <ResearchVersions pid={id!} />}
            </>
          )}
          {r && inspect && <PhotoInspector r={r} target={inspect.target} order={inspect.order}
            onOpen={(t) => openInspect(t, t.kind === inspect.target.kind ? inspect.order : t.kind === 'photo' ? (r.photos ?? []).map((p) => p.id) : (r.closeups ?? []).map((c) => c.id))}
            onClose={() => setInspect(null)} />}
        </main>

        {custom && project ? (
          <aside className="side right scroll" style={{ width: 400, padding: 16, gap: 12 }}>
            <PlanPanel project={project} busy={anyWork} error={planErr} onApprove={() => void approve()} onAsk={(m) => void ask(m)}
              onChange={setProject}
              detail={r && sel ? <DetailPanel r={r} sel={sel} onAsk={ask} busy={busy} onInspect={inspectFromPanel} /> : null} />
          </aside>
        ) : (
          <aside className="side right scroll" style={{ width: 300, padding: 16, gap: 14 }}>
            {r ? <DetailPanel r={r} sel={sel} onAsk={ask} busy={busy} onInspect={inspectFromPanel} /> : <span className="faint">Details of a selected claim, photograph, close-up or question show here.</span>}
          </aside>
        )}
      </div>
    </>
  );
}

/** `buildLabel` says "Build · waiting for your approval" on a custom ship before its first approved build. */
export function ModeSwitch({ id, mode, buildLabel = 'Build' }: { id: string; mode: 'research' | 'build'; buildLabel?: string }) {
  return (
    <div role="group" aria-label="Ship workshop mode" className="segmented" style={{ flexShrink: 0, whiteSpace: 'nowrap' }}>
      <Link to={paths.shipResearch(id)} className={mode === 'research' ? 'on' : ''}>Research</Link>
      <Link to={paths.shipBuild(id)} className={mode === 'build' ? 'on' : ''}
        style={buildLabel !== 'Build' && mode !== 'build' ? { color: 'var(--text-3)' } : undefined}>{buildLabel}</Link>
    </div>
  );
}

/**
 * The design plan (CONTRACT §5 `type: "custom"`): what will be built, in plain English, approved by
 * the user before anything builds (Lester, 29 Oct). Changes are made by chatting; the plan follows.
 */
function PlanPanel({ project, busy, error, onApprove, onAsk, onChange, detail }: {
  project: Project; busy: boolean; error: string | null; onApprove: () => void; onAsk: (m: string) => void;
  onChange: (p: Project) => void; detail: ReactNode;
}) {
  const plan = project.design_plan ?? null;
  const st = planState(project);
  const chip = planStatusLabel(st);
  const notice = planNotice(st);
  const lines = (plan?.summary ?? []).map(summaryLine);
  const redraft = st === 'none' ? 'Draft the design plan' : 'Redraft the design plan';
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontWeight: 500, fontSize: 15 }}>Design plan</span>
        <span className={`chip ${chip.tone}`}>{chip.text}</span>
      </div>
      <span className="faint" style={{ fontSize: 12 }}>What will be built. Change anything by chatting; the plan updates with the research.</span>
      {notice && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-start' }}>
          <span className={st === 'none' ? 'muted' : 'warn'} style={{ lineHeight: 1.5 }}>{notice}</span>
          <button className="btn" disabled={busy} onClick={() => onAsk(redraft)} title="Sends this to the AI in the research chat">{redraft}</button>
        </div>
      )}
      {plan?.build_error && <span className="bad" style={{ lineHeight: 1.5 }}>The approved plan did not build: {plan.build_error}</span>}
      {lines.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, lineHeight: 1.5, color: 'var(--text-soft)', opacity: st === 'redraft' || st === 'stale' ? 0.7 : 1 }}>
          {lines.map((l, i) => (
            <span key={i} style={l.assumed ? { color: 'var(--amber-text)' } : undefined}>
              {l.label && <b style={l.assumed ? undefined : { color: 'var(--text)', fontWeight: 600 }}>{l.label}</b>}{l.label ? ' · ' : ''}{l.text}
            </span>
          ))}
        </div>
      )}
      {plan?.spec && <PlanValues project={project} editable={st === 'draft' && !busy} onChange={onChange} />}
      {detail && <div style={{ display: 'flex', flexDirection: 'column', gap: 12, borderTop: '1px solid var(--border)', paddingTop: 12 }}>{detail}</div>}
      <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: 8, paddingTop: 12 }}>
        {error && <span className="bad" role="alert" style={{ fontSize: 12, lineHeight: 1.5 }}>{error}</span>}
        {st === 'approved' && project.plan_built ? (
          <Link to={paths.shipBuild(project.id)} className="btn lg" style={{ justifyContent: 'center' }}>Open Build</Link>
        ) : (
          <button className="btn primary lg" style={{ height: 44, justifyContent: 'center', fontSize: 14 }} disabled={!canApprove(project, busy)} onClick={onApprove}>
            {st === 'approved' ? 'Approved · building' : 'Approve and build'}
          </button>
        )}
        <span className="faint" style={{ fontSize: 12, textAlign: 'center' }}>Building starts only when you approve. A later change to the research needs approval again.</span>
      </div>
    </>
  );
}

function EmptyCustom({ researching }: { researching: boolean }) {
  return (
    <div style={{ margin: 'auto', maxWidth: 520, display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'flex-start' }}>
      <span className="eyebrow">Research</span>
      <h1 className="title">{researching ? 'Researching your ship…' : 'No research for this ship yet'}</h1>
      <p className="muted" style={{ margin: 0, lineHeight: 1.6 }}>
        {researching
          ? 'The AI is reading your description and photos and searching for references. The findings show here, and a design plan appears on the right for you to check.'
          : 'Describe the ship in the chat on the left, with photos if you have them. The AI researches it and drafts a design plan; nothing is built until you approve it.'}
      </p>
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
    ['photos', '01', 'Photographs', r.photos?.length ? String(r.photo_count ?? r.photos.length) : '', 'var(--text-2)'],
    ['platform', '02', 'Platform evidence', weak ? `${weak} open` : String(r.claims?.length ?? 0), weak ? 'var(--red-text)' : 'var(--text-2)'],
    ['closeups', '03', 'Close-ups', r.closeups?.length ? String(r.closeups.length) : '', 'var(--text-2)'],
    ['articulation', '04', 'Articulation', String(r.articulation?.length ?? 0), 'var(--text-2)'],
    ['plan', '05', 'Build plan', r.plan?.length ? String(r.plan.length) : '', 'var(--text-2)'],
    ['questions', '06', 'Open questions', String(r.open_questions?.length ?? 0), 'var(--amber-text)'],
    ['sources', '07', 'Sources', String(r.sources?.length ?? 0), 'var(--text-2)'],
    ['read', '08', 'What the AI read', '', ''],
    ['versions', '09', 'Versions', '', ''],
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

const sourcesText = (s: string[] | string | undefined) => (Array.isArray(s) ? s.join(', ') : s ?? '');

function Claims({ r, sel, setSel, excluded = [] }: { r: Research; sel: Sel; setSel: (s: Sel) => void; excluded?: string[] }) {
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
            <span style={{ fontWeight: 500 }} className={claimUsed(c, excluded) ? undefined : 'struck'}
              title={claimUsed(c, excluded) ? undefined : 'Not used: every source it cites is crossed out'}>
              {c.claim}{c.value != null && c.value !== '' ? `: ${String(c.value)}${c.unit ? ` ${c.unit}` : ''}` : ''}</span>
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
          <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }} className={excluded[s.id] ? 'struck' : undefined}>
            {s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a> : <span>{s.title}</span>}
            <span className="muted" style={{ fontSize: 12 }}>{s.what_it_proves}</span>
          </span>
          <span><EvidenceChip status={excluded[s.id] ? 'Excluded' : s.status} /></span>
          <button className="btn sm" onClick={() => onExclude(s.id, s.title)}
            title={excluded[s.id] ? 'Use this source again' : 'Cross it out: the AI rests nothing on it, and the design plan must be redrafted'}>{excluded[s.id] ? 'Use again' : 'Cross out'}</button>
        </div>
      ))}
    </div>
  );
}

function DetailPanel({ r, sel, onAsk, busy, onInspect }: { r: Research; sel: Sel; onAsk: (m: string) => void; busy: boolean; onInspect: (t: InspectTarget) => void }) {
  const [value, setValue] = useState('');
  let kind = 'Selected finding', title = 'Pick a claim, photograph, close-up or question', rows: [string, string][] = [], actions: [string, string][] = [];
  let answerFor: string | null = null;
  let inspect: { label: string; t: InspectTarget }[] = [];
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
      kind = 'Photograph';
      title = `${p.id} · ${p.title}`;
      rows = provenanceRows(p).filter((row) => !['Source', 'Image link', 'Observation'].includes(row.label)).map((row) => [row.label, row.value]);
      if (p.observation) rows.push(['Observation', p.observation]);
      inspect = [{ label: 'Inspect', t: { kind: 'photo', id: p.id } }];
      actions = photoStatus(p) === 'excluded'
        ? [['Use it again', `Use photo ${p.id} (${p.title}) again, and tell me what it changes.`]]
        : [['Mark as misleading', `Photo ${p.id} is misleading for this vessel: mark it excluded and recheck what depends on it.`], ['Find a better photo', `Find a better photo than ${p.id} for: ${p.modelling_use || p.observation || p.title}`]];
    }
  } else if (sel?.kind === 'closeup') {
    const c = r.closeups?.[sel.i];
    if (c) {
      kind = 'Close-up';
      title = `${c.id} · ${c.title}`;
      rows = [['From', c.photo], ['Shows', c.what_it_shows ?? ''], ['Supports', (c.claims ?? []).join('; ')], ['Crop', boxText(c.box)]];
      inspect = [{ label: 'Inspect', t: { kind: 'closeup', id: c.id } }, { label: `Show on ${c.photo}`, t: { kind: 'photo', id: c.photo, focus: c.id } }];
      actions = [['Recrop', `Recrop close-up ${c.id} (${c.title}) from ${c.photo}: the box misses part of it.`], ['Find a sharper view', `Find a sharper photo of: ${c.title}`]];
    }
  } else if (sel?.kind === 'question') {
    const q = r.open_questions?.[sel.i];
    if (q) { kind = 'Open question'; title = q.question; rows = [['Until answered', q.meanwhile ?? '']]; answerFor = q.question; }
  }
  return (
    <>
      <span className="faint" style={{ fontSize: 12 }}>{kind}</span>
      <span style={{ fontSize: 16, fontWeight: 500, lineHeight: 1.4 }}>{title}</span>
      {inspect.length > 0 && <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>{inspect.map((x) => <button key={x.label} className="btn sm" onClick={() => onInspect(x.t)}>{x.label}</button>)}</div>}
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

/** The draft plan's key settings, which the user may set before approving: their values win, survive
 * a redraft, and the build AI keeps them (CONTRACT §12, Review). Shapes and lists are changed by chatting. */
function PlanValues({ project, editable, onChange }: { project: Project; editable: boolean; onChange: (p: Project) => void }) {
  const plan = project.design_plan!;
  const planned = ((plan.spec ?? {}) as { params?: Record<string, unknown> }).params ?? {};
  const mine = plan.user_params ?? {};
  const [err, setErr] = useState<string | null>(null);
  const [more, setMore] = useState(false);
  const { key, more: rest } = planRows(planned, mine);
  if (!key.length && !rest.length) return null;
  const set = async (name: string, value: unknown) => {
    setErr(null);
    try { onChange((await api.setPlanValues(project.id, { [name]: value })).project); } catch (e) { setErr(errorText(e)); }
  };
  const field = (r: PlanRow) => <PlanField key={r.name} row={r} editable={editable} onSet={(v) => void set(r.name, v)} />;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
      <span style={{ fontWeight: 500 }}>Key settings</span>
      <span className="faint" style={{ fontSize: 12 }}>{editable ? 'Change a value to override the plan. Yours win, survive a redraft, and the AI keeps them.' : 'What this plan builds with.'}</span>
      {key.map(field)}
      {rest.length > 0 && <button className="btn sm muted" style={{ alignSelf: 'flex-start' }} onClick={() => setMore(!more)}>{more ? 'Fewer settings' : `More settings (${rest.length})`}</button>}
      {more && rest.map(field)}
      {err && <span className="bad" role="alert" style={{ fontSize: 12 }}>{err}</span>}
    </div>
  );
}

/** One plan setting: a length, number or colour, whose value it is, and a way back to the plan's. */
function PlanField({ row, editable, onSet }: { row: PlanRow; editable: boolean; onSet: (v: unknown) => void }) {
  const [text, setText] = useState(String(row.value ?? ''));
  useEffect(() => { setText(String(row.value ?? '')); }, [row.value]);
  const commit = () => {
    const n = Number(text);
    if (!text.trim() || Number.isNaN(n)) { setText(String(row.value ?? '')); return; }
    if (n !== row.value) onSet(n);
  };
  return (
    <div className="plan-field" title={row.doc}>
      <span className="plan-label">{row.label}{row.kind === 'length' ? ' (m)' : ''}</span>
      {row.kind === 'colour'
        ? <ColourInput value={Array.isArray(row.value) ? (row.value as number[]) : [0.5, 0.5, 0.5]} disabled={!editable} onSet={onSet} label={row.label} />
        : <input className="field" type="number" step="any" value={text} disabled={!editable} aria-label={row.label}
            onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') commit(); }} />}
      <span className={`chip ${row.from === 'yours' ? 'blue' : 'grey'}`}>{row.from === 'yours' ? 'Yours' : 'Plan'}</span>
      {editable && row.from === 'yours'
        ? <button className="btn sm muted" title="Back to the plan's value" aria-label={`Reset ${row.label}`} onClick={() => onSet(null)}>↺</button>
        : <span />}
    </div>
  );
}

/** A colour picker over a Blender (linear) colour; saved a moment after the user stops moving it. */
function ColourInput({ value, disabled, onSet, label }: { value: number[]; disabled: boolean; onSet: (v: unknown) => void; label: string }) {
  const [hex, setHex] = useState(linearToHex(value));
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => { setHex(linearToHex(value)); }, [value]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return (
    <input type="color" className="colour-input" value={hex} disabled={disabled} aria-label={label}
      onChange={(e) => {
        const h = e.target.value;
        setHex(h);
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => onSet(hexToLinear(h)), 450);
      }} />
  );
}
