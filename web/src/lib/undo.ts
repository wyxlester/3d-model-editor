// "Undo last change" (ship and crew). Lester, 29 Oct: "create a undo button for both crew and ship
// for user to return back to previous state". The hover text says where Undo goes and what it takes
// away, so nobody has to guess which change is undone. Pure, unit tested.
import type { Project } from '../types';

type UndoInfo = Pick<Project, 'version' | 'versions' | 'can_undo' | 'undo_to'>;

/**
 * The version Undo returns to: the server's `undo_to`, or, from a server that does not send it, the
 * newest good version below the current one. Null when there is nothing to undo.
 */
export function undoTarget(p: UndoInfo | null | undefined): number | null {
  if (!p || p.can_undo === false || !p.version) return null;
  if (p.undo_to != null) return p.undo_to;
  const below = p.versions.filter((v) => v.n < p.version && v.ok !== false);
  return below.length ? below[below.length - 1].n : null;
}

/** "Back to v2, undoing v3: Edited equipment", or "Nothing to undo". */
export function undoHint(p: UndoInfo | null | undefined): string {
  const to = undoTarget(p);
  if (to == null || !p) return 'Nothing to undo';
  const undone = p.versions.find((v) => v.n === p.version)?.summary?.trim();
  return undone ? `Back to v${to}, undoing v${p.version}: ${undone}` : `Back to v${to}`;
}
