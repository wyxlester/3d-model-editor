import { Component, useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { MOCK, api, errorText } from '../api';
import { ACCEPT, fileType, isImage, readAsDataUrl, refuse, sizeText } from '../lib/attachments';
import { recall, useTicker, type RunningJob } from '../hooks';
import { describe } from '../lib/jobs';
import { paths } from '../lib/routing';
import type { Attachment, EvidenceStatus, Message } from '../types';

export function Logo() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#5B9BFF" strokeWidth="1.8" aria-hidden="true">
      <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" />
      <path d="M12 12l8-4.5M12 12v9M12 12L4 7.5" />
    </svg>
  );
}

/** Top bar: brand, the four workshops, then whatever the screen adds. */
export function TopBar({ title, children, home }: { title?: string; children?: ReactNode; home?: boolean }) {
  const loc = useLocation();
  const ship = recall('ship');
  const crew = recall('crew');
  const scene = recall('scene');
  const on = (prefix: string) => (loc.pathname.startsWith(prefix) ? 'active' : '');
  return (
    <header className="topbar">
      <Link to="/" className="brand"><Logo />{title ?? '3D model editor'}</Link>
      <nav className="nav" aria-label="Workshops">
        {home && <NavLink to="/" className={loc.pathname === '/' ? 'active' : ''} end>Home</NavLink>}
        <Link to={ship ? paths.shipBuild(ship) : '/ship'} className={on('/ship')}>Ship</Link>
        <Link to={crew ? paths.crew(crew) : '/crew'} className={on('/crew')}>Crew</Link>
        <Link to={paths.assembly(scene)} className={on('/assembly')}>Assembly</Link>
        <Link to={paths.review(scene)} className={on('/review')}>Review</Link>
      </nav>
      {MOCK && <span className="chip amber" title="VITE_MOCK=1: data and AI replies are canned, nothing reaches the server">Mock data</span>}
      {children}
    </header>
  );
}

/** Live progress for every job a screen started. Never leave the user staring at a frozen screen. */
export function JobLines({ jobs, dismiss }: { jobs: RunningJob[]; dismiss: (k: number) => void }) {
  const active = jobs.some((j) => j.state.phase === 'polling');
  useTicker(active);
  if (!jobs.length) return null;
  const now = Date.now();
  return (
    <div className="jobs" role="status" aria-live="polite">
      {jobs.map((j) => (
        <div key={j.key} className={`jobline ${j.state.phase === 'error' ? 'error' : j.state.phase === 'done' ? 'done' : ''}`}>
          {j.state.phase === 'polling' && <span className="spinner" aria-hidden="true" />}
          <span style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>{describe(j.state, now, j.label)}</span>
          {j.state.phase === 'error' && <button className="btn sm" onClick={() => dismiss(j.key)}>Dismiss</button>}
        </div>
      ))}
    </div>
  );
}

/** A spinner while the AI works, with seconds counting, so a wait never looks like a frozen screen (Lester, 29 Oct). */
export function AiWorking({ text = 'AI is working…', since }: { text?: string; since?: number }) {
  useTicker(since != null);
  const secs = since != null ? Math.max(0, Math.round((Date.now() - since) / 1000)) : null;
  return (
    <span className="ai-working" role="status">
      <span className="spinner" aria-hidden="true" />
      <span>{text}{secs != null && <span className="faint"> · {secs} s</span>}</span>
    </span>
  );
}

const bubbleClass = (who: string) => (who === 'You' ? 'me' : who === 'Customer' ? 'customer' : 'ai');

/** Pictures and documents on a message: thumbnails for pictures, a labelled link for the rest. */
export function AttachmentList({ items }: { items: Attachment[] }) {
  if (!items.length) return null;
  return (
    <span className="attach-list">
      {items.map((a) => isImage(a.type)
        ? <a key={a.id} href={a.url} target="_blank" rel="noreferrer" title={a.name}><img className="attach-thumb" src={a.url} alt={a.name} /></a>
        : <a key={a.id} className="attach-file" href={a.url} target="_blank" rel="noreferrer">{a.type === 'application/pdf' ? 'PDF' : 'Text'} · {a.name} · {sizeText(a.size)}</a>)}
    </span>
  );
}

interface Picked { key: number; name: string; state: 'uploading' | 'ready' | 'error'; att?: Attachment; error?: string; preview?: string }
type Upload = (file: { name: string; type: string; data: string }) => Promise<{ attachment: Attachment }>;

/**
 * Picking files to send with a message: the + button, drop, paste, and chips with a spinner while
 * each uploads. Shared by every chat panel and Home. `upload` null hides it all.
 */
export function useAttachPicker(upload: Upload | null, initial: Attachment[] = []) {
  const keyRef = useRef(0);
  const fileRef = useRef<HTMLInputElement>(null);
  const [picked, setPicked] = useState<Picked[]>(() => initial.map((att) => ({
    key: ++keyRef.current, name: att.name, state: 'ready', att, preview: isImage(att.type) ? att.url : undefined,
  })));
  const [dragging, setDragging] = useState(false);
  const uploading = picked.some((f) => f.state === 'uploading');
  const ready = picked.filter((f) => f.state === 'ready').map((f) => f.att!);

  const addFiles = (files: File[]) => {
    if (!upload) return;
    for (const file of files) {
      const key = ++keyRef.current;
      const update = (patch: Partial<Picked>) => setPicked((ps) => ps.map((f) => (f.key === key ? { ...f, ...patch } : f)));
      const why = refuse(file.name, file.type, file.size);
      const preview = !why && isImage(fileType(file.name, file.type)) ? URL.createObjectURL(file) : undefined;
      setPicked((ps) => [...ps, { key, name: file.name, state: why ? 'error' : 'uploading', error: why ?? undefined, preview }]);
      if (why) continue;
      readAsDataUrl(file)
        .then((data) => upload({ name: file.name, type: fileType(file.name, file.type), data }))
        .then((r) => update({ state: 'ready', att: r.attachment }), (e) => update({ state: 'error', error: `${file.name}: ${errorText(e)}` }));
    }
  };
  const revoke = (f: Picked) => { if (f.preview?.startsWith('blob:')) URL.revokeObjectURL(f.preview); };
  const remove = (key: number) => setPicked((ps) => {
    const gone = ps.find((f) => f.key === key);
    if (gone) revoke(gone);
    return ps.filter((f) => f.key !== key);
  });
  const clear = () => { picked.forEach(revoke); setPicked([]); };

  const dropProps = upload ? {
    onDragOver: (e: React.DragEvent) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); setDragging(true); } },
    onDragLeave: (e: React.DragEvent) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false); },
    onDrop: (e: React.DragEvent) => { e.preventDefault(); setDragging(false); addFiles([...e.dataTransfer.files]); },
  } : {};
  const onPaste = (e: React.ClipboardEvent) => { if (upload && e.clipboardData.files.length) { e.preventDefault(); addFiles([...e.clipboardData.files]); } };

  const button = upload ? <>
    <button type="button" className="btn muted" style={{ width: 40, height: 40, justifyContent: 'center', fontSize: 18, padding: 0, flexShrink: 0 }}
      aria-label="Attach pictures or documents" title="Attach pictures, PDFs or text files. You can also drop or paste them here."
      onClick={() => fileRef.current?.click()}>+</button>
    <input ref={fileRef} type="file" multiple accept={ACCEPT} hidden
      onChange={(e) => { addFiles([...(e.target.files ?? [])]); e.target.value = ''; }} />
  </> : null;
  const chips = picked.length > 0 ? (
    <div className="attach-chips">
      {picked.map((f) => (
        <span key={f.key} className={`attach-chip ${f.state}`}>
          {f.preview ? <img src={f.preview} alt="" /> : null}
          {f.state === 'uploading' && <span className="spinner" aria-hidden="true" />}
          <span className="attach-name">{f.state === 'error' ? f.error : f.name}</span>
          <button type="button" aria-label={`Remove ${f.name}`} onClick={() => remove(f.key)}>×</button>
        </span>
      ))}
    </div>
  ) : null;
  const note = uploading
    ? <span className="faint" style={{ fontSize: 12 }}>Uploading… Send is ready when it finishes.</span>
    : picked.length > 0 ? <span className="faint" style={{ fontSize: 12 }}>Files go to the AI in the cloud with your message: public material only.</span> : null;

  return { picked, ready, uploading, dragging, addFiles, clear, dropProps, onPaste, button, chips, note };
}

/** The text sent when only files were attached. */
export const filesOnlyText = (n: number) => (n === 1 ? 'Please look at the attached file.' : 'Please look at the attached files.');

/** The chat panel every screen carries (Lester, 29 Oct). With `projectId`, it also takes pictures and documents. */
export function ChatPanel(props: {
  messages: Message[];
  onSend: (text: string, attachments: Attachment[]) => Promise<unknown> | void;
  label: string;
  placeholder: string;
  projectId?: string;
  carried?: Message[] | null;
  busy?: boolean;
  intro?: ReactNode;
  footer?: ReactNode;
  afterLastAi?: ReactNode;
  empty?: string;
}) {
  const lastCarried = [...(props.carried ?? [])].reverse().find((m) => m.who === 'You');
  const [draft, setDraft] = useState(() => lastCarried?.text ?? '');
  const [err, setErr] = useState(false);
  // What was just sent, until the AI's job finishes: shown at once, with a spinner under it.
  const [waiting, setWaiting] = useState<{ text: string; atts: Attachment[]; from: number; since: number } | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const pid = props.projectId;
  // Files attached on Home come along pre-attached, ready to send with the carried request.
  const files = useAttachPicker(pid ? (f) => api.upload(pid, f) : null, lastCarried?.attachments ?? []);
  const { uploading, ready } = files;

  const send = async () => {
    if (uploading) return;
    const t = draft.trim() || (ready.length ? filesOnlyText(ready.length) : '');
    if (!t) { setErr(true); return; }
    const atts = ready;
    setDraft('');
    files.clear();
    setWaiting({ text: t, atts, from: props.messages.length, since: Date.now() });
    try { await props.onSend(t, atts); } finally { setWaiting(null); }
  };
  const lastAi = [...props.messages].map((m, i) => [m, i] as const).reverse().find(([m]) => m.who === 'AI')?.[1];
  const echoed = !!waiting && props.messages.slice(waiting.from).some((m) => m.who === 'You' && m.text === waiting.text);
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [props.messages.length, waiting]);
  return (
    <div className={`chat ${files.dragging ? 'dropping' : ''}`} {...files.dropProps}>
      <div className="chat-log" ref={logRef}>
        {props.intro}
        {props.carried && props.carried.length > 0 && (
          <>
            <div className="chat-carried">From Home</div>
            {props.carried.map((m, i) => <div key={`c${i}`} className={`bubble ${bubbleClass(m.who)}`}><AttachmentList items={m.attachments ?? []} />{m.text}</div>)}
            <div className="chat-carried">Nothing has been sent here yet. Edit the request below, then Send.</div>
          </>
        )}
        {props.messages.length === 0 && !props.carried?.length && props.empty && <div className="faint">{props.empty}</div>}
        {props.messages.map((m, i) => (
          <div key={i} className={`bubble ${bubbleClass(m.who)}`}>
            {m.who === 'Customer' && <span className="who">Customer</span>}
            <AttachmentList items={m.attachments ?? []} />
            {m.text}
            {i === lastAi && props.afterLastAi}
          </div>
        ))}
        {waiting && !echoed && <div className="bubble me"><AttachmentList items={waiting.atts} />{waiting.text}</div>}
        {waiting && <div className="bubble ai"><AiWorking since={waiting.since} /></div>}
      </div>
      <div className="chat-input">
        {props.footer}
        <label htmlFor="chat-ask">{props.label}</label>
        {files.chips}
        <div style={{ display: 'flex', gap: 8 }}>
          {files.button}
          <input
            id="chat-ask" className="field" style={{ flex: 1, minWidth: 0 }} value={draft} placeholder={props.placeholder}
            onChange={(e) => { setDraft(e.target.value); setErr(false); }}
            onKeyDown={(e) => { if (e.key === 'Enter') void send(); }}
            onPaste={files.onPaste}
          />
          <button className="btn primary lg" style={{ height: 40 }} onClick={() => void send()} disabled={props.busy || uploading}>Send</button>
        </div>
        {err && <span className="bad" style={{ fontSize: 12 }}>Type a request first.</span>}
        {files.note}
      </div>
    </div>
  );
}

/** Home's conversation, carried into the destination's chat. Shown, and pre-filled, but never sent silently. */
export function useCarried(): Message[] | null {
  const loc = useLocation();
  const st = loc.state as { carried?: Message[] } | null;
  return st?.carried ?? null;
}

export function ErrorNote({ error, retry }: { error: string | null; retry?: () => void }) {
  if (!error) return null;
  return (
    <div className="jobline error" role="alert">
      <span style={{ flex: 1 }}>{error}</span>
      {retry && <button className="btn sm" onClick={retry}>Try again</button>}
    </div>
  );
}

export function Loading({ what }: { what: string }) {
  return <div className="viewer-msg"><span className="spinner" style={{ marginRight: 8 }} />Loading {what}…</div>;
}

const EVIDENCE: Record<string, string> = {
  Official: 'green', Photographed: 'blue', 'Official + photo': 'green', 'Photo reading': 'violet',
  Inference: 'amber', 'Comparison only': 'grey', 'Secondary lead': 'amber', Unverified: 'red', Excluded: 'red',
};
export function EvidenceChip({ status }: { status: EvidenceStatus }) {
  return <span className={`chip ${EVIDENCE[status] ?? 'grey'}`}>{status}</span>;
}

export const PRIORITY: Record<string, string> = { P0: 'red', P1: 'amber', P2: 'grey', Fixed: 'green' };

export function Dot({ color }: { color: string }) {
  return <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, display: 'inline-block', flexShrink: 0 }} />;
}

export class ErrorBoundary extends Component<{ children: ReactNode; fallback: (e: Error) => ReactNode; resetKey?: unknown }, { error: Error | null; key: unknown }> {
  state = { error: null as Error | null, key: undefined as unknown };
  static getDerivedStateFromError(error: Error) { return { error }; }
  static getDerivedStateFromProps(p: { resetKey?: unknown }, s: { key: unknown; error: Error | null }) {
    return p.resetKey !== s.key ? { key: p.resetKey, error: null } : null;
  }
  render() { return this.state.error ? this.props.fallback(this.state.error) : this.props.children; }
}
