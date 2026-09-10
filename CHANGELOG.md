# Changelog

## 1.0.7 - 2026-09-11

- Move live task progress into the page flow above the active workspace and remove the large fixed overlay and duplicate creation status card.
- Keep the current task, stage steps, queued task order, task summaries, and cancellation controls visible in one compact section.
- Add the creator credit and direct GitHub, Hugging Face model, Bilibili, and YouTube links to the local WebUI.

## 1.0.6 - 2026-09-11

- Replace the ambiguous single-job drawer with a live task center that identifies the current task and lists every queued task in execution order.
- Show localized task types, human-readable stages, source, style summary, elapsed/submitted time, and task-specific cancellation controls.
- Lock submit buttons immediately, reflect queued/running/cancelling state in place, and restore active tasks from the server after page refresh.
- Deduplicate identical active requests server-side and remove cancelled queued work from the logical queue immediately.
- Separate runtime readiness from GPU workload, remove the misleading fake progress bar, and add responsive and reduced-motion task-center styles.

## 1.0.5 - 2026-09-11

- Bind staged inference to the exact manifest-recorded files and recursively verify plan, semantic, and latent lineage while preserving compatibility with 1.0.4 manifests.
- Protect queued and running job dependencies from retention, serialize reads and exports with cleanup, and publish exports atomically with collision-safe names.
- Reject non-loopback Host headers, acquire a per-installation instance lock and bind the service port before job recovery, and make CUDA/BF16 self-check failures explicit.
- Pin model source identities in code, make provenance comparison insensitive to unrelated manifest formatting, and reject placeholder runtime files.
- Preserve transcription errors, prevent missing ABC from silently becoming a new composition, and add transcription manifests with source-audio and output hashes.
- Refresh the local WebUI with the light pink, blue, and slate palette used by the T8star IndexTTS 2.5 integration.

## 1.0.4 - 2026-09-11

- Add independent semantic, latent, and decode manifests with file hashes, pinned model sources, runtime weight identities, and stage-to-stage lineage verification.
- Add configurable automatic retention for terminal jobs, uploads, and logs, plus manual cleanup and storage reporting in the WebUI.
- Rotate service logs and keep exported artifacts outside automatic retention.
- Re-run full song generation, transcription, and staged semantic/synthesis/decode validation on the current release.

## 1.0.3 - 2026-09-10

- Block job-ID path traversal in file and artifact export APIs.
- Preserve cancellation through VAE decoding and terminate the complete worker process tree when stopping the service.
- Use collision-free atomic status writes and accurate token counters.
- Validate model bundle hashes and every installer subprocess; handle missing and per-protocol Windows proxy settings.
- Detect stale or conflicting local services, preserve successful candidates on a later candidate failure, and fix WebUI multi-job tracking.
- Validate full model layouts and reject unsupported batched transcription input.

## 1.0.2 - 2026-09-10

- Use the current `comfy node install yue2-t8` CLI command in the README.

## 1.0.1 - 2026-09-10

- Pin the installer to the verified `t8star/YuE2-Comfy` model bundle commit.
- Fix Registry links and Windows launcher packaging.

## 1.0.0 - 2026-09-10

- Initial Comfy Registry release.
- Add 11 nodes for song generation, editable ABC plans, transcription, cover generation, staged inference, artifact export, and cancellation.
- Add an isolated Windows runtime so the YuE2 dependencies do not replace ComfyUI's Torch installation.
- Add the local WebUI, shared single-GPU scheduler, example workflows, and resumable model setup.
