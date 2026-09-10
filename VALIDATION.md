# YuE2 Music T8 validation

Validation date: 2026-09-11. Host: Windows, NVIDIA GeForce RTX 5090 Laptop GPU. The current service and workers report version 1.0.7.

## Release checks

- Full song job `20260910-235141-24a06204`: default planning, semantic generation, synthesis, and tiled VAE decode completed. Output is a finite 48 kHz stereo FLAC, 39.9187 seconds; ABC and semantic outputs were not truncated.
- Transcription job `20260910-235403-260b286c`: the generated song was processed by SheetSage2 + MERT. ABC, MIDI, and a PNG score were produced with no warnings or renderer error.
- Staged jobs `20260910-235554-83264570`, `20260910-235754-71f82623`, and `20260910-235911-f3d46499`: semantic generation produced 2,214 tokens, synthesis produced 2,214 latent frames, and independent VAE decode produced a finite 48 kHz stereo FLAC of 88.5587 seconds.
- The semantic, latent, and decode manifests were re-read after completion. Every recorded file size and SHA-256 matched. The manifests identify the pinned YuE2-3B and YuE2-Vae source revisions and link each downstream stage to the preceding manifest digest.
- A live retention cleanup deleted one expired terminal job, one expired upload, and one expired log while preserving a sentinel in `exports`; the cleanup report contained no errors.

All six validation jobs were exported before cleanup. Local paths and generated media are machine artifacts and are intentionally excluded from the Registry ZIP.

## 1.0.7 page-integrated progress audit

- The live progress UI is part of the document flow directly below navigation and before the active workspace. No fixed task overlay or duplicate creation status card remains.
- The section appears only while a task is running or queued, shows the current stage and ordered queue, and keeps task-specific cancellation and history access in the same compact region.
- The creator credit and GitHub, Hugging Face model, Bilibili, and YouTube links are available in the page header and remain readable in the mobile layout.

## 1.0.6 task-center regression audit

- Release tests: 21 passed and one expected model-installation skip. Local integration tests: all 22 passed with the installed models present.
- Live duplicate submissions with different client request IDs returned the same active job `20260911-031109-38b46885` and `deduplicated: true`; only one worker entered the scheduler.
- A live task-center state showed environment self-check `20260911-031144-f1dae5c3` as the running WebUI task, followed by ComfyUI decode `20260911-031144-de7ca40f` in queue position 1 and API transcription `20260911-031144-63ed7d80` in position 2.
- The in-app browser accessibility tree exposed the task-center name, current stage, discrete task steps, source, elapsed time, both queue positions, summaries, short task IDs, and separate cancellation buttons.
- The local service and installed ComfyUI client both report 1.0.6. JavaScript syntax, Python compilation, Git whitespace validation, responsive task-center CSS, focus styles, and reduced-motion behavior passed inspection.

## 1.0.5 regression audit

- Release tests: 19 passed and one expected model-installation skip. Local integration tests: 20 passed with all four installed model layouts present.
- Live HTTP probes rejected non-loopback Host and cross-origin requests with HTTP 403 without creating a job. Health reported generation, transcription, FFmpeg, and score-renderer capabilities ready.
- Transcription job `20260911-010022-7642e2ce` processed a real 12-second FLAC with SheetSage2 + MERT, emitted ABC, MIDI, and PNG, and wrote a verified 27-file transcription manifest containing the source-audio SHA-256 and both pinned model revisions.
- Two simultaneous exports of that job completed to distinct directories, and no temporary or partial export remained.
- Doctor job `20260911-010118-2383f99b` passed CUDA 12.8/BF16 checks and re-verified all four installed model sizes and SHA-256 hashes.
- A 1.0.4 latent chain was re-verified with the 1.0.5 canonical provenance comparison. A duplicate service process was rejected by the per-installation lock before job recovery; the original PID and `server.json` remained unchanged.
- The local WebUI was rendered at 1508×1000 after applying the light pink, blue, and slate palette from the T8star IndexTTS 2.5 integration. Health state, typography, cards, inputs, navigation, and responsive layout rendered without missing assets.
