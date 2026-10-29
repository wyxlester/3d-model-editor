// The research photo inspector (CONTRACT §12): a large view of a stored photo or close-up with
// zoom (wheel, buttons, slider, + and - keys) and drag-pan (pointer or arrow keys), Esc to close,
// beside the record of where it came from. Modelled on the PCG 3-Series dossier's viewer.
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { STATUS_TEXT, ZOOM_MAX, ZOOM_MIN, boxText, boxToPercent, clampPan, clampZoom, fitSize, photoStatus, provenanceRows, viewLabel, zoomAt } from '../lib/photos';
import type { Research, ResearchCloseup, ResearchPhoto } from '../types';

export type InspectTarget = { kind: 'photo'; id: string; focus?: string } | { kind: 'closeup'; id: string };

type Pt = { x: number; y: number };

export function PhotoInspector({ r, target, order, onOpen, onClose }: {
  r: Research;
  target: InspectTarget;
  /** Ids to step through with Previous / Next (the gallery's current filter, or the close-ups). */
  order: string[];
  onOpen: (t: InspectTarget) => void;
  onClose: () => void;
}) {
  const dlg = useRef<HTMLDialogElement>(null);
  const photos = r.photos ?? [];
  const closeups = r.closeups ?? [];
  const photo = target.kind === 'photo' ? photos.find((p) => p.id === target.id) : undefined;
  const crop = target.kind === 'closeup' ? closeups.find((c) => c.id === target.id) : undefined;
  const parent = crop ? photos.find((p) => p.id === crop.photo) : undefined;

  useEffect(() => {
    const d = dlg.current;
    const back = document.activeElement as HTMLElement | null;
    if (d && !d.open) d.showModal();
    d?.querySelector<HTMLElement>('.insp-view')?.focus(); // + and - work at once
    return () => { if (d?.open) d.close(); back?.focus?.(); };
  }, []);

  const at = order.indexOf(target.id);
  const step = (k: number) => {
    const id = order[(at + k + order.length) % order.length];
    if (id) onOpen({ kind: target.kind, id });
  };

  const title = photo ? photo.title : crop ? crop.title : 'Not found';
  const src = photo?.file ?? crop?.file;
  const natural = photo ? { w: photo.width ?? 0, h: photo.height ?? 0 } : crop?.box ? { w: crop.box[2], h: crop.box[3] } : { w: 0, h: 0 };
  // Outlines of the close-ups cut from this photo; the one asked for is emphasised.
  const outlines = photo ? closeups.filter((c) => c.photo === photo.id).map((c) => ({ c, o: boxToPercent(c.box, photo.width, photo.height) })) : [];

  return (
    <dialog ref={dlg} className="insp" aria-labelledby="insp-title"
      onCancel={(e) => { e.preventDefault(); onClose(); }}
      onClick={(e) => { if (e.target === dlg.current) onClose(); }}>
      <div className="insp-shell">
        <div className="insp-top">
          <span className="mono insp-id">{target.id}</span>
          <h2 id="insp-title">{title}</h2>
          {order.length > 1 && at >= 0 && <>
            <button className="btn sm" onClick={() => step(-1)} aria-label="Previous">‹ Previous</button>
            <span className="faint" style={{ fontSize: 12 }}>{at + 1} of {order.length}</span>
            <button className="btn sm" onClick={() => step(1)} aria-label="Next">Next ›</button>
          </>}
          <button className="btn sm" onClick={onClose} aria-label="Close the inspector">Close <span className="faint">Esc</span></button>
        </div>
        <ZoomView key={`${target.kind}:${target.id}`} src={src} natural={natural} alt={title}
          outlines={outlines.map(({ c, o }) => ({ id: c.id, title: c.title, o, on: target.kind === 'photo' && target.focus === c.id }))}
          onOutline={(id) => onOpen({ kind: 'closeup', id })} />
        <aside className="insp-side scroll">
          {photo && <PhotoRecord p={photo} crops={outlines.map(({ c }) => c)} focus={target.kind === 'photo' ? target.focus : undefined} onOpen={onOpen} />}
          {crop && <CloseupRecord c={crop} parent={parent} onOpen={onOpen} />}
          {!photo && !crop && <span className="muted">This record is no longer in the research.</span>}
        </aside>
      </div>
    </dialog>
  );
}

function PhotoRecord({ p, crops, focus, onOpen }: { p: ResearchPhoto; crops: ResearchCloseup[]; focus?: string; onOpen: (t: InspectTarget) => void }) {
  const st = STATUS_TEXT[photoStatus(p)];
  return (
    <>
      <span className="faint" style={{ fontSize: 12 }}>Provenance</span>
      <span className={`chip ${st.tone}`} style={{ alignSelf: 'flex-start' }}>{st.label}</span>
      <span className="muted" style={{ fontSize: 12, lineHeight: 1.5 }}>{st.means}</span>
      <dl className="prov">
        {provenanceRows(p).filter((row) => row.label !== 'Status').map((row) => (
          <div key={row.label}>
            <dt>{row.label}</dt>
            <dd className={`${row.mono ? 'mono' : ''} ${row.missing ? 'faint' : ''}`} title={row.title}>
              {row.href ? <a href={row.href} target="_blank" rel="noreferrer noopener">{row.value} ↗</a> : row.value}
            </dd>
          </div>
        ))}
      </dl>
      {crops.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
          <span className="faint" style={{ fontSize: 12 }}>Close-ups cut from this photo (outlined)</span>
          {crops.map((c) => (
            <button key={c.id} className={`row-btn ${focus === c.id ? 'on' : ''}`} onClick={() => onOpen({ kind: 'closeup', id: c.id })}>
              <span className="mono faint">{c.id}</span><span>{c.title}</span>
            </button>
          ))}
        </div>
      )}
    </>
  );
}

function CloseupRecord({ c, parent, onOpen }: { c: ResearchCloseup; parent?: ResearchPhoto; onOpen: (t: InspectTarget) => void }) {
  return (
    <>
      <span className="faint" style={{ fontSize: 12 }}>Close-up · a crop of a recorded photograph, with no generated detail</span>
      <button className="btn" style={{ alignSelf: 'flex-start' }} onClick={() => onOpen({ kind: 'photo', id: c.photo, focus: c.id })}
        title="Opens the source photo with this crop outlined">
        From {c.photo}{parent ? ` · ${parent.title}` : ' (not in the research)'}
      </button>
      <dl className="prov">
        <div><dt>What it shows</dt><dd className={c.what_it_shows ? '' : 'faint'}>{c.what_it_shows || 'not recorded'}</dd></div>
        <div><dt>Supports</dt><dd>{(c.claims ?? []).length ? <ul className="claims">{c.claims!.map((t, i) => <li key={i}>{t}</li>)}</ul> : <span className="faint">no claim named</span>}</dd></div>
        <div><dt>Crop</dt><dd className="mono">{boxText(c.box)}</dd></div>
        {parent && <>
          <div><dt>Credit</dt><dd className={parent.credit ? '' : 'faint'}>{parent.credit || 'not recorded'}</dd></div>
          <div><dt>Rights</dt><dd className={parent.rights ? '' : 'faint'}>{parent.rights || 'not recorded'}</dd></div>
          <div><dt>Views</dt><dd>{(parent.views ?? []).map(viewLabel).join(', ') || '—'}</dd></div>
        </>}
      </dl>
    </>
  );
}

/** The zoomable picture: wheel or +/- to zoom about the pointer, drag or arrow keys to move, 0 to fit. */
function ZoomView({ src, natural, alt, outlines, onOutline }: {
  src?: string; natural: { w: number; h: number }; alt: string;
  outlines: { id: string; title: string; o: ReturnType<typeof boxToPercent>; on: boolean }[];
  onOutline: (id: string) => void;
}) {
  const vp = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ w: 0, h: 0 });
  const [nat, setNat] = useState(natural);
  // One state, updated functionally: a burst of wheel events within a frame must all count.
  const [{ zoom, pan }, setZp] = useState<{ zoom: number; pan: Pt }>({ zoom: 1, pan: { x: 0, y: 0 } });
  const setPan = (p: Pt) => setZp((s) => ({ ...s, pan: p }));
  const [failed, setFailed] = useState(false);
  const drag = useRef<{ x: number; y: number; pan: Pt; id: number } | null>(null);
  const base = fitSize(nat, view);

  // Measured on resize, and again once the dialog has opened (it is still closed, so 0 × 0, on the
  // first layout) and when the image loads, so the picture never waits on a resize to appear.
  const measure = useCallback(() => {
    const el = vp.current;
    if (el) setView((v) => (v.w === el.clientWidth && v.h === el.clientHeight ? v : { w: el.clientWidth, h: el.clientHeight }));
  }, []);
  useLayoutEffect(() => {
    const el = vp.current;
    if (!el) return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    measure();
    const t = setTimeout(measure, 0);
    return () => { ro.disconnect(); clearTimeout(t); };
  }, [measure]);

  const zoomBy = useCallback((factor: number, at: Pt = { x: 0, y: 0 }) => {
    setZp((s) => zoomAt(s.zoom, s.pan, factor, at, base, view));
  }, [base.w, base.h, view]); // eslint-disable-line react-hooks/exhaustive-deps
  const setZoomTo = (z: number) => setZp((s) => zoomAt(s.zoom, s.pan, clampZoom(z) / s.zoom, { x: 0, y: 0 }, base, view));
  const fit = () => setZp({ zoom: 1, pan: { x: 0, y: 0 } });

  // React's wheel listener is passive; this one must stop the page scrolling, so it is attached by hand.
  const wheel = useRef(zoomBy);
  wheel.current = zoomBy;
  useEffect(() => {
    const el = vp.current;
    if (!el) return;
    const on = (e: WheelEvent) => {
      e.preventDefault();
      measure();
      const b = el.getBoundingClientRect();
      wheel.current(Math.exp(-e.deltaY * 0.0015), { x: e.clientX - b.left - b.width / 2, y: e.clientY - b.top - b.height / 2 });
    };
    el.addEventListener('wheel', on, { passive: false });
    return () => el.removeEventListener('wheel', on);
  }, []);

  const key = (e: KeyboardEvent) => {
    const k = e.key;
    const move = (dx: number, dy: number) => setPan(clampPan({ x: pan.x + dx, y: pan.y + dy }, zoom, base, view));
    if (k === '+' || k === '=') zoomBy(1.25);
    else if (k === '-' || k === '_') zoomBy(0.8);
    else if (k === '0') fit();
    else if (k === 'ArrowLeft') move(60, 0);
    else if (k === 'ArrowRight') move(-60, 0);
    else if (k === 'ArrowUp') move(0, 60);
    else if (k === 'ArrowDown') move(0, -60);
    else return;
    e.preventDefault();
  };

  return (
    <>
      <div ref={vp} className={`insp-view ${zoom > 1 ? 'can-drag' : ''}`} tabIndex={0} role="group"
        aria-label={`${alt}. Zoom with + and −, move with the arrow keys, 0 to fit.`}
        onKeyDown={key}
        onDoubleClick={(e) => { const b = e.currentTarget.getBoundingClientRect(); zoomBy(zoom > 1 ? 1 / zoom : 2.5, { x: e.clientX - b.left - b.width / 2, y: e.clientY - b.top - b.height / 2 }); }}
        onPointerDown={(e) => {
          if ((e.target as HTMLElement).closest('.insp-box')) return;
          drag.current = { x: e.clientX, y: e.clientY, pan, id: e.pointerId };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d || d.id !== e.pointerId) return;
          setPan(clampPan({ x: d.pan.x + e.clientX - d.x, y: d.pan.y + e.clientY - d.y }, zoom, base, view));
        }}
        onPointerUp={() => { drag.current = null; }}
        onPointerCancel={() => { drag.current = null; }}>
        {!src || failed ? (
          <div className="insp-missing">
            {!src ? 'The server has not stored this image, so there is nothing to show. The record is kept for its provenance; the published link is in the record.'
              : 'The stored image could not be loaded. Try again later; the record is kept.'}
          </div>
        ) : (
          <div className="insp-stage" style={{ width: base.w * zoom, height: base.h * zoom, transform: `translate(calc(-50% + ${pan.x}px), calc(-50% + ${pan.y}px))`, visibility: base.w ? 'visible' : 'hidden' }}>
            <img src={src} alt="" draggable={false}
              onLoad={(e) => { const i = e.currentTarget; if (!nat.w || !nat.h) setNat({ w: i.naturalWidth, h: i.naturalHeight }); measure(); }}
              onError={() => setFailed(true)} />
            {outlines.map(({ id, title, o, on }) => o && (
              <button key={id} className={`insp-box ${on ? 'on' : ''}`} style={{ left: `${o.left}%`, top: `${o.top}%`, width: `${o.width}%`, height: `${o.height}%` }}
                onClick={() => onOutline(id)} title={`${id} · ${title}: open the close-up`} aria-label={`Close-up ${id}: ${title}`}>
                <span>{id}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="insp-controls">
        <button className="btn sm" onClick={() => zoomBy(0.8)} disabled={zoom <= ZOOM_MIN} aria-label="Zoom out">−</button>
        <input type="range" min={ZOOM_MIN} max={ZOOM_MAX} step={0.1} value={zoom} onChange={(e) => setZoomTo(Number(e.target.value))} aria-label="Zoom" style={{ maxWidth: 260 }} />
        <button className="btn sm" onClick={() => zoomBy(1.25)} disabled={zoom >= ZOOM_MAX} aria-label="Zoom in">+</button>
        <span className="mono faint" style={{ width: 48 }}>{Math.round(zoom * 100)}%</span>
        <button className="btn sm" onClick={fit} disabled={zoom === 1 && !pan.x && !pan.y}>Fit</button>
        <span className="faint" style={{ marginLeft: 'auto', fontSize: 12 }}>Scroll or + − to zoom · drag or arrow keys to move · Esc closes</span>
      </div>
    </>
  );
}
