// What a running research turn is doing, read from its job log (CONTRACT §10: lines starting "• "
// are written for people; the server writes one per web search, page read, photograph and save).
// Pure, unit tested.
import { activityLines } from './variant';

export type StepKind = 'search' | 'read' | 'photo' | 'save' | 'ai' | 'other';

export interface ResearchStep {
  kind: StepKind;
  /** The line as shown, without the leading marker. */
  text: string;
  /** The search words, the site being read, or the AI's own words; the rest of the line. */
  detail: string;
}

export interface ResearchProgress {
  steps: ResearchStep[];
  /** The step under way: the last one that is not the AI talking, or the AI talking if nothing else has happened. */
  now: ResearchStep | null;
  searches: number;
  pages: number;
  photos: number;
}

const PATTERNS: [StepKind, RegExp][] = [
  ['search', /^Searching the web:\s*(.*)$/],
  ['read', /^Reading\s+(.*)$/],
  ['photo', /^(?:Adding a photograph|Cropping a close-up):\s*(.*)$/],
  ['photo', /^(Finding photographs)$/],
  ['photo', /^Removing (?:photograph|close-up)\s*(.*)$/],
  ['save', /^(Saving the research|Drafting the design plan|Redrafting the design plan.*|Noting which views.*|Filing it under .*)$/],
  ['ai', /^AI:\s*(.*)$/],
];

export function researchStep(text: string): ResearchStep {
  for (const [kind, re] of PATTERNS) {
    const m = re.exec(text);
    if (m) return { kind, text, detail: m[1] ?? text };
  }
  return { kind: 'other', text, detail: text };
}

export function researchProgress(log: string[]): ResearchProgress {
  const steps = activityLines(log).map(researchStep);
  const acts = steps.filter((s) => s.kind !== 'ai');
  const count = (k: StepKind, re?: RegExp) => steps.filter((s) => s.kind === k && (!re || re.test(s.text))).length;
  return {
    steps,
    now: acts[acts.length - 1] ?? steps[steps.length - 1] ?? null,
    searches: count('search'),
    pages: count('read'),
    // Kept, not added: the AI often removes a photograph and adds it again under a better title.
    photos: Math.max(0, count('photo', /^Adding a photograph/) - count('photo', /^Removing photograph/)),
  };
}

/** "Searching the web for …" style headline for the step under way. */
export function headline(step: ResearchStep | null): string {
  if (!step) return 'Starting the research';
  switch (step.kind) {
    case 'search': return `Searching the web for “${step.detail}”`;
    case 'read': return `Reading ${step.detail}`;
    case 'ai': return 'Thinking it over';
    default: return step.text;
  }
}

/** "3 searches · 2 pages read · 1 photograph", leaving out what has not happened yet. */
export function tally(p: ResearchProgress): string {
  const n = (k: number, one: string, many: string) => (k ? `${k} ${k === 1 ? one : many}` : '');
  return [n(p.searches, 'search', 'searches'), n(p.pages, 'page read', 'pages read'), n(p.photos, 'photograph', 'photographs')]
    .filter(Boolean).join(' · ');
}
