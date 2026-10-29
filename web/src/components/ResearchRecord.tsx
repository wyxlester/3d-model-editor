// The research's record, kept for review afterwards (CONTRACT §12, Kept for review): every search
// and page the AI read, with the text it saw, and every saved version of the research.
import { useState } from 'react';
import { api } from '../api';
import { useLoad } from '../hooks';
import { shortTime } from '../lib/pins';
import { EvidenceChip, ErrorNote, Loading } from './common';

/** Every search and page the research AI used, as it saw them. */
export function ResearchMaterials({ pid }: { pid: string }) {
  const list = useLoad(() => api.materials(pid), [pid]);
  const [open, setOpen] = useState<string | null>(null);
  const one = useLoad(() => (open ? api.material(pid, open) : Promise.resolve(null)), [pid, open]);
  const items = list.data ?? [];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 13 }}>
      <span className="eyebrow">What the AI read</span>
      <span className="faint" style={{ fontSize: 12 }}>Kept with the project, so the research can be checked later against what the AI actually read.</span>
      <ErrorNote error={list.error} retry={list.reload} />
      {!list.loading && !items.length && <span className="faint">Nothing read yet. Searches and pages appear here as the AI uses them.</span>}
      {items.map((m) => (
        <div key={m.id} className="card" style={{ padding: 10 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', minWidth: 0 }}>
            <span className={`chip ${m.kind === 'search' ? 'violet' : 'blue'}`}>{m.kind === 'search' ? 'Search' : 'Page'}</span>
            <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {m.kind === 'page' ? <a href={m.title} target="_blank" rel="noreferrer">{m.title}</a> : m.title}
            </span>
            <span className="faint" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{shortTime(m.at)} · {Math.max(1, Math.round(m.chars / 1000))}k chars</span>
            <button className="btn sm" onClick={() => setOpen(open === m.id ? null : m.id)}>{open === m.id ? 'Hide' : 'Show what it read'}</button>
          </div>
          {open === m.id && (one.data ? <pre className="material-text">{one.data.text}</pre> : <Loading what="the text" />)}
        </div>
      ))}
    </div>
  );
}

/** Each saved version of the research; any one can be read again as it was. */
export function ResearchVersions({ pid }: { pid: string }) {
  const list = useLoad(() => api.researchHistory(pid), [pid]);
  const [n, setN] = useState<number | null>(null);
  const one = useLoad(() => (n ? api.researchVersion(pid, n) : Promise.resolve(null)), [pid, n]);
  const items = list.data ?? [];
  const r = one.data?.research;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 13 }}>
      <span className="eyebrow">Versions of the research</span>
      <ErrorNote error={list.error} retry={list.reload} />
      {!list.loading && !items.length && <span className="faint">No research saved yet.</span>}
      {items.map((h) => (
        <button key={h.n} className={`row-btn ${n === h.n ? 'on' : ''}`} style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 2, padding: '8px 12px', textAlign: 'left' }}
          onClick={() => setN(n === h.n ? null : h.n)}>
          <span><strong>Version {h.n}</strong> <span className="faint">· {shortTime(h.at)} · {h.sources} sources, {h.claims} claims{h.n === items[items.length - 1].n ? ' · current' : ''}</span></span>
          {h.verdict && <span className="muted" style={{ fontSize: 12 }}>{h.verdict}</span>}
        </button>
      ))}
      {n && !r && <Loading what="that version" />}
      {r && (
        <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <span className="chip amber" style={{ alignSelf: 'flex-start' }}>Version {n}, as saved {shortTime(one.data!.at)}</span>
          <strong>{r.subject}</strong>
          <span>{r.summary?.verdict}</span>
          <table className="report-table">
            <thead><tr><th>Claim</th><th>Sources</th><th>Status</th></tr></thead>
            <tbody>{(r.claims ?? []).map((c, i) => (
              <tr key={i}><td>{c.claim}{c.value != null && c.value !== '' ? `: ${String(c.value)}${c.unit ? ` ${c.unit}` : ''}` : ''}</td>
                <td>{Array.isArray(c.sources) ? c.sources.join(', ') : c.sources}</td><td><EvidenceChip status={c.status} /></td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
