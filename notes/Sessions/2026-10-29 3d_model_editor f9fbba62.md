---
date: 2026-10-29
project: 3d_model_editor
branch: main
session: f9fbba62
status: in progress
summary: Captioned demo video storyline finalized to 1:20; awaiting visual review and Azure credentials for voiceover
written_by: Claude
---

# Hackathon POC demo video (judges and peers)

**Asked:** Build 1-minute demo video for hackathon presentation of 3D editor proof of concept. Show the problem, user journey (research → build ship → customize crew → assembly → export to Unreal), and key benefits. Originally 60 seconds (Lester, 2026-10-29 15:01), compressed to 45 s, then expanded to show all features.

## Done
- Captioned video cut to 1 minute 20 seconds: `demo_v6.mp4` in scratchpad `/private/tmp/claude-501/-Users-wyxlester-dev-3d-model-editor/f9fbba62-8a5c-463b-9971-ff50cd5e0520/scratchpad/demo2/`
- Storyboard follows Lester's outline (16:08): intro (3 s) → user prompt and research (4 s) → research outcome with photos/tabs (13 s) → approve ship build (6 s) → comment feature and AI iteration (10 s) → crew creation and module clicks (6 s) → SAR 21 equipment prompt (6 s) → screenshot feedback (7 s) → assembly with crew on ship, animation (9 s) → review and export button (7 s) → Unreal proof (8 s)
- Five iterative cuts from script feedback (v1–v5: 45 s → 38 s → 75.25 s → 75.9 s → 1:24)

## Decided
- **Format:** captions burned in; voiceover and music deferred until visual review — Lester (15:08)
- **Voice:** female, Singaporean, Azure `en-SG-LunaNeural` — Lester (15:06)
- **Storyboard order:** Lester's exact sequence — Lester (16:08)

## Open
- [ ] **Lester:** Review demo_v6.mp4 for visual accuracy (contact sheet images failed to load for Claude). Verify rifle visibility in screenshot feedback step, ship build start frame, crew scenes.
- [ ] **Lester:** Decide whether to add key-benefits card after Unreal shot or end on proof as is
- [ ] **Lester:** Set Azure Speech environment variables (`AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`)
- [ ] **Claude:** Generate voiceover with Azure TTS once env vars set; add royalty-free music and produce final audio mix

## Links
- Video file: `demo_v6.mp4` (1:20, captions)
- Prior cuts: demo_v1 (45 s), demo_v2 (38 s refactor), demo_v3 (75.25 s extended), demo_v4 (75.9 s +assembly/review/export), demo_v5 (1:24 +Unreal)
- Source screen recordings: 3.24.27 PM (research, ship, comments), 3.57.50 PM (assembly, review, export), 3.59.41 PM (Unreal compilation)
