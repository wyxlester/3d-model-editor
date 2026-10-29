// /ship and /crew without a project: pick one or start a new one. The Ship page also describes a
// new ship of any type (Lester, 29 Oct: "research first, wait for user confirmation to build").
import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { api, errorText } from '../api';
import { ErrorNote, JobLines, TopBar, useAttachPicker } from '../components/common';
import { remember, useCatalog, useJobs, useProjects } from '../hooks';
import { briefFromCarried } from '../lib/designPlan';
import { paths } from '../lib/routing';
import type { Message, ProjectBase, ProjectKind, ProjectSummary } from '../types';

export function ProjectPicker({ kind }: { kind: ProjectKind }) {
  const nav = useNavigate();
  // Home's conversation (and files) passes through this picker to the project chosen.
  const carried = useLocation().state as { carried?: Message[] } | null;
  const projects = useProjects();
  const catalog = useCatalog();
  const { jobs, run, dismiss, busy } = useJobs();
  const mine = (projects.data ?? []).filter((p) => p.kind === kind);
  // A custom ship opens in Research: its design plan lives there and nothing builds without it.
  const open = (p: ProjectSummary) => (kind === 'crew' ? paths.crew(p.id) : p.base === 'custom' ? paths.shipResearch(p.id) : paths.shipBuild(p.id));
  const bases: { base: ProjectBase; name: string; description?: string }[] = kind === 'ship'
    ? (catalog.data?.ships.filter((s) => s.type !== 'custom').map((s) => ({ base: s.type as ProjectBase, name: s.name, description: s.description })) ?? [])
    : (catalog.data?.crew.map((c) => ({ base: c.id as ProjectBase, name: c.name })) ?? [{ base: 'male_default', name: 'Default male crew' }]);

  const create = (base: ProjectBase, name: string) => {
    let id = '';
    void run(`Building a new ${name.toLowerCase()}`, async () => {
      const r = await api.createProject(kind, base);
      id = r.project.id;
      return { job_id: r.job_id };
    }, () => { remember(kind, id); nav(kind === 'ship' ? paths.shipBuild(id) : paths.crew(id), { state: carried }); });
  };

  return (
    <>
      <TopBar />
      <main className="scroll" style={{ flex: 1, padding: '40px 24px', display: 'flex', justifyContent: 'center' }}>
        <div style={{ width: '100%', maxWidth: kind === 'ship' ? 900 : 760, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <h1 className="title">{kind === 'ship' ? 'Ship workshop' : 'Crew workshop'}</h1>
          <p className="muted" style={{ margin: 0 }}>{kind === 'ship' ? 'Open one you have, describe a new one, or start from a base.' : 'Open one you have, or start a new one from a base.'}</p>
          {kind === 'ship' && <DescribeShip carried={carried?.carried ?? null} disabled={busy} />}
          <span className="muted" style={{ marginTop: 6 }}>Yours</span>
          <ErrorNote error={projects.error} retry={projects.reload} />
          {mine.map((p) => (
            <Link key={p.id} to={open(p)} state={carried} onClick={() => remember(kind, p.id)} className="card" style={{ color: 'var(--text)', display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ fontWeight: 500 }}>{p.name}</span>
              <span className="muted">{p.version ? `v${p.version}` : 'not built yet'}</span>
            </Link>
          ))}
          {!projects.loading && !mine.length && !projects.error && <span className="faint">None yet.</span>}
          <span className="muted" style={{ marginTop: 6 }}>{kind === 'ship' ? 'Or start from a base' : 'Start new'}</span>
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
