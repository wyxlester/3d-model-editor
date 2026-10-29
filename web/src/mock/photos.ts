// Mock-mode research photographs and close-ups (VITE_MOCK=1 only). The images are generated SVG
// drawings, labelled on the image and in every record as placeholders: never real photographs and
// never made to look like one. A pixel grid is drawn on them so a close-up's outline can be checked
// against its box by eye. Close-ups are the same drawing cut to the box, as the server crops.
import type { Coverage, Research, ResearchCloseup, ResearchPhoto } from '../types';

const W = 1600, H = 1000;

type Kind = 'side' | 'bow' | 'top';

function drawing(kind: Kind, label: string, hue: number): string {
  const grid: string[] = [];
  for (let x = 0; x <= W; x += 100) grid.push(`<line x1="${x}" y1="0" x2="${x}" y2="${H}" stroke="#ffffff" stroke-opacity="${x % 500 ? 0.07 : 0.18}"/>${x % 500 === 0 && x < W ? `<text x="${x + 6}" y="22" font-size="18" fill="#ffffff" fill-opacity="0.45">${x}</text>` : ''}`);
  for (let y = 0; y <= H; y += 100) grid.push(`<line x1="0" y1="${y}" x2="${W}" y2="${y}" stroke="#ffffff" stroke-opacity="${y % 500 ? 0.07 : 0.18}"/>${y % 500 === 0 && y > 0 && y < H ? `<text x="6" y="${y - 6}" font-size="18" fill="#ffffff" fill-opacity="0.45">${y}</text>` : ''}`);
  const boat = kind === 'side'
    ? `<path d="M220 560 L1380 560 L1300 700 L340 700 Z" fill="hsl(${hue} 30% 55%)"/><rect x="620" y="430" width="420" height="130" fill="hsl(${hue} 20% 75%)"/><rect x="660" y="455" width="120" height="50" fill="#1b2633"/><rect x="820" y="455" width="180" height="50" fill="#1b2633"/><line x1="900" y1="430" x2="900" y2="300" stroke="#dde" stroke-width="8"/><circle cx="900" cy="292" r="16" fill="#e6b04a"/><rect x="1300" y="600" width="60" height="120" fill="#444c57"/>`
    : kind === 'bow'
      ? `<path d="M800 300 L1080 620 L800 760 L520 620 Z" fill="hsl(${hue} 30% 55%)"/><rect x="660" y="440" width="280" height="150" fill="hsl(${hue} 20% 75%)"/><rect x="690" y="470" width="220" height="50" fill="#1b2633"/><circle cx="800" cy="400" r="18" fill="#e6b04a"/>`
      : `<path d="M300 500 Q800 260 1300 500 Q800 740 300 500 Z" fill="hsl(${hue} 30% 55%)"/><rect x="700" y="430" width="300" height="140" fill="hsl(${hue} 20% 75%)"/><circle cx="1150" cy="500" r="20" fill="#e6b04a"/>`;
  return `<rect width="${W}" height="${H}" fill="hsl(${hue} 25% 16%)"/>${grid.join('')}<rect x="0" y="780" width="${W}" height="220" fill="hsl(205 40% 22%)" fill-opacity="0.8"/>${boat}`
    + `<text x="${W / 2}" y="880" text-anchor="middle" font-size="64" font-family="sans-serif" font-weight="700" fill="#ffffff" fill-opacity="0.9">MOCK PLACEHOLDER</text>`
    + `<text x="${W / 2}" y="940" text-anchor="middle" font-size="30" font-family="sans-serif" fill="#ffffff" fill-opacity="0.75">${label} · generated in mock mode, not a photograph</text>`;
}

function svgUri(inner: string, box?: [number, number, number, number]): string {
  const [x, y, w, h] = box ?? [0, 0, W, H];
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="${x} ${y} ${w} ${h}">${inner}</svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

const MOCK_CREDIT = 'Mock mode: generated placeholder, not a photograph';

const SETS: { kind: Kind; title: string; views: string[]; status: string; hue: number; observation: string; use: string }[] = [
  { kind: 'side', title: 'port side profile', views: ['port', 'details'], status: 'baseline', hue: 210, observation: 'Placeholder for a full port profile: hull length, sheer and cabin position.', use: 'hull profile, cabin position' },
  { kind: 'bow', title: 'bow view', views: ['bow'], status: 'comparison', hue: 160, observation: 'Placeholder for a bow-on view of a similar craft: beam and windscreen proportions only.', use: 'beam ratio' },
  { kind: 'top', title: 'elevated view', views: ['top', 'starboard'], status: 'excluded', hue: 30, observation: 'Placeholder for an elevated oblique view, excluded to show how an excluded photo looks.', use: 'none (excluded)' },
];

/**
 * What a research turn that mentions photographs or close-ups does in mock mode: three placeholder
 * photos (once), then two close-ups cut from the first two. Returns the reply sentence.
 */
export function addMockPhotos(research: Research | null, subject: string): { research: Research; reply: string } {
  const r: Research = research ? { ...research } : { schema: 'me3d.research/1', subject, summary: { verdict: '(mock) Research started from a photo request.' } };
  const photos = [...(r.photos ?? [])];
  let added = 0;
  if (!photos.some((p) => p.credit === MOCK_CREDIT)) {
    for (const s of SETS) {
      const id = `P${String(photos.length + 1).padStart(2, '0')}`;
      const p: ResearchPhoto = {
        id, title: `Mock placeholder: ${s.title}`, vessel: subject, views: s.views, credit: MOCK_CREDIT,
        date: 'Not applicable (generated)', observation: s.observation, source: 'https://example.org/mock-source-page',
        ...(s.kind === 'side' ? { image_url: 'https://example.org/mock-image.png' } : {}),
        status: s.status, rights: 'Mock placeholder; no rights to record', modelling_use: s.use,
        width: W, height: H, sha256: 'f0'.repeat(32), accessed: new Date().toISOString().slice(0, 10),
        file: svgUri(drawing(s.kind, `${id} ${s.title}`, s.hue)),
      };
      photos.push(p);
      added++;
    }
  }
  const mock = photos.filter((p) => p.credit === MOCK_CREDIT);
  const closeups = [...(r.closeups ?? [])];
  let cut = 0;
  if (!closeups.some((c) => c.id.startsWith('C') && mock.some((p) => p.id === c.photo))) {
    const side = mock[0], bow = mock[1];
    const make = (photo: ResearchPhoto, kind: Kind, hue: number, box: [number, number, number, number], title: string, shows: string, claims: string[]): ResearchCloseup => ({
      id: `C${String(closeups.length + 1).padStart(2, '0')}`, photo: photo.id, box, title, what_it_shows: shows, claims,
      file: svgUri(drawing(kind, `${photo.id} ${kind}`, hue), box),
    });
    if (side) { closeups.push(make(side, 'side', 210, [800, 250, 300, 220], 'Placeholder: mast light', '(mock) Where a real crop would show the masthead light and its bracket.', ['Mast height (mock claim)'])); cut++; }
    if (bow) { closeups.push(make(bow, 'bow', 160, [620, 420, 360, 200], 'Placeholder: windscreen', '(mock) Where a real crop would show the windscreen frame and wipers.', ['Windscreen proportions (mock claim)', 'Comparison only: similar craft'])); cut++; }
  }
  const coverage: Coverage = {
    port: mock[0]?.id ?? null, starboard: null, bow: mock[1] ? `${mock[1].id} (similar craft)` : null, stern: null,
    top: mock[2] ? `${mock[2].id} elevated, excluded` : null, details: mock[0]?.id ?? null,
    note: 'Mock mode: these are generated placeholders, so no real view is covered.',
  };
  const out: Research = {
    ...r, photos, closeups, coverage, photo_count: photos.length,
    count_method: 'Mock mode: generated placeholders counted one each; no real photographs were found or compared.',
  };
  const parts = [added ? `${added} generated placeholder images (not photographs)` : '', cut ? `${cut} close-ups cut from them` : ''].filter(Boolean);
  const reply = parts.length
    ? `(mock) Added ${parts.join(' and ')}, so the Photographs and Close-ups tabs can be tried. No real search runs in mock mode.`
    : '(mock) The placeholder photographs and close-ups are already there. No real search runs in mock mode.';
  return { research: out, reply };
}

export const mentionsPhotos = (message: string) => /photo|close-?up|crop/i.test(message);
