// Pieces shared by the Crew workshop and the animation editor.
import { useCallback, useRef, useState } from 'react';
import { api } from '../api';
import { mergePatch } from '../lib/spec';
import type { Attachment } from '../types';

type Run = <R>(label: string, start: () => Promise<{ job_id: string }>, onDone?: (r: R) => void | Promise<void>) => Promise<R | undefined>;

/**
 * Spec edits for one project. Each edit is a rebuild (10–60 s), so while one is running further
 * edits are merged and sent together when it finishes. `optimistic` holds what was asked for but
 * not yet reloaded, so pickers and sliders show the chosen value straight away.
 */
export function useSpecEditor(projectId: string | undefined, run: Run, reload: () => Promise<unknown>) {
  const pending = useRef<Record<string, unknown> | null>(null);
  const inFlight = useRef(false);
  const [optimistic, setOptimistic] = useState<Record<string, unknown>>({});

  const flush = useCallback(async () => {
    if (!projectId || inFlight.current || !pending.current) return;
    inFlight.current = true;
    const patch = pending.current;
    pending.current = null;
    await run('Rebuilding with your changes', () => api.patchSpec(projectId, patch), async () => { await reload(); });
    inFlight.current = false;
    if (pending.current) void flush();
    else setOptimistic({});
  }, [projectId, run, reload]);

  const edit = useCallback((patch: Record<string, unknown>) => {
    pending.current = mergePatch(pending.current ?? {}, patch);
    setOptimistic((o) => mergePatch(o, patch));
    void flush();
  }, [flush]);

  return { edit, optimistic };
}

/** Send a chat message to the crew project and reload when the AI is done. */
export function useCrewChat(projectId: string | undefined, run: Run, reload: () => Promise<unknown>) {
  return useCallback(async (text: string, atts: Attachment[] = []) => {
    if (!projectId) return;
    await run('AI is working on it', async () => {
      const r = await api.chat(projectId, text, 'build', atts.map((a) => a.id));
      void reload(); // show the sent message while the AI works
      return r;
    }, async () => { await reload(); });
  }, [projectId, run, reload]);
}

export const fmtSecs = (t: number) => `${t.toFixed(1)} s`;
