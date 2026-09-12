# YuE2 本地整合验证报告

## v1.3.0 发布候选验证（2026-09-12）

- 高音排查：用户指出男声音色的气声、高音及自然长歌三项 RVC 输出崩坏。保持原调改用 FP32 未解决高音问题；关闭检索并明确降八度后的三项对照，用户反馈“降低八度后声音都正常了”。该反馈仅覆盖这三项参数调整版本，原调失败样本保留，其他项目不据此视为人工通过。
- 音高控制回归：同一真实 GPU 对比任务中，Seed-VC 保持 0 半音、RVC 使用 -12 半音，两个子进程的成品记录与设置一致；两套已完成的 100 轮音色补充音域统计时复用原权重、索引和音色 ID。界面验证涵盖独立参数提交、当前/历史结果标注、390px 布局。音域统计来自训练连续 F0，不是模型能力边界。

目标设备：RTX 5090 Laptop 24GB，Windows，统一 CPython 3.12.10、Torch 2.10.0+cu128、NumPy 1.26.4、Transformers 4.57.6。以下为发布候选的已完成验证。包含运行时和七组基础模型的实际完整包已构建并冷启动验证；尚未完成发布，不代表已上传。

- 清洁统一环境：120 项锁定依赖及浏览器/FFmpeg 探测；真实 GPU 歌曲生成、Seed-VC、转谱及渲染通过。发布源码的 111 项自动测试全部通过，JavaScript 与 PowerShell 语法检查通过，统一环境按发布依赖清单重新校验通过。
- 本地 GGUF：歌词/曲风/有效 8 小节 ABC 与真实浏览器跨页发送通过；另一份样例 ABC 无效，保留文本，未降低谱面校验要求。
- RVC：真实 GPU 训练取消/续训、推理混音；CPU v1/48k 与 v2/40k 训练/续训；重复完成注册复用同一音色；目录迁移后继续训练通过。这些少量轮次样例验证流程，不证明成熟音色质量。
- 公开数据正式训练：CSD 干声 20 分 41 秒、14 个文件，RVC v2 / 48k / RMVPE 完成 100 轮，实际批次为 6，共 2777.30 秒（46 分 17 秒），生成推理模型、匹配索引和试听样例并通过登记校验。测试歌曲按歌曲 ID 留出，两个调性同时排除；测试权重与录音不随发布包分发。音质比较另行记录。
- Seed/RVC 同曲对比：真实 12 秒以及重复拼接的 180 秒输入均完成两路 48kHz 音频；共用分轨缓存；恢复后的成品 SHA256 与原结果一致。重复拼接长音频不是自然长歌音质测试。
- 180 秒转换在每个推理子进程 4 GiB Torch 分配器上限下通过，Seed 峰值 allocated 3.152 GiB。此限制不覆盖所有 CUDA 分配或 WDDM，也不证明实体 4/6GB 显卡或整首 YuE2 生成适用。
- 真实训练 OOM：在 3 GiB Torch 分配器限制下实际触发两次 CUDA OOM，批量 6→3→1，从第 3 轮检查点续训并完成第 4 轮，注册音色成功。总耗时 111.97 秒；这是 RTX 5090 上的分配器限制测试，不是实体 3GB 显卡测试。
- 完整包：使用包内唯一的 `runtime/python.exe` 完成依赖、浏览器/FFmpeg 和七组模型校验；原生 EXE 在独立端口 8198 冷启动，页面和 ComfyUI 客户端连接通过。Python 搜索路径只指向该包。
- 便携运行库：补齐经过微软签名与哈希校验的 VC++ x64 DLL，版本与 Python 已携带的 DLL 一致。新进程实际加载的 `MSVCP140.dll` 位于包内，统一组件、浏览器及 FFmpeg 检查通过；安装和完整包构建均校验这些文件，更新器也会识别其版本变化。这不是全新 Windows 虚拟机实测。
- 浏览器：上传试听、当前页/历史双结果、失败保留音频、下载/导出、直接换声、训练预检/进度及迁移通过；显示用双路音频夹具与真实双路 GPU 验证分开记录。
- 更新：旧版到新统一环境、注入新服务启动失败后回滚、仅模型更新复用环境通过。
- FlashAttention 2.8.3：社区轮子及本地 SM120 编译轮子各 12 项 GPU 算子检查通过；当前 YuE2 未接入独立 flash_attn，不宣称整曲加速。

本页记录功能与环境验收。公开素材的跨歌手、音域、气声及自然长歌对比，以及说话人相似度、歌词识别、DNSMOS 和人工盲听状态，见发布附件 [RVC_EVALUATION.md](https://github.com/T8mars/Comfyui-YuE2-T8/releases/download/v1.3.0/RVC_EVALUATION.md)。测试录音和测试音色不随发布包分发；不把信号指标写成主观音质排名或低显存硬件承诺。

## 历史验证记录

以下保留旧版本在当时环境的结果与交付状态，不代表当前状态。

# YuE2 Music T8 validation

Validation date: 2026-09-11. Host: Windows, NVIDIA GeForce RTX 5090 Laptop GPU (24 GB). Historical results below are version-specific.

## 1.1.5 long-song memory and result visibility validation

- Replayed the original failing generation input without shortening the 280.999-second source transcription, lyrics, style or seed. The complete generation, separation, 30-step reference conversion and remix produced a 300.399-second 48 kHz stereo FLAC in 759.93 seconds. Generated duration may differ from the source. BF16, CFG and solver steps were retained; FP8 was not used.
- Generation peak active PyTorch allocation was 8.513 GiB; reference conversion peaked at 4.030 GiB in its separate worker. NAR offload moved 4.034 GiB of model weights to CPU and reduced active GPU allocation from 7.799 to 3.764 GiB at that boundary. Actual query tiles were at most 256 rows, with bounded math and cuDNN execution and zero OOM retries.
- Three consecutive workflows used 30/120/30-second source excerpts and produced 29.879/103.039/30.639-second covers in 120.92/282.99/149.75 seconds, excluding queue and transcription time. Their generation peaks were 7.795/7.899/7.796 GiB. All child processes began with the same 22.495 GiB available; no task-to-task accumulation was observed. Final idle GPU usage was 404 MiB. These are observations on this host, not guarantees under arbitrary external GPU load.
- A separate real workflow was cancelled at NAR step 2/32, then resumed from semantic checkpoints and completed. Every semantic checkpoint file retained its SHA256. Atomic checkpoint tests also cover interrupted writes and decoding failures that resume latents without repeating sampling.
- A real browser submitted the reference-cover flow, refreshed and closed while it ran; the backend still completed. The final player appeared above the current cover form, playback advanced, its downloaded FLAC matched the backend file hash, and a reload restored the same page and result. Mock API browser checks separately cover failure/log/recovery controls, tab switching, polling without replacing the player, and 390 px layout.
- CUDA BF16 regressions compare 128/256/512 query rows, and attention tests cover GQA, full visible keys, absolute causal masks, partial final blocks, bounded OOM retry and unavailable fused kernels. Saved ComfyUI examples now explicitly enable AR offload and use automatic attention with 256-row tiles.
- Runtime, model weights, original recordings, generated audio and private job logs are excluded from the published source and code ZIP. Public test summaries report measurements without bundling user media.
- The clean release checkout ran 41 automated tests: 39 passed and two skipped because model files and the voice runtime's SciPy dependency are not included in the code checkout. JavaScript syntax and Git whitespace checks passed. The development installation separately passed the model-presence check and installer preservation regression.

Validation limits: the separate existing 1.1.4 desktop service has not been replaced or restarted, and the updated nodes have not yet been reloaded in that running ComfyUI installation. These local installation checks remain separate from the source release. Audio finiteness, level and whole-second silence checks passed, but subjective listening and voice-similarity review have not been performed. The release does not claim those checks.

## 1.1.4 configurable model directory validation

- Added one page-integrated model setting shared by the WebUI, service workers, ComfyUI nodes, model verifiers, score renderer, and runtime installer. The setting stores only the chosen directory in `settings.json`; the default remains the bundle-local `models` folder.
- A live 1.1.4 service switched from `E:\\yue2\\models` to the separate installed model folder `E:\\YuE2-T8-Local-v1.1.3-Windows-NVIDIA-20260911\\models`. Health reported generation, transcription, score rendering, and reference-voice conversion ready from the external path, then reported all four ready again after resetting to the default.
- Release tests passed 25 checks with two expected environment skips. JavaScript syntax, Python compilation, PowerShell parsing, Git whitespace validation, and live settings API reads/writes passed.
- Browser validation at 1280 px confirmed the collapsed setting shows the active path, the expanded panel exposes the exact six-subdirectory layout and Hugging Face link, and the page has no horizontal overflow or console errors.

## 1.1.3 standalone source and diagnostics validation

- Restored all 14 tracked YuE2 0.1.6 inference files under `vendor/yue2`; live health reports generation, transcription, score rendering, and reference-voice conversion ready.
- Replayed the exact request that failed in 1.1.2. Job `20260911-135440-d0f97332` completed a 46.1987-second song with seed 831001 and no ABC or semantic truncation.
- Live 30-step Seed-VC job `20260911-135831-3d37d331` converted that song with the reference-voice path, completing Demucs separation, voice conversion, and 48 kHz stereo remix for the full 46.199 seconds.
- The WebUI history view exposed “查看任务日志”, “打开日志目录”, and “打开输出目录”; expanding the original failed job displayed its full traceback inside the page.
- Integration checks passed all 25 tests, including worker error extraction and all eight localized workflow JSON files across the four workflow types.

## 1.1.2 multi-installation launcher validation

- When a different idle YuE2 installation owns port 8189, the launcher verifies its state file, service command line, and exact Python executable before switching to the requested installation.
- Running and queued jobs prevent automatic switching and remain untouched; the launcher reports the protected job and queue size.

## 1.1.1 launcher validation

- The native Windows launcher and compatibility batch launcher both start or reuse the local service and leave a visible success or failure result.
- Automated no-browser/no-pause checks verify exit codes without changing the normal double-click behavior.
- A missing-runtime fixture returns a nonzero exit code with an actionable Chinese error instead of flashing and disappearing.

## 1.1.0 reference-voice validation

- Release source checks ran 24 tests: 23 passed and the model-installation check was skipped as expected; the installed local source under the voice runtime passed all 24 checks.
- All 24 Seed-VC/Demucs bundle files passed the pinned size and SHA-256 check in `VOICE_MODEL_MANIFEST.json` (2,574,547,539 bytes total).
- Live service job `20260911-045414-30d09856` completed with 30 diffusion steps. Demucs separated the source, Seed-VC converted the vocal, and the worker produced a finite 12.0-second, 48 kHz stereo FLAC together with separated vocal, converted vocal, accompaniment, result JSON, and artifact manifest.
- ComfyUI prompt `4a593e72-f797-4b94-adf7-630d7a87d925` loaded two real AUDIO inputs and executed `YuE2ReferenceVoiceCover` at four diffusion steps. ComfyUI reported `success` with no node validation errors and PreviewAudio produced a 12.0-second, 48 kHz stereo FLAC (peak 0.98001, RMS 0.24306, all samples finite).
- ComfyUI 0.33.0 registered the new node after reinstall/restart. The local service recorded the linked job `20260911-050433-ef9b2cd5` with source `comfyui` and terminal status `complete`.
- The WebUI was rendered at 1508×1000. The page has no horizontal overflow; reference-voice controls remain inside the document flow and use the same pink, blue, white, and slate palette as the rest of the integration.

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
