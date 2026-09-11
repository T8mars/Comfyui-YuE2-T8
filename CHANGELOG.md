# Changelog

## 1.1.5 - 2026-09-11

- Bound NAR attention query tiles on CUDA, select supported fused kernels explicitly, and fall back to bounded math with limited OOM retries. Enable AR offload by default without truncating songs or changing BF16, seeds, CFG, or solver steps.
- Save verified plan, semantic and latent checkpoints; resume failed generation and exact-plan rendering without repeating completed stages.
- Run reference-cover generation and conversion as a persistent backend workflow with sequential GPU workers, cancellation, stage progress and resource logs.
- Save and verify voice separation/conversion checkpoints; preserve a visible result or failure card across browser refreshes and provide recovery buttons.
- Show completed audio, duration and a direct download above the current page's form. Persist its page association and selected tab, restore older cover results, and preserve the player during polling.
- Add regressions for attention correctness, OOM bounds, cancellation, checkpoint integrity, workflow ordering, page refresh and mobile failure display.

## 1.1.4 - 2026-09-11

- Add an in-page model directory setting with the active path, bundled-default reset, folder opener, model layout guidance, and a direct Hugging Face link.
- Apply the configured model root consistently to generation, transcription, score rendering, reference-voice conversion, provenance, verification, and model downloads.
- Add a command-line model path configurator and prepare code-only GitHub Release assets with checksums and a machine-readable update manifest that preserves models and user data.

## 1.1.3 - 2026-09-11

- Restore the vendored YuE2 inference package in standalone archives so generation and reference-voice cover flows can start.
- Show the final worker exception in failed jobs and add in-page task-log viewing plus buttons that open the managed log and output directories.
- Reject jobs before queueing when their generation, transcription, or voice-conversion capability is incomplete, with a specific repair message.

## 1.1.2 - 2026-09-11

- Automatically switch from another idle YuE2 installation that already owns port 8189 after verifying the exact service process and executable path.
- Refuse to switch while the other installation has a running or queued task, and identify the protected task in the launcher message.

## 1.1.1 - 2026-09-11

- Add a native `YuE2-T8.exe` launcher for the standalone Windows bundle, while keeping the batch launcher as a compatibility entry point.
- Keep launcher windows open after both successful and failed starts so the service URL or failure reason remains visible.
- Show localized runtime checks, service startup progress, stale-service guidance, and log locations instead of silently closing.

## 1.1.0 - 2026-09-11

- Add local zero-shot reference-voice covers with Demucs vocal separation, Seed-VC conversion, and 48 kHz stereo remixing.
- Add the `YuE2 参考音色翻唱` ComfyUI node and a fourth front-end workflow using separate source-song and reference-voice inputs.
- Add an isolated Python 3.11 voice runtime, offline model verification, detailed capability checks, and local-only worker stages.
- Redesign the WebUI cover page as two integrated choices: melody remake or reference voice, with novice guidance and advanced voice controls.
- Validate the WebUI layout, a 30-step live service conversion, and a real ComfyUI `/prompt` execution.

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
