// Crew animation editor (canvas: Moves.dc.html). Plays and scrubs the crew GLB's real glTF
// animations with three's AnimationMixer; new moves are asked for in the chat.
import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { modelUrl } from '../api';
import { ChatPanel, ErrorNote, JobLines, Loading, TopBar, useCarried } from '../components/common';
import { remember, useCatalog, useJobs, useProject, useProjects } from '../hooks';
import { paths } from '../lib/routing';
import { assetLabel, mergePatch } from '../lib/spec';
import type { CrewSpec } from '../types';
import { CrewViewer } from '../viewer/Viewer';
import { fmtSecs, useCrewChat, useSpecEditor } from './CrewShared';

const SEL = { border: '1px solid var(--accent)', background: 'var(--accent-bg)', color: 'var(--accent-text)' };

export function Moves() {
  const { id } = useParams();
  const carried = useCarried();
  const project = useProject(id);
  const projects = useProjects();
  const catalog = useCatalog();
  const { jobs, run, dismiss, busy } = useJobs();
  const { edit, optimistic } = useSpecEditor(id, run, project.reload);
  const sendChat = useCrewChat(id, run, project.reload);
  const [anim, setAnim] = useState<string | null>(null);
  const [clips, setClips] = useState<string[]>([]);
  const [duration, setDuration] = useState(0);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [seek, setSeek] = useState<{ t: number } | null>(null);
  const [bodies, setBodies] = useState(false);
  const [roll, setRoll] = useState(false);

  useEffect(() => { if (id) remember('crew', id); }, [id]);

  const p = project.data;
  const spec = useMemo(() => mergePatch((p?.spec ?? {}) as Record<string, unknown>, optimistic) as unknown as Partial<CrewSpec>, [p, optimistic]);
  const specAnims = spec.animations ?? [];
  const onPerson = [...new Set([...clips, ...specAnims])];
  const notYet = (catalog.data?.animations ?? []).filter((a) => !onPerson.includes(a));

  useEffect(() => {
    if (anim && onPerson.includes(anim)) return;
    setAnim(clips[0] ?? specAnims[0] ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clips.join(), specAnims.join()]);

  const urls = useMemo(() => {
    if (!p?.model_url) return [];
    if (!bodies) return [p.model_url];
    const others = (projects.data ?? []).filter((x) => x.kind === 'crew' && x.id !== p.id && x.version).map((x) => modelUrl(x.id, x.version!));
    return [p.model_url, ...others];
  }, [p, bodies, projects.data]);

  const pick = (a: string) => { setAnim(a); setTime(0); setSeek({ t: 0 }); setPlaying(true); };
  const scrub = (t: number) => { setPlaying(false); setTime(t); setSeek({ t }); };
  const inGlb = anim ? clips.includes(anim) : false;

  const itemStyle = (on: boolean) => ({
    display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, height: 40, padding: '0 10px', borderRadius: 6,
    fontSize: 13, textAlign: 'left' as const, width: '100%',
    ...(on ? SEL : { border: '1px solid transparent', background: 'transparent', color: 'var(--text)' }),
  });
  const tog = (on: boolean) => `btn ${on ? 'toggle-on' : 'muted'}`;

  return (
    <>
      <TopBar title={p?.name ?? 'Crew'} back="crew">
        <span style={{ fontSize: 13, color: 'var(--text-2)' }}><Link to={paths.crew(id)}>Crew workshop</Link> › Animation</span>
      </TopBar>
      <div className="workspace">
        <aside className="side left" style={{ width: 280, fontSize: 13 }}>
          <div style={{ padding: '14px 16px 8px', fontSize: 12, color: 'var(--text-3)' }}>Animation catalogue</div>
          <div className="scroll" style={{ flex: 1, padding: '0 10px 12px', display: 'flex', flexDirection: 'column', gap: 14 }}>
            <ErrorNote error={catalog.error} retry={catalog.reload} />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ padding: '0 6px', fontSize: 12, color: 'var(--text-3)' }}>On this person</span>
              {onPerson.length === 0 && <span className="faint" style={{ padding: '0 6px' }}>None yet.</span>}
              {onPerson.map((a) => (
                <button key={a} style={itemStyle(anim === a)} onClick={() => pick(a)}>
                  <span>{assetLabel(a)}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{clips.includes(a) ? 'In the model' : 'Waiting for rebuild'}</span>
                </button>
              ))}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ padding: '0 6px', fontSize: 12, color: 'var(--text-3)' }}>In the catalogue, not on this person yet</span>
              {notYet.length === 0 && <span className="faint" style={{ padding: '0 6px' }}>Nothing else in the catalogue.</span>}
              {notYet.map((a) => (
                <div key={a} style={{ ...itemStyle(false), cursor: 'default' }}>
                  <span>{assetLabel(a)}</span>
                  <button className="btn sm" onClick={() => edit({ animations: [...specAnims, a] })}>Add</button>
                </div>
              ))}
            </div>
          </div>
        </aside>

        <section className="stage">
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, padding: '10px 16px', borderBottom: '1px solid var(--border)', fontSize: 13 }}>
            <span style={{ fontWeight: 500 }}>{anim ? assetLabel(anim) : 'No animation'}</span>
            <span className="muted">· {inGlb ? 'Loop in preview' : anim ? 'Not in the built model yet' : 'Pick one on the left'} · on the shared skeleton</span>
            <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
              <button className={tog(bodies)} onClick={() => setBodies(!bodies)} title="Shows every crew member you have, side by side, playing this move">Check on all body sizes</button>
              <button className={tog(roll)} onClick={() => setRoll(!roll)}>Rolling deck</button>
            </span>
          </div>
          {project.error && <div style={{ padding: 12 }}><ErrorNote error={project.error} retry={project.reload} /></div>}
          {!p && project.loading ? <div className="viewer"><Loading what="crew member" /></div> : (
            <div style={{ position: 'relative', flex: 1, minHeight: 0, display: 'flex' }}>
              <CrewViewer urls={urls} animation={inGlb ? anim : null} playing={playing} seek={seek} rollDeg={roll ? 4 : 0}
                onTime={(t) => { if (playing) setTime(t); }}
                onDuration={(d, c) => { setDuration(d); setClips((old) => (old.join() === c.join() ? old : c)); }} />
              <span style={{ position: 'absolute', left: 16, bottom: 12, fontSize: 12, color: 'var(--text-3)', pointerEvents: 'none' }}>
                {roll ? 'Deck rolling 4°: feet must stay planted' : 'Deck'}{bodies ? ` · ${urls.length} crew members` : ''}
              </span>
            </div>
          )}
          <div style={{ borderTop: '1px solid var(--border)', background: 'var(--panel)', padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 10, fontSize: 12 }}>
            {jobs.length > 0 && <JobLines jobs={jobs} dismiss={dismiss} />}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <button onClick={() => setPlaying(!playing)} aria-label={playing ? 'Pause' : 'Play'} disabled={!inGlb}
                style={{ width: 36, height: 36, borderRadius: '50%', border: '1px solid var(--border-2)', background: 'transparent', color: 'var(--text)', fontSize: 14, flexShrink: 0 }}>
                {playing ? '❚❚' : '▶'}
              </button>
              <label htmlFor="scrub" style={{ color: 'var(--text-2)', minWidth: 90, fontSize: 13 }}>{fmtSecs(time)} / {fmtSecs(duration)}</label>
              <input id="scrub" type="range" min={0} max={Math.max(duration, 0.01)} step={0.01} value={Math.min(time, duration)} disabled={!inGlb}
                onChange={(e) => scrub(Number(e.target.value))} style={{ flex: 1 }} />
              <span className="faint">Drag to scrub</span>
            </div>
            <span className="faint">Key poses aren't shown: the server doesn't send key-pose data for an animation yet.</span>
          </div>
        </section>

        <aside className="side right" style={{ width: 360, fontSize: 13 }}>
          <ChatPanel
            key={id}
            messages={p?.messages ?? []}
            carried={carried}
            busy={busy}
            label="Describe a new move or a change"
            placeholder="Hand signal to the coxswain: slow ahead"
            empty="Describe a move, e.g. 'brace as we come alongside, one hand on the rail', or a change: 'faster on the way up'."
            onSend={sendChat}
            projectId={id}
            footer={
              <div className="kv" style={{ gridTemplateColumns: '110px minmax(0, 1fr)', fontSize: 12 }}>
                <span>Plays</span><span>{inGlb ? 'Loops in this preview' : '—'}</span>
                <span>Clip length</span><span>{inGlb ? fmtSecs(duration) : '—'}</span>
              </div>
            }
          />
        </aside>
      </div>
    </>
  );
}
