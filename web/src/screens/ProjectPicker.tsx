// /ship and /crew without a project: find one or start a new one. A search box, category chips,
// filters (status, original or variant, base type), sorting and grouping narrow the list; the
// filter lives in the page address, so Back and a shared link keep it. Each project shows a
// picture of its model; the selected one is previewed live beside the list, where it can also be
// renamed and filed under a category.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { api, errorText, modelUrl } from '../api';
import { ErrorNote, JobLines, TopBar } from '../components/common';
import { useVariantDialog } from '../components/VariantDialog';
import { useJobCenter } from '../components/JobCenter';
import { activeFor, isActiveJob } from '../lib/jobcenter';
import { recall, remember, useCatalog, useJobs, useProjects } from '../hooks';
import type { ViewName } from '../lib/frames';
import {
  DEFAULT_FILTER, UNCATEGORIZED, applyFilter, applyOtherFilters, categoryCounts, categoryOf, filterFromParams,
  filterToParams, groupByCategory, isFiltered, type ProjectFilter,
} from '../lib/projectFilter';
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
  const [params, setParams] = useSearchParams();
  // The search text is kept here and copied to the address: read back from the address, fast
  // typing would lose letters while each update lands.
  const fromAddress = filterFromParams(params);
  const [q, setQ] = useState(fromAddress.q);
  const filter: ProjectFilter = { ...fromAddress, q };
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
  const open = (id: string) => (kind === 'ship' ? paths.shipBuild(id) : paths.crew(id));
  const bases: { base: ProjectBase; name: string; description?: string; category?: string | null }[] = kind === 'ship'
    ? (catalog.data?.ships.map((s) => ({ base: s.type as ProjectBase, name: s.name, description: s.description, category: s.category })) ?? [])
    : (catalog.data?.crew.map((c) => ({ base: c.id as ProjectBase, name: c.name, category: c.category })) ?? [{ base: 'male_default', name: 'Default male crew' }]);
  const baseLabel = (b: string) => bases.find((x) => x.base === b)?.name ?? b;
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
    }, () => { remember(kind, id); nav(open(id), { state: carried }); });
  };
  const go = (p: ProjectSummary) => { remember(kind, p.id); nav(projectPath(p), { state: carried }); };
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
          <p className="muted" style={{ margin: 0 }}>Find one to preview it, then open it. Or start a new one from a base, below.</p>
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
                    <button className="btn" disabled={!selected.version || !!selected.draft || busy} onClick={() => variant.show(selected.id)}
                      title={selected.draft ? 'Approve this variant before making variants of it' : `A new ${kind === 'ship' ? 'ship' : 'crew member'} copied from ${selected.name}, changed as you describe`}>
                      Make a variant
                    </button>
                    <Link to={projectPath(selected)} state={carried} onClick={() => remember(kind, selected.id)} className="btn primary">
                      {selected.draft ? 'Review the variant' : 'Open'}
                    </Link>
                  </div>
                  <CategoryField key={selected.id} project={selected} choices={counts.map((c) => c.category).filter((c) => c !== UNCATEGORIZED)}
                    onSaved={() => void reloadProjects()} />
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
