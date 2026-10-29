// Assembly: put crew on a ship's crew points (station__* empties), choose what each one does,
// and play the scene. Recreates Assembly.dc.html on the scene API (CONTRACT §7, §10).
import { Fragment, Suspense, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Html } from '@react-three/drei';
import { api, errorText, modelUrl } from '../api';
import { ChatPanel, ErrorNote, JobLines, Loading, TopBar } from '../components/common';
import { remember, useJobs, useLoad, useProject } from '../hooks';
import type { ViewName } from '../lib/frames';
import { isStationName, partLabel } from '../lib/parts';
import { paths } from '../lib/routing';
import type { Attachment, Project, Scene, SceneCrew, SceneSpec } from '../types';
import { AttachTo, CrewModel, ShipViewer, ViewButtons, stationObjects } from '../viewer/Viewer';
import { ScenePicker } from './AssemblyScenePicker';

const CREW_COLORS = ['#5B9BFF', '#B58CFF', '#4CC38A', '#F2914F', '#E6B04A', '#7FDDB0'];

/** "station__aft_port" → "Aft port". */
export function stationLabel(path: string): string {
  const s = partLabel(path.replace(/^station__/, ''));
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** The body PUT /api/scenes/{id} takes: the spec without server-added fields. */
export function specOf(s: Scene, crew: SceneCrew[] = s.crew): SceneSpec {
  return { schema: 'me3d.scene_spec/1', id: s.id, name: s.name, ship: s.ship, crew };
}

/** All crew projects, loaded in full (their spec lists the animations each one has). */
export function useCrewProjects() {
  return useLoad<Project[]>(async () => {
    const list = await api.projects();
    // A variant awaiting approval can't go in a scene yet (the server refuses it).
    return Promise.all(list.filter((p) => p.kind === 'crew' && !p.draft).map((p) => api.project(p.id)));
  }, []);
}

type Picked = { kind: 'member'; index: number } | { kind: 'new'; project: string } | null;

export function Assembly() {
  const { sceneId } = useParams();
  if (!sceneId) return <ScenePicker to={paths.assembly} title="Assembly" />;
  return <AssemblyScene key={sceneId} sceneId={sceneId} />;
}

function AssemblyScene({ sceneId }: { sceneId: string }) {
  const scene = useLoad<Scene>(() => api.scene(sceneId), [sceneId]);
  const s = scene.data;
  const ship = useProject(s?.ship.project);
  const crewProjects = useCrewProjects();
  const { jobs, run, dismiss, busy } = useJobs();
  const [picked, setPicked] = useState<Picked>(null);
  const [point, setPoint] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [view, setView] = useState<ViewName>('top');
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const nav = useNavigate();

  useEffect(() => { remember('scene', sceneId); }, [sceneId]);

  const byId = useMemo(() => new Map((crewProjects.data ?? []).map((p) => [p.id, p])), [crewProjects.data]);
  const shipParts = ship.data?.parts ?? [];
  const stations = shipParts.filter((p) => isStationName(p.path));
  const firstStation = stations[0]?.path ?? null;
  useEffect(() => { if (!point && firstStation) setPoint(firstStation); }, [point, firstStation]);

  const crew = s?.crew ?? [];
  const nameOf = (c: SceneCrew, i: number) => {
    const base = byId.get(c.project)?.name ?? c.project;
    const same = crew.filter((x, j) => x.project === c.project && j < i).length;
    return same ? `${base} ${same + 1}` : base;
  };
  const occupant = (station: string) => crew.findIndex((c) => c.station === station);
  // A variant of the ship may have lost a crew point; its crew keep it until moved (export refuses it).
  const lost = (station: string) => !!ship.data && !stations.some((p) => p.path === station);

  const save = async (next: SceneCrew[]) => {
    if (!s) return;
    setSaving(true); setErr(null);
    try {
      const r = await api.updateScene(s.id, specOf(s, next));
      scene.setData(r.scene);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  const clickStation = (station: string) => {
    setPoint(station);
    if (!picked) return;
    const occ = occupant(station);
    if (picked.kind === 'member') {
      if (occ === picked.index) { setPicked(null); return; }
      const from = crew[picked.index].station;
      // Swap with whoever stands there, so nobody is left without a point (the contract needs one).
      const next = crew.map((c, i) => (i === picked.index ? { ...c, station } : i === occ ? { ...c, station: from } : c));
      void save(next);
    } else {
      if (occ >= 0) { setErr(`${stationLabel(station)} is taken by ${nameOf(crew[occ], occ)}. Move them first, or pick an empty point.`); return; }
      const p = byId.get(picked.project);
      if (!p) return;
      const anims = p.spec.animations ?? [];
      void save([...crew, { project: p.id, version: p.version, station, animation: anims[0] ?? 'idle' }]);
    }
    setPicked(null);
  };

  /** Same ship, different crew: a copy of this scene to change. */
  const duplicate = async () => {
    if (!s) return;
    setSaving(true); setErr(null);
    try {
      const { id: _id, ...spec } = specOf(s);
      void _id;
      const r = await api.createScene({ ...spec, name: `${s.name} (copy)` });
      nav(paths.assembly(r.scene.id));
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  const setAnimation = (i: number, animation: string) => void save(crew.map((c, j) => (j === i ? { ...c, animation } : c)));
  const remove = (i: number) => void save(crew.filter((_, j) => j !== i));

  if (scene.error) return <><TopBar /><div style={{ padding: 24 }}><ErrorNote error={scene.error} retry={scene.reload} /></div></>;
  if (!s) return <><TopBar /><div className="viewer"><Loading what="the scene" /></div></>;

  const sel = stations.find((p) => p.path === point) ?? null;
  const selOcc = sel ? occupant(sel.path) : -1;
  const stationData = sel && sel.station && typeof sel.station === 'object' ? Object.entries(sel.station) : [];
  const url = modelUrl(s.ship.project, s.ship.version);

  const chatSend = (text: string, atts: Attachment[] = []) => {
    if (!ship.data) return;
    return run('Working on it', () => api.chat(ship.data!.id, text, 'build', atts.map((a) => a.id)), () => { void ship.reload(); });
  };

  return (
    <>
      <TopBar title={s.name}>
        <div className="topbar-right" style={{ fontSize: 13 }}>
          {ship.data?.name ?? 'Ship'} · v{s.ship.version} · {crew.length} crew placed on {stations.length} crew points
          {saving && <span className="spinner" aria-label="Saving" />}
          <button className="btn sm" onClick={() => void duplicate()} disabled={saving}
            title="A copy of this scene, to try the same ship with different crew">Duplicate scene</button>
        </div>
      </TopBar>
      <div className="workspace">
        <aside className="side left scroll" style={{ width: 280, gap: 10, padding: 16, fontSize: 13 }}>
          <div className="faint">Crew. Click a person, then a point on the deck.</div>
          {crew.map((c, i) => {
            const on = picked?.kind === 'member' && picked.index === i;
            return (
              <div key={`${c.project}-${i}`} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <button className={`row-btn ${on ? 'on' : ''}`} style={{ alignItems: 'center', height: 60, padding: '0 12px', gap: 12 }}
                  onClick={() => { setPicked(on ? null : { kind: 'member', index: i }); setPoint(c.station); }}>
                  <span style={{ width: 36, height: 36, borderRadius: '50%', background: CREW_COLORS[i % CREW_COLORS.length], flexShrink: 0 }} />
                  <span style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
                    <span style={{ fontWeight: 500 }}>{nameOf(c, i)}</span>
                    {lost(c.station)
                      ? <span className="warn" style={{ fontSize: 12 }}>{stationLabel(c.station)} is gone from this ship. Click, then pick a point.</span>
                      : <span className="muted" style={{ fontSize: 12 }}>At {stationLabel(c.station).toLowerCase()}</span>}
                  </span>
                </button>
                <button className="btn sm muted" aria-label={`Take ${nameOf(c, i)} off the ship`} title="Take off the ship" onClick={() => remove(i)}>×</button>
              </div>
            );
          })}
          {!crew.length && <span className="warn" style={{ fontSize: 12 }}>Nobody is on board yet.</span>}

          <div className="faint" style={{ marginTop: 8 }}>Add a person</div>
          <ErrorNote error={crewProjects.error} retry={crewProjects.reload} />
          {(crewProjects.data ?? []).map((p) => {
            const on = picked?.kind === 'new' && picked.project === p.id;
            return (
              <button key={p.id} className={`row-btn ${on ? 'on' : ''}`} style={{ alignItems: 'center' }}
                onClick={() => setPicked(on ? null : { kind: 'new', project: p.id })}>
                <span style={{ fontWeight: 500 }}>{p.name}</span>
                <span className="faint" style={{ marginLeft: 'auto', fontSize: 12 }}>v{p.version}</span>
              </button>
            );
          })}
          <span className="faint" style={{ fontSize: 11, lineHeight: 1.5 }}>A person joins the scene when you place them on a point: the scene file needs a point for everyone.</span>
          <Link to="/crew" className="btn dashed lg" style={{ justifyContent: 'center', height: 40 }}>+ Add from crew workshop</Link>
          {stations.length === 0 && ship.data && (
            <div style={{ marginTop: 'auto', background: '#2A2210', border: '1px solid #5C4718', borderRadius: 10, padding: 10, color: 'var(--amber-text)', fontSize: 12, lineHeight: 1.5 }}>
              This ship has no crew points yet. Ask in the chat to add them, e.g. “a crew point at the helm”.
            </div>
          )}
          {stations.length > 0 && stations.length <= crew.length && (picked?.kind === 'new') && (
            <div style={{ marginTop: 'auto', background: '#2A2210', border: '1px solid #5C4718', borderRadius: 10, padding: 10, color: 'var(--amber-text)', fontSize: 12, lineHeight: 1.5 }}>
              Every crew point is taken. Ask for another point in the chat, or move someone.
            </div>
          )}
        </aside>

        <section className="stage">
          <div className="toolbar">
            <ViewButtons view={view} onView={setView} />
            <span className="faint toolbar-hint" style={{ marginLeft: 8 }}>
              {picked ? 'Now click a point on the deck.' : 'Walk routes are not built yet.'}
            </span>
            <span style={{ marginLeft: 'auto', display: 'flex', gap: 14 }} className="muted">
              <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}><span style={{ width: 10, height: 10, borderRadius: '50%', background: '#F2914F' }} />Empty point</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}><span style={{ width: 10, height: 10, borderRadius: '50%', background: '#4CC38A' }} />Crewed</span>
            </span>
          </div>
          <ShipViewer url={url} view={view} partPaths={shipParts.map((p) => p.path)}>
            {(root) => stationObjects(root).map((node) => {
              const i = crew.findIndex((c) => c.station === node.name);
              const c = i >= 0 ? crew[i] : null;
              return (
                <AttachTo key={node.uuid} node={node}>
                  <Html zIndexRange={[30, 10]} position={[0, 0.05, 0]}>
                    <button className="station-dot" title={stationLabel(node.name)} aria-label={stationLabel(node.name)}
                      style={{ background: c ? '#4CC38A' : '#F2914F', boxShadow: point === node.name ? '0 0 0 4px rgba(91,155,255,0.55)' : undefined }}
                      onClick={(e) => { e.stopPropagation(); clickStation(node.name); }}>
                      {stationLabel(node.name).charAt(0)}
                    </button>
                  </Html>
                  {c && (
                    <Suspense fallback={null}>
                      <CrewModel url={modelUrl(c.project, c.version)} animation={c.animation} playing={playing} />
                    </Suspense>
                  )}
                </AttachTo>
              );
            })}
          </ShipViewer>
          <div style={{ borderTop: '1px solid var(--border)', background: 'var(--panel)', padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <button onClick={() => setPlaying(!playing)} aria-label={playing ? 'Pause scene' : 'Play scene'}
                style={{ width: 36, height: 36, borderRadius: '50%', border: '1px solid var(--border-2)', background: 'transparent', color: 'var(--text)', fontSize: 14 }}>
                {playing ? '❚❚' : '▶'}
              </button>
              <span className="muted" style={{ fontSize: 13 }}>{playing ? 'Playing · every crew member loops their move' : 'Play the whole boat working'}</span>
              <span className="faint" style={{ marginLeft: 'auto' }}>Timed activity tracks and walk routes are not in the scene file yet</span>
            </div>
            {crew.map((c, i) => {
              const anims = byId.get(c.project)?.spec.animations ?? [];
              const opts = anims.includes(c.animation) ? anims : [c.animation, ...anims];
              return (
                <div key={`t${i}`} style={{ display: 'grid', gridTemplateColumns: '120px minmax(0, 1fr)', gap: 10, alignItems: 'center' }}>
                  <span className="muted">{nameOf(c, i)}</span>
                  <select className="field" style={{ maxWidth: 260 }} value={c.animation} aria-label={`Move for ${nameOf(c, i)}`}
                    onChange={(e) => setAnimation(i, e.target.value)}>
                    {opts.map((a) => <option key={a} value={a}>{a.replace(/_/g, ' ')}</option>)}
                  </select>
                </div>
              );
            })}
            <ErrorNote error={err} />
            <ErrorNote error={ship.error} retry={ship.reload} />
          </div>
        </section>

        <aside className="side right" style={{ width: 320, gap: 16, padding: 16, fontSize: 13, overflowY: 'auto' }}>
          {sel ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div style={{ fontSize: 15, fontWeight: 500 }}>{stationLabel(sel.path)}</div>
              <div className="kv" style={{ gridTemplateColumns: '110px minmax(0, 1fr)' }}>
                <span>On part</span><span>{sel.parent ? partLabel(sel.parent) : 'The ship'}</span>
                {stationData.map(([k, v]) => (
                  <Fragment key={k}><span>{k.replace(/_/g, ' ')}</span><span>{typeof v === 'string' ? v : JSON.stringify(v)}</span></Fragment>
                ))}
                <span>Assigned</span><span>{selOcc >= 0 ? nameOf(crew[selOcc], selOcc) : 'Nobody yet'}</span>
              </div>
            </div>
          ) : (
            <span className="faint">{stations.length ? 'Click a point to see it.' : 'No crew points on this ship.'}</span>
          )}
          <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: 8, borderTop: '1px solid var(--border)', paddingTop: 14, minHeight: 280 }}>
            <span className="faint" style={{ fontSize: 12 }}>Chat</span>
            <span className="faint" style={{ fontSize: 11 }}>Goes to the ship's chat: the API has no scene chat yet.</span>
            <div style={{ flex: 1, minHeight: 200, display: 'flex', flexDirection: 'column', border: '1px solid var(--border)', borderRadius: 10 }}>
              <ChatPanel messages={ship.data?.messages ?? []} onSend={chatSend} busy={busy} projectId={ship.data?.id}
                label="Message the AI" placeholder="Navigator watches the target, not the screens" />
            </div>
            <JobLines jobs={jobs} dismiss={dismiss} />
            <Link to={paths.review(sceneId)} className="btn primary lg" style={{ justifyContent: 'center' }}>Review with customer</Link>
          </div>
        </aside>
      </div>
    </>
  );
}
