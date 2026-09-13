# YuE2 Music T8 · AMD Radeon (ROCm) 移植指南

> 把本地工作室跑在 **AMD Radeon / Windows 原生 ROCm** 上的完整记录与脚本。
> 实测环境：**Radeon RX 9070 XT 16GB（gfx1201 / RDNA4）· Windows 11 build 26200 ·
> 原生 Windows ROCm（无 CUDA、无 Triton、无 flash-attn）**。
> 四项能力（出歌 / 音频转谱 / 乐谱渲染 / 参考音色）**全部实测产出真实产物**，
> 不是只看 capability 标志。

本目录同时提供 `scripts/rocm/` 下的脚本，所以这份指南不只是一份报告 —— 可以照着复现。

---

## 0. TL;DR

| | NVIDIA（现有安装路径） | AMD（本文） |
|---|---|---|
| PyTorch 来源 | `download.pytorch.org/whl/cu128` | `rocm.nightlies.amd.com/v2/gfx120X-all/`（AMD TheRock） |
| 运行时布局 | `runtime/python.exe` | **完全相同**，不新增布局 |
| 源码改动 | — | **3 处**（见 §3，纯设备无关的健壮性修复） |
| 站点包改动 | — | **1 处**（`descript-audiotools`，见 §3.2） |
| 出歌 | ✅ | ✅ 175 s 音频 / 393.4 s |
| 音频转谱（SheetSage2 + MERT） | ✅ | ✅ 24 s 音频 / 36 s |
| 乐谱渲染（playwright + abcjs） | ✅ | ✅ 产出 PDF + 钢琴试奏 WAV |
| 参考音色（Seed-VC + Demucs） | ✅ | ✅ 47 s，产出 `audio.flac`，RTF 0.79 |

**核心结论**：出歌路径上唯一"长得像 CUDA"的代码是两行自检 ——

```python
if not torch.cuda.is_available(): raise ...
if not torch.cuda.is_bf16_supported(): raise ...
```

ROCm 下 `torch.cuda` 就是 **HIP 别名**，两者都返回 True（gfx1201 原生支持 BF16）。
真正 NVIDIA-only 的只有**安装脚本里的轮子索引**。模型权重与 t8 的 pin
**逐位一致**（YuE2-3B `1d55c42c…a59e9`、YuE2-Vae `807ce9d5…7751346`；
从 `m-a-p` / `mrfakename` 下载的字节相同），所以 t8 自带的 SHA 清单校验原样通过。

---

## 1. 为什么这条路能走通

t8 的作业链路是**每个作业起独立 worker 进程**，各角色互不污染：

```
service (runtime/python.exe)
  ├── kind=generate / plan / decode  → core_worker
  ├── kind=transcribe                → transcribe_worker
  ├── kind=voice_convert             → voice_worker
  └── kind=reference_cover           → workflow_worker
```

决定移植形状的几个事实：

- **没有任何 sm_XX / compute-capability / NVML 检查**，只有上面那两行 `torch.cuda` 自检；
- `vendor/yue2/` 与 `vendor/seed-vc/` 两个模型栈**互相零引用**，音色转换是独立后处理
  （出歌 → Demucs 分离 → Seed-VC 换音色 → 重混）；
- `MODEL_MANIFEST.json` / `VOICE_MODEL_MANIFEST.json` 自带 path/size/sha256，
  校验脚本只认清单 —— 权重逐位一致即可，与设备无关。

> 有一处**版本现实**要说明：文档与 `scripts/rocm/` 的脚本最初是针对 t8 **v1.2.2**
> （当时是 `runtime/core`、`runtime/transcribe`、`runtime/voice` 三套运行时）开发的，
> 现在上游已统一成 `runtime/python.exe` 单运行时。**§3 的三处源码改动与版本无关**
> （已确认在 `main` 上锚点逐字命中），`scripts/rocm/setup_rocm_runtime.ps1` 已改写为
> 单运行时布局。三运行时时期的完整实测数据见 §4 / §5。

---

## 2. 安装（单运行时布局）

前置：AMD 驱动 + Windows 11；磁盘约 45 GB；网络能访问 `hf-mirror.com` 与
`rocm.nightlies.amd.com`（`python.org` 在部分网络不可达，脚本带镜像回退）。

```powershell
# 1) 取代码（不要用上游的 cu128 安装器）
git clone --depth 1 https://github.com/T8mars/Comfyui-YuE2-T8.git YuE2-T8
cd YuE2-T8

# 2) 建 ROCm 运行时（对应上游 scripts/setup.ps1 的位置）
powershell -ExecutionPolicy Bypass -File scripts\rocm\setup_rocm_runtime.ps1

# 3) 应用补丁（已合并的部分会自动 SKIP，可重复运行）
python scripts\rocm\apply_rocm_port.py .

# 4) 模型（可选：网络到 huggingface.co 不通时走镜像直连）
python scripts\rocm\fetch_mirror_models.py
runtime\python.exe scripts\verify_models.py --root .
runtime\python.exe scripts\verify_voice_models.py --root .

# 5) 启动
启动本地整合包.bat      # 或 runtime\python.exe -X utf8 -m app.yue2_app.service --host 127.0.0.1 --port 8189

# 6) 功能验证（doctor + 转谱渲染 + 音色转换）
python scripts\rocm\verify_capabilities.py
```

启动前建议设置的环境变量（`start_rocm.bat` 里已经写好）：

```bat
set TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1
set FLASH_ATTENTION_TRITON_AMD_ENABLE=FALSE
set TORCH_BLAS_PREFER_HIPBLASLT=1
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set YUE2_HOME=%~dp0
set YUE2_KIT=%~dp0
```

---

## 3. 全部改动

### 3.1 源码（3 处，PR-A / PR-B 已提交）

| # | 文件 | 现象 → 根因 | 修法 |
|---|---|---|---|
| 1 | `vendor/yue2/cuda_graph.py` | 解码第一步 `RuntimeError: [ROCm] mha_varlen_fwd: seqused_k must be nullopt`。后端探测**只看 ATen schema**，而 schema 是跨后端共享的；ROCm 下 `device.type == "cuda"` 为真且 `_flash_attention_forward` 确实声明了 `seqused_k`，于是误选 flash | HIP 构建上强制走掩码 SDPA。**不能**改传 `seqused_k=None` —— 变长 FA 会去注意未使用的未来 cache 槽位，算出错误结果。实测与 eager **数值差 0.0**，且在捕获的 graph 内 replay 正常 |
| 2 | `vendor/seed-vc/inference.py` | CAMPPlus / RMVPE 是 `load_models()` 里**仅剩的 fp32 模型**（其它都跟随 `args.fp16`）。fp32 batchnorm 触发 MIOpen 经 HIPRTC **运行时编译** `MIOpenBatchNormFwdInferSpatial`，而 TheRock wheel 不带 libc++ 头 → `fatal error: 'type_traits' file not found` → `miopenStatusUnknownError`，Seed-VC 一个音符都出不来 | 两者都跟随 `fp16`，并把 kaldi fbank 特征转成对应 dtype |
| 3 | `app/yue2_app/voice_worker.py` | 同类 JIT 失败在别的 fp32 kernel 上复发（spatial BN、RMVPE 里的 GRU）—— 逐个打地鼠不如绕开 | 该 worker 关闭 MIOpen（ROCm PyTorch 里 `torch.backends.cudnn` **就是** MIOpen），conv/BN/RNN 走 PyTorch 原生实现，无需 JIT。**仅在 `torch.version.hip` 非空时生效，CUDA 用户保持 cuDNN**；出歌（core_worker）不受影响，继续用 MIOpen 跑重度 GEMM |
| 4 | `app/yue2_app/core_worker.py` | VAE 分块大小由 budget 单值推导（`>12GiB → 1024`），隐含 24 GB 卡假设；16 GB 卡上 1024 帧命中慢得多的卷积 solver | 按实卡显存自适应（`<20GiB → 512`），请求仍可显式覆盖 `vae_core_frames` |

### 3.2 站点包（1 处，无法进仓库，故提供脚本）

`descript-audiotools`（由 `demucs → dac` 带入）在 **类体导入时**对 `dist.ReduceOp`
求值，而 torch ≥ 2.9 的 `torch.distributed` 是惰性模块，未初始化进程组前没有该属性：

```
AttributeError: module 'torch.distributed' has no attribute 'ReduceOp'
  audiotools/ml/decorators.py  op: dist.ReduceOp = dist.ReduceOp.AVG
```

Seed-VC 因此完全无法启动。**这是 descript-audiotools 与现代 torch 的不兼容，不是 AMD 问题**
（NVIDIA 上装 torch ≥ 2.9 同样复现）。本移植在 voice 运行时的站点包里注入哨兵：

```powershell
python scripts\rocm\patch_audiotools.py runtime\Lib\site-packages
```

> 上游更干净的解法可能是换一个兼容版本的 `descript-audiotools`，或在安装脚本里加一步
> 后置补丁 —— 这个取舍留给维护者。`apply_rocm_port.py` 也会在运行时已安装时顺手处理。

### 3.3 t8 自带文件一律未改

除 `core_worker.py` 的 1 处功能改动外，上游既有文件保持原样；ROCm 用户用新脚本
**并排安装**，便于 rebase 与提 PR。

---

## 4. 实测结果

| 验证 | 耗时 | 产物 |
|---|---:|---|
| 出歌（中文，175 s 音频，seed 20260917） | 393.4 s | `audio.flac` + `score.abc` |
| 音频转谱（24 s 音频） | 36 s | ABC 310 字符 + MIDI + **PDF** + 钢琴试奏 `piano_mix.wav` |
| 参考音色（24 s，`diffusion_steps=8`） | 47 s | `audio.flac`（换音色人声 + 伴奏重混），Seed-VC **RTF 0.79** |
| doctor 自检 | 12 s | GPU / BF16 / 四模型 SHA256 全过 |

**一个正确性信号**：同 seed 同歌词，经「官方 CLI 直跑」与「service → worker → vendored
yue2」两条完全不同的代码路径，产出音频时长**逐位一致**（`174.91866666666667 s`）
—— 移植未引入行为偏差。

### 4.1 VAE 分块大小（§3.1 #4 的依据）

同一段 1499 帧（60 s 音频）latent，只跑 decode：

| tiles | `core_frames` | 解码耗时 | 峰值显存 | RMS |
|---:|---:|---:|---:|---:|
| 12 | 128 | 104.0 s | 1.27 GB | 0.0942 |
| 6 | 256 | 161.6 s | 1.88 GB | 0.0942 |
| **3** | **512** | **101.6 s** | 3.11 GB | 0.0942 |
| 2 | 1024 | 230.9 s | 5.50 GB | 0.0942 |
| 1 | full | 285.0 s | 7.70 GB | 0.0942 |

五者 RMS 完全一致 —— 只是速度差异，不影响音质。**耗时对分块大小非单调**
（256 比 128 和 512 都慢），说明主导因素是 **MIOpen 按张量形状挑 solver**，
而不是干净的 O(T²) 关系；512 恰好在这个形状的好档位上。

端到端（同一请求 `zh_song.json`，seed 20260917，产出 174.919 s 音频）：

| 运行 | wall | VAE 解码 | `vae_core_frames` |
|---|---:|---:|---|
| 改动前 | 613.7 s | 313.0 s | 1024 |
| 改动后 | **393.4 s** | **107.0 s** | 512 |
| 收益 | **−220.3 s（−35.9 %）** | −206 s | — |

折算 **3.51 s/音频秒 → 2.25 s/音频秒**；改动后的路线**已快于**当初官方 CLI 直跑
同请求的 423.4 s。

### 4.2 其它性能参考

- 出歌速度：每 1 秒音频 ≈ 2.25 s（eager）
- **歌长 = 0.04 秒/token**（25 token/秒音频），默认 `semantic.max_tokens=9000` → 上限 6 分钟；
  实际歌长由**歌词段数**决定
- AR 吞吐：eager 24.7 tok/s；CUDA graph 短序列 46.3 tok/s，长序列降到 14.3 tok/s
  —— 掩码 SDPA 每步对**整个 capacity** 算注意力（`capacity = max(len(prefix)) + max_tokens`），
  上游 flash 路径靠 `seqused_k` 规避，而这正是 ROCm 拒绝的参数。所以 **graph 只在小
  capacity 时划算**，生产参数下用 `--backend torch-eager`

> **测速纪律**：跨天比较有系统性漂移。同是 512 分块，早期 VAE 解码 155.8 s、后一次
> 107.0 s（最可能是 MIOpen solver 调优缓存随首次运行落盘）。**只有同日同会话的 A/B
> 才可靠**，与历史数字比较应留 ~15 % 余量。

---

## 5. 常见坑速查（全部实测踩过）

| 坑 | 现象 | 解法 |
|---|---|---|
| `python.org` 不可达 | `curl` 无超时永久挂起 | `--connect-timeout/--max-time` + 镜像回退；或预置归档 |
| pip 装不了 AMD `rocm` | `Cannot import 'setuptools.build_meta'`（sdist + 嵌入式解释器无 setuptools） | **用 uv** 建隔离构建环境 |
| pip 静默换 CUDA torch | ROCm torch 装失败后 pip 去 PyPI 拉 cu 版，看起来"成功" | 末尾断言 `torch.version.cuda is None` |
| torch / torchaudio 配错对 | uv **独立解析**两者，配出 `torch 2.9.0+rocm7.10` × `torchaudio 2.9.0+rocm7.13` 的 ABI 不匹配组合 | 钉**同一个完整构建串**（本移植：`2.11.0+rocm7.13.0a20260416`） |
| 缺 `hipsparselt` | `Unknown rocm library 'hipsparselt'` | torch 2.9.0（2025-11 构建）需要它，其配对的 rocm 7.10 SDK 与整个 v4 索引都没有；换 2.11.0+rocm7.13 |
| hub 客户端下不了模型 | `RemoteDisconnected` / `LocalEntryNotFoundError` / 符号链接 `PermissionError` | HTTP 分块续传直连 `hf-mirror.com`（`fetch_mirror_models.py`） |
| MIOpen 需要写权限 | `miopenStatusInternalError`（SQLite 调优库打不开） | 受限沙箱/只读环境会撞；普通用户权限无碍 |
| 首次 VAE 解码异常慢 | 首跑 69.7 s/chunk，之后 41–55 s | MIOpen 一次性 solver 调优，**不是配置问题** |
| `torchaudio.save` 失败 | `TorchCodec is required for save_with_torchcodec`（2.9+ 把 `.save()` 改走 torchcodec，Windows 无 FFmpeg 共享库） | 改用 `soundfile`（本仓库 `vendor/seed-vc/inference.py` 已这么做） |
| SheetSage2 加载失败 | 缺 `configuration_sheetsage2.py` 等 | `trust_remote_code` 模型要下**全部** `.py`，不能只下 `REQUIRED_FILES` |
| **`.bat` 里放中文导致闪退** | 双击即退，报 `'IMENTAL' is not recognized` 之类 | cmd 按当前代码页逐行切分，多字节字符会**错位断行**。**`.bat` 只放 ASCII**，中文提示移到 Python 输出 |

---

## 6. 已知限制 / 未解项

1. **`quantization="fp8"` 绝不可用。** capability 门是 `>= (8, 9)`，本机报 **(12, 0)**
   ⇒ 门槛通过，`torch._scaled_mm` 也存在，**看起来一切正常**。但实测（含真实形状
   `M=1 K=2048 N=184704` 的 lm_head）对反量化权重的**相对误差 90–117**：

   | 形状 | vs 反量化参考 |
   |---|---:|
   | M=16 K=2048 N=512 | 99.99863 |
   | M=16 K=2048 N=2048 | 109.82619 |
   | M=1 K=2048 N=184704（lm_head） | 116.88484 |
   | M=1500 K=2048 N=2048（prefill） | 89.68730 |

   这不是精度损失，是**计算错误** —— 开了会静默产出垃圾音频。建议在
   `quantization.py` 的能力门里显式排除 HIP 构建。

2. **MIOpen 的 JIT 依赖未根治。** §3.1 #3 只让 voice worker 绕开了它。core 的 GEMM 走
   预编译 kernel，实测无碍；但若未来 core 路径触发新的 JIT kernel，会以同样方式失败。
   彻底解法是给 comgr 提供 libc++ 头 —— **注意**：把 LLVM 的 libc++ 头（1721 个文件）
   平铺进 clang 资源目录 `lib/clang/23/include` **无效**，HIPRTC 的搜索路径不含资源目录。
   这条弯路记下来避免重复。

3. **上游 pin 的 torch 版本已变。** t8 v1.2.2 pin `torch==2.8.0`；现在的
   `requirements-unified.lock.txt` 是 `torch==2.10.0+cu128`。§3.2 的
   `descript-audiotools` 问题在 torch ≥ 2.9 上成立，建议在 NVIDIA 侧也确认一遍
   （我们只在 ROCm/torch 2.11 上实测过）。

4. **NAR 阶段比官方 CLI 路线慢约 16 s**（40.7 s vs 24.7 s），原因未定位，
   疑与 `nar_query_chunk_size`（t8 传 256）或 `offload_ar` 相关；占总时长 < 5 %，未深究。

5. **应用内自更新会覆盖手改环境。** 「更新到 vX」会整包重解压，对任何本地改过 kit 的
   用户（不只是 AMD）都会静默冲掉改动。上游 commit `c91ab46` 已把更新改成 code-only，
   方向正确；本移植的做法是让它恒返回"已是最新"，升级改为手动（重装新版后重跑
   `apply_rocm_port.py`）。

---

## 7. 许可证

t8 代码见仓库 `LICENSE`；YuE2 权重 **CC BY-NC 4.0（非商用）**；
SheetSage2 / MERT 见各自 LICENSE；Seed-VC **GPL-3.0**；Demucs MIT。
本目录的脚本沿用仓库许可。
