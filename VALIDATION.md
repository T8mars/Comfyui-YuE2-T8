# YuE2 Music T8 validation

Validation date: 2026-09-11. Host: Windows, NVIDIA GeForce RTX 5090 Laptop GPU. The current service and workers report version 1.0.5.

## Release checks

- Full song job `20260910-235141-24a06204`: default planning, semantic generation, synthesis, and tiled VAE decode completed. Output is a finite 48 kHz stereo FLAC, 39.9187 seconds; ABC and semantic outputs were not truncated.
- Transcription job `20260910-235403-260b286c`: the generated song was processed by SheetSage2 + MERT. ABC, MIDI, and a PNG score were produced with no warnings or renderer error.
- Staged jobs `20260910-235554-83264570`, `20260910-235754-71f82623`, and `20260910-235911-f3d46499`: semantic generation produced 2,214 tokens, synthesis produced 2,214 latent frames, and independent VAE decode produced a finite 48 kHz stereo FLAC of 88.5587 seconds.
- The semantic, latent, and decode manifests were re-read after completion. Every recorded file size and SHA-256 matched. The manifests identify the pinned YuE2-3B and YuE2-Vae source revisions and link each downstream stage to the preceding manifest digest.
- A live retention cleanup deleted one expired terminal job, one expired upload, and one expired log while preserving a sentinel in `exports`; the cleanup report contained no errors.

All six validation jobs were exported before cleanup. Local paths and generated media are machine artifacts and are intentionally excluded from the Registry ZIP.

## 1.0.5 regression audit

- Release tests: 19 passed and one expected model-installation skip. Local integration tests: 20 passed with all four installed model layouts present.
- Live HTTP probes rejected non-loopback Host and cross-origin requests with HTTP 403 without creating a job. Health reported generation, transcription, FFmpeg, and score-renderer capabilities ready.
- Transcription job `20260911-010022-7642e2ce` processed a real 12-second FLAC with SheetSage2 + MERT, emitted ABC, MIDI, and PNG, and wrote a verified 27-file transcription manifest containing the source-audio SHA-256 and both pinned model revisions.
- Two simultaneous exports of that job completed to distinct directories, and no temporary or partial export remained.
- Doctor job `20260911-010118-2383f99b` passed CUDA 12.8/BF16 checks and re-verified all four installed model sizes and SHA-256 hashes.
- A 1.0.4 latent chain was re-verified with the 1.0.5 canonical provenance comparison. A duplicate service process was rejected by the per-installation lock before job recovery; the original PID and `server.json` remained unchanged.
- The local WebUI was rendered at 1508×1000 after applying the light pink, blue, and slate palette from the T8star IndexTTS 2.5 integration. Health state, typography, cards, inputs, navigation, and responsive layout rendered without missing assets.
