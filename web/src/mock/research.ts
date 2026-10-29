// Mock-mode research fixture, in the CONTRACT §12 shape, with content from the approved canvas (PCG 3-Series PC 303 sample).
// Development only. The real research comes from the server's AI research job.
import type { Research } from '../types';

export const mockResearch: Research = {
  schema: 'me3d.research/1',
  subject: 'PCG 3-Series PC Class, PC 303 as photographed in 2025',
  summary: {
    verdict: 'Coverage is strong above the waterline. Engineering dimensions remain open.',
    existing_work: 'Searched the project library for PCG, PC Class, 3-Series and patrol craft. No existing PC 3-Series model or report.',
  },
  sources: [
    { id: 'S01', title: 'SPF: 3-Series commissioning release', status: 'Official', what_it_proves: 'Class identity, role, outboard propulsion, seats' },
    { id: 'S03', title: 'MINDEF PIONEER article (P01)', status: 'Photographed', what_it_proves: 'Baseline exterior photo of PC 303' },
    { id: 'S05', title: 'Unsplash photo of PC 310 (P04)', status: 'Photo reading', what_it_proves: '"350" cowl reading; door' },
    { id: 'S06', title: 'Launch news coverage', status: 'Secondary lead', what_it_proves: '11.3 t mass, load state unknown' },
    { id: 'S12', title: 'Mercury trim and tilt guidance', status: 'Comparison only', what_it_proves: 'How outboard trim works in general' },
  ],
  claims: [
    { claim: 'Class identity: PC 3-Series patrol craft, commissioned 30 Jun 2022', sources: ['S01'], status: 'Official', consequence_for_model: 'Keep the PC 3xx family separate from PT and PJ vessels.' },
    { claim: 'Low enclosed cabin, pointed foredeck, heavy fender, aft cockpit', sources: ['P01', 'P08'], status: 'Photographed', consequence_for_model: 'Hard-chine V planing form is an inference; underbody unverified.' },
    { claim: 'Four outboards', sources: ['S01', 'P02', 'P04'], status: 'Official + photo', consequence_for_model: 'Model four separate motor assemblies; no waterjets.' },
    { claim: 'Engine rating 350 hp', sources: ['S05'], status: 'Photo reading', consequence_for_model: 'Mercury 350 hp is a candidate only.' },
    { claim: 'Steering by yawing the outboards', sources: ['P19'], status: 'Inference', consequence_for_model: 'Four steering pivots, one helm command. Max angle unknown.' },
    { claim: 'Length, beam, draught', sources: [], status: 'Unverified', consequence_for_model: 'Keep dimensions as labelled provisional parameters.' },
  ],
  open_questions: [
    { question: 'Length overall, beam and draught for the PC 3-Series', meanwhile: 'photo-estimated, labelled provisional' },
    { question: 'Maximum outboard steering angle and linkage', meanwhile: 'illustrative ±30°, not a specification' },
    { question: 'PC 303 cabin door hinge side and sweep', meanwhile: 'door modelled closed' },
  ],
  articulation: [
    { part: 'Transom__Outboard_1', motion: 'steer, tilt, prop spin', priority: 'P0' },
    { part: 'Roof__Searchlight', motion: 'pan, tilt, light on/off', priority: 'P1' },
    { part: 'Cabin__Door', motion: 'hinged opening', priority: 'P1' },
    { part: 'Roof__Radome', motion: 'none: rotation is internal', priority: 'Fixed' },
  ],
  // No photos: mock mode starts on the empty Photographs and Close-ups tabs. A research message that
  // mentions photos adds generated placeholders (src/mock/photos.ts), never real-looking images.
};
