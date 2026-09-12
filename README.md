# ComfyUI YuE2 T8

[中文](#中文说明) · [English](#english) · [模型仓库 / Model weights](https://huggingface.co/t8star/YuE2-Comfy) · [ComfyUI Registry](https://registry.comfy.org/nodes/yue2-t8)

![YuE2 Music T8](icon.svg)

## 完整版整合包

完整版整合包：[夸克网盘下载](https://pan.quark.cn/s/67ebf18a2d51)

Windows / NVIDIA 完整包，包含运行环境与模型。完整解压后，双击 `YuE2-T8.exe` 即可启动。

v1.3.0 完整包也可从 [GitHub Release](https://github.com/T8mars/Comfyui-YuE2-T8/releases/tag/v1.3.0) 下载：选择名称以 `YuE2-T8-Local-v1.3.0` 开头的全部 `.7z.001`、`.7z.002` 等分卷，放在同一目录，用 7-Zip 打开 `.001` 解压。完整包包含一套 Python 3.12.10、七组基础模型和 GGUF 后端；GGUF 权重按需另下。`code.zip` 是更新用的代码包，不含运行环境与模型。

## 模型网盘

模型网盘：[夸克网盘下载](https://pan.quark.cn/s/6c40eac8af6c)

模型放置方式见下方“模型放置路径”。


## 本地 LLM 模型（可选）

本地 LLM 模型：[夸克网盘下载](https://pan.quark.cn/s/55eab3bb2d9b)。

用于独立 WebUI 的“AI 创作助手”生成歌词、曲风和可选 ABC。使用 API 无需下载。使用本地模式时，解压模型，在助手设置中填写存放 GGUF 的目录（例如 `E:\LLM`），保存后选择模型并测试连接；目录留空则使用模型根目录下的 `LLM` 文件夹。该下载独立于音乐模型包，不是运行 YuE2 的必选项。

## 中文说明

YuE2 Music T8 把 YuE2-3B 完整歌曲生成接入 ComfyUI，并提供一个可单独使用的本地 WebUI。节点通过 `127.0.0.1:8189` 调用隔离的推理 worker，不会替换或污染 ComfyUI 自带的 Torch 环境。1.1.0 新增 Seed-VC + Demucs 零样本参考音色翻唱；1.1.1 新增 Windows EXE 启动器；1.1.2 可在端口被另一套空闲 YuE2 占用时自动安全切换；1.1.3 修复独立整合包缺少 YuE2 推理源码的问题并提供页面日志；1.1.4 新增模型目录设置和无模型 GitHub Release 更新清单。

主要功能：

- 中文、英文歌词生成 48kHz 双声道歌曲；支持 `full`、`melody`、`off` 三种规划模式。
- 生成并保存 ABC 旋律/和弦计划，可精确恢复原始计划，也可编辑或导入 ABC 后重新生成。
- 一次生成 1–8 个连续种子候选；完整歌曲工件保留请求、配置、tokens、latents 与完整性清单，后续候选失败时仍保留已完成结果。
- 使用 SheetSage2 + MERT 把 WAV、FLAC、MP3、M4A、OGG、AAC 转为 ABC/MIDI，并生成翻唱。
- 输入 1–30 秒参考干声，把新生成歌曲的人声转换为参考音色，再与 Demucs 分离的伴奏混合为 48 kHz 双声道 FLAC。
- 共享单 GPU 队列、任务中心、逐项取消、任务历史与导出；任务中心会区分当前任务和完整等待列表，并显示来源、阶段、风格摘要与排队顺序。
- 自动清理过期或超出容量的任务、上传和日志；`exports` 中的重要成品永久保留，服务重启时会把中断任务明确标为失败。

### v1.3.0：统一运行环境与 RVC 训练工作台

- 所有本地功能共用 Python 3.12.10 / Torch 2.10.0 + CUDA 12.8，逐阶段子进程运行。
- “我的音色 / 训练”：导入素材、试听筛选、分离伴奏、训练、取消/续训、建索引、音色库预览与导入导出。没有 RVC 模型的用户可直接在页面训练。
- 翻唱页可直接转换已有歌曲，也可先由 YuE2 重制再转换；选择 Seed-VC、RVC 或同曲对比。对比共用分轨缓存，完成后当前页和历史页都保留可试听结果。
- 训练项目/缓存、素材和用户音色库可指定目录并校验迁移；原数据保留备份。更新器支持旧多环境迁移和失败回滚。

RVC 的训练和换声已做实际验证；少数样本不能证明所有音色效果，不能承诺 RVC 一定优于 Seed-VC。公开素材的对比指标和人工盲听状态见发布附件 [RVC_EVALUATION.md](https://github.com/T8mars/Comfyui-YuE2-T8/releases/download/v1.3.0/RVC_EVALUATION.md)。完整包附带已编译并验证的可选 FlashAttention 轮子；当前歌曲推理未接入独立 `flash_attn`，无需安装，也不宣称整曲提速。

### AI 创作助手（独立 WebUI）

“AI 创作助手”通过贞贞平价小屋、贞贞的 AI 工坊、OpenAI 兼容接口或本地 GGUF 生成歌词、曲风和可选 ABC。结果可编辑、保存和下载，再选择字段发送到“创作”“乐谱计划”或“旋律重制 / 参考音色”。发送只填入草稿，生成音频由目标页按钮启动；该功能不增加 ComfyUI 节点。

默认先生成歌词和曲风，ABC 交给 YuE2 规划；需要 LLM 作谱时再选自动创作 ABC。未通过校验的谱面不能直接发送，失败保留已完成文本，支持只重试失败步骤。草稿保存在 `userdata/assistant`，升级时需要保留该目录。

API 密钥默认仅在本次服务会话有效，也可选择使用 Windows 当前用户加密保存。本地模型放在模型根目录的 `LLM` 下，或指定其他目录。v1.3.0 的音乐生成、转谱、Seed-VC、RVC 训练/推理和 GGUF 助手全部使用同一个 `runtime/python.exe`（Python 3.12.10），按任务启动子进程释放模型；没有第二套 Python。完整包已包含 GGUF 后端，`安装本地LLM.bat` 仅用于修复这一共享环境中的组件，不下载 GGUF 权重。模型加载成功不代表其乐谱生成质量通过验证。使用步骤见 [用户指南](USER_GUIDE.md#ai-创作助手)。

渠道会自动填入参考节点使用的默认模型：贞贞平价小屋为 `bytedance/doubao-seed-evolving`，贞贞的 AI 工坊为 `gemini-3.5-flash`。模型框支持预置下拉、手动模型 ID，以及从标准 OpenAI `/models` 接口获取账号可用的模型 LIST；接口不支持 LIST 时仍可手填。API Key 获取：[贞贞平价小屋](https://api.seedance.nz/sign-up?aff=5f4w) · [贞贞的 AI 工坊](https://ai.t8star.org/register?aff=dP7j)。

### 安装

Registry 版本审核通过后，可通过 ComfyUI Registry/Manager 安装：

```bash
comfy node install yue2-t8
```

也可以手动安装：

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/T8mars/Comfyui-YuE2-T8.git
```

安装节点后，进入节点目录并运行一次 `install_runtime.bat`。脚本会下载模型、统一 Python 3.12.10 运行时与 RVC 底模、CUDA 12.8 Torch、FFmpeg 和离线乐谱渲染组件。完成后重启 ComfyUI。

要求：Windows 10/11、NVIDIA GPU、建议 24GB 显存、建议至少 60GB 可用磁盘空间用于安装、下载与迁移（训练素材、检查点和作品另计）。正常生成、转谱和参考音色转换均使用离线模式。

### 模型放置路径

模型统一发布在 [t8star/YuE2-Comfy](https://huggingface.co/t8star/YuE2-Comfy)。安装脚本固定使用已验证的模型提交 [`a083f1064`](https://huggingface.co/t8star/YuE2-Comfy/commit/a083f106499daead99259dd0c443a5494254cfc5)。默认放到当前节点目录的 `models` 下；也可以在 WebUI 顶部展开“模型位置与安装说明”填写其他硬盘的绝对路径，或者双击 `configure_models.bat` 后再安装。当前路径保存在 `settings.json`。

```text
ComfyUI/custom_nodes/yue2-t8/models/YuE2-3B/model.safetensors
ComfyUI/custom_nodes/yue2-t8/models/YuE2-Vae/model.safetensors
ComfyUI/custom_nodes/yue2-t8/models/SheetSage2/model.safetensors
ComfyUI/custom_nodes/yue2-t8/models/MERT-v2-FullSong/model.safetensors
ComfyUI/custom_nodes/yue2-t8/models/SheetSage2/render_assets/
ComfyUI/custom_nodes/yue2-t8/models/Seed-VC/DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth
ComfyUI/custom_nodes/yue2-t8/models/Demucs/955717e8.safetensors
ComfyUI/custom_nodes/yue2-t8/models/RVC/
ComfyUI/custom_nodes/yue2-t8/models/VOICE_MODEL_MANIFEST.json
```

手动 Git clone 时，把上面的 `yue2-t8` 换成实际仓库目录名 `Comfyui-YuE2-T8`。不要把权重直接放入 ComfyUI 的 `checkpoints` 目录；代码需要保留七个模型子目录、配置文件及两个清单。

如果使用自定义目录，该目录本身就是上面路径中的 `models`：七个子目录和 `MODEL_MANIFEST.json`、`VOICE_MODEL_MANIFEST.json` 必须直接位于其中。命令行安装也可使用：

```powershell
.\install_runtime.bat -ModelsDirectory "D:\AI\YuE2-models"
```

### 更新

从 v1.2.2 开始，本地 WebUI 首页右上方的运行状态卡提供“检查更新”按钮，页面打开时也会自动检查稳定版。发现新版后点击“更新到 vX”，程序会从 [最新版本清单](https://github.com/T8mars/Comfyui-YuE2-T8/releases/latest/download/update-manifest.json) 下载代码包、验证来源与 SHA256、备份旧代码、安装并重启当前端口。更新保留模型、本地 GGUF、作品、上传、导出、日志、缓存、助手草稿和设置。首次升级到 v1.3.0 会准备并校验统一运行时，补齐 RVC 底模，旧服务退出后再切换代码与 Python；新版服务通过启动检查才清理旧运行时。失败时恢复旧代码与原运行时。运行或排队任务存在时不会开始更新。

GitHub 的 `*-code.zip` 是自动更新用代码包，不含模型和 Python；完整版包含统一运行时与基础模型，GGUF 权重另选。v1.2.2 起可用页面更新器。更早版本建议把新版完整版解压到新目录，设置已有模型路径后启动，保留原安装目录和作品。

不要只把 v1.3.0 代码覆盖到旧版多 Python 整合包：新版需要统一环境迁移。请使用页面更新器，或在新目录安装完整版。首次升级需联网下载缺失组件并留出新旧环境并存的临时空间；已通过校验的模型会复用。

1.1.5 修复 Windows 长曲声学合成的显存峰值，默认按查询分块并卸载闲置 AR 权重；参考音色翻唱改为后台持久任务，支持阶段保存和恢复。完成后当前页面直接显示播放器、时长及下载按钮，刷新或切换页面后仍保留最近作品。详见 [验证记录](VALIDATION.md)。

### 使用

节点位于 `YuE2 音乐` 分类。`workflows` 目录提供歌词创作、先计划再渲染、外部 ABC 重生成和参考音色翻唱四个前端工作流。Windows 整合包可双击 `YuE2-T8.exe` 启动；节点源码包可双击 `start_webui.bat`。启动窗口会保留并显示服务地址或失败原因。若 8189 已由另一套空闲 YuE2 占用，启动器会校验进程后自动切换；有运行或排队任务时不会中断。`stop_service.bat` 用于手动停止后台服务。

参考音色翻唱需要 1–30 秒清晰单人干声，推荐 5–25 秒、无伴奏、少混响。工作流先生成或接收歌曲，再分离歌声/伴奏、转换音色并重新混音。请只使用本人声音或已经取得明确授权的声音。

首次使用建议先运行“YuE2 模型服务”节点或 WebUI 右上角的自检。所有输出保存在节点目录下的 `outputs/jobs`，导出结果保存在 `exports`。失败任务可在 WebUI“任务与版本”页直接展开日志，或打开节点目录下的 `logs`；页面会显示 worker 的具体异常，不再只报告退出码。

存储策略首次启动时写入节点目录的 `retention.json`：完成任务默认保留 30 天、最多 100 个且总计不超过 100 GiB；上传保留 7 天且不超过 10 GiB；普通日志保留 30 天且不超过 2 GiB。服务每 6 小时自动执行，也可在 WebUI“任务与版本”页手动执行。把需要长期保存的结果导出到 `exports`；自动策略不会删除该目录。

### 节点

| 节点 | 功能 |
| --- | --- |
| YuE2 模型服务 | 检查运行时、模型与共享服务 |
| YuE2 生成歌曲 | 歌词、风格、ABC 到完整音频 |
| YuE2 生成乐谱计划 | 只生成可编辑 ABC 计划 |
| YuE2 渲染乐谱计划 | 精确恢复或编辑后重生成 |
| YuE2 音频转谱 | 音频到 ABC、MIDI、事件与乐谱图 |
| YuE2 生成翻唱 | 使用核对后的 ABC 与新风格生成 |
| YuE2 参考音色翻唱 | 使用 Seed-VC + Demucs 转换人声音色并重新混音 |
| YuE2 生成语义 Tokens | 高级分阶段推理 |
| YuE2 声学合成 | 语义 tokens 到声学 latent |
| YuE2 VAE 解码 | latent 到 48kHz 双声道音频 |
| YuE2 导出工件 | 把完整工件复制到 `exports` |
| YuE2 卸载/取消 | 查询或取消当前隔离 worker |

## English

YuE2 Music T8 integrates YuE2-3B full-song generation with ComfyUI and includes a standalone local WebUI. Its local scheduler runs models in isolated Python workers, so installing the node does not replace ComfyUI's Torch packages. Version 1.1.4 adds a configurable model directory and code-only GitHub Release update metadata.

Install it with `comfy node install yue2-t8`, then run `install_runtime.bat` once from the node directory and restart ComfyUI. Models are downloaded from [t8star/YuE2-Comfy](https://huggingface.co/t8star/YuE2-Comfy) into `<node-directory>/models`; use the WebUI model settings or `configure_models.bat` to place them on another drive. Keep all seven model subdirectories (including RVC) and their configuration files. Windows and an NVIDIA GPU are required, with 24GB VRAM and at least 60GB free disk space recommended for installation and migration, plus storage for training data and outputs.

The node pack supports Chinese and English lyrics, editable ABC plans, multi-candidate generation, SheetSage2 transcription, melody remake, Seed-VC reference-voice conversion, staged inference, per-task cancellation, history, and artifact export. The reference-voice workflow accepts a 1–30 second clean voice sample, separates the generated song with Demucs, converts the vocal, and remixes a 48 kHz stereo FLAC. Its page-integrated progress section identifies the current job and every queued job with stage, source, summary, and queue position. Example front-end workflows are in `workflows`.

The standalone v1.3.0 studio uses one CPython 3.12.10 runtime for music, transcription, Seed-VC, RVC training/inference and optional GGUF. The RVC workbench includes material review, training/resume and a voice library. Existing songs can be converted directly or compared through Seed-VC and RVC with shared separation. The updater migrates legacy runtimes and rolls back a failed startup. Short compatibility runs are not a voice-quality benchmark.

## Links

- Creator: By Bilibili creator T8star-Aix
- GitHub: https://github.com/T8mars/Comfyui-YuE2-T8
- Bilibili: https://space.bilibili.com/385085361
- YouTube: https://www.youtube.com/@T8star-Aix/
- API: https://api.seedance.nz/sign-up?aff=5f4w
- 在线 AI 应用 / Online AI apps: https://www.runninghub.ai/zh-cn/user-center/1907375370302308353/userPost?inviteCode=rh-v1121
- ComfyUI 整合包 / Portable package: https://pan.quark.cn/s/264edb7e36bd
- Hugging Face: https://huggingface.co/t8star
- Model repository: https://huggingface.co/t8star/YuE2-Comfy
- Release validation: [VALIDATION.md](VALIDATION.md)

## License

YuE2 first-party inference code and model weights are licensed under CC BY-NC 4.0 and are for non-commercial use. Seed-VC source is GPL-3.0. Demucs, BigVGAN and other third-party components retain their own licenses; see `THIRD_PARTY_NOTICES.md`, `MODEL_LICENSE`, `vendor/seed-vc/LICENSE`, and `vendor/licenses`.

This integration vendors YuE2 inference code version 0.1.6 from commit `8e06871aa2e704d87ffb9bc71b5f5420f6813724` of https://github.com/multimodal-art-projection/YuE.
