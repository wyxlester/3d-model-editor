// Draft or final, and what the delete dialog says (Lester, 29 Oct: "there should be indication
// which ship is draft, which ship is final. next, should also allow user to delete ship"). Pure,
// unit tested.
import type { ProjectSummary } from '../types';

type Holder = Pick<ProjectSummary, 'kind' | 'base' | 'plan_built' | 'draft'>;

/**
 * Why a project is still a draft, or null when it is final:
 * - `variant`: a variant awaiting review (Bryan's "Awaiting your approval");
 * - `plan`: a ship described from scratch whose design plan has never been built, so what the
 *   list shows is only the placeholder hull.
 */
export type DraftKind = 'variant' | 'plan' | null;

export function draftKind(p: Holder | null | undefined): DraftKind {
  if (!p) return null;
  if (p.draft) return 'variant';
  if (p.kind === 'ship' && p.base === 'custom' && !p.plan_built) return 'plan';
  return null;
}

export const isDraft = (p: Holder | null | undefined) => draftKind(p) !== null;

/** The badge on a list row and in the preview panel; null for a final project (no badge). */
export function draftBadge(p: Holder | null | undefined): string | null {
  switch (draftKind(p)) {
    case 'variant': return 'Awaiting your approval';
    case 'plan': return 'Draft · plan not approved';
    default: return null;
  }
}

/** Said in the delete dialog under the facts: where the files go. */
export const TRASH_NOTE = 'It is removed from the app. Its files are kept in the data/trash folder, so it can be recovered by hand.';

/**
 * The facts the delete dialog lists, one per line: kind, versions, category, and Draft when it is
 * one. `versions` is how many versions were made (built or not), or null while still loading;
 * `category` is as the list shows it (projectFilter's categoryOf).
 */
export function deleteFacts(p: Holder, versions: number | null, category: string): string[] {
  const kind = p.kind === 'ship' ? 'Ship' : 'Crew member';
  const made = versions === null ? 'Counting versions…' : versions === 0 ? 'No versions built yet' : versions === 1 ? '1 version' : `${versions} versions`;
  const facts = [kind, made, `Category: ${category}`];
  const k = draftKind(p);
  if (k === 'plan') facts.push('Draft: its design plan was never approved');
  if (k === 'variant') facts.push('Draft: a variant awaiting your approval');
  return facts;
}
