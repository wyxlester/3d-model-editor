// Moves made from words (CONTRACT §6 "Custom moves"). Lester's plan, "Animation editor": prompted moves
// built as key poses on the shared skeleton, refined in words ("faster on the way up"), and previewed.
import type { CrewSpec, CustomMove } from '../types';

type Spec = Partial<CrewSpec>;

/** One row in the Moves list. `inModel`: the built model has the clip (else it waits for the rebuild). */
export interface MoveRow { name: string; kind: 'built_in' | 'words'; inModel: boolean; description?: string; keys?: number[]; seconds?: number }

/** The person's moves: built-in clips (the spec's list and whatever the model has), then the moves made from words. */
export function moveRows(spec: Spec, clips: string[]): MoveRow[] {
  const words = (spec.custom_moves ?? []).filter((m): m is CustomMove => !!m && typeof m.name === 'string');
  const wordNames = new Set(words.map((m) => m.name));
  const builtIn = [...new Set([...clips, ...(spec.animations ?? [])])].filter((a) => !wordNames.has(a));
  return [
    ...builtIn.map((name): MoveRow => ({ name, kind: 'built_in', inModel: clips.includes(name) })),
    ...words.map((m): MoveRow => ({
      name: m.name, kind: 'words', inModel: clips.includes(m.name), description: m.description || undefined,
      keys: (m.keys ?? []).map((k) => k.t), seconds: m.duration_s,
    })),
  ];
}

/** The message a "Refine" sends: the move named, then Lester's words. */
export function refineMessage(name: string, words: string): string {
  return `Refine the move "${name}": ${words.trim()}`;
}

/** Remove a move made from words; the whole list goes, as every spec patch does. */
export function removeMovePatch(spec: Spec, name: string): { custom_moves: CustomMove[] } {
  return { custom_moves: (spec.custom_moves ?? []).filter((m) => m.name !== name) };
}

/** "Keys at 0.6 s, 1.6 s · 2.4 s" for a move made from words. */
export function keyLine(row: MoveRow): string {
  if (row.kind !== 'words' || !row.keys) return '';
  const keys = row.keys.length ? `Keys at ${row.keys.map((t) => `${t.toFixed(1)} s`).join(', ')}` : 'No keys';
  return row.seconds ? `${keys} · ${row.seconds.toFixed(1)} s long` : keys;
}
