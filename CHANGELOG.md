# Changelog

## 1.5.12 - 2026-09-18

- Prevent duplicate paid assistant submissions before configuration/draft saves complete; wait before project changes and continue tracking submitted jobs when draft persistence fails.
- Constrain asset purge to its approved preview, scan past protected trash batches and expose occupied waveform or zero-byte GC retries.
- Preserve retryable job control records during partial Windows/manual or policy cleanup; reject directory junctions and mark partially cleaned results unavailable.
- Discard stale training-project and style-model reads, align resized asset pages, open active training assets and persist model handoffs.
- Translate the instrumental checkbox into explicit native no-vocal text conditions and the instrumental lyric marker (Issue #12); this remains model guidance.
- Add optional MuLaCover style conditioning using the upstream empty-tag behavior (Issue #13), preserving legacy drafts and API defaults without promising identical arrangements.

## 1.5.9 - 2026-09-17

- Shorten atomic checkpoint staging directories and JSON temporary filenames so deeply nested reference-cover jobs fit the legacy Windows path limit without sacrificing complete-checkpoint publication or unique concurrent writes.
- Keep final checkpoint names and manifests compatible with existing jobs; clean temporary JSON files when serialization or replacement fails.
- Add storage regressions for the reported 262-character path, near-limit directories, cancellation and concurrent JSON writers, including the Windows CI job.
- Fix the unanchored model-data ignore rule that omitted MuLaCover codec and torchtune Python sources from GitHub/Registry/code ZIPs; validate required sources in readiness and release archives (Issue #9).

## 1.5.8 - 2026-09-16

- Fix project asset pagination when one revision has multiple project roles, reject ambiguous voice booleans and empty pitch controls, and localize malformed request errors.
- Keep invalid ABC recovery guidance visible, add copy confirmation, remove late assistant initialization overwrites, and make mobile asset pagination, keyboard entry and repeated control names accessible.
- Exit the Windows launcher after a successful start so in-app updates can replace it; publish Registry packages only from immutable version tags whose complete Quality workflow passed.
- Exclude development-only CI/tests from code update ZIPs, keep runtime verification read-only, and pin all release actions to reviewed commit SHAs.

## 1.5.7 - 2026-09-16

- Replace blocking browser alerts, confirmations and text prompts with accessible in-studio dialogs for project, model, cleanup and update actions.
- Restore the latest project-scoped AI creation even after any number of later model connection tests by filtering tests before pagination.
- Align the stable release tag with a fully passing Linux/browser and Windows launcher/update CI run.

## 1.5.6 - 2026-09-16

- Serialize job status updates across the service and worker so a last-moment cancel can no longer overwrite a committed result.
- Recover the active job and project-scoped assistant result beyond the recent-task window; show the real queue total and hide non-exportable housekeeping tasks from export actions.
- Page through every training run and project asset instead of silently stopping at 200 or 500 records; keep project tracks and linked assets bounded to 8 and 10 items per page.
- Disable AI actions until the selected API credential or local GGUF is ready, surface model-list failures, disambiguate trained style models, and improve technical audio-component errors.
- Add keyboard skip navigation, clearer mobile labels and compact non-project headers, plus Windows launcher version metadata and CI coverage.

## 1.5.5 - 2026-09-16

- AI 创作助手的新安装默认使用 32K 输出预算，云端与兼容接口允许设置到 262,144 Token，覆盖 220K 需求；实际可用值仍由模型和渠道限制。
- 切换本地 GGUF 时独立恢复保守的 4K 输出预算，避免云端大额度超过本地上下文；界面同时说明费用和模型限制。

## 1.5.4 - 2026-09-16

- RVC 切片、音高提取、HuBERT 特征和训练轮次现在把实时 `x/y` 进度写入后台任务，离开页面后仍可在任务中心查看。
- 完整包构建器强制包含 MuLaCover、HeartCodec、Qwen3 Embedding、SymbolicTranscriptor、示例模型与原生 ComfyUI MuLaCover 节点包，避免发布缺功能的“完整包”。

## 1.5.3 - 2026-09-16

- Keep asset-library and completed-job audio on the server during cross-workspace handoff instead of downloading the whole file into browser memory and uploading it again.
- Add native ComfyUI nodes for YuE2 style training, trained LoRA selection and strength, RVC voice loading, RVC training and RVC song conversion.
- Allow completed YuE2 training output to connect directly to song generation and completed RVC training output to connect directly to RVC conversion.
- Preserve manual uploads while clearly tracking server-side audio and MIDI references across previews, transcription, RVC import, MuLaCover and voice conversion.

## 1.5.2 - 2026-09-16

- Require verified, separated vocals for RVC training and reject unreviewed or mixed material before GPU work starts.
- Restrict YuE2 style training to complete songs or works of at least five seconds and derive train/validation lineage from immutable server metadata.
- Persist project-scoped YuE2 training and MuLaCover drafts, restore archived projects, preview training sources and keep completed model controls stable while refreshing.
- Restore the latest result for every project panel from the complete job store while returning compact task summaries without large stage-history arrays.
- Archive generated ABC and MuLaCover MIDI files into the asset library and stream large job files with HTTP range support.
- Recover stale updater transactions after a crashed process and include RVC and MuLaCover model setup in the unified installer.
- Add a four-step first-run guide, correct mobile training layout and validate desktop, tablet and phone workflows in the browser smoke suite.

## 1.5.1 - 2026-09-16

- Accept `127.0.0.1`, `localhost` and IPv6 loopback aliases for browser POST requests when the port matches, preventing valid local pages from being rejected as cross-site requests.
- Continue rejecting non-loopback origins, browser-extension origins and loopback pages on another port.

## 1.5.0 - 2026-09-15

- Add a project-scoped MuLaCover remix workspace for audio or MIDI conditioning, editable lyrics and structured style tags, transposition, deterministic seeds, live stages, playback, MIDI export and one-click handoff to voice conversion.
- Run MuLaCover in the existing unified Python 3.12 runtime with lazy, sequential component loading, cooperative cancellation and GPU cleanup.
- Add pinned model download and integrity checks for MuLaCover, HeartCodec, Qwen3 Embedding and the symbolic transcriptor.
- Publish the separate `Comfyui-Mulacover-T8` native node package for in-process ComfyUI workflows without an HTTP bridge.

## 1.4.21 - 2026-09-15

- Restore the previously hidden creator and project links in the compact studio header.
- Add direct, visible links to the GitHub source, ComfyUI Registry node, Hugging Face model repository, Bilibili creator page and YouTube channel.
- Prevent older training-record requests from overwriting a newer model-manager refresh during rapid workspace switching.

## 1.4.20 - 2026-09-15

- Add a completed YuE2 model manager with three models per page, model details, equal-size actions and direct use, download, folder and path controls.
- Export completed YuE2 training tasks as a usable `.safetensors` model package with a provenance manifest.
- Hide export actions for internal tasks that do not produce files and label YuE2 training exports clearly.
- Limit task history to ten records per page while preserving filters and navigation.

## 1.4.19 - 2026-09-15

- Let every YuE2 training song use a manually entered style, a saved style asset, or the shared fallback style.
- Add a persistent completed-model panel with completed and selected steps, losses, rank, size, local path, model download, folder access and one-click use in song creation.
- Identify byte-identical duplicate imports by both visible filenames before rejecting a train/validation split.

## 1.4.18 - 2026-09-15

- Persist each paid AI Assistant checkpoint as soon as it reaches the browser, so later ABC work cannot hide already generated lyrics or style text.
- Restore the latest project-scoped Assistant task from the durable job store after workspace switches, reloads, and browser restarts, even when the draft did not record its job ID.
- Recheck the current Assistant task whenever its workspace opens while preserving deliberate user edits.

## 1.4.17 - 2026-09-15

- Migrate older assistant drafts from the former downstream ABC default while preserving deliberate downstream choices saved by the new draft format.
- Make YuE2 quick training (200 steps) the default and align its underlying advanced parameters with that preset.
- Force training text fields to left-to-right, left-aligned entry so English style prompts cannot unexpectedly type from the right edge.
- Preserve an invalid paid ABC response in the result and allow sending it unchanged to the score-planning prompt, with a visible validation warning at both ends.
- Add a real Seedance ABC-generation acceptance check to the release verification record.

## 1.4.16 - 2026-09-15

- Change the Seedance provider default to `bytedance/doubao-seed-2.1-turbo` and expose an explicit Custom model choice with a dedicated model-ID input.
- Generate ABC by default for new AI Assistant drafts and add a one-click “补写 ABC” action to results created with downstream score planning.
- Let every YuE2 training song use pasted lyrics, a linked lyrics asset, the optional shared fallback, or an explicit instrumental flag.
- Replace silent native form blocking with an inline validation and background-preprocessing status beside the training action.

## 1.4.15 - 2026-09-15

- Scope YuE2 training songs to the current project and refresh them whenever the training workspace opens, removing the stale empty list seen after adding assets.
- Add “添加歌曲”, guided asset-library and direct-import paths; explain that an audio asset becomes trainable after it is added to the current project.
- Skip byte-identical files during automatic updates, avoiding Windows launcher replacement failures when the unchanged native launcher is temporarily locked.

## 1.4.14 - 2026-09-15

- Make AI provider readiness visible before creation, with a direct API Key setup button and the selected provider's registration link.
- Detect expired session-only credentials after restart, automatically expand channel settings, and prevent credential-less assistant jobs from entering the queue.
- Add one-click API Key recovery to credential failure results, preserving keyboard focus and responsive layout across desktop, tablet and phone widths.

## 1.4.13 - 2026-09-15

- Gate RVC output from the separated source-vocal envelope so instrumental leakage cannot become sustained synthetic tones.
- Save and restore Korean and other UTF-8 symbolic plans independently of the Windows system locale.

## 1.4.12 - 2026-09-15

- Complete the requested YuE2 training schedule but publish the checkpoint with the lowest song-disjoint validation loss.
- Record both completed steps and the selected checkpoint step in model metadata.

## 1.4.11 - 2026-09-15

- Train YuE2 style LoRAs with reproducible 30-second semantic windows so long songs remain practical on 24GB GPUs.
- Evaluate fixed start, middle and end windows while keeping song-disjoint validation groups.

## 1.4.10 - 2026-09-15

- Keep full-bundle build manifests location-independent and remove local build paths from verification logs.
- Document all eight model directories, including the RVC and YuE2 style-training resources mirrored on Hugging Face.

## 1.4.9 - 2026-09-15

- Put Seed-VC and RVC octave controls directly in the reference-cover workflow, with clear female-song-to-male-voice and male-song-to-female-voice presets.
- Preserve independent Seed-VC and RVC pitch choices, explain the effect of non-octave shifts, and show the selected pitch in completed-result descriptions.
- Reject boolean values in numeric voice parameters before a task enters the queue.

## 1.4.8 - 2026-09-15

- Raise the contrast of rose section markers, primary buttons, completed workflow stages and completed history states to meet WCAG AA for normal-size text.
- Keep backend-reported task progress visible while users switch workspaces or scroll, with direct access to the full task details.
- Recheck the complete desktop and mobile workbench, fresh-start guidance, keyboard dialog flow, persistent asset membership and code-only update package.

## 1.4.7 - 2026-09-15

- Give every generated, historical, comparison, stem, RVC, training-preview and global audio player a distinct accessible name.
- Give the read-only asset-content field an accessible name and remove unsupported selection semantics from ordinary workspace buttons.
- Hide raw technical errors in partially completed multi-candidate result summaries, while retaining full details in task logs.
- Extend real-browser accessibility checks to audio players, open-dialog contents, navigation semantics and partial-result failures.

## 1.4.6 - 2026-09-15

- Show whether each asset's current revision is already linked to the selected project, instead of leaving an idempotent “Add to project” action that appears to do nothing.
- Keep “Add to project” available when an asset has a newer revision than the revision pinned in the project.
- Extend real-browser regression coverage with a mixed set of linked and unlinked assets.

## 1.4.5 - 2026-09-15

- Keep every asset-card action inside its card at desktop widths, including the fourth “Add to project” action.
- Reset the page position when switching workspaces and immediately remove stale result banners from unrelated pages.
- Give dynamic asset dialogs accessible names and replace browser-default required-field messages with Chinese guidance.
- Summarize technical failures consistently on live and history pages while preserving full details in task logs.
- Treat completed update records from older releases as idle, and extend real-browser regression coverage with seeded projects, assets and a completed playable result.

## 1.4.4 - 2026-09-14

- Add an always-visible “All workspaces” menu to phone and tablet layouts, with current-workspace highlighting and full keyboard/dialog semantics.
- Run a real Chromium UI smoke suite in GitHub Quality at desktop, tablet and phone viewports. Validate navigation, project focus, empty-filter recovery, disabled task actions, accessible control names, unique DOM ids, page overflow and console errors.
- Upload desktop, tablet, phone and phone-menu screenshots plus the browser/service report as CI artifacts for every Quality run.
- Exclude source-control, virtual-environment, package-cache and other development-only directories when applying an update from an unpacked source tree; retry short Windows file locks and keep rollback idempotent.

## 1.4.3 - 2026-09-14

- Remove the duplicate desktop navigation and compact the header on phone and tablet layouts so the active workspace appears earlier.
- Keep the current project visible below 1024 px and provide a direct shortcut back to the project selector.
- Clarify update and storage-cleanup actions, require confirmation before retention cleanup, disable task cancellation when no task is running, and summarize technical failures with details available on demand.
- Add recovery actions for empty filtered asset and task lists, accessible names for previously ambiguous controls, and stronger contrast for supporting text.

## 1.4.2 - 2026-09-14

- Bind uploads, submissions, drafts, results and errors to the project that started them, even when the user switches projects before an asynchronous request finishes.
- Keep YuE2 training, checkpoint previews and auxiliary jobs in separate UI channels; preserve pause/resume controls and send the exact selected checkpoint model into song creation.
- Import ABC score assets into an editable score-plan draft and keep generated previews associated with their originating project.
- Make the full workbench usable at narrow desktop and tablet widths with a scrollable top navigation, visible active workspace, stacked controls and automatically collapsed model settings.
- Prevent stale project, asset and history requests from replacing newer selections. Clarify the local/API assistant description and the 1600-step training preset.
- Keep the GitHub updater archive code-only. It excludes Python, models, GGUF files, user data and the private roadmap.

## 1.4.1 - 2026-09-14

- Make model settings reachable from every workbench page and simplify mobile navigation, tap targets, assistant setup and YuE2 training presets.
- Keep project-scoped assistant, creation, score and cover drafts isolated across initial selection, switching, archiving, refresh and failed draft loads; assistant tasks now retain their project scope.
- Add asset paging, editing, text revisions and direct transfer into song creation, score planning, reference-voice cover and RVC training while keeping training inputs independent from asset filters.
- Require per-song lyrics or an explicit instrumental marker, preserve per-song style/lyrics choices, and reject duplicate audio blobs across training and validation splits.
- Add training-run and checkpoint selectors, integrity-bound previews for paused checkpoints, accurate missing validation metrics and visible polling failures.
- Add project rename/archive/remove actions, dynamic workflow progress, master-version selection and a verified audio-plus-manifest project export.
- Add type, project, status and text filters to paged task history; refresh the active project after completed jobs and keep unrelated results off the current page.
- Add the missing favicon and responsive/accessibility fixes. GitHub updater artifacts remain code-only and contain no Python runtime, models, GGUF files or user data.

## 1.4.0 - 2026-09-14

- Redesign the standalone WebUI as a connected music workbench while preserving the established light blue-gray and berry palette.
- Add projects and a content-addressed asset library for songs, vocals, instrumentals, reference voices, lyrics, scores, works and trained models, with waveform previews and revision-pinned project links.
- Add YuE2 AR LoRA song-style training from immutable train/validation snapshots, pinned Mothersuperior v4 tokenizer/NAR resources, MERT features, loss charts, safe pause/resume and a model library.
- Pair trained AR adapters with the pinned v4 NAR companion during direct generation, record full model provenance, and generate playable short checkpoint previews inside the training page.
- Require a recorded rights confirmation for YuE2 training snapshots and auto-promote completed audio and model outputs into the current project and asset library.
- Keep generation, transcription, Seed-VC, RVC, YuE2 training and local GGUF in the single verified CPython 3.12.10 runtime; code-only updates still exclude Python, models and user data.

## 1.3.1 - 2026-09-14

- Allow ComfyUI users to select realistic memory budgets below 12 GiB or above 24 GiB.
- Resume failed generation with the current memory budget instead of silently reusing the failed value.
- Choose VAE decode tiles from the smaller of physical VRAM and the configured budget, with validated explicit overrides.
- Reject experimental FP8 on HIP/ROCm and report CUDA/HIP accelerators without NVIDIA-only wording.
- Warn before replacing local code, require repository quality checks, and fail Registry publishing when a version is not accepted.
- Include the merged HIP/ROCm decode and Seed-VC compatibility fixes from PR #2.

## 1.3.0 - 2026-09-12

- Unify generation, transcription, Seed-VC, RVC and GGUF under CPython 3.12.10 / Torch 2.10.0 + CUDA 12.8; keep sequential subprocess model release.
- Add an RVC training workbench with material preview/review, separation, preflight, progress/logs, cancellation/resume, paired checkpoints, indexes and a versioned voice library.
- Add validated storage migration for training projects/cache, datasets and voices, preserving original backups and resumable migration state.
- Convert existing songs directly or compare Seed-VC and RVC sequentially using a verified shared Demucs cache. Preserve playable successful candidates when the other backend fails.
- Migrate legacy runtimes transactionally during updates, verify new service startup, and roll back code/runtime on failure. Retire the old code-only patch installer.
- Package optional prebuilt/local SM120 FlashAttention wheels; standalone flash_attn is not enabled in song generation.
- Verify GPU/CPU compatibility, long repeated-audio conversion, checkpoint recovery and browser workflows. Voice-quality benchmarks and physical low-VRAM GPU support are not claimed.

## 1.2.2 - 2026-09-12

- Add a visible update control to the WebUI status card and automatically check the stable GitHub Release channel when the page opens.
- Download and validate the project-bound update manifest and code archive before installation, including the release URL, archive root, size limits, and SHA256.
- Install updates through a separate process, preserve models, runtimes, local LLM files, works, uploads, drafts, settings, logs, and caches, then restart the same local port.
- Back up every replaced file and automatically restore the previous code if the updated service cannot start.

## 1.2.1 - 2026-09-12

- Add channel-specific default models: `bytedance/doubao-seed-evolving` for ZhenZhen Affordable AI Shop and `gemini-3.5-flash` for ZhenZhen AI Workshop.
- Add searchable provider model choices and a bounded OpenAI-compatible `/models` refresh. A failed LIST request keeps the defaults and manual model-ID input available.
- Add the two provider registration links beside the channel settings so users can obtain the matching API key without searching elsewhere.

## 1.2.0 - 2026-09-11

- Add a standalone AI creation tab with the T8 project's pinned YuE2 skill rules, API provider options and isolated local GGUF support; no new ComfyUI nodes.
- Edit, save and transfer lyrics, style and validated ABC between tabs, with revision checks, overwrite review, undo and result restoration after refresh.
- Preserve completed text on failures; resume only failed stages and reuse successful responses. Keep API credentials out of jobs and prevent repeated submissions from creating duplicate paid tasks.
- Add a pinned Windows GGUF runtime installer, verified resumable downloads, packaged CUDA dependencies and metadata/shard checks without importing Torch into the LLM worker.
- Preserve assistant drafts and local models during updates; use the memory-saving generation default when importing an external score into the plan page.
- Verify the Seedance API, imported-score audio generation, browser workflows and error recovery. Local Qwen 27B lyrics/style generation passes; constrain response fields to avoid placeholder/wrong-field output. Reject over-budget prompts without truncating lyrics.
- Make the local GGUF directory optional and add a separate model download link. API users do not need local LLM weights. Local ABC quality remains model-dependent; invalid scores preserve text and can fall back to YuE2 planning.

## 1.1.6 - 2026-09-11

- Preview the source song and reference voice immediately after file selection, with native playback, seeking, duration and file replacement controls.
- Keep players outside file-input overlays, release replaced file URLs, pause the other upload preview, and show a helpful message for unsupported browser audio formats.
- Verify WAV/FLAC playback, replacement, seeking, cleanup and narrow-screen layout in the browser. This release does not change inference backends.

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
