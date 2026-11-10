---
date: 2026-11-10
project: 3d_model_editor
branch: main
session: bf51fa98
status: done
summary: TDD strategy for 3d_model_editor—test-first for pure logic, run-and-verify for wiring; Impeccable design setup recommended
written_by: nightly sweep
---

# Test-driven development—where it fits in this project

**Asked:** "Share with me how test driven development works and how it can apply to this project?" (14:44)

## Done
- TDD cycle explained: write test first, watch it fail, write code, refactor
- Existing coverage surveyed: ~30 pytest files in `server/tests/`, cross-part tests, `web/src/lib/logic.test.ts`
- Test-first candidates identified: spec merging, coordinate frames, transforms, packaging — these are pure logic where silent failures corrupt exports
- Run-and-verify approaches: 3D viewport, Review page layout, Blender build scripts, Unreal import — failures here are visible or loud
- `AGENTS.md` rule confirmed: pure logic gets unit tests you've seen fail; wiring checked by running it

## Open
- [ ] **Lester:** Decide whether to run `/impeccable init` (writes `PRODUCT.md` so design commands work), then `/impeccable critique web/src/screens/Home.tsx` (UX review)

## Links
- `server/app/specmerge.py`
- `AGENTS.md`
