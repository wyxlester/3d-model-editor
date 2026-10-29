// /ship and /crew without a project: pick one or start a new one. Each project shows a picture of
// its model; the selected one is previewed live beside the list, with Open and Make a variant.
import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { api, modelUrl } from '../api';
import { ErrorNote, JobLines, TopBar } from '../components/common';
import { useVariantDialog } from '../components/VariantDialog';
import { recall, remember, useCatalog, useJobs, useProjects } from '../hooks';
import type { ViewName } from '../lib/frames';
import { paths, projectPath } from '../lib/routing';
import type { ProjectBase, ProjectKind, ProjectSummary } from '../types';
import { CrewViewer, ShipViewer } from '../viewer/Viewer';
import { useThumbnail } from '../viewer/thumbnail';

/** Ships are pictured from the bow quarter, people from the front. */
const PICTURE_VIEW: Record<ProjectKind, ViewName> = { ship: 'orbit', crew: 'bow' };

const urlOf = (p: ProjectSummary) => (p.version ? modelUrl(p.id, p.version) : null);

export function ProjectPicker({ kind }: { kind: ProjectKind }) {
  const nav = useNavigate();
  // Home's conversation (and files) passes through this picker to the project chosen.
  const carried = useLocation().state;
  const projects = useProjects();
  const catalog = useCatalog();
  const { jobs, run, dismiss, busy } = useJobs();
  const variant = useVariantDialog();
  const mine = (projects.data ?? []).filter((p) => p.kind === kind);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const open = (id: string) => (kind === 'ship' ? paths.shipBuild(id) : paths.crew(id));
  const bases: { base: ProjectBase; name: string; description?: string }[] = kind === 'ship'
    ? (catalog.data?.ships.map((s) => ({ base: s.type as ProjectBase, name: s.name, description: s.description })) ?? [])
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
    }, () => { remember(kind, id); nav(open(id), { state: carried }); });
  };
  const go = (p: ProjectSummary) => { remember(kind, p.id); nav(projectPath(p), { state: carried }); };

  return (
    <>
      <TopBar />
      <main className="scroll" style={{ flex: 1, padding: '32px 24px', display: 'flex', justifyContent: 'center' }}>
        <div style={{ width: '100%', maxWidth: 1180, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <h1 className="title">{kind === 'ship' ? 'Ship workshop' : 'Crew workshop'}</h1>
          <p className="muted" style={{ margin: 0 }}>Pick one to preview it, then open it. Or start a new one from a base.</p>
          <ErrorNote error={projects.error} retry={projects.reload} />

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 20, alignItems: 'flex-start' }}>
            <div role="listbox" aria-label={kind === 'ship' ? 'Ships' : 'Crew'} style={{ flex: '1 1 380px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
              {mine.map((p) => (
                <ProjectRow key={p.id} p={p} kind={kind} on={p.id === selectedId}
                  onSelect={() => setSelectedId(p.id)} onOpen={() => go(p)} />
              ))}
              {!projects.loading && !mine.length && !projects.error && <span className="faint">None yet. Start one below.</span>}
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
                  <button className="btn" disabled={!selected.version || !!selected.draft || busy} onClick={() => variant.show(selected.id)}
                    title={selected.draft ? 'Approve this variant before making variants of it' : `A new ${kind === 'ship' ? 'ship' : 'crew member'} copied from ${selected.name}, changed as you describe`}>
                    Make a variant
                  </button>
                  <Link to={projectPath(selected)} state={carried} onClick={() => remember(kind, selected.id)} className="btn primary">
                    {selected.draft ? 'Review the variant' : 'Open'}
                  </Link>
                </div>
              </div>
            )}
          </div>

          <span className="muted" style={{ marginTop: 8 }}>Start new</span>
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

/** One project: its picture, name and state. Click to preview it; double-click (or Enter) to open it. */
function ProjectRow({ p, kind, on, onSelect, onOpen }: {
  p: ProjectSummary; kind: ProjectKind; on: boolean; onSelect: () => void; onOpen: () => void;
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
      </span>
    </button>
  );
}
