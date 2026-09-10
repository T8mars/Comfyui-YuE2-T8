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
