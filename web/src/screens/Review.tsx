// Review and export: show the version the user picks, pin customer feedback on the model,
// export the scene's package, and build it in Unreal (checked) and open it there.
// Recreates Review.dc.html (CONTRACT §8–10).
import { Suspense, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import type * as THREE from 'three';
import { useParams } from 'react-router-dom';
import { api, errorText, fileUrl, modelUrl } from '../api';
import { ChatPanel, ErrorNote, JobLines, Loading, TopBar } from '../components/common';
import { remember, useJobs, useLoad, useProject, useProjects } from '../hooks';
import { crewOnVersion, stationWords } from '../lib/sceneCrew';
import type { ViewName } from '../lib/frames';
import { PANEL_KEEP, PANEL_MIN, dragHeight } from '../lib/panel';
import { partLabel } from '../lib/parts';
import { paths } from '../lib/routing';
import type { Attachment, ExportResult, Pin, Scene, UnrealReport, Vec3 } from '../types';
import { AttachTo, CrewModel, PIN_COLORS, ShipViewer, ViewButtons, stationObjects } from '../viewer/Viewer';
import { ScenePicker } from './AssemblyScenePicker';
import { specOf } from './Assembly';

const CONSOLE_KEY = 'me3d-review-console-height';
const CONSOLE_DEFAULT = 220;

function readConsoleH(): number {
  try {
    const v = Number(localStorage.getItem(CONSOLE_KEY));
    return Number.isFinite(v) && v >= PANEL_MIN ? v : CONSOLE_DEFAULT;
  } catch {
    return CONSOLE_DEFAULT;
  }
}

/** Inside the viewer: tells the page which crew points the loaded version has (they can differ
 * between versions), so it can say which crew members it could not place. */
function StationsSeen({ root, onSeen }: { root: THREE.Object3D; onSeen: (names: string[]) => void }) {
  // eslint-disable-next-line react-hooks/exhaustive-deps -- once per loaded model
  useEffect(() => { onSeen(stationObjects(root).map((n) => n.name)); }, [root]);
  return null;
}

const STATUS_TEXT: Record<string, string> = { open: 'Open', working: 'AI working', waiting: 'Waiting for you', resolved: 'Done' };
const STATUS_CHIP: Record<string, string> = { open: 'amber', working: 'blue', waiting: 'blue', resolved: 'green' };

export function Review() {
  const { sceneId } = useParams();
  if (!sceneId) return <ScenePicker to={paths.review} title="Review and export" />;
  return <ReviewScene key={sceneId} sceneId={sceneId} />;
}

interface Draft { part: string; point: Vec3; left: number; top: number }

function ReviewScene({ sceneId }: { sceneId: string }) {
  const scene = useLoad<Scene>(() => api.scene(sceneId), [sceneId]);
  const s = scene.data;
  const ship = useProject(s?.ship.project);
  const { jobs, run, dismiss, busy } = useJobs();
  const [shown, setShown] = useState<number | null>(null);   // the one version on screen
  // The scene's crew stand on whichever version is shown; these are the crew points it has.
  const [stationsSeen, setStationsSeen] = useState<{ version: number | null; names: string[] } | null>(null);
  const projects = useProjects();
  const [view, setView] = useState<ViewName>('port');
  const [pinMode, setPinMode] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [draftText, setDraftText] = useState('');
  const [selPin, setSelPin] = useState<string | null>(null);
  const [target, setTarget] = useState<'unreal' | 'mak'>('unreal');
  const [exported, setExported] = useState<ExportResult | null>(null);
  const [makNote, setMakNote] = useState(false);
  const [report, setReport] = useState<UnrealReport | null>(null);
  const [opening, setOpening] = useState(false);
  const [openedLevel, setOpenedLevel] = useState<string | null>(null);
  // The export console under the viewers: its own height, dragged at its top edge, remembered.
  const stageRef = useRef<HTMLElement>(null);
  const [consoleH, setConsoleH] = useState(readConsoleH);
  const [resizing, setResizing] = useState(false);
  const stageHeight = () => stageRef.current?.clientHeight ?? window.innerHeight;
  const keepConsoleH = (h: number) => {
    setConsoleH(h);
    try { localStorage.setItem(CONSOLE_KEY, String(h)); } catch { /* per-viewer nicety only */ }
  };
  const startResize = (e: ReactPointerEvent<HTMLDivElement>) => {
    const el = e.currentTarget, startY = e.clientY, startH = consoleH, total = stageHeight();
    el.setPointerCapture(e.pointerId);
    setResizing(true);
    let last = startH;
    const move = (ev: PointerEvent) => { last = dragHeight(startH, startY, ev.clientY, total); setConsoleH(last); };
    const up = () => {
      el.removeEventListener('pointermove', move); el.removeEventListener('pointerup', up); el.removeEventListener('pointercancel', up);
      setResizing(false); keepConsoleH(last);
    };
    el.addEventListener('pointermove', move); el.addEventListener('pointerup', up); el.addEventListener('pointercancel', up);
  };
  const [feedback, setFeedback] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const presentRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<HTMLDivElement>(null);

  useEffect(() => { remember('scene', sceneId); }, [sceneId]);

  const p = ship.data;
  const versions = p?.versions ?? [];
  // Default: the latest version.
  useEffect(() => {
    if (!p || shown !== null) return;
    const ns = p.versions.map((v) => v.n);
    setShown(ns[ns.length - 1] ?? p.version);
  }, [p, shown]);

  if (scene.error) return <><TopBar /><div style={{ padding: 24 }}><ErrorNote error={scene.error} retry={scene.reload} /></div></>;
  if (!s) return <><TopBar /><div className="viewer"><Loading what="the scene" /></div></>;

  const info = versions.find((v) => v.n === shown);
  const crewName = (project: string) => projects.data?.find((x) => x.id === project)?.name ?? project;
  const seen = stationsSeen?.version === shown ? stationsSeen.names : null;   // null until this version has loaded
  const onCrew = seen ? crewOnVersion(s.crew, seen) : null;
  const pins = p?.pins ?? [];
  const partPaths = (p?.parts ?? []).map((x) => x.path);

  const reloadShip = () => { void ship.reload(); };
  const chat = (text: string, atts: Attachment[] = []) => p ? run('Working on it', () => api.chat(p.id, text, 'build', atts.map((a) => a.id)), reloadShip) : undefined;

  const sendPin = () => {
    if (!p || !draft || !draftText.trim()) return;
    const d = draft; const text = draftText.trim();
    setDraft(null); setDraftText(''); setPinMode(false);
    void run('Sending the customer’s comment to the AI', async () => {
      const r = await api.addPin(p.id, { part: d.part, point: d.point, text, author: 'Customer' });
      setSelPin(r.pin.id);
      await ship.reload();
      return { job_id: r.job_id };
    }, reloadShip);
  };

  const pinAction = (pin: Pin) => {
    if (!p) return;
    if (pin.status === 'open') {
      // Reload once the job starts, so the card shows "AI working" with its spinner straight away.
      void run('Sending to the AI', async () => { const r = await api.replyPin(p.id, pin.id, 'Please make this change.'); reloadShip(); return r; }, reloadShip, reloadShip);
    } else if (pin.status === 'waiting') {
      api.setPinStatus(p.id, pin.id, 'resolved').then(reloadShip, (e) => setErr(errorText(e)));
    } else if (pin.status === 'resolved' && pin.after_version != null) {
      setShown(pin.after_version);   // the version the change made
    }
  };
  const actionLabel = (pin: Pin) => ({ open: 'Send to AI', working: 'Working…', waiting: 'Keep it', resolved: 'View change' } as Record<string, string>)[pin.status] ?? 'Open';

  const doExport = () => {
    setExported(null); setMakNote(false);
    if (target === 'mak') { setMakNote(true); return; }
    void run<ExportResult>('Exporting the package', () => api.exportScene(s.id), (r) => setExported(r));
  };
  // One click: build the level (the Unreal test, with its checks), then open it in the editor.
  const buildAndOpen = () => {
    setReport(null); setOpenedLevel(null);
    void run<UnrealReport>('Building the scene in Unreal 5.8, then opening it', () => api.unrealTest(s.id),
      async (r) => { setReport(r); await openInUnreal(r.level); });
  };
  const openInUnreal = async (level: string) => {
    setOpening(true); setErr(null);
    try {
      await api.unrealOpen(level);
      setOpenedLevel(level);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setOpening(false);
    }
  };
  const adoptVersion = async (n: number) => {
    try {
      const r = await api.updateScene(s.id, { ...specOf(s), ship: { ...s.ship, version: n } });
      scene.setData(r.scene);
    } catch (e) { setErr(errorText(e)); }
  };

  const present = () => {
    const el = presentRef.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else void el.requestFullscreen?.();
  };

  const shipId = p?.spec.id ?? s.ship.project;
  const crewIds = Array.from(new Set(s.crew.map((c) => c.project)));
  const layout = `<package_id>/ · manifest.json · ship/${shipId}.glb · ship/${shipId}.json · ship/articulation.json · ${crewIds.map((c) => `crew/${c}.glb`).join(', ') || 'crew/ (none placed)'} · research/research.json`;

  const viewer = (
    <div ref={presentRef} style={{ flex: 1, minHeight: 0, background: 'var(--stage)', display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 13, padding: '12px 16px 8px' }}>
        <span style={{ fontWeight: 500, whiteSpace: 'nowrap' }}>
          v{shown ?? '–'}
          {onCrew && <span className="muted" style={{ fontWeight: 400 }}> · {onCrew.placed.length ? `${onCrew.placed.length} crew from the scene` : 'no crew in the scene'}</span>}
        </span>
        <span className="muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{info?.summary ?? ''}</span>
      </div>
      {!!onCrew?.missing.length && (
        <div className="warn" style={{ fontSize: 12, padding: '0 16px 8px' }}>
          Not shown on v{shown}, which has no crew point for them:{' '}
          {onCrew.missing.map((c) => `${crewName(c.project)} (${stationWords(c.station)})`).join(', ')}
        </div>
      )}
      <div ref={viewRef} style={{ position: 'relative', flex: 1, minHeight: 0, display: 'flex' }}>
        {shown !== null && p ? (
          <ShipViewer
            url={modelUrl(p.id, shown)} view={view} partPaths={partPaths}
            pins={pins} selectedPinId={selPin}
            selectedPart={draft?.part ?? pins.find((x) => x.id === selPin)?.part ?? null}
            pickMode={pinMode}
            onPinClick={(pin) => { setSelPin(pin.id); setDraft(null); }}
            onPick={(r) => {
              if (!r.part) return;
              const box = viewRef.current?.getBoundingClientRect();
              const left = box ? Math.min(Math.max(8, r.clientX - box.left + 12), box.width - 290) : 12;
              const top = box ? Math.min(Math.max(8, r.clientY - box.top - 20), box.height - 220) : 12;
              setDraft({ part: r.part, point: r.point, left, top }); setDraftText('');
            }}
            overlay={draft ? (
              <div className="popover" role="dialog" aria-label="Customer comment" style={{ left: draft.left, top: draft.top, width: 280 }}>
                <span className="faint" style={{ fontSize: 12 }}>The customer is pointing at</span>
                <span className="tag" style={{ alignSelf: 'flex-start', fontSize: 13 }}>{partLabel(draft.part)}</span>
                <label htmlFor="cust-text" className="sr-only">What they said</label>
                <textarea id="cust-text" className="field dark" rows={2} autoFocus value={draftText}
                  placeholder="Type what they said" onChange={(e) => setDraftText(e.target.value)} />
                <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                  <button className="btn sm" onClick={() => setDraft(null)}>Cancel</button>
                  <button className="btn sm primary" disabled={!draftText.trim()} onClick={sendPin}>Pin it</button>
                </div>
              </div>
            ) : undefined}
          >
            {(root) => (
              <>
                <StationsSeen root={root} onSeen={(names) => setStationsSeen({ version: shown, names })} />
                {stationObjects(root).map((node) => {
                  const c = s.crew.find((x) => x.station === node.name);
                  return c ? (
                    <AttachTo key={node.uuid} node={node}>
                      <Suspense fallback={null}>
                        <CrewModel url={modelUrl(c.project, c.version)} animation={c.animation} playing />
                      </Suspense>
                    </AttachTo>
                  ) : null;
                })}
              </>
            )}
          </ShipViewer>
        ) : <Loading what="the ship" />}
      </div>
    </div>
  );

  const exportVersion = s.ship.version;

  return (
    <>
      <TopBar title={s.name}>
        <button className="btn lg" style={{ marginLeft: 'auto', height: 34 }} onClick={present}>Present to customer</button>
      </TopBar>
      <div className="workspace">
        <aside className="side left" style={{ width: 260, padding: 16, gap: 8, fontSize: 13 }}>
          <div className="faint">Versions. Click one to show it.</div>
          <div className="scroll" style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: '40%' }}>
            {versions.map((v) => {
              const on = v.n === shown;
              return (
                <button key={v.n} className={`row-btn ${on ? 'on' : ''}`} aria-pressed={on} style={{ flexDirection: 'column', gap: 4, padding: '10px 12px' }} onClick={() => setShown(v.n)}>
                  <span style={{ display: 'flex', justifyContent: 'space-between', gap: 8, width: '100%' }}>
                    <span style={{ fontWeight: 500 }}>v{v.n}{v.ok ? '' : ' · build failed'}</span>
                    <span style={{ fontSize: 11, color: 'var(--accent-link)', textAlign: 'right' }}>
                      {[on ? 'Showing' : '', v.n === exportVersion ? 'In the scene' : ''].filter(Boolean).join(' · ')}
                    </span>
                  </span>
                  <span className="muted" style={{ fontSize: 12 }}>{v.summary}</span>
                </button>
              );
            })}
            {p && !versions.length && <span className="faint">No versions yet.</span>}
          </div>
          <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', borderTop: '1px solid var(--border)', marginTop: 4 }}>
            <ChatPanel messages={p?.messages ?? []} onSend={chat} busy={busy} projectId={p?.id} label="Chat" placeholder="Do all open feedback" />
          </div>
        </aside>

        <section className="stage" ref={stageRef}>
          <div className="toolbar">
            <button className={`btn ${pinMode ? 'primary' : 'selected'}`} onClick={() => { setPinMode(!pinMode); setDraft(null); }}>
              {pinMode ? 'Placing a pin…' : 'Pin customer feedback'}
            </button>
            <span className="faint toolbar-hint">{pinMode ? 'Click the part they point at.' : `Pins show on the version shown, v${shown ?? '–'}.`}</span>
            <span style={{ marginLeft: 'auto' }}><ViewButtons view={view} onView={setView} /></span>
          </div>
          {viewer}
          <div className={`resize-row${resizing ? ' on' : ''}`} role="separator" aria-orientation="horizontal"
            aria-label="Resize the export panel" aria-valuenow={consoleH} tabIndex={0} title="Drag to resize; double-click to reset"
            onPointerDown={startResize} onDoubleClick={() => keepConsoleH(CONSOLE_DEFAULT)}
            onKeyDown={(e) => {
              if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
                e.preventDefault();
                keepConsoleH(dragHeight(consoleH, 0, e.key === 'ArrowUp' ? -24 : 24, stageHeight()));   // up = taller
              }
            }} />
          <div style={{ height: consoleH, minHeight: PANEL_MIN, maxHeight: `calc(100% - ${PANEL_KEEP}px)`, flexShrink: 0,
            display: 'flex', flexDirection: 'column', background: 'var(--panel)', fontSize: 13 }}>
            <div style={{ padding: '12px 16px', display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center', flexShrink: 0 }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: '1 1 360px', minWidth: 0 }}>
                <span style={{ fontWeight: 500 }}>Export package</span>
                <span className="mono muted" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>{layout}</span>
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <button className={`btn lg ${target === 'unreal' ? 'selected' : 'muted'}`} onClick={() => { setTarget('unreal'); setMakNote(false); }}>Unreal 5.8</button>
                <button className={`btn lg ${target === 'mak' ? 'selected' : 'muted'}`} onClick={() => { setTarget('mak'); setExported(null); }}>MAK (planned)</button>
              </div>
              <button className="btn primary lg" onClick={doExport} disabled={busy}>Export v{exportVersion}</button>
              <button className="btn lg" onClick={buildAndOpen} disabled={busy || opening || target === 'mak'}
                title="Builds a level with the ship and crew in Unreal 5.8, checks it, and opens it in the Unreal editor on this computer">
                Build and Open in Unreal
              </button>
            </div>
            <div className="scroll" style={{ flex: 1, minHeight: 0, padding: '0 16px 12px', display: 'flex', flexDirection: 'column', gap: 10 }}>
              {shown !== null && shown !== exportVersion && (
                <span className="warn" style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                  The scene uses v{exportVersion}, so that is what exports. You are looking at v{shown}.
                  <button className="btn sm" onClick={() => void adoptVersion(shown)}>Use v{shown} in the scene</button>
                </span>
              )}
              {makNote && <span className="ok">MAK export isn’t built yet. The same folder will feed it when it is.</span>}
              {exported && (
                <span className="ok" style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
                  Exported v{exportVersion}, with the research inside, to <span className="mono">{exported.package_dir}</span>.
                  <a className="btn sm" href={fileUrl(exported.zip_url)} download>Download zip</a>
                </span>
              )}
              <JobLines jobs={jobs} dismiss={dismiss} />
              <ErrorNote error={err ?? ship.error} />
              {report && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <span style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span className={report.ok ? 'ok' : 'bad'} style={{ fontWeight: 500 }}>
                      {report.ok ? '✓ Unreal built it and every check passed' : '✗ Unreal built it with failing checks'} · Unreal {report.engine} · <span className="mono" style={{ fontWeight: 400 }}>{report.level}</span>
                    </span>
                    <button className="btn sm" onClick={() => void openInUnreal(report.level)} disabled={opening || busy}
                      title="Opens this level again in the Unreal editor, without rebuilding it">
                      {opening ? 'Starting Unreal…' : 'Open again'}
                    </button>
                  </span>
                  {openedLevel === report.level && (
                    <span className="faint">
                      Unreal is starting with this level: sky, sea and the ship with its crew. The first time takes a few minutes while it
                      prepares shaders. Press Simulate to see the crew move. Close Unreal before building again, which rebuilds this level.
                    </span>
                  )}
                  {report.checks.map((c, i) => (
                    <span key={i} style={{ display: 'flex', gap: 8 }}>
                      <span className={c.pass ? 'ok' : 'bad'}>{c.pass ? '✓' : '✗'}</span>
                      <span>{c.name}</span>
                      <span className="muted">{c.detail}</span>
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        </section>

        <aside className="side right" style={{ width: 340, fontSize: 13 }}>
          <div style={{ padding: '16px 16px 8px', display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 15, fontWeight: 500 }}>Pinned comments</span>
            <span className="faint">{p?.name ?? 'Ship'} · shown on v{shown ?? '–'} · each pin is a task</span>
          </div>
          <div className="scroll" style={{ flex: 1, padding: '8px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
            {!pins.length && <span className="faint">No pins yet. In the meeting, press “Pin customer feedback” and click where they point.</span>}
            {pins.map((pin, i) => (
              <div key={pin.id} className="card" onClick={() => setSelPin(pin.id)}
                style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8, borderColor: pin.id === selPin ? 'var(--accent)' : undefined }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ width: 24, height: 24, borderRadius: 12, background: PIN_COLORS[pin.status] ?? '#A7AEB8', color: 'var(--on-accent)', fontSize: 12, fontWeight: 600, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{i + 1}</span>
                  <span className="tag">{partLabel(pin.part)}</span>
                  <span className="faint" style={{ marginLeft: 'auto', fontSize: 11 }}>{pin.author}</span>
                </span>
                <span style={{ lineHeight: 1.5 }}>“{pin.messages[0]?.text ?? ''}”</span>
                {pin.messages.length > 1 && <span className="muted" style={{ fontSize: 12, lineHeight: 1.5 }}>AI: {pin.messages[pin.messages.length - 1].text}</span>}
                <span style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span className={`chip ${STATUS_CHIP[pin.status] ?? 'grey'}`} style={{ fontSize: 12, padding: '3px 8px', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    {pin.status === 'working' && <span className="spinner" aria-hidden="true" />}{STATUS_TEXT[pin.status] ?? pin.status}
                  </span>
                  <button className="btn sm" disabled={pin.status === 'working' || (pin.status === 'resolved' && pin.after_version == null)}
                    onClick={(e) => { e.stopPropagation(); pinAction(pin); }}>{actionLabel(pin)}</button>
                </span>
              </div>
            ))}
          </div>
          <div style={{ padding: '12px 16px 16px', borderTop: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <label htmlFor="fb" className="faint" style={{ fontSize: 12 }}>In the meeting, pin where they point. For anything ship-wide, type it here</label>
            <input id="fb" className="field" value={feedback} placeholder="Overall it should look more used"
              onChange={(e) => setFeedback(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && feedback.trim()) { void chat(`Customer feedback: ${feedback.trim()}`); setFeedback(''); } }} />
          </div>
        </aside>
      </div>
    </>
  );
}
