// The approved design plan being built, over the model it is building: what the AI is doing now,
// every version so far (click one to look at it), and every step. The model behind it follows the
// newest version as each one lands (CONTRACT §12, Watching the build).
import { useEffect, useRef, useState } from 'react';
import { buildHeadline, buildProgress, shortStep, versionText, type BuildStepKind } from '../lib/buildProgress';
import type { FeedSection } from './ActivityFeed';

const MARK: Record<BuildStepKind, string> = { building: 'Build', built: 'Built', failed: 'Failed', detail: 'Detail', ai: 'AI', other: 'Step' };
const CHIP: Record<BuildStepKind, string> = { building: 'blue', built: 'green', failed: 'red', detail: 'violet', ai: '', other: '' };

export function BuildLive({ section, since, shown, onShow, onClose }: {
  section: FeedSection;
  /** When the job was created, if known (it may have started before this screen opened). */
  since?: number;
  /** The version on screen. */
  shown: number;
  onShow: (n: number) => void;
  onClose: () => void;
}) {
  const [open, setOpen] = useState(true);
  const listRef = useRef<HTMLOListElement>(null);
  const p = buildProgress(section.log);
  const running = section.phase === 'running';
  useEffect(() => { listRef.current?.scrollTo({ top: listRef.current.scrollHeight }); }, [p.steps.length, open]);
  const end = section.endedAt ?? Date.now();
  const secs = Math.max(0, Math.round((end - (since ?? section.startedAt)) / 1000));
  const elapsed = secs < 60 ? `${secs} s` : `${Math.floor(secs / 60)} min ${secs % 60} s`;
  const built = p.versions.filter((v) => v.ok).length;
  const counts = [built && `${built} version${built === 1 ? '' : 's'} built`, p.details && `${p.details} detail${p.details === 1 ? '' : 's'} added`]
    .filter(Boolean).join(' · ');

  return (
    <div className="card build-live" role="status" aria-live="polite"
      onPointerDown={(e) => e.stopPropagation()} onWheel={(e) => e.stopPropagation()}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="eyebrow">{running ? 'Building the approved plan' : section.phase === 'error' ? 'The build stopped' : 'Built from the approved plan'} · {elapsed}</span>
        {!running && <button className="btn sm muted" style={{ marginLeft: 'auto' }} onClick={onClose}>Close</button>}
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start', fontSize: 15, fontWeight: 500 }}>
        {running && <span className="spinner" style={{ flex: 'none', marginTop: 3 }} />}
        <span style={{ overflowWrap: 'anywhere' }}>{section.phase === 'error' ? 'The build did not finish' : buildHeadline(p, running)}</span>
      </div>
      {section.phase === 'error' && <span className="bad" style={{ fontSize: 12 }}>{section.error}</span>}
      {p.versions.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }} aria-label="Versions built so far">
          {p.versions.map((v) => (
            <button key={v.n} className={`btn sm ${v.n === shown ? 'selected' : 'muted'}`} disabled={!v.ok}
              title={`v${v.n}: ${versionText(v)}${v.ok ? '' : ' (did not build)'}`} onClick={() => onShow(v.n)}>
              {v.ok ? '✓' : '✗'} v{v.n}
            </button>
          ))}
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'center', fontSize: 12 }}>
        <span className="faint">{counts || 'The hull and superstructure come first, then the details a group at a time.'}</span>
        {p.steps.length > 0 && <button className="btn sm muted" style={{ marginLeft: 'auto', height: 24 }} onClick={() => setOpen(!open)}>{open ? 'Hide steps' : 'Show steps'}</button>}
      </div>
      {open && p.steps.length > 0 && (
        <ol ref={listRef} className="scroll" aria-label="Build steps so far"
          style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 5, maxHeight: 240, overflowX: 'hidden', fontSize: 12 }}>
          {p.steps.map((s, i) => (
            <li key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', minWidth: 0 }}>
              <span className={`chip ${CHIP[s.kind]}`} style={{ flex: 'none', width: 50, justifyContent: 'center', fontSize: 10 }}>{MARK[s.kind]}</span>
              <span title={s.text.length > 160 ? s.text : undefined}
                style={{ minWidth: 0, overflowWrap: 'anywhere', color: s.kind === 'ai' ? 'var(--text-2)' : 'var(--text)', fontStyle: s.kind === 'ai' ? 'italic' : undefined }}>{shortStep(s.text)}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
