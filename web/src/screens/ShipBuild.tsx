// Ship workshop, Build mode (canvas: Ship.dc.html). The main way to change one part is a comment
// pinned on it: click the model, confirm the tag (with a level picker), write the request; the AI
// answers in the pin's thread and offers Keep / Undo with before and after versions.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api, errorText, modelUrl } from '../api';
import { AiWorking, ChatPanel, ErrorNote, JobLines, PRIORITY, TopBar, useCarried } from '../components/common';
import { DerivedFromLink, useVariantDialog } from '../components/VariantDialog';
import { remember, useJobs, useLoad, useProject } from '../hooks';
import { blenderToGltf, type ViewName } from '../lib/frames';
import { hiddenParts, isStationName, jointsOf, levelsFor, partLabel } from '../lib/parts';
import { groupPins, pinSummary, shortTime } from '../lib/pins';
import { paths } from '../lib/routing';
import type { Attachment, Part, Pin, Project, Vec3 } from '../types';
import { PIN_COLORS, ShipViewer, ViewButtons, type JointPreview, type PickResult } from '../viewer/Viewer';
import { ModeSwitch } from './ShipResearch';

const STATUS_TEXT: Record<string, string> = { open: 'Open', working: 'AI is working', waiting: 'Waiting for you', resolved: 'Resolved' };
const STATUS_COLOR: Record<string, string> = { open: '#F0C978', working: '#9CC2FF', waiting: '#9CC2FF', resolved: '#7FDDB0' };

interface Draft { part: string | null; chosen: string | null; point: Vec3; x: number; y: number }

export function ShipBuild() {
  const { id } = useParams();
  const [search] = useSearchParams();
  const { data: project, setData: setProject, error, reload } = useProject(id);
  const { jobs, run, dismiss, busy } = useJobs();
  const carried = useCarried();
  const stageRef = useRef<HTMLDivElement>(null);
  const variant = useVariantDialog();

  const [tab, setTab] = useState<'chat' | 'comments'>(carried?.length ? 'chat' : 'comments');
  const [pinMode, setPinMode] = useState(false);
  const [selPin, setSelPin] = useState<{ id: string; x: number; y: number } | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [draftText, setDraftText] = useState('');
  const [draftErr, setDraftErr] = useState<string | null>(null);
  const [inspect, setInspect] = useState(false);
  const [selPart, setSelPart] = useState<string | null>(search.get('part'));
  // The part lit on the model: only one the user asked to see (an issue marker or comment clicked,
  // "Show in Build"), never one just because the AI changed it. Cleared with its thread.
  const [shownPart, setShownPart] = useState<string | null>(search.get('part'));
  const [hover, setHover] = useState<string | null>(null);
  const [view, setView] = useState<ViewName>('port');
  const [viewNonce, setViewNonce] = useState(0);
  const [joints, setJoints] = useState<JointPreview>({});
  const [viewing, setViewing] = useState<number | null>(null); // an older version, read-only
  const [actionErr, setActionErr] = useState<string | null>(null);
  // Pins with an AI job in flight, and when it started: the spinner shows from the moment you send.
  const [aiPins, setAiPins] = useState<Record<string, number>>({});
  const trackPin = (pinId: string, on: boolean) => setAiPins((m) => {
    const next = { ...m };
    if (on) next[pinId] = Date.now(); else delete next[pinId];
    return next;
  });
  const aiWorking = (p: Pin) => p.status === 'working' || p.id in aiPins;

  useEffect(() => { if (id) remember('ship', id); }, [id]);
  useEffect(() => { if (search.get('part')) { setSelPart(search.get('part')); setShownPart(search.get('part')); setInspect(true); } }, [search]);
  const showVariant = variant.show;
  useEffect(() => {
    // Home routed here to make a variant: open the dialog with the request as the brief.
    if (id && search.get('variant')) showVariant(id, [...(carried ?? [])].reverse().find((m) => m.who === 'You')?.text ?? '');
  }, [id, search, carried, showVariant]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') { setPinMode(false); setDraft(null); setSelPin(null); setShownPart(null); } };
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, []);

  const current = project?.version ?? 0;
  const shownVersion = viewing ?? current;
  const olderParts = useLoad<Part[] | null>(() => (id && viewing ? api.parts(id, viewing) : Promise.resolve(null)), [id, viewing]);
  const parts = (viewing ? olderParts.data : null) ?? project?.parts ?? [];
  const partPaths = useMemo(() => parts.map((p) => p.path), [parts]);
  const url = !project ? null : viewing ? modelUrl(project.id, viewing) : project.model_url;
  // Comments are a record of the project, so they stay listed and pinned whichever version is shown.
  const pins = project?.pins ?? [];
  const pin = pins.find((p) => p.id === selPin?.id) ?? null;
  const openCount = pins.filter((p) => p.status !== 'resolved').length;

  /** Popover position inside the stage, from a screen point. */
  const local = (x: number, y: number) => {
    const r = stageRef.current?.getBoundingClientRect();
    return r ? { x: x - r.left, y: y - r.top, w: r.width, h: r.height } : { x: 40, y: 40, w: 800, h: 600 };
  };

  const afterJob = async () => { const p = await reload(); if (p) setProject(p); };

  const closeThread = () => { setSelPin(null); setShownPart(null); };
  /** Open an issue's thread because the user clicked it: its part lights up. */
  const openPin = (p: Pin, x: number, y: number) => { setSelPin({ id: p.id, x, y }); setSelPart(p.part); setShownPart(p.part); setDraft(null); };

  const onPick = (r: PickResult) => {
    setPinMode(false);
    closeThread();
    const l = local(r.clientX, r.clientY);
    setDraft({ part: r.part, chosen: r.part, point: r.point, x: l.x, y: l.y });
    setDraftText(''); setDraftErr(null);
    if (r.part) setSelPart(r.part);
  };

  const startHiddenDraft = (p: Part) => {
    const c: Vec3 = [(p.bounds_min[0] + p.bounds_max[0]) / 2, (p.bounds_min[1] + p.bounds_max[1]) / 2, (p.bounds_min[2] + p.bounds_max[2]) / 2];
    closeThread(); setViewing(null);
    setDraft({ part: p.path, chosen: p.path, point: blenderToGltf(c), x: 40, y: 40 });
    setSelPart(p.path); setDraftText(''); setDraftErr(null);
  };

  const sendDraft = () => {
    if (!id || !draft?.chosen) return;
    if (!draftText.trim()) { setDraftErr('Write what you want changed first.'); return; }
    const d = draft, text = draftText;
    setDraft(null);
    let pinId: string | null = null;
    void run(`Working on ${partLabel(d.chosen!)}`, async () => {
      const r = await api.addPin(id, { part: d.chosen!, point: d.point, text, author: 'You' });
      pinId = r.pin.id;
      trackPin(pinId, true);
      await afterJob();
      setSelPin({ id: r.pin.id, x: d.x, y: d.y });
      setTab('comments');
      return r;
    }, afterJob, afterJob).finally(() => { if (pinId) trackPin(pinId, false); });
  };

  const replyPin = (pinId: string, part: string, text: string) => {
    if (!id) return;
    trackPin(pinId, true);
    void run(`Working on ${partLabel(part)}`, async () => { const r = await api.replyPin(id, pinId, text); void afterJob(); return r; }, afterJob, afterJob)
      .finally(() => trackPin(pinId, false));
  };

  const pinAction = async (fn: () => Promise<unknown>) => {
    setActionErr(null);
    try { await fn(); await afterJob(); } catch (e) { setActionErr(errorText(e)); }
  };

  const chat = async (message: string, atts: Attachment[] = []) => {
    if (!id) return;
    await run('Working on the ship', async () => { const r = await api.chat(id, message, 'build', atts.map((a) => a.id)); void afterJob(); return r; }, afterJob, afterJob);
  };

  const undo = () => pinAction(async () => { if (id) { const r = await api.undo(id); setProject(r.project); } });

  const report = project?.build_report ?? {};
  const building = !!project && !project.model_url;
  const hint = viewing ? `Viewing older v${viewing}, read-only. Commenting takes you back to the current version.`
    : pinMode ? (hover ? `Pointing at ${partLabel(hover)}. Click to comment. Esc to cancel.` : 'Click any part of the model. Esc to cancel.')
      : 'Or click a pin to open its thread.';

  return (
    <>
      <TopBar title={project?.name} back="ship">
        {id && <ModeSwitch id={id} mode="build" />}
        <div className="topbar-right">
          <span>{project ? `${project.name} · ${current ? `v${current}` : 'not built yet'} · ${busy ? 'working…' : 'saved'}` : ''}</span>
          <DerivedFromLink project={project} />
          {project && project.versions.length > 0 && (
            <select className="field" aria-label="Version shown" value={shownVersion} onChange={(e) => { const n = Number(e.target.value); setViewing(n === current ? null : n); setDraft(null); }}>
              {[...project.versions].reverse().map((v) => (
                <option key={v.n} value={v.n}>v{v.n}{v.n === current ? ' (current)' : ''}{v.ok === false ? ' · failed' : ''} · {v.summary}</option>
              ))}
            </select>
          )}
          <button className="btn" onClick={() => id && variant.show(id)} disabled={!current || !!project?.draft}
            title="A new ship copied from this one, changed as you describe; this one stays as it is">Make a variant</button>
          <button className="btn" onClick={() => void undo()} disabled={!project || project.can_undo === false || busy || !!viewing}
            title={project?.can_undo === false ? 'Nothing to undo' : 'Go back to the previous version'}>Undo last change</button>
        </div>
      </TopBar>
      <div className="workspace">
        <aside className="side left" style={{ width: 320 }}>
          <div className="tabs" role="tablist">
            <button role="tab" className={`tab ${tab === 'chat' ? 'on' : ''}`} onClick={() => setTab('chat')}>Chat</button>
            <button role="tab" className={`tab ${tab === 'comments' ? 'on' : ''}`} onClick={() => setTab('comments')}>Comments ({pins.length})</button>
          </div>
          {tab === 'chat' ? (
            <ChatPanel messages={project?.messages ?? []} carried={carried} onSend={chat} busy={busy} projectId={id}
              label="Ask about the whole ship" placeholder="What's still unverified?"
              empty="For a change to one part, pin a comment on it: the AI will know exactly which part you mean. Use this chat for the whole ship." />
          ) : (
            <CommentsTab pins={pins} parts={parts} selected={selPin?.id ?? null} viewingOld={!!viewing} working={aiWorking}
              onPick={(p) => openPin(p, 40, 40)}
              onHidden={startHiddenDraft} />
          )}
        </aside>

        <section className="stage">
          <div className="toolbar">
            <button className={`btn ${pinMode ? 'primary' : 'selected'}`} style={{ padding: '0 14px' }} disabled={!url}
              onClick={() => { if (viewing) { setViewing(null); setPinMode(true); } else setPinMode(!pinMode); closeThread(); setDraft(null); }}>
              {pinMode ? 'Placing a comment…' : 'Comment on a part'}
            </button>
            <span className="faint toolbar-hint" title={hint}>{hint}</span>
            {viewing && <button className="btn primary sm" onClick={() => setViewing(null)}>Back to current v{current}</button>}
            <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
              <ViewButtons view={view} onView={(v) => { setView(v); setViewNonce((n) => n + 1); }} />
              <button className={`btn ${inspect ? 'selected' : 'muted'}`} onClick={() => setInspect(!inspect)}>Inspect</button>
            </span>
          </div>
          <ShipViewer
            containerRef={stageRef}
            url={url}
            emptyText={building ? 'Building the first version… the model appears here when the build finishes.' : error ? 'Could not load the project.' : 'Loading…'}
            view={view} viewNonce={viewNonce}
            partPaths={partPaths}
            pickMode={pinMode}
            onPick={onPick}
            onHover={setHover}
            selectedPart={draft?.chosen ?? shownPart}
            joints={joints}
            pins={pins}
            selectedPinId={selPin?.id ?? null}
            onPinClick={(p, x, y) => { const l = local(x, y); openPin(p, l.x, l.y); }}
            overlay={<>
              {pin && project && selPin && (
                <Thread project={project} pin={pin} n={pins.indexOf(pin) + 1} at={selPin} stage={stageRef.current} shown={shownVersion}
                  busy={busy} error={actionErr} working={aiWorking(pin)} since={aiPins[pin.id]}
                  onClose={closeThread}
                  onKeep={() => void pinAction(() => api.setPinStatus(project.id, pin.id, 'resolved'))}
                  onUndo={() => void pinAction(async () => { const r = await api.undo(project.id); setProject(r.project); await api.setPinStatus(project.id, pin.id, 'open'); })}
                  onReopen={() => void pinAction(() => api.setPinStatus(project.id, pin.id, 'open'))}
                  onReply={(text) => replyPin(pin.id, pin.part, text)}
                  onInspect={() => { setSelPart(pin.part); setShownPart(pin.part); setInspect(true); }}
                  onShowVersion={(n) => setViewing(n === current ? null : n)}
                />
              )}
              {draft && (
                <DraftBox draft={draft} parts={parts} stage={stageRef.current} text={draftText} error={draftErr}
                  setText={(t) => { setDraftText(t); setDraftErr(null); }}
                  setChosen={(c) => { setDraft({ ...draft, chosen: c }); setSelPart(c); }}
                  onCancel={() => setDraft(null)} onSend={sendDraft} onRetry={() => { setDraft(null); setPinMode(true); }} />
              )}
            </>}
          />
          <div style={{ padding: jobs.length || error ? '8px 16px 0' : 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
            <ErrorNote error={error} retry={reload} />
            <JobLines jobs={jobs} dismiss={dismiss} />
          </div>
          <div className="statusbar">
            {project && !project.model_url && <span className="faint">No build yet</span>}
            {report.ok === true && <span className="ok">✓ Build passed its checks</span>}
            {report.ok === false && <span className="bad">✗ Build failed</span>}
            {(report.errors ?? []).map((e, i) => <span key={`e${i}`} className="bad">✗ {e}</span>)}
            {(report.warnings ?? []).map((w, i) => <span key={`w${i}`} className="warn">! {w}</span>)}
            {report.triangles !== undefined && <span className="muted">{report.triangles.toLocaleString()} triangles</span>}
            <span style={{ marginLeft: 'auto' }} className="muted">{openCount} open comments</span>
          </div>
        </section>

        {inspect && project && (
          <Inspect project={project} parts={parts} part={selPart} joints={joints} setJoints={setJoints} busy={busy}
            onClose={() => setInspect(false)}
            onPatch={(label, patch) => void run(label, () => api.patchSpec(project.id, patch), afterJob, afterJob)} />
        )}
      </div>
      {variant.dialog}
    </>
  );
}

function CommentsTab({ pins, parts, selected, onPick, onHidden, viewingOld, working }: {
  pins: Pin[]; parts: Part[]; selected: string | null; viewingOld: boolean; working: (p: Pin) => boolean;
  onPick: (p: Pin) => void; onHidden: (p: Part) => void;
}) {
  const hidden = hiddenParts(parts);
  const all = parts.filter((p) => !isStationName(p.path));
  return (
    <div className="scroll" style={{ flex: 1, minHeight: 0, padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {viewingOld && <span className="faint">Showing an older version. Comments still open here; new ones go on the current version.</span>}
      {(['open', 'resolved'] as const).map((group) => {
        const rows = groupPins(pins)[group];
        if (!rows.length) return null;
        return (
          <div key={group} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <span className="faint" style={{ fontSize: 12 }}>{group === 'open' ? `Open (${rows.length})` : `Resolved (${rows.length})`}</span>
            {rows.map(({ pin: p, n }) => {
              const s = pinSummary(p);
              return (
                <button key={p.id} className={`row-btn ${selected === p.id ? 'on' : ''}`} onClick={() => onPick(p)}>
                  <span style={{ flexShrink: 0, width: 24, height: 24, borderRadius: 12, background: PIN_COLORS[p.status], color: 'var(--on-accent)', fontSize: 12, fontWeight: 600, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{n}</span>
                  <span style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 }}>
                    <span style={{ fontWeight: 500 }}>{partLabel(p.part)}</span>
                    <span style={{ fontSize: 12 }}>{s.first}</span>
                    {s.latest && s.replies > 0 && <span className="muted" style={{ fontSize: 12, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>{s.latest.who}: {s.latest.text}</span>}
                    <span style={{ fontSize: 11, color: STATUS_COLOR[working(p) ? 'working' : p.status], display: 'flex', alignItems: 'center', gap: 6 }}>{working(p) && <span className="spinner" aria-hidden="true" />}{STATUS_TEXT[working(p) ? 'working' : p.status] ?? p.status} ·{s.replies} {s.replies === 1 ? 'reply' : 'replies'}{s.lastAt ? ` · ${shortTime(s.lastAt)}` : ''}</span>
                  </span>
                </button>
              );
            })}
          </div>
        );
      })}
      {!pins.length && <span className="faint">No comments yet. Press “Comment on a part” and click the model.</span>}
      <div style={{ marginTop: 8, borderTop: '1px solid var(--border)', paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <span className="faint" style={{ fontSize: 12 }}>Can't click it? Comment on a hidden part</span>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {hidden.map((p) => <button key={p.path} className="btn pill dashed" style={{ fontSize: 12 }} onClick={() => onHidden(p)}>{partLabel(p.path)}, underwater</button>)}
        </div>
        <select className="field" aria-label="Comment on any part" value="" onChange={(e) => { const p = all.find((x) => x.path === e.target.value); if (p) onHidden(p); }}>
          <option value="">Any part, from the list…</option>
          {all.map((p) => <option key={p.path} value={p.path}>{partLabel(p.path)}</option>)}
        </select>
      </div>
    </div>
  );
}

/** Position a 300 px popover beside a point in the stage, kept inside it. */
function boxStyle(x: number, y: number, stage: HTMLElement | null): React.CSSProperties {
  const w = stage?.clientWidth ?? 900, h = stage?.clientHeight ?? 600;
  const left = x > w / 2 ? Math.max(8, x - 316) : Math.min(w - 308, x + 16);
  const top = Math.max(8, Math.min(y - 40, h - 320));
  return { left, top };
}

function Thread({ project, pin, n, at, stage, shown, busy, error, working, since, onClose, onKeep, onUndo, onReopen, onReply, onInspect, onShowVersion }: {
  project: Project; pin: Pin; n: number; at: { x: number; y: number }; stage: HTMLElement | null; busy: boolean; error: string | null;
  working: boolean; since?: number;
  onClose: () => void; onKeep: () => void; onUndo: () => void; onReopen: () => void; onReply: (t: string) => void; onInspect: () => void; onShowVersion: (n: number) => void;
  shown: number;
}) {
  const [reply, setReply] = useState('');
  const old = shown !== project.version;
  const undoable = pin.after_version === project.version && project.can_undo !== false;
  const send = () => { if (reply.trim()) { onReply(reply.trim()); setReply(''); } };
  return (
    <div role="dialog" aria-label="Comment thread" className="popover" style={boxStyle(at.x, at.y, stage)}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <span className="tag">{partLabel(pin.part)}</span>
        <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: STATUS_COLOR[pin.status] }}>{STATUS_TEXT[pin.status] ?? pin.status}</span>
          <button className="btn sm" aria-label="Close thread" onClick={onClose} style={{ width: 26, padding: 0, justifyContent: 'center' }}>×</button>
        </span>
      </div>
      <span className="faint" style={{ fontSize: 11 }}>Pin {n} · {pin.author} · attached to the part, follows it through rebuilds</span>
      {pin.messages.map((m, i) => (
        <div key={i} className={`msg ${m.who === 'AI' ? 'ai' : m.who === 'Customer' ? 'customer' : 'me'}`}>
          <span className="faint" style={{ fontSize: 11 }}>{m.who}{m.at ? ` · ${shortTime(m.at)}` : ''}</span><span>{m.text}</span>
        </div>
      ))}
      {working && <AiWorking text="The AI is making this change…" since={since} />}
      {pin.before_version != null && pin.after_version != null && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 6, fontSize: 11 }}>
          {[['Before', pin.before_version!], ['After', pin.after_version!]].map(([label, v]) => (
            <button key={label} aria-pressed={shown === v} onClick={() => onShowVersion(v as number)}
              style={{ height: 54, borderRadius: 6, border: shown === v ? '1px solid var(--accent)' : '1px solid transparent', background: shown === v ? 'var(--accent-bg)' : 'var(--bubble)', color: shown === v ? 'var(--accent-text)' : 'var(--text-3)' }}>
              {label} · v{v}<br />{shown === v ? 'showing' : 'show'}
            </button>
          ))}
        </div>
      )}
      {old && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12 }} className="muted">
          <span>Showing older v{shown}. To reply or keep it, go back.</span>
          <button className="btn sm primary" onClick={() => onShowVersion(project.version)}>Back to current</button>
        </div>
      )}
      {!old && <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {pin.status === 'waiting' && <>
          <button className="btn sm primary" onClick={onKeep} disabled={busy}>Keep it</button>
          <button className="btn sm" onClick={onUndo} disabled={busy || !undoable}
            title={undoable ? 'Go back to the version before this change' : 'A later change was made on top of this one, so undo would remove the wrong change. Reply to ask for a fix instead.'}>Undo</button>
        </>}
        {pin.status === 'open' && <button className="btn sm primary" disabled={busy} onClick={() => onReply('Please make this change.')}>Ask AI to fix</button>}
        {pin.status === 'resolved' && <button className="btn sm" onClick={onReopen}>Reopen</button>}
        <button className="btn sm" onClick={onInspect}>Inspect</button>
      </div>}
      {error && <span className="bad" style={{ fontSize: 12 }}>{error}</span>}
      {!old && <>
        <label htmlFor="reply" className="sr-only">Reply</label>
        <input id="reply" className="field dark" style={{ height: 34, fontSize: 12 }} placeholder="Reply or ask for another change" value={reply}
          onChange={(e) => setReply(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') send(); }} disabled={working} />
      </>}
    </div>
  );
}

function DraftBox({ draft, parts, stage, text, error, setText, setChosen, onCancel, onSend, onRetry }: {
  draft: Draft; parts: Part[]; stage: HTMLElement | null; text: string; error: string | null;
  setText: (t: string) => void; setChosen: (c: string) => void; onCancel: () => void; onSend: () => void; onRetry: () => void;
}) {
  if (!draft.part) {
    return (
      <div role="dialog" aria-label="New comment" className="popover" style={boxStyle(draft.x, draft.y, stage)}>
        <span>That spot isn't a named part, so a comment there would not follow anything.</span>
        <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
          <button className="btn sm" onClick={onCancel}>Cancel</button>
          <button className="btn sm primary" onClick={onRetry}>Try again</button>
        </div>
      </div>
    );
  }
  const levels = levelsFor(draft.part, parts);
  return (
    <div role="dialog" aria-label="New comment" className="popover" style={boxStyle(draft.x, draft.y, stage)}>
      <span className="faint" style={{ fontSize: 12 }}>You're pointing at</span>
      <span className="tag" style={{ fontSize: 13, padding: '4px 10px', alignSelf: 'flex-start' }}>{partLabel(draft.chosen ?? draft.part)}</span>
      {levels.length > 1 && <>
        <span className="faint" style={{ fontSize: 11 }}>Not quite? Pick the right level:</span>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {levels.map((l) => <button key={l.path} className={`level ${l.path === draft.chosen ? 'on' : ''}`} onClick={() => setChosen(l.path)}>{l.label}</button>)}
        </div>
      </>}
      <label htmlFor="draftText" className="sr-only">Comment</label>
      <textarea id="draftText" rows={2} className="field dark" autoFocus value={text} onChange={(e) => setText(e.target.value)}
        placeholder="Too far forward compared with the photos" onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) onSend(); }} />
      {error && <span className="bad" style={{ fontSize: 12 }}>{error}</span>}
      <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
        <button className="btn sm" onClick={onCancel}>Cancel</button>
        <button className="btn sm primary" onClick={onSend}>Send to AI</button>
      </div>
    </div>
  );
}

const fmt = (v: number) => (Math.round(v * 100) / 100).toString();

function Inspect({ project, parts, part, joints, setJoints, busy, onClose, onPatch }: {
  project: Project; parts: Part[]; part: string | null; joints: JointPreview; setJoints: (j: JointPreview) => void; busy: boolean;
  onClose: () => void; onPatch: (label: string, patch: Record<string, unknown>) => void;
}) {
  const p = parts.find((x) => x.path === part) ?? null;
  const allJoints = jointsOf(parts, project.spec.joint_limits);
  const mine = allJoints.find((j) => j.part.path === part) ?? null;
  const others = allJoints.filter((j) => j.part.path !== part);
  const art = (project.research?.articulation ?? []).find((a) => a.part === part);
  const ov = (part && project.spec.overrides?.[part]) || {};
  const [offset, setOffset] = useState<Vec3>(ov.offset_m ?? [0, 0, 0]);
  const [rotate, setRotate] = useState<Vec3>(ov.rotate_deg ?? [0, 0, 0]);
  const [scale, setScale] = useState<Vec3>(ov.scale ?? [1, 1, 1]);
  const [hiddenFlag, setHidden] = useState<boolean>(!!ov.hidden);
  const [params, setParams] = useState<Record<string, string>>({});
  useEffect(() => {
    const o = (part && project.spec.overrides?.[part]) || {};
    setOffset(o.offset_m ?? [0, 0, 0]); setRotate(o.rotate_deg ?? [0, 0, 0]); setScale(o.scale ?? [1, 1, 1]); setHidden(!!o.hidden);
  }, [part, project.spec.overrides]);

  const slider = (path: string, j: { axis: string; min_deg: number; max_deg: number }, label: string) => {
    const v = joints[path]?.deg ?? 0;
    return (
      <div key={path} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <label htmlFor={`j-${path}`} style={{ display: 'flex', justifyContent: 'space-between' }} className="muted">
          <span>{label}</span><span style={{ color: 'var(--text)' }}>{Math.round(v)}°</span>
        </label>
        <input id={`j-${path}`} type="range" min={j.min_deg} max={j.max_deg} step={1} value={v}
          onChange={(e) => setJoints({ ...joints, [path]: { axis: j.axis, deg: Number(e.target.value) } })} />
        <span className="faint" style={{ fontSize: 11 }}>{j.min_deg}° to {j.max_deg}° about {j.axis.toUpperCase()} · preview only, not saved</span>
      </div>
    );
  };
  const vec = (label: string, v: Vec3, set: (v: Vec3) => void, step: number) => (
    <>
      <span>{label}</span>
      <span style={{ display: 'flex', gap: 4 }}>
        {v.map((x, i) => (
          <input key={i} aria-label={`${label} ${'xyz'[i]}`} className="field dark" type="number" step={step} value={x} style={{ height: 28, width: '100%', padding: '0 6px', fontSize: 12 }}
            onChange={(e) => { const n = [...v] as Vec3; n[i] = Number(e.target.value); set(n); }} />
        ))}
      </span>
    </>
  );
  const numericParams = Object.entries(project.spec.params ?? {}).filter(([, v]) => typeof v === 'number') as [string, number][];

  return (
    <aside className="side right scroll" style={{ width: 320, padding: 16, gap: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span className="faint" style={{ fontSize: 12 }}>Inspect</span>
        <button className="btn sm" aria-label="Close inspect" onClick={onClose} style={{ width: 30, justifyContent: 'center', padding: 0 }}>×</button>
      </div>
      {p ? <>
        <span style={{ fontSize: 16, fontWeight: 500 }}>{partLabel(p.path)}</span>
        <span style={{ display: 'flex', gap: 8 }}>
          {mine ? <span className={`chip ${PRIORITY[mine.joint.priority] ?? 'grey'}`}>{mine.joint.priority} · moves</span> : <span className="chip green">Fixed</span>}
          {art && <span className="chip blue">In research: {art.motion}</span>}
        </span>
        {mine && slider(p.path, mine.joint, `${mine.joint.kind === 'revolute' ? 'Rotation' : mine.joint.kind}`)}
        <div className="kv">
          <span>Path</span><span className="mono" style={{ fontSize: 12 }}>{p.path}</span>
          <span>Inside</span><span>{p.parent ? partLabel(p.parent) : 'Top level'}</span>
          <span>Size</span><span>{[0, 1, 2].map((i) => fmt(p.bounds_max[i] - p.bounds_min[i])).join(' × ')} m</span>
          {p.origin_m && <><span>Pivot</span><span>{p.origin_m.map(fmt).join(', ')} m</span></>}
          <span>Triangles</span><span>{p.triangles.toLocaleString()}</span>
        </div>
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <span className="faint" style={{ fontSize: 12 }}>Exact values (Blender frame: +X bow, +Y port, +Z up)</span>
          <div className="kv" style={{ alignItems: 'center' }}>
            {vec('Move, m', offset, setOffset, 0.05)}
            {vec('Rotate, °', rotate, setRotate, 1)}
            {vec('Scale', scale, setScale, 0.05)}
            <span>Hidden</span><span><input type="checkbox" checked={hiddenFlag} onChange={(e) => setHidden(e.target.checked)} /></span>
          </div>
          <button className="btn" disabled={busy} onClick={() => onPatch(`Rebuilding with ${partLabel(p.path)} changed`, { overrides: { [p.path]: { ...ov, offset_m: offset, rotate_deg: rotate, scale, hidden: hiddenFlag } } })}>Apply and rebuild</button>
        </div>
        <Link to={paths.shipResearch(project.id)} className="btn" style={{ justifyContent: 'center', height: 34, borderRadius: 8, fontSize: 12 }}>Open in Research</Link>
      </> : <span className="muted">Click a pin or a part (in comment mode) to inspect it.</span>}

      {others.length > 0 && (
        <details style={{ borderTop: '1px solid var(--border)', paddingTop: 12 }}>
          <summary className="muted" style={{ cursor: 'pointer' }}>Other moving parts ({others.length})</summary>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 10 }}>
            {others.map((j) => slider(j.part.path, j.joint, partLabel(j.part.path)))}
            <button className="btn sm" onClick={() => setJoints({})}>Reset all to rest</button>
          </div>
        </details>
      )}

      {numericParams.length > 0 && (
        <details style={{ borderTop: '1px solid var(--border)', paddingTop: 12 }}>
          <summary className="muted" style={{ cursor: 'pointer' }}>Ship parameters</summary>
          <div className="kv" style={{ marginTop: 10, alignItems: 'center', gridTemplateColumns: '120px minmax(0, 1fr)' }}>
            {numericParams.map(([k, v]) => (
              <span key={k} style={{ display: 'contents' }}>
                <label htmlFor={`param-${k}`}>{k.replace(/_/g, ' ')}</label>
                <input id={`param-${k}`} className="field dark" type="number" style={{ height: 28, fontSize: 12 }} value={params[k] ?? String(v)} onChange={(e) => setParams({ ...params, [k]: e.target.value })} />
              </span>
            ))}
          </div>
          <button className="btn" style={{ marginTop: 10 }} disabled={busy || !Object.keys(params).length}
            onClick={() => { const patch = Object.fromEntries(Object.entries(params).map(([k, v]) => [k, Number(v)])); onPatch('Rebuilding with new parameters', { params: patch }); setParams({}); }}>Apply and rebuild</button>
        </details>
      )}
    </aside>
  );
}
