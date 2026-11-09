---
date: 2026-10-29
project: 3d_model_editor
branch: main
session: b7529c9c
status: in progress
summary: Ship and crew 3D editor designed, built, live-tested; awaiting tagline commit
written_by: nightly sweep
---

# Design and build the AI 3D editor for ships and crew

**Asked:** Build an AI 3D editor to design ships, humans, and equipment in text; eliminate the three-handoff process (2026-10-28, 23:11)

## Done
- Five-screen user journey: home, ship workshop, crew workshop, assembly, review — [design canvas](https://claude.ai/artifact/67Wgj688QSRc4PycUKmw1u)
- Ship and crew workshops: describe in text, edit modules, live preview with AI
- Assembly: drag crew onto ship interaction points (smart objects); animation plays
- Female crew base added to match male (d1f9f29)
- Equipment module: describe item and carry method together in one prompt
- Undo on crew workshop; shows what version it undoes
- Animation: aim stance, raise-to-aim move, arbitrary moves from pose descriptions
- Home page: removed Try One/Start New/Catalogue (0485d06)
- Merged Bryan's research review system and Mathew's Unreal equipment persistence
- All tests pass: server 305, web 123, Blender 245; live end-to-end tests pass
- GitHub at 4900080; local app running
- Tagline created and added to Home: "Prompt to production-ready 3D models for simulators" (pending commit)
- Context deck updated with research and cost — [artifact](https://claude.ai/artifact/PiBYCnDFCT9j7QJBkoRbk5)

## Decided
- **Foundation:** Claude Agent SDK — secure project isolation, direct AI integration
- **Three workshops:** ship, crew, assembly — separate edit and preview loops
- **Assembly:** smart objects — ship parts declare interaction points; crew drag onto them
- **Humans:** catalogue items (promoted from prompts) + free-form prompts; reusable after creation (Lester, 23:58)
- **Review context:** meetings on Lester's laptop, no hosting or data leaving his Mac (Lester, 00:21)
- **Female crew:** dark brown hair, T-shirt, work trousers; matches male skeleton and moves
- **Equipment:** single module combining item and carry method
- **Home page:** chat box and Recent only (Lester, 14:42)
- **Final Unreal export:** dropped (Lester, 17:52)
- **Tagline:** "Prompt to production-ready 3D models for simulators" — 8 words, differentiates from generic 3D tools (Lester, 10:47)

## Open
- [ ] **Lester:** Review tagline on Home at localhost (http://localhost:5173/); confirm it looks right, then commit and push

## Links
- Design canvas: https://claude.ai/artifact/67Wgj688QSRc4PycUKmw1u
- Context deck: https://claude.ai/artifact/PiBYCnDFCT9j7QJBkoRbk5
- GitHub: 4900080 (main)
- Local: http://localhost:5173/
