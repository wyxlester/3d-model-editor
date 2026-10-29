// Crew animation editor (canvas: Moves.dc.html). Plays and scrubs the crew GLB's real glTF
// animations with three's AnimationMixer. New moves are described in words in the chat (scoped to
// the moves module, CONTRACT §6 "Custom moves"), listed beside the built-ins, and refined in words.
import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { modelUrl } from '../api';
import { ChatPanel, ErrorNote, JobLines, Loading, TopBar, useCarried } from '../components/common';
import { OtherWork, useProjectWork } from '../components/JobCenter';
import { remember, useCatalog, useJobs, useProject, useProjects } from '../hooks';
import { paths } from '../lib/routing';
import { moveNeeds } from '../lib/modules';
import { keyLine, moveRows, refineMessage, removeMovePatch } from '../lib/moves';
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
  const work = useProjectWork(id, () => void project.reload(), jobs);
  const [anim, setAnim] = useState<string | null>(null);
  const [clips, setClips] = useState<string[]>([]);
  const [duration, setDuration] = useState(0);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [seek, setSeek] = useState<{ t: number } | null>(null);
  const [bodies, setBodies] = useState(false);
  const [roll, setRoll] = useState(false);
  const [refine, setRefine] = useState('');
  const [askingRemove, setAskingRemove] = useState<string | null>(null);

  useEffect(() => { if (id) remember('crew', id); }, [id]);

  const p = project.data;
  const spec = useMemo(() => mergePatch((p?.spec ?? {}) as Record<string, unknown>, optimistic) as unknown as Partial<CrewSpec>, [p, optimistic]);
  const specAnims = spec.animations ?? [];
  const rows = moveRows(spec, clips);
  const onPerson = rows.map((r) => r.name);
  const builtIns = rows.filter((r) => r.kind === 'built_in');
  const fromWords = rows.filter((r) => r.kind === 'words');
  const selected = rows.find((r) => r.name === anim) ?? null;
  const notYet = (catalog.data?.animations ?? []).filter((a) => !onPerson.includes(a));
  const sendMoves = (text: string, atts: Parameters<typeof sendChat>[1] = []) => sendChat(text, atts, 'moves');

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
              {builtIns.length === 0 && <span className="faint" style={{ padding: '0 6px' }}>None yet.</span>}
              {builtIns.map((r) => (
                <button key={r.name} style={itemStyle(anim === r.name)} onClick={() => pick(r.name)}>
                  <span>{assetLabel(r.name)}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{r.inModel ? 'In the model' : 'Waiting for rebuild'}</span>
                </button>
              ))}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ padding: '0 6px', fontSize: 12, color: 'var(--text-3)' }}>Made from words</span>
              {fromWords.length === 0 && <span className="faint" style={{ padding: '0 6px' }}>None yet. Describe one in the chat, e.g. "salute".</span>}
              {fromWords.map((r) => (
                <div key={r.name} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <button style={itemStyle(anim === r.name)} onClick={() => pick(r.name)} title={r.description}>
                    <span>{assetLabel(r.name)}</span>
                    <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{r.inModel ? 'In the model' : 'Waiting for rebuild'}</span>
                  </button>
                  {anim === r.name && (askingRemove === r.name ? (
                    <span style={{ display: 'flex', gap: 6, padding: '0 6px' }}>
                      <button className="btn sm" onClick={() => setAskingRemove(null)}>Keep</button>
                      <button className="btn sm" style={{ color: 'var(--red-text)' }} onClick={() => { setAskingRemove(null); edit(removeMovePatch(spec, r.name)); }}>Remove</button>
                    </span>
                  ) : (
                    <span style={{ display: 'flex', gap: 6, padding: '0 6px' }}>
                      <button className="btn sm" aria-label={`Remove ${assetLabel(r.name)}`} onClick={() => setAskingRemove(r.name)}>Remove</button>
                    </span>
                  ))}
                </div>
              ))}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ padding: '0 6px', fontSize: 12, color: 'var(--text-3)' }}>In the catalogue, not on this person yet</span>
              {notYet.length === 0 && <span className="faint" style={{ padding: '0 6px' }}>Nothing else in the catalogue.</span>}
              {notYet.map((a) => {
                const needs = moveNeeds(spec, a);
                return (
                  <div key={a} style={{ ...itemStyle(false), cursor: 'default' }}>
                    <span>{assetLabel(a)}{needs && <span className="faint" style={{ display: 'block', fontSize: 11 }}>{needs}</span>}</span>
                    <button className="btn sm" disabled={!!needs} onClick={() => edit({ animations: [...specAnims, a] })}>Add</button>
                  </div>
                );
              })}
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
            <OtherWork jobs={work.others} />
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
            {selected?.kind === 'words' ? (
              <form style={{ display: 'flex', alignItems: 'center', gap: 8 }}
                onSubmit={(e) => { e.preventDefault(); if (!refine.trim() || !anim) return; void sendMoves(refineMessage(anim, refine)); setRefine(''); }}>
                <span className="faint" style={{ minWidth: 0, flexShrink: 1 }}>{keyLine(selected)}</span>
                <label htmlFor="refine" className="sr-only">Refine {assetLabel(selected.name)}</label>
                <input id="refine" className="field" style={{ flex: 1, minWidth: 0, height: 32 }} value={refine} disabled={busy}
                  placeholder={`Refine ${assetLabel(selected.name).toLowerCase()}: e.g. faster on the way up`} onChange={(e) => setRefine(e.target.value)} />
                <button className="btn sm" type="submit" disabled={busy || !refine.trim()}>Refine</button>
              </form>
            ) : <span className="faint">Built-in move. Key poses are shown for moves made from words.</span>}
          </div>
        </section>

        <aside className="side right" style={{ width: 360, fontSize: 13 }}>
          <ChatPanel
            key={id}
            messages={p?.messages ?? []}
            carried={carried}
            busy={busy}
            label="Describe a new move"
            placeholder="Salute, then lower the hand"
            empty="Describe a move, e.g. 'salute', 'point forward at the bow', 'wave the boat in'. Pick a move made from words on the left to refine it: 'faster on the way up'."
            onSend={sendMoves}
            projectId={id}
            working={work.chatWorking}
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
