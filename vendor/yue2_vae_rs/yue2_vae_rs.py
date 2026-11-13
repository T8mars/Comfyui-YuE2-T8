"""YuE2-VAE-RS: the fat x3 decoder for the YuE2 waveform VAE, as a standalone PyTorch module.

Ruach Studio (https://github.com/igrbible/Ruach_Studio) grew the Standard decoder of m-a-p's YuE2-Vae three times over and trained the new
units on music. The latent space is unchanged: the encoder is m-a-p's Standard one, so every YuE2 model's latents decode with this file as
they decode with Standard (64 channels at 25 frames a second, 48 kHz stereo out, hop 1920).

Only PyTorch and safetensors are needed; nothing from Ruach Studio is imported. Float32 only (see INTEGRATION.md for why).

    from yue2_vae_rs import load_decoder
    dec = load_decoder("YuE2-VAE-RS-x3_v1.safetensors", device="cuda")
    audio = dec.decode_tiled(latents)          # latents [B, 64, T] -> audio [B, 2, 1920*T], float32, unclipped

The decoder object speaks the decode API of m-a-p's YuE2VAE (decode, decode_tiled, decode_audio, natural_output_length, required_halo),
so a pipeline built on `YuE2VAE.from_pretrained(..., decoder_only=True)` swaps it in by changing the line that loads the VAE.

It also loads the Standard decoder (m-a-p's model.safetensors, or the vae.* part of Comfy-Org's single YuE2 checkpoint), for A/B tests.
"""
import json
import math
from contextlib import contextmanager

import torch
from torch import nn
import torch.nn.functional as F

__version__ = "1.0.0"

SAMPLE_RATE = 48000
HOP = 1920                                  # audio samples per latent frame
LATENT_DIM = 64
AUDIO_CHANNELS = 2
STRIDES = (6, 5, 4, 4, 2, 2)                # the decoder's six upsampling blocks, in order
WIDTHS = (2048, 1024, 512, 256, 128, 64, 64)
UNIT_DILATIONS = (1, 3, 9)                  # each block's residual units, Standard's and the new ones alike

# The only layouts this module accepts. x3: three new units at the latent rate (2048 wide, dilations 1, 3, 9) and three more units in each
# of the six blocks at the block's own width. Anything else (other counts, a hidden width other than the stream's) is refused.
CONFIGS = {
    "standard": {"pre": 0, "pre_mult": 1.0, "pre_dil": [], "extra": [0] * 6, "mult": [1.0] * 6},
    "x3": {"pre": 3, "pre_mult": 1.0, "pre_dil": [1, 3, 9], "extra": [3] * 6, "mult": [1.0] * 6},
}

# The kitchen's tiling (Ruach Studio's lab/trainer/vae.py tiled_decode): 750 frames (30 s) kept per tile, 16 frames of context each side.
KITCHEN_CORE, KITCHEN_HALO = 750, 16


class DecoderError(ValueError):
    """the file is not a decoder this module can run as it was trained"""


# ---------------------------------------------------------------- the network (weight norm folded at load, snake constants precomputed)

class Snake(nn.Module):
    """SnakeBeta with log-scale parameters: x + 1/(exp(beta) + 1e-9) * sin(exp(alpha) * x)^2"""

    def __init__(self, ch):
        super().__init__()
        self.register_buffer("a", torch.ones(1, ch, 1))
        self.register_buffer("inv_b", torch.ones(1, ch, 1))

    def set(self, alpha, beta):
        a = alpha.unsqueeze(0).unsqueeze(-1).exp()
        b = beta.unsqueeze(0).unsqueeze(-1).exp()
        self.a = a
        self.inv_b = 1.0 / (b + 1e-9)

    def forward(self, x):
        return x + self.inv_b * torch.sin(x * self.a).pow(2)


class ResUnit(nn.Module):
    def __init__(self, ch, dilation, hidden):
        super().__init__()
        self.snake1 = Snake(ch)
        self.conv1 = nn.Conv1d(ch, hidden, 7, dilation=dilation, padding=(dilation * 6) // 2)
        self.snake2 = Snake(hidden)
        self.conv2 = nn.Conv1d(hidden, ch, 1)

    def forward(self, x):
        return x + self.conv2(self.snake2(self.conv1(self.snake1(x))))


class Block(nn.Module):
    def __init__(self, cin, cout, stride, extra, mult):
        super().__init__()
        self.snake1 = Snake(cin)
        self.conv_t1 = nn.ConvTranspose1d(cin, cout, 2 * stride, stride=stride, padding=math.ceil(stride / 2), output_padding=stride % 2)
        units = [ResUnit(cout, d, cout) for d in UNIT_DILATIONS]
        units += [ResUnit(cout, UNIT_DILATIONS[i % 3], int(round(cout * mult))) for i in range(extra)]
        self.units = nn.ModuleList(units)

    def forward(self, x):
        x = self.conv_t1(self.snake1(x))
        for u in self.units:
            x = u(x)
        return x


class YuE2VAERSDecoder(nn.Module):
    """The grown decoder. Build it with load_decoder(); its weights are plain (weight norm already applied) and float32."""

    def __init__(self, config):
        super().__init__()
        self.config = dict(config)
        c = self.config
        self.conv1 = nn.Conv1d(LATENT_DIM, WIDTHS[0], 7, padding=3)
        pd = c.get("pre_dil") or [1, 3, 9]
        self.pre = nn.ModuleList([ResUnit(WIDTHS[0], pd[i % len(pd)], int(round(WIDTHS[0] * c.get("pre_mult", 1.0))))
                                  for i in range(c["pre"])])
        self.blocks = nn.ModuleList([Block(WIDTHS[i], WIDTHS[i + 1], STRIDES[i], c["extra"][i], c["mult"][i]) for i in range(6)])
        self.snake1 = Snake(WIDTHS[-1])
        self.conv2 = nn.Conv1d(WIDTHS[-1], AUDIO_CHANNELS, 7, padding=3, bias=False)
        self.metadata = {}
        self.requires_grad_(False)
        self.eval()

    # ------------------------------------------------------------ the forward pass

    def forward(self, z):
        x = self.conv1(z)
        for u in self.pre:
            x = u(x)
        for b in self.blocks:
            x = b(x)
        return self.conv2(self.snake1(x))

    # ------------------------------------------------------------ m-a-p's YuE2VAE decode API

    @property
    def decoder_device(self):
        return self.conv1.weight.device

    def _latent(self, latent):
        latent = torch.as_tensor(latent)
        if latent.ndim == 2:
            latent = latent[None]
        if latent.ndim != 3 or latent.shape[1] != LATENT_DIM or latent.shape[0] < 1 or latent.shape[-1] < 1:
            raise ValueError(f"expected nonempty latents [B, {LATENT_DIM}, T] (or [{LATENT_DIM}, T]), got {tuple(latent.shape)}")
        if not torch.isfinite(latent).all():
            raise ValueError("the latents contain non-finite values")
        if self.conv1.weight.dtype != torch.float32:
            raise DecoderError("the decoder's weights must stay float32")
        return latent

    @torch.inference_mode()
    def decode(self, latent):
        """the whole latent in one pass: float32 [B, 2, 1920*T], unclipped. Memory grows with T; use decode_tiled for songs."""
        latent = self._latent(latent)
        with exact_f32(self.decoder_device):
            return self(latent.to(device=self.decoder_device, dtype=torch.float32))

    def natural_output_length(self, frames):
        if int(frames) < 1:
            raise ValueError("frames must be positive")
        # 1920*T: the stride-5 block's transposed conv has output_padding 1, as in Ruach Studio's trainer and ComfyUI's Oobleck.
        # m-a-p's modeling_vae.py leaves it out and returns 1920*T - 64: the same audio, its last few milliseconds computed without
        # that one extra sample (the decoders were trained with it).
        return HOP * int(frames)

    def required_halo(self, core_frames=None):
        """latent frames of context each side that make a tiled decode equal to the whole-latent decode"""
        core = KITCHEN_CORE if core_frames is None else int(core_frames)
        lo, hi = 0, core * HOP - 1             # the core's output samples; walk back to the latent frames they depend on
        for op in reversed(list(_ops(self))):
            lo, hi = op(lo, hi)
        return max(0, -lo, hi - core + 1)

    @torch.inference_mode()
    def decode_tiled(self, latent, core_frames=KITCHEN_CORE, halo_frames=None, output_device="cpu", on_progress=None,
                     allow_short_halo=False):
        """Tiles of core_frames decoded with halo_frames of context each side, the cores kept, no crossfade.

        halo_frames None = required_halo(): the result equals decode() up to float rounding. A shorter halo is refused unless
        allow_short_halo=True; core 750 with halo 16 is how Ruach Studio's kitchen decoded every published comparison (see INTEGRATION.md)."""
        latent = self._latent(latent)
        required = self.required_halo()
        halo = required if halo_frames is None else int(halo_frames)
        if halo < required and not allow_short_halo:
            raise ValueError(f"halo_frames must be at least {required} for this decoder (or pass allow_short_halo=True)")
        core = int(core_frames)
        if core < 1:
            raise ValueError("core_frames must be a positive integer")
        frames = latent.shape[-1]
        total = self.natural_output_length(frames)
        audio = torch.empty((latent.shape[0], AUDIO_CHANNELS, total), dtype=torch.float32, device=output_device)
        if frames <= core + 2 * halo:          # the kitchen decodes a short latent whole
            audio.copy_(self.decode(latent).to(output_device))
            if on_progress is not None:
                on_progress(1, 1)
            return audio
        tiles = (frames + core - 1) // core
        for i, s in enumerate(range(0, frames, core)):
            e = min(s + core, frames)
            lo, hi = max(0, s - halo), min(frames, e + halo)
            y = self.decode(latent[..., lo:hi])
            a, b = s * HOP, min(e * HOP, total)
            c0 = (s - lo) * HOP
            audio[..., a:b].copy_(y[..., c0:c0 + b - a].to(output_device))
            del y
            if on_progress is not None:
                on_progress(i + 1, tiles)
        return audio

    def decode_kitchen(self, latent, output_device="cpu"):
        """exactly the kitchen's decode: core 750, halo 16"""
        return self.decode_tiled(latent, KITCHEN_CORE, KITCHEN_HALO, output_device=output_device, allow_short_halo=True)

    def decode_audio(self, latent, chunked=True, **kwargs):
        return self.decode_tiled(latent, **kwargs) if chunked else self.decode(latent)

    # ------------------------------------------------------------ helpers

    @property
    def variant(self):
        for name, c in CONFIGS.items():
            if _same_config(self.config, c):
                return name
        return "?"

    def parameter_count(self):
        return sum(p.numel() for p in self.parameters())


@contextmanager
def exact_f32(device):
    """TF32 off for the decode (PyTorch lets cuDNN convolutions run in TF32 by default on Ampere and newer), restored after"""
    if torch.device(device).type != "cuda":
        yield
        return
    old = torch.backends.cudnn.allow_tf32, torch.backends.cuda.matmul.allow_tf32
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cuda.matmul.allow_tf32 = False
    try:
        yield
    finally:
        torch.backends.cudnn.allow_tf32, torch.backends.cuda.matmul.allow_tf32 = old


# ---------------------------------------------------------------- receptive field (for required_halo), m-a-p's interval rule

def _conv_back(m):
    k, s, p, d = m.kernel_size[0], m.stride[0], m.padding[0], m.dilation[0]
    if isinstance(m, nn.ConvTranspose1d):
        return lambda lo, hi: (-(-(lo + p - d * (k - 1)) // s), (hi + p) // s)
    return lambda lo, hi: (lo * s - p, hi * s - p + d * (k - 1))


def _unit_back(u):
    inner = [_conv_back(u.conv1), _conv_back(u.conv2)]

    def f(lo, hi):
        a, b = lo, hi
        for op in reversed(inner):
            a, b = op(a, b)
        return min(a, lo), max(b, hi)
    return f


def _ops(dec):
    yield _conv_back(dec.conv1)
    for u in dec.pre:
        yield _unit_back(u)
    for b in dec.blocks:
        yield _conv_back(b.conv_t1)
        for u in b.units:
            yield _unit_back(u)
    yield _conv_back(dec.conv2)


# ---------------------------------------------------------------- loading

def _same_config(a, b):
    """same counts and widths; pre_dil compared only when `a` declares it (the shapes cannot show a dilation)"""
    if int(a["pre"]) != int(b["pre"]) or [int(x) for x in a["extra"]] != [int(x) for x in b["extra"]]:
        return False
    if [float(x) for x in a["mult"]] != [float(x) for x in b["mult"]] or float(a.get("pre_mult", 1.0)) != float(b.get("pre_mult", 1.0)):
        return False
    if a.get("pre_dil") is not None and b["pre"]:
        return [int(x) for x in a["pre_dil"]][:b["pre"]] == [int(x) for x in b["pre_dil"]][:b["pre"]]
    return True


def _std_name(k):
    """Standard's decoder keys (decoder.layers.<i>..., as m-a-p and Comfy-Org store them) -> this module's raw names"""
    p = k.split(".")
    i = int(p[2])
    if i == 0:
        head, rest = "conv1", p[3:]
    elif i == 7:
        head, rest = "snake1", p[3:]
    elif i == 8:
        head, rest = "conv2", p[3:]
    else:
        b, j, rest = i - 1, int(p[4]), p[5:]
        if j == 0:
            head = f"blocks.{b}.snake1"
        elif j == 1:
            head = f"blocks.{b}.conv_t1"
        else:
            head = f"blocks.{b}.units.{j - 2}." + ("snake1", "conv1", "snake2", "conv2")[int(rest[1])]
            rest = rest[2:]
    return head + "." + ".".join(rest)


_LEAF = {"weight_g": "g", "weight_v": "v", "parametrizations.weight.original0": "g", "parametrizations.weight.original1": "v",
         "bias": "bias", "alpha": "alpha", "beta": "beta"}


def _split(name):
    for leaf, short in _LEAF.items():
        if name.endswith("." + leaf):
            return name[:-len(leaf) - 1], short
    raise DecoderError(f"unexpected tensor {name}")


def _read(path):
    from safetensors import safe_open
    with safe_open(str(path), framework="pt", device="cpu") as f:
        meta = dict(f.metadata() or {})
        keys = list(f.keys())
        if any(k.startswith(("pre.", "blocks.")) for k in keys):
            layout, take = "vae-rs", [k for k in keys if k.split(".")[0] in ("conv1", "pre", "blocks", "snake1", "conv2")]
            names = {k: k for k in take}
        else:
            prefix = "vae.decoder.layers." if any(k.startswith("vae.decoder.layers.") for k in keys) else "decoder.layers."
            take = [k for k in keys if k.startswith(prefix)]
            if not take:
                raise DecoderError(f"{path}: neither a YuE2-VAE-RS decoder (pre.*, blocks.*) nor a YuE2 Standard VAE (decoder.layers.*)")
            layout = "standard"
            names = {k: _std_name(k[len(prefix) - len("decoder.layers."):]) for k in take}
        raw = {}
        for k in take:
            t = f.get_tensor(k)
            if t.dtype != torch.float32:
                raise DecoderError(f"{path}: {k} is {t.dtype}. This decoder is released and validated in float32 only; a {t.dtype} copy "
                                   "is not ours and will not decode as published (see INTEGRATION.md, «Why float32»).")
            raw[names[k]] = t
    return layout, meta, raw


def _infer_config(raw):
    pre = sorted({int(k.split(".")[1]) for k in raw if k.startswith("pre.")})
    c = {"pre": len(pre), "extra": [], "mult": []}
    c["pre_mult"] = raw["pre.0.conv1.bias"].shape[0] / WIDTHS[0] if pre else 1.0
    for b in range(6):
        units = sorted({int(k.split(".")[3]) for k in raw if k.startswith(f"blocks.{b}.units.")})
        c["extra"].append(max(0, len(units) - 3))
        c["mult"].append(raw[f"blocks.{b}.units.3.conv1.bias"].shape[0] / WIDTHS[b + 1] if len(units) > 3 else 1.0)
    return c


def load_decoder(path, device="cpu", allow_standard=True):
    """A YuE2-VAE-RS x3 file (or, with allow_standard, a YuE2 Standard VAE) -> YuE2VAERSDecoder on `device`, float32, eval.

    Checks: float32 tensors only; the layout is x3 (or Standard) and nothing else; every tensor present with the expected shape, none left
    over; the metadata's config, when present, agrees with the shapes."""
    layout, meta, raw = _read(path)
    if layout == "standard":
        if not allow_standard:
            raise DecoderError(f"{path} is the Standard YuE2 decoder, not VAE-RS")
        config = dict(CONFIGS["standard"])
    else:
        found = _infer_config(raw)
        declared = json.loads(meta["config"]) if "config" in meta else None
        if meta.get("variant", "x3") != "x3" or not _same_config(found, CONFIGS["x3"]):
            raise DecoderError(
                f"{path}: not the released x3 decoder (found {found['pre']} units at the latent rate, {found['extra']} new units per block, "
                f"hidden width x{found['mult']} of the stream; metadata variant {meta.get('variant')!r}). Only YuE2-VAE-RS x3 is supported; "
                "the x4 experiment was discarded and is not a release.")
        if declared is not None and not _same_config({**found, "pre_dil": declared.get("pre_dil")}, CONFIGS["x3"]):
            raise DecoderError(f"{path}: the metadata config {declared} disagrees with the x3 layout")
        config = dict(CONFIGS["x3"])
    dec = YuE2VAERSDecoder(config)
    _fill(dec, raw, path)
    dec.metadata = meta
    return dec.to(device) if torch.device(device).type == "cpu" else _refill_on(dec, raw, device, path)


def _refill_on(dec, raw, device, path):
    """fold the weight norm on the target device itself, as the kitchen's parametrization computes it there at every forward"""
    dec = dec.to(device)
    _fill(dec, {k: v.to(device) for k, v in raw.items()}, path)
    return dec


def _fill(dec, raw, path):
    groups = {}
    for name, t in raw.items():
        mod, leaf = _split(name)
        groups.setdefault(mod, {})[leaf] = t
    modules = dict(dec.named_modules())
    wanted = {n for n, m in modules.items() if isinstance(m, (nn.Conv1d, nn.ConvTranspose1d, Snake))}
    missing, stray = sorted(wanted - set(groups)), sorted(set(groups) - wanted)
    if missing or stray:
        raise DecoderError(f"{path}: tensors do not match the layout (missing {missing[:4]}{'…' if len(missing) > 4 else ''}, "
                           f"unexpected {stray[:4]}{'…' if len(stray) > 4 else ''})")
    with torch.no_grad():
        for mod, t in groups.items():
            m = modules[mod]
            if isinstance(m, Snake):
                for leaf in ("alpha", "beta"):
                    if leaf not in t or t[leaf].shape != (m.a.shape[1],):
                        raise DecoderError(f"{path}: {mod}.{leaf} has shape {tuple(t[leaf].shape) if leaf in t else None}, "
                                           f"expected ({m.a.shape[1]},): a decoder of another width")
                m.set(t["alpha"], t["beta"])
                continue
            if "g" not in t or "v" not in t:
                raise DecoderError(f"{path}: {mod} lacks its weight-norm pair (weight_g/weight_v or parametrizations original0/original1)")
            if t["v"].shape != m.weight.shape or t["g"].shape != (m.weight.shape[0], 1, 1):
                raise DecoderError(f"{path}: {mod} weight {tuple(t['v'].shape)} / g {tuple(t['g'].shape)}, expected {tuple(m.weight.shape)}: "
                                   "a decoder of another width")
            m.weight.copy_(torch._weight_norm(t["v"], t["g"], 0))
            if m.bias is not None:
                if "bias" not in t or t["bias"].shape != m.bias.shape:
                    raise DecoderError(f"{path}: {mod}.bias missing or of another shape")
                m.bias.copy_(t["bias"])
            elif "bias" in t:
                raise DecoderError(f"{path}: {mod} has a bias the layout does not")


def is_vae_rs(path):
    """True when the file holds a YuE2-VAE-RS decoder (pre.* / blocks.* tensors), False for anything else"""
    from safetensors import safe_open
    with safe_open(str(path), framework="pt", device="cpu") as f:
        return any(k.startswith(("pre.", "blocks.")) for k in f.keys())


def comfy_state_dict(path, prefix="decoder."):
    """The decoder's tensors renamed into the Oobleck layout ComfyUI and m-a-p use (weight_g / weight_v), extended for the new units:

        decoder.layers.0                      conv1 (Standard)
        decoder.pre.<i>.layers.{0,1,2,3}      the units at the latent rate: snake, conv k7, snake, conv k1 (new)
        decoder.layers.<b+1>.layers.0 / .1    block b's snake and transposed conv (Standard)
        decoder.layers.<b+1>.layers.<2..4>    block b's units 1-3 (Standard)
        decoder.layers.<b+1>.layers.<5..7>    block b's units 4-6 (new), dilations 1, 3, 9
        decoder.layers.7 / .8                 the last snake and conv (Standard)

    For the Standard file this gives back m-a-p's own key names. Loading the x3 result needs the ComfyUI patch in INTEGRATION.md:
    unpatched ComfyUI loads VAEs with strict=False and would silently drop every new unit."""
    layout, _, raw = _read(path)
    out = {}
    for name, t in raw.items():
        mod, leaf = _split(name)
        leaf = {"g": "weight_g", "v": "weight_v"}.get(leaf, leaf)
        p = mod.split(".")
        if p[0] == "conv1":
            key = "layers.0"
        elif p[0] == "snake1":
            key = "layers.7"
        elif p[0] == "conv2":
            key = "layers.8"
        elif p[0] == "pre":
            key = f"pre.{p[1]}.layers.{('snake1', 'conv1', 'snake2', 'conv2').index(p[2])}"
        else:
            b = int(p[1])
            if p[2] == "snake1":
                key = f"layers.{b + 1}.layers.0"
            elif p[2] == "conv_t1":
                key = f"layers.{b + 1}.layers.1"
            else:
                key = f"layers.{b + 1}.layers.{int(p[3]) + 2}.layers.{('snake1', 'conv1', 'snake2', 'conv2').index(p[4])}"
        out[f"{prefix}{key}.{leaf}"] = t
    return out


def ggml_names(dec):
    """this module's raw tensor names onto the naming Ruach Studio's ggml engine uses (decoder.conv1, decoder.pre.<i>.*,
    decoder.block.<b>.res_unit<j>.*, j = 1..3 Standard's, 4..6 the new ones; weight_g / weight_v)"""
    out = {}
    for mod, m in dec.named_modules():
        if not isinstance(m, (nn.Conv1d, nn.ConvTranspose1d, Snake)):
            continue
        p = mod.replace("blocks.", "block.").split(".")
        if p[0] == "block" and len(p) > 2 and p[2] == "units":
            p[2:4] = [f"res_unit{int(p[3]) + 1}"]
        base = "decoder." + ".".join(p)
        leaves = ("alpha", "beta") if isinstance(m, Snake) else ("weight_g", "weight_v") + (("bias",) if m.bias is not None else ())
        for leaf in leaves:
            out[f"{mod}.{leaf}"] = f"{base}.{leaf}"
    return out


if __name__ == "__main__":
    import argparse
    import time
    ap = argparse.ArgumentParser(description="decode YuE2 latents with YuE2-VAE-RS (or Standard) to a 48 kHz stereo WAV")
    ap.add_argument("weights")
    ap.add_argument("latents", help=".npy [64, T] or [1, 64, T], or a Ruach Studio latents.f32")
    ap.add_argument("out", help="output .wav (float32)")
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--kitchen", action="store_true", help="core 750 / halo 16, as Ruach Studio's published comparisons")
    a = ap.parse_args()
    import numpy as np
    if a.latents.endswith(".f32"):
        hdr = np.fromfile(a.latents, dtype=np.int32, count=3)
        z = np.fromfile(a.latents, dtype=np.float32, offset=12).reshape(int(hdr[1]), int(hdr[2])).T.copy()
    else:
        z = np.load(a.latents)
    dec = load_decoder(a.weights, a.device)
    t = time.time()
    y = (dec.decode_kitchen if a.kitchen else dec.decode_tiled)(torch.from_numpy(z).float())
    import soundfile as sf
    sf.write(a.out, y[0].numpy().T, SAMPLE_RATE, subtype="FLOAT")
    print(f"{dec.variant}: {z.shape[-1]} frames -> {y.shape[-1] / SAMPLE_RATE:.1f} s in {time.time() - t:.1f} s, peak {float(y.abs().max()):.3f}")
