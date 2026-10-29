// Crew workshop (canvas: Crew.dc.html). Sims-style pickers and body sliders edit the crew spec
// (CONTRACT §6) through POST /spec; the viewer plays the chosen animation from the built GLB.
import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { ChatPanel, ErrorNote, JobLines, Loading, TopBar, useCarried } from '../components/common';
import { DerivedFromLink, useVariantDialog } from '../components/VariantDialog';
import { remember, useCatalog, useJobs, useProject, useProjects } from '../hooks';
import { paths } from '../lib/routing';
import { assetLabel, groupClothes, mergePatch, toggleIn } from '../lib/spec';
import type { CrewSpec } from '../types';
import { CrewViewer } from '../viewer/Viewer';
import { useCrewChat, useSpecEditor } from './CrewShared';

type Slot = 'Body' | 'Clothes' | 'Head' | 'Equipment' | 'Moves' | 'Outfits';
const SLOTS: Slot[] = ['Body', 'Clothes', 'Head', 'Equipment', 'Moves', 'Outfits'];

// The contract types `body.skin` as a free string ("medium"). The allowed values are not fixed
// anywhere yet; these three follow the plan's "3 skin tones". Flagged to the contract owner.
const SKINS: { id: string; color: string }[] = [
  { id: 'light', color: '#F1D3B8' },
  { id: 'medium', color: '#B07A4F' },
  { id: 'dark', color: '#4E3220' },
];

const SEL = { border: '1px solid var(--accent)', background: 'var(--accent-bg)', color: 'var(--accent-text)' };
const OFF = { border: '1px solid var(--border)', background: 'var(--panel-2)', color: 'var(--text)' };
const TILE: CSSProperties = {
  display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 6, height: 96, padding: 8,
  borderRadius: 10, fontSize: 12, textAlign: 'center', position: 'relative',
};

/** A stable, muted swatch colour per catalogue item (the catalogue has no thumbnails yet). */
function swatch(name: string | null): string {
  if (!name) return '#2E333B';
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `hsl(${h} 28% 36%)`;
}

function Tile({ label, note, on, color, onClick }: { label: string; note?: string; on: boolean; color: string; onClick: () => void }) {
  return (
    <button onClick={onClick} style={{ ...TILE, ...(on ? SEL : OFF) }} aria-pressed={on}>
      <span style={{ display: 'block', width: 36, height: 36, borderRadius: 6, background: color, flexShrink: 0 }} />
      <span>{label}</span>
      {note && <span className="faint" style={{ fontSize: 11 }}>{note}</span>}
    </button>
  );
}

function PromptTile() {
  return (
    <button style={{ ...TILE, border: '1px dashed var(--border-2)', background: 'transparent', color: 'var(--text-2)' }}
      onClick={() => document.getElementById('chat-ask')?.focus()} title="Describe the item in the chat on the left">
      <span style={{ fontSize: 18 }}>+</span><span>Prompt new</span>
    </button>
  );
}

const grid: CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8 };

export function CrewWorkshop() {
  const { id } = useParams();
  const nav = useNavigate();
  const carried = useCarried();
  const project = useProject(id);
  const projects = useProjects();
  const catalog = useCatalog();
  const { jobs, run, dismiss, busy } = useJobs();
  const [search] = useSearchParams();
  const variant = useVariantDialog();
  const reload = project.reload;
  const { edit, optimistic } = useSpecEditor(id, run, reload);
  const sendChat = useCrewChat(id, run, reload);
  const [slot, setSlot] = useState<Slot>('Body');
  const [anim, setAnim] = useState<string | null>(null);
  const [clips, setClips] = useState<string[]>([]);
  const [drafts, setDrafts] = useState<Record<string, number>>({});
  const [dialog, setDialog] = useState(false);
  const [pickerName, setPickerName] = useState('');
  const [pickerSlot, setPickerSlot] = useState('Equipment');
  const [answeredAt, setAnsweredAt] = useState<number | null>(null);

  useEffect(() => { if (id) remember('crew', id); }, [id]);
  const showVariant = variant.show;
  useEffect(() => {
    // Home routed here to make a variant: open the dialog with the request as the brief.
    if (id && search.get('variant')) showVariant(id, [...(carried ?? [])].reverse().find((m) => m.who === 'You')?.text ?? '');
  }, [id, search, carried, showVariant]);

  const p = project.data;
  const spec = useMemo(() => mergePatch((p?.spec ?? {}) as Record<string, unknown>, optimistic) as unknown as Partial<CrewSpec>, [p, optimistic]);
  const body = spec.body ?? { gender: 'male', height_cm: 175, weight: 0.5, muscle: 0.5, age_years: 30, skin: 'medium' };
  const clothes = spec.clothes ?? { suit: null, shoes: null, hat: null, suit_color: null };
  const equipment = spec.equipment ?? [];
  const animations = spec.animations ?? [];

  // Default to the first animation the model actually carries.
  useEffect(() => {
    if (anim && (clips.includes(anim) || animations.includes(anim))) return;
    setAnim(clips.find((c) => animations.includes(c)) ?? clips[0] ?? animations[0] ?? null);
  }, [clips, animations, anim]);

  const people = (projects.data ?? []).filter((x) => x.kind === 'crew');
  const name = p?.name ?? 'Crew member';
  const groups = groupClothes(catalog.data?.clothes ?? []);

  const addPerson = () => {
    let newId = '';
    void run('Building a new crew member', async () => {
      const r = await api.createProject('crew', 'male_default');
      newId = r.project.id;
      return { job_id: r.job_id };
    }, async () => { remember('crew', newId); await projects.reload(); nav(paths.crew(newId)); });
  };

  const slider = (key: string, value: number) => ({
    value: drafts[key] ?? value,
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setDrafts((d) => ({ ...d, [key]: Number(e.target.value) })),
    onPointerUp: () => done(),
    onKeyUp: () => done(),
    onBlur: () => done(),
  });
  function done() {
    const entries = Object.entries(drafts);
    if (!entries.length) return;
    const patchBody: Record<string, number> = {};
    for (const [k, v] of entries) patchBody[k] = v;
    setDrafts({});
    edit({ body: patchBody });
  }

  const msgs = p?.messages ?? [];
  const last = msgs[msgs.length - 1];
  const offerPicker = last?.who === 'AI' && msgs.length > 1 && answeredAt !== msgs.length;

  const confirmPicker = () => {
    const n = pickerName.trim() || 'the new item';
    setDialog(false);
    setAnsweredAt(msgs.length);
    void sendChat(`Please add "${n}" to the catalogue as a new ${pickerSlot.toLowerCase()} picker, using what you just made, with its research and licence.`);
  };
  const useOnce = () => {
    setAnsweredAt(msgs.length);
    void sendChat(`Use what you just made on ${name} only. Don't add it to the catalogue.`);
  };

  return (
    <>
      <TopBar title={name}>
        <div className="topbar-right" style={{ gap: 6, flexWrap: 'wrap', fontSize: 13 }}>
          {people.map((x) => (
            <Link key={x.id} to={paths.crew(x.id)} onClick={() => remember('crew', x.id)}
              style={{ height: 32, padding: '0 12px', borderRadius: 16, display: 'flex', alignItems: 'center', fontSize: 13, ...(x.id === id ? SEL : OFF) }}>{x.name}</Link>
          ))}
          <button className="btn dashed pill" onClick={addPerson} disabled={busy}>+ Person</button>
        </div>
      </TopBar>
      <div className="workspace">
        <aside className="side left" style={{ width: 340 }}>
          <div style={{ padding: '14px 16px 6px', fontSize: 12, color: 'var(--text-3)' }}>Prompt something the catalogue doesn't have</div>
          <ChatPanel
            key={id}
            messages={msgs}
            carried={carried}
            busy={busy}
            label="Describe a new item or move"
            placeholder="Animation: brace as we come alongside"
            empty="Ask for anything the pickers don't have: a life vest like the boarding team wears, a beard, a move."
            onSend={sendChat}
            afterLastAi={offerPicker ? (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
                <button className="btn primary" style={{ height: 36, padding: '0 14px', borderRadius: 8 }} onClick={() => { setPickerName(''); setDialog(true); }}>Add as a picker</button>
                <button className="btn" style={{ height: 36, padding: '0 14px', borderRadius: 8 }} onClick={useOnce}>Use on {name} only</button>
              </div>
            ) : undefined}
          />
        </aside>

        <section className="stage">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', borderBottom: '1px solid var(--border)', fontSize: 13, color: 'var(--text-2)' }}>
            <span style={{ color: 'var(--text)', fontWeight: 500 }}>{name}</span><span>·</span>
            <span>{body.gender === 'female' ? 'Female' : 'Male'}, {drafts.height_cm ?? body.height_cm} cm</span>
            <DerivedFromLink project={p} />
            <span style={{ marginLeft: 'auto' }}>Playing: {anim ? assetLabel(anim) : 'nothing'}</span>
          </div>
          {project.error && <div style={{ padding: 12 }}><ErrorNote error={project.error} retry={reload} /></div>}
          {!p && project.loading ? <div className="viewer"><Loading what="crew member" /></div> : (
            <CrewViewer urls={p?.model_url ? [p.model_url] : []} animation={anim} playing
              onDuration={(_d, c) => setClips((old) => (old.join() === c.join() ? old : c))} />
          )}
          {jobs.length > 0 && <div style={{ padding: '8px 16px 0' }}><JobLines jobs={jobs} dismiss={dismiss} /></div>}
          <div style={{ display: 'flex', gap: 8, padding: '12px 16px', borderTop: '1px solid var(--border)', background: 'var(--panel)', flexWrap: 'wrap', marginTop: 8 }}>
            <button className="btn" style={{ height: 36, borderRadius: 8 }} disabled title="Saved outfits need a catalogue endpoint, which the API does not have yet">Save as outfit</button>
            <button className="btn" style={{ height: 36, borderRadius: 8 }} disabled={!p?.version || !!p?.draft} onClick={() => id && variant.show(id)}
              title="A new crew member copied from this one, changed as you describe; this one stays as it is">Make a variant</button>
            <Link to={paths.assembly()} className="btn primary" style={{ marginLeft: 'auto', height: 36, borderRadius: 8 }}>Put crew on the ship</Link>
          </div>
        </section>

        <aside className="side right" style={{ width: 380 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, padding: 12, borderBottom: '1px solid var(--border)' }} role="tablist">
            {SLOTS.map((s) => (
              <button key={s} role="tab" aria-selected={slot === s} onClick={() => setSlot(s)}
                style={{ height: 34, padding: '0 12px', borderRadius: 6, fontSize: 13, ...(slot === s ? SEL : { border: '1px solid transparent', background: 'transparent', color: 'var(--text-2)' }) }}>{s}</button>
            ))}
          </div>
          <div className="scroll" style={{ flex: 1, padding: 16, display: 'flex', flexDirection: 'column', gap: 18, fontSize: 13 }}>
            <ErrorNote error={catalog.error} retry={catalog.reload} />
            {slot === 'Body' && (
              <>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <span className="faint">Body</span>
                  <div style={{ display: 'flex', gap: 6 }}>
                    {(['female', 'male'] as const).map((g) => (
                      <button key={g} onClick={() => edit({ body: { gender: g } })}
                        style={{ height: 36, padding: '0 16px', borderRadius: 8, fontSize: 13, ...(body.gender === g ? SEL : OFF) }}>{g === 'female' ? 'Female' : 'Male'}</button>
                    ))}
                  </div>
                </div>
                <SliderRow id="h" label="Height" text={`${drafts.height_cm ?? body.height_cm} cm`} min={150} max={195} step={1} {...slider('height_cm', body.height_cm)} />
                <SliderRow id="w" label="Weight" text={pct(drafts.weight ?? body.weight)} min={0} max={1} step={0.05} {...slider('weight', body.weight)} />
                <SliderRow id="m" label="Build (muscle)" text={pct(drafts.muscle ?? body.muscle)} min={0} max={1} step={0.05} {...slider('muscle', body.muscle)} />
                <SliderRow id="a" label="Age" text={`${drafts.age_years ?? body.age_years} years`} min={18} max={65} step={1} {...slider('age_years', body.age_years)} />
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <span className="faint">Skin tone</span>
                  <div style={{ display: 'flex', gap: 10 }}>
                    {SKINS.map((k) => (
                      <button key={k.id} aria-label={`Skin tone ${k.id}`} title={k.id} onClick={() => edit({ body: { skin: k.id } })}
                        style={{ width: 40, height: 40, borderRadius: '50%', background: k.color, border: `3px solid ${body.skin === k.id ? 'var(--accent)' : 'transparent'}` }} />
                    ))}
                  </div>
                </div>
                <span className="faint" style={{ fontSize: 12 }}>Each change rebuilds the person in the human generator; it takes a little while.</span>
              </>
            )}
            {slot === 'Clothes' && (
              <>
                <Group label="Suit">
                  <Tile label="None" on={!clothes.suit} color="#2E333B" onClick={() => edit({ clothes: { suit: null } })} />
                  {groups.suit.map((c) => <Tile key={c} label={assetLabel(c)} on={clothes.suit === c} color={swatch(c)} onClick={() => edit({ clothes: { suit: c } })} />)}
                  <PromptTile />
                </Group>
                <Group label="Footwear">
                  <Tile label="None" on={!clothes.shoes} color="#2E333B" onClick={() => edit({ clothes: { shoes: null } })} />
                  {groups.shoes.map((c) => <Tile key={c} label={assetLabel(c)} on={clothes.shoes === c} color={swatch(c)} onClick={() => edit({ clothes: { shoes: c } })} />)}
                  <PromptTile />
                </Group>
              </>
            )}
            {slot === 'Head' && (
              <Group label="Hats and helmets">
                <Tile label="None" on={!clothes.hat} color="#2E333B" onClick={() => edit({ clothes: { hat: null } })} />
                {groups.hat.map((c) => <Tile key={c} label={assetLabel(c)} on={clothes.hat === c} color={swatch(c)} onClick={() => edit({ clothes: { hat: c } })} />)}
                <PromptTile />
              </Group>
            )}
            {slot === 'Equipment' && (
              <Group label="Carried or worn">
                {(catalog.data?.equipment ?? []).map((e) => (
                  <Tile key={e} label={assetLabel(e)} note={equipment.includes(e) ? 'Worn' : undefined} on={equipment.includes(e)} color={swatch(e)}
                    onClick={() => edit({ equipment: toggleIn(equipment, e) })} />
                ))}
                {equipment.filter((e) => !(catalog.data?.equipment ?? []).includes(e)).map((e) => (
                  <Tile key={e} label={assetLabel(e)} note="Not in catalogue" on color={swatch(e)} onClick={() => edit({ equipment: toggleIn(equipment, e) })} />
                ))}
                <PromptTile />
              </Group>
            )}
            {slot === 'Moves' && (
              <>
                <Group label="Animations">
                  {(catalog.data?.animations ?? animations).map((a) => {
                    const has = animations.includes(a);
                    return (
                      <Tile key={a} label={assetLabel(a)} note={has ? (anim === a ? 'Playing' : undefined) : 'Add'} on={has && anim === a} color={has ? '#2E333B' : 'transparent'}
                        onClick={() => (has ? setAnim(a) : edit({ animations: [...animations, a] }))} />
                    );
                  })}
                  <PromptTile />
                </Group>
                <Link to={paths.moves(id)} style={{ height: 44, borderRadius: 8, border: '1px solid var(--border-2)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--accent-link)' }}>Open the animation editor</Link>
              </>
            )}
            {slot === 'Outfits' && (
              <div className="card" style={{ lineHeight: 1.55 }}>
                <div style={{ fontWeight: 500, marginBottom: 6 }}>Saved outfits aren't available yet</div>
                <span className="muted">Saving a whole look, like "Boarding team", needs a place in the catalogue to keep it, and the server doesn't have one yet. For now, pick each item in Clothes, Head and Equipment.</span>
              </div>
            )}
          </div>
        </aside>
      </div>

      {dialog && (
        <div className="scrim" onClick={(e) => { if (e.target === e.currentTarget) setDialog(false); }}>
          <div role="dialog" aria-label="Add as a picker" className="dialog">
            <div style={{ fontSize: 18, fontWeight: 500 }}>Add as a picker?</div>
            <div className="muted" style={{ lineHeight: 1.5 }}>
              It goes into the catalogue, so you and anyone using this editor can pick it for any crew member.
            </div>
            <div style={{ lineHeight: 1.5, padding: 10, borderRadius: 8, background: 'var(--amber-bg)', color: 'var(--amber-text)', fontSize: 12 }}>
              For now this sends your request to the AI through the chat. The catalogue can't take new items directly yet, so check the AI's reply to see whether it was added.
            </div>
            <label htmlFor="nm" className="faint">Name</label>
            <input id="nm" className="field dark" value={pickerName} onChange={(e) => setPickerName(e.target.value)} placeholder="Boarding life vest, PCG pattern" />
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8, alignItems: 'center' }} className="muted">
              <label htmlFor="slot">Slot</label>
              <select id="slot" className="field dark" value={pickerSlot} onChange={(e) => setPickerSlot(e.target.value)}>
                <option>Clothes</option><option>Equipment</option><option>Animation</option>
              </select>
              <span>Evidence</span><span style={{ color: 'var(--text)' }}>Whatever research the AI did for it, in its reply</span>
              <span>Licence</span><span style={{ color: 'var(--text)' }}>Generated, yours</span>
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn lg" onClick={() => setDialog(false)}>Cancel</button>
              <button className="btn primary lg" onClick={confirmPicker}>Send to the AI</button>
            </div>
          </div>
        </div>
      )}
      {variant.dialog}
    </>
  );
}

const pct = (v: number) => `${Math.round(v * 100)}%`;

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <span className="faint">{label}</span>
      <div style={grid}>{children}</div>
    </div>
  );
}

function SliderRow({ id, label, text, ...input }: {
  id: string; label: string; text: string; min: number; max: number; step: number; value: number;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void; onPointerUp: () => void; onKeyUp: () => void; onBlur: () => void;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <label htmlFor={`crew-${id}`} style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--text-3)' }}>
        <span>{label}</span><span style={{ color: 'var(--text)' }}>{text}</span>
      </label>
      <input id={`crew-${id}`} type="range" {...input} />
    </div>
  );
}
