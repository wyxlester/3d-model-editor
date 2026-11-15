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
