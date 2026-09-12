# Third-party code notices

The Oobleck VAE and SnakeBeta implementation in `modeling_vae.py` is derived
from stable-audio-tools commit `a6ae0cdf8b2eb1567a4b42ceadddec3712d99d45`.
The module hierarchy, weight normalization and activation equations preserve
the checkpoint's original inference implementation.

- Oobleck / stable-audio-tools: Copyright (c) 2023 Stability AI, MIT.
  Full text: `licenses/stable-audio-tools-MIT.txt`.
- SnakeBeta / BigVGAN: Copyright (c) 2022 NVIDIA CORPORATION, MIT.
  Full text: `licenses/SnakeBeta-NVIDIA-MIT.txt`.

These notices cover the identified source code and retain its original licenses.
The YuE2 model checkpoint weights are separately licensed under CC BY-NC 4.0;
see MODEL_LICENSE for the scope and full terms. This does not relicense third-party code.

Reference-voice support also uses the following upstream projects and models:

- Seed-VC source, Copyright (c) Plachtaa and contributors, GPL-3.0. The vendored
  source and full license are in `vendor/seed-vc`.
- Demucs source and HTDemucs weights, Copyright (c) Facebook AI Research and
  contributors, MIT: https://github.com/facebookresearch/demucs and
  https://github.com/adefossez/demucs.
- NVIDIA BigVGAN source and weights, MIT:
  https://github.com/NVIDIA/BigVGAN and https://huggingface.co/nvidia/bigvgan_v2_44khz_128band_512x.
- OpenAI Whisper small weights, Apache-2.0 model distribution:
  https://huggingface.co/openai/whisper-small.
- RMVPE is distributed under MIT by the VoiceConversionWebUI model repository;
  CAMPPlus is distributed under Apache-2.0 by FunASR. Their original model
  cards remain applicable.

All third-party code and weights retain their original terms. Nothing in this
repository changes or combines those licenses into the YuE2 model license.

The standalone AI creation assistant adapts text rules and provider/metadata
helpers from the owner's `T8mars/comfyui-minimax-h3-prompt-enhancer-T8` project,
commit `b09412575ef726b4b7637f7279f1848108435607`, with the owner's authorization.
Its original notice is retained at `app/yue2_app/assistant_rules/T8-LICENSE.txt`;
this integration does not grant a general license to the source project.
The official YuE2 skill snapshot and ABC helpers retain Apache-2.0 at
`app/yue2_app/assistant_rules/official_skills/yue2-music/LICENSE`, pinned to
`92a73cc7652fcc1f937855e4b765e0a0edd7ff2e`. Source records remain beside the files.

The optional local runtime uses llama-cpp-python / llama.cpp from the JamePeng
Windows distribution. Its packaged license notices remain in that runtime.
CUDA runtime libraries copied from the existing bundled CUDA 12 environment
remain governed by NVIDIA's original redistribution terms; no Torch code is
imported into the assistant worker for this purpose.


RVC training and inference code is vendored from
https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI
at commit 81eed5e8f68b6bed1789f682fe78cdd324495afc, MIT;
see vendor/rvc/LICENSE, UPSTREAM.json and PATCHES.json for attribution and local changes.
RVC base asset provenance and hashes are recorded in app/yue2_app/rvc_assets.json.

Optional FlashAttention wheels in the full bundle derive from
https://github.com/Dao-AILab/flash-attention (BSD-3-Clause), version 2.8.3,
commit 060c9188beec3a8b62b33a3bfa6d5d2d44975fab. The multi-architecture wheel is
published by https://github.com/mjun0812/flash-attention-prebuild-wheels;
the separate SM120 wheel was compiled locally. Bundle extras include the
upstream license and build/verification provenance. They are not enabled by default.
