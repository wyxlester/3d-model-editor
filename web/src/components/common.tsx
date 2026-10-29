import { Component, useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { MOCK } from '../api';
import { recall, useTicker, type RunningJob } from '../hooks';
import { describe } from '../lib/jobs';
import { paths } from '../lib/routing';
import type { EvidenceStatus, Message } from '../types';

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

/** The chat panel every screen carries (Lester, 29 Oct). */
export function ChatPanel(props: {
  messages: Message[];
  onSend: (text: string) => Promise<unknown> | void;
  label: string;
  placeholder: string;
  carried?: Message[] | null;
  busy?: boolean;
  intro?: ReactNode;
  footer?: ReactNode;
  afterLastAi?: ReactNode;
  empty?: string;
}) {
  const [draft, setDraft] = useState(() => [...(props.carried ?? [])].reverse().find((m) => m.who === 'You')?.text ?? '');
  const [err, setErr] = useState(false);
  // What was just sent, until the AI's job finishes: shown at once, with a spinner under it.
  const [waiting, setWaiting] = useState<{ text: string; from: number; since: number } | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const send = async () => {
    const t = draft.trim();
    if (!t) { setErr(true); return; }
    setDraft('');
    setWaiting({ text: t, from: props.messages.length, since: Date.now() });
    try { await props.onSend(t); } finally { setWaiting(null); }
  };
  const lastAi = [...props.messages].map((m, i) => [m, i] as const).reverse().find(([m]) => m.who === 'AI')?.[1];
  const echoed = !!waiting && props.messages.slice(waiting.from).some((m) => m.who === 'You' && m.text === waiting.text);
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [props.messages.length, waiting]);
  return (
    <div className="chat">
      <div className="chat-log" ref={logRef}>
        {props.intro}
        {props.carried && props.carried.length > 0 && (
          <>
            <div className="chat-carried">From Home</div>
            {props.carried.map((m, i) => <div key={`c${i}`} className={`bubble ${bubbleClass(m.who)}`}>{m.text}</div>)}
            <div className="chat-carried">Nothing has been sent here yet. Edit the request below, then Send.</div>
          </>
        )}
        {props.messages.length === 0 && !props.carried?.length && props.empty && <div className="faint">{props.empty}</div>}
        {props.messages.map((m, i) => (
          <div key={i} className={`bubble ${bubbleClass(m.who)}`}>
            {m.who === 'Customer' && <span className="who">Customer</span>}
            {m.text}
            {i === lastAi && props.afterLastAi}
          </div>
        ))}
        {waiting && !echoed && <div className="bubble me">{waiting.text}</div>}
        {waiting && <div className="bubble ai"><AiWorking since={waiting.since} /></div>}
      </div>
      <div className="chat-input">
        {props.footer}
        <label htmlFor="chat-ask">{props.label}</label>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            id="chat-ask" className="field" style={{ flex: 1 }} value={draft} placeholder={props.placeholder}
            onChange={(e) => { setDraft(e.target.value); setErr(false); }}
            onKeyDown={(e) => { if (e.key === 'Enter') void send(); }}
          />
          <button className="btn primary lg" style={{ height: 40 }} onClick={() => void send()} disabled={props.busy}>Send</button>
        </div>
        {err && <span className="bad" style={{ fontSize: 12 }}>Type a request first.</span>}
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
