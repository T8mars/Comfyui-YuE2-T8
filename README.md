# ComfyUI YuE2 T8

[中文](#中文说明) · [English](#english) · [模型仓库 / Model weights](https://huggingface.co/t8star/YuE2-Comfy) · [ComfyUI Registry](https://registry.comfy.org/nodes/yue2-t8)

![YuE2 Music T8](icon.svg)

## 中文说明

YuE2 Music T8 把 YuE2-3B 完整歌曲生成接入 ComfyUI，并提供一个可单独使用的本地 WebUI。节点通过 `127.0.0.1:8189` 调用隔离的推理 worker，不会替换或污染 ComfyUI 自带的 Torch 环境。

主要功能：

- 中文、英文歌词生成 48kHz 双声道歌曲；支持 `full`、`melody`、`off` 三种规划模式。
- 生成并保存 ABC 旋律/和弦计划，可精确恢复原始计划，也可编辑或导入 ABC 后重新生成。
- 一次生成 1–8 个连续种子候选，并保留完整请求、配置、tokens、latents 与完整性清单。
- 使用 SheetSage2 + MERT 把 WAV、FLAC、MP3、M4A、OGG、AAC 转为 ABC/MIDI，并生成翻唱。
- 共享单 GPU 队列、进度、取消、任务恢复、历史、导出，以及语义 token、声学合成、VAE 解码高级节点。

### 安装

通过 ComfyUI Registry/Manager 安装：

```bash
comfy node registry-install yue2-t8
```

也可以手动安装：

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/T8mars/Comfyui-YuE2-T8.git
```

安装节点后，进入节点目录并运行一次 `install_runtime.bat`。脚本会下载模型、Python 3.12/3.11 隔离运行时、CUDA 12.8 Torch、FFmpeg 和离线乐谱渲染组件。完成后重启 ComfyUI。

要求：Windows 10/11、NVIDIA GPU、建议 24GB 显存、约 35GB 可用磁盘空间。正常生成与转谱均使用离线模式。

### 模型放置路径

模型统一发布在 [t8star/YuE2-Comfy](https://huggingface.co/t8star/YuE2-Comfy)。安装脚本会自动放到当前节点目录的 `models` 下。Registry 默认目录名为 `yue2-t8`，完整路径为：

```text
ComfyUI/custom_nodes/yue2-t8/models/YuE2-3B/model.safetensors
ComfyUI/custom_nodes/yue2-t8/models/YuE2-Vae/model.safetensors
ComfyUI/custom_nodes/yue2-t8/models/SheetSage2/model.safetensors
ComfyUI/custom_nodes/yue2-t8/models/MERT-v2-FullSong/model.safetensors
ComfyUI/custom_nodes/yue2-t8/models/SheetSage2/render_assets/
```

手动 Git clone 时，把上面的 `yue2-t8` 换成实际仓库目录名 `Comfyui-YuE2-T8`。不要把四个权重直接放入 ComfyUI 的 `checkpoints` 目录；代码需要保留上述四个模型子目录及其配置文件。

### 使用

节点位于 `YuE2 音乐` 分类。`workflows` 目录提供歌词创作、先计划再渲染、外部 ABC 重生成三个示例。双击 `start_webui.bat` 可打开本地工作室；双击 `stop_service.bat` 停止后台服务。

首次使用建议先运行“YuE2 模型服务”节点或 WebUI 右上角的自检。所有输出保存在节点目录下的 `outputs/jobs`，导出结果保存在 `exports`。

### 节点

| 节点 | 功能 |
| --- | --- |
| YuE2 模型服务 | 检查运行时、模型与共享服务 |
| YuE2 生成歌曲 | 歌词、风格、ABC 到完整音频 |
| YuE2 生成乐谱计划 | 只生成可编辑 ABC 计划 |
| YuE2 渲染乐谱计划 | 精确恢复或编辑后重生成 |
| YuE2 音频转谱 | 音频到 ABC、MIDI、事件与乐谱图 |
| YuE2 生成翻唱 | 使用核对后的 ABC 与新风格生成 |
| YuE2 生成语义 Tokens | 高级分阶段推理 |
| YuE2 声学合成 | 语义 tokens 到声学 latent |
| YuE2 VAE 解码 | latent 到 48kHz 双声道音频 |
| YuE2 导出工件 | 把完整工件复制到 `exports` |
| YuE2 卸载/取消 | 查询或取消当前隔离 worker |

## English

YuE2 Music T8 integrates YuE2-3B full-song generation with ComfyUI and includes a standalone local WebUI. Its local scheduler runs models in isolated Python workers, so installing the node does not replace ComfyUI's Torch packages.

Install it with `comfy node registry-install yue2-t8`, then run `install_runtime.bat` once from the node directory and restart ComfyUI. Models are downloaded from [t8star/YuE2-Comfy](https://huggingface.co/t8star/YuE2-Comfy) into `<node-directory>/models`; keep all four model subdirectories and their configuration files. Windows and an NVIDIA GPU are required, with 24GB VRAM recommended.

The node pack supports Chinese and English lyrics, editable ABC plans, multi-candidate generation, SheetSage2 transcription, cover generation, staged inference, cancellation, history, and artifact export. Example workflows are in `workflows`.

## Links

- Bilibili: https://space.bilibili.com/385085361
- YouTube: https://www.youtube.com/@T8star-Aix/
- API: https://api.seedance.nz/sign-up?aff=5f4w
- 在线 AI 应用 / Online AI apps: https://www.runninghub.ai/zh-cn/user-center/1907375370302308353/userPost?inviteCode=rh-v1121
- ComfyUI 整合包 / Portable package: https://pan.quark.cn/s/264edb7e36bd
- Hugging Face: https://huggingface.co/t8star
- Model repository: https://huggingface.co/t8star/YuE2-Comfy

## License

YuE2 first-party inference code and model weights are licensed under CC BY-NC 4.0 and are for non-commercial use. Third-party components retain their own licenses; see `THIRD_PARTY_NOTICES.md`, `MODEL_LICENSE`, and `vendor/licenses`.

This integration vendors YuE2 inference code version 0.1.6 from commit `8e06871aa2e704d87ffb9bc71b5f5420f6813724` of https://github.com/multimodal-art-projection/YuE.
