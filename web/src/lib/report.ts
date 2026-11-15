/** A build report's warnings, split for the Build screen's status bar (CONTRACT §3b). Articulation lines
 *  ("articulation: <part> (<role>): ...") are shown as one summary that opens to the list: right after a plan's
 *  first build there is one per planned moving part, and listed whole they pushed the viewport to nothing. */
const ART = 'articulation: ';

export function splitWarnings(warnings: string[] | undefined): { other: string[]; articulation: string[] } {
  const all = warnings ?? [];
  return {
    other: all.filter((w) => !w.startsWith(ART)),
    articulation: all.filter((w) => w.startsWith(ART)).map((w) => w.slice(ART.length)),
  };
}

/** CONTRACT §10 health.code: a sentence when the server runs older code than is on disk, else null. A server
 *  without `code` predates the check, so it is older still. Drafts it makes (Review > MAK) use its old rules. */
export function staleServerText(health: { code?: { stale: boolean; started: string; changed_count: number } } | null): string | null {
  if (!health) return null;
  if (!health.code) return 'The editor’s server is older than this page. Restart the editor (start.ps1) before saving MAK settings or exporting.';
  if (!health.code.stale) return null;
  const n = health.code.changed_count;
  return `The editor’s server has run since ${health.code.started.replace('T', ' ').replace('Z', ' UTC')}, and ${n} of its file${n > 1 ? 's have' : ' has'} changed since. Restart the editor (start.ps1) before saving MAK settings or exporting.`;
}
