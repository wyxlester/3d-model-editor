// A panel under the viewers that the user resizes by dragging its top edge (Review's export
// console): dragging up makes it taller. It never gets so tall that the viewers above disappear.

export const PANEL_MIN = 96;   // px: the buttons row and one line of output
export const PANEL_KEEP = 200; // px left for the viewers above it

/** Height after dragging the top edge from `startY` to `y`, in an area `total` px tall. */
export function dragHeight(startH: number, startY: number, y: number, total: number,
  min = PANEL_MIN, keep = PANEL_KEEP): number {
  const most = Math.max(min, total - keep);
  return Math.round(Math.min(most, Math.max(min, startH + (startY - y))));
}
