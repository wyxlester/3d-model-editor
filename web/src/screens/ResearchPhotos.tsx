// Research tabs 01 Photographs and 03 Close-ups (CONTRACT §12 "Photographs and close-ups"), to the
// standard of the PCG 3-Series dossier's reference atlas and magnified evidence (Lester, 29 Oct:
// research "doesn't render photos and close up for me").
import { useState } from 'react';
import { STATUS_TEXT, countLine, coverageSummary, filterPhotos, photoStatus, statusOptions, viewLabel, viewOptions, type StatusFilter } from '../lib/photos';
import type { Research } from '../types';
import type { InspectTarget } from '../components/PhotoInspector';

export const findPhotosPrompt = (subject: string) => `Find photographs of ${subject} and add them with provenance`;
export const CROP_PROMPT = 'Crop close-ups of the fittings that change the model';

function Head({ n, eyebrow, title, sub }: { n: string; eyebrow: string; title: string; sub: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span className="eyebrow">{n} · {eyebrow}</span>
      <h1 className="title">{title}</h1>
      <p className="muted" style={{ margin: 0, lineHeight: 1.55, maxWidth: 820 }}>{sub}</p>
    </div>
  );
}

// Lester, 29 Oct: "whenever user prompt, ai should already start finding and populating relevant
// photos". Every research request gathers photographs itself (the server follows a research turn
// with a photo turn when there are too few), so these are small secondary links, not the way in.
function Ask({ label, message, onAsk, busy }: { label: string; message: string; onAsk: (m: string) => void; busy: boolean }) {
  return (
    <button className="btn sm" style={{ alignSelf: 'flex-start' }} disabled={busy} onClick={() => onAsk(message)}
      title={`Sends to the research AI: “${message}”`}>{label}</button>
  );
}

function Gathering({ busy, what }: { busy: boolean; what: string }) {
  return busy
    ? <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}><span className="spinner" aria-hidden="true" />Finding {what}… they appear here as the research runs.</span>
    : <span className="muted">The AI gathers {what} with every research request. Ask it anything in the chat and they arrive with the answer.</span>;
}

export function PhotosTab({ r, subject, selected, onSelect, onInspect, onAsk, busy }: {
  r: Research;
  subject: string;
  selected: string | null;
  onSelect: (id: string) => void;
  /** Opens the inspector; `order` is what Previous / Next step through. */
  onInspect: (t: InspectTarget, order: string[]) => void;
  onAsk: (m: string) => void;
  busy: boolean;
}) {
  const [view, setView] = useState('all');
  const [status, setStatus] = useState<StatusFilter>('all');
  const photos = r.photos ?? [];
  const head = <Head n="01" eyebrow="Reference atlas" title="Photographs you can inspect"
    sub="Click a photograph to see it large, then zoom and drag. Each one records where it came from, who took it, when, and what it is used for in the model. Republished copies and crops do not add to the count." />;

  if (!photos.length) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18, paddingTop: 12 }}>
        {head}
        <div className="card ph-empty">
          <span style={{ fontWeight: 500 }}>No photographs yet</span>
          <Gathering busy={busy} what="photographs" />
          <span className="faint" style={{ fontSize: 12 }}>Each one is downloaded, measured and fingerprinted, so a copy is kept even if the web page
            changes, and shown with its credit, date, source and rights.</span>
          {!busy && <Ask label="Look for photographs now" message={findPhotosPrompt(subject)} onAsk={onAsk} busy={busy} />}
        </div>
      </div>
    );
  }

  const shown = filterPhotos(photos, view, status);
  const order = shown.map((x) => x.photo.id);
  const line = countLine(r.photo_count, r.count_method, photos.length);
  const open = (id: string, ids = order) => { onSelect(id); onInspect({ kind: 'photo', id }, ids.includes(id) ? ids : photos.map((p) => p.id)); };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, paddingTop: 12 }}>
      {head}
      <div className="ph-count">
        <b>{line.count}</b>
        <span className="muted" style={{ fontSize: 12, lineHeight: 1.5 }}>Counted by: {line.method}</span>
        {busy ? <span className="faint" style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}><span className="spinner" aria-hidden="true" />Research running: new photographs appear here when it finishes.</span>
          : <Ask label="Find more photographs" message={findPhotosPrompt(subject)} onAsk={onAsk} busy={busy} />}
      </div>
      <Coverage r={r} onOpen={(id) => open(id, photos.map((p) => p.id))} />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div className="ph-filters" role="group" aria-label="Filter photographs by view">
          <span className="lbl">View</span>
          {viewOptions(photos).filter((o) => o.key !== 'interior' || o.count > 0).map((o) => (
            <button key={o.key} className={`btn sm pill ${view === o.key ? 'selected' : 'muted'}`} aria-pressed={view === o.key} onClick={() => setView(o.key)}>
              {o.label} <span className="n">{o.count}</span>
            </button>
          ))}
        </div>
        <div className="ph-filters" role="group" aria-label="Filter photographs by how they may be used">
          <span className="lbl">Status</span>
          {statusOptions(photos).map((o) => (
            <button key={o.key} className={`btn sm pill ${status === o.key ? 'selected' : 'muted'}`} aria-pressed={status === o.key} onClick={() => setStatus(o.key)}
              title={o.key === 'all' ? undefined : STATUS_TEXT[o.key].means}>
              {o.label} <span className="n">{o.count}</span>
            </button>
          ))}
        </div>
        <span className="faint" style={{ fontSize: 12 }} aria-live="polite">
          {shown.length === photos.length ? `Showing all ${photos.length}` : `Showing ${shown.length} of ${photos.length}`}
        </span>
      </div>
      {shown.length === 0 ? (
        <div className="card muted" style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          No photograph matches {view === 'all' ? '' : `the ${viewLabel(view).toLowerCase()} view `}{status === 'all' ? '' : `with status ${STATUS_TEXT[status].label.toLowerCase()}`}.
          <button className="btn sm" onClick={() => { setView('all'); setStatus('all'); }}>Show all</button>
        </div>
      ) : (
        <div className="ph-gallery">
          {shown.map(({ photo: p }) => {
            const st = STATUS_TEXT[photoStatus(p)];
            return (
              <button key={p.id} className={`ph-card ${selected === p.id ? 'on' : ''} ${photoStatus(p) === 'excluded' ? 'excluded' : ''}`}
                onClick={() => open(p.id)} aria-label={`Inspect ${p.id}: ${p.title}`}>
                <span className="ph-thumb">
                  {p.file ? <img src={p.file} alt="" loading="lazy" /> : <span>Not stored by the server</span>}
                  <span className="ph-id">{p.id}</span>
                  <span className={`chip ${st.tone}`}>{st.label}</span>
                  {p.file && <span className="ph-zoom">Inspect ⤢</span>}
                </span>
                <span className="ph-body">
                  <span className="t">{p.title}</span>
                  {(p.views ?? []).length > 0 && <span className="ph-tags">{p.views!.map((v) => <span key={v}>{viewLabel(v)}</span>)}</span>}
                  {p.observation && <span className="obs">{p.observation}</span>}
                  <span className="credit">{[p.vessel, p.credit].filter(Boolean).join(' · ') || 'Credit not recorded'}</span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Six views, always all six: a view without a photo says "missing" in amber (a finding, not a failure). */
function Coverage({ r, onOpen }: { r: Research; onOpen: (id: string) => void }) {
  const { cells, note, stated } = coverageSummary(r.coverage, r.photos ?? []);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div className="ph-coverage" role="list" aria-label="Which views have photographs">
        {cells.map((c) => (
          <div key={c.view} role="listitem" className={`ph-cov ${c.missing ? 'missing' : ''}`}>
            <b>{c.label}{c.missing ? '' : ' ✓'}</b>
            <span className="ids">
              {c.missing ? 'missing' : c.text.split(/(\bP\d+\b)/).map((part, k) => c.ids.includes(part)
                ? <button key={k} onClick={() => onOpen(part)} aria-label={`Inspect ${part}`}>{part}</button>
                : part ? <span key={k}>{part}</span> : null)}
            </span>
          </div>
        ))}
      </div>
      {note && <span className="muted" style={{ fontSize: 12 }}>{note}</span>}
      {!stated && (r.photos ?? []).length > 0 && <span className="faint" style={{ fontSize: 12 }}>Worked out from each photograph’s views; the research does not state its coverage.</span>}
    </div>
  );
}

export function CloseupsTab({ r, selected, onSelect, onInspect, onAsk, busy, subject }: {
  r: Research;
  subject: string;
  selected: string | null;
  onSelect: (id: string) => void;
  onInspect: (t: InspectTarget, order: string[]) => void;
  onAsk: (m: string) => void;
  busy: boolean;
}) {
  const crops = r.closeups ?? [];
  const photos = r.photos ?? [];
  const head = <Head n="03" eyebrow="Magnified evidence" title="Small fittings that change the model"
    sub="Each close-up is a crop of a recorded photograph, with no generated or enhanced detail. It says what it shows and which findings it supports; “from P01” opens the original with the crop outlined." />;
  if (!crops.length) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18, paddingTop: 12 }}>
        {head}
        <div className="card ph-empty">
          <span style={{ fontWeight: 500 }}>No close-ups yet</span>
          {photos.length ? (
            <>
              <Gathering busy={busy} what="close-ups" />
              <span className="faint" style={{ fontSize: 12 }}>Each is cut from a stored photograph; nothing is redrawn.</span>
              {!busy && <Ask label="Crop close-ups now" message={CROP_PROMPT} onAsk={onAsk} busy={busy} />}
            </>
          ) : (
            <>
              <Gathering busy={busy} what="photographs and close-ups" />
              {!busy && <Ask label="Look for photographs now" message={findPhotosPrompt(subject)} onAsk={onAsk} busy={busy} />}
            </>
          )}
        </div>
      </div>
    );
  }
  const order = crops.map((c) => c.id);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, paddingTop: 12 }}>
      {head}
      <div className="cu-grid">
        {crops.map((c, i) => {
          const src = photos.find((p) => p.id === c.photo);
          return (
            <figure key={c.id} className={`cu-card ${selected === c.id ? 'on' : ''}`}>
              <button className="cu-img" onClick={() => { onSelect(c.id); onInspect({ kind: 'closeup', id: c.id }, order); }} aria-label={`Inspect close-up ${c.id}: ${c.title}`}>
                {c.file ? <img src={c.file} alt="" loading="lazy" /> : <span>Not cut by the server yet</span>}
              </button>
              <figcaption>
                <h3><span className="mono faint" style={{ fontSize: 12 }}>{String(i + 1).padStart(2, '0')} · {c.id}</span> {c.title}</h3>
                <span className="muted">{c.what_it_shows || 'What this shows is not recorded.'}</span>
                {(c.claims ?? []).length > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <span className="faint" style={{ fontSize: 12 }}>Supports</span>
                    <ul>{c.claims!.map((t, k) => <li key={k}>{t}</li>)}</ul>
                  </div>
                )}
                <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', paddingTop: 8, borderTop: '1px solid var(--border)' }}>
                  <button className="btn sm" disabled={!src}
                    onClick={() => { onSelect(c.id); onInspect({ kind: 'photo', id: c.photo, focus: c.id }, photos.map((p) => p.id)); }}
                    title={src ? 'Opens the original photograph with this crop outlined' : 'That photograph is not in the research'}>
                    from {c.photo}
                  </button>
                  <span className="faint" style={{ fontSize: 12, minWidth: 0, overflowWrap: 'anywhere' }}>{src ? [src.vessel, src.credit].filter(Boolean).join(' · ') : 'not in the research'}</span>
                </span>
              </figcaption>
            </figure>
          );
        })}
      </div>
    </div>
  );
}
