# ComfyUI YuE2 T8

[中文](#中文说明) · [English](#english) · [模型仓库 / Model weights](https://huggingface.co/t8star/YuE2-Comfy) · [ComfyUI Registry](https://registry.comfy.org/nodes/yue2-t8)

![YuE2 Music T8](icon.svg)

## 完整版整合包

完整版整合包：[夸克网盘下载](https://pan.quark.cn/s/67ebf18a2d51)

Windows / NVIDIA 完整包，包含运行环境与模型。完整解压后，双击 `YuE2-T8.exe` 即可启动。

[GitHub Release](https://github.com/T8mars/Comfyui-YuE2-T8/releases) 仅提供代码与自动更新附件，不包含 Python 或模型。完整整合包从上方夸克网盘获取；模型和可选 GGUF 权重也可通过下方网盘单独下载。旧版升级涉及统一运行环境迁移时，更新器会按需另外下载依赖。

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

### v1.4.5：资产操作、页面切换与失败提示收尾

- 资产卡的试听、发送、编辑和加入项目操作会自动换行，常见桌面宽度不再出现横向滚动或按钮被裁掉。
- 手机和平板从长页面切换工作区时回到新页面顶部；不相关页面不会短暂残留上一个页面的结果卡。
- 资产弹窗补齐读屏名称，普通必填项统一显示中文提示；当前创作页与历史页使用一致的友好错误摘要，完整技术信息仍可从任务日志查看。
- 浏览器回归现在会装入固定项目、四项素材和一条已完成音频，实际检查资产卡边界、三个动态弹窗、跨页滚动和当前页播放器。
- 旧版本遗留的“更新完成”记录不会再与当前版本状态冲突。GitHub 自动更新包仍只包含代码。

### v1.4.4：移动端全部功能菜单与浏览器回归

- 手机和平板顶部新增始终可见的“全部”入口，打开后可直接选择全部 9 个工作区，并标出当前页面；不再依赖用户发现横向滑动。
- GitHub Quality 新增真实 Chromium 回归，在桌面、平板、手机三档检查导航、项目焦点、空筛选恢复、按钮状态、控件名称、重复 ID、页面溢出和控制台错误。
- 每次质量检查都会保存桌面、平板、手机和手机菜单截图，以及浏览器与服务报告，便于发现后续样式回归。
- 更新同步会排除 `.git`、虚拟环境、测试缓存和 `node_modules` 等开发目录，并重试 Windows 短暂文件锁；正式自动更新包仍只包含版本化代码。

### v1.4.3：工作台导航与新手交互修复

- 桌面端只保留左侧工作台导航；手机和平板压缩页头和自检区，让当前功能更早进入首屏。
- 1024 px 及以下持续显示当前项目，并可一键返回项目选择，减少把生成结果放错项目的风险。
- 存储清理前明确说明范围并要求确认；没有正在执行的任务时禁用取消按钮；历史技术错误先显示易懂摘要，详情和日志仍可展开。
- 资产与任务筛选无结果时可一键清除筛选，并补齐搜索、乐谱编辑和上传控件的辅助名称与文字对比度。

### v1.4.2：项目竞态与窄窗口交互修复

- 上传、生成、转谱、翻唱、训练和助手任务始终归属发起时的项目；操作途中切换项目，不会再把结果、草稿或错误串到新项目。
- YuE2 训练、检查点试听与辅助任务分别显示；试听不会覆盖安全暂停/继续状态，发送到创作时使用用户明确选择的检查点模型。
- 资产库里的 ABC 乐谱可直接发送到可编辑的乐谱计划，试听结果保留在原项目；项目、资产和历史的慢请求不会覆盖较新的选择。
- 1024 px 及以下改为可横向浏览的顶部工作区导航，并自动把当前功能滚入视野；模型设置在切换页面后收起，表单和结果区不再被侧栏挤压。
- 发布包仍只有代码和自动更新清单，不包含 Python、音乐模型、GGUF、本地作品、用户数据或私有路线图。

### v1.4.1：工作台闭环与训练数据修复

- 项目、资产、创作和训练现在真正连通：资产可直接发送到原曲、参考音色、RVC、歌词、曲风与乐谱输入；文本可修订，资产和任务均可分页筛选。
- 项目草稿按项目隔离；切换、归档、刷新和读取失败不会串入其他项目。项目可重命名、归档、移出素材、选择主版本，并把音频与校验清单导出到 `exports`。
- YuE2 训练逐首绑定歌词、曲风或明确的纯器乐标记；后端禁止相同音频跨训练/验证集。暂停后可选择任意完整检查点试听，试听前核验训练身份、步数和全部文件哈希。
- 模型设置在所有页面可达；移动端只保留一套工作台导航并改善触控尺寸。历史任务支持状态、类型、项目和文字筛选。
- GitHub Release 仍只有代码自动更新包，不包含 Python、模型、GGUF 或用户数据；Windows / NVIDIA 完整包继续从页面顶部的夸克地址下载。

### v1.4.0：音乐工作台、资产库与 YuE2 风格训练

- 独立 WebUI 改为统一音乐工作台：项目、资产库、创作、乐谱、参考音色、AI 助手、训练和历史共用一套导航与当前项目。
- 资产库统一管理歌曲、作品、人声、伴奏、参考音色、歌词、曲风、乐谱和模型。音频导入后可立即播放、查看波形并固定版本加入项目；任务结果也会自动归档。
- “训练工作台”使用至少两首不同歌曲建立不可变训练/验证快照，要求确认素材使用权，使用 MERT 特征训练 YuE2 AR LoRA，并显示进度、训练/验证 loss、暂停与继续。
- 风格模型自动关联固定提交的 Mothersuperior v4 NAR 配套资源。完成后可在当前页面生成约 10 秒试听，再发送到歌曲创作；首期只开放已通过实机验证的“直接生成”模式。
- RVC 继续用于专用演唱音色训练，YuE2 LoRA 用于歌曲风格，两者在工作台内分工明确。所有功能继续共用一个 `runtime/python.exe`。

### v1.3.1：显存控制、恢复和发布门禁

- ComfyUI 与独立 WebUI 使用一致的可编辑显存预算，不再把节点输入限制在 12–24 GiB。
- 失败任务恢复时采用页面当前显存预算；VAE 分块同时服从物理显存和用户预算。
- ROCm/HIP 使用兼容的解码与 Seed-VC 路径，并禁止会产生错误结果的实验性 FP8；正式 ROCm 完整运行时仍需对应 AMD 设备验收。
- 自动更新前明确提示代码备份位置；GitHub PR 运行完整测试，Registry 未接受版本时发布任务会失败并显示真实状态。

### v1.3.0：统一运行环境与 RVC 训练工作台

- 所有本地功能共用 Python 3.12.10 / Torch 2.10.0 + CUDA 12.8，逐阶段子进程运行。
- “我的音色 / 训练”：导入素材、试听筛选、分离伴奏、训练、取消/续训、建索引、音色库预览与导入导出。没有 RVC 模型的用户可直接在页面训练。
- 翻唱页可直接转换已有歌曲，也可先由 YuE2 重制再转换；选择 Seed-VC、RVC 或同曲对比。对比共用分轨缓存，完成后当前页和历史页都保留可试听结果。
- RVC 显示所选音色的训练音域统计，并提供独立半音与八度选择、关闭检索对照。默认保留原调；低八度会改变演唱音高，对比模式下不影响 Seed-VC 的音高设置。
- 训练项目/缓存、素材和用户音色库可指定目录并校验迁移；原数据保留备份。更新器支持旧多环境迁移和失败回滚。

RVC 的训练和换声已做实际验证；少数样本不能证明所有音色效果，不能承诺 RVC 一定优于 Seed-VC。公开素材的对比指标和人工盲听状态见发布附件 [RVC_EVALUATION.md](https://github.com/T8mars/Comfyui-YuE2-T8/releases/download/v1.3.0/RVC_EVALUATION.md)。完整包附带已编译并验证的可选 FlashAttention 轮子；当前歌曲推理未接入独立 `flash_attn`，无需安装，也不宣称整曲提速。

### AI 创作助手（独立 WebUI）

“AI 创作助手”通过贞贞平价小屋、贞贞的 AI 工坊、OpenAI 兼容接口或本地 GGUF 生成歌词、曲风和可选 ABC。结果可编辑、保存和下载，再选择字段发送到“创作”“乐谱计划”或“旋律重制 / 参考音色”。发送只填入草稿，生成音频由目标页按钮启动；该功能不增加 ComfyUI 节点。

默认先生成歌词和曲风，ABC 交给 YuE2 规划；需要 LLM 作谱时再选自动创作 ABC。未通过校验的谱面不能直接发送，失败保留已完成文本，支持只重试失败步骤。草稿保存在 `userdata/assistant`，升级时需要保留该目录。

API 密钥默认仅在本次服务会话有效，也可选择使用 Windows 当前用户加密保存。本地模型放在模型根目录的 `LLM` 下，或指定其他目录。v1.3.x 的音乐生成、转谱、Seed-VC、RVC 训练/推理和 GGUF 助手全部使用同一个 `runtime/python.exe`（Python 3.12.10），按任务启动子进程释放模型；没有第二套 Python。完整包已包含 GGUF 后端，`安装本地LLM.bat` 仅用于修复这一共享环境中的组件，不下载 GGUF 权重。模型加载成功不代表其乐谱生成质量通过验证。使用步骤见 [用户指南](USER_GUIDE.md#ai-创作助手)。

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
ComfyUI/custom_nodes/yue2-t8/models/YuE2-training/
ComfyUI/custom_nodes/yue2-t8/models/VOICE_MODEL_MANIFEST.json
```

手动 Git clone 时，把上面的 `yue2-t8` 换成实际仓库目录名 `Comfyui-YuE2-T8`。不要把权重直接放入 ComfyUI 的 `checkpoints` 目录；代码需要保留八个模型子目录、配置文件及两个清单。`YuE2-training` 约 414 MB，包含固定 v4 tokenizer head、NAR companion 和 regularizer；完整版附带，缺失时也可在训练页校验后续传下载。

如果使用自定义目录，该目录本身就是上面路径中的 `models`：八个子目录和 `MODEL_MANIFEST.json`、`VOICE_MODEL_MANIFEST.json` 必须直接位于其中。命令行安装也可使用：

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

The standalone v1.4 studio uses one CPython 3.12.10 runtime for music, transcription, Seed-VC, RVC, YuE2 style training and optional GGUF. Its project workspace and content-addressed asset library connect source songs, stems, lyrics, scores, generated versions, voices and trained models. YuE2 AR LoRA training uses immutable train/validation snapshots, pinned Mothersuperior v4 companion resources, loss tracking, resumable checkpoints and an in-page audio preview. Trained adapters are currently enabled only for the validated direct-generation mode. The updater migrates legacy runtimes and rolls back a failed startup.

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
