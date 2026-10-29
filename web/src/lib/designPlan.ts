// Ships of any type (CONTRACT §5 `type: "custom"`): research first, iterate, then a design plan
// the user approves before anything is built (Lester, 29 Oct). Pure, unit tested.
import type { Attachment, DesignPlan, Message } from '../types';

/**
 * What the plan panel shows. `redraft` = a draft written against older research (the server will
 * not approve it); `stale` = an approved plan the research has since moved past.
 */
export type PlanState = 'none' | 'draft' | 'redraft' | 'stale' | 'approved';

interface PlanHolder {
  kind?: string;
  base?: string;
  research_rev?: number;
  design_plan?: DesignPlan | null;
  plan_built?: boolean;
}

export const isCustomShip = (p: PlanHolder | null | undefined) => !!p && p.kind === 'ship' && p.base === 'custom';

export function planState(p: PlanHolder | null | undefined): PlanState {
  const plan = p?.design_plan;
  if (!plan || !plan.status) return 'none';
  if (plan.status === 'approved') return 'approved';
  if (plan.status === 'stale') return 'stale';
  if ((plan.research_rev ?? 0) !== (p?.research_rev ?? 0)) return 'redraft';
  return 'draft';
}

/** The status chip on the plan panel: its words and colour (amber = waiting for you). */
export function planStatusLabel(s: PlanState): { text: string; tone: 'amber' | 'green' | 'red' | 'grey' } {
  switch (s) {
    case 'draft': return { text: 'Draft · waiting for you', tone: 'amber' };
    case 'approved': return { text: 'Approved', tone: 'green' };
    case 'redraft':
    case 'stale': return { text: 'Out of date', tone: 'red' };
    case 'none': return { text: 'No plan yet', tone: 'grey' };
  }
}

/** The sentence under the chip when the plan cannot be approved as it stands, else null. */
export function planNotice(s: PlanState): string | null {
  switch (s) {
    case 'none': return 'No design plan yet — ask the AI to draft one.';
    case 'redraft': return 'Research changed since this plan — ask the AI to redraft it.';
    case 'stale': return 'Research changed since this plan was built — redraft and approve to rebuild.';
    default: return null;
  }
}

/** Approve is offered only on a draft matching the current research, and not while a job runs. */
export function canApprove(p: PlanHolder | null | undefined, busy: boolean): boolean {
  return isCustomShip(p) && planState(p) === 'draft' && !busy;
}

/**
 * Only a custom ship's FIRST build waits for approval (Lester's rule, via the lead, 29 Oct): Build,
 * its chat, Inspect and pins are locked until the server reports `plan_built`, and work normally
 * from then on, even when the plan later goes stale.
 */
export const awaitingApproval = (p: PlanHolder | null | undefined) => isCustomShip(p) && !p!.plan_built;

export const buildTabLabel = (p: PlanHolder | null | undefined) => (awaitingApproval(p) ? 'Build · waiting for your approval' : 'Build');

interface ResearchHolder extends PlanHolder {
  version?: number;
  research?: unknown;
  research_applied_rev?: number | null;
}

/**
 * Whether Research offers "Update the model from the research" (CONTRACT §12), and whether the model is
 * behind the research it would read. A catalog ship only: a custom ship follows its design plan instead.
 */
export function researchUpdate(p: ResearchHolder | null | undefined): 'none' | 'behind' | 'current' {
  if (!p || p.kind !== 'ship' || isCustomShip(p) || !p.research || !p.version) return 'none';
  return p.research_applied_rev === (p.research_rev ?? 0) ? 'current' : 'behind';
}

/**
 * One summary line, split for display: "Size · 38 m long" or "Size: 38 m long" bolds "Size".
 * Lines that state an assumption are flagged so the panel can highlight them.
 */
export function summaryLine(line: string): { label: string | null; text: string; assumed: boolean } {
  const s = line.trim();
  const m = s.match(/^([^:·]{1,28}?)\s*[:·]\s+(.+)$/s);
  const label = m && m[1].trim().split(/\s+/).length <= 3 ? m[1].trim() : null;
  const text = label ? m![2].trim() : s;
  return { label, text, assumed: /^assum/i.test(label ?? s) };
}

/**
 * What Home carried in, as the Describe card's starting text and files: the last thing the user
 * typed, and every file they attached on the way (each once).
 */
export function briefFromCarried(carried: Message[] | null | undefined): { text: string; attachments: Attachment[] } {
  const mine = (carried ?? []).filter((m) => m.who === 'You');
  const last = mine[mine.length - 1];
  const seen = new Set<string>();
  const attachments: Attachment[] = [];
  for (const m of mine) for (const a of m.attachments ?? []) if (!seen.has(a.id)) { seen.add(a.id); attachments.push(a); }
  return { text: last?.text ?? '', attachments };
}

/**
 * The text of a failed API call. A 409 carries a sentence written for the user (why a build was
 * refused, what to do), so it is shown verbatim; anything else keeps the method, path and status.
 */
export function httpErrorText(method: string, path: string, status: number, detail: unknown): string {
  if (status === 409 && typeof detail === 'string' && detail.trim()) return detail.trim();
  return `${method} ${path} failed (${status}): ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`;
}
