// /ship and /crew without a project: find one or start a new one. The Ship page also describes a
// new ship of any type (Lester, 29 Oct: "research first, wait for user confirmation to build").
// A search box, category chips, filters (status, original or variant, base type), sorting and
// grouping narrow the list; the filter lives in the page address, so Back and a shared link keep it.
// Each project shows a picture of its model; the selected one is previewed live beside the list,
// with Open and Make a variant, and can be renamed and filed under a category there.
// Order on the Ship page (Lester, 29 Oct): the Describe card, then the search and filters directly
// above the list they filter, then the list with its preview, then the bases.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { api, errorText, modelUrl } from '../api';
import { ErrorNote, JobLines, TopBar, useAttachPicker } from '../components/common';
import { useVariantDialog } from '../components/VariantDialog';
import { useJobCenter } from '../components/JobCenter';
import { activeFor, isActiveJob } from '../lib/jobcenter';
import { recall, remember, useCatalog, useJobs, useProjects } from '../hooks';
import { briefFromCarried } from '../lib/designPlan';
import type { ViewName } from '../lib/frames';
import {
  DEFAULT_FILTER, UNCATEGORIZED, applyFilter, applyOtherFilters, categoryCounts, categoryOf, filterFromParams,
  filterToParams, groupByCategory, isFiltered, type ProjectFilter,
} from '../lib/projectFilter';
import { paths, projectPath } from '../lib/routing';
import type { Message, ProjectBase, ProjectKind, ProjectSummary } from '../types';
import { CrewViewer, ShipViewer } from '../viewer/Viewer';
import { useThumbnail } from '../viewer/thumbnail';

/** Ships are pictured from the bow quarter, people from the front. */
const PICTURE_VIEW: Record<ProjectKind, ViewName> = { ship: 'orbit', crew: 'bow' };

const urlOf = (p: ProjectSummary) => (p.version ? modelUrl(p.id, p.version) : null);

/** A custom ship still waiting for its plan: no variants yet (Lester, 29 Oct). */
const placeholder = (p: ProjectSummary) => p.kind === 'ship' && p.base === 'custom' && !p.plan_built;
/**
 * Where a project opens from the list. A custom ship opens in Research: its design plan lives there
 * and nothing builds without it. Everything else goes where projectPath says, so a variant still
 * awaiting approval opens its review (that check comes first, custom ship or not).
 */
const openPath = (p: ProjectSummary) => (!p.draft && p.kind === 'ship' && p.base === 'custom' ? paths.shipResearch(p.id) : projectPath(p));

export function ProjectPicker({ kind }: { kind: ProjectKind }) {
  const nav = useNavigate();
  // Home's conversation (and files) passes through this picker to the project chosen.
  const carried = useLocation().state as { carried?: Message[] } | null;
  const [params, setParams] = useSearchParams();
  // The search text is kept here and copied to the address: read back from the address, fast
  // typing would lose letters while each update lands.
  const fromAddress = filterFromParams(params);
  const [q, setQ] = useState(fromAddress.q);
  const filter: ProjectFilter = { ...fromAddress, q };
  // The location state (Home's carried conversation) goes along with every filter change, so the
  // pre-filled Describe card and the conversation handed to the project opened are not lost.
  const setFilter = (patch: Partial<ProjectFilter>) => {
    if (patch.q !== undefined) setQ(patch.q);
    setParams(filterToParams({ ...filter, ...patch }), { replace: true, state: carried });
  };
  const clearFilters = () => setFilter({ ...DEFAULT_FILTER, sort: filter.sort, group: filter.group });
  const projects = useProjects();
  const catalog = useCatalog();
  const { jobs, run, dismiss, busy } = useJobs();
  const variant = useVariantDialog();
  const mine = useMemo(() => (projects.data ?? []).filter((p) => p.kind === kind), [projects.data, kind]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  // Models being changed in the background: marked in the list, and re-read when a change finishes
  // so the picture and preview show the new version.
  const center = useJobCenter();
  const finishedHere = mine.reduce((n, p) => n + (center.finished[p.id] ?? 0), 0);
  const reloadProjects = projects.reload;
  useEffect(() => { if (finishedHere) void reloadProjects(); }, [finishedHere, reloadProjects]);
  // Every catalog type names its projects (a described ship's base reads "Any ship", and the
  // base-type filter offers it), but "Any ship" is started from the Describe card, not as a base.
  const types: { base: ProjectBase; name: string; description?: string; category?: string | null }[] = kind === 'ship'
    ? (catalog.data?.ships.map((s) => ({ base: s.type as ProjectBase, name: s.name, description: s.description, category: s.category })) ?? [])
    : (catalog.data?.crew.map((c) => ({ base: c.id as ProjectBase, name: c.name, category: c.category })) ?? [{ base: 'male_default', name: 'Default male crew' }]);
  const bases = types.filter((b) => b.base !== 'custom');
  const baseLabel = (b: string) => types.find((x) => x.base === b)?.name ?? b;
  const defaults = catalog.data?.categories?.[kind] ?? [];

  const working = new Set(center.jobs.filter(isActiveJob).map((j) => j.project).filter((p): p is string => !!p));
  const ctx = { working, baseLabel };
  const shown = applyFilter(mine, filter, ctx);
  const counts = categoryCounts(applyOtherFilters(mine, filter, ctx), defaults);
  const usedBases = [...new Set(mine.map((p) => p.base).filter((b): b is string => !!b))];

  // Keep a project selected among those shown: the one opened last, else the first.
  const shownIds = shown.map((p) => p.id).join();
  useEffect(() => {
    const list = shownIds ? shownIds.split(',') : [];
    setSelectedId((cur) => {
      if (cur && list.includes(cur)) return cur;
      const last = recall(kind);
      return last && list.includes(last) ? last : list[0] ?? null;
    });
  }, [shownIds, kind]);
  const selected = shown.find((p) => p.id === selectedId) ?? null;

  // "/" jumps to the search box, as on many sites; Escape in it clears it.
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.key === '/' && !(t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable))) {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, []);

  const create = (base: ProjectBase, name: string) => {
    let id = '';
    void run(`Building a new ${name.toLowerCase()}`, async () => {
      const r = await api.createProject(kind, base);
      id = r.project.id;
      return { job_id: r.job_id };
    }, () => { remember(kind, id); nav(kind === 'ship' ? paths.shipBuild(id) : paths.crew(id), { state: carried }); });
  };
  const go = (p: ProjectSummary) => { remember(kind, p.id); nav(openPath(p), { state: carried }); };
  const noun = kind === 'ship' ? 'ships' : 'crew';
  const row = (p: ProjectSummary) => (
    <ProjectRow key={p.id} p={p} kind={kind} on={p.id === selectedId} working={activeFor(center.jobs, p.id).length > 0}
      onSelect={() => setSelectedId(p.id)} onOpen={() => go(p)} />
  );

  return (
    <>
      <TopBar />
      <main className="scroll" style={{ flex: 1, padding: '32px 24px', display: 'flex', justifyContent: 'center' }}>
        <div style={{ width: '100%', maxWidth: 1180, display: 'flex', flexDirection: 'column', gap: 14 }}>
          <h1 className="title">{kind === 'ship' ? 'Ship workshop' : 'Crew workshop'}</h1>
          <p className="muted" style={{ margin: 0 }}>{kind === 'ship'
            ? 'Describe a new ship, or find one to preview and open. Or start from a base, below.'
            : 'Find one to preview it, then open it. Or start a new one from a base, below.'}</p>
          {kind === 'ship' && <DescribeShip carried={carried?.carried ?? null} disabled={busy} />}
          <ErrorNote error={projects.error} retry={projects.reload} />

          {mine.length > 0 && <>
            <div className="filter-bar" role="search">
              <input ref={searchRef} type="search" className="field" style={{ flex: '1 1 260px', minWidth: 0 }} value={filter.q}
                placeholder={`Search ${noun} by name, category or type  ( / )`} aria-label={`Search ${noun}`}
                onChange={(e) => setFilter({ q: e.target.value })}
                onKeyDown={(e) => { if (e.key === 'Escape') setFilter({ q: '' }); }} />
              <select className="field" aria-label="Status" value={filter.status} onChange={(e) => setFilter({ status: e.target.value as ProjectFilter['status'] })}>
                <option value="all">Any status</option>
                <option value="working">Working on a change</option>
                <option value="approval">Awaiting approval</option>
                <option value="built">Built</option>
                <option value="unbuilt">Not built yet</option>
              </select>
              <select className="field" aria-label="Originals or variants" value={filter.origin} onChange={(e) => setFilter({ origin: e.target.value as ProjectFilter['origin'] })}>
                <option value="all">Originals and variants</option>
                <option value="originals">Originals only</option>
                <option value="variants">Variants only</option>
              </select>
              {usedBases.length > 1 && (
                <select className="field" aria-label="Base type" value={filter.base ?? ''} onChange={(e) => setFilter({ base: e.target.value || null })}>
                  <option value="">Any type</option>
                  {usedBases.map((b) => <option key={b} value={b}>{baseLabel(b)}</option>)}
                </select>
              )}
              <select className="field" aria-label="Sort by" value={filter.sort} onChange={(e) => setFilter({ sort: e.target.value as ProjectFilter['sort'] })}>
                <option value="recent">Recently changed</option>
                <option value="name">Name, A to Z</option>
                <option value="name_desc">Name, Z to A</option>
                <option value="versions">Most versions</option>
              </select>
              <label className="muted" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, whiteSpace: 'nowrap' }}>
                <input type="checkbox" checked={filter.group} onChange={(e) => setFilter({ group: e.target.checked })} />
                Group by category
              </label>
            </div>
            <div className="chip-row" role="group" aria-label="Category">
              <button className={`btn sm pill ${!filter.category ? 'selected' : ''}`} aria-pressed={!filter.category} onClick={() => setFilter({ category: null })}>
                All <span className="faint">{counts.reduce((n, c) => n + c.count, 0)}</span>
              </button>
              {counts.map((c) => (
                <button key={c.category} className={`btn sm pill ${filter.category === c.category ? 'selected' : ''}`} aria-pressed={filter.category === c.category}
                  style={c.count ? undefined : { opacity: 0.55 }} onClick={() => setFilter({ category: filter.category === c.category ? null : c.category })}>
                  {c.category} <span className="faint">{c.count}</span>
                </button>
              ))}
            </div>
            <div className="faint" style={{ fontSize: 12, display: 'flex', gap: 10, alignItems: 'center' }}>
              <span>{shown.length === mine.length ? `${mine.length} ${noun}` : `Showing ${shown.length} of ${mine.length} ${noun}`}</span>
              {isFiltered(filter) && <button className="btn sm muted" onClick={() => clearFilters()}>Clear filters</button>}
            </div>
          </>}

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 20, alignItems: 'flex-start' }}>
            <div role="listbox" aria-label={kind === 'ship' ? 'Ships' : 'Crew'} style={{ flex: '1 1 380px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
              {filter.group
                ? groupByCategory(shown, defaults).map((g) => (
                  <section key={g.category} aria-label={g.category} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <span className="eyebrow" style={{ marginTop: 6 }}>{g.category} · {g.items.length}</span>
                    {g.items.map(row)}
                  </section>
                ))
                : shown.map(row)}
              {!projects.loading && !mine.length && !projects.error && <span className="faint">None yet. Start one below.</span>}
              {mine.length > 0 && !shown.length && (
                <span className="faint" style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                  No {noun} match.
                  <button className="btn sm" onClick={() => clearFilters()}>Clear filters</button>
                </span>
              )}
            </div>
            {selected && (
              <div className="card" style={{ flex: '1 1 460px', minWidth: 0, padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column', position: 'sticky', top: 0 }}>
                <div style={{ position: 'relative', height: 360, display: 'flex' }}>
                  {!urlOf(selected) ? <div className="viewer"><div className="viewer-msg">Not built yet.</div></div>
                    : kind === 'ship'
                      ? <ShipViewer url={urlOf(selected)} view="orbit" partPaths={[]} />
                      : <CrewViewer urls={[urlOf(selected)!]} animation="idle" playing />}
                </div>
                <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    <span style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0, flex: 1 }}>
                      <NameField key={selected.id} project={selected} onSaved={() => void reloadProjects()} />
                      <span className="faint" style={{ fontSize: 12 }}>
                        {selected.version ? `v${selected.version}` : 'Not built yet'} · {baseLabel(selected.base ?? '')}
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
                  <CategoryField key={selected.id} project={selected} choices={counts.map((c) => c.category).filter((c) => c !== UNCATEGORIZED)}
                    onSaved={() => void reloadProjects()} />
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
                {b.category && <span className="chip grey" style={{ alignSelf: 'flex-start' }}>{b.category}</span>}
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

/** One project: its picture, name, category and state. Click to preview it; double-click (or Enter) to open it. */
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
        <span className="faint" style={{ fontSize: 12 }}>
          {categoryOf(p)}{p.derived_from ? ` · variant of ${p.derived_from.name} v${p.derived_from.version}` : ''}
        </span>
        {p.draft ? <span className="chip amber" style={{ alignSelf: 'flex-start' }}>Awaiting your approval</span>
          : <span className="muted" style={{ fontSize: 12 }}>{p.version ? `v${p.version}` : 'not built yet'}</span>}
        {working && <span className="chip blue" style={{ alignSelf: 'flex-start', display: 'inline-flex', gap: 6, alignItems: 'center' }}><span className="spinner" aria-hidden="true" />Working on a change</span>}
      </span>
    </button>
  );
}

/** The project's name, with Rename. Renaming changes what lists and screens show, not the exported model. */
function NameField({ project, onSaved }: { project: ProjectSummary; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(project.name);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    const name = text.trim();
    if (!name || name === project.name) { setEditing(false); return; }
    try { await api.patchProject(project.id, { name }); setEditing(false); setErr(null); onSaved(); } catch (e) { setErr(errorText(e)); }
  };
  if (!editing) {
    return (
      <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
        <span style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{project.name}</span>
        <button className="btn sm muted" style={{ height: 24 }} onClick={() => { setText(project.name); setEditing(true); }}>Rename</button>
      </span>
    );
  }
  return (
    <span style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
      <input className="field" autoFocus aria-label="New name" value={text} maxLength={60} style={{ flex: 1, minWidth: 140 }}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') void save(); if (e.key === 'Escape') setEditing(false); }} />
      <button className="btn sm primary" onClick={() => void save()}>Save</button>
      <button className="btn sm" onClick={() => setEditing(false)}>Cancel</button>
      {err && <span className="bad" style={{ fontSize: 12, flexBasis: '100%' }}>{err}</span>}
    </span>
  );
}

const NEW = '\u0000new';

/** File the project under a category: one of the defaults or in use, or a new one typed in. */
function CategoryField({ project, choices, onSaved }: { project: ProjectSummary; choices: string[]; onSaved: () => void }) {
  const current = project.category?.trim() || '';
  const [adding, setAdding] = useState(false);
  const [text, setText] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const save = async (category: string) => {
    const c = category.trim();
    if (!c || c === current) { setAdding(false); return; }
    try { await api.patchProject(project.id, { category: c }); setAdding(false); setErr(null); onSaved(); } catch (e) { setErr(errorText(e)); }
  };
  const options = current && !choices.includes(current) ? [current, ...choices] : choices;
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 13 }}>
      <label htmlFor="project-category" className="faint">Category</label>
      {!adding ? (
        <select id="project-category" className="field" value={current} style={{ minWidth: 200 }}
          onChange={(e) => { if (e.target.value === NEW) { setText(''); setAdding(true); } else void save(e.target.value); }}>
          {!current && <option value="">{UNCATEGORIZED}</option>}
          {options.map((c) => <option key={c} value={c}>{c}</option>)}
          <option value={NEW}>New category…</option>
        </select>
      ) : (
        <>
          <input id="project-category" className="field" autoFocus value={text} maxLength={60} placeholder="e.g. Pilot boats" style={{ minWidth: 180 }}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void save(text); if (e.key === 'Escape') setAdding(false); }} />
          <button className="btn sm primary" disabled={!text.trim()} onClick={() => void save(text)}>Add</button>
          <button className="btn sm" onClick={() => setAdding(false)}>Cancel</button>
        </>
      )}
      {err && <span className="bad" style={{ fontSize: 12, flexBasis: '100%' }}>{err}</span>}
    </div>
  );
}
