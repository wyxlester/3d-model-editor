// What the research AI is doing right now, while a research turn runs: the step under way in large
// type, a tally of searches, pages and photographs, and every step so far (CONTRACT §10, "• " lines).
// Shown in place of the empty Research page before the first research arrives, and as a strip above
// the report while a later turn runs.
import { useEffect, useRef, useState } from 'react';
import { headline, researchProgress, tally, type StepKind } from '../lib/researchProgress';
import type { FeedSection } from './ActivityFeed';

const MARK: Record<StepKind, string> = { search: 'Search', read: 'Read', photo: 'Photo', save: 'Saved', ai: 'AI', other: 'Step' };
const CHIP: Record<StepKind, string> = { search: 'violet', read: 'blue', photo: 'green', save: 'amber', ai: '', other: '' };

function Steps({ section, fill }: { section: FeedSection; fill: boolean }) {
  const p = researchProgress(section.log);
  const ref = useRef<HTMLOListElement>(null);
  useEffect(() => { ref.current?.scrollTo({ top: ref.current.scrollHeight }); }, [p.steps.length]);
  if (!p.steps.length) return null;
  return (
    <ol ref={ref} className="scroll" aria-label="Research steps so far"
      style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6, maxHeight: fill ? 360 : 220, overflowX: 'hidden', fontSize: 13 }}>
      {p.steps.map((s, i) => {
        const last = i === p.steps.length - 1 && section.phase === 'running';
        return (
          <li key={i} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', minWidth: 0 }}>
            <span className={`chip ${CHIP[s.kind]}`} style={{ flex: 'none', width: 56, justifyContent: 'center', fontSize: 11 }}>{MARK[s.kind]}</span>
            <span style={{ minWidth: 0, overflowWrap: 'anywhere', color: s.kind === 'ai' ? 'var(--text-2)' : 'var(--text)', fontStyle: s.kind === 'ai' ? 'italic' : undefined }}>
              {s.kind === 'ai' ? s.detail : s.text}
            </span>
            {last && <span className="spinner" style={{ flex: 'none', marginTop: 3, marginLeft: 'auto' }} />}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * The running research turns on this ship. `full` fills the page (no research yet); otherwise a strip
 * above the report, with the steps one click away.
 */
export function ResearchLive({ sections, full, since, above }: {
  sections: FeedSection[]; full: boolean; since: Record<string, number>;
  /** Shown above a report that is still filling in (photographs saved, findings not yet), not in place of one. */
  above?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const section = sections[sections.length - 1];
  if (!section) return null;
  const p = researchProgress(section.log);
  // From when the job was created (it may have started before this screen opened).
  const secs = Math.max(0, Math.round((Date.now() - (since[section.jobId] ?? section.startedAt)) / 1000));
  const elapsed = secs < 60 ? `${secs} s` : `${Math.floor(secs / 60)} min ${secs % 60} s`;
  const queued = !section.log.length;
  const now = queued ? 'Waiting for the work before it on this ship to finish' : headline(p.now);
  const counts = tally(p);

  if (full) {
    return (
      <div role="status" aria-live="polite" style={{ margin: above ? '20px 0 0' : '48px auto 0', width: '100%', maxWidth: above ? undefined : 680, display: 'flex', flexDirection: 'column', gap: 14 }}>
        <span className="eyebrow">Researching now · {elapsed}</span>
        <h1 className="title" style={{ display: 'flex', gap: 12, alignItems: 'flex-start', overflowWrap: 'anywhere' }}>
          <span className="spinner" style={{ flex: 'none', marginTop: 8 }} />{now}
        </h1>
        {counts && <span style={{ fontSize: 14 }}>So far: {counts}</span>}
        <span className="muted" style={{ fontSize: 13 }}>
          The AI reads your description and photos, then searches for references. The report shows here when the research is
          saved, and the design plan appears on the right for you to check. Nothing is built until you approve it.
        </span>
        <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <span className="faint" style={{ fontSize: 12 }}>Every step so far</span>
          {p.steps.length ? <Steps section={section} fill /> : <span className="faint" style={{ fontSize: 13 }}>No steps yet.</span>}
        </div>
      </div>
    );
  }
  return (
    <div className="card" role="status" aria-live="polite" style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 10, borderColor: 'var(--accent)' }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', minWidth: 0 }}>
        <span className="spinner" style={{ flex: 'none' }} />
        <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          <span className="eyebrow" style={{ marginRight: 8 }}>Researching now</span>{now}
        </span>
        <span className="faint" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{[counts, elapsed].filter(Boolean).join(' · ')}</span>
        {p.steps.length > 0 && <button className="btn sm" onClick={() => setOpen(!open)}>{open ? 'Hide steps' : 'Show steps'}</button>}
      </div>
      {open && <Steps section={section} fill={false} />}
    </div>
  );
}
