// Shared by Assembly and Review: with no scene in the URL, list every scene to open, and offer to
// start a new one with any ship. Inside a scene, SceneSwitch moves to another scene or back here.
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, errorText } from '../api';
import { ErrorNote, Loading, TopBar } from '../components/common';
import { recall, useLoad, useProjects } from '../hooks';
import { shortTime } from '../lib/pins';
import type { ProjectSummary, Scene } from '../types';

type ScenePath = (sceneId?: string | null) => string;

/** Ships a scene can use: built, and not a variant still awaiting approval (the server refuses those). */
function sceneShips(projects: ProjectSummary[] | null): ProjectSummary[] {
  return (projects ?? []).filter((p) => p.kind === 'ship' && !!p.version && !p.draft && !p.discarded);
}

/** "Sampan · v3 · 29 Oct, 10:51": two ships can share a name, so the date tells them apart. */
function shipOption(p: ProjectSummary): string {
  return [p.name, p.version ? `v${p.version}` : '', shortTime(p.updated)].filter(Boolean).join(' · ');
}

function sceneLine(s: Scene, projects: ProjectSummary[] | null): string {
  const ship = (projects ?? []).find((p) => p.id === s.ship.project)?.name ?? s.ship.project;
  return `${ship} · v${s.ship.version} · ${s.crew.length} crew`;
}

export function ScenePicker({ to, title }: { to: ScenePath; title: string }) {
  const nav = useNavigate();
  const projects = useProjects();
  const scenes = useLoad<Scene[]>(() => api.scenes(), []);
  const [err, setErr] = useState<string | null>(null);
  const [ship, setShip] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);

  const ships = sceneShips(projects.data);
  const last = recall('scene');
  const create = async () => {
    const p = ships.find((s) => s.id === ship);
    if (!p) return;
    setBusy(true); setErr(null);
    try {
      const full = await api.project(p.id);
      const r = await api.createScene({
        schema: 'me3d.scene_spec/1', name: name.trim() || `${p.name} with crew`,
        ship: { project: p.id, version: full.version }, crew: [],
      });
      nav(to(r.scene.id));
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <TopBar />
      <main className="scroll" style={{ flex: 1, padding: '40px 24px', display: 'flex', justifyContent: 'center', position: 'relative' }}>
        {scenes.loading && !scenes.data && <Loading what="scenes" />}
        {(scenes.data || scenes.error) && (
          <div style={{ width: '100%', maxWidth: 560, display: 'flex', flexDirection: 'column', gap: 14 }}>
            <h1 className="title">{title}</h1>
            <p className="muted" style={{ margin: 0 }}>A scene is one ship with crew placed on it. Open a scene, or start a new one with any ship.</p>
            <ErrorNote error={scenes.error} retry={scenes.reload} />

            {!!scenes.data?.length && (
              <>
                <span className="faint">Scenes</span>
                {scenes.data.map((s) => (
                  <button key={s.id} className="row-btn" style={{ alignItems: 'center' }} onClick={() => nav(to(s.id))}>
                    <span style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
                      <span style={{ fontWeight: 500 }}>{s.name}</span>
                      <span className="muted" style={{ fontSize: 12 }}>{sceneLine(s, projects.data)}</span>
                    </span>
                    {s.id === last && <span className="faint" style={{ marginLeft: 'auto', fontSize: 12 }}>Last opened</span>}
                  </button>
                ))}
              </>
            )}

            <span className="faint" style={{ marginTop: 8 }}>New scene</span>
            <ErrorNote error={err} />
            <ErrorNote error={projects.error} retry={projects.reload} />
            {!projects.loading && !ships.length && <span className="faint">No ships yet. Build one in the Ship workshop first.</span>}
            {ships.length > 0 && (
              <>
                <label className="faint" htmlFor="scene-ship">Ship</label>
                <select id="scene-ship" className="field" value={ship} onChange={(e) => setShip(e.target.value)}>
                  <option value="">Choose a ship…</option>
                  {ships.map((s) => <option key={s.id} value={s.id}>{shipOption(s)}</option>)}
                </select>
                <label className="faint" htmlFor="scene-name">Name (optional)</label>
                <input id="scene-name" className="field" value={name} onChange={(e) => setName(e.target.value)} placeholder="PC 303 coming alongside" />
                <button className="btn primary lg" style={{ alignSelf: 'flex-start' }} disabled={!ship || busy} onClick={() => void create()}>
                  {busy ? 'Creating…' : 'New scene'}
                </button>
              </>
            )}
          </div>
        )}
      </main>
    </>
  );
}

const NEW = '__new__';

/** The scene being shown, as a choice of every scene, plus a way to start a new one with another ship. */
export function SceneSwitch({ current, to }: { current: string; to: ScenePath }) {
  const nav = useNavigate();
  const projects = useProjects();
  const scenes = useLoad<Scene[]>(() => api.scenes(), [current]);
  const list = scenes.data ?? [];
  if (!list.some((s) => s.id === current)) return null;
  return (
    <select className="field" aria-label="Scene" value={current} style={{ maxWidth: 280 }}
      onChange={(e) => nav(e.target.value === NEW ? to(null) : to(e.target.value))}>
      {list.map((s) => <option key={s.id} value={s.id}>{s.name} · {sceneLine(s, projects.data)}</option>)}
      <option value={NEW}>+ New scene with another ship…</option>
    </select>
  );
}
