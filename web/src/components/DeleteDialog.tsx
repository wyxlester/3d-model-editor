// "Delete" on the ship and crew lists (Lester, 29 Oct: "if choose delete ship, render a dialog box
// to confirm with user, before actually deleting"). Says what is about to go, and where the files
// go; the server may refuse (a scene uses it, a change is running), and then its sentence is shown.
import { useEffect, useId, useRef, useState } from 'react';
import { api, errorText } from '../api';
import { useProject } from '../hooks';
import { TRASH_NOTE, deleteFacts } from '../lib/draft';
import type { ProjectSummary } from '../types';

export function DeleteDialog({ project, category, onCancel, onDeleted }: {
  project: ProjectSummary;
  /** As the list shows it. */
  category: string;
  onCancel: () => void;
  onDeleted: () => void;
}) {
  const full = useProject(project.id);
  const versions = full.data ? full.data.versions.length : full.error ? (project.version ? null : 0) : null;
  const [sending, setSending] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const factsId = useId();

  // Cancel has the focus to start with; whatever had it before gets it back on close (if it is
  // still on the page: after a delete the preview panel it sat in is gone, so the caller places it).
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    return () => { if (before?.isConnected) before.focus(); };
  }, []);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); if (!sending) onCancel(); return; }
    if (e.key !== 'Tab' || !box.current) return;
    // Keep Tab inside the dialog.
    const items = [...box.current.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')];
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };

  const confirm = async () => {
    setSending(true); setRefused(null);
    try {
      await api.deleteProject(project.id);
      onDeleted();
    } catch (e) {
      setRefused(errorText(e));
      setSending(false);
      cancelRef.current?.focus();
    }
  };

  return (
    <div className="scrim" onMouseDown={(e) => { if (e.target === e.currentTarget && !sending) onCancel(); }}>
      <div ref={box} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={factsId} className="dialog"
        style={{ width: 460, maxWidth: 'calc(100vw - 32px)' }} onKeyDown={onKey}>
        <div id={titleId} style={{ fontSize: 18, fontWeight: 500, overflowWrap: 'anywhere' }}>Delete {project.name}?</div>
        <div id={factsId} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.6 }}>
            {deleteFacts(project, versions, category).map((f) => <li key={f}>{f}</li>)}
          </ul>
          <span className="muted" style={{ lineHeight: 1.5 }}>{TRASH_NOTE}</span>
        </div>
        {refused && <div className="note bad" role="alert" style={{ lineHeight: 1.5, boxShadow: 'none' }}>{refused}</div>}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
          <button ref={cancelRef} className="btn lg" onClick={onCancel} disabled={sending}>Cancel</button>
          <button className="btn lg danger" onClick={() => void confirm()} disabled={sending}
            style={{ maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {sending ? 'Deleting…' : `Delete ${project.name}`}
          </button>
        </div>
      </div>
    </div>
  );
}
