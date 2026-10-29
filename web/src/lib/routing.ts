// Where each POST /api/route destination opens. Pure, unit tested.
import type { Destination } from '../types';

export interface RouteTarget {
  path: string;
  label: string;
}

/** Screen paths. A missing project id opens the workshop's project picker. */
export const paths = {
  home: () => '/',
  shipResearch: (id?: string | null) => (id ? `/ship/${id}/research` : '/ship'),
  shipBuild: (id?: string | null, part?: string | null) =>
    id ? `/ship/${id}/build${part ? `?part=${encodeURIComponent(part)}` : ''}` : '/ship',
  crew: (id?: string | null) => (id ? `/crew/${id}` : '/crew'),
  moves: (id?: string | null) => (id ? `/crew/${id}/moves` : '/crew'),
  assembly: (sceneId?: string | null) => (sceneId ? `/assembly/${sceneId}` : '/assembly'),
  review: (sceneId?: string | null) => (sceneId ? `/review/${sceneId}` : '/review'),
  variant: (id: string) => `/variant/${id}`,
};

/** Where a project opens: its workshop, or its review screen while it is a variant awaiting approval. */
export function projectPath(p: { kind: 'ship' | 'crew'; id: string; draft?: unknown }): string {
  if (p.draft) return paths.variant(p.id);
  return p.kind === 'ship' ? paths.shipBuild(p.id) : paths.crew(p.id);
}

export function targetFor(dest: Destination, projectId: string | null): RouteTarget | null {
  switch (dest) {
    case 'ship_research':
      return { path: paths.shipResearch(projectId), label: 'Ship workshop: Research' };
    case 'ship_build':
      return { path: paths.shipBuild(projectId), label: 'Ship workshop: Build' };
    case 'ship_variant':
      return { path: projectId ? `${paths.shipBuild(projectId)}?variant=1` : paths.shipBuild(), label: 'Ship workshop: Make a variant' };
    case 'crew':
      return { path: paths.crew(projectId), label: 'Crew workshop' };
    case 'crew_variant':
      return { path: projectId ? `${paths.crew(projectId)}?variant=1` : paths.crew(), label: 'Crew workshop: Make a variant' };
    case 'animation':
      return { path: paths.moves(projectId), label: 'Crew animation' };
    case 'assembly':
      // project_id is a project, not a scene; Assembly picks the scene itself.
      return { path: paths.assembly(), label: 'Assembly' };
    case 'review':
      return { path: paths.review(), label: 'Review and export' };
    case 'clarify':
      return null;
  }
}

/**
 * Home starts the research itself when asked to research a ship that has no project yet ("Research
 * and build USS Abraham Lincoln"): it creates the ship and opens Research on the running turn, rather
 * than the Ship page with the request waiting for another click. A known ship opens as usual.
 */
export function startsNewResearch(dest: Destination, projectId: string | null): boolean {
  return dest === 'ship_research' && !projectId;
}

/** The canvas's clarifying choices. The contract's `clarify` has no options field, so these are fixed. */
export const clarifyOptions: { label: string; text: string }[] = [
  { label: 'A ship', text: 'Research and build a new ship' },
  { label: 'A crew member', text: 'Make a new crew member' },
  { label: 'Crew on a ship', text: 'Put crew on a ship' },
];
