// /ship and /crew without a project: pick one or start a new one.
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import { ErrorNote, JobLines, TopBar } from '../components/common';
import { useVariantDialog } from '../components/VariantDialog';
import { remember, useCatalog, useJobs, useProjects } from '../hooks';
import { paths, projectPath } from '../lib/routing';
import type { ProjectBase, ProjectKind } from '../types';

export function ProjectPicker({ kind }: { kind: ProjectKind }) {
  const nav = useNavigate();
  const projects = useProjects();
  const catalog = useCatalog();
  const { jobs, run, dismiss, busy } = useJobs();
  const variant = useVariantDialog();
  const mine = (projects.data ?? []).filter((p) => p.kind === kind);
  const open = (id: string) => (kind === 'ship' ? paths.shipBuild(id) : paths.crew(id));
  const bases: { base: ProjectBase; name: string; description?: string }[] = kind === 'ship'
    ? (catalog.data?.ships.map((s) => ({ base: s.type as ProjectBase, name: s.name, description: s.description })) ?? [])
    : (catalog.data?.crew.map((c) => ({ base: c.id as ProjectBase, name: c.name })) ?? [{ base: 'male_default', name: 'Default male crew' }]);

  const create = (base: ProjectBase, name: string) => {
    let id = '';
    void run(`Building a new ${name.toLowerCase()}`, async () => {
      const r = await api.createProject(kind, base);
      id = r.project.id;
      return { job_id: r.job_id };
    }, () => { remember(kind, id); nav(open(id)); });
  };

  return (
    <>
      <TopBar />
      <main className="scroll" style={{ flex: 1, padding: '40px 24px', display: 'flex', justifyContent: 'center' }}>
        <div style={{ width: '100%', maxWidth: 760, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <h1 className="title">{kind === 'ship' ? 'Ship workshop' : 'Crew workshop'}</h1>
          <p className="muted" style={{ margin: 0 }}>Open one you have, or start a new one from a base.</p>
          <ErrorNote error={projects.error} retry={projects.reload} />
          {mine.map((p) => (
            <div key={p.id} style={{ display: 'flex', gap: 8, alignItems: 'stretch' }}>
              <Link to={projectPath(p)} onClick={() => remember(kind, p.id)} className="card" style={{ flex: 1, minWidth: 0, color: 'var(--text)', display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                <span style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
                  <span style={{ fontWeight: 500 }}>{p.name}</span>
                  {p.derived_from && <span className="faint" style={{ fontSize: 12 }}>Variant of {p.derived_from.name} v{p.derived_from.version}</span>}
                </span>
                {p.draft ? <span className="chip amber" style={{ alignSelf: 'center' }}>Awaiting your approval</span>
                  : <span className="muted">{p.version ? `v${p.version}` : 'not built yet'}</span>}
              </Link>
              <button className="btn" style={{ height: 'auto' }} disabled={!p.version || !!p.draft || busy} onClick={() => variant.show(p.id)}
                title={p.draft ? 'Approve this variant before making variants of it' : `A new ${kind === 'ship' ? 'ship' : 'crew member'} copied from ${p.name}, changed as you describe`}>Make a variant</button>
            </div>
          ))}
          {!projects.loading && !mine.length && !projects.error && <span className="faint">None yet.</span>}
          <span className="muted" style={{ marginTop: 8 }}>Start new</span>
          <ErrorNote error={catalog.error} retry={catalog.reload} />
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8 }}>
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
