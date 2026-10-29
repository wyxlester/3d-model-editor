// "Make a variant" (CONTRACT §10): a new ship or crew project copied from one version of an
// existing one, then changed by the AI from an optional description of what is different.
import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, errorText } from '../api';
import { useLoad, useProject } from '../hooks';
import { paths, projectPath } from '../lib/routing';
import type { Project, VariantRequest } from '../types';
import { ErrorNote, Loading } from './common';

/**
 * The dialog. Making the variant opens its review screen at once, where the build is watched
 * live next to the original and the variant is approved or discarded.
 */
export function useVariantDialog() {
  const nav = useNavigate();
  const [source, setSource] = useState<{ id: string; brief: string } | null>(null);

  const start = async (src: Project, req: VariantRequest) => {
    const r = await api.createVariant(src.id, req);
    setSource(null);
    nav(paths.variant(r.project.id));
  };

  const show = useCallback((id: string, brief = '') => setSource({ id, brief }), []);
  const dialog = source
    ? <VariantDialog projectId={source.id} initialBrief={source.brief} onCancel={() => setSource(null)} onStart={start} />
    : null;
  return { show, dialog };
}

/** "Variant of PC 303 v4", linking to the source, and to the review while it awaits approval. */
export function DerivedFromLink({ project }: { project: Pick<Project, 'id' | 'kind' | 'derived_from' | 'draft'> | null | undefined }) {
  if (!project?.derived_from) return null;
  const d = project.derived_from;
  return (
    <>
      <Link to={projectPath({ kind: project.kind, id: d.project })} className="faint" title="Open the project this was copied from">Variant of {d.name} v{d.version}</Link>
      {project.draft && <Link to={paths.variant(project.id)} className="chip amber">Awaiting your approval</Link>}
    </>
  );
}

function VariantDialog({ projectId, initialBrief, onCancel, onStart }: {
  projectId: string;
  initialBrief: string;
  onCancel: () => void;
  onStart: (source: Project, req: VariantRequest) => Promise<void>;
}) {
  const project = useProject(projectId);
  const p = project.data;
  const isShip = p?.kind === 'ship';
  const scenes = useLoad(() => (isShip ? api.scenes() : Promise.resolve([])), [isShip]);
  const onThisShip = (scenes.data ?? []).filter((s) => s.ship.project === projectId);

  const [version, setVersion] = useState<number | null>(null);
  const [name, setName] = useState('');
  const [brief, setBrief] = useState(initialBrief);
  const [research, setResearch] = useState(false);
  const [scene, setScene] = useState('');
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!p) return;
    setVersion((v) => v ?? p.version);
    setName((n) => n || `${p.name} (variant)`);
  }, [p]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel(); };
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [onCancel]);

  const built = (p?.versions ?? []).filter((v) => v.ok).reverse();
  const hasBrief = brief.trim().length > 0;
  const submit = async () => {
    if (!p || version === null) return;
    setSending(true); setErr(null);
    try {
      await onStart(p, {
        name: name.trim() || `${p.name} (variant)`, version,
        ...(hasBrief ? { brief: brief.trim() } : {}),
        ...(isShip && hasBrief && research ? { research: true } : {}),
        ...(isShip && scene ? { scene } : {}),
      });
    } catch (e) {
      setErr(errorText(e));
      setSending(false);
    }
  };

  return (
    <div className="scrim" onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div role="dialog" aria-label="Make a variant" className="dialog" style={{ width: 520, maxWidth: 'calc(100vw - 32px)' }}>
        <div style={{ fontSize: 18, fontWeight: 500 }}>Make a variant{p ? ` of ${p.name}` : ''}</div>
        <ErrorNote error={project.error} retry={project.reload} />
        {!p ? <Loading what="the project" /> : <>
          <div className="muted" style={{ lineHeight: 1.5 }}>
            A new {isShip ? 'ship' : 'crew member'} that starts as an exact copy. {p.name} stays as it is.
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '120px minmax(0, 1fr)', gap: 10, alignItems: 'center' }}>
            <label htmlFor="variant-from" className="faint">Based on</label>
            <select id="variant-from" className="field dark" value={version ?? ''} onChange={(e) => setVersion(Number(e.target.value))}>
              {built.map((v) => <option key={v.n} value={v.n}>v{v.n}{v.n === p.version ? ' (current)' : ''} · {v.summary}</option>)}
            </select>
            <label htmlFor="variant-name" className="faint">Name</label>
            <input id="variant-name" className="field dark" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <label htmlFor="variant-brief" className="faint">What's different? (optional)</label>
          <textarea id="variant-brief" className="field dark" rows={4} value={brief} onChange={(e) => setBrief(e.target.value)}
            placeholder={isShip
              ? 'A 25 mm remote weapon station on the foredeck in place of the 12.7 mm, and a second X-band radar on the mast'
              : 'Same build, but female, in a combat helmet and carrying binoculars'} />
          {isShip && (
            <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', lineHeight: 1.45, opacity: hasBrief ? 1 : 0.5 }}>
              <input type="checkbox" checked={research && hasBrief} disabled={!hasBrief} onChange={(e) => setResearch(e.target.checked)} />
              <span>Research the changes first, from public sources. Slower, but new equipment is based on evidence, not a guess.</span>
            </label>
          )}
          {isShip && (
            <div style={{ display: 'grid', gridTemplateColumns: '120px minmax(0, 1fr)', gap: 10, alignItems: 'center' }}>
              <label htmlFor="variant-scene" className="faint">Bring the crew</label>
              <select id="variant-scene" className="field dark" value={scene} onChange={(e) => setScene(e.target.value)} disabled={!onThisShip.length}>
                <option value="">{onThisShip.length ? 'No, ship only' : 'No scenes on this ship'}</option>
                {onThisShip.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.crew.length} crew)</option>)}
              </select>
            </div>
          )}
          <div className="faint" style={{ lineHeight: 1.5, fontSize: 12 }}>
            {hasBrief
              ? `You'll watch it being made next to the original: first the exact copy, then the AI ${isShip && research ? 'researches and ' : ''}makes the changes.`
              : 'With nothing described, it is an exact copy.'}
            {' Nothing is kept until you approve it.'}
            {isShip && scene && ' On approval the scene is copied onto the variant with the same crew; anyone whose crew point is gone is flagged in Assembly.'}
          </div>
          <ErrorNote error={err} />
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button className="btn lg" onClick={onCancel}>Cancel</button>
            <button className="btn primary lg" onClick={() => void submit()} disabled={sending || version === null || !built.length}>
              {sending ? 'Starting…' : 'Make variant'}
            </button>
          </div>
        </>}
      </div>
    </div>
  );
}
