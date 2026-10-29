// /ship and /crew without a project: pick one or start a new one. The Ship page also describes a
// new ship of any type (Lester, 29 Oct: "research first, wait for user confirmation to build").
// Each project shows a picture of its model; the selected one is previewed live beside the list,
// with Open and Make a variant. Order on the Ship page (Lester, 29 Oct): the Describe card, then
// the list with its preview, then the bases.
import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { api, errorText, modelUrl } from '../api';
import { ErrorNote, JobLines, TopBar, useAttachPicker } from '../components/common';
import { useVariantDialog } from '../components/VariantDialog';
import { useJobCenter } from '../components/JobCenter';
import { activeFor } from '../lib/jobcenter';
import { recall, remember, useCatalog, useJobs, useProjects } from '../hooks';
import { briefFromCarried } from '../lib/designPlan';
import type { ViewName } from '../lib/frames';
import { paths, projectPath } from '../lib/routing';
import type { Message, ProjectBase, ProjectKind, ProjectSummary } from '../types';
import { CrewViewer, ShipViewer } from '../viewer/Viewer';
import { useThumbnail } from '../viewer/thumbnail';

/** Ships are pictured from the bow quarter, people from the front. */
const PICTURE_VIEW: Record<ProjectKind, ViewName> = { ship: 'orbit', crew: 'bow' };

const urlOf = (p: ProjectSummary) => (p.version ? modelUrl(p.id, p.version) : null);

/**
 * Where a project opens from the list. A custom ship opens in Research: its design plan lives there
 * and nothing builds without it. Everything else goes where projectPath says, so a variant still
 * awaiting approval opens its review (that check comes first, custom ship or not).
 */
/** A custom ship still waiting for its plan: no variants yet (Lester, 29 Oct). */
const placeholder = (p: ProjectSummary) => p.kind === 'ship' && p.base === 'custom' && !p.plan_built;
const openPath = (p: ProjectSummary) => (!p.draft && p.kind === 'ship' && p.base === 'custom' ? paths.shipResearch(p.id) : projectPath(p));

export function ProjectPicker({ kind }: { kind: ProjectKind }) {
  const nav = useNavigate();
  // Home's conversation (and files) passes through this picker to the project chosen.
  const carried = useLocation().state as { carried?: Message[] } | null;
  const projects = useProjects();
  const catalog = useCatalog();
  const { jobs, run, dismiss, busy } = useJobs();
  const variant = useVariantDialog();
  const mine = (projects.data ?? []).filter((p) => p.kind === kind);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Models being changed in the background: marked in the list, and re-read when a change finishes
  // so the picture and preview show the new version.
  const center = useJobCenter();
  const finishedHere = mine.reduce((n, p) => n + (center.finished[p.id] ?? 0), 0);
  const reloadProjects = projects.reload;
  useEffect(() => { if (finishedHere) void reloadProjects(); }, [finishedHere, reloadProjects]);
  const bases: { base: ProjectBase; name: string; description?: string }[] = kind === 'ship'
    ? (catalog.data?.ships.filter((s) => s.type !== 'custom').map((s) => ({ base: s.type as ProjectBase, name: s.name, description: s.description })) ?? [])
    : (catalog.data?.crew.map((c) => ({ base: c.id as ProjectBase, name: c.name })) ?? [{ base: 'male_default', name: 'Default male crew' }]);

  // Start on the one opened last, else the first.
  const ids = mine.map((p) => p.id).join();
  useEffect(() => {
    if (!ids) return;
    setSelectedId((cur) => {
      const list = ids.split(',');
      if (cur && list.includes(cur)) return cur;
      const last = recall(kind);
      return last && list.includes(last) ? last : list[0];
    });
  }, [ids, kind]);
  const selected = mine.find((p) => p.id === selectedId) ?? null;

  const create = (base: ProjectBase, name: string) => {
    let id = '';
    void run(`Building a new ${name.toLowerCase()}`, async () => {
      const r = await api.createProject(kind, base);
      id = r.project.id;
      return { job_id: r.job_id };
    }, () => { remember(kind, id); nav(kind === 'ship' ? paths.shipBuild(id) : paths.crew(id), { state: carried }); });
  };
  const go = (p: ProjectSummary) => { remember(kind, p.id); nav(openPath(p), { state: carried }); };

  return (
    <>
      <TopBar />
      <main className="scroll" style={{ flex: 1, padding: '32px 24px', display: 'flex', justifyContent: 'center' }}>
        <div style={{ width: '100%', maxWidth: 1180, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <h1 className="title">{kind === 'ship' ? 'Ship workshop' : 'Crew workshop'}</h1>
          <p className="muted" style={{ margin: 0 }}>{kind === 'ship'
            ? 'Describe a new ship, pick one you have to preview and open it, or start from a base.'
            : 'Pick one to preview it, then open it. Or start a new one from a base.'}</p>
          {kind === 'ship' && <DescribeShip carried={carried?.carried ?? null} disabled={busy} />}
          <span className="muted" style={{ marginTop: 6 }}>Yours</span>
          <ErrorNote error={projects.error} retry={projects.reload} />

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 20, alignItems: 'flex-start' }}>
            <div role="listbox" aria-label={kind === 'ship' ? 'Ships' : 'Crew'} style={{ flex: '1 1 380px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
              {mine.map((p) => (
                <ProjectRow key={p.id} p={p} kind={kind} on={p.id === selectedId} working={activeFor(center.jobs, p.id).length > 0}
                  onSelect={() => setSelectedId(p.id)} onOpen={() => go(p)} />
              ))}
              {!projects.loading && !mine.length && !projects.error && <span className="faint">None yet.</span>}
            </div>
            {selected && (
              <div className="card" style={{ flex: '1 1 460px', minWidth: 0, padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column', position: 'sticky', top: 0 }}>
                <div style={{ position: 'relative', height: 360, display: 'flex' }}>
                  {!urlOf(selected) ? <div className="viewer"><div className="viewer-msg">Not built yet.</div></div>
                    : kind === 'ship'
                      ? <ShipViewer url={urlOf(selected)} view="orbit" partPaths={[]} />
                      : <CrewViewer urls={[urlOf(selected)!]} animation="idle" playing />}
                </div>
                <div style={{ padding: 14, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <span style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0, flex: 1 }}>
                    <span style={{ fontWeight: 500 }}>{selected.name}</span>
                    <span className="faint" style={{ fontSize: 12 }}>
                      {selected.version ? `v${selected.version}` : 'Not built yet'}
                      {selected.derived_from ? ` · variant of ${selected.derived_from.name} v${selected.derived_from.version}` : ''}
                    </span>
                  </span>
                  <button className="btn" disabled={!selected.version || !!selected.draft || busy || placeholder(selected)} onClick={() => variant.show(selected.id)}
                    title={selected.draft ? 'Approve this variant before making variants of it' : placeholder(selected) ? 'Approve its design plan first'
                      : `A new ${kind === 'ship' ? 'ship' : 'crew member'} copied from ${selected.name}, changed as you describe`}>
                    Make a variant
                  </button>
                  <Link to={openPath(selected)} state={carried} onClick={() => remember(kind, selected.id)} className="btn primary">
                    {selected.draft ? 'Review the variant' : 'Open'}
                  </Link>
                </div>
              </div>
            )}
          </div>

          <span className="muted" style={{ marginTop: 8 }}>{kind === 'ship' ? 'Or start from a base' : 'Start new'}</span>
          <ErrorNote error={catalog.error} retry={catalog.reload} />
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8 }}>
            {bases.map((b) => (
              <button key={b.base} className="card" style={{ textAlign: 'left', color: 'var(--text)', display: 'flex', flexDirection: 'column', gap: 4 }} disabled={busy} onClick={() => create(b.base, b.name)}>
                <span style={{ fontWeight: 500 }}>{b.name}</span>
                {b.description && <span className="muted" style={{ fontSize: 12 }}>{b.description}</span>}
              </button>
            ))}
          </div>
          <JobLines jobs={jobs} dismiss={dismiss} />
        </div>
      </main>
      {variant.dialog}
    </>
  );
}

/** What the Ship page hands the Research screen of a ship it just created. */
export interface ResearchStart { researchJob?: string; note?: string; startError?: string; carried?: Message[] }

/**
 * "Describe a new ship": one click creates a custom ship, sends the description and photos as the
 * first research turn, and opens Research to watch it. Nothing is built until the plan is approved.
 */
function DescribeShip({ carried, disabled }: { carried: Message[] | null; disabled: boolean }) {
  const nav = useNavigate();
  const [start] = useState(() => briefFromCarried(carried));
  const [text, setText] = useState(start.text);
  const [empty, setEmpty] = useState(false);
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // No project exists yet, so files are held on the server like Home's and named by the Research chat.
  const files = useAttachPicker((f) => api.uploadHome(f), start.attachments);
  const fromHome = start.attachments.length > 0 && files.ready.some((a) => start.attachments.some((s) => s.id === a.id));

  const go = async () => {
    if (files.uploading || creating) return;
    const brief = text.trim() || (files.ready.length ? 'Create a ship model to look like the attached pictures.' : '');
    if (!brief) { setEmpty(true); return; }
    setCreating(true); setErr(null);
    try {
      const r = await api.createProject('ship', 'custom', undefined, brief);
      const id = r.project.id;
      remember('ship', id);
      const atts = files.ready;
      let state: ResearchStart = { note: r.project.note };
      try {
        // Home's upload ids are copied into the project by the server when the chat names them.
        const c = await api.chat(id, brief, 'research', atts.map((a) => a.id));
        state = { ...state, researchJob: c.job_id };
      } catch (e) {
        // The ship exists but the research did not start: open it with the brief ready to send again.
        state = { ...state, startError: `The research did not start: ${errorText(e)}`,
          carried: [{ who: 'You', text: brief, at: new Date().toISOString(), ...(atts.length ? { attachments: atts } : {}) }] };
      }
      nav(paths.shipResearch(id), { state });
    } catch (e) {
      setErr(errorText(e));
      setCreating(false);
    }
  };

  return (
    <div {...files.dropProps} className="card" style={{ borderColor: files.dragging ? 'var(--accent)' : 'var(--accent)', borderStyle: files.dragging ? 'dashed' : 'solid', borderRadius: 12, padding: 18, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <span style={{ fontWeight: 500, fontSize: 16 }}>Describe a new ship</span>
      <span className="muted" style={{ fontSize: 13 }}>Any type: a yacht, a tug, a patrol boat. Add photos or drawings. The AI researches it first, then builds it.</span>
      {files.chips}
      {fromHome && <span className="faint" style={{ fontSize: 12, marginTop: -6 }}>Carried from Home</span>}
      <label htmlFor="describe-ship" className="sr-only">Describe the ship</label>
      <textarea id="describe-ship" rows={2} className="field dark" value={text} placeholder="A 38 m motor yacht like the one in the photo"
        onChange={(e) => { setText(e.target.value); setEmpty(false); }}
        onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void go(); } }}
        onPaste={files.onPaste}
        style={{ padding: '10px 12px', fontSize: 14 }} />
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        {files.button}
        {empty ? <span className="bad" style={{ fontSize: 12 }}>Describe the ship, or attach a photo, first.</span>
          : <span className="faint" style={{ fontSize: 12 }}>Name it later; the AI suggests one from the research.</span>}
        <button className="btn primary lg" style={{ marginLeft: 'auto' }} disabled={disabled || creating || files.uploading} onClick={() => void go()}>
          {creating && <span className="spinner" aria-hidden="true" />}{creating ? 'Starting…' : 'Research and build'}
        </button>
      </div>
      {files.note}
      <ErrorNote error={err} />
    </div>
  );
}

/** One project: its picture, name and state. Click to preview it; double-click (or Enter) to open it. */
function ProjectRow({ p, kind, on, working, onSelect, onOpen }: {
  p: ProjectSummary; kind: ProjectKind; on: boolean; working: boolean; onSelect: () => void; onOpen: () => void;
}) {
  const { src, failed } = useThumbnail(urlOf(p), PICTURE_VIEW[kind]);
  return (
    <button role="option" aria-selected={on} className={`row-btn ${on ? 'on' : ''}`}
      onClick={onSelect} onDoubleClick={onOpen} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); onOpen(); } }}
      title="Click to preview, double-click to open"
      style={{ alignItems: 'center', gap: 12, padding: 8, textAlign: 'left' }}>
      <span style={{ width: 128, height: 80, flexShrink: 0, borderRadius: 6, overflow: 'hidden', background: 'var(--stage)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {src ? <img src={src} alt="" width={128} height={80} style={{ display: 'block', objectFit: 'cover' }} />
          : !p.version || failed ? <span className="faint" style={{ fontSize: 11 }}>{p.version ? 'No preview' : 'Not built'}</span>
            : <span className="spinner" aria-label="Drawing a preview" />}
      </span>
      <span style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0, flex: 1 }}>
        <span style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</span>
        {p.derived_from && <span className="faint" style={{ fontSize: 12 }}>Variant of {p.derived_from.name} v{p.derived_from.version}</span>}
        {p.draft ? <span className="chip amber" style={{ alignSelf: 'flex-start' }}>Awaiting your approval</span>
          : <span className="muted" style={{ fontSize: 12 }}>{p.version ? `v${p.version}` : 'not built yet'}</span>}
        {working && <span className="chip blue" style={{ alignSelf: 'flex-start', display: 'inline-flex', gap: 6, alignItems: 'center' }}><span className="spinner" aria-hidden="true" />Working on a change</span>}
      </span>
    </button>
  );
}
