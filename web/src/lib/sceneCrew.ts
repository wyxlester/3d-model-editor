// The scene's crew shown on whichever ship version Review has on screen: a crew member stands at
// their station if that version has it, and is listed as missing if not (a station can be added
// or removed between versions).
import type { SceneCrew } from '../types';

export function crewOnVersion(crew: SceneCrew[], stations: Iterable<string>): { placed: SceneCrew[]; missing: SceneCrew[] } {
  const have = new Set(stations);
  return { placed: crew.filter((c) => have.has(c.station)), missing: crew.filter((c) => !have.has(c.station)) };
}

/** "station__passenger_1" -> "passenger 1" */
export function stationWords(station: string): string {
  return station.replace(/^station__/, '').replace(/_/g, ' ');
}
