// Crew workshop. The right panel is the ten crew modules (CONTRACT §6 "Modules", approved mock
// "Crew workshop: modules"): each opens to presets, colours and "Or describe it". Presets and
// colours patch the spec through POST /spec (no AI); "Make it" sends the words through the crew
// chat scoped to that module, so only that module changes. The viewer plays the chosen animation.
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { AiWorking, ChatPanel, ErrorNote, JobLines, Loading, TopBar, useCarried } from '../components/common';
import { DerivedFromLink, useVariantDialog } from '../components/VariantDialog';
import { OtherWork, useProjectWork } from '../components/JobCenter';
import { remember, useCatalog, useJobs, useProject, useProjects } from '../hooks';
import { paths } from '../lib/routing';
import { assetLabel, groupClothes, mergePatch } from '../lib/spec';
import { GEAR_SWATCHES, HAIR_SWATCHES, hexToLinear, madeSomethingNew, removeItemPatch, selectedSwatch, type Swatch } from '../lib/describe';
import {
  ACCESSORIES, HAIR_STYLES, accessoryName, presetPatch, HATS, MODULES, PANTS_STYLES, PANTS_SWATCHES, placesFor, SHIRT_STYLES, SHIRT_SWATCHES, SHOE_SWATCHES, SKIN_TONES,
  changedModules, equipmentName, garmentColorPatch, garmentStylePatch, itemsInModule, moduleMessage, moduleSummary, outfitPatch,
  outfitWouldRemove, placePatch, placementRows, shoeLabel, suitLabel, suitsFor, toggleAccessory, toggleEquipment, type ModuleDef,
} from '../lib/modules';
import type { Attachment, CrewSpec, CustomItem, ModuleId, Vec3 } from '../types';
import { CrewViewer } from '../viewer/Viewer';
import { useCrewChat, useSpecEditor } from './CrewShared';

/** How long a row keeps its "changed" tag after the AI's turn. */
const CHANGED_MS = 6000;

type Row = ModuleId | 'moves' | 'outfits';

const SEL = { border: '1px solid var(--accent)', background: 'var(--accent-bg)', color: 'var(--accent-text)' };
const OFF = { border: '1px solid var(--border)', background: 'var(--panel-2)', color: 'var(--text)' };
const TILE: CSSProperties = {
  display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 6, height: 96, padding: 8,
  borderRadius: 10, fontSize: 12, textAlign: 'center', position: 'relative',
};

function Tile({ label, note, on, color, onClick }: { label: string; note?: string; on: boolean; color: string; onClick: () => void }) {
  return (
    <button onClick={onClick} style={{ ...TILE, ...(on ? SEL : OFF) }} aria-pressed={on}>
      <span style={{ display: 'block', width: 36, height: 36, borderRadius: 6, background: color, flexShrink: 0 }} />
      <span>{label}</span>
      {note && <span className="faint" style={{ fontSize: 11 }}>{note}</span>}
    </button>
  );
}

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
  // Work on this person carries on while the user is elsewhere; when it finishes, show the result.
  const work = useProjectWork(id, () => void reload(), jobs);
  const [open, setOpen] = useState<Row | null>('gender');
  const [anim, setAnim] = useState<string | null>(null);
  const [clips, setClips] = useState<string[]>([]);
  const [drafts, setDrafts] = useState<Record<string, number>>({});
  const [dialog, setDialog] = useState(false);
  const [pickerName, setPickerName] = useState('');
  const [pickerSlot, setPickerSlot] = useState('Equipment');
  const [answeredAt, setAnsweredAt] = useState<number | null>(null);
  const [itemsAtSend, setItemsAtSend] = useState<string[] | null>(null);
  const [said, setSaid] = useState<Partial<Record<ModuleId, string>>>({});
  const [describing, setDescribing] = useState<{ module: ModuleId; since: number } | null>(null);
  const [blank, setBlank] = useState<ModuleId | null>(null);
  const [changed, setChanged] = useState<ModuleId[]>([]);
  // A preset that would remove described items waits here for a yes (same confirm-first rule as Remove).
  const [pending, setPending] = useState<{ module: ModuleId; label: string; patch: Record<string, unknown>; gone: CustomItem[] } | null>(null);
  const changedTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => { if (id) remember('crew', id); }, [id]);
  useEffect(() => () => clearTimeout(changedTimer.current), []);
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
  const customItems = spec.custom_items ?? [];

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

  // Every chat turn (left Send or a module's "Make it") goes through here: it remembers the items
  // that existed, for the picker offer, and lights the rows the AI says it changed.
  const sendTracked = async (text: string, atts: Attachment[] = [], module?: ModuleId) => {
    setItemsAtSend(customItems.map((i) => i.id));
    const result = await sendChat(text, atts, module);
    const ids = changedModules(result);
    if (ids.length) {
      setChanged(ids);
      clearTimeout(changedTimer.current);
      changedTimer.current = setTimeout(() => setChanged([]), CHANGED_MS);
    }
  };
  const describe = async (m: ModuleId) => {
    const text = moduleMessage(m, said[m] ?? '');
    if (!text) { setBlank(m); return; }
    setBlank(null);
    setSaid((d) => ({ ...d, [m]: '' }));
    setDescribing({ module: m, since: Date.now() });
    try { await sendTracked(text, [], m); } finally { setDescribing(null); }
  };

  const msgs = p?.messages ?? [];
  const last = msgs[msgs.length - 1];
  const offerPicker = last?.who === 'AI' && answeredAt !== msgs.length && madeSomethingNew(itemsAtSend, customItems);

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

  /** Apply a preset; if it would remove described items, ask first. */
  const applyPreset = (m: ModuleId, label: string, patch: Record<string, unknown>, gone: CustomItem[]) => {
    if (gone.length) { setPending({ module: m, label, patch, gone }); return; }
    setPending(null);
    edit(patch);
  };
  const pickOutfit = (suit: string | null) =>
    applyPreset('outfit', suit ? suitLabel(suit) : 'None', outfitPatch(spec, suit), suit ? outfitWouldRemove(spec) : []);
  const pick = (m: ModuleId, label: string, patch: Record<string, unknown>) =>
    applyPreset(m, label, patch, itemsInModule(customItems, m));
  const removeItem = (itemId: string) => edit(removeItemPatch(customItems, itemId));
  const chip = (label: string, on: boolean, onClick: () => void, key?: string) => (
    <button key={key ?? label} className={`pick${on ? ' on' : ''}`} aria-pressed={on} onClick={onClick}>{label}</button>
  );

  /** Presets, colours, described items and the describe box for one module. */
  const moduleBody = (d: ModuleDef): ReactNode => {
    const m = d.id;
    const made = m === 'placement' ? [] : itemsInModule(customItems, m);
    let controls: ReactNode = null;
    switch (m) {
      case 'gender':
        controls = (
          <>
            <div className="picks">{(['male', 'female'] as const).map((g) => chip(g === 'female' ? 'Female' : 'Male', body.gender === g, () => edit({ body: { gender: g } })))}</div>
            <SliderRow id="h" label="Height" text={`${drafts.height_cm ?? body.height_cm} cm`} min={140} max={210} step={1} {...slider('height_cm', body.height_cm)} />
            <SliderRow id="w" label="Weight" text={pct(drafts.weight ?? body.weight)} min={0} max={1} step={0.05} {...slider('weight', body.weight)} />
            <SliderRow id="m" label="Build (muscle)" text={pct(drafts.muscle ?? body.muscle)} min={0} max={1} step={0.05} {...slider('muscle', body.muscle)} />
            <SliderRow id="p" label="Proportions" text={pct(drafts.proportions ?? body.proportions ?? 0.5)} min={0} max={1} step={0.05} {...slider('proportions', body.proportions ?? 0.5)} />
            <SliderRow id="a" label="Age" text={`${drafts.age_years ?? body.age_years} years`} min={18} max={80} step={1} {...slider('age_years', body.age_years)} />
          </>
        );
        break;
      case 'skin':
        controls = (
          <div className="swatches">
            <span className="module-label" style={{ width: 52 }}>Colour</span>
            {SKIN_TONES.map((k) => (
              <button key={k.id} className={`swatch${body.skin === k.id ? ' on' : ''}`} aria-label={`Skin colour ${k.label}`} aria-pressed={body.skin === k.id}
                title={k.label} style={{ background: k.hex }} onClick={() => edit({ body: { skin: k.id } })} />
            ))}
          </div>
        );
        break;
      case 'hair':
        controls = (
          <>
            <div className="picks">{HAIR_STYLES.map((h) => chip(h.label, (body.hair ?? null) === h.id, () => pick('hair', h.label, presetPatch(spec, 'hair', { body: { hair: h.id } })), h.label))}</div>
            {body.hair !== 'none' && <Swatches label="Hair colour" swatches={HAIR_SWATCHES} value={body.hair_color} withDefault onPick={(c) => edit({ body: { hair_color: c } })} />}
          </>
        );
        break;
      case 'shirt':
      case 'pants': {
        const styles = m === 'shirt' ? SHIRT_STYLES : PANTS_STYLES;
        const cur = clothes[m] ?? null;
        controls = (
          <>
            <div className="picks">
              {styles.map((s) => chip(s.label, cur?.style === s.id, () => pick(m, s.label, garmentStylePatch(spec, m, s.id)), s.id))}
            </div>
            {cur
              ? <Swatches label={`${d.name} colour`} swatches={m === 'shirt' ? SHIRT_SWATCHES : PANTS_SWATCHES} value={cur.color}
                  onPick={(c) => { const patch = c && garmentColorPatch(spec, m, c); if (patch) edit(patch); }} />
              : <span className="module-label">Pick a style to choose its colour.</span>}
            {clothes.suit && <span className="module-label">The full outfit is on now. Choosing a {m === 'shirt' ? 'shirt' : 'pair of pants'} takes it off.</span>}
            {!clothes.suit && <span className="module-label">Always worn: swap the style or describe another, but it can't be taken off.</span>}
          </>
        );
        break;
      }
      case 'outfit': {
        const suits = suitsFor(groups.suit, body.gender, clothes.suit);
        controls = (
          <>
            <div className="picks">
              {chip('Shirt and pants', !clothes.suit, () => pickOutfit(null))}
              {suits.map((s) => chip(suitLabel(s), clothes.suit === s, () => pickOutfit(s), s))}
            </div>
          </>
        );
        break;
      }
      case 'shoes':
        controls = (
          <>
            <div className="picks">
              {chip('Barefoot', !clothes.shoes, () => pick('shoes', 'Barefoot', presetPatch(spec, 'shoes', { clothes: { shoes: null } })))}
              {groups.shoes.map((s) => chip(shoeLabel(s), clothes.shoes === s, () => pick('shoes', shoeLabel(s), presetPatch(spec, 'shoes', { clothes: { shoes: s } })), s))}
            </div>
            {clothes.shoes && <Swatches label="Shoe colour" swatches={SHOE_SWATCHES} value={clothes.shoes_color} withDefault onPick={(c) => edit({ clothes: { shoes_color: c } })} />}
          </>
        );
        break;
      case 'equipment': {
        const names = equipment.map(equipmentName);
        const cat = catalog.data?.equipment ?? [];
        controls = (
          <div className="picks">
            {cat.map((e) => chip(assetLabel(e), names.includes(e), () => edit({ equipment: toggleEquipment(equipment, e) }), e))}
            {names.filter((e) => !cat.includes(e)).map((e) => chip(`${assetLabel(e)} (not in catalogue)`, true, () => edit({ equipment: toggleEquipment(equipment, e) }), e))}
          </div>
        );
        break;
      }
      case 'placement': {
        const rows = placementRows(spec);
        controls = rows.length ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {rows.map((r) => (
              <div key={r.key} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <span style={{ color: 'var(--text-soft)', overflowWrap: 'anywhere' }}>{r.label}{r.place === 'fixed' && <span className="faint"> · fixed</span>}</span>
                {r.place !== 'fixed' && (
                  <div className="picks">{placesFor(r).map((pl) => chip(pl.label, r.place === pl.id, () => edit(placePatch(spec, r, pl.id)), pl.id))}</div>
                )}
              </div>
            ))}
          </div>
        ) : <span className="module-label">Add equipment first; then choose where each item goes.</span>;
        break;
      }
      case 'accessories': {
        const acc = spec.accessories ?? [];
        const hats = groups.hat.length ? groups.hat : HATS.map((h) => h.id);
        controls = (
          <>
            <div className="picks">
              {chip('None', !acc.length && !clothes.hat, () => edit({ accessories: [], clothes: { hat: null } }))}
              {ACCESSORIES.map((a) => chip(a.label, acc.some((x) => accessoryName(x) === a.id), () => edit({ accessories: toggleAccessory(acc, a.id) }), a.id))}
              {hats.map((h) => chip(HATS.find((x) => x.id === h)?.label ?? assetLabel(h), clothes.hat === h, () => edit({ clothes: { hat: clothes.hat === h ? null : h } }), h))}
            </div>
            {clothes.hat && <Swatches label="Hat colour" swatches={GEAR_SWATCHES} value={clothes.hat_color} withDefault onPick={(c) => edit({ clothes: { hat_color: c } })} />}
          </>
        );
        break;
      }
    }
    return (
      <div className="module-body">
        {controls}
        {pending?.module === m && (
          <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: 10, lineHeight: 1.45 }}>
            <span>
              {m === 'outfit' ? `${pending.label} replaces the shirt and pants, and removes what was described for them: ` : `${pending.label} replaces what was described here: `}
              {pending.gone.map((i) => i.description || i.id).join(', ')}.
            </span>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn sm" onClick={() => setPending(null)}>{pending.gone.length > 1 ? 'Keep them' : 'Keep it'}</button>
              <button className="btn sm" style={{ color: 'var(--red-text)' }} onClick={() => { edit(pending.patch); setPending(null); }}>Use {pending.label}</button>
            </div>
          </div>
        )}
        {d.note && <span className="module-label">{d.note}</span>}
        <MadeList items={made} onRemove={removeItem} />
        {m === 'outfit'
          // The server cannot scope a described outfit, so this module has no describe box.
          ? <span className="module-label">To describe your own, use Shirt and Pants.</span>
          : <DescribeBox module={m} placeholder={d.placeholder} value={said[m] ?? ''} busy={busy} blank={blank === m}
              working={describing?.module === m ? describing.since : null}
              onChange={(v) => { setSaid((s) => ({ ...s, [m]: v })); if (blank === m) setBlank(null); }}
              onSend={() => void describe(m)} />}
      </div>
    );
  };

  const row = (key: Row, title: string, summary: string, content: () => ReactNode, isChanged = false) => {
    const isOpen = open === key;
    return (
      <div key={key} className={`module${isOpen ? ' open' : ''}${isChanged ? ' changed' : ''}`}>
        <button className="module-head" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : key)}>
          <span className="module-name">{title}</span>
          <span className="module-sum" title={summary}>{summary}</span>
          {isChanged && <span className="chip blue">changed</span>}
          <span className="faint" aria-hidden>{isOpen ? '−' : '+'}</span>
        </button>
        {isOpen && content()}
      </div>
    );
  };

  return (
    <>
      <TopBar title={name} back="crew">
        {/* One row that scrolls sideways (Lester's screenshot, 29 Oct: with many people the pills
            wrapped to a second line and pushed the screen down). Long names are cut, full on hover. */}
        <div className="topbar-right people-row">
          {people.map((x) => (
            <Link key={x.id} to={paths.crew(x.id)} onClick={() => remember('crew', x.id)} title={x.name}
              ref={x.id === id ? (el) => el?.scrollIntoView({ block: 'nearest', inline: 'nearest' }) : undefined}
              className="person-pill" style={x.id === id ? SEL : OFF}>{x.name}</Link>
          ))}
          <button className="btn dashed pill" style={{ flexShrink: 0 }} onClick={addPerson} disabled={busy}>+ Person</button>
        </div>
      </TopBar>
      <div className="workspace">
        <aside className="side left" style={{ width: 300 }}>
          <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', fontWeight: 500 }}>Chat</div>
          <ChatPanel
            key={id}
            messages={msgs}
            carried={carried}
            busy={busy}
            label="Ask about the whole person"
            placeholder="Ask about the whole person"
            empty="Ask for anything about the whole person here. To change one part only, open it on the right and use “Or describe it”."
            onSend={(text, atts) => sendTracked(text, atts)}
            projectId={id}
            working={work.chatWorking}
            footer={describing ? <AiWorking text="AI is making what you described…" since={describing.since} /> : undefined}
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
          {(jobs.length > 0 || work.others.length > 0) && (
            <div style={{ padding: '8px 16px 0', display: 'flex', flexDirection: 'column', gap: 6 }}>
              <JobLines jobs={jobs} dismiss={dismiss} />
              <OtherWork jobs={work.others} />
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, padding: '12px 16px', borderTop: '1px solid var(--border)', background: 'var(--panel)', flexWrap: 'wrap', marginTop: 8 }}>
            <button className="btn" style={{ height: 36, borderRadius: 8 }} disabled title="Saved outfits need a catalogue endpoint, which the API does not have yet">Save as outfit</button>
            <button className="btn" style={{ height: 36, borderRadius: 8 }} disabled={!p?.version || !!p?.draft} onClick={() => id && variant.show(id)}
              title="A new crew member copied from this one, changed as you describe; this one stays as it is">Make a variant</button>
            <Link to={paths.assembly()} className="btn primary" style={{ marginLeft: 'auto', height: 36, borderRadius: 8 }}>Put crew on the ship</Link>
          </div>
        </section>

        <aside className="side right modules-panel">
          <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontWeight: 500, fontSize: 15 }}>Build the person</span>
            <span className="faint" style={{ fontSize: 12 }}>Each part: pick a preset, set a colour, or describe it in words.</span>
          </div>
          <div className="modules">
            <ErrorNote error={catalog.error} retry={catalog.reload} />
            {MODULES.map((d) => row(d.id, d.name, moduleSummary(spec, d.id), () => moduleBody(d), changed.includes(d.id)))}
            <div className="module-group">Moves and saved looks</div>
            {row('moves', 'Moves', animations.length ? animations.map(assetLabel).join(', ') : 'None', () => (
              <div className="module-body">
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8 }}>
                  {(catalog.data?.animations ?? animations).map((a) => {
                    const has = animations.includes(a);
                    return (
                      <Tile key={a} label={assetLabel(a)} note={has ? (anim === a ? 'Playing' : undefined) : 'Add'} on={has && anim === a} color={has ? 'var(--border)' : 'transparent'}
                        onClick={() => (has ? setAnim(a) : edit({ animations: [...animations, a] }))} />
                    );
                  })}
                </div>
                <span className="module-label">Ask the chat on the left for a new move.</span>
                <Link to={paths.moves(id)} style={{ height: 44, borderRadius: 8, border: '1px solid var(--border-2)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--accent-link)' }}>Open the animation editor</Link>
              </div>
            ))}
            {row('outfits', 'Saved outfits', 'Not available yet', () => (
              <div className="module-body">
                <span className="muted" style={{ lineHeight: 1.55 }}>Saving a whole look, like "Boarding team", needs a place in the catalogue to keep it, and the server doesn't have one yet. For now, set each part above.</span>
              </div>
            ))}
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

/**
 * Colour swatches where a colour is the whole change: no AI, just a spec patch. `withDefault` adds
 * a dashed "Default" swatch that clears the colour (for hair, shoes and hats, where null means the
 * asset's own colour).
 */
function Swatches({ label, swatches, value, withDefault, onPick }: {
  label: string; swatches: Swatch[]; value: Vec3 | null | undefined; withDefault?: boolean; onPick: (c: Vec3 | null) => void;
}) {
  const on = selectedSwatch(value, swatches);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div className="swatches">
        <span className="module-label" style={{ width: 52 }}>Colour</span>
        {withDefault && (
          <button className={`swatch default${on === 'default' ? ' on' : ''}`} aria-label={`${label}: default`} aria-pressed={on === 'default'} title="Default" onClick={() => onPick(null)} />
        )}
        {swatches.map((s) => (
          <button key={s.id} className={`swatch${on === s.id ? ' on' : ''}`} aria-label={`${label}: ${s.label}`} aria-pressed={on === s.id}
            title={s.label} style={{ background: s.hex }} onClick={() => onPick(hexToLinear(s.hex))} />
        ))}
      </div>
      {on === null && <span className="module-label">A colour the AI chose. Pick one to replace it.</span>}
    </div>
  );
}

/** "Or describe it": words typed in a module, sent through the crew chat scoped to that module. */
function DescribeBox({ module, placeholder, value, busy, blank, working, onChange, onSend }: {
  module: ModuleId; placeholder: string; value: string; busy: boolean; blank: boolean; working: number | null;
  onChange: (v: string) => void; onSend: () => void;
}) {
  const fieldId = `describe-${module}`;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <label htmlFor={fieldId} className="module-label">Or describe it</label>
      <div style={{ display: 'flex', gap: 6 }}>
        <input id={fieldId} className="field" style={{ flex: 1, minWidth: 0, height: 36 }} value={value} placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !busy) onSend(); }} />
        <button className="btn" style={{ height: 36, borderRadius: 8, background: 'var(--bubble)', flexShrink: 0 }} onClick={onSend} disabled={busy}>Make it</button>
      </div>
      {blank && <span className="bad" style={{ fontSize: 12 }}>Describe it in a few words first.</span>}
      {working != null
        ? <AiWorking text="The AI is changing just this part. Its reply appears in the chat on the left." since={working} />
        : busy && <span className="faint" style={{ fontSize: 12 }}>Wait for the current change to finish.</span>}
    </div>
  );
}

/** What the AI made from descriptions for this module, each removable after a confirm (a rebuild). */
function MadeList({ items, onRemove }: { items: CustomItem[]; onRemove: (id: string) => void }) {
  const [asking, setAsking] = useState<string | null>(null);
  if (!items.length) return null;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span className="module-label">Made by description</span>
      {items.map((i) => (
        <div key={i.id} className="card" style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px', background: 'var(--bg)' }}>
          <span style={{ flex: 1, minWidth: 0, lineHeight: 1.4, overflowWrap: 'anywhere' }}>{i.description || i.id}</span>
          {asking === i.id ? (
            <>
              <button className="btn sm" onClick={() => setAsking(null)}>Keep</button>
              <button className="btn sm" style={{ color: 'var(--red-text)' }} onClick={() => { setAsking(null); onRemove(i.id); }}>Remove</button>
            </>
          ) : (
            <button className="btn sm" aria-label={`Remove ${i.description || i.id}`} onClick={() => setAsking(i.id)}>Remove</button>
          )}
        </div>
      ))}
    </div>
  );
}
