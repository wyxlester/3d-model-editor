// The Review page's MAK VR-Forces export (CONTRACT §9b, §10): the ship's settings (DIS type,
// donor entity, short name, a DIS code per moving part), the export report, and the confirm
// dialog for installing a passing export into a custom SMS. Ships only; crew are not exported.
import { useEffect, useId, useRef, useState } from 'react';
import { api, errorText, fileUrl } from '../api';
import { useLoad } from '../hooks';
import { disTypeError, installFolders, makFormStart, makRows, makSettingsOf, parseCode, type MakForm, type MakRow } from '../lib/mak';
import type { MakDraft, MakInstallResult, MakReport, Project } from '../types';
import { ErrorNote } from './common';

/** The ship's MAK settings form, saved with PATCH /mak (no new version). */
export function MakSettingsForm({ project, onSaved }: { project: Project; onSaved: (p: Project) => void }) {
  const draft = useLoad<MakDraft>(() => api.makDraft(project.id), [project.id, project.version]);
  const [form, setForm] = useState<MakForm | null>(null);
  const [rows, setRows] = useState<MakRow[]>([]);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState(false);
  const [dirty, setDirty] = useState(false);
  const ids = { dis: useId(), donor: useId(), short: useId() };

  // Start (again) from the saved settings whenever a draft for this version arrives.
  useEffect(() => {
    if (!draft.data) return;
    setForm(makFormStart(draft.data, project.mak));
    setRows(makRows(draft.data, project.mak));
    setDirty(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- project.mak is read once per draft
  }, [draft.data]);

  if (draft.error) return <ErrorNote error={draft.error} retry={() => void draft.reload()} />;
  if (!draft.data || !form) return <span className="faint">Loading the VR-Forces suggestions…</span>;
  const d = draft.data;

  const edit = (f: Partial<MakForm>) => { setForm({ ...form, ...f }); setDirty(true); setSavedNote(false); };
  const editCode = (path: string, code: string) => { setRows(rows.map((r) => (r.path === path ? { ...r, code } : r))); setDirty(true); setSavedNote(false); };
  const disErr = form.dis_type.trim() ? disTypeError(form.dis_type) : null;
  const donor = d.donors.find((x) => x.entity === form.donor_entity);

  const save = async () => {
    const built = makSettingsOf(form, rows);
    if (!built.ok) { setErr(built.error); return; }
    setSaving(true); setErr(null);
    try {
      const r = await api.patchMak(project.id, built.mak);
      setDirty(false); setSavedNote(true);
      onSaved(r.project);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <span style={{ fontWeight: 500 }}>VR-Forces settings for {project.name}</span>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 10 }}>
        <label htmlFor={ids.dis} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span className="faint" style={{ fontSize: 12 }}>DIS entity type (7 numbers)</span>
          <input id={ids.dis} className="field mono" value={form.dis_type} placeholder={d.dis_type_hint}
            aria-invalid={!!disErr} onChange={(e) => edit({ dis_type: e.target.value })} />
          <span className={disErr ? 'bad' : 'faint'} style={{ fontSize: 12 }}>
            {disErr ?? <>Suggested <button className="btn sm" style={{ height: 22, padding: '0 6px' }} onClick={() => edit({ dis_type: d.dis_type_hint })}
              title="Use the suggested DIS type"><span className="mono">{d.dis_type_hint}</span></button></>}
          </span>
        </label>
        <label htmlFor={ids.donor} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span className="faint" style={{ fontSize: 12 }}>Donor entity (systems and visuals are copied from it)</span>
          <select id={ids.donor} className="field" value={form.donor_entity} onChange={(e) => edit({ donor_entity: e.target.value })}>
            {!donor && <option value={form.donor_entity}>{form.donor_entity || 'Pick one'}</option>}
            {d.donors.map((x) => <option key={x.entity} value={x.entity}>{x.entity}{x.military ? ' · military' : ''}</option>)}
          </select>
          <span className="faint" style={{ fontSize: 12 }}>
            {donor ? <>{donor.size_lwh.map((n) => `${n}`).join(' × ')} m · <span className="mono">{donor.objectType}</span></> : 'Not one of the suggested donors.'}
          </span>
        </label>
        <label htmlFor={ids.short} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span className="faint" style={{ fontSize: 12 }}>Short name (optional)</span>
          <input id={ids.short} className="field" value={form.short_name} placeholder={d.short_name} onChange={(e) => edit({ short_name: e.target.value })} />
        </label>
      </div>

      {rows.length ? (
        <table className="report-table" style={{ marginTop: 0 }}>
          <thead>
            <tr><th>Moving part</th><th>Motion</th><th>Limits</th><th>DIS part code</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const bad = parseCode(r.code) === 'bad';
              return (
                <tr key={r.path}>
                  <td>{r.label} <span className="faint">{r.priority}</span></td>
                  <td>{r.motion}</td>
                  <td className="mono">{r.min_deg}° to {r.max_deg}°</td>
                  <td>
                    <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                      <input className="field mono" style={{ height: 28, width: 90 }} inputMode="numeric" value={r.code} aria-invalid={bad}
                        aria-label={`DIS part code for ${r.label}`} placeholder="static" onChange={(e) => editCode(r.path, e.target.value)} />
                      {bad ? <span className="bad">whole number</span>
                        : r.code.trim() ? <button className="btn sm" onClick={() => editCode(r.path, '')} title="Keep this part still in VR-Forces">Make static</button>
                        : <span className="faint">static{r.suggested != null && <> · <button className="btn sm" onClick={() => editCode(r.path, String(r.suggested))}>Use {r.suggested}</button></>}</span>}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : <span className="faint">v{project.version} has no moving parts, so the ship goes to VR-Forces as one rigid model.</span>}

      <span style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn primary" onClick={() => void save()} disabled={saving || !dirty && !!project.mak}>{saving ? 'Saving…' : 'Save settings'}</button>
        {savedNote && <span className="ok">Saved. No new version was made.</span>}
        {dirty && <span className="warn">Unsaved changes. Export uses the saved settings.</span>}
        {!project.mak && !dirty && <span className="faint">Not saved yet. Save before exporting.</span>}
      </span>
      <ErrorNote error={err} />
    </div>
  );
}

/** What a MAK export said: checks styled like the Unreal ones, the zip, what was left out, and how to deploy. */
export function MakReportView({ report, installed }: { report: MakReport; installed: MakInstallResult | null }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <span className={report.ok ? 'ok' : 'bad'} style={{ fontWeight: 500 }}>
          {report.ok ? '✓ Exported for VR-Forces and every check passed' : '✗ Exported for VR-Forces with failing checks'} · VR-Forces {report.vrforces} ·{' '}
          <span className="mono" style={{ fontWeight: 400 }}>{report.name}</span> · <span className="mono" style={{ fontWeight: 400 }}>{report.dis_type}</span>
        </span>
        {report.zip_url && <a className="btn sm" href={fileUrl(report.zip_url)} download>Download zip</a>}
      </span>
      <span className="faint">Written to <span className="mono">{report.output_dir}</span>.</span>
      {report.checks.map((c, i) => (
        <span key={i} style={{ display: 'flex', gap: 8 }}>
          <span className={c.pass ? 'ok' : 'bad'}>{c.pass ? '✓' : '✗'}</span>
          <span>{c.name}</span>
          <span className="muted">{c.detail}</span>
        </span>
      ))}
      {!!report.pending.length && <span className="warn">Still to do: {report.pending.join('; ')}</span>}
      {!!report.not_exported.length && <span className="muted">Not exported: {report.not_exported.join('; ')}</span>}
      {installed ? (
        <div className="ok" style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span>Installed into the SMS <span className="mono">{installed.sms}</span>.{installed.backups_dir
            ? <> Replaced files were backed up to <span className="mono">{installed.backups_dir}</span>.</>
            : ' Nothing was replaced.'}</span>
          {installed.written.map((w) => <span key={w} className="mono muted" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>{w}</span>)}
          <span className="muted">Restart VR-Forces with that SMS, then find {report.name} among the surface simulation objects.</span>
        </div>
      ) : report.ok && (
        <ol className="muted" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.6 }}>
          <li>Press “Install into VR-Forces…” to copy it into a custom SMS, or by hand: <span className="mono">SharedData/</span> into the SharedData folder, <span className="mono">sms/</span> into a copy of EntityLevel (never EntityLevel itself).</li>
          <li>Start VR-Forces with that SMS.</li>
          <li>Find {report.name} among the surface simulation objects and place it.</li>
        </ol>
      )}
    </div>
  );
}

/** Confirm before writing into the VR-Forces install: names the SMS and every folder it writes. */
export function MakInstallDialog({ name, onCancel, onConfirm }: { name: string; onCancel: () => void; onConfirm: (sms: string) => void }) {
  const list = useLoad<{ sms: string[] }>(() => api.makSms(), []);
  const [sms, setSms] = useState('');
  const box = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const pickId = useId();
  const choices = list.data?.sms ?? [];

  useEffect(() => { if (!sms && choices.length) setSms(choices[0]); }, [choices, sms]);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    return () => { if (before?.isConnected) before.focus(); };
  }, []);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onCancel(); return; }
    if (e.key !== 'Tab' || !box.current) return;
    const items = [...box.current.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')];
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };

  return (
    <div className="scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div ref={box} role="dialog" aria-modal="true" aria-labelledby={titleId} className="dialog"
        style={{ width: 520, maxWidth: 'calc(100vw - 32px)' }} onKeyDown={onKey}>
        <div id={titleId} style={{ fontSize: 18, fontWeight: 500, overflowWrap: 'anywhere' }}>Install {name} into VR-Forces?</div>
        {list.error ? <ErrorNote error={list.error} retry={() => void list.reload()} />
          : !list.data ? <span className="faint">Looking for custom SMSs…</span>
          : !choices.length ? <span className="warn" style={{ lineHeight: 1.5 }}>VR-Forces has no custom SMS yet. In its Simulation Object Editor, create a new SMS that includes EntityLevel, then try again; the stock SMSs are never written to.</span>
          : (
            <>
              <label htmlFor={pickId} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span className="faint" style={{ fontSize: 12 }}>Simulation model set (custom ones only)</span>
                <select id={pickId} className="field" value={sms} onChange={(e) => setSms(e.target.value)}>
                  {choices.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </label>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <span>It writes into:</span>
                <ul className="mono" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.6, fontSize: 12, overflowWrap: 'anywhere' }}>
                  {installFolders(sms, name).map((f) => <li key={f}>{f}</li>)}
                </ul>
                <span className="muted" style={{ lineHeight: 1.5 }}>Every file it replaces is backed up first, under data/mak_backups/.</span>
              </div>
            </>
          )}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
          <button ref={cancelRef} className="btn lg" onClick={onCancel}>Cancel</button>
          <button className="btn lg primary" disabled={!sms} onClick={() => onConfirm(sms)}>{sms ? `Install into ${sms}` : 'Install'}</button>
        </div>
      </div>
    </div>
  );
}
